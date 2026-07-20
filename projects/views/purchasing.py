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
from .utils import (_log_po_event, _po_locked_response)


# ── Auth ──────────────────────────────────────────────────────────────────────

from django.contrib.auth.views import (
    PasswordResetView, PasswordResetDoneView,
    PasswordResetConfirmView, PasswordResetCompleteView,
)

@login_required
def po_list(request):
    q = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()
    pos = PurchaseOrder.objects.select_related('supplier', 'project').all()
    if q:
        pos = pos.filter(Q(po_number__icontains=q) | Q(supplier__name__icontains=q))
    if status:
        pos = pos.filter(status=status)
    return render(request, 'projects/po_list.html', {
        'pos': pos, 'query': q, 'status_filter': status,
        'status_choices': PurchaseOrder.STATUS_CHOICES,
    })





@login_required
def po_detail(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk)
    products = Product.objects.filter(is_active=True).order_by('code')
    suppliers = Supplier.objects.all().order_by('name')
    project_addr_json = 'null'
    if po.project:
        project_addr_json = json.dumps({
            'line1':    po.project.addr_line1,
            'line2':    po.project.addr_line2,
            'city':     po.project.addr_city,
            'county':   po.project.addr_county,
            'postcode': po.project.addr_postcode,
        })
    return render(request, 'projects/po_detail.html', {
        'po': po, 'products': products, 'suppliers': suppliers,
        'status_choices': PurchaseOrder.STATUS_CHOICES,
        'project_addr_json': project_addr_json,
        'project_pk': po.project.pk if po.project else None,
    })




@login_required
def po_print(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk)
    if not po.locked:
        po.locked = True
        po.locked_at = timezone.now()
        po.locked_by = request.user
        po.save()
    lines = po.lines.all().order_by('sort_order', 'id')
    supplier_address_lines = [
        l for l in [po.supplier.address_line1, po.supplier.address_line2,
                    po.supplier.town, po.supplier.county, po.supplier.postcode]
        if l
    ]
    if not supplier_address_lines and po.supplier.address:
        supplier_address_lines = [l for l in po.supplier.address.splitlines() if l.strip()]
    delivery_address_lines = [
        l for l in [po.del_company, po.del_line1, po.del_line2, po.del_city, po.del_county, po.del_postcode]
        if l
    ]
    if po.del_contact:
        delivery_address_lines.append(f"FAO: {po.del_contact}")
    return render(request, 'projects/po_print.html', {
        'po': po, 'lines': lines,
        'supplier_address_lines': supplier_address_lines,
        'delivery_address_lines': delivery_address_lines,
    })




@login_required
@require_POST
def po_create(request):
    data = json.loads(request.body)
    supplier_id = data.get('supplier_id')
    if not supplier_id:
        return JsonResponse({'error': 'Supplier required'}, status=400)
    supplier = get_object_or_404(Supplier, pk=supplier_id)
    po = PurchaseOrder.objects.create(
        supplier=supplier, status='draft', created_by=request.user,
        order_date=timezone.now().date(),
    )
    return JsonResponse({'ok': True, 'pk': po.pk, 'po_number': po.po_number})




@login_required
@require_POST
def po_update(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk)
    data = json.loads(request.body)
    # Expected Delivery (supplier-confirmed) is filled in after the PO is
    # sent/locked, so it's the one field allowed through even while locked.
    if not data.keys() <= {'acknowledged_date', 'ack_reference'}:
        locked = _po_locked_response(po)
        if locked:
            return locked
    if 'supplier_id' in data:
        return JsonResponse({'error': 'The supplier cannot be changed after a PO is created.'}, status=400)
    old_status = po.status
    old_project_id = po.project_id
    if 'project_id' in data:
        po.project = Project.objects.filter(pk=data['project_id']).first() if data['project_id'] else None
    if 'status' in data and not po.received:
        po.status = data['status']
    if 'order_date' in data:
        po.order_date = data['order_date'] or None
    if 'expected_date' in data:
        po.expected_date = data['expected_date'] or None
    if 'quote_number' in data:
        po.quote_number = data['quote_number']
    if 'acknowledged_date' in data:
        po.acknowledged_date = data['acknowledged_date'] or None
    if 'ack_reference' in data:
        po.ack_reference = data['ack_reference']
    if 'notes' in data:
        po.notes = data['notes']
    if 'delivery_address' in data:
        po.delivery_address = data['delivery_address']
    for field in ('del_company','del_line1','del_line2','del_city','del_county','del_postcode','del_contact'):
        if field in data:
            setattr(po, field, data[field])
    if 'carriage' in data:
        try:
            po.carriage = float(data['carriage'] or 0)
        except (ValueError, TypeError):
            pass
    po.save()
    if 'status' in data and po.status != old_status:
        _log_po_event(po, request.user, 'PO Status',
                      old_value=dict(PurchaseOrder.STATUS_CHOICES).get(old_status, old_status),
                      new_value=po.get_status_display())
    if 'project_id' in data and po.project_id != old_project_id:
        _log_po_event(po, request.user, 'PO Linked',
                      new_value=f"{po.po_number} linked to this project")
    return JsonResponse({'ok': True})




