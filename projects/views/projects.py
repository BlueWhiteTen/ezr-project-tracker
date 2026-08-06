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

from ..models import Project, ProjectLog, Customer, Comment, Message, Notification, TeamMessage, StaffProfile, LeaveRequest, InstallationReport, ReportPhoto, SatisfactionNote, CustomerProfile, ProjectDocument, Product, PickingList, PickingListItem, PickingTemplate, PickingTemplateItem, MaterialPrice, ProjectCost, ProjectCostLine, UprightAccessory, AccessoryOverride, Reminder, FittingCrew, Supplier, PurchaseOrder, PurchaseOrderLine, StockMovement, FittingNote, ProjectQuote, PriceListItem, QuotePhoto, QuoteAttachedPhoto, ProformaInvoice, DeliveryPhase, ProjectPresence, ProjectManualPO
from ..forms import RegisterForm, ProjectForm
from .utils import (_handle_quoted_status_reminders, _log_changes, _next_project_number, _snap)


# ── Auth ──────────────────────────────────────────────────────────────────────

from django.contrib.auth.views import (
    PasswordResetView, PasswordResetDoneView,
    PasswordResetConfirmView, PasswordResetCompleteView,
)

@login_required
def project_create(request):
    if request.method == 'POST':
        form = ProjectForm(request.POST)
        if form.is_valid():
            p = form.save(commit=False)
            cust = form.cleaned_data.get('customer','').strip()
            loc  = form.cleaned_data.get('location','').strip()
            p.customer = cust
            p.location = loc
            p.project_name = f"{cust} — {loc}" if loc else cust or 'New Project'
            p.created_by = request.user
            p.last_edited_by = request.user
            p.assigned_to = request.user
            p.project_number = _next_project_number()
            if cust:
                Customer.objects.get_or_create(name=cust)
                profile_id = request.POST.get('customer_profile_id', '').strip()
                if profile_id:
                    p.customer_profile = CustomerProfile.objects.filter(pk=profile_id).first()
                else:
                    # No suggestion was picked — link to an existing profile with
                    # this exact name if there is one, otherwise create a real
                    # Customer record for it rather than leaving it as text-only.
                    profile = CustomerProfile.objects.filter(name__iexact=cust).first()
                    if not profile:
                        profile = CustomerProfile.objects.create(name=cust)
                    p.customer_profile = profile
            p.save()
            ProjectLog.objects.create(project=p, user=request.user, field='Project created', old_value='', new_value=p.project_name)
            messages.success(request, 'Project created.')
            return redirect('dashboard')
    else:
        form = ProjectForm()
    return render(request, 'projects/project_form.html', {'form': form, 'action': 'Create'})




@login_required
@require_POST
def project_quick_create(request):
    """Creates project with just customer + location, returns JSON with new project pk."""
    if request.method == 'POST':
        data     = json.loads(request.body)
        customer = data.get('customer','').strip()
        location = data.get('location','').strip()
        if not customer:
            return JsonResponse({'error': 'Customer is required'}, status=400)
        name = f"{customer} — {location}" if location else customer
        kwargs = dict(project_name=name, customer=customer, location=location,
                      created_by=request.user, last_edited_by=request.user,
                      assigned_to=request.user, project_number=_next_project_number())
        # Optional pre-filled dates from calendar
        del_date  = data.get('delivery_date','')
        inst_date = data.get('installation_date','')
        if del_date:
            from datetime import datetime
            kwargs['delivery_date']    = datetime.strptime(del_date, '%Y-%m-%d').date()
            kwargs['delivery_required'] = True
        if inst_date:
            from datetime import datetime
            kwargs['installation_date']    = datetime.strptime(inst_date, '%Y-%m-%d').date()
            kwargs['installation_required'] = True
            kwargs['installation_date_type'] = 'exact'
        p = Project.objects.create(**kwargs)
        # Auto-create customer profile, and link the project to it
        if customer:
            profile = CustomerProfile.objects.filter(name__iexact=customer).first()
            if not profile:
                profile = CustomerProfile.objects.create(name=customer)
            p.customer_profile = profile
            p.save(update_fields=['customer_profile'])
        Customer.objects.get_or_create(name=customer)
        ProjectLog.objects.create(project=p, user=request.user, field='Project created', old_value='', new_value=p.project_name)
        return JsonResponse({'pk': p.pk, 'name': p.project_name})
    return JsonResponse({'error': 'POST required'}, status=405)




