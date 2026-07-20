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

from .models import Project, ProjectLog, Customer, Comment, Message, Notification, TeamMessage, StaffProfile, LeaveRequest, InstallationReport, ReportPhoto, SatisfactionNote, CustomerProfile, ProjectDocument, Product, PickingList, PickingListItem, PickingTemplate, PickingTemplateItem, MaterialPrice, ProjectCost, ProjectCostLine, UprightAccessory, AccessoryOverride, Reminder, FittingCrew, Supplier, PurchaseOrder, PurchaseOrderLine, StockMovement, FittingNote, ProjectQuote, PriceListItem, QuotePhoto, QuoteAttachedPhoto, ProformaInvoice, DeliveryPhase, ProjectPresence
from .forms import RegisterForm, ProjectForm


# ── Auth ──────────────────────────────────────────────────────────────────────

from django.contrib.auth.views import (
    PasswordResetView, PasswordResetDoneView,
    PasswordResetConfirmView, PasswordResetCompleteView,
)

password_reset_request = PasswordResetView.as_view(
    template_name='projects/password_reset.html',
    email_template_name='projects/password_reset_email.txt',
    subject_template_name='projects/password_reset_subject.txt',
    success_url='/password-reset/done/',
)
password_reset_done = PasswordResetDoneView.as_view(
    template_name='projects/password_reset_done.html',
)
password_reset_confirm = PasswordResetConfirmView.as_view(
    template_name='projects/password_reset_confirm.html',
    success_url='/password-reset/complete/',
)
password_reset_complete = PasswordResetCompleteView.as_view(
    template_name='projects/password_reset_complete.html',
)


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    error = None
    if request.method == 'POST':
        identifier = request.POST.get('identifier', '').strip()
        password   = request.POST.get('password', '')
        user = None
        try:
            u = User.objects.get(email=identifier)
            user = authenticate(request, username=u.username, password=password)
        except User.DoesNotExist:
            for u in User.objects.all():
                if u.get_full_name().lower() == identifier.lower():
                    user = authenticate(request, username=u.username, password=password)
                    break
        if user:
            login(request, user)
            return redirect('dashboard')
        else:
            # Check if account exists but is inactive (pending approval)
            try:
                u = User.objects.get(email=identifier)
                if not u.is_active:
                    error = 'Your account is pending approval. Please contact your manager.'
                else:
                    error = 'Invalid name/email or password.'
            except User.DoesNotExist:
                error = 'Invalid name/email or password.'
    return render(request, 'projects/login.html', {'error': error})


def register_view(request):
    form = RegisterForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        return render(request, 'projects/register_pending.html', {'user': user})
    return render(request, 'projects/register.html', {'form': form})


def logout_view(request):
    logout(request)
    return redirect('login')


# ── Dashboard ─────────────────────────────────────────────────────────────────

@login_required
def home(request):
    from datetime import timedelta
    now = timezone.now()
    today = now.date()
    week_end = today + timedelta(days=7)

    my_reminders = Reminder.objects.filter(
        notify_user=request.user, dismissed=False
    ).select_related('project').order_by('remind_at')[:10]

    upcoming_reminders = Reminder.objects.filter(
        notify_user=request.user, sent=False, remind_at__gt=now, dismissed=False
    ).select_related('project').order_by('remind_at')[:5]

    leave_week = LeaveRequest.objects.filter(
        date__gte=today, date__lte=week_end
    ).select_related('user').order_by('date')

    recent_logs = ProjectLog.objects.select_related('project', 'user').order_by('-timestamp')[:8]

    my_projects = Project.objects.filter(
        assigned_to=request.user
    ).exclude(status__in=['completed', 'cancelled']).order_by('-project_number')[:8]

    latest_updates = Project.objects.order_by('-updated_at')[:10]

    unread_messages = Message.objects.filter(recipient=request.user, read=False).count()

    # Random sample of install photos for the homepage slideshow
    photo_ids = list(ReportPhoto.objects.values_list('pk', flat=True))
    random.shuffle(photo_ids)
    slideshow_photos = list(
        ReportPhoto.objects.filter(pk__in=photo_ids[:12]).select_related('report__project')
    )
    random.shuffle(slideshow_photos)

    recent_team_messages = TeamMessage.objects.select_related('user').order_by('-timestamp')[:15]
    recent_team_messages = list(reversed(list(recent_team_messages)))
    last_team_msg_id = recent_team_messages[-1].pk if recent_team_messages else 0

    return render(request, 'projects/home.html', {
        'my_reminders': my_reminders,
        'upcoming_reminders': upcoming_reminders,
        'leave_week': leave_week,
        'slideshow_photos': slideshow_photos,
        'recent_logs': recent_logs,
        'my_projects': my_projects,
        'latest_updates': latest_updates,
        'unread_messages': unread_messages,
        'recent_team_messages': recent_team_messages,
        'last_team_msg_id': last_team_msg_id,
    })


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
        projects = projects.filter(delivery_required=True, delivery_date__isnull=False, delivery_booked=False)
    elif stat_filter == 'inst_to_book':
        projects = projects.filter(installation_required=True, installation_booked=False).filter(
            Q(installation_date__isnull=False) |
            Q(installation_month__gt='') |
            Q(installation_same_as_delivery=True, delivery_date__isnull=False)
        )
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
        'del_to_book':  Project.objects.filter(delivery_required=True, delivery_date__isnull=False, delivery_booked=False).count(),
        'inst_to_book': Project.objects.filter(installation_required=True, installation_booked=False).filter(
            Q(installation_date__isnull=False) | Q(installation_month__gt='') |
            Q(installation_same_as_delivery=True, delivery_date__isnull=False)
        ).count(),
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

def _snap(p):
    s = {}
    for field, _ in TRACKED:
        v = getattr(p, field)
        if hasattr(v, 'get_full_name'):
            v = v.get_full_name() or str(v)
        elif isinstance(v, bool):
            v = 'Yes' if v else 'No'
        elif v is None:
            v = ''
        s[field] = str(v)
    return s

def _log_changes(p, before, after, user):
    for field, label in TRACKED:
        if before.get(field,'') != after.get(field,''):
            old = before.get(field,'') or '—'
            new = after.get(field,'') or '—'
            ProjectLog.objects.create(project=p, user=user, field=label, old_value=old, new_value=new)


def _add_working_days(start_dt, n):
    """Add n working days (Mon-Fri) to a datetime, skipping weekends."""
    from datetime import timedelta
    d = start_dt
    added = 0
    while added < n:
        d += timedelta(days=1)
        if d.weekday() < 5:  # Mon-Fri
            added += 1
    return d


def _handle_quoted_status_reminders(project, old_status, new_status, user):
    """When a project's status changes to/from 'quoted', manage the automatic
    7- and 14-working-day follow-up reminders for the assignee."""
    from django.utils import timezone
    if new_status == 'quoted' and old_status != 'quoted':
        if not project.assigned_to:
            return
        now = timezone.now()
        for days in (7, 14):
            Reminder.objects.create(
                project=project, notify_user=project.assigned_to, created_by=user,
                message=f'Follow up with {project.customer} — quote sent {days} working days ago ({project.project_name})',
                remind_at=_add_working_days(now, days),
            )
    elif old_status == 'quoted' and new_status != 'quoted':
        # Status moved on (e.g. to Order Received, or back to Enquiry) —
        # cancel any pending follow-up reminders that haven't fired yet.
        project.reminders.filter(sent=False, message__icontains='Follow up with').delete()


def _log_po_event(po, user, field, old_value='', new_value=''):
    """Log a PO action to the Activity Log, when the PO has a linked project.
    POs without a project have nowhere to attach a ProjectLog entry."""
    if po.project_id:
        ProjectLog.objects.create(
            project=po.project, user=user, field=field,
            old_value=old_value, new_value=new_value,
        )


def _calc_sell_price(cost):
    """Read-only sell-price calculation from already-saved cost lines —
    used for exports/reports where we must not mutate/recalculate lines
    (that recalculation only happens on the live costing page itself)."""
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
    """First letter of first name + first letter of last name, matching the
    convention used everywhere else in the app (avatars, chat, dashboard)."""
    parts = name.split()
    if not parts:
        return ''
    if len(parts) == 1:
        return parts[0][:1].upper()
    return (parts[0][:1] + parts[-1][:1]).upper()


def _next_project_number():
    from django.db import transaction
    with transaction.atomic():
        last = Project.objects.select_for_update().order_by('-project_number').filter(project_number__isnull=False).first()
        return (last.project_number + 1) if last else 1


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
            p.save()
            if cust:
                Customer.objects.get_or_create(name=cust)
            ProjectLog.objects.create(project=p, user=request.user, field='Project created', old_value='', new_value=p.project_name)
            messages.success(request, 'Project created.')
            return redirect('dashboard')
    else:
        form = ProjectForm()
    return render(request, 'projects/project_form.html', {'form': form, 'action': 'Create'})


@login_required
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
        # Auto-create customer profile
        if customer:
            if not CustomerProfile.objects.filter(name__iexact=customer).exists():
                CustomerProfile.objects.create(name=customer)
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
            # Reverting from Completed is restricted to staff/admins
            if old_status_val == 'completed' and edited.status != 'completed' and not request.user.is_staff:
                messages.error(request, 'Only an administrator can revert a project from Completed status.')
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
    from .models import Comment
    comments = project.comments.select_related('user').order_by('timestamp')
    staff_users = User.objects.filter(is_active=True).order_by('first_name', 'last_name')
    from .countries import COUNTRIES
    linked_pos = project.purchase_orders.select_related('supplier').order_by('-order_date', '-id')
    completed_log = project.logs.filter(field='Status', new_value='Completed').order_by('timestamp').first()
    return render(request, 'projects/project_form.html', {
        'form': form, 'action': 'Edit', 'project': project, 'logs': logs,
        'comments': comments, 'staff_users': staff_users, 'countries': COUNTRIES,
        'linked_pos': linked_pos, 'completed_log': completed_log,
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
        l['timestamp'] = l['timestamp'].strftime('%d %b %Y, %H:%M')

    comments = list(p.comments.select_related('user').order_by('timestamp').values(
        'id','text','timestamp','user__first_name','user__last_name','user__username'
    ))
    for c in comments:
        fn = c.pop('user__first_name','') or ''
        ln = c.pop('user__last_name','') or ''
        un = c.pop('user__username','') or ''
        c['user'] = f"{fn} {ln}".strip() or un
        c['timestamp'] = c['timestamp'].strftime('%d %b %Y, %H:%M')

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
        'created_at': p.created_at.strftime('%d %b %Y, %H:%M'),
        'last_edited_by': p.last_edited_by.get_full_name() if p.last_edited_by else '',
        'updated_at': p.updated_at.strftime('%d %b %Y, %H:%M'),
        'logs': logs,
        'comments': comments,
        'status_choices': Project.STATUS_CHOICES,
        'is_overdue': p.is_overdue(),
    })


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
    old_status = p.get_status_display()
    old_status_val = p.status
    p.status = new_status
    p.last_edited_by = request.user
    p.save()
    _handle_quoted_status_reminders(p, old_status_val, new_status, request.user)
    log_field = 'Status (reverted from Completed)' if old_status_val == 'completed' else 'Status'
    ProjectLog.objects.create(
        project=p, user=request.user,
        field=log_field, old_value=old_status, new_value=p.get_status_display()
    )
    return JsonResponse({'ok': True, 'new_status': new_status, 'new_label': p.get_status_display()})


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
        'timestamp': c.timestamp.strftime('%d %b %Y, %H:%M'),
    })


# ── Logs, activity, search ────────────────────────────────────────────────────

