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

from ..models import Project, ProjectLog, Customer, Comment, Message, Notification, TeamMessage, StaffProfile, LeaveRequest, InstallationReport, ReportPhoto, SatisfactionNote, CustomerProfile, ProjectDocument, Product, PickingList, PickingListItem, PickingTemplate, PickingTemplateItem, MaterialPrice, ProjectCost, ProjectCostLine, UprightAccessory, AccessoryOverride, Reminder, FittingCrew, Supplier, PurchaseOrder, PurchaseOrderLine, StockMovement, FittingNote, ProjectQuote, PriceListItem, QuotePhoto, QuoteAttachedPhoto, ProformaInvoice, DeliveryPhase, ProjectPresence, ExchangeRate, GoodsInTransit, StockValuationItem, TodoItem
from ..forms import RegisterForm, ProjectForm
from .utils import (_calc_sell_price, _calc_cost_breakdown, _can_view_reports, require_feature)


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
        notify_user=request.user, dismissed=False, remind_at__lte=now
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

    todo_items = TodoItem.objects.filter(user=request.user)

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
        'todo_items': todo_items,
    })


@login_required
def todo_widget(request):
    """A bare, standalone view of just the personal to-do list — no
    sidebar, no nav — meant to be installed as its own app/window via
    the browser's 'Install' feature, rather than opened as a normal
    page inside the full site."""
    todo_items = TodoItem.objects.filter(user=request.user)
    return render(request, 'projects/todo_widget.html', {'todo_items': todo_items})


@login_required
@require_POST
def todo_add(request):
    data = json.loads(request.body)
    text = (data.get('text') or '').strip()
    if not text:
        return JsonResponse({'error': 'Text is required.'}, status=400)
    next_order = TodoItem.objects.filter(user=request.user).count()
    item = TodoItem.objects.create(user=request.user, text=text, sort_order=next_order)
    return JsonResponse({'ok': True, 'id': item.pk})


@login_required
@require_POST
def todo_toggle(request, pk):
    item = get_object_or_404(TodoItem, pk=pk, user=request.user)
    item.is_done = not item.is_done
    item.save(update_fields=['is_done'])
    return JsonResponse({'ok': True, 'is_done': item.is_done})


@login_required
@require_POST
def todo_edit(request, pk):
    item = get_object_or_404(TodoItem, pk=pk, user=request.user)
    data = json.loads(request.body)
    text = (data.get('text') or '').strip()
    if not text:
        return JsonResponse({'error': 'Text is required.'}, status=400)
    item.text = text
    item.save(update_fields=['text'])
    return JsonResponse({'ok': True, 'text': item.text})


@login_required
@require_POST
def todo_delete(request, pk):
    TodoItem.objects.filter(pk=pk, user=request.user).delete()
    return JsonResponse({'ok': True})




