import math
import random
from django.utils import timezone
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.db.models import Q, F as models_F, Count
from django.views.decorators.http import require_POST
from datetime import date, timedelta
import json

from ..models import Project, ProjectLog, Customer, Comment, Message, Notification, TeamMessage, StaffProfile, LeaveRequest, InstallationReport, ReportPhoto, SatisfactionNote, CustomerProfile, ProjectDocument, Product, PickingList, PickingListItem, PickingTemplate, PickingTemplateItem, MaterialPrice, ProjectCost, ProjectCostLine, UprightAccessory, AccessoryOverride, Reminder, FittingCrew, Supplier, PurchaseOrder, PurchaseOrderLine, StockMovement, FittingNote, ProjectQuote, PriceListItem, QuotePhoto, QuoteAttachedPhoto, ProformaInvoice, DeliveryPhase, ProjectPresence
from ..forms import RegisterForm, ProjectForm


# ── Auth ──────────────────────────────────────────────────────────────────────

from django.contrib.auth.views import (
    PasswordResetView, PasswordResetDoneView,
    PasswordResetConfirmView, PasswordResetCompleteView,
)

@login_required
@require_POST
def project_cost_refresh_prices(request, pk, cost_pk):
    """Manually force frame/shelf/trolley lines to current prices, regardless
    of project status — the deliberate escape hatch for an old costing that
    genuinely needs re-pricing (e.g. re-quoting a stale enquiry)."""
    project = get_object_or_404(Project, pk=pk)
    cost = get_object_or_404(ProjectCost, pk=cost_pk, project=project)
    from .price_list import ls_calc_frame_price, ls_calc_shelf_price, ls_calc_trolley_price
    mat = {m.name: float(m.price_per_sqft) for m in MaterialPrice.objects.all()}
    chipboard = mat.get('chipboard', 0.50)
    melamine_p = mat.get('melamine', 0.75)
    chip18 = mat.get('chipboard_18mm', melamine_p)

    updated = 0
    for line in cost.lines.all():
        if line.line_type == 'frame':
            parts = [p.strip() for p in line.size.replace('"', '').split('x')]
            h = d = None
            if len(parts) == 2:
                h, d = parts[0], parts[1]
            elif len(parts) == 4 and parts[0] == 'frame':
                h, d = parts[1], parts[2]
            if h and d:
                line.unit_cost = calc_frame_price(h, d)
                line.save()
                updated += 1
        elif line.line_type == 'shelf':
            parts = line.size.replace('"', '').split('x')
            if len(parts) == 3:
                stype, w, d = parts[0].strip(), parts[1].strip(), parts[2].strip()
                line.unit_cost = calc_shelf_price(stype, w, d, line.melamine, chipboard, melamine_p, chip18, no_deck=line.no_deck)
                line.save()
                updated += 1
        elif line.line_type == 'ls_frame':
            # Real format: "lsframe x H x D x galv" (4 parts)
            parts = [p.strip() for p in line.size.replace('"', '').split('x')]
            if len(parts) == 4:
                try:
                    line.unit_cost = ls_calc_frame_price(int(parts[1]), int(parts[2]))
                    line.save()
                    updated += 1
                except (ValueError, TypeError):
                    pass
        elif line.line_type == 'ls_shelf':
            # Real format: "lsshelf x W x D" (3 parts)
            parts = [p.strip() for p in line.size.replace('"', '').split('x')]
            if len(parts) == 3:
                try:
                    line.unit_cost = ls_calc_shelf_price(int(parts[1]), int(parts[2]))
                    line.save()
                    updated += 1
                except (ValueError, TypeError):
                    pass
        elif line.line_type == 'ls_trolley':
            # Real format: "lstrolley x D" (2 parts)
            parts = [p.strip() for p in line.size.replace('"', '').split('x')]
            if len(parts) == 2:
                try:
                    line.unit_cost = ls_calc_trolley_price(int(parts[1]))
                    line.save()
                    updated += 1
                except (ValueError, TypeError):
                    pass

    ProjectLog.objects.create(
        project=project, user=request.user,
        field='Costing prices refreshed', old_value='', new_value=f'{cost.label}: {updated} line(s)',
    )
    return JsonResponse({'ok': True, 'updated': updated})


