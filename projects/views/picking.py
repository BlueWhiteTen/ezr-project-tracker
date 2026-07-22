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
from .utils import (_picking_locked_response)
from .costing import generate_picking_reference, parse_ns2_key


# ── Auth ──────────────────────────────────────────────────────────────────────

from django.contrib.auth.views import (
    PasswordResetView, PasswordResetDoneView,
    PasswordResetConfirmView, PasswordResetCompleteView,
)

@login_required
def picking_list_view(request, project_pk):
    project = get_object_or_404(Project, pk=project_pk)
    # Auto-create single picking list per project
    pl, created = PickingList.objects.get_or_create(
        project=project,
        defaults={'created_by': request.user, 'status': 'draft'}
    )
    items = pl.items.select_related('product').all()
    templates = PickingTemplate.objects.all()
    # Compute stock shortage per item.
    # Free stock excluding this list's own allocation (so an allocated list still shows true picture)
    any_shortage = False
    for item in items:
        item.is_short = False
        item.short_by = 0
        if item.item_type == 'stock' and item.product:
            prod = item.product
            free = float(prod.quantity) - float(prod.qty_allocated)
            if pl.allocated:
                # This list already allocated its qty, so add it back to see real availability
                free += float(item.quantity)
            if float(item.quantity) > free:
                item.is_short = True
                item.short_by = round(float(item.quantity) - free, 2)
                any_shortage = True
    # Get customer important notes
    customer_notes = None
    if project.customer:
        cp = CustomerProfile.objects.filter(name__iexact=project.customer).first()
        if cp and cp.important_notes:
            customer_notes = cp.important_notes
    # Weight lookup: Stock's weight field (by code) is the source of truth.
    # Anything not yet weighed in Stock falls back to the built-in default
    # table in the template's JS.
    stock_weights = {
        p.code.strip().upper(): float(p.weight)
        for p in Product.objects.exclude(weight__isnull=True).exclude(code='')
    }
    return render(request, 'projects/picking_list.html', {
        'project': project, 'pl': pl, 'items': items,
        'templates': templates, 'customer_notes': customer_notes,
        'any_shortage': any_shortage, 'project_pk': project.pk,
        'stock_weights': stock_weights,
    })




@login_required
@require_POST
def picking_list_create(request, project_pk):
    project = get_object_or_404(Project, pk=project_pk)
    # Must have an approved costing option before creating a picking list
    if not project.costs.filter(is_accepted=True).exists():
        return JsonResponse({'error': 'No costing option has been approved yet. Please approve an option on the Costing page before generating a picking list.'}, status=400)
    pl, _ = PickingList.objects.get_or_create(project=project, defaults={'created_by': request.user})
    return JsonResponse({'id': pl.pk})




@login_required
@require_POST
def picking_list_save(request, pk):
    pl = get_object_or_404(PickingList, pk=pk)
    data = json.loads(request.body)

    new_status = data.get('status', pl.status)
    was_dispatched = pl.status == 'dispatched'
    pl.status = new_status
    pl.notes = data.get('notes', pl.notes)

    # Save items — but if stock is allocated, the list's composition is locked.
    # We still allow this call through for status/notes changes (e.g. dispatching),
    # we just skip rewriting the items in that case.
    items_data = data.get('items', [])
    if items_data and pl.allocated:
        return JsonResponse({'error': 'This picking list is locked because stock has been allocated. Release the allocation first to make changes.'}, status=400)
    if not pl.allocated:
        pl.items.all().delete()
        for item in items_data:
            product = get_object_or_404(Product, pk=item['product_id'])
            PickingListItem.objects.create(
                picking_list=pl,
                product=product,
                quantity=item['quantity'],
                picked=item.get('picked', False),
            )

    # Deduct stock when dispatched
    if new_status == 'dispatched' and not was_dispatched:
        pl.dispatched_at = timezone.now()
        for item in pl.items.filter(item_type='stock', product__isnull=False):
            prod = item.product
            prod.quantity = float(prod.quantity) - float(item.quantity)
            # If this list had allocated the stock, release that allocation as it's now physically gone
            if pl.allocated:
                prod.qty_allocated = max(0, float(prod.qty_allocated) - float(item.quantity))
            prod.save()
            StockMovement.objects.create(
                product=prod, project=pl.project, movement_type='dispatched',
                qty_change=-float(item.quantity),
                reason=f'Dispatched for {pl.project.project_name}',
                user=request.user,
            )

    pl.save()
    return JsonResponse({'ok': True, 'status': pl.get_status_display()})