@login_required
@require_feature('daily_accounts_report')
def daily_accounts_report(request):
    """Printable end-of-day report for the accountant: projects that became
    Completed today (ready to invoice) and POs received today (ready to pay).
    Also supports a monthly history view."""
    if not _can_view_reports(request.user):
        messages.error(request, "You don't have access to this report. Ask an administrator to enable report access on your Staff Profile.")
        return redirect('dashboard')
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
def crew_capacity(request):
    """A calendar-style view of what each fitting crew is booked into,
    week by week — matches Project.fitting_crew (free text) against the
    FittingCrew list since there's no formal link between them."""
    week_param = request.GET.get('week')
    if week_param:
        try:
            ref_date = date.fromisoformat(week_param)
        except ValueError:
            ref_date = date.today()
    else:
        ref_date = date.today()
    week_start = ref_date - timedelta(days=ref_date.weekday())  # Monday
    week_end = week_start + timedelta(days=6)
    days = [week_start + timedelta(days=i) for i in range(7)]

    crews = list(FittingCrew.objects.all())
    reports = InstallationReport.objects.exclude(fitting_crew='').exclude(project__status='cancelled').select_related('project', 'project__customer_profile')

    # For each report in range, work out its project's effective install date
    dated_projects = []
    for rep in reports:
        p = rep.project
        eff_date = p.get_effective_installation_date()
        if eff_date and week_start <= eff_date <= week_end:
            dated_projects.append((eff_date, p, rep.fitting_crew))

    crew_rows = []
    unmatched = []
    matched_project_ids = set()
    for crew in crews:
        cells = []
        for d in days:
            day_projects = [p for (pd, p, fc) in dated_projects if pd == d and crew.name.strip().lower() in (fc or '').strip().lower()]
            for p in day_projects:
                matched_project_ids.add(p.pk)
            cells.append({'date': d, 'projects': day_projects})
        crew_rows.append({'crew': crew, 'cells': cells})

    for (pd, p, fc) in dated_projects:
        if p.pk not in matched_project_ids:
            unmatched.append((pd, p, fc))
    unmatched.sort(key=lambda t: t[0])

    return render(request, 'projects/crew_capacity.html', {
        'crew_rows': crew_rows, 'days': days, 'week_start': week_start, 'week_end': week_end,
        'prev_week': (week_start - timedelta(days=7)).isoformat(),
        'next_week': (week_start + timedelta(days=7)).isoformat(),
        'this_week': date.today().isoformat(),
        'today': date.today(), 'unmatched': unmatched,
    })




def _sales_summary_data(request):
    """Shared computation for the Sales Summary page and its CSV export."""
    from collections import defaultdict

    today = date.today()
    preset = request.GET.get('preset', 'this_month')
    from_str = request.GET.get('from', '')
    to_str = request.GET.get('to', '')

    def month_range(y, m):
        start = date(y, m, 1)
        end = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
        return start, end - timedelta(days=1)

    if preset == 'custom' and from_str and to_str:
        try:
            start = date.fromisoformat(from_str)
            end = date.fromisoformat(to_str)
        except ValueError:
            start, end = month_range(today.year, today.month)
            preset = 'this_month'
    elif preset == 'last_month':
        y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        start, end = month_range(y, m)
    elif preset == 'this_year':
        start, end = date(today.year, 1, 1), date(today.year, 12, 31)
    elif preset == 'last_year':
        start, end = date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    else:
        preset = 'this_month'
        start, end = month_range(today.year, today.month)

    # ── Jobs added: projects created in the period ──────────────────────────
    added_rows = []
    added_projects = Project.objects.filter(
        created_at__date__gte=start, created_at__date__lte=end
    ).exclude(status='cancelled').select_related()
    for p in added_projects:
        cost = p.cost
        if not cost or not cost.lines.exists():
            continue
        bd = _calc_cost_breakdown(cost)
        added_rows.append({'project': p, 'date': p.created_at.date(), **bd})

    # ── Jobs invoiced: projects that became Completed in the period ─────────
    invoiced_rows = []
    seen = set()
    completed_logs = (ProjectLog.objects
        .filter(field='Status', new_value='Completed',
                timestamp__date__gte=start, timestamp__date__lte=end)
        .select_related('project').order_by('timestamp'))
    for log in completed_logs:
        if log.project_id in seen:
            continue
        seen.add(log.project_id)
        p = log.project
        cost = p.cost
        if not cost or not cost.lines.exists():
            continue
        bd = _calc_cost_breakdown(cost)
        invoiced_rows.append({'project': p, 'date': log.timestamp.date(), **bd})

    def totals(rows):
        return {
            'count': len(rows),
            'value': round(sum(r['sell_price'] for r in rows), 2),
            'margin': round(sum(r['margin'] for r in rows), 2),
        }

    def by_customer(rows):
        agg = defaultdict(lambda: {'count': 0, 'value': 0.0, 'margin': 0.0})
        for r in rows:
            key = r['project'].customer or '—'
            agg[key]['count'] += 1
            agg[key]['value'] += r['sell_price']
            agg[key]['margin'] += r['margin']
        out = [{'customer': k, 'count': v['count'], 'value': round(v['value'], 2), 'margin': round(v['margin'], 2)}
               for k, v in agg.items()]
        out.sort(key=lambda x: x['value'], reverse=True)
        return out

    return {
        'preset': preset, 'start': start, 'end': end,
        'added_rows': added_rows, 'invoiced_rows': invoiced_rows,
        'added_totals': totals(added_rows), 'invoiced_totals': totals(invoiced_rows),
        'added_by_customer': by_customer(added_rows), 'invoiced_by_customer': by_customer(invoiced_rows),
    }