@login_required
def project_cost(request, pk, cost_pk=None):
    project = get_object_or_404(Project, pk=pk)
    # Get or create the first cost option
    all_costs = list(project.costs.order_by('order', 'id'))
    if not all_costs:
        cost = ProjectCost.objects.create(project=project, label='Option A', order=0)
        all_costs = [cost]
    if cost_pk:
        cost = get_object_or_404(ProjectCost, pk=cost_pk, project=project)
    else:
        cost = all_costs[0]
    lines = cost.lines.select_related('product').all()
    mat = {m.name: float(m.price_per_sqft) for m in MaterialPrice.objects.all()}
    chipboard = mat.get('chipboard', 0.50)
    melamine_p = mat.get('melamine', 0.75)
    chip18 = mat.get('chipboard_18mm', melamine_p)

    # Recalculate dynamic prices for frame/shelf lines — but only while the
    # project is still in Enquiry. Once it's moved on (Quoted or later), a
    # price may already be in front of the customer, so lines are frozen at
    # whatever they were costed at; staff can still force a refresh via the
    # "Refresh to current prices" button if genuinely needed.
    if project.status == 'enquiry':
        for line in lines:
            if line.line_type == 'frame':
                # Real format is "frame x H x D x PREFIX" (4 parts) — a bare
                # "H x D" (2 parts) is supported too for any older rows.
                parts = [p.strip() for p in line.size.replace('"', '').split('x')]
                h = d = None
                if len(parts) == 2:
                    h, d = parts[0], parts[1]
                elif len(parts) == 4 and parts[0] == 'frame':
                    h, d = parts[1], parts[2]
                if h and d:
                    line.unit_cost = calc_frame_price(h, d)
                    line.save()
            elif line.line_type == 'shelf':
                parts = line.size.replace('"','').split('x')
                if len(parts) == 3:
                    stype, w, d = parts[0].strip(), parts[1].strip(), parts[2].strip()
                    line.unit_cost = calc_shelf_price(stype, w, d, line.melamine, chipboard, melamine_p, chip18, no_deck=line.no_deck)
                    line.save()

    lines = cost.lines.select_related('product').all()

    # Auto-fix old NS lines that should be inhang/outhang (saved before those types existed)
    for line in lines:
        if line.line_type == 'ns':
            d = line.description
            if 'Inboard Hanging' in d:
                line.line_type = 'inhang'; line.save()
            elif 'Outboard Hanging Rail' in d:
                line.line_type = 'outhang'; line.save()
            elif 'Wipe Board' in d:
                line.line_type = 'wipe'; line.save()
            elif 'Mesh Panel' in d:
                line.line_type = 'mesh'; line.save()
            elif 'Load Sign' in d:
                line.line_type = 'loadsign'; line.save()
            elif 'Top Tie' in d:
                line.line_type = 'toptie'; line.save()
            elif 'Beams' in d and ('TWB' in d or 'TFCV' in d):
                line.line_type = 'beams'
                # Fix size field too so picking_reference can use it
                import re as _re
                m = _re.search(r'(TFCV|TWB) Beams ([\d.]+)"', d)
                if m:
                    btype = m.group(1).lower()
                    w = m.group(2)
                    line.size = f'{btype} x {w}'
                line.save()

    lines = cost.lines.select_related('product').all()
    buying_total = sum(l.line_total for l in lines)

    # Count total uprights from frame lines (must be before accessory calc)
    total_uprights = 0
    for line in lines:
        if line.line_type == 'frame':
            total_uprights += int(float(line.quantity)) * 2

    # Add selected accessory costs
    if hasattr(cost, 'accessories'):
        for acc in cost.accessories.all():
            buying_total += float(acc.unit_price) * total_uprights
    markup_amount = buying_total * float(cost.markup) / 100
    sell_price = buying_total + markup_amount + float(cost.labour) + float(cost.delivery)
    # Margin on goods only — exclude labour (installation) and delivery
    goods_sell = buying_total + markup_amount
    margin = (markup_amount / goods_sell * 100) if goods_sell else 0

    accessories = UprightAccessory.objects.filter(is_active=True)
    selected_acc_ids = set(cost.accessories.values_list('id', flat=True))
    overrides = {o.accessory_id: o.override_qty for o in AccessoryOverride.objects.filter(cost=cost)}
    acc_data = []
    for acc in accessories:
        override = overrides.get(acc.id)
        eff_qty = override if override is not None else total_uprights
        acc_data.append({
            'id': acc.id,
            'name': acc.name,
            'code': acc.code,
            'unit_price': float(acc.unit_price),
            'checked': acc.id in selected_acc_ids,
            'override_qty': override,
            'total_cost': round(float(acc.unit_price) * eff_qty, 2) if acc.id in selected_acc_ids else 0,
        })

    # Build accessory list with upright counts for picking reference
    overrides_map = {o.accessory_id: o.override_qty for o in AccessoryOverride.objects.filter(cost=cost)}
    acc_with_uprights = [
        {'code': acc.code, 'uprights': overrides_map.get(acc.id) if overrides_map.get(acc.id) is not None else total_uprights}
        for acc in cost.accessories.all()
    ]
    picking_ref = generate_picking_reference(list(lines), acc_with_uprights, cost.wall_fixings, cost.back_to_back_fixings, cost.mobile_base_sets)
    # Enrich with product descriptions
    picking_ref_enriched = []
    for code, qty in sorted(picking_ref.items()):
        ns2 = parse_ns2_key(code)
        if ns2:
            _, _, ns2_desc = ns2
            ns2_prod = Product.objects.filter(code__iexact='NS2', is_active=True).first()
            picking_ref_enriched.append({
                'code': 'NS2',
                'qty': qty,
                'description': ns2_desc,
                'in_stock': float(ns2_prod.quantity) if ns2_prod else None,
            })
            continue
        prod = Product.objects.filter(code__iexact=code).first()
        # Skip if product exists but is deactivated
        if prod and not prod.is_active:
            continue
        picking_ref_enriched.append({
            'code': code,
            'qty': qty,
            'description': prod.description if prod else '—',
            'in_stock': float(prod.quantity) if prod and prod.is_active else None,
        })
    # ── Weight estimate from the picking reference ──
    ITEM_WEIGHTS = {
        'BTP48':2.0,'BTP60':2.3,'BTP72':2.7,'BTP84':3.2,'BTP96':3.6,'BTP108':4.1,'BTP120':4.5,
        'TP48':2.0,'TP60':2.3,'TP72':2.7,'TP84':3.2,'TP96':3.6,'TP108':4.1,'TP120':4.5,
        'TPC12':0.5,'TPC15':0.5,'TPC18':0.6,'TPC21':0.8,'TPC24':0.9,'TPC27':1.0,'TPC30':1.2,'TPC36':1.3,
        'TFCL36':1.6,'TFCL39.5':1.6,'TFCL48':2.0,
        'TFCV24':1.4,'TFCV30':1.4,'TFCV36':1.5,'TFCV39.5':1.6,'TFCV43.5':1.8,'TFCV48':2.0,
        'TBC36':1.5,'TBC39.5':1.8,'TBC48':2.0,
        'TWB36':2.2,'TWB39.5':3.2,'TWB48':3.2,'TWB60':3.8,'TWB66':4.2,'TWB72':4.6,
        'TCTB18':0.6,'TCTB24':0.8,'TCTB30':1.0,'TCTB36':1.2,
        'SB18':0.6,'SB24':0.8,'SB30':1.0,'SB36':1.2,
        'FP':0.1,'SMFOOT':0.12,'TFP':0.01,
    }
    import re as _re_wt
    total_weight = 0.0
    for code, qty in picking_ref.items():
        cu = code.upper()
        prod = Product.objects.filter(code__iexact=code).first()
        desc = prod.description if prod else ''
        if desc and _re_wt.search(r'CHIPBOARD|MELAMINE|MFC|TIMBER DECK|BOARD', desc, _re_wt.I):
            m = _re_wt.match(r'^\s*(\d+)\s*[xX]\s*(\d+)', desc)
            if m:
                sqft = (int(m.group(1)) * int(m.group(2))) / 92903.04
                total_weight += sqft * float(qty)
        elif cu in ITEM_WEIGHTS:
            total_weight += ITEM_WEIGHTS[cu] * float(qty)
    total_weight = round(total_weight, 1)

    return render(request, 'projects/project_cost.html', {
        'project': project, 'cost': cost, 'lines': lines,
        'all_costs': all_costs,
        'has_quote': hasattr(cost, 'quote') and cost.quote is not None,
        'picking_templates': PickingTemplate.objects.all().order_by('name'),
        'picking_ref': picking_ref_enriched,
        'accessories': acc_data,
        'total_uprights': total_uprights,
        'buying_total': round(buying_total, 2),
        'markup_amount': round(markup_amount, 2),
        'sell_price': round(sell_price, 2),
        'margin': round(margin, 1),
        'total_weight': total_weight,
        'chipboard_price': chipboard,
        'melamine_price': melamine_p,
        'chipboard_18mm_price': chip18,
        'frame_heights': FRAME_HEIGHTS,
        'frame_depths': FRAME_DEPTHS,
        'tfcv_widths': TFCV_WIDTHS,
        'twb_widths': TWB_WIDTHS,
        'shelf_depths': SHELF_DEPTHS,
        'in_hang_widths': IN_HANG_WIDTHS,
        'in_hang_depths': IN_HANG_DEPTHS,
        'out_hang_lengths': list(OUT_HANG.keys()),
        'wipe_depths': list(WIPE_BOARDS.keys()),
        'load_sign_types': list(LOAD_SIGNS.keys()),
        'extras_items': [
            {'key': 'mesh_decks',   'label': 'Mesh decks'},
            {'key': 'metal_decks',  'label': 'Metal decks'},
            {'key': 'fr_mdf_decks', 'label': 'Fire retardant MDF decks'},
            {'key': 'mobile_bases', 'label': 'Mobile bases'},
        ],
        'project_pk': project.pk,
    })




@login_required
@require_POST
def project_cost_save(request, pk):
    project = get_object_or_404(Project, pk=pk)
    data = json.loads(request.body)
    cost_pk = data.get('cost_pk')
    if cost_pk:
        cost = get_object_or_404(ProjectCost, pk=cost_pk, project=project)
    else:
        cost = project.costs.filter(is_accepted=True).first() or project.costs.first()
        if not cost:
            cost = ProjectCost.objects.create(project=project, label='Option A')
    cost.markup   = data.get('markup', cost.markup)
    cost.labour   = data.get('labour', cost.labour)
    cost.delivery = data.get('delivery', cost.delivery)
    cost.notes    = data.get('notes', cost.notes)
    cost.updated_by = request.user
    cost.save()
    return JsonResponse({'ok': True})