@login_required
@require_POST
def picking_item_add(request, pk):
    pl = get_object_or_404(PickingList, pk=pk)
    locked = _picking_locked_response(pl)
    if locked:
        return locked
    data = json.loads(request.body)
    product = get_object_or_404(Product, pk=data['product_id'])
    qty = float(data.get('quantity', 1))
    count = pl.items.count()
    item = PickingListItem.objects.create(
        picking_list=pl, product=product,
        quantity=qty, sort_order=count
    )
    return JsonResponse({'ok': True, 'item_pk': item.pk, 'quantity': float(item.quantity)})




@login_required
@require_POST
def picking_item_delete(request, pk):
    item = get_object_or_404(PickingListItem, pk=pk)
    locked = _picking_locked_response(item.picking_list)
    if locked:
        return locked
    item.delete()
    return JsonResponse({'ok': True})




@login_required
@require_POST
def picking_item_toggle(request, pk):
    item = get_object_or_404(PickingListItem, pk=pk)
    data = json.loads(request.body)
    item.picked = data.get('picked', False)
    item.save()
    return JsonResponse({'ok': True})




@login_required
def picking_status(request, pk):
    pl = get_object_or_404(PickingList, pk=pk)
    data = json.loads(request.body)
    new_status = data.get('status', pl.status)
    was_dispatched = pl.status == 'dispatched'
    pl.status = new_status
    if new_status == 'dispatched' and not was_dispatched:
        from django.utils import timezone
        pl.dispatched_at = timezone.now()
        for item in pl.items.select_related('product').all():
            item.product.quantity -= item.quantity
            item.product.save()
    pl.save()
    return JsonResponse({'ok': True})




@login_required
@require_POST
def picking_item_add_ns(request, pk):
    pl = get_object_or_404(PickingList, pk=pk)
    locked = _picking_locked_response(pl)
    if locked:
        return locked
    data = json.loads(request.body)
    desc = data.get('description', '').strip()
    if not desc:
        return JsonResponse({'error': 'Description required'}, status=400)
    qty = float(data.get('quantity', 1))
    count = pl.items.count()
    item = PickingListItem.objects.create(
        picking_list=pl, item_type='ns',
        ns_description=desc, quantity=qty, sort_order=count
    )
    return JsonResponse({'ok': True, 'item_pk': item.pk, 'quantity': float(qty)})




@login_required
@require_POST
def picking_allocate(request, pk):
    pl = get_object_or_404(PickingList, pk=pk)
    if pl.allocated:
        return JsonResponse({'error': 'Stock already allocated for this list.'}, status=400)
    # Aggregate required qty per product across stock items
    needed = {}
    for item in pl.items.filter(item_type='stock', product__isnull=False):
        needed[item.product_id] = needed.get(item.product_id, 0) + float(item.quantity)
    # Allocate everything; record any shortages (free stock = quantity - qty_allocated)
    shortages = []
    for pid, qty_needed in needed.items():
        prod = Product.objects.get(pk=pid)
        free = float(prod.quantity) - float(prod.qty_allocated)
        if qty_needed > free:
            shortages.append({
                'code': prod.code,
                'description': prod.description,
                'needed': qty_needed,
                'free': free,
                'short_by': round(qty_needed - free, 2),
            })
        # Allocate anyway (can push free stock negative)
        prod.qty_allocated = float(prod.qty_allocated) + qty_needed
        prod.save(update_fields=['qty_allocated'])
        StockMovement.objects.create(
            product=prod, project=pl.project, movement_type='allocated',
            qty_change=-qty_needed, reason=f'Allocated to {pl.project.project_name}',
            user=request.user,
        )
    pl.allocated = True
    pl.allocated_at = timezone.now()
    pl.allocated_by = request.user
    pl.save(update_fields=['allocated', 'allocated_at', 'allocated_by'])
    return JsonResponse({'ok': True, 'shortages': shortages})




