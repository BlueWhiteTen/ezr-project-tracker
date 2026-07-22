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
def project_documents(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if request.method == 'POST':
        f = request.FILES.get('file')
        if not f:
            return JsonResponse({'error': 'No file'}, status=400)
        try:
            import mimetypes
            mime = f.content_type or mimetypes.guess_type(f.name)[0] or 'application/octet-stream'
            doc = ProjectDocument.objects.create(
                project=project,
                file_data=f.read(),
                file_mime=mime,
                file_original_name=f.name,
                name=request.POST.get('name', f.name),
                doc_type=request.POST.get('doc_type', 'other'),
                notes=request.POST.get('notes', ''),
                uploaded_by=request.user,
            )
            return JsonResponse({
                'id': doc.pk,
                'name': doc.name,
                'doc_type': doc.get_doc_type_display(),
                'url': f'/document/{doc.pk}/download/',
                'uploaded_by': request.user.get_full_name() or request.user.username,
                'uploaded_at': timezone.localtime(doc.uploaded_at).strftime('%d %b %Y, %H:%M'),
                'notes': doc.notes,
                'ext': f.name.split('.')[-1].upper(),
            })
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    docs = project.documents.select_related('uploaded_by').all()
    result = []
    for d in docs:
        ext = d.file_original_name.split('.')[-1].upper() if d.file_original_name else (d.file.name.split('.')[-1].upper() if d.file else '?')
        result.append({
            'id': d.pk,
            'name': d.name,
            'doc_type': d.get_doc_type_display(),
            'url': f'/document/{d.pk}/download/',
            'uploaded_by': d.uploaded_by.get_full_name() if d.uploaded_by else '',
            'uploaded_at': timezone.localtime(d.uploaded_at).strftime('%d %b %Y, %H:%M'),
            'notes': d.notes,
            'ext': ext,
        })
    return JsonResponse(result, safe=False)




@login_required
def document_download(request, pk):
    doc = get_object_or_404(ProjectDocument, pk=pk)
    if doc.file_data:
        from django.http import HttpResponse
        response = HttpResponse(bytes(doc.file_data), content_type=doc.file_mime or 'application/octet-stream')
        filename = doc.file_original_name or doc.name
        response['Content-Disposition'] = f'inline; filename="{filename}"'
        return response
    elif doc.file:
        from django.http import FileResponse
        return FileResponse(doc.file.open(), content_type='application/octet-stream')
    from django.http import HttpResponse
    return HttpResponse('File not found', status=404)




@login_required
@require_POST
def document_delete(request, pk):
    doc = get_object_or_404(ProjectDocument, pk=pk)
    try:
        if doc.file:
            doc.file.delete(save=False)
    except Exception:
        pass
    doc.delete()
    return JsonResponse({'ok': True})


# ── Stock ─────────────────────────────────────────────────────────────────────



@login_required
def stock_list(request):
    q        = request.GET.get('q', '').strip()
    sort     = request.GET.get('sort', 'code')
    direction= request.GET.get('dir', 'asc')
    low_only = request.GET.get('low', '') == '1'
    show_inactive = request.GET.get('show_inactive', '') == '1'
    category = request.GET.get('category', '').strip()

    valid_sorts = ['code','description','quantity','qty_allocated','qty_on_order','free_stock','reorder_level','reorder_qty','cost_price','weight']
    if sort not in valid_sorts:
        sort = 'code'
    order = sort if direction == 'asc' else f'-{sort}'

    products = Product.objects.all()
    if not show_inactive:
        products = products.filter(is_active=True)
    if q:
        products = products.filter(Q(code__icontains=q) | Q(description__icontains=q))
    if category:
        products = products.filter(category=category)

    low_stock_qs = products.filter(
        Q(reorder_level__gt=0, quantity__lte=models_F('reorder_level')) | Q(quantity__lt=0)
    )
    low_stock_count = low_stock_qs.count()

    if low_only:
        products = products.filter(
            Q(reorder_level__gt=0, quantity__lte=models_F('reorder_level')) | Q(quantity__lt=0)
        )

    products = products.order_by(order)

    columns = [
        {'key':'code',          'label':'Code',        'right':False, 'default_dir':'asc'},
        {'key':'description',   'label':'Description', 'right':False, 'default_dir':'asc'},
        {'key':'quantity',      'label':'In Stock',    'right':True,  'default_dir':'desc'},
        {'key':'qty_allocated', 'label':'Allocated',   'right':True,  'default_dir':'desc'},
        {'key':'qty_on_order',  'label':'On Order',    'right':True,  'default_dir':'desc'},
        {'key':'free_stock',    'label':'Free Stock',  'right':True,  'default_dir':'asc'},
        {'key':'reorder_level', 'label':'Reorder Lvl', 'right':True, 'default_dir':'desc'},
        {'key':'reorder_qty',   'label':'Reorder Qty', 'right':True, 'default_dir':'desc'},
        {'key':'cost_price',    'label':'Buying Price','right':True, 'default_dir':'desc'},
        {'key':'weight',        'label':'Weight (kg)', 'right':True, 'default_dir':'desc'},
    ]
    return render(request, 'projects/stock_list.html', {
        'products': products, 'query': q,
        'low_stock_count': low_stock_count,
        'low_only': low_only,
        'sort': sort, 'dir': direction,
        'columns': columns,
        'show_inactive': show_inactive,
        'inactive_count': Product.objects.filter(is_active=False).count(),
        'category_choices': Product.CATEGORY_CHOICES,
        'selected_category': category,
    })




@login_required
def product_search_api(request):
    q = request.GET.get('q', '').strip()
    if len(q) < 1:
        return JsonResponse([], safe=False)
    # Code starts with query first, then code contains, then description contains
    from itertools import chain
    code_starts = list(Product.objects.filter(code__istartswith=q, is_active=True).values('id','code','description','quantity')[:10])
    if len(code_starts) < 10:
        seen = {p['id'] for p in code_starts}
        code_contains = list(Product.objects.filter(code__icontains=q, is_active=True).exclude(id__in=seen).values('id','code','description','quantity')[:10-len(code_starts)])
        code_starts += code_contains
    if len(code_starts) < 10:
        seen = {p['id'] for p in code_starts}
        desc_matches = list(Product.objects.filter(description__icontains=q, is_active=True).exclude(id__in=seen).values('id','code','description','quantity')[:10-len(code_starts)])
        code_starts += desc_matches
    return JsonResponse(code_starts, safe=False)




@login_required
def stock_activity(request, pk):
    product = get_object_or_404(Product, pk=pk)
    movements = product.movements.select_related('project', 'purchase_order', 'user').all()[:200]
    rows = []
    for mv in movements:
        proj = mv.project
        proj_label = ''
        if proj:
            name = proj.project_name or ''
            extras = []
            if proj.customer and proj.customer not in name:
                extras.append(proj.customer)
            if proj.location and proj.location not in name and proj.location not in (proj.customer or ''):
                extras.append(proj.location)
            proj_label = name
            if extras:
                proj_label = f"{name} ({', '.join(extras)})" if name else ', '.join(extras)
        elif mv.purchase_order:
            proj_label = mv.purchase_order.po_number + (f' ({mv.purchase_order.supplier.name})' if mv.purchase_order.supplier else '')
        rows.append({
            'date': mv.created_at.strftime('%d %b %Y %H:%M'),
            'qty': float(mv.qty_change),
            'direction': mv.direction,
            'type': mv.get_movement_type_display(),
            'reference': proj_label or mv.reason,
            'user': (mv.user.get_full_name() or mv.user.username) if mv.user else '',
        })
    return JsonResponse({
        'ok': True,
        'code': product.code,
        'description': product.description,
        'in_stock': float(product.quantity),
        'allocated': float(product.qty_allocated),
        'free': float(product.quantity) - float(product.qty_allocated),
        'movements': rows,
    })




@login_required
def stock_print(request):
    selected_cats = request.GET.getlist('cat')
    selected_cols = request.GET.getlist('col')
    if not selected_cols:
        selected_cols = ['code', 'description']

    all_columns = [
        ('code', 'Code'), ('description', 'Description'),
        ('quantity', 'In Stock'), ('qty_allocated', 'Allocated'),
        ('qty_on_order', 'On Order'), ('free_stock', 'Free Stock'),
        ('reorder_level', 'Reorder Level'), ('reorder_qty', 'Reorder Qty'),
        ('cost_price', 'Buying Price'), ('weight', 'Weight (kg)'),
    ]
    columns = [(key, label) for key, label in all_columns if key in selected_cols]

    from collections import OrderedDict
    sections = OrderedDict()
    for val, label in Product.CATEGORY_CHOICES:
        if val in selected_cats:
            sections[val] = {'label': label, 'products': []}
    include_uncat = 'none' in selected_cats
    if include_uncat:
        sections['none'] = {'label': 'Uncategorized', 'products': []}

    if sections:
        q = Q(category__in=[k for k in sections if k != 'none'])
        if include_uncat:
            q |= Q(category='')
        for p in Product.objects.filter(q).order_by('code'):
            key = p.category if p.category in sections else 'none'
            sections[key]['products'].append(p)

    return render(request, 'projects/stock_print.html', {
        'sections': sections,
        'columns': columns,
        'category_choices': Product.CATEGORY_CHOICES,
    })


@login_required
@require_POST
def stock_create(request):
    data = json.loads(request.body)
    code = (data.get('code') or '').strip()
    if not code:
        return JsonResponse({'error': 'Product code is required.'}, status=400)
    if Product.objects.filter(code__iexact=code).exists():
        return JsonResponse({'error': f'A product with code "{code}" already exists.'}, status=400)

    def dec(key):
        v = data.get(key)
        return float(v) if v not in (None, '') else 0

    weight_val = data.get('weight')
    product = Product.objects.create(
        code=code,
        description=(data.get('description') or '').strip(),
        category=data.get('category') or '',
        quantity=dec('quantity'),
        reorder_level=dec('reorder_level'),
        reorder_qty=dec('reorder_qty'),
        cost_price=dec('cost_price'),
        weight=float(weight_val) if weight_val not in (None, '') else None,
    )
    return JsonResponse({'ok': True, 'id': product.pk})


@login_required
@require_POST
def stock_adjust(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST':
        data = json.loads(request.body)
        if data.get('delete'):
            product.delete()
            return JsonResponse({'ok': True, 'deleted': True})
        product.code        = data.get('code', product.code).strip()
        product.description = data.get('description', product.description).strip()
        old_qty = float(product.quantity)
        new_qty = float(data.get('quantity', product.quantity) or 0)
        product.quantity    = new_qty
        product.reorder_level = data.get('reorder_level', product.reorder_level)
        if 'reorder_qty' in data:
            product.reorder_qty = data.get('reorder_qty') or 0
        if 'sales_price' in data:
            product.sales_price = data.get('sales_price') or 0
        if 'cost_price' in data:
            product.cost_price = data.get('cost_price') or 0
        if 'weight' in data:
            wv = data.get('weight')
            product.weight = float(wv) if (wv not in (None, '')) else None
        if 'category' in data:
            product.category = data.get('category') or ''
        product.save()
        if new_qty != old_qty:
            reason = (data.get('reason') or '').strip() or 'Manual stock adjustment'
            StockMovement.objects.create(
                product=product, movement_type='adjust',
                qty_change=round(new_qty - old_qty, 2),
                reason=reason, user=request.user,
            )
        return JsonResponse({'ok': True, 'deleted': False})
    return JsonResponse({'id': product.pk, 'code': product.code, 'description': product.description,
                         'quantity': float(product.quantity), 'reorder_level': float(product.reorder_level),
                         'reorder_qty': float(product.reorder_qty),
                         'sales_price': float(product.sales_price), 'cost_price': float(product.cost_price),
                         'weight': float(product.weight) if product.weight is not None else None,
                         'category': product.category})


# ── Picking Lists ─────────────────────────────────────────────────────────────



@login_required
@require_POST
def stock_import(request):
    if not request.user.is_superuser:
        return JsonResponse({'error': 'Only Tasos can import stock from Excel.'}, status=403)
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

        # Map columns by header name — robust against Sage reordering its
        # export in future, unlike matching by fixed column position.
        header_row = [str(h).strip() if h is not None else '' for h in rows[0]]
        required = ['Product Code', 'Description', 'Inactive', 'Quantity In Stock',
                    'Quantity Allocated', 'Quantity On Order', 'Re-Order Level',
                    'Re-Order Quantity', 'Free Stock']
        missing = [h for h in required if h not in header_row]
        if missing:
            return JsonResponse({'error': f'Missing expected column(s): {", ".join(missing)}'}, status=400)
        col = {h: header_row.index(h) for h in required}

        def num(row, key):
            v = row[col[key]]
            return float(v) if v is not None else 0

        count = 0
        deactivated = 0
        for row in rows[1:]:
            code = str(row[col['Product Code']]).strip() if row[col['Product Code']] else ''
            if not code or code == 'None':
                continue
            desc = str(row[col['Description']]).strip() if row[col['Description']] else ''
            is_inactive = str(row[col['Inactive']] or '').strip().upper() == 'Y'

            product, _ = Product.objects.get_or_create(code=code, defaults={'description': desc})
            product.description   = desc
            product.quantity       = num(row, 'Quantity In Stock')
            product.qty_allocated  = num(row, 'Quantity Allocated')
            product.qty_on_order   = num(row, 'Quantity On Order')
            product.reorder_level  = num(row, 'Re-Order Level')
            product.reorder_qty    = num(row, 'Re-Order Quantity')
            product.free_stock     = num(row, 'Free Stock')
            # Sales price, buying price, and weight are NOT touched here —
            # those are managed directly in the app, not from Sage (Jul 2026).
            if is_inactive:
                product.is_active = False
                product.category = '1'  # 1 Discontinued
                deactivated += 1
            # If not inactive, leave is_active/category exactly as they are
            # in the app — Sage saying "active" doesn't override a manual
            # deactivation made here for an unrelated reason.
            product.save()
            count += 1
        return JsonResponse({'ok': True, 'count': count, 'deactivated': deactivated})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