@login_required
@require_POST
def cost_line_add(request, pk):
    project = get_object_or_404(Project, pk=pk)
    # Support multiple cost options — read cost_pk from body or use active option
    try:
        _body = json.loads(request.body) if request.body else {}
        _cost_pk = _body.get('cost_pk')
    except Exception:
        _cost_pk = None
    if _cost_pk:
        cost = get_object_or_404(ProjectCost, pk=_cost_pk, project=project)
    else:
        cost = project.costs.filter(is_accepted=True).first() or project.costs.first()
        if not cost:
            return JsonResponse({'error': 'No costing found'}, status=400)
    data = json.loads(request.body)
    mat = {m.name: float(m.price_per_sqft) for m in MaterialPrice.objects.all()}
    chipboard = mat.get('chipboard', 0.50)
    melamine_p = mat.get('melamine', 0.75)
    chip18 = mat.get('chipboard_18mm', melamine_p)

    ltype = data.get('line_type')
    # Local import avoids a circular import — price_list.py imports from
    # this module at the top level, so this can't be a module-level import.
    from .price_list import ls_calc_frame_price, ls_calc_shelf_price, ls_calc_trolley_price
    qty   = float(data.get('quantity', 1))
    sort  = cost.lines.count()

    if ltype == 'frame':
        grey = bool(data.get('grey_posts', False))
        post_prefix = 'TP' if grey else 'BTP'
        rows = data.get('rows', [])
        for row in rows:
            h, d = row.get('height',''), row.get('depth','')
            row_qty = float(row.get('quantity', 0))
            if not h or not d or row_qty <= 0:
                continue
            unit_cost = calc_frame_price(h, d)
            size_str = f'{h}" x {d}"'
            desc = f'Frame {size_str} ({post_prefix})'
            ProjectCostLine.objects.create(cost=cost, line_type='frame', description=desc,
                size=f'frame x {h} x {d} x {post_prefix}', quantity=row_qty, unit_cost=unit_cost, sort_order=sort)
            sort += 1

    elif ltype == 'shelf_tfcv':
        rows = data.get('rows', [])
        for row in rows:
            w, d = row.get('width',''), row.get('depth','')
            mel = bool(row.get('melamine', False))
            no_deck = bool(row.get('no_deck', False))
            row_qty = float(row.get('quantity', 0))
            if not w or not d or row_qty <= 0:
                continue
            size = f'tfcv x {w} x {d}'
            unit_cost = calc_shelf_price('tfcv', w, d, mel, chipboard, melamine_p, chip18, no_deck=no_deck)
            mat_label = 'No Deck' if no_deck else ('Melamine' if mel else 'Chipboard')
            desc = f'TFCV Shelf {w}" x {d}" ({mat_label})'
            ProjectCostLine.objects.create(cost=cost, line_type='shelf', description=desc,
                size=size, melamine=mel, no_deck=no_deck, quantity=row_qty, unit_cost=unit_cost, sort_order=sort)
            sort += 1

    elif ltype == 'shelf_twb':
        rows = data.get('rows', [])
        for row in rows:
            w, d = row.get('width',''), row.get('depth','')
            mel = bool(row.get('melamine', False))
            no_deck = bool(row.get('no_deck', False))
            row_qty = float(row.get('quantity', 0))
            if not w or not d or row_qty <= 0:
                continue
            size = f'twb x {w} x {d}'
            unit_cost = calc_shelf_price('twb', w, d, mel, chipboard, melamine_p, chip18, no_deck=no_deck)
            mat_label = 'No Deck' if no_deck else ('Melamine' if mel else 'Chipboard')
            desc = f'TWB Shelf {w}" x {d}" ({mat_label})'
            ProjectCostLine.objects.create(cost=cost, line_type='shelf', description=desc,
                size=size, melamine=mel, no_deck=no_deck, quantity=row_qty, unit_cost=unit_cost, sort_order=sort)
            sort += 1

    elif ltype == 'stock':
        product_id = data.get('product_id')
        product = get_object_or_404(Product, pk=product_id, is_active=True)
        unit_cost = float(product.sales_price) if product.sales_price else 0
        ProjectCostLine.objects.create(cost=cost, line_type='stock',
            description=f'{product.code} — {product.description}',
            product=product, quantity=qty, unit_cost=unit_cost, sort_order=sort)

    elif ltype == 'ns':
        desc = data.get('description', '').strip()
        unit_cost = float(data.get('unit_cost', 0))
        ProjectCostLine.objects.create(cost=cost, line_type='ns', description=desc,
            quantity=qty, unit_cost=unit_cost, sort_order=sort)

    elif ltype == 'in_hang_multi':
        rows = data.get('rows', [])
        for row in rows:
            w, d = row.get('width',''), row.get('depth','')
            row_qty = float(row.get('quantity') or 0)
            if not w or not d or row_qty <= 0:
                continue
            unit_cost = calc_in_hang_price(w, d)
            desc = f'Inboard Hanging {w}" x {d}"'
            ProjectCostLine.objects.create(cost=cost, line_type='inhang', description=desc,
                quantity=row_qty, unit_cost=unit_cost, sort_order=sort)
            sort += 1

    elif ltype == 'out_hang_multi':
        rows = data.get('rows', [])
        for row in rows:
            length = row.get('length','915mm')
            row_qty = float(row.get('quantity') or 0)
            if row_qty <= 0:
                continue
            unit_cost = OUT_HANG.get(length, 2.93)
            desc = f'Outboard Hanging Rail {length}'
            ProjectCostLine.objects.create(cost=cost, line_type='outhang', description=desc,
                quantity=row_qty, unit_cost=unit_cost, sort_order=sort)
            sort += 1

    elif ltype == 'in_hang':
        w, d = data.get('width',''), data.get('depth','')
        unit_cost = calc_in_hang_price(w, d)
        desc = f'Inboard Hanging {w}" x {d}"'
        ProjectCostLine.objects.create(cost=cost, line_type='inhang', description=desc,
            quantity=qty, unit_cost=unit_cost, sort_order=sort)

    elif ltype == 'out_hang':
        length = data.get('length','915mm')
        unit_cost = OUT_HANG.get(length, 2.93)
        desc = f'Outboard Hanging Rail {length}'
        ProjectCostLine.objects.create(cost=cost, line_type='outhang', description=desc,
            quantity=qty, unit_cost=unit_cost, sort_order=sort)

    elif ltype == 'wipe_board':
        depth = data.get('depth','12')
        unit_cost = WIPE_BOARDS.get(depth, 0)
        desc = f'Wipe Board {depth}" depth'
        ProjectCostLine.objects.create(cost=cost, line_type='wipe', description=desc,
            quantity=qty, unit_cost=unit_cost, sort_order=sort)

    elif ltype == 'mesh_panel':
        mesh_size = data.get('mesh_size', 'MP120X48')
        cut_to = (data.get('cut_to') or '').strip()
        # Size labels
        SIZE_LABELS = {
            'MP120X48': '120x48"',
            'MP108X48': '108x48"',
            'MP96X48':  '96x48"',
            'MP84X48':  '84x48"',
            'MP72X48':  '72x48"',
        }
        size_label = SIZE_LABELS.get(mesh_size, mesh_size)
        desc = f'Mesh Panel {size_label}'
        if cut_to:
            desc += f' [cut to {cut_to}]'
        ProjectCostLine.objects.create(cost=cost, line_type='mesh', description=desc,
            quantity=qty, unit_cost=MESH_PANEL_PRICES.get(mesh_size, 25.0), sort_order=sort,
            size=mesh_size)  # store code in size field

    elif ltype == 'load_sign':
        sign_type = data.get('sign_type','Laminated')
        unit_cost = LOAD_SIGNS.get(sign_type, 3.0)
        desc = f'Load Sign ({sign_type})'
        ProjectCostLine.objects.create(cost=cost, line_type='loadsign', description=desc,
            quantity=qty, unit_cost=unit_cost, sort_order=sort)

    elif ltype == 'top_tie':
        length = data.get('length','').strip()
        desc = f'Top Tie{" " + length if length else ""}'
        ProjectCostLine.objects.create(cost=cost, line_type='toptie', description=desc,
            quantity=qty, unit_cost=TOP_TIE_PRICE, sort_order=sort)


    elif ltype == 'stock_by_code':
        code = data.get('code','').strip()
        product = Product.objects.filter(code__iexact=code, is_active=True).first()
        if product:
            unit_cost = float(product.sales_price) if product.sales_price else 0
            ProjectCostLine.objects.create(cost=cost, line_type='extras',
                description=f'{product.code} — {product.description}',
                product=product, quantity=qty, unit_cost=unit_cost, sort_order=sort)

    elif ltype == 'extras':
        desc = data.get('description','').strip()
        unit_cost = float(data.get('unit_cost', 0))
        if desc:
            ProjectCostLine.objects.create(cost=cost, line_type='extras', description=desc,
                quantity=qty, unit_cost=unit_cost, sort_order=sort)

    elif ltype == 'beams':
        rows = data.get('rows', [])
        for row in rows:
            w = row.get('width','')
            btype = row.get('beam_type', 'tfcv').lower()
            row_qty = float(row.get('quantity', 0))
            if not w or row_qty <= 0:
                continue
            comp = trimline_component_prices()
            if btype == 'twb':
                unit_cost = comp.get(f'TWB{w}', TWB_PRICES.get(w, 0))
            else:
                unit_cost = comp.get(f'TFCV{w}', TFCV_CONN.get(w, 0))
            label = 'TWB' if btype == 'twb' else 'TFCV'
            desc = f'{label} Beams {w}"'
            ProjectCostLine.objects.create(cost=cost, line_type='beams', description=desc,
                size=f'{btype} x {w}', quantity=row_qty, unit_cost=unit_cost, sort_order=sort)
            sort += 1

    elif ltype == 'ls_frame':
        rows = data.get('rows', [])
        for row in rows:
            try:
                h = int(row.get('height'))
                d = int(row.get('depth'))
            except (TypeError, ValueError):
                continue
            row_qty = float(row.get('quantity', 0))
            if row_qty <= 0:
                continue
            unit_cost = ls_calc_frame_price(h, d)
            desc = f'LS Frame {h} x {d} (Galv)'
            ProjectCostLine.objects.create(cost=cost, line_type='ls_frame', product_line='longspan',
                description=desc, size=f'lsframe x {h} x {d} x galv',
                quantity=row_qty, unit_cost=unit_cost, sort_order=sort)
            sort += 1

    elif ltype == 'ls_shelf':
        rows = data.get('rows', [])
        for row in rows:
            try:
                w = int(row.get('width'))
                d = int(row.get('depth'))
            except (TypeError, ValueError):
                continue
            row_qty = float(row.get('quantity', 0))
            if row_qty <= 0:
                continue
            unit_cost = ls_calc_shelf_price(w, d)
            desc = f'LS Shelf Level {w} x {d}'
            ProjectCostLine.objects.create(cost=cost, line_type='ls_shelf', product_line='longspan',
                description=desc, size=f'lsshelf x {w} x {d}',
                quantity=row_qty, unit_cost=unit_cost, sort_order=sort)
            sort += 1

    elif ltype == 'ls_trolley':
        try:
            d = int(data.get('depth'))
        except (TypeError, ValueError):
            d = None
        tqty = float(data.get('quantity', 0))
        if d and tqty > 0:
            unit_cost = ls_calc_trolley_price(d)
            desc = f'LS Trolley (castor bracket assy) {d}mm'
            ProjectCostLine.objects.create(cost=cost, line_type='ls_trolley', product_line='longspan',
                description=desc, size=f'lstrolley x {d}',
                quantity=tqty, unit_cost=unit_cost, sort_order=sort)

    elif ltype == 'template':
        tmpl_id = data.get('template_id')
        tmpl = get_object_or_404(PickingTemplate, pk=tmpl_id)
        ProjectCostLine.objects.create(cost=cost, line_type='template', product_line='trimline',
            description=f'📋 {tmpl.name}', picking_template=tmpl,
            quantity=qty, unit_cost=float(tmpl.price), sort_order=sort)

    return JsonResponse({'ok': True})




