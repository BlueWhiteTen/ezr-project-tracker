"""Shared helpers used across multiple view modules."""
import math
import random
from datetime import date, timedelta
from django.utils import timezone
from django.db.models import Q
from django.contrib.auth.models import User

from ..models import (
    Project, ProjectLog, ProjectCost, ProjectCostLine, UprightAccessory, AccessoryOverride,
    PurchaseOrder, Reminder, Notification, Product
)


def inboard_rail_code(depth):
    """Stock code for the inboard hanging rail support of a given depth.
    Stock holds these as HRS{depth}/3N (21, 24, 27 are known to be /3N); an older plain
    HRS{depth} row may also exist, often inactive. Prefer whichever one is active."""
    depth = str(depth)
    cands = [f'HRS{depth}/3N', f'HRS{depth}']
    found = {}
    for pr in Product.objects.filter(Q(code__iexact=cands[0]) | Q(code__iexact=cands[1])):
        found[pr.code.upper()] = pr
    for c in cands:
        pr = found.get(c.upper())
        if pr and pr.is_active:
            return c
    return cands[0] if depth in ('21', '24', '27') else cands[1]


# ── Stock-mirrored pricing/weight (Stock is the source of truth) ──────────────

def stock_lookup_by_code():
    """Return {CODE_UPPER: Product} for every stock item, for exact-code matching
    from the Price List and costing formulas. Stock is the single source of
    truth for buying price and weight."""
    return {p.code.strip().upper(): p for p in Product.objects.all()}


# Some Price List codes have more than one valid Stock match — e.g. Trimline
# posts come in more than one colour (TP## = grey, BTP## = blue) at the same
# cost/weight. Try the canonical code first, then these alternates.
STOCK_CODE_ALIASES = {
    'TP48': ['BTP48'], 'TP60': ['BTP60'], 'TP72': ['BTP72'], 'TP84': ['BTP84'],
    'TP96': ['BTP96'], 'TP108': ['BTP108'], 'TP120': ['BTP120'],
}


def resolve_stock_item(code, stock_by_code):
    """Look up a Product for a Price List / formula code, trying known
    colour-variant aliases when the exact code isn't in Stock. Returns the
    Product or None."""
    key = (code or '').strip().upper()
    if key in stock_by_code:
        return stock_by_code[key]
    for alias in STOCK_CODE_ALIASES.get(key, []):
        if alias.upper() in stock_by_code:
            return stock_by_code[alias.upper()]
    return None


# ── Project helpers ────────────────────────────────────────────────────────────

def _snap(p):
    return {
        'status': p.status, 'customer': p.customer, 'location': p.location,
        'assigned_to': str(p.assigned_to), 'description': p.description,
        'sales_order': p.sales_order,
    }


def _log_changes(p, before, after, user):
    for field, old_val in before.items():
        new_val = after.get(field)
        if old_val != new_val:
            ProjectLog.objects.create(
                project=p, user=user, field=field.replace('_', ' ').title(),
                old_value=str(old_val) if old_val else '',
                new_value=str(new_val) if new_val else '',
            )


def _add_working_days(start_dt, n):
    """Return a datetime n working days after start_dt, skipping weekends."""
    current = start_dt
    added = 0
    while added < n:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


def _handle_quoted_status_reminders(project, old_status, new_status, user):
    """Auto-create follow-up reminders when status moves to Quoted,
    and cancel them when it moves away from Quoted."""
    if new_status == 'quoted' and old_status != 'quoted':
        if project.assigned_to:
            now = timezone.now()
            for days in (7, 14):
                remind_at = _add_working_days(now, days)
                Reminder.objects.create(
                    project=project, notify_user=project.assigned_to,
                    created_by=user,
                    message=f"Follow up with {project.customer} — quote sent {days} working days ago ({project.project_name})",
                    remind_at=remind_at,
                )
    elif old_status == 'quoted' and new_status != 'quoted':
        project.reminders.filter(sent=False, message__icontains='Follow up with').delete()


def _log_po_event(po, user, field, old_value='', new_value=''):
    """Log a PO action to the Activity Log when the PO has a linked project."""
    if po.project_id:
        ProjectLog.objects.create(
            project=po.project, user=user, field=field,
            old_value=old_value, new_value=new_value,
        )