@login_required
def daily_accounts_report(request):
    """Printable end-of-day report for the accountant: projects that became
    Completed today (ready to invoice) and POs received today (ready to pay).
    Also supports a monthly history view."""
    mode = request.GET.get('mode', 'daily')

    # ── Monthly history mode ──────────────────────────────────────────────────
    if mode == 'history':
        try:
            year  = int(request.GET.get('year',  date.today().year))
            month = int(request.GET.get('month', date.today().month))
        except (ValueError, TypeError):
            year, month = date.today().year, date.today().month
        if not (1 <= month <= 12) or not (1 <= year <= 9999):
            year, month = date.today().year, date.today().month

        import calendar as cal_mod
        month_start = date(year, month, 1)
        month_end   = date(year, month, cal_mod.monthrange(year, month)[1])
        m_start = timezone.make_aware(timezone.datetime.combine(month_start, timezone.datetime.min.time()))
        m_end   = timezone.make_aware(timezone.datetime.combine(month_end,   timezone.datetime.max.time()))

        completed_logs = (ProjectLog.objects
            .filter(field='Status', new_value='Completed', timestamp__gte=m_start, timestamp__lte=m_end)
            .select_related('project', 'user')
            .order_by('timestamp'))

        seen = set()
        history_rows = []
        for log in completed_logs:
            if log.project_id in seen:
                continue
            seen.add(log.project_id)
            p = log.project
            cost = getattr(p, 'cost', None)
            sell_price = _calc_sell_price(cost) if cost else None
            customer_profile = CustomerProfile.objects.filter(name=p.customer).first()
            history_rows.append({
                'project': p,
                'sell_price': sell_price,
                'net': round(sell_price / 1.2, 2) if sell_price else None,
                'vat': round(sell_price - sell_price / 1.2, 2) if sell_price else None,
                'completed_at': log.timestamp,
                'completed_by': log.user.get_full_name() if log.user else '—',
                'company_code': customer_profile.account_number if customer_profile else '',
            })

        total = sum(r['sell_price'] for r in history_rows if r['sell_price'])
        return render(request, 'projects/daily_accounts_report.html', {
            'mode': 'history',
            'history_rows': history_rows,
            'history_month': month_start,
            'history_year': year,
            'history_month_num': month,
            'history_total': total,
            'history_net': round(total / 1.2, 2) if total else 0,
            'history_vat': round(total - total / 1.2, 2) if total else 0,
        })

    # ── Daily report mode (default) ───────────────────────────────────────────
    report_date_str = request.GET.get('date', '')
    try:
        report_date = date.fromisoformat(report_date_str) if report_date_str else date.today()
    except ValueError:
        report_date = date.today()

    day_start = timezone.make_aware(timezone.datetime.combine(report_date, timezone.datetime.min.time()))
    day_end   = timezone.make_aware(timezone.datetime.combine(report_date, timezone.datetime.max.time()))

    completed_logs = (ProjectLog.objects
        .filter(field='Status', new_value='Completed', timestamp__gte=day_start, timestamp__lte=day_end)
        .select_related('project')
        .order_by('project_id', '-timestamp'))
    seen_projects = set()
    completed_projects = []
    for log in completed_logs:
        if log.project_id in seen_projects:
            continue
        seen_projects.add(log.project_id)
        p = log.project
        cost = getattr(p, 'cost', None)
        sell_price = _calc_sell_price(cost) if cost else None
        address_parts = [p.addr_line1, p.addr_line2, p.addr_city, p.addr_county, p.addr_postcode]
        address = ', '.join(a for a in address_parts if a)
        customer_profile = CustomerProfile.objects.filter(name=p.customer).first()
        completed_projects.append({
            'project': p,
            'sell_price': sell_price,
            'net': round(sell_price / 1.2, 2) if sell_price else None,
            'vat': round(sell_price - sell_price / 1.2, 2) if sell_price else None,
            'address': address,
            'completed_at': log.timestamp,
            'company_code': customer_profile.account_number if customer_profile else '',
        })
    completed_projects.sort(key=lambda x: x['project'].customer)

    pos_received = (PurchaseOrder.objects
        .filter(received=True, received_at__gte=day_start, received_at__lte=day_end)
        .select_related('supplier', 'project')
        .order_by('supplier__name'))

    total_to_invoice = sum(c['sell_price'] for c in completed_projects if c['sell_price'])
    return render(request, 'projects/daily_accounts_report.html', {
        'mode': 'daily',
        'report_date': report_date,
        'completed_projects': completed_projects,
        'pos_received': pos_received,
        'total_to_invoice': total_to_invoice,
        'total_net': round(total_to_invoice / 1.2, 2) if total_to_invoice else 0,
        'total_vat': round(total_to_invoice - total_to_invoice / 1.2, 2) if total_to_invoice else 0,
        'total_to_pay': sum(po.total for po in pos_received),
    })


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
def activity_log(request):
    logs = ProjectLog.objects.select_related('project','user').order_by('-timestamp')[:200]
    return render(request, 'projects/activity_log.html', {'logs': logs})


@login_required
def customer_autocomplete(request):
    q = request.GET.get('q','').strip()
    if len(q) < 1:
        return JsonResponse([], safe=False)
    results = Customer.objects.filter(name__icontains=q).values_list('name', flat=True)[:8]
    return JsonResponse(list(results), safe=False)


@login_required
def customer_history(request, name):
    projects = Project.objects.filter(customer__iexact=name).order_by('-created_at')
    return render(request, 'projects/customer_history.html', {
        'customer_name': name, 'projects': projects,
    })


@login_required
def week_view(request):
    today = date.today()
    end   = today + timedelta(days=14)
    # Gather deliveries and installs in next 14 days
    days_data = []
    for i in range(15):
        d = today + timedelta(days=i)
        deliveries = Project.objects.filter(delivery_required=True, delivery_date=d).exclude(status__in=['completed','cancelled'])
        installs   = Project.objects.filter(installation_required=True, installation_date=d).exclude(status__in=['completed','cancelled'])
        same_del   = Project.objects.filter(installation_required=True, installation_same_as_delivery=True, delivery_date=d).exclude(status__in=['completed','cancelled'])
        all_installs = list(installs) + list(same_del)
        if deliveries or all_installs:
            days_data.append({'date': d, 'deliveries': list(deliveries), 'installs': all_installs})
    return render(request, 'projects/week_view.html', {'days_data': days_data, 'today': today})


@login_required
def monthly_summary(request):
    from django.db.models.functions import TruncMonth
    try:
        year = int(request.GET.get('year', date.today().year))
    except (ValueError, TypeError):
        year = date.today().year
    if not (1 <= year <= 9999):
        year = date.today().year
    data = []
    for m in range(1, 13):
        start = date(year, m, 1)
        if m == 12:
            end = date(year+1, 1, 1)
        else:
            end = date(year, m+1, 1)
        delivered  = Project.objects.filter(
            delivery_required=True, delivery_date__gte=start, delivery_date__lt=end
        ).exclude(status='cancelled').count()
        installed  = Project.objects.filter(
            installation_required=True, installation_date__gte=start, installation_date__lt=end
        ).exclude(status='cancelled').count()
        installed += Project.objects.filter(
            installation_required=True, installation_same_as_delivery=True,
            delivery_date__gte=start, delivery_date__lt=end
        ).exclude(status='cancelled').count()
        completed  = Project.objects.filter(updated_at__date__gte=start, updated_at__date__lt=end, status='completed').count()
        data.append({'month': start.strftime('%b'), 'delivered': delivered, 'installed': installed, 'completed': completed})
    total_del  = sum(m['delivered']  for m in data)
    total_inst = sum(m['installed']  for m in data)
    total_comp = sum(m['completed'] for m in data)
    years = list(range(date.today().year - 2, date.today().year + 2))
    return render(request, 'projects/monthly_summary.html', {
        'data': data, 'year': year, 'years': years,
        'total_del': total_del, 'total_inst': total_inst, 'total_comp': total_comp,
    })


@login_required
def calendar_view(request):
    import json, calendar as cal_mod
    try:
        year = int(request.GET.get('year', date.today().year))
    except (ValueError, TypeError):
        year = date.today().year
    try:
        month = int(request.GET.get('month', date.today().month))
    except (ValueError, TypeError):
        month = date.today().month
    # Guard against out-of-range values (e.g. someone editing the URL) —
    # fall back to the current month rather than crashing the page.
    if not (1 <= month <= 12) or not (1 <= year <= 9999):
        today = date.today()
        year, month = today.year, today.month

    # Build calendar grid — always start on Monday
    first_day = date(year, month, 1)
    last_day  = date(year, month, cal_mod.monthrange(year, month)[1])

    # Pad to start on Monday
    start = first_day - timedelta(days=first_day.weekday())
    # Pad to end on Sunday, minimum 4 weeks
    end_raw = last_day + timedelta(days=(6 - last_day.weekday()))
    end = end_raw if (end_raw - start).days >= 27 else end_raw + timedelta(weeks=1)

    # Fetch all projects with dates in range
    projects = Project.objects.filter(
        Q(delivery_date__gte=start, delivery_date__lte=end) |
        Q(installation_date__gte=start, installation_date__lte=end) |
        Q(installation_same_as_delivery=True, delivery_date__gte=start, delivery_date__lte=end)
    ).exclude(status__in=['cancelled'])

    # Build event map
    event_map = {}
    for p in projects:
        del_date = p.delivery_date.isoformat() if (p.delivery_required and p.delivery_date) else None
        inst_date_obj = p.get_effective_installation_date()
        inst_date = inst_date_obj.isoformat() if (p.installation_required and inst_date_obj) else None

        # Collect the set of dates this project touches, with what happens on each
        dates = {}
        if del_date:
            dates.setdefault(del_date, {'delivery': False, 'install': False})['delivery'] = True
        if inst_date:
            dates.setdefault(inst_date, {'delivery': False, 'install': False})['install'] = True

        for k, flags in dates.items():
            if flags['delivery'] and flags['install']:
                badge, etype = 'D + I', 'both'
            elif flags['delivery']:
                badge, etype = 'D', 'delivery'
            else:
                badge, etype = 'I', 'install'
            event_map.setdefault(k, []).append({
                'pk': p.pk, 'name': p.project_name, 'customer': p.customer,
                'location': p.location, 'type': etype, 'badge': badge,
                'status_label': p.get_status_display(),
            })

    # Build day list for template
    today = date.today()
    calendar_days = []
    d = start
    while d <= end:
        key = d.isoformat()
        calendar_days.append({
            'date': d,
            'is_today': d == today,
            'in_range': d.month == month,
            'is_past': d < today,
            'events': event_map.get(key, []),
        })
        d += timedelta(days=1)

    # Prev/next month
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1

    return render(request, 'projects/calendar_view.html', {
        'calendar_days':     calendar_days,
        'calendar_data_json': json.dumps(event_map),
        'year': year, 'month': month,
        'month_name': first_day.strftime('%B %Y'),
        'prev_year': prev_year, 'prev_month': prev_month,
        'next_year': next_year, 'next_month': next_month,
        'status_choices': Project.STATUS_CHOICES,
        'today': date.today(),
    })


@login_required
def board_view(request):
    import calendar as cal_mod
    today = date.today()

    def get_month_projects(year, month):
        last_day    = cal_mod.monthrange(year, month)[1]
        month_start = date(year, month, 1)
        month_end   = date(year, month, last_day)
        cutoff      = today - timedelta(days=7)
        projects = []
        all_p = Project.objects.exclude(status__in=['cancelled']).order_by('installation_date', 'delivery_date')
        for p in all_p:
            inst = p.get_effective_installation_date()
            if not inst:
                continue
            if inst < month_start or inst > month_end:
                continue
            if inst < cutoff:
                continue
            projects.append({'project': p, 'inst_date': inst, 'is_today': inst == today, 'is_past': inst < today})
        return projects, date(year, month, 1).strftime('%B %Y')

    y, m = today.year, today.month
    m1_proj, m1_name = get_month_projects(y, m)

    m2 = m + 1 if m < 12 else 1
    y2 = y if m < 12 else y + 1
    m2_proj, m2_name = get_month_projects(y2, m2)

    m3 = m2 + 1 if m2 < 12 else 1
    y3 = y2 if m2 < 12 else y2 + 1
    m3_proj, m3_name = get_month_projects(y3, m3)

    return render(request, 'projects/board_view.html', {
        'm1_projects': m1_proj, 'm1_name': m1_name,
        'm2_projects': m2_proj, 'm2_name': m2_name,
        'm3_projects': m3_proj, 'm3_name': m3_name,
        'today': today,
    })


@login_required
def so_search(request):
    q = request.GET.get('q','').strip()
    results = []
    if q:
        from django.db.models import Q
        qs = Project.objects.filter(
            Q(sales_order__icontains=q) |
            Q(project_number__icontains=q) |
            Q(project_name__icontains=q) |
            Q(customer__icontains=q) |
            Q(location__icontains=q)
        ).order_by('-project_number')[:12]
        results = list(qs.values('id','project_name','customer','sales_order','project_number','status'))
    return JsonResponse(results, safe=False)


# ── Notifications ─────────────────────────────────────────────────────────────

@login_required
def notification_count(request):
    notif_count = Notification.objects.filter(user=request.user, read=False).count()
    msg_count   = Message.objects.filter(recipient=request.user, read=False).count()
    return JsonResponse({'count': notif_count, 'messages': msg_count})


@login_required
def notifications_view(request):
    notes = Notification.objects.filter(user=request.user).order_by('-timestamp')[:50]
    Notification.objects.filter(user=request.user, read=False).update(read=True)
    return render(request, 'projects/notifications.html', {'notifications': notes})


# ── Messaging ─────────────────────────────────────────────────────────────────

@login_required
def inbox(request):
    from django.db.models import Max, Q
    # Get all users this person has exchanged messages with
    users_messaged = User.objects.filter(
        Q(sent_messages__recipient=request.user) |
        Q(received_messages__sender=request.user)
    ).distinct().exclude(id=request.user.id)

    conversations = []
    for u in users_messaged:
        last_msg = Message.objects.filter(
            Q(sender=request.user, recipient=u) |
            Q(sender=u, recipient=request.user)
        ).order_by('-timestamp').first()
        unread = Message.objects.filter(sender=u, recipient=request.user, read=False).count()
        conversations.append({'user': u, 'last_msg': last_msg, 'unread': unread})

    conversations.sort(key=lambda x: x['last_msg'].timestamp if x['last_msg'] else date.today(), reverse=True)
    all_users = User.objects.filter(is_active=True).exclude(id=request.user.id).order_by('first_name', 'last_name')
    total_unread = Message.objects.filter(recipient=request.user, read=False).count()

    return render(request, 'projects/inbox.html', {
        'conversations': conversations,
        'all_users': all_users,
        'total_unread': total_unread,
    })


