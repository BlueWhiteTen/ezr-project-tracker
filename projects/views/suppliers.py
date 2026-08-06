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

from ..models import Project, ProjectLog, Customer, Comment, Message, Notification, TeamMessage, StaffProfile, LeaveRequest, InstallationReport, ReportPhoto, SatisfactionNote, CustomerProfile, ProjectDocument, Product, PickingList, PickingListItem, PickingTemplate, PickingTemplateItem, MaterialPrice, ProjectCost, ProjectCostLine, UprightAccessory, AccessoryOverride, Reminder, FittingCrew, Supplier, PurchaseOrder, PurchaseOrderLine, StockMovement, FittingNote, ProjectQuote, PriceListItem, QuotePhoto, QuoteAttachedPhoto, ProformaInvoice, DeliveryPhase, ProjectPresence, SupplierDocument, SupplierContact
from ..forms import RegisterForm, ProjectForm
from .utils import require_feature


# ── Auth ──────────────────────────────────────────────────────────────────────

from django.contrib.auth.views import (
    PasswordResetView, PasswordResetDoneView,
    PasswordResetConfirmView, PasswordResetCompleteView,
)

@login_required
def supplier_list(request):
    q = request.GET.get('q', '').strip()
    letter = request.GET.get('letter', '').strip().upper()[:1]
    base = Supplier.objects.all()

    if q:
        letter = ''
        suppliers = base.filter(
            Q(name__icontains=q) | Q(contact_name__icontains=q) |
            Q(email__icontains=q) | Q(phone__icontains=q) | Q(account_number__icontains=q)
        ).order_by('name')
    else:
        if not letter:
            letter = '0'
        suppliers = base.filter(name__istartswith=letter).order_by('name')

    from django.db.models.functions import Upper, Left
    from django.db.models import Count
    counts_qs = base.annotate(first_char=Upper(Left('name', 1))).values('first_char').annotate(n=Count('id'))
    counts = {row['first_char']: row['n'] for row in counts_qs}
    index_chars = [{'char': c, 'count': counts.get(c, 0)} for c in list('0123456789') + list('ABCDEFGHIJKLMNOPQRSTUVWXYZ')]

    from django.core.paginator import Paginator
    total_matching = suppliers.count()
    paginator = Paginator(suppliers, 50)
    page_num = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page_num)
    except Exception:
        page_obj = paginator.page(1)
    suppliers = page_obj.object_list

    num_pages = paginator.num_pages
    current = page_obj.number
    if num_pages <= 15:
        page_range = list(range(1, num_pages + 1))
    else:
        pages = {1, num_pages, current}
        for d in (1, 2):
            pages.add(current - d)
            pages.add(current + d)
        pages = sorted(p for p in pages if 1 <= p <= num_pages)
        page_range = []
        prev = None
        for p in pages:
            if prev is not None and p - prev > 1:
                page_range.append(None)
            page_range.append(p)
            prev = p

    return render(request, 'projects/supplier_list.html', {
        'suppliers': suppliers, 'query': q,
        'page_obj': page_obj, 'total_matching': total_matching,
        'letter': letter, 'index_chars': index_chars, 'page_range': page_range,
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
    documents = supplier.documents.all()
    contacts = supplier.contacts.all()
    return render(request, 'projects/supplier_detail.html', {
        'supplier': supplier, 'pos': pos, 'documents': documents, 'contacts': contacts,
    })


@login_required
@require_POST
def supplier_contact_add(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    data = json.loads(request.body)
    name = (data.get('name') or '').strip()
    if not name:
        return JsonResponse({'error': 'Name is required.'}, status=400)
    next_order = supplier.contacts.count()
    contact = SupplierContact.objects.create(
        supplier=supplier, name=name,
        role=(data.get('role') or '').strip(),
        phone=(data.get('phone') or '').strip(),
        email=(data.get('email') or '').strip(),
        sort_order=next_order,
    )
    return JsonResponse({'ok': True, 'id': contact.pk})


@login_required
@require_POST
def supplier_contact_update(request, pk):
    contact = get_object_or_404(SupplierContact, pk=pk)
    data = json.loads(request.body)
    if 'name' in data:
        name = (data.get('name') or '').strip()
        if not name:
            return JsonResponse({'error': 'Name is required.'}, status=400)
        contact.name = name
    if 'role' in data:
        contact.role = (data.get('role') or '').strip()
    if 'phone' in data:
        contact.phone = (data.get('phone') or '').strip()
    if 'email' in data:
        contact.email = (data.get('email') or '').strip()
    contact.save()
    return JsonResponse({'ok': True})


@login_required
@require_POST
def supplier_contact_delete(request, pk):
    SupplierContact.objects.filter(pk=pk).delete()
    return JsonResponse({'ok': True})


@login_required
@require_feature('supplier_documents')
def supplier_documents(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    if request.method == 'POST':
        f = request.FILES.get('file')
        if not f:
            return JsonResponse({'error': 'No file'}, status=400)
        try:
            import mimetypes
            mime = f.content_type or mimetypes.guess_type(f.name)[0] or 'application/octet-stream'
            year_raw = request.POST.get('year', '').strip()
            doc = SupplierDocument.objects.create(
                supplier=supplier,
                file_data=f.read(),
                file_mime=mime,
                file_original_name=f.name,
                name=request.POST.get('name', f.name),
                doc_type=request.POST.get('doc_type', 'price_list'),
                year=int(year_raw) if year_raw.isdigit() else None,
                notes=request.POST.get('notes', ''),
                uploaded_by=request.user,
            )
            return JsonResponse({
                'id': doc.pk,
                'name': doc.name,
                'doc_type': doc.get_doc_type_display(),
                'year': doc.year,
                'url': f'/supplier-document/{doc.pk}/download/',
                'uploaded_by': request.user.get_full_name() or request.user.username,
                'uploaded_at': timezone.localtime(doc.uploaded_at).strftime('%d %b %Y, %H:%M'),
            })
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    return JsonResponse({'error': 'POST required'}, status=405)


@login_required
@require_feature('supplier_documents')
def supplier_document_download(request, pk):
    doc = get_object_or_404(SupplierDocument, pk=pk)
    if doc.file_data:
        response = HttpResponse(bytes(doc.file_data), content_type=doc.file_mime or 'application/octet-stream')
        filename = doc.file_original_name or doc.name
        response['Content-Disposition'] = f'inline; filename="{filename}"'
        return response
    return HttpResponse('File not found', status=404)


@login_required
@require_POST
@require_feature('supplier_documents')
def supplier_document_delete(request, pk):
    doc = get_object_or_404(SupplierDocument, pk=pk)
    doc.delete()
    return JsonResponse({'ok': True})




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

