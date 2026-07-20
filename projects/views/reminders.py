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
        'remind_at': timezone.localtime(r.remind_at).strftime('%d %b %Y, %H:%M'),
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