@login_required
def conversation_poll(request, user_id):
    """Return messages newer than `after` for live-updating a 1:1 conversation."""
    other = get_object_or_404(User, pk=user_id)
    after = request.GET.get('after', 0)
    try:
        after = int(after)
    except (ValueError, TypeError):
        after = 0
    new_msgs = Message.objects.filter(
        Q(sender=request.user, recipient=other) | Q(sender=other, recipient=request.user),
        pk__gt=after,
    ).order_by('timestamp')
    # Mark any newly-arrived messages from the other person as read, since
    # the person is actively viewing this conversation right now.
    Message.objects.filter(sender=other, recipient=request.user, read=False, pk__gt=after).update(read=True)
    return JsonResponse({
        'messages': [
            {
                'id': m.pk, 'text': m.text,
                'sender': m.sender.get_full_name() or m.sender.username,
                'timestamp': m.timestamp.strftime('%d %b %Y, %H:%M'),
                'is_me': m.sender_id == request.user.id,
            }
            for m in new_msgs
        ]
    })


@login_required
def conversation(request, user_id):
    other = get_object_or_404(User, pk=user_id)
    messages_qs = Message.objects.filter(
        Q(sender=request.user, recipient=other) |
        Q(sender=other, recipient=request.user)
    ).order_by('timestamp')
    # Mark as read
    Message.objects.filter(sender=other, recipient=request.user, read=False).update(read=True)

    if request.method == 'POST':
        data = json.loads(request.body)
        text = data.get('text', '').strip()
        if text:
            msg = Message.objects.create(sender=request.user, recipient=other, text=text)
            # Create notification for recipient
            sender_name = request.user.get_full_name() or request.user.username
            Notification.objects.create(
                user=other, type='message',
                text=f"{sender_name} sent you a message: {text[:80]}",
                link=f"/messages/{request.user.pk}/",
            )
            return JsonResponse({
                'id': msg.pk, 'text': msg.text,
                'sender': sender_name,
                'timestamp': msg.timestamp.strftime('%d %b %Y, %H:%M'),
                'is_me': True,
            })
        return JsonResponse({'error': 'Empty'}, status=400)

    all_users = User.objects.filter(is_active=True).exclude(id=request.user.id).order_by('first_name', 'last_name')
    # Build full conversation list for sidebar
    users_messaged = User.objects.filter(
        Q(sent_messages__recipient=request.user) |
        Q(received_messages__sender=request.user)
    ).distinct().exclude(id=request.user.id)
    # Make sure active_user appears even if no prior messages
    if other not in users_messaged:
        conversations = [{'user': other, 'last_msg': None, 'unread': 0}]
    else:
        conversations = []
    for u in users_messaged:
        last_msg = Message.objects.filter(
            Q(sender=request.user, recipient=u) |
            Q(sender=u, recipient=request.user)
        ).order_by('-timestamp').first()
        unread = Message.objects.filter(sender=u, recipient=request.user, read=False).count()
        conversations.append({'user': u, 'last_msg': last_msg, 'unread': unread})
    from django.utils.timezone import make_aware
    from datetime import datetime as _dt, timezone as _tz
    _fallback = _dt.min.replace(tzinfo=_tz.utc)
    conversations.sort(key=lambda x: x['last_msg'].timestamp if x['last_msg'] else _fallback, reverse=True)

    return render(request, 'projects/inbox.html', {
        'conversations': conversations,
        'all_users': all_users,
        'active_user': other,
        'messages_qs': messages_qs,
        'total_unread': Message.objects.filter(recipient=request.user, read=False).count(),
    })


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
        'timestamp': c.timestamp.strftime('%d %b %Y, %H:%M'),
    })


@login_required
def team_chat(request):
    if request.method == 'POST':
        import re
        data = json.loads(request.body)
        text = data.get('text', '').strip()
        if not text:
            return JsonResponse({'error': 'Empty'}, status=400)
        # Render mentions/#project-links to their final display form before
        # storing, so historic, freshly-sent, and polled messages all show
        # identically (previously this stored unprintable placeholder bytes).
        display_text = re.sub(r'@\[([^\]]+)\]\(\d+\)', r'@\1', text)
        display_text = re.sub(r'#\[([^\]]+)\]\((\d+)\)', r'<a href="/project/\2/edit/" style="color:var(--acc);font-weight:600">#\1</a>', display_text)
        msg = TeamMessage.objects.create(user=request.user, text=display_text)
        sender_name = request.user.get_full_name() or request.user.username

        # Handle @mentions (from original raw text)
        mentions = re.findall(r'@\[([^\]]+)\]\((\d+)\)', text)
        for name, uid in mentions:
            try:
                tagged = User.objects.get(pk=int(uid))
                if tagged != request.user:
                    Notification.objects.create(
                        user=tagged, type='tag',
                        text=f"{sender_name} mentioned you in team chat: {text[:80]}",
                        link='/chat/',
                    )
            except User.DoesNotExist:
                pass

        return JsonResponse({
            'id': msg.pk,
            'text': display_text,
            'raw_text': text,
            'user': sender_name,
            'initials': _initials(sender_name),
            'timestamp': msg.timestamp.strftime('%d %b %Y, %H:%M'),
            'is_me': True,
        })

    messages_qs = TeamMessage.objects.select_related('user').order_by('-timestamp')[:100]
    messages_qs = list(reversed(list(messages_qs)))
    latest_msg_pk = TeamMessage.objects.order_by('-pk').values_list('pk', flat=True).first() or 0
    all_users = User.objects.filter(is_active=True).order_by('first_name', 'last_name')
    return render(request, 'projects/team_chat.html', {
        'messages_qs': messages_qs,
        'latest_msg_pk': latest_msg_pk,
        'all_users': all_users,
    })