@login_required
@require_POST
def cost_line_delete(request, line_pk):
    line = get_object_or_404(ProjectCostLine, pk=line_pk)
    line.delete()
    return JsonResponse({'ok': True})




@login_required
@require_POST
def cost_line_update(request, line_pk):
    line = get_object_or_404(ProjectCostLine, pk=line_pk)
    data = json.loads(request.body)
    if 'quantity' in data:
        line.quantity = data['quantity']
    if 'unit_cost' in data:
        line.unit_cost = data['unit_cost']
    if 'melamine' in data:
        mat = {m.name: float(m.price_per_sqft) for m in MaterialPrice.objects.all()}
        chipboard = mat.get('chipboard', 0.50)
        melamine_p = mat.get('melamine', 0.75)
        chip18 = mat.get('chipboard_18mm', melamine_p)
        line.melamine = data['melamine']
        if line.melamine:
            line.no_deck = False  # mutually exclusive with no_deck
        parts = line.size.split(' x ')
        if len(parts) == 3:
            stype, w, d = parts
            line.unit_cost = calc_shelf_price(stype, w, d, line.melamine, chipboard, melamine_p, chip18, no_deck=line.no_deck)
            mat_label = 'No Deck' if line.no_deck else ('Melamine' if line.melamine else 'Chipboard')
            line.description = line.description.rsplit('(', 1)[0].strip() + f' ({mat_label})'
    if 'no_deck' in data:
        mat = {m.name: float(m.price_per_sqft) for m in MaterialPrice.objects.all()}
        chipboard = mat.get('chipboard', 0.50)
        melamine_p = mat.get('melamine', 0.75)
        chip18 = mat.get('chipboard_18mm', melamine_p)
        line.no_deck = data['no_deck']
        if line.no_deck:
            line.melamine = False  # mutually exclusive with melamine
        parts = line.size.split(' x ')
        if len(parts) == 3:
            stype, w, d = parts
            line.unit_cost = calc_shelf_price(stype, w, d, line.melamine, chipboard, melamine_p, chip18, no_deck=line.no_deck)
            mat_label = 'No Deck' if line.no_deck else ('Melamine' if line.melamine else 'Chipboard')
            line.description = line.description.rsplit('(', 1)[0].strip() + f' ({mat_label})'
    line.save()
    return JsonResponse({'ok': True, 'unit_cost': float(line.unit_cost), 'line_total': line.line_total, 'melamine': line.melamine, 'no_deck': line.no_deck})




@login_required
@require_POST
def material_price_update(request):
    data = json.loads(request.body)
    for name, price in data.items():
        MaterialPrice.objects.update_or_create(name=name,
            defaults={'price_per_sqft': price, 'updated_by': request.user})
    return JsonResponse({'ok': True})




@login_required
@require_POST
def cost_option_add(request, pk):
    """Add a new costing option tab for this project."""
    project = get_object_or_404(Project, pk=pk)
    existing = list(project.costs.order_by('order', 'id'))
    # Auto-name: Option A, B, C...
    labels = [c.label for c in existing]
    for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
        candidate = f'Option {letter}'
        if candidate not in labels:
            new_label = candidate
            break
    else:
        new_label = f'Option {len(existing)+1}'
    new_cost = ProjectCost.objects.create(
        project=project, label=new_label, order=len(existing),
        markup=existing[0].markup if existing else 50,
    )
    return redirect('project_cost_option', pk=pk, cost_pk=new_cost.pk)




@login_required
@require_POST
def cost_option_delete(request, pk, cost_pk):
    """Delete a costing option — not allowed if it's the only one."""
    project = get_object_or_404(Project, pk=pk)
    cost = get_object_or_404(ProjectCost, pk=cost_pk, project=project)
    if project.costs.count() <= 1:
        messages.error(request, 'Cannot delete the only costing option.')
        return redirect('project_cost_option', pk=pk, cost_pk=cost_pk)
    cost.delete()
    # Redirect to first remaining option
    first = project.costs.order_by('order', 'id').first()
    return redirect('project_cost_option', pk=pk, cost_pk=first.pk)




