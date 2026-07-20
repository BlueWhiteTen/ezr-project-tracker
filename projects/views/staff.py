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