@login_required
@require_POST
def picking_deallocate(request, pk):
    pl = get_object_or_404(PickingList, pk=pk)
    if not pl.allocated:
        return JsonResponse({'error': 'Stock is not allocated.'}, status=400)
    needed = {}
    for item in pl.items.filter(item_type='stock', product__isnull=False):
        needed[item.product_id] = needed.get(item.product_id, 0) + float(item.quantity)
    for pid, qty_needed in needed.items():
        prod = Product.objects.get(pk=pid)
        prod.qty_allocated = max(0, float(prod.qty_allocated) - qty_needed)
        prod.save(update_fields=['qty_allocated'])
        StockMovement.objects.create(
            product=prod, project=pl.project, movement_type='deallocated',
            qty_change=qty_needed, reason=f'Allocation released from {pl.project.project_name}',
            user=request.user,
        )
    pl.allocated = False
    pl.allocated_at = None
    pl.allocated_by = None
    pl.save(update_fields=['allocated', 'allocated_at', 'allocated_by'])
    return JsonResponse({'ok': True})




@login_required
@require_POST
def picking_item_add_msg(request, pk):
    pl = get_object_or_404(PickingList, pk=pk)
    locked = _picking_locked_response(pl)
    if locked:
        return locked
    data = json.loads(request.body)
    msg = data.get('message', '').strip()
    if not msg:
        return JsonResponse({'error': 'Message required'}, status=400)
    after_pk = data.get('after_pk')
    if after_pk:
        # Insert right after the given item
        try:
            after_item = pl.items.get(pk=after_pk)
            base_sort = after_item.sort_order
            # Bump everything after it
            pl.items.filter(sort_order__gt=base_sort).update(sort_order=models_F('sort_order') + 1)
            new_sort = base_sort + 1
        except PickingListItem.DoesNotExist:
            new_sort = pl.items.count()
    else:
        new_sort = pl.items.count()
    item = PickingListItem.objects.create(
        picking_list=pl, item_type='message',
        message=msg, ns_description=msg, quantity=0, sort_order=new_sort
    )
    return JsonResponse({'ok': True, 'item_pk': item.pk})


# ── Picking Templates ─────────────────────────────────────────────────────────



@login_required
def template_list(request):
    templates = PickingTemplate.objects.prefetch_related('items__product').all()
    return render(request, 'projects/template_list.html', {'templates': templates})




@login_required
def template_detail(request, pk):
    tmpl = get_object_or_404(PickingTemplate, pk=pk)
    if request.method == 'POST':
        data = json.loads(request.body)
        action = data.get('action')
        if action == 'update_name':
            tmpl.name = data.get('name', tmpl.name).strip()
            tmpl.customer = data.get('customer', tmpl.customer).strip()
            tmpl.save()
            return JsonResponse({'ok': True})
        if action == 'delete':
            tmpl.delete()
            return JsonResponse({'ok': True, 'deleted': True})
        if action == 'add_stock':
            product = get_object_or_404(Product, pk=data['product_id'])
            count = tmpl.items.count()
            item = PickingTemplateItem.objects.create(
                template=tmpl, item_type='stock', product=product,
                quantity=data.get('quantity', 1), sort_order=count
            )
            return JsonResponse({'ok': True, 'item_pk': item.pk})
        if action == 'add_ns':
            count = tmpl.items.count()
            item = PickingTemplateItem.objects.create(
                template=tmpl, item_type='ns',
                ns_description=data.get('description', ''),
                quantity=data.get('quantity', 1), sort_order=count
            )
            return JsonResponse({'ok': True, 'item_pk': item.pk})
        if action == 'add_msg':
            count = tmpl.items.count()
            item = PickingTemplateItem.objects.create(
                template=tmpl, item_type='message',
                message=data.get('message', ''), quantity=0, sort_order=count
            )
            return JsonResponse({'ok': True, 'item_pk': item.pk})
        if action == 'delete_item':
            PickingTemplateItem.objects.filter(pk=data['item_pk'], template=tmpl).delete()
            return JsonResponse({'ok': True})
        if action == 'update_qty':
            item = PickingTemplateItem.objects.filter(pk=data['item_pk'], template=tmpl).first()
            if item:
                try:
                    item.quantity = float(data.get('quantity', 0) or 0)
                    item.save(update_fields=['quantity'])
                except (ValueError, TypeError):
                    return JsonResponse({'ok': False, 'error': 'Invalid quantity'}, status=400)
            return JsonResponse({'ok': True})
    items = tmpl.items.select_related('product').all()
    return render(request, 'projects/template_detail.html', {'tmpl': tmpl, 'items': items})