@login_required
@require_POST
def po_cancel(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk)
    if po.received:
        return JsonResponse({'error': 'Undo receipt before cancelling.'}, status=400)
    po.status = 'cancelled'
    po.save()
    _log_po_event(po, request.user, 'PO Cancelled', new_value=po.po_number)
    return JsonResponse({'ok': True})




@login_required
@require_POST
def po_lock(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk)
    po.locked = True
    po.locked_at = timezone.now()
    po.locked_by = request.user
    po.save()
    _log_po_event(po, request.user, 'PO Locked', new_value=f"{po.po_number} saved & locked")
    return JsonResponse({'ok': True})




@login_required
@require_POST
def po_unlock(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk)
    po.locked = False
    po.save()
    _log_po_event(po, request.user, 'PO Unlocked', new_value=f"{po.po_number} unlocked for editing")
    return JsonResponse({'ok': True})




@login_required
@require_POST
def po_line_add(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk)
    if po.received:
        return JsonResponse({'error': 'Cannot edit a received PO.'}, status=400)
    locked = _po_locked_response(po)
    if locked:
        return locked
    data = json.loads(request.body)
    sort = po.lines.count()
    item_type = data.get('item_type', 'stock')
    if item_type == 'message':
        line = PurchaseOrderLine.objects.create(
            purchase_order=po, item_type='message',
            description=data.get('description', '').strip(),
            quantity=0, unit_cost=0, sort_order=sort,
        )
        return JsonResponse({'ok': True, 'line_pk': line.pk, 'line_total': 0, 'po_total': po.total})
    product = None
    if data.get('product_id'):
        product = Product.objects.filter(pk=data['product_id']).first()
        item_type = 'stock' if product else 'ns'
    else:
        item_type = 'ns'
    line = PurchaseOrderLine.objects.create(
        purchase_order=po,
        item_type=item_type,
        product=product,
        description=data.get('description', '').strip(),
        quantity=float(data.get('quantity', 1) or 1),
        unit_cost=float(data.get('unit_cost', 0) or 0),
        sort_order=sort,
    )
    return JsonResponse({'ok': True, 'line_pk': line.pk, 'line_total': line.line_total, 'po_total': po.total})




@login_required
@require_POST
def po_line_update(request, pk):
    line = get_object_or_404(PurchaseOrderLine, pk=pk)
    if line.purchase_order.received:
        return JsonResponse({'error': 'Cannot edit a received PO.'}, status=400)
    locked = _po_locked_response(line.purchase_order)
    if locked:
        return locked
    data = json.loads(request.body)
    if 'quantity' in data:
        line.quantity = float(data['quantity'] or 0)
    if 'unit_cost' in data:
        line.unit_cost = float(data['unit_cost'] or 0)
    line.save()
    return JsonResponse({'ok': True, 'line_total': line.line_total, 'po_total': line.purchase_order.total})




@login_required
@require_POST
def po_line_delete(request, pk):
    line = get_object_or_404(PurchaseOrderLine, pk=pk)
    if line.purchase_order.received:
        return JsonResponse({'error': 'Cannot edit a received PO.'}, status=400)
    locked = _po_locked_response(line.purchase_order)
    if locked:
        return locked
    po = line.purchase_order
    line.delete()
    return JsonResponse({'ok': True, 'po_total': po.total})




@login_required
@require_POST
def po_receive(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk)
    if po.received:
        return JsonResponse({'error': 'Already received.'}, status=400)
    # Apply stock increases for each line with a linked product
    for line in po.lines.all():
        if line.product:
            remaining = line.qty_outstanding
            if remaining > 0:
                line.product.quantity = float(line.product.quantity or 0) + remaining
                line.product.save(update_fields=['quantity'])
                StockMovement.objects.create(
                    product=line.product, purchase_order=po, project=po.project,
                    movement_type='received',
                    qty_change=remaining, reason=f'Received on {po.po_number}',
                    user=request.user,
                )
        if line.item_type != 'message':
            line.qty_received = line.quantity
            line.save(update_fields=['qty_received'])
    po.status_before_receive = po.status
    po.received = True
    po.received_at = timezone.now()
    po.received_by = request.user
    po.status = 'received'
    po.save()
    _log_po_event(po, request.user, 'PO Received',
                  old_value=po.status_before_receive, new_value=f"{po.po_number} fully received, stock updated")
    return JsonResponse({'ok': True})




