import json
from datetime import datetime
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from datetime import timedelta, time as dtime
from django.utils import timezone
from ..models import CalendarEvent, Reminder


def _can_change(user, ev):
    return user.is_superuser or ev.created_by_id == user.pk


def _parse_time(v):
    v = (v or '').strip()
    if not v:
        return None
    return datetime.strptime(v, '%H:%M').time()


@login_required
@require_POST
def calendar_event_save(request, pk=None):
    """Create (no pk) or edit (pk) a calendar event. Only the creator can edit."""
    try:
        data = json.loads(request.body or '{}')
    except ValueError:
        return JsonResponse({'ok': False, 'error': 'Bad request'}, status=400)
    ev = get_object_or_404(CalendarEvent, pk=pk) if pk else CalendarEvent(created_by=request.user)
    if pk and not _can_change(request.user, ev):
        return JsonResponse({'ok': False, 'error': 'Only the person who created this event can change it.'}, status=403)
    title = (data.get('title') or '').strip()
    if not title:
        return JsonResponse({'ok': False, 'error': 'Please give the event a title.'}, status=400)
    try:
        day = datetime.strptime((data.get('date') or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'ok': False, 'error': 'Please choose a date.'}, status=400)
    all_day = bool(data.get('all_day'))
    try:
        start, end = _parse_time(data.get('start_time')), _parse_time(data.get('end_time'))
    except ValueError:
        return JsonResponse({'ok': False, 'error': 'Times must look like 14:30.'}, status=400)
    if all_day:
        start = end = None
    else:
        if not start:
            return JsonResponse({'ok': False, 'error': 'Choose a start time, or tick All day.'}, status=400)
        if end and end <= start:
            return JsonResponse({'ok': False, 'error': 'The end time must be after the start time.'}, status=400)
    kind = data.get('kind') if data.get('kind') in dict(CalendarEvent.KIND_CHOICES) else 'meeting'
    vis = data.get('visibility') if data.get('visibility') in dict(CalendarEvent.VISIBILITY_CHOICES) else 'private'
    ev.title, ev.kind, ev.date, ev.all_day = title[:200], kind, day, all_day
    ev.start_time, ev.end_time = start, end
    ev.location = (data.get('location') or '').strip()[:300]
    ev.notes = (data.get('notes') or '').strip()
    ev.visibility = vis
    remind = data.get('remind') or ''
    if remind not in dict(CalendarEvent.REMIND_CHOICES):
        remind = ''
    ev.remind = remind
    # work out when the reminder fires (all-day events count as 09:00)
    remind_at = None
    if remind:
        if remind == 'morning':
            base = datetime.combine(ev.date, dtime(8, 0))
        else:
            base = datetime.combine(ev.date, ev.start_time or dtime(9, 0)) - timedelta(minutes=int(remind))
        remind_at = timezone.make_aware(base)
        if remind_at <= timezone.now() and not pk:
            return JsonResponse({'ok': False, 'error': 'That reminder time has already passed - pick a shorter lead time.'}, status=400)
    ev.save()
    ev.reminders.all().delete()
    if remind_at and remind_at > timezone.now() and ev.created_by_id:
        when = 'all day' if ev.all_day else ev.start_time.strftime('%H:%M')
        Reminder.objects.create(event=ev, project=None, notify_user=ev.created_by, created_by=ev.created_by,
                                message=f'{ev.title} ({ev.date.strftime("%d %b")}, {when})', remind_at=remind_at)
    return JsonResponse({'ok': True, 'id': ev.pk})


@login_required
@require_POST
def calendar_event_delete(request, pk):
    ev = get_object_or_404(CalendarEvent, pk=pk)
    if not _can_change(request.user, ev):
        return JsonResponse({'ok': False, 'error': 'Only the person who created this event can delete it.'}, status=403)
    ev.delete()
    return JsonResponse({'ok': True})