@login_required
def template_create(request):
    data = json.loads(request.body)
    name = data.get('name', '').strip()
    if not name:
        return JsonResponse({'error': 'Name required'}, status=400)
    tmpl = PickingTemplate.objects.create(
        name=name,
        customer=data.get('customer', '').strip(),
        created_by=request.user
    )
    return JsonResponse({'id': tmpl.pk})




@login_required
def template_duplicate(request, pk):
    """Save an existing template as a new one with a different name."""
    src = get_object_or_404(PickingTemplate, pk=pk)
    data = json.loads(request.body)
    name = data.get('name', '').strip()
    if not name:
        return JsonResponse({'error': 'Name required'}, status=400)
    new_tmpl = PickingTemplate.objects.create(
        name=name,
        customer=data.get('customer', src.customer).strip(),
        created_by=request.user,
    )
    for item in src.items.all():
        PickingTemplateItem.objects.create(
            template=new_tmpl,
            item_type=item.item_type,
            product=item.product,
            ns_description=item.ns_description,
            message=item.message,
            quantity=item.quantity,
            sort_order=item.sort_order,
        )
    return JsonResponse({'ok': True, 'id': new_tmpl.pk})




@login_required
def template_import_excel(request):
    """Import a picking list Excel file as a new template."""
    import openpyxl
    f = request.FILES.get('file')
    name = request.POST.get('name', '').strip() or 'Imported Template'
    customer = request.POST.get('customer', '').strip()
    if not f:
        return JsonResponse({'error': 'No file'}, status=400)
    try:
        wb = openpyxl.load_workbook(f)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))[1:]
        tmpl = PickingTemplate.objects.create(name=name, customer=customer, created_by=request.user)
        sort = 0
        for row in rows:
            code = str(row[0]).strip() if row[0] else ''
            desc = str(row[1]).strip() if row[1] else ''
            qty  = float(row[2]) if row[2] and str(row[2]).replace('.','').isdigit() else 1
            if not code or code in ('None', 'Deduction', 'Net Value Discount'):
                continue
            if code == 'M':  # message/separator row
                if desc:
                    PickingTemplateItem.objects.create(template=tmpl, item_type='message', message=desc, sort_order=sort)
                    sort += 1
                continue
            # Try to match stock product
            product = Product.objects.filter(code__iexact=code, is_active=True).first()
            if product:
                PickingTemplateItem.objects.create(template=tmpl, item_type='stock', product=product, quantity=qty, sort_order=sort)
            else:
                # NS item — use description from Excel, code as prefix
                ns_desc = desc if desc else code
                # Check if it's actually an NS code
                PickingTemplateItem.objects.create(template=tmpl, item_type='ns', ns_description=f"{code}: {ns_desc}" if desc else code, quantity=qty if qty > 0 else 1, sort_order=sort)
            sort += 1
        return JsonResponse({'ok': True, 'id': tmpl.pk, 'count': sort})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)




@login_required
@require_POST
def picking_apply_template(request, pk):
    """Apply a template to a picking list."""
    pl = get_object_or_404(PickingList, pk=pk)
    locked = _picking_locked_response(pl)
    if locked:
        return locked
    data = json.loads(request.body)
    tmpl = get_object_or_404(PickingTemplate, pk=data['template_id'])
    current_count = pl.items.count()
    # Add template name as a message line header
    PickingListItem.objects.create(
        picking_list=pl, item_type='message',
        message=tmpl.name, quantity=0, sort_order=current_count
    )
    current_count += 1
    for i, titem in enumerate(tmpl.items.select_related('product').all()):
        PickingListItem.objects.create(
            picking_list=pl,
            item_type=titem.item_type,
            product=titem.product,
            ns_description=titem.ns_description,
            message=titem.message,
            quantity=titem.quantity,
            sort_order=current_count + i,
        )
    pl.templates_used.add(tmpl)
    return JsonResponse({'ok': True, 'added': tmpl.items.count()})