@login_required
@require_POST
def cost_option_accept(request, pk, cost_pk):
    """Toggle accepted status — only one option can be accepted at a time."""
    project = get_object_or_404(Project, pk=pk)
    cost = get_object_or_404(ProjectCost, pk=cost_pk, project=project)
    shortages = []
    if cost.is_accepted:
        # Un-accept (allowed — customer hasn't decided)
        cost.is_accepted = False
        cost.save(update_fields=['is_accepted'])
    else:
        # Accept this one, clear all others
        project.costs.exclude(pk=cost_pk).update(is_accepted=False)
        cost.is_accepted = True
        cost.save(update_fields=['is_accepted'])
        # Warn (don't block) if any component is already short on free stock —
        # catches a shortfall while it's still cheap to fix, rather than only
        # discovering it later when generating the picking list.
        try:
            lines = list(cost.lines.all())
            total_uprights = sum(int(float(l.quantity)) * 2 for l in lines if l.line_type == 'frame')
            overrides_map = {o.accessory_id: o.override_qty for o in AccessoryOverride.objects.filter(cost=cost)}
            acc_with_uprights = [
                {'code': acc.code, 'uprights': overrides_map.get(acc.id) if overrides_map.get(acc.id) is not None else total_uprights}
                for acc in cost.accessories.all()
            ] if hasattr(cost, 'accessories') else []
            ref = generate_picking_reference(lines, acc_with_uprights, cost.wall_fixings, cost.back_to_back_fixings, cost.mobile_base_sets)
            codes = [c for c in ref.keys() if not c.startswith('NS2::')]
            stock_by_code = {p.code.upper(): p for p in Product.objects.filter(code__in=codes)}
            for code, qty_needed in ref.items():
                if code.startswith('NS2::'):
                    continue
                prod = stock_by_code.get(code.upper())
                if prod and float(prod.free_stock) < qty_needed:
                    shortages.append({
                        'code': prod.code, 'description': prod.description,
                        'needed': qty_needed, 'free_stock': float(prod.free_stock),
                    })
        except Exception:
            pass  # never block acceptance over a check that itself failed
    from django.urls import reverse
    proforma_url = reverse('proforma_invoice_option', kwargs={'pk': project.pk, 'cost_pk': cost.pk}) if cost.is_accepted else None
    return JsonResponse({'ok': True, 'is_accepted': cost.is_accepted, 'proforma_url': proforma_url, 'shortages': shortages})




@login_required
@require_POST
def cost_option_rename(request, pk, cost_pk):
    """Rename a costing option label."""
    project = get_object_or_404(Project, pk=pk)
    cost = get_object_or_404(ProjectCost, pk=cost_pk, project=project)
    data = json.loads(request.body)
    label = data.get('label', '').strip()
    if label:
        cost.label = label[:100]
        cost.save(update_fields=['label'])
    return JsonResponse({'ok': True, 'label': cost.label})




@login_required
def project_cost_print(request, pk):
    project = get_object_or_404(Project, pk=pk)
    cost = project.costs.filter(is_accepted=True).first() or project.costs.first()
    if not cost:
        cost, _ = ProjectCost.objects.get_or_create(project=project)
    lines = cost.lines.select_related('product').all()
    buying_total = sum(l.line_total for l in lines)
    markup_amount = buying_total * float(cost.markup) / 100
    sell_price = buying_total + markup_amount + float(cost.labour) + float(cost.delivery)
    goods_sell = buying_total + markup_amount
    margin = (markup_amount / goods_sell * 100) if goods_sell else 0
    return render(request, 'projects/project_cost_print.html', {
        'project': project, 'cost': cost, 'lines': lines,
        'buying_total': round(buying_total, 2),
        'markup_amount': round(markup_amount, 2),
        'sell_price': round(sell_price, 2),
        'margin': round(margin, 1),
    })




@login_required
@require_POST
def product_toggle_active(request, pk):
    product = get_object_or_404(Product, pk=pk)
    product.is_active = not product.is_active
    product.save()
    return JsonResponse({'ok': True, 'is_active': product.is_active})




@login_required
@require_POST
def cost_accessory_toggle(request, pk):
    project = get_object_or_404(Project, pk=pk)
    # Support multiple cost options — read cost_pk from body or use active option
    try:
        _body = json.loads(request.body) if request.body else {}
        _cost_pk = _body.get('cost_pk')
    except Exception:
        _cost_pk = None
    if _cost_pk:
        cost = get_object_or_404(ProjectCost, pk=_cost_pk, project=project)
    else:
        cost = project.costs.filter(is_accepted=True).first() or project.costs.first()
        if not cost:
            return JsonResponse({'error': 'No costing found'}, status=400)
    data = json.loads(request.body)
    acc_id = data.get('accessory_id')
    checked = data.get('checked', False)
    override_qty = data.get('override_qty')  # None means use upright count
    acc = get_object_or_404(UprightAccessory, pk=acc_id)
    if checked:
        cost.accessories.add(acc)
    else:
        cost.accessories.remove(acc)
    # Save/update override
    if override_qty is not None:
        AccessoryOverride.objects.update_or_create(cost=cost, accessory=acc,
            defaults={'override_qty': int(override_qty) if override_qty != '' else None})
    lines = cost.lines.all()
    total_uprights = sum(int(float(l.quantity)) * 2 for l in lines if l.line_type == 'frame')
    override = AccessoryOverride.objects.filter(cost=cost, accessory=acc).first()
    eff_qty = override.override_qty if override and override.override_qty is not None else total_uprights
    total_cost = round(float(acc.unit_price) * eff_qty, 2) if checked else 0
    return JsonResponse({'ok': True, 'total_cost': total_cost, 'total_uprights': total_uprights})




@login_required
@require_POST
def cost_accessory_override(request, pk):
    project = get_object_or_404(Project, pk=pk)
    # Support multiple cost options — read cost_pk from body or use active option
    try:
        _body = json.loads(request.body) if request.body else {}
        _cost_pk = _body.get('cost_pk')
    except Exception:
        _cost_pk = None
    if _cost_pk:
        cost = get_object_or_404(ProjectCost, pk=_cost_pk, project=project)
    else:
        cost = project.costs.filter(is_accepted=True).first() or project.costs.first()
        if not cost:
            return JsonResponse({'error': 'No costing found'}, status=400)
    data = json.loads(request.body)
    acc_id = data.get('accessory_id')
    raw = data.get('override_qty', '')
    acc = get_object_or_404(UprightAccessory, pk=acc_id)
    if raw == '' or raw is None:
        AccessoryOverride.objects.filter(cost=cost, accessory=acc).delete()
        override_qty = None
    else:
        override_qty = max(0, int(raw))
        AccessoryOverride.objects.update_or_create(cost=cost, accessory=acc,
            defaults={'override_qty': override_qty})
    lines = cost.lines.all()
    total_uprights = sum(int(float(l.quantity)) * 2 for l in lines if l.line_type == 'frame')
    is_checked = acc in cost.accessories.all()
    if override_qty is not None:
        # Custom override qty drives the cost directly
        eff_qty = override_qty
        total_cost = round(float(acc.unit_price) * eff_qty, 2)
    elif is_checked:
        eff_qty = total_uprights
        total_cost = round(float(acc.unit_price) * eff_qty, 2)
    else:
        eff_qty = 0
        total_cost = 0
    return JsonResponse({'ok': True, 'total_cost': total_cost, 'eff_qty': eff_qty})


# ── Bulk actions ──────────────────────────────────────────────────────────────



@login_required
@require_POST
def cost_extras_save(request, pk):
    """Save back-to-back fixings and mobile base sets counts."""
    project = get_object_or_404(Project, pk=pk)
    # Support multiple cost options — read cost_pk from body or use active option
    try:
        _body = json.loads(request.body) if request.body else {}
        _cost_pk = _body.get('cost_pk')
    except Exception:
        _cost_pk = None
    if _cost_pk:
        cost = get_object_or_404(ProjectCost, pk=_cost_pk, project=project)
    else:
        cost = project.costs.filter(is_accepted=True).first() or project.costs.first()
        if not cost:
            return JsonResponse({'error': 'No costing found'}, status=400)
    data = json.loads(request.body)
    if 'back_to_back' in data:
        cost.back_to_back_fixings = max(0, int(data['back_to_back'] or 0))
    if 'mobile_bases' in data:
        cost.mobile_base_sets = max(0, int(data['mobile_bases'] or 0))
    cost.save()
    return JsonResponse({'ok': True})