def _can_view_reports(user):
    """Sales Summary / Daily Accounts Report access — separate from is_staff.
    Superusers always have it; everyone else needs it explicitly ticked on
    their Staff Profile."""
    if user.is_superuser:
        return True
    profile = getattr(user, 'profile', None)
    return bool(profile and profile.can_view_reports)


def _client_ip(request):
    """The real visitor IP, not the proxy's — Railway (and most hosts)
    sit behind a reverse proxy, so request.META['REMOTE_ADDR'] would
    otherwise return the proxy's own address. X-Forwarded-For lists the
    original client first, followed by each hop after it."""
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def require_feature(flag_name):
    """Blocks a view when the named FEATURE_FLAGS entry is off — used for
    the operational features (POs, stock valuation, etc.) that are hidden
    from navigation but whose code and data stay in place, so this stops
    direct URL access too, not just the nav link. Flip the flag back on in
    settings.py to restore access with no code changes needed."""
    from functools import wraps
    from django.conf import settings
    from django.contrib import messages as django_messages
    from django.shortcuts import redirect

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if not settings.FEATURE_FLAGS.get(flag_name, True):
                django_messages.error(request, "This feature isn't currently available.")
                return redirect('dashboard')
            return view_func(request, *args, **kwargs)
        return wrapped
    return decorator


BTB_FALLBACK_PER_FIXING = 0.10   # used only when Stock has no cost price for the bolt + nut


def _kit_unit_cost(kit):
    """Cost of ONE set of a fixing kit ({stock code: qty per set}), from Stock cost prices.
    Returns (unit_cost, [codes with no cost price])."""
    total, missing = 0.0, []
    for code, per in kit.items():
        pr = Product.objects.filter(code__iexact=code).first()
        c = float(pr.cost_price) if pr else 0.0
        if c <= 0:
            missing.append(code)
        total += c * per
    return total, missing


def cost_buying_parts(cost, lines=None):
    """THE one calculation of a costing option's buying price. Everything that shows or
    uses a buying/sell price (costing page, print, quotes, pro-forma, reports) calls this,
    so the numbers can never disagree.

    buying = cost lines + project accessories + fixings (wall, back-to-back, mobile base)
    - Accessories: an active accessory with a custom qty is charged at that qty (ticked or not);
      otherwise a ticked one is charged once per upright.
    - Fixings: the same stock kits the picking list uses, at Stock cost price."""
    from .picking import BACK_TO_BACK_KIT, MOBILE_BASE_KIT, WALL_FIXING_KIT
    lines = list(lines) if lines is not None else list(cost.lines.all())
    lines_total = sum(l.line_total for l in lines)
    total_uprights = sum(int(float(l.quantity)) * 2 for l in lines if l.line_type == 'frame')

    overrides = {o.accessory_id: o.override_qty for o in AccessoryOverride.objects.filter(cost=cost)}
    selected = set(cost.accessories.values_list('id', flat=True))
    acc_totals = {}
    for acc in UprightAccessory.objects.filter(is_active=True):
        qty = overrides.get(acc.id)
        if qty is None:
            qty = total_uprights if acc.id in selected else 0
        acc_totals[acc.id] = round(float(acc.unit_price) * qty, 2)
    acc_total = sum(acc_totals.values())

    wall_unit, wall_missing = _kit_unit_cost(WALL_FIXING_KIT)
    btb_unit, btb_missing = _kit_unit_cost(BACK_TO_BACK_KIT)
    if btb_unit <= 0:
        btb_unit, btb_missing = BTB_FALLBACK_PER_FIXING, []
    mob_unit, mob_missing = _kit_unit_cost(MOBILE_BASE_KIT)
    n_wall, n_btb, n_mob = int(cost.wall_fixings or 0), int(cost.back_to_back_fixings or 0), int(cost.mobile_base_sets or 0)
    wall_total, btb_total, mob_total = round(wall_unit * n_wall, 2), round(btb_unit * n_btb, 2), round(mob_unit * n_mob, 2)
    missing = []
    for n, codes in ((n_wall, wall_missing), (n_btb, btb_missing), (n_mob, mob_missing)):
        if n:
            missing += [c for c in codes if c not in missing]
    return {
        'lines_total': round(lines_total, 2), 'acc_total': round(acc_total, 2), 'acc_totals': acc_totals,
        'wall_total': wall_total, 'btb_total': btb_total, 'mob_total': mob_total,
        'fixings_total': round(wall_total + btb_total + mob_total, 2),
        'missing_prices': missing, 'total_uprights': total_uprights,
        'buying_total': round(lines_total + acc_total + wall_total + btb_total + mob_total, 2),
    }