@login_required
def picking_list_print(request, project_pk):
    project = get_object_or_404(Project, pk=project_pk)
    pl, _ = PickingList.objects.get_or_create(project=project, defaults={'created_by': request.user})
    items = pl.items.select_related('product').all()
    customer_notes = None
    if project.customer:
        cp = CustomerProfile.objects.filter(name__iexact=project.customer).first()
        if cp and cp.important_notes:
            customer_notes = cp.important_notes

    # Fallback table for stock items that don't have a weight set yet —
    # Stock's own weight field (set on the Stock page) takes priority.
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
    auto_weight = 0
    import re as _re_w
    for item in items:
        if item.item_type == 'stock' and item.product:
            code = item.product.code.upper()
            desc = item.product.description or ''
            if _re_w.search(r'CHIPBOARD|MELAMINE|MFC|TIMBER DECK|BOARD', desc, _re_w.I):
                # Board: parse mm dims from description, 1kg per sqft
                m = _re_w.match(r'^\s*(\d+)\s*[xX]\s*(\d+)', desc)
                if m:
                    sqft = (int(m.group(1)) * int(m.group(2))) / 92903.04
                    auto_weight += sqft * float(item.quantity)
            elif item.product.weight is not None:
                auto_weight += float(item.product.weight) * float(item.quantity)
            elif code in ITEM_WEIGHTS:
                auto_weight += ITEM_WEIGHTS[code] * float(item.quantity)

    return render(request, 'projects/picking_list_print.html', {
        'project': project, 'pl': pl, 'items': items,
        'customer_notes': customer_notes,
        'auto_weight': round(auto_weight, 1),
    })




@login_required
@require_POST
def picking_item_qty(request, pk):
    item = get_object_or_404(PickingListItem, pk=pk)
    locked = _picking_locked_response(item.picking_list)
    if locked:
        return locked
    data = json.loads(request.body)
    qty = float(data.get('quantity', item.quantity))
    if qty > 0:
        item.quantity = qty
        item.save()
    return JsonResponse({'ok': True, 'quantity': float(item.quantity)})


# ── Picking list generation from costing ─────────────────────────────────────

BACK_TO_BACK_KIT = {
    'M8X50':  1,  # M8x50 bolt per fixing point
    'M8NUT':  1,  # M8 nut per fixing point
}
# approx £0.10 each combined
MOBILE_BASE_KIT = {
    'FIX-MOBF/PLATE':  1,
    'FIX-MOBF/BUFFER': 1,
    'M8X35HEXHD':      1,
    'M8NYLOCK':        1,
    'TEKS5.5X25MM':    1,
}

SM_FOOT_FIXINGS = {
    'SMFOOT':           1,
    'M6PENNYWASHER':    1,
    'M8X35HEXHD':       1,
    'M8NUT':            1,
    'M8WASHER':         1,
    'FIXBROWNRAWL/WALLPLUGS': 1,
    'FIX10X1.5CSK':     1,
}
WALL_FIXING_KIT = {
    'M6PENNYWASHER':    1,
    'FIXBROWNRAWL/WALLPLUGS': 1,
    'FIX10X3CSK':       1,
}