@login_required
@require_feature('sales_summary')
def sales_summary(request):
    if not _can_view_reports(request.user):
        messages.error(request, "You don't have access to this report. Ask an administrator to enable report access on your Staff Profile.")
        return redirect('dashboard')
    d = _sales_summary_data(request)
    return render(request, 'projects/sales_summary.html', d)


@login_required
@require_feature('sales_summary')
def sales_summary_export(request):
    if not _can_view_reports(request.user):
        messages.error(request, "You don't have access to this report. Ask an administrator to enable report access on your Staff Profile.")
        return redirect('dashboard')
    import csv
    d = _sales_summary_data(request)
    response = HttpResponse(content_type='text/csv')
    filename = f"sales-summary-{d['start']}-to-{d['end']}.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow([f"Sales Summary — {d['start']} to {d['end']}"])
    writer.writerow([])

    writer.writerow(['JOBS ADDED'])
    writer.writerow(['Date', 'Project', 'Customer', 'Sell Price', 'Margin', 'Margin % (ex-works)'])
    for r in d['added_rows']:
        writer.writerow([r['date'], r['project'].project_name, r['project'].customer or '',
                          r['sell_price'], r['margin'], r['margin_pct']])
    writer.writerow(['', '', 'TOTAL', d['added_totals']['value'], d['added_totals']['margin'], ''])
    writer.writerow([])

    writer.writerow(['JOBS ADDED — BY CUSTOMER'])
    writer.writerow(['Customer', 'Jobs', 'Value', 'Margin'])
    for r in d['added_by_customer']:
        writer.writerow([r['customer'], r['count'], r['value'], r['margin']])
    writer.writerow([])
    writer.writerow([])

    writer.writerow(['JOBS INVOICED (COMPLETED)'])
    writer.writerow(['Date', 'Project', 'Customer', 'Sell Price', 'Margin', 'Margin % (ex-works)'])
    for r in d['invoiced_rows']:
        writer.writerow([r['date'], r['project'].project_name, r['project'].customer or '',
                          r['sell_price'], r['margin'], r['margin_pct']])
    writer.writerow(['', '', 'TOTAL', d['invoiced_totals']['value'], d['invoiced_totals']['margin'], ''])
    writer.writerow([])

    writer.writerow(['JOBS INVOICED — BY CUSTOMER'])
    writer.writerow(['Customer', 'Jobs', 'Value', 'Margin'])
    for r in d['invoiced_by_customer']:
        writer.writerow([r['customer'], r['count'], r['value'], r['margin']])

    return response


