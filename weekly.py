"""Builds the 'This week' summary used by the Home box and the Monday chat post."""
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.db.models import Q

from .models import Project, LeaveRequest, Reminder, TodoItem

ACTIVE = ('order_received', 'processed', 'part_delivered')


def _label(p):
    return f"#{p.project_number} {p.project_name}" if p.project_number else p.project_name


def weekly_summary(today=None):
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=4)
    week_end = monday + timedelta(days=6)

    live = Project.objects.exclude(status__in=['completed', 'cancelled'])
    days = []
    for i in range(5):
        d = monday + timedelta(days=i)
        deliveries = [p for p in live.filter(delivery_required=True, delivery_date=d)]
        installs = [p for p in live.filter(installation_required=True, installation_same_as_delivery=False,
                                            installation_date_type='exact', installation_date=d)]
        installs += [p for p in live.filter(installation_required=True, installation_same_as_delivery=True,
                                             delivery_date=d) if p not in deliveries]
        off = LeaveRequest.objects.filter(date=d).select_related('user')
        days.append({
            'date': d, 'is_today': d == today,
            'deliveries': deliveries, 'installs': installs, 'off': list(off),
            'empty': not (deliveries or installs or off),
        })

    week_projects = {p.pk: p for d in days for p in d['deliveries'] + d['installs']}
    unbooked = []
    for d in days:
        for p in d['deliveries']:
            if not p.delivery_booked:
                unbooked.append((p, 'delivery', d['date']))
        for p in d['installs']:
            if not p.installation_booked:
                unbooked.append((p, 'install', d['date']))

    overdue = []
    for p in live.filter(status__in=ACTIVE):
        if p.delivery_required and p.delivery_date and p.delivery_date < monday and p.status == 'order_received':
            overdue.append((p, 'delivery was due', p.delivery_date))
        inst = p.get_effective_installation_date() if p.installation_required and p.installation_date_type == 'exact' else None
        if inst and inst < monday:
            overdue.append((p, 'install was due', inst))
    overdue.sort(key=lambda x: x[2])

    blocked = list(live.filter(is_blocked=True)[:8])
    chase = list(Project.objects.filter(status='quoted', updated_at__date__lte=today - timedelta(days=10)).order_by('updated_at')[:8])
    new_last_week = Project.objects.filter(created_at__date__gte=monday - timedelta(days=7), created_at__date__lt=monday).count()
    nxt = [p for p in live.filter(Q(delivery_required=True, delivery_date__gt=week_end, delivery_date__lte=week_end + timedelta(days=7))
                                  | Q(installation_required=True, installation_date_type='exact',
                                      installation_date__gt=week_end, installation_date__lte=week_end + timedelta(days=7)))[:8]]
    return {
        'monday': monday, 'friday': friday, 'days': days,
        'unbooked': unbooked, 'overdue': overdue[:8], 'blocked': blocked, 'chase': chase,
        'new_last_week': new_last_week, 'next_week': nxt,
        'busy': bool(week_projects),
    }


def summary_text(s):
    """Plain-text version, for posting in EZR Chat."""
    L = [f"📅 This week ({s['monday']:%d %b} – {s['friday']:%d %b})"]
    for d in s['days']:
        bits = []
        bits += [f"Delivery: {_label(p)}" for p in d['deliveries']]
        bits += [f"Install: {_label(p)}" for p in d['installs']]
        bits += [f"{(o.user.get_full_name() or o.user.username)} off" + ('' if o.half_day == 'full' else f" ({o.get_half_day_display().lower()})") for o in d['off']]
        L.append(f"{d['date']:%a %d}: " + ('; '.join(bits) if bits else 'nothing booked'))
    if s['unbooked']:
        L.append("⚠ Not yet booked: " + '; '.join(f"{_label(p)} ({k}, {dt:%a})" for p, k, dt in s['unbooked']))
    if s['overdue']:
        L.append("⏰ Overdue: " + '; '.join(f"{_label(p)} ({what} {dt:%d %b})" for p, what, dt in s['overdue']))
    if s['blocked']:
        L.append("🚧 Blocked: " + '; '.join(_label(p) for p in s['blocked']))
    if s['chase']:
        L.append("📨 Quotes to chase: " + '; '.join(_label(p) for p in s['chase']))
    if s['next_week']:
        L.append("Coming next week: " + '; '.join(_label(p) for p in s['next_week']))
    return '\n'.join(L)
