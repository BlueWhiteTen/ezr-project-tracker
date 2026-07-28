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
def customer_autocomplete(request):
    q = request.GET.get('q','').strip()
    if len(q) < 1:
        return JsonResponse([], safe=False)
    results = CustomerProfile.objects.filter(name__icontains=q).values('id', 'name')[:8]
    return JsonResponse(list(results), safe=False)




@login_required
def customer_history(request, pk):
    customer = get_object_or_404(CustomerProfile, pk=pk)
    projects = Project.objects.filter(
        customer__iexact=customer.name
    ).order_by('-created_at').select_related('assigned_to')
    total_projects = projects.count()
    # Total sell price from completed costings
    total_value = 0
    for p in projects:
        if hasattr(p, 'cost'):
            c = p.cost
            lines_total = sum(l.line_total for l in c.lines.all())
            uprights = sum(int(float(l.quantity))*2 for l in c.lines.all() if l.line_type=='frame')
            for acc in c.accessories.all():
                lines_total += float(acc.unit_price) * uprights
            markup_amt = lines_total * float(c.markup) / 100
            sell = lines_total + markup_amt + float(c.labour) + float(c.delivery)
            total_value += sell
    return render(request, 'projects/customer_history.html', {
        'customer': customer,
        'projects': projects,
        'total_projects': total_projects,
        'total_value': round(total_value, 2),
    })




@login_required
def customer_import(request):
    if not request.user.is_superuser:
        return JsonResponse({'error': 'Only Tasos can import customers from Excel.'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    import openpyxl
    f = request.FILES.get('file')
    if not f:
        return JsonResponse({'error': 'No file'}, status=400)
    try:
        wb = openpyxl.load_workbook(f)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return JsonResponse({'error': 'File is empty.'}, status=400)

        # Map columns by header name — robust against the spreadsheet's
        # column order changing, unlike matching by fixed position.
        header_row = [str(h).strip() if h is not None else '' for h in rows[0]]
        required = ['A/C', 'Name', 'Contact', 'Telephone',
                    'VAT Number', 'Company Reg. Number', 'Email 1', 'Email 3']
        missing = [h for h in required if h not in header_row]
        if missing:
            return JsonResponse({'error': f'Missing expected column(s): {", ".join(missing)}'}, status=400)
        col = {h: header_row.index(h) for h in required}

        def text(row, key):
            v = row[col[key]]
            return str(v).strip() if v is not None else ''

        created = 0
        skipped = 0
        for row in rows[1:]:
            name = text(row, 'Name')
            if not name:
                skipped += 1
                continue

            # Always create a new record — never match against, or update,
            # an existing one. Any duplicates this creates are intentional;
            # they get reconciled by hand afterwards, not merged here.
            CustomerProfile.objects.create(
                name=name,
                account_number=text(row, 'A/C'),
                contact_name=text(row, 'Contact'),
                phone=text(row, 'Telephone'),
                vat_number=text(row, 'VAT Number'),
                company_reg_number=text(row, 'Company Reg. Number'),
                email=text(row, 'Email 1'),
                email3=text(row, 'Email 3'),
            )
            created += 1
        return JsonResponse({'ok': True, 'created': created, 'skipped': skipped})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
def customer_list(request):
    q = request.GET.get('q', '').strip()
    show_inactive = request.GET.get('show_inactive', '') == '1'
    customers = CustomerProfile.objects.all().order_by('name')
    if not show_inactive:
        customers = customers.filter(is_active=True)
    if q:
        customers = customers.filter(
            Q(name__icontains=q) | Q(contact_name__icontains=q) |
            Q(email__icontains=q) | Q(phone__icontains=q)
        )
    return render(request, 'projects/customer_list.html', {
        'customers': customers, 'query': q,
        'show_inactive': show_inactive,
        'inactive_count': CustomerProfile.objects.filter(is_active=False).count(),
    })




@login_required
def customer_detail(request, pk):
    customer = get_object_or_404(CustomerProfile, pk=pk)
    if request.method == 'POST':
        old_name = customer.name
        customer.name         = request.POST.get('name', '').strip()
        customer.contact_name = request.POST.get('contact_name', '').strip()
        customer.email        = request.POST.get('email', '').strip()
        customer.email2       = request.POST.get('email2', '').strip()
        customer.email3       = request.POST.get('email3', '').strip()
        customer.phone        = request.POST.get('phone', '').strip()
        customer.vat_number   = request.POST.get('vat_number', '').strip()
        customer.company_reg_number = request.POST.get('company_reg_number', '').strip()
        customer.is_active = request.POST.get('is_active') == 'on'
        customer.eori_number  = request.POST.get('eori_number', '').strip()
        customer.account_number = request.POST.get('account_number', '').strip()
        customer.address_line1 = request.POST.get('address_line1', '').strip()
        customer.address_line2 = request.POST.get('address_line2', '').strip()
        customer.town          = request.POST.get('town', '').strip()
        customer.county        = request.POST.get('county', '').strip()
        customer.postcode      = request.POST.get('postcode', '').strip()
        customer.country       = request.POST.get('country', '').strip() or 'United Kingdom'
        customer.notes           = request.POST.get('notes', '').strip()
        customer.important_notes = request.POST.get('important_notes', '').strip()
        customer.save()
        # Cascade name change to all projects that reference the old name
        if customer.name and customer.name != old_name:
            affected = Project.objects.filter(customer__iexact=old_name)
            updated = 0
            for p in affected:
                p.customer = customer.name
                p.project_name = f"{customer.name} — {p.location}" if p.location else customer.name
                p.save(update_fields=['customer', 'project_name'])
                updated += 1
            if updated:
                messages.success(request, f'Customer updated. {updated} project{"s" if updated != 1 else ""} also updated to "{customer.name}".')
            else:
                messages.success(request, 'Customer updated.')
        else:
            messages.success(request, 'Customer updated.')
        return redirect('customer_detail', pk=pk)
    linked_projects = customer.projects.all()
    text_matched = Project.objects.filter(customer__iexact=customer.name).exclude(pk__in=linked_projects.values('pk'))
    projects = (linked_projects | text_matched).order_by('-created_at')
    pos = PurchaseOrder.objects.filter(project__in=projects).select_related('supplier', 'project').order_by('-created_at')
    from ..countries import COUNTRIES
    return render(request, 'projects/customer_detail.html', {
        'customer': customer, 'projects': projects, 'pos': pos, 'countries': COUNTRIES,
    })




@login_required
@require_POST
def customer_create(request):
    data = json.loads(request.body)
    name = data.get('name', '').strip()
    if not name:
        return JsonResponse({'error': 'Name required'}, status=400)
    try:
        existing = CustomerProfile.objects.filter(name__iexact=name).first()
        if existing:
            return JsonResponse({'id': existing.pk, 'name': existing.name, 'created': False})
        c = CustomerProfile.objects.create(
            name=name,
            contact_name=data.get('contact_name','').strip(),
            email=data.get('email','').strip(),
            phone=data.get('phone','').strip(),
            address=data.get('address','').strip(),
            notes=data.get('notes','').strip(),
        )
        return JsonResponse({'id': c.pk, 'name': c.name, 'created': True})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)




@login_required
@require_POST
def customer_delete(request, pk):
    customer = get_object_or_404(CustomerProfile, pk=pk)
    customer.delete()
    return JsonResponse({'ok': True})


# ── Suppliers ─────────────────────────────────────────────────────────────────