@login_required
def team_chat_poll(request):
    """Return team chat messages newer than `after` for live updates."""
    after = request.GET.get('after', 0)
    try:
        after = int(after)
    except (ValueError, TypeError):
        after = 0
    new_msgs = TeamMessage.objects.select_related('user').filter(pk__gt=after).order_by('timestamp')
    return JsonResponse({
        'messages': [
            {
                'id': m.pk,
                'text': m.text,
                'user': m.user.get_full_name() or m.user.username,
                'user_id': m.user_id,
                'initials': _initials(m.user.get_full_name() or m.user.username),
                'timestamp': m.timestamp.strftime('%d %b %Y, %H:%M'),
                'is_me': m.user_id == request.user.id,
            }
            for m in new_msgs
        ]
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
def staff_directory(request):
    users = User.objects.filter(is_active=True).order_by('first_name', 'last_name')
    # Ensure profiles exist
    for u in users:
        StaffProfile.objects.get_or_create(user=u)
    return render(request, 'projects/staff_directory.html', {'users': users})


@login_required
def staff_profile(request, user_id):
    member = get_object_or_404(User, pk=user_id)
    profile, _ = StaffProfile.objects.get_or_create(user=member)
    if request.method == 'POST':
        profile.role   = request.POST.get('role', '').strip()
        profile.phone  = request.POST.get('phone', '').strip()
        profile.bio    = request.POST.get('bio', '').strip()
        colour = request.POST.get('colour', '').strip()
        if colour in [c[0] for c in StaffProfile.COLOUR_CHOICES]:
            profile.colour = colour
        member.first_name = request.POST.get('first_name', '').strip()
        member.last_name  = request.POST.get('last_name', '').strip()
        member.save()
        profile.save()
        messages.success(request, 'Profile updated.')
        return redirect('staff_profile', user_id=user_id)
    assigned = Project.objects.filter(assigned_to=member).exclude(status__in=['completed','cancelled']).order_by('-created_at')
    upcoming_leave = LeaveRequest.objects.filter(user=member, date__gte=date.today()).order_by('date')[:10]
    return render(request, 'projects/staff_profile.html', {
        'member': member, 'profile': profile,
        'assigned': assigned, 'upcoming_leave': upcoming_leave,
        'colour_choices': StaffProfile.COLOUR_CHOICES,
    })


# ── Leave ─────────────────────────────────────────────────────────────────────

def get_bank_holidays(year):
    """Return dict of date -> name for England bank holidays + Xmas shutdown."""
    from datetime import date as dt_date

    def easter(y):
        a = y % 19
        b = y // 100
        c = y % 100
        d_ = b // 4
        e = b % 4
        f = (b + 8) // 25
        g = (b - f + 1) // 3
        h = (19*a + b - d_ - g + 15) % 30
        i = c // 4
        k = c % 4
        l = (32 + 2*e + 2*i - h - k) % 7
        m = (a + 11*h + 22*l) // 451
        mo = (h + l - 7*m + 114) // 31
        dy = ((h + l - 7*m + 114) % 31) + 1
        return dt_date(y, mo, dy)

    holidays = {}
    for y in [year-1, year, year+1]:
        e = easter(y)
        hols = {
            dt_date(y, 1, 1): "New Year's Day",
            e - timedelta(days=2): "Good Friday",
            e + timedelta(days=1): "Easter Monday",
            dt_date(y, 5, 1) + timedelta(days=(7 - dt_date(y,5,1).weekday()) % 7): "Early May Bank Holiday",
            dt_date(y, 5, 25) + timedelta(days=(7 - dt_date(y,5,25).weekday()) % 7): "Spring Bank Holiday",
            dt_date(y, 8, 25) + timedelta(days=(7 - dt_date(y,8,25).weekday()) % 7): "Summer Bank Holiday",
            dt_date(y, 12, 25): "Christmas Day",
            dt_date(y, 12, 26): "Boxing Day",
        }
        # Substitute if on weekend
        for dt, name in list(hols.items()):
            if dt.weekday() == 5: hols[dt + timedelta(days=2)] = name + " (subst.)"
            elif dt.weekday() == 6: hols[dt + timedelta(days=1)] = name + " (subst.)"
        holidays.update({dt.isoformat(): name for dt, name in hols.items()})

        # Christmas shutdown: working days 27 Dec – 31 Dec
        cur = dt_date(y, 12, 27)
        end = dt_date(y+1, 1, 1)
        while cur < end:
            if cur.weekday() < 5:
                k = cur.isoformat()
                if k not in holidays:
                    holidays[k] = "Xmas Shutdown"
            cur += timedelta(days=1)

    return holidays


@login_required
def leave_overview(request):
    import calendar as cal_mod, json
    today = date.today()
    try:
        year = int(request.GET.get('year', today.year))
    except (ValueError, TypeError):
        year = today.year
    try:
        month = int(request.GET.get('month', today.month))
    except (ValueError, TypeError):
        month = today.month
    if not (1 <= month <= 12) or not (1 <= year <= 9999):
        year, month = today.year, today.month

    first_day = date(year, month, 1)
    last_day  = date(year, month, cal_mod.monthrange(year, month)[1])
    # Pad to Monday
    start = first_day - timedelta(days=first_day.weekday())
    end_raw = last_day + timedelta(days=(6 - last_day.weekday()))
    end = end_raw if (end_raw - start).days >= 27 else end_raw + timedelta(weeks=1)

    leave_qs = LeaveRequest.objects.filter(date__gte=start, date__lte=end).select_related('user').order_by('date','user__first_name')

    # Load profile colours for all users with leave this period
    from projects.templatetags.project_extras import AVATAR_COLOURS
    profile_colours = {p.user_id: p.colour for p in StaffProfile.objects.filter(
        user_id__in=leave_qs.values_list('user_id', flat=True)
    ).exclude(colour='')}

    def get_user_colour(user_id):
        return profile_colours.get(user_id) or AVATAR_COLOURS[user_id % len(AVATAR_COLOURS)]

    user_colours = {}
    legend = []
    for l in leave_qs:
        if l.user_id not in user_colours:
            colour = get_user_colour(l.user_id)
            user_colours[l.user_id] = colour
            legend.append({
                'name': l.user.get_full_name() or l.user.username,
                'colour': colour,
                'user_pk': l.user_id,
            })

    # Build leave map: date -> list of leave entries
    leave_map = {}
    for l in leave_qs:
        k = l.date.isoformat()
        leave_map.setdefault(k, []).append({
            'id': l.pk,
            'name': l.user.get_full_name() or l.user.username,
            'half_day': l.get_half_day_display(),
            'user_pk': l.user.pk,
            'colour': user_colours.get(l.user_id, '#888'),
        })

    # Build bank holidays
    bank_holidays = get_bank_holidays(year)

    # Build calendar days
    calendar_days = []
    d = start
    while d <= end:
        k = d.isoformat()
        calendar_days.append({
            'date': d,
            'is_today': d == today,
            'in_range': d.month == month,
            'entries': leave_map.get(k, []),
            'bank_holiday': bank_holidays.get(k),
        })
        d += timedelta(days=1)

    if month == 1:
        prev_year, prev_month = year-1, 12
    else:
        prev_year, prev_month = year, month-1
    if month == 12:
        next_year, next_month = year+1, 1
    else:
        next_year, next_month = year, month+1

    users = User.objects.filter(is_active=True).order_by('first_name', 'last_name')
    return render(request, 'projects/leave_overview.html', {
        'calendar_days': calendar_days,
        'leave_map_json': json.dumps(leave_map),
        'year': year, 'month': month,
        'month_name': first_day.strftime('%B %Y'),
        'prev_year': prev_year, 'prev_month': prev_month,
        'next_year': next_year, 'next_month': next_month,
        'users': users, 'today': today,
        'legend': legend,
        'bank_holidays_json': json.dumps(bank_holidays),
    })


@login_required
@require_POST
def leave_add(request):
    from datetime import timedelta
    data = json.loads(request.body)
    user_id    = data.get('user_id')
    date_from  = data.get('date_from', '')
    date_to    = data.get('date_to', date_from)
    half_day   = data.get('half_day', 'full')
    note       = data.get('note', '').strip()
    try:
        leave_user = User.objects.get(pk=int(user_id))
        d_from = date.fromisoformat(date_from)
        d_to   = date.fromisoformat(date_to) if date_to else d_from
        if d_to < d_from:
            return JsonResponse({'error': 'End date must be after start date'}, status=400)
        added = []
        current = d_from
        while current <= d_to:
            if current.weekday() < 5:  # skip weekends
                obj, created = LeaveRequest.objects.get_or_create(
                    user=leave_user, date=current, half_day=half_day,
                    defaults={'note': note, 'added_by': request.user}
                )
                if created:
                    added.append(obj)
            current += timedelta(days=1)
        return JsonResponse({
            'count': len(added),
            'user': leave_user.get_full_name() or leave_user.username,
            'date_from': d_from.strftime('%d %b %Y'),
            'date_to': d_to.strftime('%d %b %Y'),
        })
    except (ValueError, User.DoesNotExist) as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_POST
def leave_delete(request, pk):
    leave = get_object_or_404(LeaveRequest, pk=pk)
    leave.delete()
    return JsonResponse({'ok': True})


# ── Installation Report ───────────────────────────────────────────────────────

@login_required
def install_report(request, pk):
    project = get_object_or_404(Project, pk=pk)
    report, _ = InstallationReport.objects.get_or_create(
        project=project,
        defaults={'created_by': request.user}
    )

    if request.method == 'POST':
        report.notes          = request.POST.get('notes', '').strip()
        report.issues         = request.POST.get('issues', '').strip()
        report.job_tasks      = request.POST.get('job_tasks', '').strip()
        report.fitting_crew        = request.POST.get('fitting_crew', '').strip()
        report.fitting_crew_phone  = request.POST.get('fitting_crew_phone', '').strip()
        report.site_contact   = request.POST.get('site_contact', '').strip()
        report.contact_number = request.POST.get('contact_number', '').strip()
        sc = request.POST.get('site_cleared', '')
        report.site_cleared = True if sc == 'yes' else (False if sc == 'no' else None)
        report.site_cleared_notes = request.POST.get('site_cleared_notes', '').strip()
        wc = request.POST.get('work_completed', '')
        report.work_completed = True if wc == 'yes' else (False if wc == 'no' else None)
        report.work_completed_notes = request.POST.get('work_completed_notes', '').strip()
        rv = request.POST.get('return_visit_required', '')
        report.return_visit_required = True if rv == 'yes' else (False if rv == 'no' else None)
        report.save()

        # Handle photo uploads
        for photo in request.FILES.getlist('photos'):
            ReportPhoto.objects.create(
                report=report,
                file_data=photo.read(),
                file_mime=photo.content_type or '',
                file_original_name=photo.name,
                caption=request.POST.get('photo_caption', ''),
            )

        # Handle satisfaction note upload (PDF or photo)
        if 'satisfaction_note' in request.FILES:
            f = request.FILES['satisfaction_note']
            SatisfactionNote.objects.create(
                report=report,
                file_data=f.read(),
                file_mime=f.content_type or '',
                file_original_name=f.name,
                caption=request.POST.get('sat_caption', '')
            )
        messages.success(request, 'Report saved.')
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept',''):
            return JsonResponse({'ok': True})
        return redirect('install_report', pk=pk)

    return render(request, 'projects/install_report.html', {
        'project': project, 'report': report,
        'photos': report.photos.all(),
        'satisfaction_notes': report.satisfaction_notes.all(),
        'project_pk': project.pk,
    })


@login_required
@require_POST
def delete_report_photo(request, pk):
    photo = get_object_or_404(ReportPhoto, pk=pk)
    if photo.image:
        photo.image.delete()
    photo.delete()
    return JsonResponse({'ok': True})


@login_required
@require_POST
def delete_satisfaction_note(request, pk):
    note = get_object_or_404(SatisfactionNote, pk=pk)
    if note.file:
        note.file.delete()
    note.delete()
    return JsonResponse({'ok': True})


@login_required
@require_POST
def report_photo_upload(request, pk):
    """Instant AJAX upload of as-built photos to a project's install report."""
    project = get_object_or_404(Project, pk=pk)
    report, _ = InstallationReport.objects.get_or_create(project=project)
    created = []
    for photo in request.FILES.getlist('photos'):
        p = ReportPhoto.objects.create(
            report=report,
            file_data=photo.read(),
            file_mime=photo.content_type or '',
            file_original_name=photo.name,
            caption=request.POST.get('photo_caption', ''),
        )
        created.append({'pk': p.pk, 'url': f'/report/photo/{p.pk}/file/'})
    return JsonResponse({'ok': True, 'photos': created})


@login_required
@require_POST
def report_sat_upload(request, pk):
    """Instant AJAX upload of a satisfaction note (PDF or image)."""
    project = get_object_or_404(Project, pk=pk)
    report, _ = InstallationReport.objects.get_or_create(project=project)
    f = request.FILES.get('satisfaction_note')
    if not f:
        return JsonResponse({'error': 'No file'}, status=400)
    note = SatisfactionNote.objects.create(
        report=report,
        file_data=f.read(),
        file_mime=f.content_type or '',
        file_original_name=f.name,
        caption=request.POST.get('sat_caption', ''),
    )
    return JsonResponse({'ok': True, 'pk': note.pk, 'name': note.file_original_name,
                         'url': f'/report/sat/{note.pk}/file/'})


@login_required
def report_photo_file(request, pk):
    from django.http import HttpResponse, FileResponse
    photo = get_object_or_404(ReportPhoto, pk=pk)
    if photo.file_data:
        resp = HttpResponse(bytes(photo.file_data), content_type=photo.file_mime or 'image/jpeg')
        resp['Content-Disposition'] = f'inline; filename="{photo.file_original_name or "photo.jpg"}"'
        return resp
    if photo.image:
        return FileResponse(photo.image.open(), content_type='image/jpeg')
    return HttpResponse('Not found', status=404)


@login_required
def photo_library(request):
    """Library of installed-project photos, grouped into one 'folder' per project."""
    q = request.GET.get('q', '').strip()
    reports = (InstallationReport.objects
               .filter(photos__isnull=False)
               .select_related('project')
               .prefetch_related('photos')
               .distinct()
               .order_by('-updated_at'))
    if q:
        reports = reports.filter(
            Q(project__project_name__icontains=q) | Q(project__customer__icontains=q) |
            Q(project__location__icontains=q)
        )
    folders = []
    for r in reports:
        photos = list(r.photos.all())
        if not photos:
            continue
        latest_upload = max(p.uploaded_at for p in photos)
        folders.append({
            'project': r.project,
            'cover': photos[0],
            'count': len(photos),
            'updated_at': latest_upload,
        })
    folders.sort(key=lambda f: f['updated_at'], reverse=True)
    quote_photo_count = QuotePhoto.objects.count()
    return render(request, 'projects/photo_library.html', {
        'folders': folders, 'query': q, 'quote_photo_count': quote_photo_count,
    })


@login_required
def photo_library_project(request, pk):
    """All install-report photos for a single project."""
    project = get_object_or_404(Project, pk=pk)
    report = get_object_or_404(InstallationReport, project=project)
    photos = report.photos.all()
    return render(request, 'projects/photo_library_project.html', {
        'project': project, 'photos': photos,
    })


@login_required
def satisfaction_note_file(request, pk):
    from django.http import HttpResponse, FileResponse
    note = get_object_or_404(SatisfactionNote, pk=pk)
    if note.file_data:
        resp = HttpResponse(bytes(note.file_data), content_type=note.file_mime or 'application/octet-stream')
        resp['Content-Disposition'] = f'inline; filename="{note.file_original_name or "note"}"'
        return resp
    if note.file:
        return FileResponse(note.file.open(), content_type='application/octet-stream')
    return HttpResponse('Not found', status=404)


# ── Customer Database ─────────────────────────────────────────────────────────

@login_required
def customer_list(request):
    q = request.GET.get('q', '').strip()
    customers = CustomerProfile.objects.all().order_by('name')
    if q:
        customers = customers.filter(
            Q(name__icontains=q) | Q(contact_name__icontains=q) |
            Q(email__icontains=q) | Q(phone__icontains=q)
        )
    return render(request, 'projects/customer_list.html', {
        'customers': customers, 'query': q,
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
    projects = Project.objects.filter(
        customer__iexact=customer.name
    ).order_by('-created_at')
    from .countries import COUNTRIES
    return render(request, 'projects/customer_detail.html', {
        'customer': customer, 'projects': projects, 'countries': COUNTRIES,
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
@require_POST
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


def _po_locked_response(po):
    """Return a JsonResponse error if the PO is locked, else None."""
    if po.locked:
        return JsonResponse({'error': 'This PO is locked. Unlock it first to make changes.'}, status=400)
    return None


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
                'uploaded_at': doc.uploaded_at.strftime('%d %b %Y, %H:%M'),
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
            'uploaded_at': d.uploaded_at.strftime('%d %b %Y, %H:%M'),
            'notes': d.notes,
            'ext': ext,
        })
    return JsonResponse(result, safe=False)


@login_required
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

    valid_sorts = ['code','description','quantity','qty_allocated','qty_on_order','free_stock','reorder_level','reorder_qty']
    if sort not in valid_sorts:
        sort = 'code'
    order = sort if direction == 'asc' else f'-{sort}'

    products = Product.objects.all()
    if q:
        products = products.filter(Q(code__icontains=q) | Q(description__icontains=q))
    # Note: inactive products still shown in stock list (greyed out) but filtered in search API

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
        {'key':'sales_price',   'label':'Price',       'right':True, 'default_dir':'desc'},
    ]
    return render(request, 'projects/stock_list.html', {
        'products': products, 'query': q,
        'low_stock_count': low_stock_count,
        'low_only': low_only,
        'sort': sort, 'dir': direction,
        'columns': columns,
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
        if 'sales_price' in data:
            product.sales_price = data.get('sales_price') or 0
        product.save()
        if new_qty != old_qty:
            StockMovement.objects.create(
                product=product, movement_type='adjust',
                qty_change=round(new_qty - old_qty, 2),
                reason='Manual stock adjustment', user=request.user,
            )
        return JsonResponse({'ok': True, 'deleted': False})
    return JsonResponse({'id': product.pk, 'code': product.code, 'description': product.description,
                         'quantity': float(product.quantity), 'reorder_level': float(product.reorder_level),
                         'sales_price': float(product.sales_price)})


# ── Picking Lists ─────────────────────────────────────────────────────────────

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
    return render(request, 'projects/picking_list.html', {
        'project': project, 'pl': pl, 'items': items,
        'templates': templates, 'customer_notes': customer_notes,
        'any_shortage': any_shortage, 'project_pk': project.pk,
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
def picking_list_delete(request, pk):
    pl = get_object_or_404(PickingList, pk=pk)
    if pl.allocated:
        return JsonResponse({'error': 'This picking list is locked because stock has been allocated. Release the allocation first to delete it.'}, status=400)
    pl.delete()
    return JsonResponse({'ok': True})


@login_required
@require_POST
@login_required
@require_POST
def stock_import(request):
    import openpyxl
    f = request.FILES.get('file')
    if not f:
        return JsonResponse({'error': 'No file'}, status=400)
    try:
        wb = openpyxl.load_workbook(f)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))[1:]
        count = 0
        for row in rows:
            code = str(row[0]).strip() if row[0] else ''
            desc = str(row[1]).strip() if row[1] else ''
            if not code or code == 'None':
                continue
            Product.objects.update_or_create(
                code=code,
                defaults={
                    'description':   desc,
                    'quantity':      float(row[3]) if row[3] is not None else 0,
                    'qty_allocated': float(row[4]) if row[4] is not None else 0,
                    'qty_on_order':  float(row[5]) if row[5] is not None else 0,
                    'reorder_level': float(row[6]) if row[6] is not None else 0,
                    'reorder_qty':   float(row[7]) if row[7] is not None else 0,
                    'cost_price':    float(row[8]) if row[8] is not None else 0,
                    'free_stock':    float(row[9]) if row[9] is not None else 0,
                    'sales_price':   float(row[2]) if row[2] is not None else 0,
                }
            )
            count += 1
        return JsonResponse({'ok': True, 'count': count})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


def _picking_locked_response(pl):
    """Return a JsonResponse error if the picking list's stock is allocated
    (locked against composition/quantity changes), else None."""
    if pl.allocated:
        return JsonResponse({'error': 'This picking list is locked because stock has been allocated. Release the allocation first to make changes.'}, status=400)
    return None


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
@require_POST
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
@require_POST
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
@require_POST
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
@require_POST
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

    ITEM_WEIGHTS = {
        'BTP48':2.0,'BTP60':2.3,'BTP72':2.7,'BTP84':3.2,'BTP96':3.6,'BTP108':4.1,'BTP120':4.5,'BTP144':5.0,
        'TP48':2.0,'TP60':2.3,'TP72':2.7,'TP84':3.2,'TP96':3.6,'TP108':4.1,'TP120':4.5,'TP144':5.0,
        'TPC12':0.5,'TPC15':0.5,'TPC18':0.6,'TPC21':0.8,'TPC24':0.9,'TPC27':1.0,'TPC30':1.2,'TPC36':1.3,
        'TFCL36':1.6,'TFCL39.5':1.6,'TFCL48':2.0,
        'TFCV24':1.4,'TFCV30':1.4,'TFCV36':1.5,'TFCV39.5':1.6,'TFCV43.5':1.8,'TFCV48':2.0,
        'TBC36':1.5,'TBC39.5':1.8,'TBC48':2.0,
        'TWB36':2.2,'TWB39.5':3.2,'TWB48':3.2,'TWB60':3.8,'TWB72':4.6,
        'TCTB12':0.6,'TCTB15':0.6,'TCTB18':0.6,'TCTB24':0.8,'TCTB30':1.0,'TCTB36':1.2,
        'SB18':0.6,'SB24':0.8,'SB27':1.0,'SB30':1.0,'SB36':1.2,
        'FP':0.1,'SM':0.12,'TFP':0.01,
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

def generate_picking_reference(lines, selected_accessories=None, wall_fixings=0, back_to_back=0, mobile_bases=0):
    from collections import defaultdict
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
                # Board code: WxD for chipboard, WxDMFC for melamine
                board_code = f'{w}X{d}MFC' if melamine else f'{w}X{d}'
                items[board_code] += qty
                # Connector/beam
                if stype == 'tfcv':
                    items[f'TFCV{w}'] += qty
                elif stype == 'twb':
                    items[f'TWB{w}'] += qty
        elif line.line_type == 'inhang':
            # Description: "Inboard Hanging 48" x 18""
            # 2 rails (HRS{depth}) + 1 foot (SMFOOT) per set
            parts = line.description.replace('"','').split('x')
            if len(parts) >= 2:
                depth_str = parts[-1].strip().split()[0]  # last number
                try:
                    depth = str(int(float(depth_str)))
                    items[f'HRS{depth}'] += qty * 2
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

# Post prices (code -> price)
POSTS = {'TP48':5.55,'TP60':6.81,'TP72':8.18,'TP84':9.45,'TP96':10.91,
         'TP108':12.25,'TP120':13.61,'TP144':16.32}
# Connector prices (code -> price)
TPCS  = {'TPC12':1.44,'TPC15':1.61,'TPC18':1.90,'TPC21':2.28,'TPC24':2.28,
          'TPC27':2.81,'TPC30':2.81,'TPC36':3.25}
# Height -> (post_code, n_connectors)
HEIGHT_MAP = {'48':('TP48',2),'60':('TP60',2),'72':('TP72',3),'84':('TP84',3),
              '96':('TP96',4),'108':('TP108',4),'120':('TP120',5),'144':('TP144',5)}
# Depth -> connector_code
DEPTH_MAP  = {'12':'TPC12','15':'TPC15','18':'TPC18','21':'TPC21',
              '24':'TPC24','27':'TPC27','30':'TPC30','36':'TPC36'}
# TFCV connector prices (depth -> price)
TFCV_CONN  = {'24':3.50,'30':3.50,'36':4.25,'39.5':4.99,'43.5':5.58,'48':5.58}
# TWB prices (width"xdepth" -> price) - beams only, no deck included
TWB_PRICES = {'36':5.74,'48':7.57,'60':10.38,'72':12.20}
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

FRAME_HEIGHTS = ['48','60','72','84','96','108','120','144']
FRAME_DEPTHS  = ['12','15','18','21','24','27','30','36']
TFCV_WIDTHS   = ['24','30','36','39.5','43.5','48']
TWB_WIDTHS    = ['36','48','60','72']
SHELF_DEPTHS  = ['12','15','18','21','24','27','30','36']


def trimline_component_prices():
    """Return {code: price} for trimline components from the editable price list."""
    try:
        return {item.code: float(item.price)
                for item in PriceListItem.objects.filter(product_line='trimline')}
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


def calc_shelf_price(shelf_type, width_in, depth_in, melamine, chipboard_price, melamine_price, chipboard_18mm_price=None):
    """Return shelf unit cost.
    Board material rate:
      - melamine -> melamine rate
      - chipboard, depth >= 27" -> 18mm chipboard rate
      - chipboard, depth < 27"  -> standard (15mm) chipboard rate
    """
    w, d = float(width_in), float(depth_in)
    sqft = (w * d) / 144
    if melamine:
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

    # Recalculate dynamic prices for frame/shelf lines
    for line in lines:
        if line.line_type == 'frame':
            parts = line.size.replace('"','').split('x')
            if len(parts) == 2:
                line.unit_cost = calc_frame_price(parts[0].strip(), parts[1].strip())
                line.save()
        elif line.line_type == 'shelf':
            parts = line.size.replace('"','').split('x')
            if len(parts) == 3:
                stype, w, d = parts[0].strip(), parts[1].strip(), parts[2].strip()
                line.unit_cost = calc_shelf_price(stype, w, d, line.melamine, chipboard, melamine_p, chip18)
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
        'BTP48':2.0,'BTP60':2.3,'BTP72':2.7,'BTP84':3.2,'BTP96':3.6,'BTP108':4.1,'BTP120':4.5,'BTP144':5.0,
        'TP48':2.0,'TP60':2.3,'TP72':2.7,'TP84':3.2,'TP96':3.6,'TP108':4.1,'TP120':4.5,'TP144':5.0,
        'TPC12':0.5,'TPC15':0.5,'TPC18':0.6,'TPC21':0.8,'TPC24':0.9,'TPC27':1.0,'TPC30':1.2,'TPC36':1.3,
        'TFCL36':1.6,'TFCL39.5':1.6,'TFCL48':2.0,
        'TFCV24':1.4,'TFCV30':1.4,'TFCV36':1.5,'TFCV39.5':1.6,'TFCV43.5':1.8,'TFCV48':2.0,
        'TBC36':1.5,'TBC39.5':1.8,'TBC48':2.0,
        'TWB36':2.2,'TWB39.5':3.2,'TWB48':3.2,'TWB60':3.8,'TWB72':4.6,
        'TCTB12':0.6,'TCTB15':0.6,'TCTB18':0.6,'TCTB24':0.8,'TCTB30':1.0,'TCTB36':1.2,
        'SB18':0.6,'SB24':0.8,'SB27':1.0,'SB30':1.0,'SB36':1.2,
        'FP':0.1,'SM':0.12,'TFP':0.01,
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
            {'key': 'mobile_bases', 'label': 'Mobile bases'},
        ],
        'project_pk': project.pk,
    })


@login_required
@require_POST
def project_cost_save(request, pk):
    project = get_object_or_404(Project, pk=pk)
    cost, _ = ProjectCost.objects.get_or_create(project=project)
    data = json.loads(request.body)
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
    cost = get_object_or_404(ProjectCost, project__pk=pk)
    data = json.loads(request.body)
    mat = {m.name: float(m.price_per_sqft) for m in MaterialPrice.objects.all()}
    chipboard = mat.get('chipboard', 0.50)
    melamine_p = mat.get('melamine', 0.75)
    chip18 = mat.get('chipboard_18mm', melamine_p)

    ltype = data.get('line_type')
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
            row_qty = float(row.get('quantity', 0))
            if not w or not d or row_qty <= 0:
                continue
            size = f'tfcv x {w} x {d}'
            unit_cost = calc_shelf_price('tfcv', w, d, mel, chipboard, melamine_p, chip18)
            mat_label = 'Melamine' if mel else 'Chipboard'
            desc = f'TFCV Shelf {w}" x {d}" ({mat_label})'
            ProjectCostLine.objects.create(cost=cost, line_type='shelf', description=desc,
                size=size, melamine=mel, quantity=row_qty, unit_cost=unit_cost, sort_order=sort)
            sort += 1

    elif ltype == 'shelf_twb':
        rows = data.get('rows', [])
        for row in rows:
            w, d = row.get('width',''), row.get('depth','')
            mel = bool(row.get('melamine', False))
            row_qty = float(row.get('quantity', 0))
            if not w or not d or row_qty <= 0:
                continue
            size = f'twb x {w} x {d}'
            unit_cost = calc_shelf_price('twb', w, d, mel, chipboard, melamine_p, chip18)
            mat_label = 'Melamine' if mel else 'Chipboard'
            desc = f'TWB Shelf {w}" x {d}" ({mat_label})'
            ProjectCostLine.objects.create(cost=cost, line_type='shelf', description=desc,
                size=size, melamine=mel, quantity=row_qty, unit_cost=unit_cost, sort_order=sort)
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
        parts = line.size.split(' x ')
        if len(parts) == 3:
            stype, w, d = parts
            line.unit_cost = calc_shelf_price(stype, w, d, line.melamine, chipboard, melamine_p, chip18)
            mat_label = 'Melamine' if line.melamine else 'Chipboard'
            line.description = line.description.rsplit('(', 1)[0].strip() + f' ({mat_label})'
    line.save()
    return JsonResponse({'ok': True, 'unit_cost': float(line.unit_cost), 'line_total': line.line_total})


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
    if cost.is_accepted:
        # Un-accept (allowed — customer hasn't decided)
        cost.is_accepted = False
        cost.save(update_fields=['is_accepted'])
    else:
        # Accept this one, clear all others
        project.costs.exclude(pk=cost_pk).update(is_accepted=False)
        cost.is_accepted = True
        cost.save(update_fields=['is_accepted'])
    from django.urls import reverse
    proforma_url = reverse('proforma_invoice_option', kwargs={'pk': project.pk, 'cost_pk': cost.pk}) if cost.is_accepted else None
    return JsonResponse({'ok': True, 'is_accepted': cost.is_accepted, 'proforma_url': proforma_url})


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
    cost = get_object_or_404(ProjectCost, project__pk=pk)
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
    cost = get_object_or_404(ProjectCost, project__pk=pk)
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
def save_dashboard_filters(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        request.session['dash_filters'] = data
        return JsonResponse({'ok': True})
    return JsonResponse({'filters': request.session.get('dash_filters', {})})


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
def customer_quote(request, pk, cost_pk=None):
    project = get_object_or_404(Project, pk=pk)
    if cost_pk:
        cost = get_object_or_404(ProjectCost, pk=cost_pk, project=project)
    else:
        cost = project.costs.filter(is_accepted=True).first() or project.costs.first()
        if not cost:
            cost, _ = ProjectCost.objects.get_or_create(project=project)
    lines = cost.lines.select_related('product').all()
    buying_total = sum(l.line_total for l in lines)
    uprights = sum(int(float(l.quantity))*2 for l in lines if l.line_type=='frame')
    for acc in cost.accessories.all():
        buying_total += float(acc.unit_price) * uprights
    markup_amount = buying_total * float(cost.markup) / 100
    sell_price = round(buying_total + markup_amount + float(cost.labour) + float(cost.delivery), 2)

    # Customer profile for address + contact
    cp = CustomerProfile.objects.filter(name__iexact=project.customer).first()
    quote, created = ProjectQuote.objects.get_or_create(cost=cost, defaults={'project': project})


    if request.method == 'POST':
        quote.intro          = request.POST.get('intro', '').strip()
        quote.greeting       = request.POST.get('greeting', '').strip()
        quote.thank_you      = request.POST.get('thank_you', '').strip()
        quote.closing        = request.POST.get('closing', '').strip()
        quote.signature_name = request.POST.get('signature_name', '').strip()
        quote.quote_date     = request.POST.get('quote_date', '').strip()
        quote.address_block  = request.POST.get('address_block', '').strip()
        quote.header_ref     = request.POST.get('header_ref', '').strip()
        quote.supply_line    = request.POST.get('supply_line', '').strip()
        quote.capacity       = request.POST.get('capacity', '').strip()
        quote.bay_breakdown  = request.POST.get('bay_breakdown', '').strip()
        quote.spec_note      = request.POST.get('spec_note', '').strip()
        quote.main_price_label = request.POST.get('main_price_label', '').strip()
        quote.main_price     = request.POST.get('main_price') or 0
        quote.extra_label    = request.POST.get('extra_label', '').strip()
        ep = request.POST.get('extra_price', '').strip()
        quote.extra_price    = ep if ep else None
        quote.lead_time      = request.POST.get('lead_time', '').strip()
        quote.payment_terms  = request.POST.get('payment_terms', '').strip()
        quote.terms_text     = request.POST.get('terms_text', '').strip()
        try:
            quote.photo_columns = int(request.POST.get('photo_columns', 2)) or 2
        except (ValueError, TypeError):
            quote.photo_columns = 2
        quote.save()
        if request.POST.get('action') == 'print':
            return redirect(f"{request.path}?print=1")
        messages.success(request, 'Quote saved.')
        return redirect('customer_quote', pk=pk)

    # Contact first name for "Dear X"
    contact_name = cp.contact_name if cp and cp.contact_name else ''
    first_name = contact_name.split()[0] if contact_name else ''

    # Project label for the enquiry line: "Rieker - Doncaster"
    proj_label = project.project_name
    if project.location and project.location not in project.project_name:
        proj_label = f"{project.project_name} - {project.location}"

    # Determine supply phrasing from costing
    has_delivery = float(cost.delivery) > 0
    has_install = float(cost.labour) > 0
    drawing_ref = project.drawing_number or '[drawing number]'
    if has_delivery and has_install:
        supply_verb = "To supply, deliver and install"
    elif has_delivery and not has_install:
        supply_verb = "To supply and deliver"
    else:
        supply_verb = "To supply only (ex. works)"
    default_supply_line = (
        f"{supply_verb} Trimline shelving to give the layout as shown on the drawing "
        f"{drawing_ref}, and as described below:"
    )

    # On first creation, seed defaults
    if created:
        quote.main_price = sell_price
        quote.main_price_label = f'Our price to {supply_verb.lower().replace("to ", "")}:'
        quote.lead_time = '4-5 weeks from receipt of PO and approved drawing.'
        quote.payment_terms = '30 days from the date of invoice, please'
        quote.terms_text = (
            "Our prices are exclusive of VAT and remain open for acceptance for 30 days.\n\n"
            "Our price is subject to site survey and is based on a clear and level site with light and power, good access and normal working hours.\n\n"
            "Title of all goods supplied remains the property of E-Z-Rect Ltd t/a EZR Shelving until paid for in full. Our trading terms apply.\n\n"
            "We have not made any allowance for MCD or retention within our costs."
        )
        quote.intro = ''
        quote.save()

    # Auto opening lines (always live from project data)
    thank_you_line = f"Thank you for your enquiry for shelving at {proj_label}, for which we now have the pleasure of quoting as follows:"

    # Build address block lines
    addr_lines = []
    if cp:
        if cp.contact_name: addr_lines.append(cp.contact_name)
        addr_lines.append(cp.name)
        for part in [cp.address_line1, cp.address_line2, cp.town, cp.county, cp.postcode]:
            if part: addr_lines.append(part)

    default_address_block = '\n'.join(addr_lines)
    import datetime as _dt
    today = _dt.date.today()
    default_date = today.strftime('%d %B %Y').lstrip('0')
    default_ref = f"{project.customer}\n{project.project_name}"
    default_greeting = f"Dear {first_name}," if first_name else "Dear Sir/Madam,"
    default_closing = "We trust the above meets with your approval and if you require any further information, please do not hesitate to contact me."
    sig = request.user.get_full_name() or request.user.username

    attached_photos = quote.attached_photos.all()
    library_photos = QuotePhoto.objects.all()

    return render(request, 'projects/customer_quote.html', {
        'project': project,
        'cost': cost,
        'quote': quote,
        'customer_profile': cp,
        'contact_name': contact_name,
        'first_name': first_name,
        'thank_you_line': quote.thank_you or thank_you_line,
        'supply_line': quote.supply_line or default_supply_line,
        'supply_verb': supply_verb,
        'default_price_label': f'Our price to {supply_verb.lower().replace("to ", "")}:',
        'greeting': quote.greeting or default_greeting,
        'closing': quote.closing or default_closing,
        'signature': quote.signature_name or sig,
        'addr_lines': addr_lines,
        'quote_date': quote.quote_date or default_date,
        'address_block': quote.address_block or default_address_block,
        'header_ref': quote.header_ref or default_ref,
        'sell_price': sell_price,
        'today': today,
        'attached_photos': attached_photos,
        'library_photos': library_photos,
        'print_mode': request.GET.get('print') == '1',
        'project_pk': project.pk if not request.GET.get('print') else None,
    })


@login_required
@require_POST
def quote_refresh_price(request, pk, cost_pk=None):
    """Recompute sell price from the current costing and update the quote."""
    project = get_object_or_404(Project, pk=pk)
    if cost_pk:
        cost = get_object_or_404(ProjectCost, pk=cost_pk, project=project)
    else:
        cost = project.costs.filter(is_accepted=True).first() or project.costs.first()
    if not cost:
        return JsonResponse({'error': 'No costing found for this project.'}, status=400)
    sell_price = _calc_sell_price(cost)
    has_delivery = float(cost.delivery or 0) > 0
    has_install  = float(cost.labour   or 0) > 0
    if has_delivery and has_install:
        supply_verb = "supply, deliver and install"
    elif has_delivery:
        supply_verb = "supply and deliver"
    else:
        supply_verb = "supply only (ex. works)"
    price_label = f'Our price to {supply_verb}:'
    quote, _ = ProjectQuote.objects.get_or_create(cost=cost, defaults={'project': project})
    quote.main_price = sell_price
    quote.main_price_label = price_label
    quote.save(update_fields=['main_price', 'main_price_label'])
    return JsonResponse({'ok': True, 'sell_price': float(sell_price), 'price_label': price_label})


@login_required
def proforma_invoice(request, pk, cost_pk=None):
    project = get_object_or_404(Project, pk=pk)
    if cost_pk:
        cost = get_object_or_404(ProjectCost, pk=cost_pk, project=project)
    else:
        cost = project.costs.filter(is_accepted=True).first()
        if not cost:
            messages.error(request, 'Please approve a costing option before creating a Pro-Forma invoice.')
            return redirect('project_edit', pk=pk)
    lines = cost.lines.select_related('product').all()
    buying_total = sum(l.line_total for l in lines)
    uprights = sum(int(float(l.quantity))*2 for l in lines if l.line_type=='frame')
    for acc in cost.accessories.all():
        buying_total += float(acc.unit_price) * uprights
    markup_amount = buying_total * float(cost.markup) / 100
    sell_price = round(buying_total + markup_amount + float(cost.labour) + float(cost.delivery), 2)

    cp = CustomerProfile.objects.filter(name__iexact=project.customer).first()

    pf, created = ProformaInvoice.objects.get_or_create(cost=cost, defaults={'project': project})
    if created:
        pf.order_no = project.sales_order or ''
        from datetime import date
        pf.invoice_date = date.today().strftime('%B %d, %Y').upper()
        # Invoice-to block from customer profile
        block = []
        if cp:
            block.append(cp.name or project.customer)
            for part in [cp.address_line1, cp.address_line2, cp.town, cp.county, cp.postcode]:
                if part:
                    block.append(part)
            if cp.country and cp.country != 'United Kingdom':
                block.append(cp.country)
            if cp.contact_name:
                block.append(f'ATTN – {cp.contact_name}')
        else:
            block.append(project.customer)
        pf.invoice_to = '\n'.join(block)
        pf.ezr_contact = (request.user.get_full_name() or request.user.username)
        pf.description = f'{project.project_name}'
        if project.drawing_number:
            pf.description += f'\n{project.drawing_number}'
        pf.goods_total = sell_price
        pf.deposit_pct = 50
        pf.save()

    if request.method == 'POST':
        pf.order_no      = request.POST.get('order_no', '').strip()
        pf.invoice_date  = request.POST.get('invoice_date', '').strip()
        pf.invoice_to    = request.POST.get('invoice_to', '').strip()
        pf.delivery_to   = request.POST.get('delivery_to', '').strip()
        pf.ezr_contact   = request.POST.get('ezr_contact', '').strip()
        pf.po_number     = request.POST.get('po_number', '').strip()
        pf.requisitioner = request.POST.get('requisitioner', '').strip()
        pf.shipped_via   = request.POST.get('shipped_via', '').strip()
        pf.fob_point     = request.POST.get('fob_point', '').strip()
        pf.terms         = request.POST.get('terms', '').strip()
        pf.comments      = request.POST.get('comments', '').strip()
        pf.description   = request.POST.get('description', '').strip()
        try:
            pf.goods_total = float(request.POST.get('goods_total') or 0)
        except (ValueError, TypeError):
            pf.goods_total = 0
        try:
            pf.deposit_pct = int(request.POST.get('deposit_pct') or 50)
        except (ValueError, TypeError):
            pf.deposit_pct = 50
        sh = request.POST.get('shipping', '').strip()
        pf.shipping = float(sh) if sh else None
        pf.save()
        if request.POST.get('action') == 'print':
            return redirect(f"{request.path}?print=1")
        messages.success(request, 'Pro-forma saved.')
        return redirect('proforma_invoice', pk=pk)

    goods = float(pf.goods_total)
    vat = round(goods * float(pf.vat_rate) / 100, 2)
    shipping = float(pf.shipping) if pf.shipping else 0
    total = round(goods + vat + shipping, 2)
    deposit_amount = round(goods * pf.deposit_pct / 100, 2)
    balance_amount = round(goods - deposit_amount, 2)

    return render(request, 'projects/proforma_invoice.html', {
        'project': project, 'cost': cost, 'pf': pf,
        'goods': goods, 'vat': vat, 'shipping': shipping, 'total': total,
        'deposit_amount': deposit_amount, 'balance_amount': balance_amount,
        'balance_pct': 100 - pf.deposit_pct,
        'print_mode': request.GET.get('print') == '1',
        'project_pk': project.pk if not request.GET.get('print') else None,
    })


@login_required
@require_POST
def quote_photo_attach(request, pk):
    """Upload a photo directly to a quote, or attach one from the library."""
    project = get_object_or_404(Project, pk=pk)
    quote, _ = ProjectQuote.objects.get_or_create(project=project)
    sort = quote.attached_photos.count()

    # From library?
    lib_id = request.POST.get('library_id')
    if lib_id:
        lib = get_object_or_404(QuotePhoto, pk=lib_id)
        photo = QuoteAttachedPhoto.objects.create(
            quote=quote,
            file_data=lib.file_data,
            file_mime=lib.file_mime,
            file_original_name=lib.file_original_name,
            sort_order=sort,
        )
        return JsonResponse({'ok': True, 'pk': photo.pk})

    f = request.FILES.get('file')
    if not f:
        return JsonResponse({'error': 'No file'}, status=400)
    photo = QuoteAttachedPhoto.objects.create(
        quote=quote,
        file_data=f.read(),
        file_mime=f.content_type or '',
        file_original_name=f.name,
        sort_order=sort,
    )
    return JsonResponse({'ok': True, 'pk': photo.pk, 'url': f'/quote/attached-photo/{photo.pk}/file/', 'size': photo.size})


@login_required
@require_POST
def quote_attached_photo_update(request, pk):
    photo = get_object_or_404(QuoteAttachedPhoto, pk=pk)
    data = json.loads(request.body)
    if 'size' in data and data['size'] in ('small', 'medium', 'large'):
        photo.size = data['size']
    if 'sort_order' in data:
        try:
            photo.sort_order = int(data['sort_order'])
        except (ValueError, TypeError):
            pass
    photo.save()
    return JsonResponse({'ok': True})


@login_required
@require_POST
def quote_attached_photo_delete(request, pk):
    get_object_or_404(QuoteAttachedPhoto, pk=pk).delete()
    return JsonResponse({'ok': True})


@login_required
def quote_attached_photo_file(request, pk):
    from django.http import HttpResponse
    photo = get_object_or_404(QuoteAttachedPhoto, pk=pk)
    if photo.file_data:
        resp = HttpResponse(bytes(photo.file_data), content_type=photo.file_mime or 'image/jpeg')
        resp['Content-Disposition'] = f'inline; filename="{photo.file_original_name or "photo.jpg"}"'
        return resp
    return HttpResponse('Not found', status=404)


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
        # 4. Boards/decks (e.g. 48X27, 48X27MFC — digit-starting codes)
        if c[0].isdigit():
            return (4, c)
        # 5. Hanging rails (HRS, JWGR)
        if c.startswith('HRS') or c.startswith('JWGR'):
            return (5, c)
        # 6. Everything else (fixings, accessories, rods, etc.)
        return (6, c)

    # Enrich stock items with descriptions and stock levels, in category order
    stock_items = []
    for code, qty in sorted(ref.items(), key=lambda x: picking_sort_key(x[0])):
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
    from .longspan_data import ls_explode_frame, ls_explode_shelf, ls_explode_trolley
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
                PickingListItem.objects.create(
                    picking_list=pl, item_type='stock',
                    product=prod, quantity=qty, sort_order=sort
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
@require_POST
def cost_wall_fixings_save(request, pk):
    cost = get_object_or_404(ProjectCost, project__pk=pk)
    data = json.loads(request.body)
    cost.wall_fixings = max(0, int(data.get('wall_fixings', 0)))
    cost.save()
    return JsonResponse({'ok': True, 'wall_fixings': cost.wall_fixings})


# ── Fitting Notes Library ─────────────────────────────────────────────────────

@login_required
def fitting_notes_list(request):
    notes = FittingNote.objects.prefetch_related('products', 'templates').all()
    products = Product.objects.filter(is_active=True).order_by('code')
    templates = PickingTemplate.objects.all()
    return render(request, 'projects/fitting_notes_list.html', {
        'notes': notes, 'products': products, 'templates': templates,
    })


@login_required
@require_POST
def fitting_note_upload(request):
    title = request.POST.get('title', '').strip()
    f = request.FILES.get('file')
    product_ids = request.POST.getlist('product_ids')
    if not f:
        return JsonResponse({'error': 'File required'}, status=400)
    if not title:
        title = f.name
    mime = f.content_type or ''
    note = FittingNote.objects.create(
        title=title,
        file_data=f.read(),
        file_mime=mime,
        file_original_name=f.name,
        notes=request.POST.get('notes', '').strip(),
        uploaded_by=request.user,
    )
    if product_ids:
        note.products.set(Product.objects.filter(pk__in=product_ids))
    template_ids = request.POST.getlist('template_ids')
    if template_ids:
        note.templates.set(PickingTemplate.objects.filter(pk__in=template_ids))
    return JsonResponse({'ok': True, 'pk': note.pk})


@login_required
@require_POST
def fitting_note_delete(request, pk):
    note = get_object_or_404(FittingNote, pk=pk)
    note.delete()
    return JsonResponse({'ok': True})


@login_required
@require_POST
def fitting_note_update(request, pk):
    note = get_object_or_404(FittingNote, pk=pk)
    data = json.loads(request.body)
    if 'title' in data:
        note.title = data['title'].strip() or note.title
    if 'notes' in data:
        note.notes = data['notes'].strip()
    if 'template_ids' in data:
        note.templates.set(PickingTemplate.objects.filter(pk__in=data['template_ids']))
    note.save()
    return JsonResponse({'ok': True})


@login_required
def quote_photos_list(request):
    photos = QuotePhoto.objects.all()
    products = Product.objects.filter(is_active=True).order_by('code')
    return render(request, 'projects/quote_photos_list.html', {'photos': photos, 'products': products})


@login_required
@require_POST
def quote_photo_upload(request):
    title = request.POST.get('title', '').strip()
    f = request.FILES.get('file')
    if not f:
        return JsonResponse({'error': 'Photo required'}, status=400)
    if not title:
        title = f.name
    photo = QuotePhoto.objects.create(
        title=title,
        codes=request.POST.get('codes', '').strip(),
        file_data=f.read(),
        file_mime=f.content_type or '',
        file_original_name=f.name,
        uploaded_by=request.user,
    )
    return JsonResponse({'ok': True, 'pk': photo.pk})


@login_required
@require_POST
def quote_photo_update(request, pk):
    photo = get_object_or_404(QuotePhoto, pk=pk)
    data = json.loads(request.body)
    if 'codes' in data:
        photo.codes = data['codes'].strip()
    if 'title' in data:
        photo.title = data['title'].strip()
    photo.save()
    return JsonResponse({'ok': True})


@login_required
@require_POST
def quote_photo_delete(request, pk):
    get_object_or_404(QuotePhoto, pk=pk).delete()
    return JsonResponse({'ok': True})


@login_required
def quote_photo_file(request, pk):
    from django.http import HttpResponse
    photo = get_object_or_404(QuotePhoto, pk=pk)
    if photo.file_data:
        resp = HttpResponse(bytes(photo.file_data), content_type=photo.file_mime or 'image/jpeg')
        resp['Content-Disposition'] = f'inline; filename="{photo.file_original_name or photo.title}"'
        return resp
    return HttpResponse('Not found', status=404)


@login_required
def fitting_note_download(request, pk):
    note = get_object_or_404(FittingNote, pk=pk)
    if note.file_data:
        from django.http import HttpResponse
        response = HttpResponse(bytes(note.file_data), content_type=note.file_mime or 'application/octet-stream')
        response['Content-Disposition'] = f'inline; filename="{note.file_original_name or note.title}"'
        return response
    from django.http import HttpResponse
    return HttpResponse('File not found', status=404)


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


def _can_edit_prices(user):
    """Only Tasos and Martin Chapman can edit the price list."""
    if user.is_superuser:
        return True
    full = (user.get_full_name() or '').lower()
    uname = (user.username or '').lower()
    allowed_names = ['martin chapman', 'tasos', 'tasos bogiatzis']
    allowed_users = ['martin', 'mchapman', 'martin.chapman', 'tasos', 'tbogiatzis']
    return full in allowed_names or uname in allowed_users


@login_required
def price_list(request):
    # Seed component prices on first load, or migrate from old formats
    old_format = PriceListItem.objects.filter(
        category__in=['ls_frame','ls_shelf','ls_trolley','Posts (TP)','Connectors (TPC)','TFCV Connectors','TFCL Beams','Mesh Panels']
    ).exists() or PriceListItem.objects.filter(code='HRT25').exists() \
      or PriceListItem.objects.filter(category__in=['Hanging Rails','Inboard Hanging Rail Set','25mm Inboard Hanging Rail Tube']).exists()
    has_trimline = PriceListItem.objects.filter(product_line='trimline', category='Shelf Bar').exists()
    has_longspan = PriceListItem.objects.filter(product_line='longspan').exists()
    if not PriceListItem.objects.exists() or old_format or not has_trimline or not has_longspan:
        from django.db import transaction
        with transaction.atomic():
            PriceListItem.objects.all().delete()
            _seed_price_list()

    can_edit = _can_edit_prices(request.user)
    # Preserve category display order using first-seen order by pk
    from collections import OrderedDict
    items = PriceListItem.objects.all().order_by('pk')
    trimline = OrderedDict()
    longspan = OrderedDict()
    for item in items:
        target = longspan if item.product_line == 'longspan' else trimline
        target.setdefault(item.category, []).append(item)
    # Sort items within each category by sort_order
    for groups in (trimline, longspan):
        for cat in groups:
            groups[cat].sort(key=lambda x: x.sort_order)

    # Board materials (£/sqft) — chipboard (15mm), melamine, 18mm chipboard
    mat_defaults = {'chipboard': 0.50, 'melamine': 0.75, 'chipboard_18mm': 0.75}
    mat_labels = {'chipboard': 'Chipboard (15mm)', 'melamine': 'Melamine', 'chipboard_18mm': 'Chipboard (18mm)'}
    for key, default in mat_defaults.items():
        if not MaterialPrice.objects.filter(name=key).exists():
            MaterialPrice.objects.create(name=key, price_per_sqft=default)
    board_materials = [
        {'name': key, 'label': mat_labels[key],
         'price': float(MaterialPrice.objects.get(name=key).price_per_sqft)}
        for key in ['chipboard', 'chipboard_18mm', 'melamine']
    ]

    return render(request, 'projects/price_list.html', {
        'can_edit': can_edit,
        'trimline_groups': trimline,
        'longspan_groups': longspan,
        'board_materials': board_materials,
    })


def _seed_price_list():
    """Seed component-level prices for Trimline and Longspan from the data tables."""
    from .longspan_data import (LS_FRAME_PRICES, LS_FRAME_HEIGHTS, LS_FRAME_DEPTHS)

    # Weights (kg each) keyed by code
    W = {
        'TP48':2.0,'TP60':2.3,'TP72':2.7,'TP84':3.2,'TP96':3.6,'TP108':4.1,'TP120':4.5,'TP144':5.0,
        'TPC12':0.5,'TPC15':0.5,'TPC18':0.6,'TPC21':0.8,'TPC24':0.9,'TPC27':1.0,'TPC30':1.2,'TPC36':1.3,
        'TFCV24':1.4,'TFCV30':1.4,'TFCV36':1.5,'TFCV39.5':1.6,'TFCV43.5':1.8,'TFCV48':2.0,
        'TWB36':2.2,'TWB48':3.2,'TWB60':3.8,'TWB72':4.6,
        'TCTB12':0.6,'TCTB15':0.6,'TCTB18':0.6,'TCTB24':0.8,'TCTB30':1.0,'TCTB36':1.2,
        'SB18':0.6,'SB24':0.8,'SB27':1.0,'SB30':1.0,'SB36':1.2,
        'TSR':0.0,'TPS':0.1,'TCLC':0.0,'FP':0.1,'TTC':0.0,'TFP':0.01,
        'STM':0.0,'DTM':0.0,'SM':0.12,
    }
    def w(code): return W.get(code)

    # ── TRIMLINE components (in display order) ──
    sort = 0
    for code, price in POSTS.items():
        PriceListItem.objects.create(product_line='trimline', category='Posts',
            code=code, label=code, price=price, weight=w(code), sort_order=sort); sort += 1
    sort = 0
    for code, price in TPCS.items():
        PriceListItem.objects.create(product_line='trimline', category='Post Connectors',
            code=code, label=code, price=price, weight=w(code), sort_order=sort); sort += 1
    sort = 0
    for wd, price in TFCV_CONN.items():
        PriceListItem.objects.create(product_line='trimline', category='TFCV Beams',
            code=f'TFCV{wd}', label=f'TFCV {wd}"', price=price, weight=w(f'TFCV{wd}'), sort_order=sort); sort += 1
    sort = 0
    for wd, price in TWB_PRICES.items():
        PriceListItem.objects.create(product_line='trimline', category='TWB Beams',
            code=f'TWB{wd}', label=f'TWB {wd}"', price=price, weight=w(f'TWB{wd}'), sort_order=sort); sort += 1
    # Inboard Hanging Rail Support (a set = a pair of these + a 25mm tube)
    sort = 0
    for d, price in IN_HANG_RAILS.items():
        PriceListItem.objects.create(product_line='trimline', category='Inboard Hanging Rail Support',
            code=f'HRS{d}', label=f'Inboard hanging rail support {d}"', price=price, sort_order=sort); sort += 1
    # 25mm Inboard Hanging Rail Tube
    PriceListItem.objects.create(product_line='trimline', category='Inboard Hanging Rail Support',
        code='25MMFLOCOATTUBE1MM', label='25mm inboard hanging rail tube', price=0, sort_order=sort)
    # Combination Tie Bars
    tctb = {'TCTB12':1.62,'TCTB15':1.84,'TCTB18':2.38,'TCTB24':2.99,'TCTB30':3.56,'TCTB36':4.20}
    sort = 0
    for code, price in tctb.items():
        PriceListItem.objects.create(product_line='trimline', category='Combination Tie Bars',
            code=code, label=code, price=price, weight=w(code), sort_order=sort); sort += 1
    # Shelf Bar
    sb = {'SB18':1.62,'SB24':2.16,'SB27':2.70,'SB30':2.70,'SB36':3.18}
    sort = 0
    for code, price in sb.items():
        PriceListItem.objects.create(product_line='trimline', category='Shelf Bar',
            code=code, label=code, price=price, weight=w(code), sort_order=sort); sort += 1
    # Fittings & Misc
    misc = [
        ('TSR','Shelf clips',0.43),('TPS','Post splice',0.49),('TCLC','Connector locking clip',0.25),
        ('FP','Floor plate metal',0.75),('TTC','Top cap',0.27),('TFP','Floor plate plastic',0.27),
        ('STM','SGL mount foot',1.20),('STM-SHIM','Shim to suit (SGL)',0.56),
        ('DTM','DBL mount foot',1.31),('DTM-SHIM','Shim to suit (DBL)',0.56),
        ('SM','Side mount foot',1.05),('SM-SHIM','Shim to suit (side)',0.51),
    ]
    sort = 0
    for code, label, price in misc:
        PriceListItem.objects.create(product_line='trimline', category='Fittings & Misc',
            code=code, label=label, price=price, weight=w(code), sort_order=sort); sort += 1

    # ── LONGSPAN components ──
    # Posts by height
    ls_post_prices = {2000:7.5, 2500:9.0, 3000:10.5, 3500:12.0, 4000:13.5, 4500:15.0, 5000:16.5}
    sort = 0
    for h in LS_FRAME_HEIGHTS:
        PriceListItem.objects.create(product_line='longspan', category='Posts',
            code=f'LSP{h}', label=f'Post LSP{h}', price=ls_post_prices.get(h,0), sort_order=sort); sort += 1
    # Horizontal braces by depth
    ls_horiz = {600:'LSHB565', 900:'LSHB865', 1000:'LSHB965', 1200:'LSHB1165'}
    horiz_price = {600:3.0, 900:3.8, 1000:4.0, 1200:4.5}
    sort = 0
    for d, code in ls_horiz.items():
        PriceListItem.objects.create(product_line='longspan', category='Horizontal Braces',
            code=code, label=f'{code} ({d}D)', price=horiz_price.get(d,0), sort_order=sort); sort += 1
    # Diagonal braces by depth (galv)
    ls_diag = {600:'LSDB835-G', 900:'LSDB1058-G', 1000:'LSDB1294', 1200:'LSDB1312-G'}
    diag_price = {600:3.5, 900:4.2, 1000:4.6, 1200:5.0}
    sort = 0
    for d, code in ls_diag.items():
        PriceListItem.objects.create(product_line='longspan', category='Diagonal Braces',
            code=code, label=f'{code} ({d}D)', price=diag_price.get(d,0), sort_order=sort); sort += 1
    # Beams by width
    ls_beams = {950:4.52, 1150:5.31, 1500:6.78, 1800:8.38, 1850:8.96, 2250:10.76, 2400:11.42, 2700:12.78}
    sort = 0
    for w, price in ls_beams.items():
        PriceListItem.objects.create(product_line='longspan', category='Beams (pair)',
            code=f'LSB{w}', label=f'LSB{w}', price=price, sort_order=sort); sort += 1
    # Chipboard supports by depth
    ls_cbs = {600:'LSCB600', 900:'LSCB900', 1000:'LSCB1000', 1200:'LSCB1200'}
    cbs_price = {600:0.63, 900:0.913, 1000:1.009, 1200:1.2}
    sort = 0
    for d, code in ls_cbs.items():
        PriceListItem.objects.create(product_line='longspan', category='Chipboard Supports',
            code=code, label=f'{code} ({d}D)', price=cbs_price.get(d,0), sort_order=sort); sort += 1
    # Castor / Trolley
    castor = [('LSCASTBRKT600','Castor bracket 600',3.37),('LSCASTBRKT900','Castor bracket 900',4.46),
              ('LSCASTBRKTS','Castor bracket 1200',6.43),('CASTOR3','Castor wheel',4.76)]
    sort = 0
    for code, label, price in castor:
        PriceListItem.objects.create(product_line='longspan', category='Castor / Trolley',
            code=code, label=label, price=price, sort_order=sort); sort += 1
    # Fixings & Misc
    fixed = [('LSFT','Foot',0.5),('LSP','Spacer',0.3),('LSLP','Locking pin',0.05),
             ('M8X35HEXHD','M8x35 bolt set',0.2),('M8X65HEXHD','M8x65 bolt set',0.25),
             ('LSTC','Top cap',0.4)]
    sort = 0
    for code, label, price in fixed:
        PriceListItem.objects.create(product_line='longspan', category='Fixings & Misc',
            code=code, label=label, price=price, sort_order=sort); sort += 1


@login_required
@require_POST
def price_list_update(request):
    if not _can_edit_prices(request.user):
        return JsonResponse({'error': 'You do not have permission to edit prices.'}, status=403)
    data = json.loads(request.body)
    # Material price update (board materials)
    if data.get('material'):
        mat = get_object_or_404(MaterialPrice, name=data['material'])
        try:
            mat.price_per_sqft = float(data.get('price', 0) or 0)
            mat.save()
        except (ValueError, TypeError):
            return JsonResponse({'error': 'Invalid price'}, status=400)
        return JsonResponse({'ok': True, 'price': float(mat.price_per_sqft)})
    # Component price update
    item = get_object_or_404(PriceListItem, pk=data.get('pk'))
    if 'weight' in data:
        wv = str(data.get('weight', '')).strip()
        item.weight = float(wv) if wv else None
        item.save(update_fields=['weight', 'updated_at'])
        return JsonResponse({'ok': True, 'weight': float(item.weight) if item.weight is not None else None})
    try:
        item.price = float(data.get('price', 0) or 0)
        item.save(update_fields=['price', 'updated_at'])
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Invalid price'}, status=400)
    return JsonResponse({'ok': True, 'price': float(item.price)})


def ls_component_prices():
    """Return {code: price} for all longspan components from the editable price list."""
    return {item.code: float(item.price) for item in PriceListItem.objects.filter(product_line='longspan')}


def ls_calc_frame_price(height, depth):
    """Frame price = sum of its exploded component prices."""
    from .longspan_data import ls_explode_frame, LS_FRAME_PRICES
    comp = ls_component_prices()
    if not comp:
        return LS_FRAME_PRICES.get(height, {}).get(depth, 0)
    total = 0
    for code, qty in ls_explode_frame(height, depth, 1).items():
        total += comp.get(code, 0) * qty
    return round(total, 2)


def ls_calc_shelf_price(width, depth):
    """Shelf level price = sum of exploded component prices (beams, pins, board, CBS).
    The chipboard board is priced from the 18mm chipboard £/sqft rate by its area."""
    from .longspan_data import ls_explode_shelf, LS_SHELF, ls_board_code
    comp = ls_component_prices()
    if not comp:
        info = LS_SHELF.get(f'{width}x{depth}')
        return info['price'] if info else 0
    # 18mm chipboard rate
    mat = {m.name: float(m.price_per_sqft) for m in MaterialPrice.objects.all()}
    chip18 = mat.get('chipboard_18mm', mat.get('melamine', 0.75))
    board_code = ls_board_code(width, depth)
    total = 0
    for code, qty in ls_explode_shelf(width, depth, 1).items():
        if code == board_code or code[0].isdigit():
            # Board — price by area from 18mm chipboard rate.
            # Parse mm dims from the code (e.g. 1495X895)
            import re as _re_b
            m = _re_b.match(r'(\d+)X(\d+)', code)
            if m:
                sqft = (int(m.group(1)) * int(m.group(2))) / 92903.04
                total += sqft * chip18 * qty
            continue
        total += comp.get(code, 0) * qty
    return round(total, 2)


def ls_calc_trolley_price(depth):
    from .longspan_data import ls_explode_trolley, LS_TROLLEY_COST
    comp = ls_component_prices()
    if not comp:
        return LS_TROLLEY_COST.get(depth, 0)
    total = 0
    for code, qty in ls_explode_trolley(depth, 1).items():
        price = comp.get(code)
        if price is None:
            prod = Product.objects.filter(code__iexact=code).first()
            price = float(prod.cost_price) if prod and prod.cost_price else 0
        total += price * qty
    return round(total, 2)


def create_superuser_once(request):
    from django.http import HttpResponse
    from django.contrib.auth.models import User
    from django.middleware.csrf import get_token
    if User.objects.filter(is_superuser=True).exists():
        return HttpResponse("Superuser already exists.", status=403)
    token = get_token(request)
    if request.method == 'POST':
        email = request.POST.get('email','').strip()
        password = request.POST.get('password','').strip()
        if email and password:
            try:
                user = User.objects.filter(username=email).first()
                if user:
                    user.is_active = True
                    user.is_staff = True
                    user.is_superuser = True
                    user.set_password(password)
                    user.save()
                else:
                    parts = email.split('@')[0].split('.')
                    user = User.objects.create_superuser(
                        username=email,
                        email=email,
                        password=password,
                        first_name=parts[0].title() if parts else '',
                        last_name=parts[1].title() if len(parts)>1 else '',
                    )
                return HttpResponse(f"<h2>✅ Done! <a href='/login/'>Click here to log in</a></h2>")
            except Exception as e:
                return HttpResponse(f"<h2>Error: {e}</h2>", status=500)
    html = f"""<!DOCTYPE html><html><head><title>Setup</title>
    <style>body{{font-family:Arial;background:#1a1a1a;color:#fff;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
    .b{{background:#2a2a2a;border:1px solid #444;border-radius:10px;padding:2rem;width:340px}}
    h2{{color:#f97316;margin:0 0 1rem}}input{{width:100%;padding:.6rem;margin-bottom:.8rem;border:1px solid #555;border-radius:6px;background:#333;color:#fff;font-size:.95rem;box-sizing:border-box}}
    button{{width:100%;padding:.7rem;background:#f97316;border:none;border-radius:6px;color:#fff;font-weight:700;cursor:pointer;font-size:1rem}}</style>
    </head><body><div class="b"><h2>⚙ Create Admin Account</h2>
    <form method="post"><input type="hidden" name="csrfmiddlewaretoken" value="{token}">
    <input type="email" name="email" placeholder="Your email" required>
    <input type="password" name="password" placeholder="Password" required>
    <button type="submit">Create</button></form>
    <p style="font-size:.75rem;color:#888;margin-top:.8rem">⚠ Disables after first use</p>
    </div></body></html>"""
    return HttpResponse(html)


# ── Reminders ─────────────────────────────────────────────────────────────────

@login_required
@require_POST
def reminder_add(request, pk):
    project = get_object_or_404(Project, pk=pk)
    data = json.loads(request.body)
    user_ids  = data.get('user_ids') or ([data['user_id']] if data.get('user_id') else [])
    notify_all = data.get('all', False)
    message   = data.get('message', '').strip()
    remind_at = data.get('remind_at', '')
    if (not user_ids and not notify_all) or not remind_at:
        return JsonResponse({'error': 'Missing fields'}, status=400)
    from django.utils.dateparse import parse_datetime
    from django.utils import timezone
    dt = parse_datetime(remind_at)
    if not dt:
        return JsonResponse({'error': 'Invalid date'}, status=400)
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)

    if notify_all:
        recipients = list(User.objects.filter(is_active=True))
    else:
        recipients = list(User.objects.filter(pk__in=user_ids))
    if not recipients:
        return JsonResponse({'error': 'No valid recipients'}, status=400)

    created = []
    for user in recipients:
        r = Reminder.objects.create(
            project=project, notify_user=user,
            created_by=request.user,
            message=message or f"Reminder: {project.project_name}",
            remind_at=dt,
        )
        created.append({'id': r.pk, 'user': user.get_full_name() or user.username})

    return JsonResponse({'ok': True,
        'remind_at': dt.strftime('%d %b %Y, %H:%M'),
        'message': message or f"Reminder: {project.project_name}",
        'recipients': created,
        'count': len(created),
    })


@login_required
@require_POST
def reminder_delete(request, pk):
    r = get_object_or_404(Reminder, pk=pk)
    r.delete()
    return JsonResponse({'ok': True})


@login_required
@require_POST
def reminder_dismiss(request, pk):
    r = get_object_or_404(Reminder, pk=pk, notify_user=request.user)
    r.dismissed = True
    r.save(update_fields=['dismissed'])
    return JsonResponse({'ok': True})


@login_required
def reminders_list(request, pk):
    project = get_object_or_404(Project, pk=pk)
    reminders = project.reminders.select_related('notify_user').filter(sent=False)
    return JsonResponse([{
        'id': r.pk,
        'message': r.message,
        'remind_at': r.remind_at.strftime('%d %b %Y, %H:%M'),
        'user': r.notify_user.get_full_name() or r.notify_user.username,
        'user_id': r.notify_user.pk,
    } for r in reminders], safe=False)


@login_required
def check_reminders(request):
    """Called periodically by the frontend — fires due reminders as notifications."""
    from django.utils import timezone
    now = timezone.now()
    due = Reminder.objects.filter(
        notify_user=request.user, sent=False, remind_at__lte=now
    ).select_related('project')
    fired = []
    for r in due:
        Notification.objects.create(
            user=r.notify_user,
            type='reminder',
            text=r.message,
            link=f'/project/{r.project.pk}/edit/',
        )
        r.sent = True
        r.save()
        fired.append({'id': r.pk, 'message': r.message, 'link': f'/project/{r.project.pk}/edit/'})
    return JsonResponse({'fired': fired})


@login_required
def satisfaction_note(request, pk):
    """Generate a client satisfaction note as a printable HTML page."""
    project = get_object_or_404(Project, pk=pk)
    report = getattr(project, 'install_report', None)
    try:
        customer_profile = CustomerProfile.objects.get(name__iexact=project.customer)
    except CustomerProfile.DoesNotExist:
        customer_profile = None
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


@login_required
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
@require_POST
def cost_extras_save(request, pk):
    """Save back-to-back fixings and mobile base sets counts."""
    cost = get_object_or_404(ProjectCost, project__pk=pk)
    data = json.loads(request.body)
    if 'back_to_back' in data:
        cost.back_to_back_fixings = max(0, int(data['back_to_back'] or 0))
    if 'mobile_bases' in data:
        cost.mobile_base_sets = max(0, int(data['mobile_bases'] or 0))
    cost.save()
    return JsonResponse({'ok': True})