@login_required
@require_POST
def picking_from_costing(request, pk):
    """Generate a picking list preview from costing lines. Does NOT save yet —
    returns the proposed items as JSON for the user to review and edit."""
    project = get_object_or_404(Project, pk=pk)
    cost = project.costs.filter(is_accepted=True).first()
    if not cost:
        return JsonResponse({'ok': False, 'error': 'No costing option has been approved yet. Please approve an option on the Costing page before generating a picking list.'})

    lines = cost.lines.select_related('product').all()

    # Build reference using existing function
    uprights_count = sum(int(float(l.quantity)) * 2 for l in lines if l.line_type == 'frame')
    acc_with_uprights = [
        {'code': acc.code, 'uprights': uprights_count}
        for acc in cost.accessories.all()
    ]
    ref = generate_picking_reference(list(lines), acc_with_uprights, cost.wall_fixings, cost.back_to_back_fixings, cost.mobile_base_sets)

    # Add NS/extras lines from costing as NS picking items
    # Note: inhang/outhang are handled by generate_picking_reference into stock codes
    ns_lines = []
    for line in lines:
        if line.line_type in ('ns', 'extras', 'wipe', 'loadsign'):
            ns_lines.append({
                'type': 'ns',
                'description': line.description,
                'quantity': int(float(line.quantity)),
            })

    # Add accessories
    uprights = sum(int(float(l.quantity)) * 2 for l in lines if l.line_type == 'frame')
    for acc in cost.accessories.all():
        ref[acc.code or acc.name] = uprights

    # Category sort order for picking list
    def picking_sort_key(code):
        c = code.upper()
        # 1. Uprights (BTP/TP post codes)
        if c.startswith('BTP') or c.startswith('TP') and not c.startswith('TPC') and not c.startswith('TFCV') and not c.startswith('TWB'):
            return (1, c)
        # 2. Post connectors (TPC)
        if c.startswith('TPC'):
            return (2, c)
        # 3. Shelf beams (TFCV, TWB)
        if c.startswith('TFCV') or c.startswith('TWB'):
            return (3, c)
        # 4. Boards/decks (e.g. 48X27, 48X27MFC — digit-starting codes),
        # including NS2 non-stock board fallback lines
        if c[0].isdigit() or c.startswith('NS2::'):
            return (4, c)
        # 5. Hanging rails (HRS, JWGR)
        if c.startswith('HRS') or c.startswith('JWGR'):
            return (5, c)
        # 6. Everything else (fixings, accessories, rods, etc.)
        return (6, c)

    # Enrich stock items with descriptions and stock levels, in category order
    stock_items = []
    for code, qty in sorted(ref.items(), key=lambda x: picking_sort_key(x[0])):
        ns2 = parse_ns2_key(code)
        if ns2:
            _, _, ns2_desc = ns2
            ns2_prod = Product.objects.filter(code__iexact='NS2', is_active=True).first()
            stock_items.append({
                'type': 'stock',
                'code': 'NS2',
                'description': ns2_desc,
                'quantity': qty,
                'in_stock': float(ns2_prod.quantity) if ns2_prod else None,
                'product_id': ns2_prod.pk if ns2_prod else None,
                'found': bool(ns2_prod),
            })
            continue
        prod_any = Product.objects.filter(code__iexact=code).first()
        # If code exists but is inactive, skip it from picking list
        if prod_any and not prod_any.is_active:
            continue
        prod = prod_any  # active or not found
        stock_items.append({
            'type': 'stock',
            'code': code,
            'description': prod.description if prod else code,
            'quantity': qty,
            'in_stock': float(prod.quantity) if prod else None,
            'product_id': prod.pk if prod else None,
            'found': bool(prod),
        })
        # After mesh panel stock item, add cut-to message if applicable
        if code.upper().startswith('MP'):
            for line in lines:
                if line.line_type == 'mesh' and (line.size or 'MP120X48') == code:
                    if '[cut to' in (line.description or ''):
                        cut_info = line.description.split('[cut to')[-1].strip().rstrip(']')
                        stock_items.append({
                            'type': 'message',
                            'description': f'Cut to {cut_info} ({int(float(line.quantity))} panel{"s" if float(line.quantity)!=1 else ""})',
                            'quantity': None,
                        })
        if code.upper() == '19MMRODS':
            # Group top ties by their actual length (parsed from description)
            import re as _re_tt2
            from collections import defaultdict as _dd
            len_counts = _dd(int)
            for l in lines:
                if l.line_type == 'toptie':
                    m = _re_tt2.search(r'(\d{3,5})', l.description or '')
                    tlen = int(m.group(1)) if m else TOP_TIE_ROD_LENGTH
                    len_counts[tlen] += int(float(l.quantity))
            for tlen, tqty in sorted(len_counts.items()):
                total_mm = tqty * tlen
                stock_items.append({
                    'type': 'message',
                    'description': f'Cut {tqty} x {tlen}mm lengths from 6450mm rods (total {total_mm}mm)',
                    'quantity': None,
                })

    # ── Longspan items (separate explosion) ──
    from ..longspan_data import ls_explode_frame, ls_explode_shelf, ls_explode_trolley
    ls_ref = {}
    for line in lines:
        parts = [p.strip() for p in (line.size or '').split(' x ')]
        if line.line_type == 'ls_frame' and len(parts) == 4:
            # lsframe x H x D x system
            try:
                h, d = int(parts[1]), int(parts[2])
                system = parts[3]
                for code, q in ls_explode_frame(h, d, float(line.quantity), system).items():
                    ls_ref[code] = ls_ref.get(code, 0) + q
            except (ValueError, IndexError):
                pass
        elif line.line_type == 'ls_shelf' and len(parts) == 3:
            try:
                w, d = int(parts[1]), int(parts[2])
                for code, q in ls_explode_shelf(w, d, float(line.quantity)).items():
                    ls_ref[code] = ls_ref.get(code, 0) + q
            except (ValueError, IndexError):
                pass
        elif line.line_type == 'ls_trolley' and len(parts) == 2:
            try:
                d = int(parts[1])
                tqty = float(line.quantity)
                for code, q in ls_explode_trolley(d, tqty).items():
                    ls_ref[code] = ls_ref.get(code, 0) + q
            except (ValueError, IndexError):
                pass

    def ls_sort_key(code):
        c = code.upper()
        if c.startswith('LSP'): return (1, c)       # posts
        if c.startswith('LSH'): return (2, c)       # horizontals
        if c.startswith('LSD'): return (3, c)       # diagonals
        if c in ('LSFT', 'LSPACER'): return (4, c)  # feet/spacers
        if c.startswith('LSB'): return (5, c)       # beams
        if c.startswith('LSCB'): return (6, c)      # chipboard supports
        if c[0].isdigit(): return (7, c)            # boards
        return (8, c)                                # fixings/other

    ls_stock_items = []
    for code, qty in sorted(ls_ref.items(), key=lambda x: ls_sort_key(x[0])):
        prod_any = Product.objects.filter(code__iexact=code).first()
        if prod_any and not prod_any.is_active:
            continue
        ls_stock_items.append({
            'type': 'stock',
            'code': code,
            'description': prod_any.description if prod_any else code,
            'quantity': qty,
            'in_stock': float(prod_any.quantity) if prod_any else None,
            'product_id': prod_any.pk if prod_any else None,
            'found': bool(prod_any),
        })

    # If there are longspan items, prepend a separator then append them
    if ls_stock_items:
        stock_items.append({'type': 'message', 'description': '── LONGSPAN ITEMS ──', 'quantity': None})
        stock_items.extend(ls_stock_items)

    return JsonResponse({
        'ok': True,
        'stock_items': stock_items,
        'ns_lines': ns_lines,
    })