@login_required
@require_POST
def cost_wall_fixings_save(request, pk):
    project = get_object_or_404(Project, pk=pk)
    # Support multiple cost options — read cost_pk from body or use active option
    try:
        _body = json.loads(request.body) if request.body else {}
        _cost_pk = _body.get('cost_pk')
    except Exception:
        _cost_pk = None
    if _cost_pk:
        cost = get_object_or_404(ProjectCost, pk=_cost_pk, project=project)
    else:
        cost = project.costs.filter(is_accepted=True).first() or project.costs.first()
        if not cost:
            return JsonResponse({'error': 'No costing found'}, status=400)
    data = json.loads(request.body)
    cost.wall_fixings = max(0, int(data.get('wall_fixings', 0)))
    cost.save()
    return JsonResponse({'ok': True, 'wall_fixings': cost.wall_fixings})


# ── Fitting Notes Library ─────────────────────────────────────────────────────



def generate_picking_reference(lines, selected_accessories=None, wall_fixings=0, back_to_back=0, mobile_bases=0):
    from collections import defaultdict
    # Local import avoids a circular import — picking.py imports from this
    # module at the top level, so this can't be a module-level import.
    from .picking import BACK_TO_BACK_KIT, MOBILE_BASE_KIT, SM_FOOT_FIXINGS, WALL_FIXING_KIT
    items = defaultdict(float)
    selected_accessories = selected_accessories or []
    for line in lines:
        qty = float(line.quantity)
        if line.line_type == 'frame':
            # size stored as: "frame x HEIGHT x DEPTH x PREFIX"
            size_parts = [p.strip() for p in line.size.split(' x ')] if line.size else []
            h = size_parts[1] if len(size_parts) >= 3 else None
            d = size_parts[2] if len(size_parts) >= 3 else None
            post_prefix = size_parts[3] if len(size_parts) == 4 else 'BTP'
            if h and d and h in HEIGHT_MAP and d in DEPTH_MAP:
                _, n = HEIGHT_MAP[h]
                conn_code = DEPTH_MAP[d]
                items[f'{post_prefix}{h}'] += qty * 2
                items[conn_code] += qty * n
                if ' x SM' in (line.size or ''):
                    items['SMFOOT'] += qty * 2
        elif line.line_type == 'shelf':
            # size stored as: "tfcv x WIDTH x DEPTH" or "twb x WIDTH x DEPTH"
            raw = (line.size or '').replace('"', '').replace('”', '').strip()
            size_parts = [p.strip() for p in raw.split(' x ')]
            if len(size_parts) == 3:
                stype, w, d = size_parts
                stype = stype.lower()
                melamine = line.melamine
                # Board: use the real Stock code if this size/material is
                # actually stocked; otherwise a synthetic NS2::WxD[MFC] key
                # that the enrichment step turns into a generic NS2
                # non-stock line with the size and material spelled out.
                # Skipped entirely when No Deck is set — that shelf level
                # gets mesh/FR MDF/steel sourced separately, not a board
                # from us, so it shouldn't appear on the picking list.
                if not line.no_deck:
                    real_code = board_stock_code(w, d, melamine)
                    if real_code:
                        items[real_code] += qty
                    else:
                        ns2_key = f'NS2::{w}X{d}{"MFC" if melamine else ""}'
                        items[ns2_key] += qty
                # Connector/beam — still needed structurally either way
                if stype == 'tfcv':
                    items[f'TFCV{w}'] += qty
                elif stype == 'twb':
                    items[f'TWB{w}'] += qty
        elif line.line_type == 'inhang':
            # Description: "Inboard Hanging 48" x 18""
            # 2 rails (HRS{depth}) + 1 foot (SMFOOT) per set
            # HRS21/HRS27 use different real Stock codes (HRS21/3N, HRS27/3N)
            # — matches the same override in price_list.py's _seed_price_list().
            hrs_code_override = {'21': 'HRS21/3N', '27': 'HRS27/3N'}
            parts = line.description.replace('"','').split('x')
            if len(parts) >= 2:
                depth_str = parts[-1].strip().split()[0]  # last number
                try:
                    depth = str(int(float(depth_str)))
                    hrs_code = hrs_code_override.get(depth, f'HRS{depth}')
                    items[hrs_code] += qty * 2
                    items['SMFOOT'] += qty
                    items['25MMFLOCOATTUBE1MM'] += qty
                except (ValueError, IndexError):
                    pass

        elif line.line_type == 'outhang':
            # Description: "Outboard Hanging Rail 915mm"
            # Codes: JWGR915, JWGR1000, JWGR1220
            for length in ['915', '1000', '1220', '1525']:
                if length in line.description:
                    items[f'JWGR{length}'] += qty
                    break

        elif line.line_type == 'beams':
            # size stored as: "tfcv x WIDTH" or "twb x WIDTH"
            raw = (line.size or '').replace('"', '').replace('”', '').strip()
            size_parts = [p.strip() for p in raw.split(' x ')]
            if len(size_parts) == 2:
                btype, w = size_parts
                btype = btype.lower()
                if btype == 'tfcv':
                    items[f'TFCV{w}'] += qty
                elif btype == 'twb':
                    items[f'TWB{w}'] += qty

        elif line.line_type == 'stock' and line.product:
            items[line.product.code] += qty

        elif line.line_type == 'mesh':
            # Use the size field as stock code (MP120X48 etc)
            code = line.size if line.size else 'MP120X48'
            items[code] += qty

        elif line.line_type == 'toptie':
            # Fixings: double the top tie quantity for each fixing
            for fix_code, qty_per in TOP_TIE_FIXINGS.items():
                items[fix_code] += qty * qty_per
            # 19MMRODS: each top tie needs a rod length equal to its size (mm).
            # Parse the length from the description (e.g. "Top Tie 1500"); default 1100.
            import re as _re_tt
            m = _re_tt.search(r'(\d{3,5})', line.description or '')
            tie_len = int(m.group(1)) if m else TOP_TIE_ROD_LENGTH
            total_mm = qty * tie_len
            rods_needed = math.ceil(total_mm / ROD_FULL_LENGTH)
            if rods_needed > 0:
                items['19MMRODS'] += rods_needed
    # Back-to-back fixings
    if back_to_back:
        for code, qty_per in BACK_TO_BACK_KIT.items():
            items[code] += back_to_back * qty_per

    # Mobile base sets — add kit parts
    if mobile_bases:
        for code, qty_per in MOBILE_BASE_KIT.items():
            items[code] += mobile_bases * qty_per

    # Accessories
    for acc in selected_accessories:
        code = acc.code if hasattr(acc, 'code') else acc.get('code','')
        n_uprights = acc.get('uprights', 0) if isinstance(acc, dict) else 0
        if code == 'TTC':
            items['TTC'] += n_uprights
        elif code == 'SMFOOT':
            for fix_code, qty_per in SM_FOOT_FIXINGS.items():
                items[fix_code] += n_uprights * qty_per

    # Subtract SM foot fixings replaced by mobile base sets (must run after accessories)
    if mobile_bases:
        for code in SM_FOOT_FIXINGS:
            if items[code] > 0:
                items[code] = max(0, items[code] - mobile_bases * SM_FOOT_FIXINGS[code])

    # Wall fixings
    if wall_fixings:
        for fix_code, qty_per in WALL_FIXING_KIT.items():
            items[fix_code] += wall_fixings * qty_per

    return {k: round(v) for k, v in items.items() if v > 0}

# ── Pricing data ──────────────────────────────────────────────────────────────

# Post prices (code -> price) — TP144 removed, no Stock code exists (Jul 2026)
POSTS = {'TP48':5.55,'TP60':6.81,'TP72':8.18,'TP84':9.45,'TP96':10.91,
         'TP108':12.25,'TP120':13.61}
# Connector prices (code -> price)
TPCS  = {'TPC12':1.44,'TPC15':1.61,'TPC18':1.90,'TPC21':2.28,'TPC24':2.28,
          'TPC27':2.81,'TPC30':2.81,'TPC36':3.25}
