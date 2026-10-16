import json
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from ..models import Feedback, Notification


def _is_admin(user):
    return user.is_superuser or user.is_staff


@login_required
@require_POST
def feedback_add(request):
    """Save an idea / problem / question sent from the floating Feedback button."""
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Bad request'}, status=400)
    message = (data.get('message') or '').strip()
    if not message:
        return JsonResponse({'ok': False, 'error': 'Please write something first.'}, status=400)
    kind = data.get('kind') if data.get('kind') in dict(Feedback.KINDS) else 'idea'
    fb = Feedback.objects.create(user=request.user, kind=kind, message=message[:3000], page=(data.get('page') or '')[:300])
    name = request.user.get_full_name() or request.user.username
    for admin in User.objects.filter(is_active=True, is_superuser=True).exclude(pk=request.user.pk):
        Notification.objects.create(user=admin, type='tag', text=f"{name} sent feedback ({fb.get_kind_display().lower()}): {message[:80]}", link='/feedback/')
    return JsonResponse({'ok': True, 'id': fb.pk})


@login_required
def feedback_list(request):
    admin = _is_admin(request.user)
    qs = Feedback.objects.select_related('user')
    if not admin:
        qs = qs.filter(user=request.user)
    status = request.GET.get('status', '')
    if status in dict(Feedback.STATUSES):
        qs = qs.filter(status=status)
    return render(request, 'projects/feedback_list.html', {
        'items': qs, 'is_admin': admin, 'statuses': Feedback.STATUSES, 'status': status,
    })


@login_required
@require_POST
def feedback_update(request, pk):
    """Admins: set the status and an optional short reply."""
    if not _is_admin(request.user):
        return JsonResponse({'ok': False}, status=403)
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False}, status=400)
    fb = Feedback.objects.filter(pk=pk).first()
    if not fb:
        return JsonResponse({'ok': False}, status=404)
    if data.get('status') in dict(Feedback.STATUSES):
        fb.status = data['status']
    if 'reply' in data:
        fb.reply = (data.get('reply') or '').strip()[:1000]
    fb.save()
    if fb.user_id and fb.user_id != request.user.pk and (data.get('status') or data.get('reply')):
        Notification.objects.create(user=fb.user, type='tag', text=f"Your feedback was updated: {fb.get_status_display()} — {fb.message[:60]}", link='/feedback/')
    return JsonResponse({'ok': True, 'status': fb.status, 'label': fb.get_status_display()})