@login_required
@require_POST
def po_receive_partial(request, pk):
    """Book in a (partial) delivery: receive specific quantities per line."""
    po = get_object_or_404(PurchaseOrder, pk=pk)
    if po.status == 'received':
        return JsonResponse({'error': 'Already fully received.'}, status=400)
    data = json.loads(request.body)
    receipts = data.get('receipts', {})  # {line_pk: qty_now}
    for line in po.lines.all():
        if line.item_type == 'message':
            continue
        try:
            qty_now = float(receipts.get(str(line.pk), 0) or 0)
        except (ValueError, TypeError):
            qty_now = 0
        if qty_now <= 0:
            continue
        # Don't allow receiving more than outstanding
        qty_now = min(qty_now, line.qty_outstanding)
        if qty_now <= 0:
            continue
        line.qty_received = float(line.qty_received) + qty_now
        line.save(update_fields=['qty_received'])
        if line.product:
            line.product.quantity = float(line.product.quantity or 0) + qty_now
            line.product.save(update_fields=['quantity'])
            StockMovement.objects.create(
                product=line.product, purchase_order=po, project=po.project,
                movement_type='received',
                qty_change=qty_now, reason=f'Part-received on {po.po_number}',
                user=request.user,
            )
    # Derive status
    lines = [l for l in po.lines.all() if l.item_type != 'message']
    if lines and all(l.is_fully_received for l in lines):
        po.status = 'received'
        po.received = True
        po.received_at = timezone.now()
        po.received_by = request.user
    elif any(float(l.qty_received) > 0 for l in lines):
        po.status = 'part_received'
    po.save()
    _log_po_event(po, request.user, 'PO Part Received',
                  new_value=f"{po.po_number} — delivery booked in, now {po.get_status_display()}")
    return JsonResponse({'ok': True, 'status': po.status, 'status_label': po.get_status_display()})




@login_required
@require_POST
def po_unreceive(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk)
    if not po.received:
        return JsonResponse({'error': 'Not received.'}, status=400)
    # Reverse stock movements for this PO
    for mv in po.movements.all():
        mv.product.quantity = float(mv.product.quantity or 0) - float(mv.qty_change)
        mv.product.save(update_fields=['quantity'])
        mv.delete()
    for line in po.lines.all():
        if line.item_type != 'message':
            line.qty_received = 0
            line.save(update_fields=['qty_received'])
    po.received = False
    po.received_at = None
    po.received_by = None
    po.status = po.status_before_receive or 'confirmed'
    po.status_before_receive = ''
    po.save()
    _log_po_event(po, request.user, 'PO Receipt Undone', new_value=f"{po.po_number} receipt undone, stock reversed")
    return JsonResponse({'ok': True})


# ── Project Documents ─────────────────────────────────────────────────────────



@login_required
@require_POST
def delivery_phase_add(request, pk):
    project = get_object_or_404(Project, pk=pk)
    data = json.loads(request.body)
    sort = project.delivery_phases.count()
    phase = DeliveryPhase.objects.create(
        project=project,
        label=data.get('label', '').strip() or f'Phase {sort+1}',
        notes=data.get('notes', '').strip(),
        sort_order=sort,
    )
    return JsonResponse({'ok': True, 'pk': phase.pk})




@login_required
@require_POST
def delivery_phase_update(request, pk):
    phase = get_object_or_404(DeliveryPhase, pk=pk)
    data = json.loads(request.body)
    if 'label' in data:
        phase.label = data['label'].strip()
    if 'notes' in data:
        phase.notes = data['notes'].strip()
    if 'delivered' in data:
        phase.delivered = bool(data['delivered'])
        phase.delivered_on = timezone.now().date() if phase.delivered else None
    phase.save()
    # If some (not all) phases delivered, mark project part-delivered
    proj = phase.project
    phases = proj.delivery_phases.all()
    if phases:
        if all(p.delivered for p in phases) and proj.status not in ('completed','cancelled'):
            pass  # leave to user to mark completed
        elif any(p.delivered for p in phases) and proj.status not in ('completed','cancelled'):
            proj.status = 'part_delivered'
            proj.save(update_fields=['status'])
    return JsonResponse({'ok': True, 'delivered_on': phase.delivered_on.strftime('%d %b %Y') if phase.delivered_on else ''})




@login_required
@require_POST
def delivery_phase_delete(request, pk):
    get_object_or_404(DeliveryPhase, pk=pk).delete()
    return JsonResponse({'ok': True})




@login_required
def project_search(request):
    q = request.GET.get("q", "").strip()
    if not q:
        return JsonResponse({"results": []})
    results = Project.objects.filter(
        Q(project_number__icontains=q) |
        Q(project_name__icontains=q) |
        Q(customer__icontains=q)
    ).exclude(status="cancelled").order_by("-project_number")[:10]
    return JsonResponse({"results": [
        {"pk": p.pk, "label": ("Ref " + str(p.project_number) + " — " if p.project_number else "") + p.project_name}
        for p in results
    ]})