def _calc_sell_price(cost):
    """Read-only sell-price calculation from already-saved cost lines."""
    buying_total = cost_buying_parts(cost)['buying_total']
    markup_amount = buying_total * float(cost.markup) / 100
    sell_price = round(buying_total + markup_amount + float(cost.labour) + float(cost.delivery), 2)
    return sell_price


def _calc_cost_flags(cost, breakdown=None):
    """Concrete, specific pricing checks worth a staff member's attention -
    not a vague 'this looks off', but plain rules that can each be pointed
    to directly. Returns a list of short warning strings; empty list means
    nothing flagged. Thresholds (15%/100% markup) match what EZR consider
    a normal range."""
    if breakdown is None:
        breakdown = _calc_cost_breakdown(cost)
    flags = []
    markup_pct = float(cost.markup)
    if markup_pct <= 0:
        flags.append('Selling at cost or a loss — markup is 0% or negative.')
    elif markup_pct < 15:
        flags.append(f'Markup is unusually low ({markup_pct:g}%, normal range is 15–100%).')
    elif markup_pct > 100:
        flags.append(f'Markup is unusually high ({markup_pct:g}%, normal range is 15–100%).')
    zero_cost_stock = [l.description for l in cost.lines.all() if l.line_type == 'stock' and not l.unit_cost]
    if zero_cost_stock:
        flags.append(f'{len(zero_cost_stock)} stock item(s) have £0 unit cost: {", ".join(zero_cost_stock[:3])}{"…" if len(zero_cost_stock) > 3 else ""}')
    zero_cost_extras = [l.description for l in cost.lines.all() if l.line_type == 'extras' and not l.unit_cost]
    if zero_cost_extras:
        flags.append(f'{len(zero_cost_extras)} extra line(s) have £0 unit cost: {", ".join(zero_cost_extras[:3])}{"…" if len(zero_cost_extras) > 3 else ""}')
    return flags



def _calc_cost_breakdown(cost):
    """Full read-only cost/sell/margin breakdown for one costing option, for
    reporting (Sales Summary). Margin = markup only — labour and delivery
    are treated as pass-through costs, not profit, matching how the costing
    screen itself presents them."""
    buying_total = cost_buying_parts(cost)['buying_total']
    markup_amount = round(buying_total * float(cost.markup) / 100, 2)
    labour = float(cost.labour)
    delivery = float(cost.delivery)
    ex_works_price = round(buying_total + markup_amount, 2)
    sell_price = round(ex_works_price + labour + delivery, 2)
    # Margin % is based on the ex-works price (goods only), not the full
    # invoice — delivery and labour carry no markup, so including them would
    # dilute the % and misrepresent the markup actually applied to the goods.
    margin_pct = round((markup_amount / ex_works_price) * 100, 1) if ex_works_price else 0
    return {
        'buying_total': round(buying_total, 2),
        'labour': labour,
        'delivery': delivery,
        'ex_works_price': ex_works_price,
        'sell_price': sell_price,
        'margin': markup_amount,
        'margin_pct': margin_pct,
    }


def _initials(name):
    """First letter of first + last name."""
    parts = name.split()
    if not parts:
        return ''
    if len(parts) == 1:
        return parts[0][:1].upper()
    return (parts[0][:1] + parts[-1][:1]).upper()


def _next_project_number():
    from ..models import Project as Proj
    last = Proj.all_objects.filter(project_number__isnull=False).order_by('-project_number').first()
    return (last.project_number + 1) if last else 1


def _po_locked_response(po):
    if po.locked:
        from django.http import JsonResponse
        return JsonResponse({'error': 'This PO is locked. Unlock it to make changes.'}, status=400)
    return None


def _picking_locked_response(pl):
    from django.conf import settings
    if pl.allocated and settings.FEATURE_FLAGS.get('stock_movements'):
        from django.http import JsonResponse
        return JsonResponse({'error': 'This picking list is allocated — unlock stock first.'}, status=400)
    return None


def _can_edit_prices(user):
    return user.is_staff