# Height -> (post_code, n_connectors)
HEIGHT_MAP = {'48':('TP48',2),'60':('TP60',2),'72':('TP72',3),'84':('TP84',3),
              '96':('TP96',4),'108':('TP108',4),'120':('TP120',5)}
# Depth -> connector_code
DEPTH_MAP  = {'12':'TPC12','15':'TPC15','18':'TPC18','21':'TPC21',
              '24':'TPC24','27':'TPC27','30':'TPC30','36':'TPC36'}
# TFCV connector prices (depth -> price)
TFCV_CONN  = {'24':3.50,'30':3.50,'36':4.25,'39.5':4.99,'43.5':5.58,'48':5.58}
# TWB prices (width"xdepth" -> price) - beams only, no deck included
TWB_PRICES = {'36':5.74,'48':7.57,'60':10.38,'66':11.29,'72':12.20}
TFCL_PRICES= {'36':4.58,'39.5':5.39,'48':5.90}

# Inboard hanging: (width, depth) -> price
IN_HANG_WIDTHS = ['24','30','36','39.5','43.5','48']
IN_HANG_DEPTHS = ['12','15','18','21','24','27','30','36']
IN_HANG_RAILS  = {'12':4.0,'15':4.2,'18':4.35,'21':4.65,'24':4.85,'27':5.0,'30':5.3,'36':6.5}
IN_HANG_FOOT   = 1.5



def calc_in_hang_price(width, depth):
    rail = IN_HANG_RAILS.get(depth, 0)
    feet = 2 if depth == '36' else 1
    return round(2*rail + feet*IN_HANG_FOOT, 2)

# Outboard hanging
OUT_HANG = {'915mm': 2.93, '1000mm': 2.93, '1220mm': 2.93}
OUT_HANG_CODES = {'915mm': 'JWGR915', '1000mm': 'JWGR1000', '1220mm': 'JWGR1220'}

# Wipe boards (bay depth -> price)
WIPE_BOARDS = {'12':3.2,'15':3.9,'18':4.6,'24':6.1,'27':6.8,'30':7.5,'36':8.8}

# Top tie fixings — per top tie qty (doubled)
TOP_TIE_FIXINGS = {
    'M8X35HEXHD':         2,
    'FIXCCL08':           2,
    'FIX466285T/INSERT':  2,
    'FIX111056/INSERT':   2,
}
TOP_TIE_ROD_LENGTH   = 1100   # mm per top tie
ROD_FULL_LENGTH      = 6450   # mm per 19MMROD length


LOAD_SIGNS = {'Laminated': 3.0, 'Foam A3': 6.0}
MESH_PANEL_PRICES = {
    'MP120X48': 25.0,
    'MP108X48': 23.0,
    'MP96X48':  21.0,
    'MP84X48':  19.0,
    'MP72X48':  17.0,
}
TOP_TIE_PRICE = 3.0

FRAME_HEIGHTS = ['48','60','72','84','96','108','120']
FRAME_DEPTHS  = ['12','15','18','21','24','27','30','36']
TFCV_WIDTHS   = ['24','30','36','39.5','43.5','48']
TWB_WIDTHS    = ['36','48','60','66','72']
SHELF_DEPTHS  = ['12','15','18','21','24','27','30','36']

# Board (chipboard/melamine) Stock codes by (width, depth), from Tasos's
# mapping (Jul 2026). None = not a real Stock item at that size — the
# picking list falls back to a generic NS2 "non-stock, order this" line
# with the size and material spelled out, instead of a code that won't
# exist. Costing/pricing is unaffected either way (still £/sqft x board rate).
BOARD_STOCK_CODES = {
    ('24','12'):   {'chipboard': '24X12',        'melamine': None},
    ('24','15'):   {'chipboard': None,           'melamine': '24X15MFC2SE'},
    ('24','18'):   {'chipboard': None,           'melamine': None},
    ('24','21'):   {'chipboard': None,           'melamine': None},
    ('24','24'):   {'chipboard': '24X24',        'melamine': None},
    ('24','27'):   {'chipboard': None,           'melamine': None},
    ('24','30'):   {'chipboard': None,           'melamine': None},
    ('24','36'):   {'chipboard': None,           'melamine': None},
    ('30','12'):   {'chipboard': '30X12',        'melamine': None},
    ('30','15'):   {'chipboard': None,           'melamine': '30X15MFC2SE'},
    ('30','18'):   {'chipboard': None,           'melamine': None},
    ('30','21'):   {'chipboard': None,           'melamine': None},
    ('30','24'):   {'chipboard': '30X24',        'melamine': None},
    ('30','27'):   {'chipboard': None,           'melamine': None},
    ('30','30'):   {'chipboard': None,           'melamine': None},
    ('30','36'):   {'chipboard': None,           'melamine': None},
    ('36','12'):   {'chipboard': '36X12',        'melamine': '36X12MFC'},
    ('36','15'):   {'chipboard': '36X15',        'melamine': '36X15MFC'},
    ('36','18'):   {'chipboard': '36X18',        'melamine': '36X18MFC'},
    ('36','21'):   {'chipboard': '36X21',        'melamine': None},
    ('36','24'):   {'chipboard': '36X24',        'melamine': '36X24MFC'},
    ('36','27'):   {'chipboard': '36X27',        'melamine': None},
    ('36','30'):   {'chipboard': '36X30',        'melamine': '36X30MFC'},
    ('36','36'):   {'chipboard': '36X36',        'melamine': '36X36MFC2LE'},
    ('39.5','12'): {'chipboard': '39.5X12',      'melamine': '39.5X12MFC/L2SE'},
    ('39.5','15'): {'chipboard': '39.5X15',      'melamine': '39.5X15MFC/L2SE'},
    ('39.5','18'): {'chipboard': '39.5X18',      'melamine': '39.5X18MFC/L2SE'},
    ('39.5','21'): {'chipboard': '39.5X21',      'melamine': None},
    ('39.5','24'): {'chipboard': '39.5X24',      'melamine': '39.5X24MFCL2SEWHITE'},
    ('39.5','27'): {'chipboard': '39.5X27',      'melamine': '39.5X27MFC1SE'},
    ('39.5','30'): {'chipboard': '39.5X30',      'melamine': None},
    ('39.5','36'): {'chipboard': '39.5X36',      'melamine': '39.5X36MFC/2SE'},
    ('43.5','12'): {'chipboard': None,           'melamine': None},
    ('43.5','15'): {'chipboard': None,           'melamine': '43.5X15MFC2SE'},
    ('43.5','18'): {'chipboard': None,           'melamine': None},
    ('43.5','21'): {'chipboard': None,           'melamine': None},
    ('43.5','24'): {'chipboard': None,           'melamine': None},
    ('43.5','27'): {'chipboard': None,           'melamine': None},
    ('43.5','30'): {'chipboard': None,           'melamine': '43.5X30MFC2SE'},
    ('43.5','36'): {'chipboard': None,           'melamine': None},
    ('48','12'):   {'chipboard': '48X12',        'melamine': '48X12MFC/L2SE'},
    ('48','15'):   {'chipboard': '48X15',        'melamine': '48X15MFC/L2SE'},
    ('48','18'):   {'chipboard': '48X18',        'melamine': '48X18MFC2SE'},
    ('48','21'):   {'chipboard': '48X21C',       'melamine': '48X21'},
    ('48','24'):   {'chipboard': '48X24',        'melamine': '48X24MFC2SE'},
    ('48','27'):   {'chipboard': '48X27',        'melamine': '48X27MFC2SE'},
    ('48','30'):   {'chipboard': '48X30',        'melamine': '48X30MFC2SE'},
    ('48','36'):   {'chipboard': '48X36',        'melamine': '48X36MFC'},
    ('60','12'):   {'chipboard': '60X12',        'melamine': None},
    ('60','15'):   {'chipboard': '60X15',        'melamine': '60X15MFCEAR'},
    ('60','18'):   {'chipboard': '60X18',        'melamine': None},
    ('60','21'):   {'chipboard': None,           'melamine': None},
    ('60','24'):   {'chipboard': '60X24',        'melamine': None},
    ('60','27'):   {'chipboard': None,           'melamine': None},
    ('60','30'):   {'chipboard': '60X30',        'melamine': None},
    ('60','36'):   {'chipboard': '60X36',        'melamine': None},
    ('72','12'):   {'chipboard': '72X12',        'melamine': None},
    ('72','15'):   {'chipboard': '72X15',        'melamine': None},
    ('72','18'):   {'chipboard': '72X18',        'melamine': None},
    ('72','21'):   {'chipboard': None,           'melamine': None},
    ('72','24'):   {'chipboard': '72X24',        'melamine': None},
    ('72','27'):   {'chipboard': None,           'melamine': None},
    ('72','30'):   {'chipboard': '72X30',        'melamine': '72X30MFC'},
    ('72','36'):   {'chipboard': '72X36',        'melamine': None},
    # 66" width: TWB beam is stocked (TWB66) but no board size is — every
    # depth falls through to the NS2 non-stock line automatically.
}


