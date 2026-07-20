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
def supplier_list(request):
    q = request.GET.get('q', '').strip()
    suppliers = Supplier.objects.all().order_by('name')
    if q:
        suppliers = suppliers.filter(
            Q(name__icontains=q) | Q(contact_name__icontains=q) |
            Q(email__icontains=q) | Q(phone__icontains=q) | Q(account_number__icontains=q)
        )
    return render(request, 'projects/supplier_list.html', {
        'suppliers': suppliers, 'query': q,
    })




@login_required
def supplier_import(request):
    """Import suppliers from a Sage export (.xlsx) with columns: A/C, Name, Contact, Telephone."""
    f = request.FILES.get('file')
    if not f:
        return JsonResponse({'error': 'No file uploaded'}, status=400)
    try:
        import openpyxl
        wb = openpyxl.load_workbook(f, data_only=True)
        ws = wb.active
    except Exception as e:
        return JsonResponse({'error': f'Could not read file: {e}'}, status=400)

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return JsonResponse({'error': 'File is empty'}, status=400)

    header = [str(h).strip().lower() if h else '' for h in rows[0]]
    def col_idx(*names):
        for n in names:
            if n in header:
                return header.index(n)
        return None

    i_ac    = col_idx('a/c', 'ac', 'account')
    i_name  = col_idx('name')
    i_contact = col_idx('contact')
    i_phone = col_idx('telephone', 'phone')

    if i_name is None:
        return JsonResponse({'error': 'Could not find a "Name" column in the file.'}, status=400)

    created, updated, skipped = 0, 0, 0
    for row in rows[1:]:
        if not row or all(c in (None, '') for c in row):
            continue
        name = str(row[i_name]).strip() if i_name is not None and row[i_name] else ''
        if not name:
            skipped += 1
            continue
        ac = str(row[i_ac]).strip() if i_ac is not None and row[i_ac] else ''
        contact = str(row[i_contact]).strip() if i_contact is not None and row[i_contact] else ''
        phone = str(row[i_phone]).strip() if i_phone is not None and row[i_phone] else ''

        supplier = None
        if ac:
            supplier = Supplier.objects.filter(account_number=ac).first()
        if not supplier and not ac:
            # No A/C to disambiguate — fall back to matching by name
            supplier = Supplier.objects.filter(name__iexact=name).first()

        if supplier:
            supplier.contact_name = contact or supplier.contact_name
            supplier.phone = phone or supplier.phone
            if ac:
                supplier.account_number = ac
            supplier.save()
            updated += 1
        else:
            # Name must be unique; if a different supplier already has this name, suffix with the A/C ref
            final_name = name
            if Supplier.objects.filter(name__iexact=name).exists() and ac:
                final_name = f'{name} ({ac})'
            Supplier.objects.create(
                name=final_name, account_number=ac, contact_name=contact, phone=phone,
            )
            created += 1

    return JsonResponse({'ok': True, 'created': created, 'updated': updated, 'skipped': skipped})




@login_required
def supplier_detail(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    if request.method == 'POST':
        supplier.name           = request.POST.get('name', '').strip()
        supplier.contact_name   = request.POST.get('contact_name', '').strip()
        supplier.email          = request.POST.get('email', '').strip()
        supplier.phone          = request.POST.get('phone', '').strip()
        supplier.address_line1  = request.POST.get('address_line1', '').strip()
        supplier.address_line2  = request.POST.get('address_line2', '').strip()
        supplier.town           = request.POST.get('town', '').strip()
        supplier.county         = request.POST.get('county', '').strip()
        supplier.postcode       = request.POST.get('postcode', '').strip()
        supplier.payment_terms  = request.POST.get('payment_terms', '').strip()
        try:
            supplier.lead_time_days = int(request.POST.get('lead_time_days', 0) or 0)
        except ValueError:
            supplier.lead_time_days = 0
        supplier.notes          = request.POST.get('notes', '').strip()
        supplier.save()
        messages.success(request, 'Supplier updated.')
        return redirect('supplier_detail', pk=pk)
    pos = supplier.purchase_orders.all().order_by('-created_at')
    return render(request, 'projects/supplier_detail.html', {
        'supplier': supplier, 'pos': pos,
    })




@login_required
@require_POST
def supplier_create(request):
    data = json.loads(request.body)
    name = data.get('name', '').strip()
    if not name:
        return JsonResponse({'error': 'Name required'}, status=400)
    existing = Supplier.objects.filter(name__iexact=name).first()
    if existing:
        return JsonResponse({'id': existing.pk, 'name': existing.name, 'created': False})
    s = Supplier.objects.create(
        name=name,
        contact_name=data.get('contact_name','').strip(),
        email=data.get('email','').strip(),
        phone=data.get('phone','').strip(),
        address_line1=data.get('address_line1','').strip(),
        address_line2=data.get('address_line2','').strip(),
        town=data.get('town','').strip(),
        county=data.get('county','').strip(),
        postcode=data.get('postcode','').strip(),
        payment_terms=data.get('payment_terms','').strip(),
    )
    return JsonResponse({'id': s.pk, 'name': s.name, 'account_number': s.account_number, 'created': True})




@login_required
@require_POST
def supplier_delete(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    if supplier.purchase_orders.exists():
        return JsonResponse({'error': 'Cannot delete — supplier has purchase orders.'}, status=400)
    supplier.delete()
    return JsonResponse({'ok': True})




@login_required
def supplier_api_list(request):
    suppliers = Supplier.objects.all().order_by('name').values('id', 'name', 'account_number')
    return JsonResponse({'suppliers': list(suppliers)})


# ── Purchase Orders ───────────────────────────────────────────────────────────