@login_required
def project_edit(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if request.method == 'POST':
        before = _snap(project)
        form = ProjectForm(request.POST, instance=project)
        if form.is_valid():
            old_status_val = project.status
            had_no_assignee = not project.assigned_to_id
            edited = form.save(commit=False)
            edited.last_edited_by = request.user
            if had_no_assignee and not edited.assigned_to_id:
                edited.assigned_to = request.user
            cust = form.cleaned_data.get('customer','').strip()
            loc  = form.cleaned_data.get('location','').strip()
            edited.customer = cust
            edited.location = loc
            edited.project_name = f"{cust} — {loc}" if loc else cust or 'New Project'
            if cust:
                profile_id = request.POST.get('customer_profile_id', '').strip()
                if profile_id:
                    edited.customer_profile = CustomerProfile.objects.filter(pk=profile_id).first()
                elif not edited.customer_profile_id or edited.customer_profile.name.lower() != cust.lower():
                    # The text was hand-edited without picking a suggestion, or
                    # there was no link yet — (re)resolve it to a real record.
                    profile = CustomerProfile.objects.filter(name__iexact=cust).first()
                    if not profile:
                        profile = CustomerProfile.objects.create(name=cust)
                    edited.customer_profile = profile
            # Reverting from Completed is restricted to staff/admins
            if old_status_val == 'completed' and edited.status != 'completed' and not request.user.is_staff:
                messages.error(request, 'Only an administrator can revert a project from Completed status.')
                return redirect('project_edit', pk=project.pk)
            # Once a project has left Enquiry, it can never go back — a price
            # has already been (or may be about to be) communicated to the
            # customer, so re-opening it as a fresh enquiry isn't allowed.
            if old_status_val != 'enquiry' and edited.status == 'enquiry':
                messages.error(request, "This project has already moved past Enquiry and can't be set back to it.")
                return redirect('project_edit', pk=project.pk)
            edited.save()
            if old_status_val != edited.status:
                _handle_quoted_status_reminders(edited, old_status_val, edited.status, request.user)
            after = _snap(edited)
            _log_changes(edited, before, after, request.user)
            if cust:
                Customer.objects.get_or_create(name=cust)
            messages.success(request, 'Project updated.')
            return redirect('project_edit', pk=edited.pk)
    else:
        form = ProjectForm(instance=project)
    logs = project.logs.select_related('user').order_by('-timestamp')[:30]
    from ..models import Comment
    comments = project.comments.select_related('user').order_by('timestamp')
    staff_users = User.objects.filter(is_active=True).order_by('first_name', 'last_name')
    from ..countries import COUNTRIES
    linked_pos = project.purchase_orders.select_related('supplier').order_by('-order_date', '-id')
    manual_pos = project.manual_pos.all()
    completed_log = project.logs.filter(field='Status', new_value='Completed').order_by('timestamp').first()
    return render(request, 'projects/project_form.html', {
        'form': form, 'action': 'Edit', 'project': project, 'logs': logs,
        'comments': comments, 'staff_users': staff_users, 'countries': COUNTRIES,
        'linked_pos': linked_pos, 'manual_pos': manual_pos, 'completed_log': completed_log,
        'project_pk': project.pk,
    })




@login_required
def project_delete(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if request.method == 'POST':
        project.delete()
        messages.success(request, 'Project deleted.')
        return redirect('dashboard')
    return render(request, 'projects/project_confirm_delete.html', {'project': project})




@login_required
def project_data(request, pk):
    p = get_object_or_404(Project, pk=pk)
    logs = list(p.logs.select_related('user').order_by('-timestamp')[:30].values(
        'field','old_value','new_value','timestamp','user__first_name','user__last_name','user__username'
    ))
    for l in logs:
        fn = l.pop('user__first_name','') or ''
        ln = l.pop('user__last_name','') or ''
        un = l.pop('user__username','') or ''
        l['user'] = f"{fn} {ln}".strip() or un
        l['timestamp'] = timezone.localtime(l['timestamp']).strftime('%d %b %Y, %H:%M')

    comments = list(p.comments.select_related('user').order_by('timestamp').values(
        'id','text','timestamp','user__first_name','user__last_name','user__username'
    ))
    for c in comments:
        fn = c.pop('user__first_name','') or ''
        ln = c.pop('user__last_name','') or ''
        un = c.pop('user__username','') or ''
        c['user'] = f"{fn} {ln}".strip() or un
        c['timestamp'] = timezone.localtime(c['timestamp']).strftime('%d %b %Y, %H:%M')

    return JsonResponse({
        'id': p.pk,
        'project_name': p.project_name,
        'customer': p.customer,
        'location': p.location,
        'description': p.description,
        'status': p.status,
        'sales_order': p.sales_order,
        'drawing_number': p.drawing_number,
        'assigned_to': p.assigned_to.get_full_name() if p.assigned_to else '',
        'assigned_to_id': p.assigned_to_id or '',
        'delivery_required': p.delivery_required,
        'delivery_date': p.delivery_date.isoformat() if p.delivery_date else '',
        'delivery_booked': p.delivery_booked,
        'installation_required': p.installation_required,
        'installation_same_as_delivery': p.installation_same_as_delivery,
        'installation_date_type': p.installation_date_type,
        'installation_date': p.installation_date.isoformat() if p.installation_date else '',
        'installation_month': p.installation_month,
        'installation_month_part': p.installation_month_part,
        'installation_booked': p.installation_booked,
        'rams_required': p.rams_required,
        'rams_sent': p.rams_sent,
        'notes': p.notes,
        'created_by': p.created_by.get_full_name() if p.created_by else '',
        'created_at': timezone.localtime(p.created_at).strftime('%d %b %Y, %H:%M'),
        'last_edited_by': p.last_edited_by.get_full_name() if p.last_edited_by else '',
        'updated_at': timezone.localtime(p.updated_at).strftime('%d %b %Y, %H:%M'),
        'logs': logs,
        'comments': comments,
        'status_choices': Project.STATUS_CHOICES,
        'is_overdue': p.is_overdue(),
    })




@login_required
@require_POST
def project_manual_po_add(request, pk):
    project = get_object_or_404(Project, pk=pk)
    data = json.loads(request.body)
    next_order = project.manual_pos.count()
    mpo = ProjectManualPO.objects.create(
        project=project,
        supplier=(data.get('supplier') or '').strip(),
        po_number=(data.get('po_number') or '').strip(),
        date_ordered=data.get('date_ordered') or None,
        date_delivery=data.get('date_delivery') or None,
        sort_order=next_order,
    )
    return JsonResponse({'ok': True, 'id': mpo.pk})


@login_required
@require_POST
def project_manual_po_update(request, pk):
    mpo = get_object_or_404(ProjectManualPO, pk=pk)
    data = json.loads(request.body)
    if 'supplier' in data:
        mpo.supplier = (data.get('supplier') or '').strip()
    if 'po_number' in data:
        mpo.po_number = (data.get('po_number') or '').strip()
    if 'date_ordered' in data:
        mpo.date_ordered = data.get('date_ordered') or None
    if 'date_delivery' in data:
        mpo.date_delivery = data.get('date_delivery') or None
    mpo.save()
    return JsonResponse({'ok': True})


@login_required
@require_POST
def project_manual_po_delete(request, pk):
    ProjectManualPO.objects.filter(pk=pk).delete()
    return JsonResponse({'ok': True})


@login_required
@require_POST
def project_toggle_blocked(request, pk):
    project = get_object_or_404(Project, pk=pk)
    data = json.loads(request.body)
    blocked = bool(data.get('is_blocked'))
    project.is_blocked = blocked
    if blocked:
        project.blocked_reason = (data.get('blocked_reason') or '').strip()
        project.blocked_at = timezone.now()
    else:
        project.blocked_reason = ''
        project.blocked_at = None
    project.save(update_fields=['is_blocked', 'blocked_reason', 'blocked_at'])
    return JsonResponse({'ok': True, 'is_blocked': project.is_blocked, 'blocked_reason': project.blocked_reason})


@login_required
@require_POST
def project_quick_status(request, pk):
    """AJAX: change status directly from dashboard."""
    p = get_object_or_404(Project, pk=pk)
    data = json.loads(request.body)
    new_status = data.get('status','')
    valid = [v for v,_ in Project.STATUS_CHOICES]
    if new_status not in valid:
        return JsonResponse({'error': 'Invalid status'}, status=400)
    # Reverting from Completed is restricted to staff/admins
    if p.status == 'completed' and new_status != 'completed' and not request.user.is_staff:
        return JsonResponse({'error': 'Only an administrator can revert a project from Completed status.'}, status=403)
    # Once a project has left Enquiry, it can never go back
    if p.status != 'enquiry' and new_status == 'enquiry':
        return JsonResponse({'error': "This project has already moved past Enquiry and can't be set back to it."}, status=400)
    old_status = p.get_status_display()
    old_status_val = p.status
    p.status = new_status
    p.last_edited_by = request.user
    if new_status == 'cancelled':
        reason = data.get('lost_reason', '').strip()
        if reason:
            p.lost_reason = reason
    # Optionally save payment method at the same time (sent from order_received modal)
    payment = data.get('payment_method', '').strip()
    valid_payments = [v for v,_ in Project.PAYMENT_CHOICES]
    if payment and payment in valid_payments:
        p.payment_method = payment
    p.save()
    _handle_quoted_status_reminders(p, old_status_val, new_status, request.user)
    log_field = 'Status (reverted from Completed)' if old_status_val == 'completed' else 'Status'
    ProjectLog.objects.create(
        project=p, user=request.user,
        field=log_field, old_value=old_status, new_value=p.get_status_display()
    )
    payment_label = dict(Project.PAYMENT_CHOICES).get(p.payment_method, '')
    return JsonResponse({'ok': True, 'new_status': new_status, 'new_label': p.get_status_display(),
                         'payment_method': p.payment_method, 'payment_label': payment_label})


@login_required
@require_POST
def project_payment_method(request, pk):
    """AJAX: update payment method only."""
    p = get_object_or_404(Project, pk=pk)
    data = json.loads(request.body)
    payment = data.get('payment_method', '').strip()
    valid = [v for v,_ in Project.PAYMENT_CHOICES]
    if payment not in valid and payment != '':
        return JsonResponse({'error': 'Invalid payment method'}, status=400)
    p.payment_method = payment
    p.save(update_fields=['payment_method'])
    label = dict(Project.PAYMENT_CHOICES).get(payment, '')
    return JsonResponse({'ok': True, 'payment_method': payment, 'payment_label': label})




@login_required
@require_POST
def project_add_comment(request, pk):
    p = get_object_or_404(Project, pk=pk)
    data = json.loads(request.body)
    text = data.get('text','').strip()
    if not text:
        return JsonResponse({'error': 'Empty comment'}, status=400)
    c = Comment.objects.create(project=p, user=request.user, text=text)
    name = request.user.get_full_name() or request.user.username
    return JsonResponse({
        'id': c.id, 'text': c.text, 'user': name,
        'timestamp': timezone.localtime(c.timestamp).strftime('%d %b %Y, %H:%M'),
    })


# ── Logs, activity, search ────────────────────────────────────────────────────



@login_required
@require_POST
def project_presence(request, pk):
    """Heartbeat endpoint — upserts the user's presence and returns who else
    is currently active in this project (seen within the last 90 seconds)."""
    project = get_object_or_404(Project, pk=pk)
    # Upsert this user's presence record (auto_now on last_seen handles timestamp)
    ProjectPresence.objects.update_or_create(
        project=project, user=request.user,
        defaults={},  # auto_now=True on last_seen, so no need to set it explicitly
    )
    # Re-save to force auto_now update (update_or_create with defaults={} doesn't trigger auto_now on update)
    presence, _ = ProjectPresence.objects.get_or_create(project=project, user=request.user)
    ProjectPresence.objects.filter(pk=presence.pk).update(last_seen=timezone.now())

    # Return who else is active (seen within 90 seconds, excluding self)
    cutoff = timezone.now() - timedelta(seconds=90)
    others = ProjectPresence.objects.filter(
        project=project, last_seen__gte=cutoff
    ).exclude(user=request.user).select_related('user')

    return JsonResponse({
        'others': [
            {'name': p.user.get_full_name() or p.user.username}
            for p in others
        ]
    })




@login_required
@require_POST
def project_duplicate(request, pk):
    original = get_object_or_404(Project, pk=pk)
    new = Project.objects.create(
        project_name    = f"{original.project_name} (Option)",
        project_number  = _next_project_number(),
        customer        = original.customer,
        location        = original.location,
        drawing_number  = original.drawing_number,
        sales_order     = f"COPY-{original.sales_order}" if original.sales_order else "",
        status          = 'received',
        description     = original.description,
        notes           = original.notes,
        assigned_to     = original.assigned_to,
        addr_line1      = original.addr_line1,
        addr_line2      = original.addr_line2,
        addr_city       = original.addr_city,
        addr_county     = original.addr_county,
        addr_postcode   = original.addr_postcode,
        addr_country    = original.addr_country,
        addr_fao        = original.addr_fao,
        addr_phone      = original.addr_phone,
        delivery_required   = original.delivery_required,
        installation_required = original.installation_required,
        rams_required   = original.rams_required,
    )
    # Duplicate costing if it exists
    if hasattr(original, 'cost'):
        orig_cost = original.cost
        new_cost = ProjectCost.objects.create(
            project  = new,
            markup   = orig_cost.markup,
            labour   = orig_cost.labour,
            delivery = orig_cost.delivery,
            notes    = orig_cost.notes,
        )
        for acc in orig_cost.accessories.all():
            new_cost.accessories.add(acc)
        for line in orig_cost.lines.all():
            ProjectCostLine.objects.create(
                cost        = new_cost,
                line_type   = line.line_type,
                product_line= line.product_line,
                description = line.description,
                size        = line.size,
                melamine    = line.melamine,
                product     = line.product,
                quantity    = line.quantity,
                unit_cost   = line.unit_cost,
                sort_order  = line.sort_order,
            )
    return JsonResponse({'ok': True, 'new_pk': new.pk})




@login_required
@require_POST
def bulk_status_update(request):
    data = json.loads(request.body)
    ids    = data.get('ids', [])
    status = data.get('status', '')
    valid  = [s[0] for s in Project.STATUS_CHOICES]
    if not ids or status not in valid:
        return JsonResponse({'ok': False, 'error': 'Invalid'}, status=400)
    updated = Project.objects.filter(pk__in=ids).update(status=status)
    return JsonResponse({'ok': True, 'updated': updated})




@login_required
def save_dashboard_filters(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        request.session['dash_filters'] = data
        return JsonResponse({'ok': True})
    return JsonResponse({'filters': request.session.get('dash_filters', {})})




@login_required
def dashboard(request):
    query           = request.GET.get('q', '')
    status          = request.GET.get('status', '')
    tl              = request.GET.get('tl', '')
    sort            = request.GET.get('sort', '-project_number')
    stat_filter     = request.GET.get('sf', '')
    assigned_filter = request.GET.get('assigned', '')

    projects = Project.objects.select_related('assigned_to', 'last_edited_by')

    if query:
        projects = projects.filter(
            Q(project_name__icontains=query) |
            Q(customer__icontains=query) |
            Q(location__icontains=query) |
            Q(sales_order__icontains=query) |
            Q(project_number__icontains=query)
        )
    if status:
        projects = projects.filter(status=status)

    if stat_filter == 'sfp':
        projects = projects.filter(status='processed')
    elif stat_filter == 'del_to_book':
        projects = projects.filter(
            delivery_required=True,
            delivery_booked=False,
            delivery_date__isnull=True,
        ).exclude(status__in=['completed', 'on_hold', 'cancelled'])
    elif stat_filter == 'inst_to_book':
        projects = projects.filter(
            installation_required=True,
            installation_booked=False,
            installation_date__isnull=True,
            installation_month='',
            installation_same_as_delivery=False,
        ).exclude(status__in=['completed', 'on_hold', 'cancelled'])
    elif stat_filter == 'rams_to_send':
        projects = projects.filter(rams_required=True, rams_sent=False)
    elif stat_filter == 'completed':
        projects = projects.filter(status='completed')
    elif stat_filter == 'overdue':
        all_p = list(projects)
        projects = [p for p in all_p if p.is_overdue()]

    if assigned_filter == 'me':
        projects = projects.filter(assigned_to=request.user)
    elif assigned_filter:
        try:
            projects = projects.filter(assigned_to_id=int(assigned_filter))
        except (ValueError, TypeError):
            pass

    if tl:
        all_p = list(projects) if not isinstance(projects, list) else projects
        projects = [p for p in all_p if p.get_traffic_light() == tl]

    allowed = ['sales_order','-sales_order','customer','-customer','status','-status',
               'delivery_date','-delivery_date','installation_date','-installation_date',
               'project_number','-project_number','-created_at','created_at']
    if sort not in allowed:
        sort = '-project_number'

    if not isinstance(projects, list):
        if sort in ('installation_date', '-installation_date'):
            # Sort: exact dates first, then month-based, then none
            reverse = sort.startswith('-')
            proj_list = list(projects)
            def inst_sort_key(p):
                if p.installation_date:
                    return (0, p.installation_date)
                if p.installation_same_as_delivery and p.delivery_date:
                    return (0, p.delivery_date)
                return (1, None)
            projects = sorted(proj_list, key=inst_sort_key, reverse=reverse)
        elif sort in ('sales_order', '-sales_order'):
            reverse = sort.startswith('-')
            proj_list = list(projects)
            def so_sort_key(p):
                if p.sales_order:
                    try:
                        return (0, -int(p.sales_order) if reverse else int(p.sales_order), '')
                    except ValueError:
                        return (1, 0, p.sales_order)
                return (2, 0, p.customer or '')
            projects = sorted(proj_list, key=lambda p: so_sort_key(p))
            if reverse:
                # Already handled by negating the int above
                pass
        else:
            projects = list(projects.order_by(sort))

    # Paginate — without this, a page with 500+ projects spends most of its
    # time rendering rows nobody can see, not running queries.
    from django.core.paginator import Paginator
    if not isinstance(projects, list):
        total_matching = projects.count()
    else:
        total_matching = len(projects)
    paginator = Paginator(projects, 100)
    page_num = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page_num)
    except Exception:
        page_obj = paginator.page(1)
    projects = page_obj.object_list

    counts = {
        'total':        Project.objects.count(),
        'sfp':          Project.objects.filter(status='processed').count(),
        'del_to_book':  Project.objects.filter(
            delivery_required=True, delivery_booked=False, delivery_date__isnull=True,
        ).exclude(status__in=['completed','on_hold','cancelled']).count(),
        'inst_to_book': Project.objects.filter(
            installation_required=True, installation_booked=False,
            installation_date__isnull=True, installation_month='',
            installation_same_as_delivery=False,
        ).exclude(status__in=['completed','on_hold','cancelled']).count(),
        'rams_to_send': Project.objects.filter(rams_required=True, rams_sent=False).count(),
        'completed':    Project.objects.filter(status='completed').count(),
        'overdue':      sum(1 for p in Project.objects.exclude(status__in=['completed','on_hold','cancelled']) if p.is_overdue()),
    }

    all_users = User.objects.filter(is_active=True).order_by('first_name','last_name')
    return render(request, 'projects/dashboard.html', {
        'projects': projects, 'status_choices': Project.STATUS_CHOICES,
        'selected_status': status, 'query': query, 'sort': sort,
        'tl': tl, 'stat_filter': stat_filter, 'counts': counts,
        'assigned_filter': assigned_filter, 'all_users': all_users,
        'page_obj': page_obj, 'total_matching': total_matching,
    })


# ── Project CRUD ──────────────────────────────────────────────────────────────

TRACKED = [
    ('customer','Customer'),('location','Location'),('status','Status'),
    ('sales_order','Sales Order'),('drawing_number','Drawing Number'),
    ('description','Description'),
    ('delivery_required','Delivery Required'),('delivery_date','Delivery Date'),
    ('delivery_booked','Delivery Booked'),
    ('installation_required','Installation Required'),
    ('installation_same_as_delivery','Same as Delivery'),
    ('installation_date_type','Install Date Type'),
    ('installation_date','Installation Date'),
    ('installation_month','Installation Month'),
    ('installation_month_part','Install Month Part'),
    ('installation_booked','Installation Booked'),
    ('rams_required','RAMS Required'),('rams_sent','RAMS Sent'),
    ('assigned_to','Assigned To'),('notes','Notes'),
]



@login_required
@require_POST
def add_comment_with_tags(request, pk):
    """Overrides project_add_comment — handles @mentions."""
    import re
    p = get_object_or_404(Project, pk=pk)
    data = json.loads(request.body)
    text = data.get('text', '').strip()
    if not text:
        return JsonResponse({'error': 'Empty comment'}, status=400)

    # Store readable text — replace @[Name](id) → @Name
    import re as _re2
    clean_text = _re2.sub(r'@\[([^\]]+)\]\(\d+\)', r'@\1', text)
    clean_text = _re2.sub(r'#\[([^\]]+)\]\((\d+)\)', r'#\1', clean_text)
    c = Comment.objects.create(project=p, user=request.user, text=clean_text)
    sender_name = request.user.get_full_name() or request.user.username

    # Parse @mentions from original raw text
    mentions = re.findall(r'@\[([^\]]+)\]\((\d+)\)', text)
    for name, uid in mentions:
        try:
            tagged_user = User.objects.get(pk=int(uid))
            if tagged_user != request.user:
                Notification.objects.create(
                    user=tagged_user, type='tag',
                    text=f"{sender_name} tagged you in {p.project_name}: {text[:80]}",
                    link=f"/project/{p.pk}/edit/",
                )
        except User.DoesNotExist:
            pass

    # Format display text — replace @[Name](id) with @Name
    display_text = re.sub(r'@\[([^\]]+)\]\(\d+\)', r'@\1', text)

    return JsonResponse({
        'id': c.id, 'text': display_text, 'raw_text': text,
        'user': sender_name,
        'timestamp': timezone.localtime(c.timestamp).strftime('%d %b %Y, %H:%M'),
    })




@login_required
def projects_search(request):
    q = request.GET.get('q', '').strip()
    if len(q) < 1:
        return JsonResponse([], safe=False)
    results = list(Project.objects.filter(
        Q(project_name__icontains=q) | Q(customer__icontains=q)
    ).values('id', 'project_name', 'customer')[:8])
    return JsonResponse(results, safe=False)


# ── Staff Directory ───────────────────────────────────────────────────────────



@login_required
@require_POST
def project_address_save(request, pk):
    project = get_object_or_404(Project, pk=pk)
    data = json.loads(request.body)
    project.addr_line1   = data.get('addr_line1', '').strip()
    project.addr_line2   = data.get('addr_line2', '').strip()
    project.addr_city    = data.get('addr_city', '').strip()
    project.addr_county  = data.get('addr_county', '').strip()
    project.addr_postcode= data.get('addr_postcode', '').strip()
    project.addr_country = data.get('addr_country', '').strip() or 'United Kingdom'
    project.addr_fao     = data.get('addr_fao', '').strip()
    project.addr_phone   = data.get('addr_phone', '').strip()
    project.save()
    has_address = bool(project.addr_line1 or project.addr_postcode)
    return JsonResponse({'ok': True, 'has_address': has_address})