def board_stock_code(width, depth, melamine):
    """Real Stock code for a board size/material, or None if not stocked."""
    entry = BOARD_STOCK_CODES.get((width, depth))
    if not entry:
        return None
    return entry['melamine' if melamine else 'chipboard']


def board_material_label(width, depth, melamine, chipboard_18mm_threshold=27):
    """Human label for the material actually used, matching calc_shelf_price's
    own board-rate logic, for the NS2 non-stock description."""
    if melamine:
        return 'Melamine'
    return '18mm Chipboard' if float(depth) >= chipboard_18mm_threshold else 'Chipboard'


import re as _re_ns2
_NS2_KEY_RE = _re_ns2.compile(r'^NS2::([\d.]+)X([\d.]+)(MFC)?$')


def parse_ns2_key(code):
    """If `code` is a synthetic 'NS2::WxD[MFC]' key from generate_picking_reference,
    return (width, depth, description) for the non-stock picking line. Otherwise None."""
    m = _NS2_KEY_RE.match(code or '')
    if not m:
        return None
    width, depth, mfc = m.group(1), m.group(2), bool(m.group(3))
    material = board_material_label(width, depth, mfc)
    description = f'{width}" x {depth}" {material} — board not stocked, order to size'
    return width, depth, description




def trimline_component_prices():
    """Return {code: price} for trimline components. Stock's buying price
    (matched by code, with colour-variant aliases) takes priority; falls back
    to the Price List's own stored value for any code with no Stock match yet."""
    try:
        from .utils import stock_lookup_by_code, resolve_stock_item
        stock_by_code = stock_lookup_by_code()
        result = {}
        for item in PriceListItem.objects.filter(product_line='trimline'):
            stock_item = resolve_stock_item(item.code, stock_by_code)
            result[item.code] = float(stock_item.cost_price) if stock_item else float(item.price)
        return result
    except Exception:
        return {}




def calc_frame_price(height_in, depth_in):
    """Return frame unit cost given height and depth in inches (strings).
    Sources post + connector prices from the editable price list, falling back to defaults."""
    hm = HEIGHT_MAP.get(height_in)
    dc = DEPTH_MAP.get(depth_in)
    if not hm or not dc:
        return 0
    post_code, n = hm
    comp = trimline_component_prices()
    post_price = comp.get(post_code, POSTS.get(post_code, 0))
    conn_price = comp.get(dc, TPCS.get(dc, 0))
    return round(2 * post_price + n * conn_price, 4)




def calc_shelf_price(shelf_type, width_in, depth_in, melamine, chipboard_price, melamine_price, chipboard_18mm_price=None, no_deck=False):
    """Return shelf unit cost.
    Board material rate:
      - no_deck   -> no board cost at all (mesh/FR MDF/steel deck sourced separately)
      - melamine  -> melamine rate
      - chipboard, depth >= 27" -> 18mm chipboard rate
      - chipboard, depth < 27"  -> standard (15mm) chipboard rate
    The beam/connector price always applies — the shelf level itself still
    needs supporting either way, only the board is what's being skipped.
    """
    w, d = float(width_in), float(depth_in)
    sqft = (w * d) / 144
    if no_deck:
        mat_price = 0
    elif melamine:
        mat_price = melamine_price
    elif d >= 27 and chipboard_18mm_price is not None:
        mat_price = chipboard_18mm_price
    else:
        mat_price = chipboard_price
    comp = trimline_component_prices()

    if shelf_type == 'tfcv':
        conn_price = comp.get(f'TFCV{width_in}', TFCV_CONN.get(width_in, 0))
        return round(sqft * mat_price + conn_price, 4)
    elif shelf_type == 'twb':
        beam_price = comp.get(f'TWB{width_in}', TWB_PRICES.get(width_in, 0))
        return round(sqft * mat_price + beam_price, 4)
    return 0


# ── Costing views ─────────────────────────────────────────────────────────────



@login_required
@require_POST
def fitting_crew_api(request):
    if request.method == 'GET':
        crews = list(FittingCrew.objects.values('id', 'name', 'phone'))
        return JsonResponse(crews, safe=False)
    if request.method == 'POST':
        data = json.loads(request.body)
        name  = data.get('name', '').strip()
        phone = data.get('phone', '').strip()
        if not name:
            return JsonResponse({'error': 'Name required'}, status=400)
        crew, created = FittingCrew.objects.get_or_create(name=name, defaults={'phone': phone})
        if not created and phone:
            crew.phone = phone
            crew.save()
        return JsonResponse({'id': crew.pk, 'name': crew.name, 'phone': crew.phone})
    if request.method == 'DELETE':
        data = json.loads(request.body)
        FittingCrew.objects.filter(pk=data.get('id')).delete()
        return JsonResponse({'ok': True})
    return JsonResponse({'error': 'Method not allowed'}, status=405)




@login_required
def satisfaction_note(request, pk):
    """Generate a client satisfaction note as a printable HTML page."""
    project = get_object_or_404(Project, pk=pk)
    report = getattr(project, 'install_report', None)
    customer_profile = project.customer_profile or CustomerProfile.objects.filter(name__iexact=project.customer).first()
    # Pre-resolve all fields so template is simple
    site_contact = project.addr_fao or (report.site_contact if report else '') or (customer_profile.contact_name if customer_profile else '')
    contact_number = project.addr_phone or (report.contact_number if report else '') or (customer_profile.phone if customer_profile else '')
    fitting_crew = report.fitting_crew if report else ''
    fitting_crew_phone = report.fitting_crew_phone if report else ''
    job_tasks = report.job_tasks if report else ''
    import datetime
    inst_date = ''
    eff = project.get_effective_installation_date()
    if eff:
        inst_date = eff.strftime('%d.%m.%y')
    elif project.installation_month:
        # Format month string e.g. "2025-09" -> "Sep 2025"
        try:
            y, m = project.installation_month.split('-')
            import calendar
            inst_date = f"{calendar.month_abbr[int(m)]} {y}"
        except Exception:
            inst_date = project.installation_month
    address_parts = [project.location]
    if project.addr_line1: address_parts.append(project.addr_line1)
    if project.addr_line2: address_parts.append(project.addr_line2)
    if project.addr_city:
        city = project.addr_city
        if project.addr_county: city += ', ' + project.addr_county
        if project.addr_postcode: city += ' ' + project.addr_postcode
        address_parts.append(city)
    return render(request, 'projects/satisfaction_note.html', {
        'project': project,
        'report': report,
        'customer_profile': customer_profile,
        'site_contact': site_contact,
        'contact_number': contact_number,
        'fitting_crew': fitting_crew,
        'fitting_crew_phone': fitting_crew_phone,
        'job_tasks': job_tasks,
        'inst_date': inst_date,
        'site_address': '\n'.join(filter(None, address_parts)),
    })