@login_required
@require_POST
def picking_from_costing_save(request, pk):
    """Save the reviewed/edited costing-generated items to the picking list."""
    project = get_object_or_404(Project, pk=pk)
    data = json.loads(request.body)
    items = data.get('items', [])
    replace = data.get('replace', False)

    pl, _ = PickingList.objects.get_or_create(
        project=project,
        defaults={'created_by': request.user, 'status': 'draft'}
    )

    if replace:
        pl.items.all().delete()

    sort = pl.items.count()
    added = 0
    for item in items:
        if item.get('skip'):
            continue
        # Message items don't have a quantity — handle first
        if item['type'] == 'message':
            PickingListItem.objects.create(
                picking_list=pl, item_type='message',
                ns_description=item.get('description', ''),
                quantity=0, sort_order=sort
            )
            sort += 1
            added += 1
            continue
        qty = float(item.get('quantity', 1))
        if qty <= 0:
            continue
        if item['type'] == 'stock' and item.get('product_id'):
            prod = Product.objects.filter(pk=item['product_id'], is_active=True).first()
            if prod:
                # ns_description carries a per-line description override — used
                # by generic codes like NS2 where the product's own description
                # ("NON-STOCK ITEM — THIS NEEDS OVERTYPED...") isn't specific
                # enough. For a normal stock item this just duplicates
                # product.description, which is harmless.
                custom_desc = item.get('description', '')
                PickingListItem.objects.create(
                    picking_list=pl, item_type='stock',
                    product=prod, quantity=qty, sort_order=sort,
                    ns_description=custom_desc if custom_desc != prod.description else '',
                )
                sort += 1
                added += 1
            else:
                # Product id given but not found — add as NS
                PickingListItem.objects.create(
                    picking_list=pl, item_type='ns',
                    ns_description=item.get('code') or item.get('description', ''),
                    quantity=qty, sort_order=sort
                )
                sort += 1
                added += 1
        elif item['type'] == 'stock' and not item.get('product_id'):
            # Stock-type but no product in DB — add as NS using the code
            PickingListItem.objects.create(
                picking_list=pl, item_type='ns',
                ns_description=item.get('code') or item.get('description', ''),
                quantity=qty, sort_order=sort
            )
            sort += 1
            added += 1
        elif item['type'] == 'ns':
            PickingListItem.objects.create(
                picking_list=pl, item_type='ns',
                ns_description=item.get('description', ''),
                quantity=qty, sort_order=sort
            )
            sort += 1
            added += 1

    return JsonResponse({'ok': True, 'added': added, 'pl_pk': pl.pk})




