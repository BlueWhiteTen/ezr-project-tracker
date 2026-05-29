from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Q, F as models_F, Count
from django.views.decorators.http import require_POST
from datetime import date, timedelta
import json

from .models import Project, ProjectLog, Customer, Comment, Message, Notification, TeamMessage, StaffProfile, LeaveRequest, InstallationReport, ReportPhoto, SatisfactionNote, CustomerProfile, ProjectDocument, Product, PickingList, PickingListItem, PickingTemplate, PickingTemplateItem, MaterialPrice, ProjectCost, ProjectCostLine, UprightAccessory, AccessoryOverride, Reminder
from .forms import RegisterForm, ProjectForm


# ── Auth ──────────────────────────────────────────────────────────────────────

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
def dashboard(request):
    query           = request.GET.get('q', '')
    status          = request.GET.get('status', '')
    tl              = request.GET.get('tl', '')
    sort            = request.GET.get('sort', '-created_at')
    stat_filter     = request.GET.get('sf', '')
    assigned_filter = request.GET.get('assigned', '')

    projects = Project.objects.select_related('assigned_to', 'last_edited_by')

    if query:
        projects = projects.filter(
            Q(project_name__icontains=query) |
            Q(customer__icontains=query) |
            Q(location__icontains=query) |
            Q(sales_order__icontains=query)
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
               '-created_at','created_at']
    if sort not in allowed:
        sort = '-created_at'

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
        else:
            projects = list(projects.order_by(sort))

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
        'overdue':      sum(1 for p in Project.objects.all() if p.is_overdue()),
    }

    all_users = User.objects.filter(is_active=True).order_by('first_name','last_name')
    return render(request, 'projects/dashboard.html', {
        'projects': projects, 'status_choices': Project.STATUS_CHOICES,
        'selected_status': status, 'query': query, 'sort': sort,
        'tl': tl, 'stat_filter': stat_filter, 'counts': counts,
        'assigned_filter': assigned_filter, 'all_users': all_users,
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


@login_required
def project_create(request):
    if request.method == 'POST':
        form = ProjectForm(request.POST)
        if form.is_valid():
            p = form.save(commit=False)
            cust = form.cleaned_data.get('customer','').strip().title()
            loc  = form.cleaned_data.get('location','').strip().title()
            p.customer = cust
            p.location = loc
            p.project_name = f"{cust} — {loc}" if loc else cust or 'New Project'
            p.created_by = request.user
            p.last_edited_by = request.user
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
        customer = data.get('customer','').strip().title()
        location = data.get('location','').strip().title()
        if not customer:
            return JsonResponse({'error': 'Customer is required'}, status=400)
        name = f"{customer} — {location}" if location else customer
        kwargs = dict(project_name=name, customer=customer, location=location,
                      created_by=request.user, last_edited_by=request.user)
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
            edited = form.save(commit=False)
            edited.last_edited_by = request.user
            cust = form.cleaned_data.get('customer','').strip().title()
            loc  = form.cleaned_data.get('location','').strip().title()
            edited.customer = cust
            edited.location = loc
            edited.project_name = f"{cust} — {loc}" if loc else cust or 'New Project'
            edited.save()
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
    return render(request, 'projects/project_form.html', {
        'form': form, 'action': 'Edit', 'project': project, 'logs': logs,
        'comments': comments, 'staff_users': staff_users,
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
    old_status = p.get_status_display()
    p.status = new_status
    p.last_edited_by = request.user
    p.save()
    ProjectLog.objects.create(
        project=p, user=request.user,
        field='Status', old_value=old_status, new_value=p.get_status_display()
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
    year = int(request.GET.get('year', date.today().year))
    data = []
    for m in range(1, 13):
        start = date(year, m, 1)
        if m == 12:
            end = date(year+1, 1, 1)
        else:
            end = date(year, m+1, 1)
        delivered  = Project.objects.filter(delivery_date__gte=start, delivery_date__lt=end, status__in=['delivered','installed','completed']).count()
        installed  = Project.objects.filter(installation_date__gte=start, installation_date__lt=end, status__in=['installed','completed']).count()
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
    year  = int(request.GET.get('year',  date.today().year))
    month = int(request.GET.get('month', date.today().month))

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
        if p.delivery_required and p.delivery_date:
            k = p.delivery_date.isoformat()
            event_map.setdefault(k, []).append({
                'pk': p.pk, 'name': p.project_name, 'customer': p.customer,
                'location': p.location, 'type': 'delivery',
                'status_label': p.get_status_display(),
            })
        inst_date = p.get_effective_installation_date()
        if p.installation_required and inst_date:
            k = inst_date.isoformat()
            event_map.setdefault(k, []).append({
                'pk': p.pk, 'name': p.project_name, 'customer': p.customer,
                'location': p.location, 'type': 'install',
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

    # Add leave data to calendar
    leave_qs = LeaveRequest.objects.filter(date__gte=start, date__lte=end).select_related('user')
    for l in leave_qs:
        k = l.date.isoformat()
        event_map.setdefault(k, []).append({
            'pk': None, 'name': f"{l.user.get_full_name() or l.user.username} — {l.get_half_day_display()}",
            'customer': '', 'location': '', 'type': 'leave',
            'status_label': l.get_half_day_display(),
        })

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
        results = list(Project.objects.filter(sales_order__icontains=q).values('id','project_name','customer','sales_order','status')[:10])
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
    clean_text = _re2.sub("@\[([^\]]+)\]\(\d+\)", "@\1", text)
    clean_text = _re2.sub("#\[([^\]]+)\]\((\d+)\)", "#\1", clean_text)
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
        # Clean display text for storage
        import re as _re
        display_text = _re.sub(r'@\[([^\]]+)\]\(\d+\)', r'@', text)
        display_text = _re.sub(r'#\[([^\]]+)\]\((\d+)\)', r'#LINK::', display_text)
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

        # Handle #project mentions — create notifications for all online users? Just return link
        display_text = re.sub(r'@\[([^\]]+)\]\(\d+\)', r'@\1', text)
        display_text = re.sub(r'#\[([^\]]+)\]\((\d+)\)', r'<a href="/project/\2/edit/" style="color:var(--acc);font-weight:600">#\1</a>', display_text)

        return JsonResponse({
            'id': msg.pk,
            'text': display_text,
            'raw_text': text,
            'user': sender_name,
            'initials': sender_name[:2].upper(),
            'timestamp': msg.timestamp.strftime('%d %b %Y, %H:%M'),
            'is_me': True,
        })

    messages_qs = TeamMessage.objects.select_related('user').order_by('-timestamp')[:100]
    messages_qs = list(reversed(list(messages_qs)))
    all_users = User.objects.filter(is_active=True).order_by('first_name', 'last_name')
    return render(request, 'projects/team_chat.html', {
        'messages_qs': messages_qs,
        'all_users': all_users,
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
        profile.role  = request.POST.get('role', '').strip()
        profile.phone = request.POST.get('phone', '').strip()
        profile.bio   = request.POST.get('bio', '').strip()
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
    year  = int(request.GET.get('year',  today.year))
    month = int(request.GET.get('month', today.month))

    first_day = date(year, month, 1)
    last_day  = date(year, month, cal_mod.monthrange(year, month)[1])
    # Pad to Monday
    start = first_day - timedelta(days=first_day.weekday())
    end_raw = last_day + timedelta(days=(6 - last_day.weekday()))
    end = end_raw if (end_raw - start).days >= 27 else end_raw + timedelta(weeks=1)

    leave_qs = LeaveRequest.objects.filter(date__gte=start, date__lte=end).select_related('user').order_by('date','user__first_name')

    # Build leave map: date -> list of leave entries
    leave_map = {}
    for l in leave_qs:
        k = l.date.isoformat()
        leave_map.setdefault(k, []).append({
            'id': l.pk,
            'name': l.user.get_full_name() or l.user.username,
            'half_day': l.get_half_day_display(),
            'user_pk': l.user.pk,
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
            ReportPhoto.objects.create(report=report, image=photo, caption=request.POST.get('photo_caption', ''))

        # Handle satisfaction note upload
        if 'satisfaction_note' in request.FILES:
            SatisfactionNote.objects.create(
                report=report,
                file=request.FILES['satisfaction_note'],
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
    })


@login_required
@require_POST
def delete_report_photo(request, pk):
    photo = get_object_or_404(ReportPhoto, pk=pk)
    photo.image.delete()
    photo.delete()
    return JsonResponse({'ok': True})


@login_required
@require_POST
def delete_satisfaction_note(request, pk):
    note = get_object_or_404(SatisfactionNote, pk=pk)
    note.file.delete()
    note.delete()
    return JsonResponse({'ok': True})


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
        customer.name         = request.POST.get('name', '').strip()
        customer.contact_name = request.POST.get('contact_name', '').strip()
        customer.email        = request.POST.get('email', '').strip()
        customer.phone        = request.POST.get('phone', '').strip()
        customer.address      = request.POST.get('address', '').strip()
        customer.notes           = request.POST.get('notes', '').strip()
        customer.important_notes = request.POST.get('important_notes', '').strip()
        customer.save()
        messages.success(request, 'Customer updated.')
        return redirect('customer_detail', pk=pk)
    projects = Project.objects.filter(
        customer__iexact=customer.name
    ).order_by('-created_at')
    return render(request, 'projects/customer_detail.html', {
        'customer': customer, 'projects': projects,
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
def stock_adjust(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST':
        data = json.loads(request.body)
        if data.get('delete'):
            product.delete()
            return JsonResponse({'ok': True, 'deleted': True})
        product.code        = data.get('code', product.code).strip()
        product.description = data.get('description', product.description).strip()
        product.quantity    = data.get('quantity', product.quantity)
        product.reorder_level = data.get('reorder_level', product.reorder_level)
        product.save()
        return JsonResponse({'ok': True, 'deleted': False})
    return JsonResponse({'id': product.pk, 'code': product.code, 'description': product.description,
                         'quantity': float(product.quantity), 'reorder_level': float(product.reorder_level)})


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
    # Get customer important notes
    customer_notes = None
    if project.customer:
        cp = CustomerProfile.objects.filter(name__iexact=project.customer).first()
        if cp and cp.important_notes:
            customer_notes = cp.important_notes
    return render(request, 'projects/picking_list.html', {
        'project': project, 'pl': pl, 'items': items,
        'templates': templates, 'customer_notes': customer_notes,
    })


@login_required
@require_POST
def picking_list_create(request, project_pk):
    project = get_object_or_404(Project, pk=project_pk)
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

    # Save items
    items_data = data.get('items', [])
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
        from django.utils import timezone
        pl.dispatched_at = timezone.now()
        for item in pl.items.all():
            item.product.quantity -= item.quantity
            item.product.save()

    pl.save()
    return JsonResponse({'ok': True, 'status': pl.get_status_display()})


@login_required
@require_POST
def picking_list_delete(request, pk):
    pl = get_object_or_404(PickingList, pk=pk)
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


@login_required
@require_POST
def picking_item_add(request, pk):
    pl = get_object_or_404(PickingList, pk=pk)
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
def picking_item_add_msg(request, pk):
    pl = get_object_or_404(PickingList, pk=pk)
    data = json.loads(request.body)
    msg = data.get('message', '').strip()
    if not msg:
        return JsonResponse({'error': 'Message required'}, status=400)
    count = pl.items.count()
    item = PickingListItem.objects.create(
        picking_list=pl, item_type='message',
        message=msg, quantity=0, sort_order=count
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
    return render(request, 'projects/picking_list_print.html', {
        'project': project, 'pl': pl, 'items': items,
        'customer_notes': customer_notes,
    })


@login_required
@require_POST
def picking_item_qty(request, pk):
    item = get_object_or_404(PickingListItem, pk=pk)
    data = json.loads(request.body)
    qty = float(data.get('quantity', item.quantity))
    if qty > 0:
        item.quantity = qty
        item.save()
    return JsonResponse({'ok': True, 'quantity': float(item.quantity)})


# ── Picking list generation from costing ─────────────────────────────────────

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

def generate_picking_reference(lines, selected_accessories=None, wall_fixings=0):
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
            size_parts = [p.strip() for p in line.size.split(' x ')] if line.size else []
            if len(size_parts) == 3:
                stype, w, d = size_parts
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
            size_parts = line.size.split(' x ') if line.size else []
            if len(size_parts) == 2:
                btype, w = size_parts
                if btype == 'tfcv':
                    items[f'TFCV{w}'] += qty
                elif btype == 'twb':
                    items[f'TWB{w}'] += qty

        elif line.line_type == 'stock' and line.product:
            items[line.product.code] += qty
    # Accessories
    for acc in selected_accessories:
        code = acc.code if hasattr(acc, 'code') else acc.get('code','')
        n_uprights = acc.get('uprights', 0) if isinstance(acc, dict) else 0
        if code == 'TTC':
            items['TTC'] += n_uprights
        elif code == 'SMFOOT':
            for fix_code, qty_per in SM_FOOT_FIXINGS.items():
                items[fix_code] += n_uprights * qty_per

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

# Mesh panels, load signs, top ties
MESH_PANEL_PRICE = 25.0
LOAD_SIGNS = {'Laminated': 3.0, 'Foam A3': 6.0}
TOP_TIE_PRICE = 3.0

FRAME_HEIGHTS = ['48','60','72','84','96','108','120','144']
FRAME_DEPTHS  = ['12','15','18','21','24','27','30','36']
TFCV_WIDTHS   = ['24','30','36','39.5','43.5','48']
TWB_WIDTHS    = ['36','48','60','72']
SHELF_DEPTHS  = ['12','15','18','21','24','27','30','36']


def calc_frame_price(height_in, depth_in):
    """Return frame unit cost given height and depth in inches (strings)."""
    hm = HEIGHT_MAP.get(height_in)
    dc = DEPTH_MAP.get(depth_in)
    if not hm or not dc:
        return 0
    post_code, n = hm
    return round(2 * POSTS[post_code] + n * TPCS[dc], 4)


def calc_shelf_price(shelf_type, width_in, depth_in, melamine, chipboard_price, melamine_price):
    """Return shelf unit cost.
    shelf_type: 'tfcv' or 'twb'
    width/depth in inches as strings
    melamine: bool — melamine board is more expensive than chipboard
    chipboard_price/melamine_price: float per sqft
    Formula: connector_or_beam_price + (width x depth / 144) x board_price_per_sqft
    """
    w, d = float(width_in), float(depth_in)
    sqft = (w * d) / 144
    mat_price = melamine_price if melamine else chipboard_price

    if shelf_type == 'tfcv':
        conn_price = TFCV_CONN.get(width_in, 0)
        return round(sqft * mat_price + conn_price, 4)
    elif shelf_type == 'twb':
        beam_price = TWB_PRICES.get(width_in, 0)
        return round(sqft * mat_price + beam_price, 4)
    return 0


# ── Costing views ─────────────────────────────────────────────────────────────

@login_required
def project_cost(request, pk):
    project = get_object_or_404(Project, pk=pk)
    cost, _ = ProjectCost.objects.get_or_create(project=project)
    lines = cost.lines.select_related('product').all()
    mat = {m.name: float(m.price_per_sqft) for m in MaterialPrice.objects.all()}
    chipboard = mat.get('chipboard', 0.50)
    melamine_p = mat.get('melamine', 0.75)

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
                line.unit_cost = calc_shelf_price(stype, w, d, line.melamine, chipboard, melamine_p)
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
    margin = (markup_amount / sell_price * 100) if sell_price else 0

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
    picking_ref = generate_picking_reference(list(lines), acc_with_uprights, cost.wall_fixings)
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
    return render(request, 'projects/project_cost.html', {
        'project': project, 'cost': cost, 'lines': lines,
        'picking_ref': picking_ref_enriched,
        'accessories': acc_data,
        'total_uprights': total_uprights,
        'buying_total': round(buying_total, 2),
        'markup_amount': round(markup_amount, 2),
        'sell_price': round(sell_price, 2),
        'margin': round(margin, 1),
        'chipboard_price': chipboard,
        'melamine_price': melamine_p,
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
            unit_cost = calc_shelf_price('tfcv', w, d, mel, chipboard, melamine_p)
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
            unit_cost = calc_shelf_price('twb', w, d, mel, chipboard, melamine_p)
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
        unit_cost = MESH_PANEL_PRICE
        desc = 'Mesh Panel 120x48"'
        ProjectCostLine.objects.create(cost=cost, line_type='mesh', description=desc,
            quantity=qty, unit_cost=unit_cost, sort_order=sort)

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
            if btype == 'twb':
                unit_cost = TWB_PRICES.get(w, 0)
            else:
                unit_cost = TFCV_CONN.get(w, 0)
            label = 'TWB' if btype == 'twb' else 'TFCV'
            desc = f'{label} Beams {w}"'
            ProjectCostLine.objects.create(cost=cost, line_type='beams', description=desc,
                size=f'{btype} x {w}', quantity=row_qty, unit_cost=unit_cost, sort_order=sort)
            sort += 1

    elif ltype == 'extras':
        desc = data.get('description','').strip()
        unit_cost = float(data.get('unit_cost', 0))
        if desc and unit_cost >= 0:
            ProjectCostLine.objects.create(cost=cost, line_type='extras', description=desc,
                quantity=qty, unit_cost=unit_cost, sort_order=sort)

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
        line.melamine = data['melamine']
        parts = line.size.split(' x ')
        if len(parts) == 3:
            stype, w, d = parts
            line.unit_cost = calc_shelf_price(stype, w, d, line.melamine, chipboard, melamine_p)
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
def project_cost_print(request, pk):
    project = get_object_or_404(Project, pk=pk)
    cost, _ = ProjectCost.objects.get_or_create(project=project)
    lines = cost.lines.select_related('product').all()
    buying_total = sum(l.line_total for l in lines)
    markup_amount = buying_total * float(cost.markup) / 100
    sell_price = buying_total + markup_amount + float(cost.labour) + float(cost.delivery)
    margin = (markup_amount / sell_price * 100) if sell_price else 0
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
    eff_qty = override_qty if override_qty is not None else total_uprights
    is_checked = acc in cost.accessories.all()
    total_cost = round(float(acc.unit_price) * eff_qty, 2) if is_checked else 0
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
        customer        = original.customer,
        location        = original.location,
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
def customer_quote(request, pk):
    project = get_object_or_404(Project, pk=pk)
    cost, _ = ProjectCost.objects.get_or_create(project=project)
    lines = cost.lines.select_related('product').all()
    buying_total = sum(l.line_total for l in lines)
    uprights = sum(int(float(l.quantity))*2 for l in lines if l.line_type=='frame')
    for acc in cost.accessories.all():
        buying_total += float(acc.unit_price) * uprights
    markup_amount = buying_total * float(cost.markup) / 100
    sell_price = buying_total + markup_amount + float(cost.labour) + float(cost.delivery)
    # Group lines by type for display — show description + qty, no prices
    display_lines = []
    for line in lines:
        display_lines.append({
            'description': line.description,
            'quantity': int(float(line.quantity)),
            'line_type': line.line_type,
            'type_label': line.get_line_type_display(),
        })
    # Add accessories
    for acc in cost.accessories.all():
        display_lines.append({
            'description': acc.name,
            'quantity': uprights,
            'line_type': 'stock',
            'type_label': 'Accessory',
        })
    return render(request, 'projects/customer_quote.html', {
        'project': project,
        'cost': cost,
        'sell_price': round(sell_price, 2),
        'labour': float(cost.labour),
        'delivery': float(cost.delivery),
        'display_lines': display_lines,
        'today': __import__('datetime').date.today(),
    })


@login_required
@require_POST
def picking_from_costing(request, pk):
    """Generate a picking list preview from costing lines. Does NOT save yet —
    returns the proposed items as JSON for the user to review and edit."""
    project = get_object_or_404(Project, pk=pk)
    try:
        cost = project.cost
    except ProjectCost.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'No costing found for this project'})

    lines = cost.lines.select_related('product').all()

    # Build reference using existing function
    uprights_count = sum(int(float(l.quantity)) * 2 for l in lines if l.line_type == 'frame')
    acc_with_uprights = [
        {'code': acc.code, 'uprights': uprights_count}
        for acc in cost.accessories.all()
    ]
    ref = generate_picking_reference(list(lines), acc_with_uprights, cost.wall_fixings)

    # Add NS/extras lines from costing as NS picking items
    # Note: inhang/outhang are handled by generate_picking_reference into stock codes
    ns_lines = []
    for line in lines:
        if line.line_type in ('ns', 'extras', 'wipe', 'mesh', 'loadsign', 'toptie'):
            ns_lines.append({
                'type': 'ns',
                'description': line.description,
                'quantity': int(float(line.quantity)),
            })

    # Add accessories
    uprights = sum(int(float(l.quantity)) * 2 for l in lines if l.line_type == 'frame')
    for acc in cost.accessories.all():
        ref[acc.code or acc.name] = uprights

    # Enrich stock items with descriptions and stock levels
    stock_items = []
    for code, qty in sorted(ref.items()):
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
    user_id   = data.get('user_id')
    message   = data.get('message', '').strip()
    remind_at = data.get('remind_at', '')
    if not user_id or not remind_at:
        return JsonResponse({'error': 'Missing fields'}, status=400)
    from django.utils.dateparse import parse_datetime
    from django.utils import timezone
    dt = parse_datetime(remind_at)
    if not dt:
        return JsonResponse({'error': 'Invalid date'}, status=400)
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    user = get_object_or_404(User, pk=user_id)
    r = Reminder.objects.create(
        project=project, notify_user=user,
        created_by=request.user,
        message=message or f"Reminder: {project.project_name}",
        remind_at=dt,
    )
    return JsonResponse({'ok': True, 'id': r.pk,
        'remind_at': dt.strftime('%d %b %Y, %H:%M'),
        'user': user.get_full_name() or user.username,
        'message': r.message,
    })


@login_required
@require_POST
def reminder_delete(request, pk):
    r = get_object_or_404(Reminder, pk=pk)
    r.delete()
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
