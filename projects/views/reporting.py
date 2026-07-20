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
from .utils import (_calc_sell_price)


# ── Auth ──────────────────────────────────────────────────────────────────────

from django.contrib.auth.views import (
    PasswordResetView, PasswordResetDoneView,
    PasswordResetConfirmView, PasswordResetCompleteView,
)

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
                'payment': p.get_payment_method_display() if p.payment_method else '—',
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
            'payment': p.get_payment_method_display() if p.payment_method else '—',
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
def activity_log(request):
    logs = ProjectLog.objects.select_related('project','user').order_by('-timestamp')[:200]
    return render(request, 'projects/activity_log.html', {'logs': logs})




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