@login_required
def monthly_summary(request):
    from django.db.models.functions import TruncMonth
    from collections import defaultdict
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

    # ── Companies by order count (no £ values) ──────────────────────────────
    co_preset = request.GET.get('co_preset', 'all_time')
    co_from = request.GET.get('co_from', '')
    co_to = request.GET.get('co_to', '')
    today = date.today()

    def month_range(y, m):
        s = date(y, m, 1)
        e = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
        return s, e - timedelta(days=1)

    co_projects = Project.objects.exclude(status='cancelled')
    if co_preset == 'custom' and co_from and co_to:
        try:
            co_start = date.fromisoformat(co_from)
            co_end = date.fromisoformat(co_to)
            co_projects = co_projects.filter(created_at__date__gte=co_start, created_at__date__lte=co_end)
        except ValueError:
            co_preset = 'all_time'
    elif co_preset == 'this_month':
        s, e = month_range(today.year, today.month)
        co_projects = co_projects.filter(created_at__date__gte=s, created_at__date__lte=e)
    elif co_preset == 'last_month':
        y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        s, e = month_range(y, m)
        co_projects = co_projects.filter(created_at__date__gte=s, created_at__date__lte=e)
    elif co_preset == 'this_year':
        co_projects = co_projects.filter(created_at__year=today.year)
    elif co_preset == 'last_year':
        co_projects = co_projects.filter(created_at__year=today.year - 1)
    # else: all_time — no date filter

    company_counts = defaultdict(int)
    for p in co_projects.values_list('customer', flat=True):
        company_counts[p or '—'] += 1
    companies = sorted(
        [{'customer': k, 'count': v} for k, v in company_counts.items()],
        key=lambda x: x['count'], reverse=True
    )

    # ── Projects by status, with the actual project list per status ────────
    status_groups = []
    for val, label in Project.STATUS_CHOICES:
        projs = Project.objects.filter(status=val).order_by('-created_at')
        status_groups.append({'value': val, 'label': label, 'count': projs.count(), 'projects': list(projs[:50])})

    # ── Stalled projects: active status, no update in N days ────────────────
    try:
        stalled_days = int(request.GET.get('stalled_days', 30))
    except (ValueError, TypeError):
        stalled_days = 30
    stalled_cutoff = timezone.now() - timedelta(days=stalled_days)
    active_statuses = ['enquiry', 'quoted', 'order_received', 'processed', 'part_delivered']
    stalled_projects = (Project.objects
        .filter(status__in=active_statuses, updated_at__lt=stalled_cutoff)
        .order_by('updated_at'))

    return render(request, 'projects/monthly_summary.html', {
        'data': data, 'year': year, 'years': years,
        'total_del': total_del, 'total_inst': total_inst, 'total_comp': total_comp,
        'companies': companies, 'co_preset': co_preset, 'co_from': co_from, 'co_to': co_to,
        'status_groups': status_groups,
        'stalled_projects': stalled_projects, 'stalled_days': stalled_days,
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
        projects = Project.objects.filter(
            Q(sales_order__icontains=q) |
            Q(project_number__icontains=q) |
            Q(project_name__icontains=q) |
            Q(customer__icontains=q) |
            Q(location__icontains=q)
        ).order_by('-project_number')[:12]
        results = [
            {'type': 'project', 'id': p.id, 'project_name': p.project_name, 'customer': p.customer,
             'sales_order': p.sales_order, 'project_number': p.project_number, 'status': p.status}
            for p in projects
        ]

        # Purchase Orders — searched directly, since a PO doesn't always have
        # a project attached (e.g. general stock replenishment), so it can't
        # always be reached by finding "its" project.
        pos = PurchaseOrder.objects.filter(po_number__icontains=q).select_related('supplier', 'project')[:8]
        results += [
            {'type': 'po', 'id': po.id, 'po_number': po.po_number, 'supplier': po.supplier.name,
             'status': po.status, 'project_id': po.project_id, 'project_name': po.project.project_name if po.project else None}
            for po in pos
        ]
    return JsonResponse(results, safe=False)


# ── Notifications ─────────────────────────────────────────────────────────────


# ── Stock Valuation ────────────────────────────────────────────────────────────

@login_required
@require_feature('stock_valuation')
def stock_valuation(request):
    """Total value of stock actually in the warehouse, plus Goods In Transit
    (ordered/invoiced but not yet received) converted to GBP at a manually
    maintained rate — replaces the old spreadsheet's stock valuation."""
    if not _can_view_reports(request.user):
        messages.error(request, "You don't have access to this report. Ask an administrator to enable report access on your Staff Profile.")
        return redirect('dashboard')

    rates = {r.currency: r.rate_to_gbp for r in ExchangeRate.objects.all()}
    for cur in ('CAD', 'EUR', 'USD'):
        rates.setdefault(cur, None)

    # Warehouse value comes from the imported spreadsheet snapshot, not live
    # Stock prices — GBP-native lines use their recorded total directly;
    # lines priced in CAD/EUR/USD (e.g. Trimline, bought from Canada) are
    # converted using the same manually-maintained rates as Goods In Transit,
    # since the spreadsheet's own £ total for those is 0/uncalculated.
    valuation_items = list(StockValuationItem.objects.all())
    warehouse_value = 0
    unconverted_count = 0
    for item in valuation_items:
        cur = item.native_currency
        qty = item.live_quantity or 0
        cost = item.native_cost or 0
        if cur == 'GBP':
            warehouse_value += qty * cost
        elif cur in rates and rates[cur]:
            warehouse_value += qty * cost * rates[cur]
        elif cur:
            unconverted_count += 1
        # items with no recorded cost in any currency contribute nothing

    git_entries = list(GoodsInTransit.objects.all().order_by('-added_at'))
    git_by_currency = {}
    for cur in ('CAD', 'EUR', 'USD'):
        entries = [g for g in git_entries if g.currency == cur]
        native_total = sum(g.value for g in entries)
        rate = rates.get(cur)
        gbp_total = (native_total * rate) if rate else None
        git_by_currency[cur] = {'entries': entries, 'native_total': native_total, 'gbp_total': gbp_total}

    git_gbp_total = sum(v['gbp_total'] for v in git_by_currency.values() if v['gbp_total'] is not None)
    grand_total = warehouse_value + git_gbp_total

    return render(request, 'projects/stock_valuation.html', {
        'warehouse_value': warehouse_value,
        'rates': rates,
        'git_by_currency': git_by_currency,
        'git_gbp_total': git_gbp_total,
        'grand_total': grand_total,
        'unconverted_count': unconverted_count,
        'item_count': len(valuation_items),
    })


@login_required
@require_POST
@require_feature('stock_valuation')
def stock_valuation_item_update(request, pk):
    item = get_object_or_404(StockValuationItem, pk=pk)
    data = json.loads(request.body)
    if 'date_changed' in data:
        item.date_changed = (data['date_changed'] or '').strip()[:60]
    if 'po_reference' in data:
        item.po_reference = (data['po_reference'] or '').strip()[:100]
    if 'linked_product_id' in data:
        pid = data['linked_product_id']
        if pid:
            product = Product.objects.filter(pk=pid).first()
            if not product:
                return JsonResponse({'error': 'Stock item not found.'}, status=400)
            item.linked_product = product
        else:
            item.linked_product = None
    if 'cost_value' in data and 'cost_currency' in data:
        currency = data['cost_currency']
        try:
            value = float(data['cost_value']) if data['cost_value'] not in ('', None) else None
        except (TypeError, ValueError):
            return JsonResponse({'error': 'Cost must be a number.'}, status=400)
        if currency not in ('GBP', 'EUR', 'CAD', 'USD'):
            return JsonResponse({'error': 'Invalid currency.'}, status=400)
        # Only one currency field is ever populated at a time — clear the
        # others so native_currency stays unambiguous.
        item.cost_gbp = value if currency == 'GBP' else None
        item.cost_eur = value if currency == 'EUR' else None
        item.cost_cad = value if currency == 'CAD' else None
        item.cost_usd = value if currency == 'USD' else None
    item.save()

    rates = {r.currency: float(r.rate_to_gbp) if r.rate_to_gbp is not None else None for r in ExchangeRate.objects.all()}
    cur = item.native_currency
    qty = float(item.live_quantity or 0)
    cost = float(item.native_cost or 0)
    if cur == 'GBP':
        computed_total = qty * cost
    elif cur and rates.get(cur):
        computed_total = qty * cost * rates[cur]
    else:
        computed_total = None

    return JsonResponse({
        'ok': True, 'live_quantity': float(qty),
        'computed_total': float(computed_total) if computed_total is not None else None,
        'linked_product': {'id': item.linked_product.id, 'code': item.linked_product.code, 'description': item.linked_product.description} if item.linked_product else None,
    })


@login_required
@require_feature('stock_valuation')
def stock_valuation_export(request):
    """Excel export of the full Stock Valuation breakdown, with real Excel
    formulas (Qty x Cost, converted at the exchange rate) rather than
    pre-computed numbers, so it recalculates if edited."""
    if not _can_view_reports(request.user):
        messages.error(request, "You don't have access to this report. Ask an administrator to enable report access on your Staff Profile.")
        return redirect('dashboard')

    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill
    from openpyxl.utils import get_column_letter

    rates = {r.currency: float(r.rate_to_gbp) if r.rate_to_gbp is not None else None for r in ExchangeRate.objects.all()}

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Stock Valuation'

    headers = ['Description', 'Linked Stock Item', 'Price Changed', 'PO Reference', 'Qty', 'Currency', 'Cost', 'Total £']
    header_fill = PatternFill(start_color='E8E8E8', end_color='E8E8E8', fill_type='solid')
    for col, h in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = Font(name='Arial', bold=True)
        cell.fill = header_fill

    items = StockValuationItem.objects.all().order_by('description')
    row = 2
    first_data_row = row
    unconverted_rows = []
    for item in items:
        cur = item.native_currency
        qty = float(item.live_quantity or 0)
        cost = float(item.native_cost) if item.native_cost is not None else None

        ws.cell(row=row, column=1, value=item.description).font = Font(name='Arial')
        ws.cell(row=row, column=2, value=f"{item.linked_product.code} — {item.linked_product.description}" if item.linked_product else '').font = Font(name='Arial')
        ws.cell(row=row, column=3, value=item.date_changed).font = Font(name='Arial')
        ws.cell(row=row, column=4, value=item.po_reference).font = Font(name='Arial')
        qty_cell = ws.cell(row=row, column=5, value=qty)
        qty_cell.font = Font(name='Arial', color='008000' if item.linked_product else '000000')
        ws.cell(row=row, column=6, value=cur or '').font = Font(name='Arial')
        cost_cell = ws.cell(row=row, column=7, value=cost)
        cost_cell.font = Font(name='Arial')
        cost_cell.number_format = '#,##0.00'

        qty_ref = f'E{row}'
        cost_ref = f'G{row}'
        total_cell = ws.cell(row=row, column=8)
        if cur == 'GBP' and cost is not None:
            total_cell.value = f'={qty_ref}*{cost_ref}'
        elif cur and cost is not None and rates.get(cur):
            # Rate is embedded as the value in effect at export time — see
            # the note below the table for why it's not a live formula.
            total_cell.value = f'={qty_ref}*{cost_ref}*{rates[cur]}'
        else:
            total_cell.value = None
            if cur:
                unconverted_rows.append(row)
        total_cell.number_format = '#,##0.00;(#,##0.00);"-"'
        total_cell.font = Font(name='Arial')
        row += 1

    last_data_row = row - 1
    total_row = row + 1
    ws.cell(row=total_row, column=7, value='Total').font = Font(name='Arial', bold=True)
    grand_total_cell = ws.cell(row=total_row, column=8, value=f'=SUM(H{first_data_row}:H{last_data_row})')
    grand_total_cell.font = Font(name='Arial', bold=True)
    grand_total_cell.number_format = '#,##0.00'

    note_row = total_row + 2
    ws.cell(row=note_row, column=1,
        value=f"Note: Total £ = Qty × Cost, converted to GBP using the exchange rate in effect on "
              f"{timezone.now().strftime('%d %b %Y')} (CAD {rates.get('CAD','not set')}, EUR {rates.get('EUR','not set')}, "
              f"USD {rates.get('USD','not set')}). The rate is a fixed number in each formula, not a live lookup — "
              f"editing Qty or Cost recalculates correctly, but the rate itself won't update unless you edit the formula."
    ).font = Font(name='Arial', italic=True, size=9, color='666666')
    if unconverted_rows:
        ws.cell(row=note_row + 1, column=1,
            value=f"{len(unconverted_rows)} row(s) have no exchange rate set and show a blank Total — see the Summary page."
        ).font = Font(name='Arial', italic=True, size=9, color='CC0000')

    widths = [34, 30, 13, 20, 9, 9, 11, 12]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = 'A2'

    from io import BytesIO
    buf = BytesIO()
    wb.save(buf)
    response = HttpResponse(buf.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="Stock-Valuation-{timezone.now().strftime("%Y-%m-%d")}.xlsx"'
    return response


@login_required
@require_feature('stock_valuation')
def stock_valuation_items(request):
    """The full itemized breakdown behind the Stock Valuation summary —
    every line from the imported spreadsheet snapshot."""
    if not _can_view_reports(request.user):
        messages.error(request, "You don't have access to this report. Ask an administrator to enable report access on your Staff Profile.")
        return redirect('dashboard')

    q = request.GET.get('q', '').strip()
    items = StockValuationItem.objects.all().order_by('description')
    if q:
        items = items.filter(Q(description__icontains=q) | Q(po_reference__icontains=q))

    from django.core.paginator import Paginator
    total_matching = items.count()
    paginator = Paginator(items, 100)
    page_num = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page_num)
    except Exception:
        page_obj = paginator.page(1)

    rates = {r.currency: r.rate_to_gbp for r in ExchangeRate.objects.all()}
    page_items = list(page_obj.object_list)
    for item in page_items:
        cur = item.native_currency
        qty = item.live_quantity or 0
        cost = item.native_cost or 0
        if cur == 'GBP':
            item.computed_total = qty * cost
        elif cur and rates.get(cur):
            item.computed_total = qty * cost * rates[cur]
        else:
            item.computed_total = None

    return render(request, 'projects/stock_valuation_items.html', {
        'items': page_items, 'page_obj': page_obj, 'total_matching': total_matching,
        'query': q,
    })


@login_required
@require_POST
@require_feature('stock_valuation')
def stock_valuation_rate_update(request):
    data = json.loads(request.body)
    for currency, rate in data.items():
        if currency not in ('CAD', 'EUR', 'USD'):
            continue
        ExchangeRate.objects.update_or_create(
            currency=currency, defaults={'rate_to_gbp': rate, 'updated_by': request.user})
    return JsonResponse({'ok': True})


@login_required
@require_POST
@require_feature('stock_valuation')
def goods_in_transit_add(request):
    data = json.loads(request.body)
    reference = (data.get('reference') or '').strip()
    currency = data.get('currency', 'CAD')
    try:
        value = float(data.get('value', 0))
    except (TypeError, ValueError):
        value = 0
    if not reference or value <= 0 or currency not in ('CAD', 'EUR', 'USD'):
        return JsonResponse({'error': 'Reference and a positive value are required.'}, status=400)
    git = GoodsInTransit.objects.create(
        reference=reference, currency=currency, value=value,
        note=(data.get('note') or '').strip(), added_by=request.user)
    return JsonResponse({'ok': True, 'id': git.id})


@login_required
@require_POST
@require_feature('stock_valuation')
def goods_in_transit_delete(request, pk):
    GoodsInTransit.objects.filter(pk=pk).delete()
    return JsonResponse({'ok': True})

