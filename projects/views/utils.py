"""Shared helpers used across multiple view modules."""
import math
import random
from datetime import date, timedelta
from django.utils import timezone
from django.db.models import Q
from django.contrib.auth.models import User

from ..models import (
    Project, ProjectLog, ProjectCost, ProjectCostLine, UprightAccessory,
    PurchaseOrder, Reminder, Notification, Product
)


# ── Stock-mirrored pricing/weight (Stock is the source of truth) ──────────────

def stock_lookup_by_code():
    """Return {CODE_UPPER: Product} for every stock item, for exact-code matching
    from the Price List and costing formulas. Stock is the single source of
    truth for buying price and weight."""
    return {p.code.strip().upper(): p for p in Product.objects.all()}


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


def _calc_sell_price(cost):
    """Read-only sell-price calculation from already-saved cost lines."""
    lines = list(cost.lines.all())
    buying_total = sum(l.line_total for l in lines)
    total_uprights = sum(int(float(l.quantity)) * 2 for l in lines if l.line_type == 'frame')
    if hasattr(cost, 'accessories'):
        for acc in cost.accessories.all():
            buying_total += float(acc.unit_price) * total_uprights
    markup_amount = buying_total * float(cost.markup) / 100
    sell_price = round(buying_total + markup_amount + float(cost.labour) + float(cost.delivery), 2)
    return sell_price


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
    last = Proj.objects.filter(project_number__isnull=False).order_by('-project_number').first()
    return (last.project_number + 1) if last else 1


def _po_locked_response(po):
    if po.locked:
        from django.http import JsonResponse
        return JsonResponse({'error': 'This PO is locked. Unlock it to make changes.'}, status=400)
    return None


def _picking_locked_response(pl):
    if pl.allocated:
        from django.http import JsonResponse
        return JsonResponse({'error': 'This picking list is allocated — unlock stock first.'}, status=400)
    return None


def _can_edit_prices(user):
    return user.is_staff