@login_required
def picking_fitting_notes(request, pk):
    """Show all fitting notes matching the stock items in this picking list, for printing."""
    pl = get_object_or_404(PickingList, pk=pk)
    project = pl.project
    product_ids = set(
        pl.items.filter(item_type='stock', product__isnull=False).values_list('product_id', flat=True)
    )
    template_ids = set(pl.templates_used.values_list('pk', flat=True))
    from django.db.models import Q
    notes = FittingNote.objects.filter(
        Q(products__in=product_ids) | Q(templates__in=template_ids)
    ).distinct().prefetch_related('products')
    return render(request, 'projects/fitting_notes_print.html', {
        'pl': pl, 'project': project, 'notes': notes,
    })




@login_required
def picking_fitting_notes_pdf(request, pk):
    """Merge selected fitting notes into one PDF, each drawing on its own page."""
    import io
    from django.http import HttpResponse
    from pypdf import PdfWriter, PdfReader
    import img2pdf

    pl = get_object_or_404(PickingList, pk=pk)
    # ids come as comma-separated query param ?ids=1,2,3
    ids_param = request.GET.get('ids', '')
    if ids_param:
        sel_ids = [int(i) for i in ids_param.split(',') if i.strip().isdigit()]
        notes = FittingNote.objects.filter(pk__in=sel_ids).prefetch_related('products')
        # preserve order of sel_ids
        notes = sorted(notes, key=lambda n: sel_ids.index(n.pk))
    else:
        product_ids = set(pl.items.filter(item_type='stock', product__isnull=False).values_list('product_id', flat=True))
        template_ids = set(pl.templates_used.values_list('pk', flat=True))
        from django.db.models import Q
        notes = FittingNote.objects.filter(
            Q(products__in=product_ids) | Q(templates__in=template_ids)
        ).distinct().prefetch_related('products')

    writer = PdfWriter()
    for n in notes:
        if not n.file_data:
            continue
        data = bytes(n.file_data)
        try:
            if n.is_pdf:
                reader = PdfReader(io.BytesIO(data))
                for page in reader.pages:
                    writer.add_page(page)
            else:
                # image -> one-page PDF via img2pdf
                pdf_bytes = img2pdf.convert(data)
                reader = PdfReader(io.BytesIO(pdf_bytes))
                for page in reader.pages:
                    writer.add_page(page)
        except Exception:
            continue

    if len(writer.pages) == 0:
        return HttpResponse('No printable drawings selected.', status=404)

    out = io.BytesIO()
    writer.write(out)
    out.seek(0)
    response = HttpResponse(out.read(), content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="fitting_notes_{pl.project.project_name}.pdf"'
    return response




@login_required
@require_POST
def picking_list_delete(request, pk):
    pl = get_object_or_404(PickingList, pk=pk)
    if pl.allocated:
        return JsonResponse({'error': 'This picking list is locked because stock has been allocated. Release the allocation first to delete it.'}, status=400)
    pl.delete()
    return JsonResponse({'ok': True})


