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
from .utils import (_initials)


# ── Auth ──────────────────────────────────────────────────────────────────────

from django.contrib.auth.views import (
    PasswordResetView, PasswordResetDoneView,
    PasswordResetConfirmView, PasswordResetCompleteView,
)

@login_required
def notification_count(request):
    # Excludes type='message' from the bell's count entirely - a new message
    # is now indicated on the EZR Chat / Private Chat nav buttons themselves,
    # not via the bell or the notifications list, so it isn't shown twice.
    notif_count = Notification.objects.filter(user=request.user, read=False).exclude(type='message').count()
    msg_count   = Message.objects.filter(recipient=request.user, read=False, is_deleted=False).count()
    latest_notif = Notification.objects.filter(user=request.user, read=False).exclude(type='message').order_by('-timestamp').first()
    latest_msg = Message.objects.filter(recipient=request.user, read=False, is_deleted=False).order_by('-timestamp').select_related('sender').first()
    latest_team_pk = TeamMessage.objects.order_by('-pk').values_list('pk', flat=True).first() or 0
    return JsonResponse({
        'count': notif_count, 'messages': msg_count,
        'latest_notif': {'id': latest_notif.id, 'text': latest_notif.text, 'link': latest_notif.link or '/notifications/'} if latest_notif else None,
        'latest_msg': {
            'id': latest_msg.id,
            'text': latest_msg.text[:120],
            'sender': latest_msg.sender.get_full_name() or latest_msg.sender.username,
            'link': f'/messages/{latest_msg.sender_id}/',
        } if latest_msg else None,
        'latest_team_pk': latest_team_pk,
    })




@login_required
def notifications_view(request):
    notes = Notification.objects.filter(user=request.user).exclude(type='message').order_by('-timestamp')[:50]
    Notification.objects.filter(user=request.user, read=False).exclude(type='message').update(read=True)
    return render(request, 'projects/notifications.html', {'notifications': notes})


@login_required
@require_POST
def notification_dismiss(request, pk):
    # Scoped to request.user so nobody can dismiss someone else's notification
    # by guessing a pk.
    Notification.objects.filter(pk=pk, user=request.user).delete()
    return JsonResponse({'ok': True})


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
        unread = Message.objects.filter(sender=u, recipient=request.user, read=False, is_deleted=False).count()
        conversations.append({'user': u, 'last_msg': last_msg, 'unread': unread})

    conversations.sort(key=lambda x: x['last_msg'].timestamp if x['last_msg'] else date.today(), reverse=True)
    all_users = User.objects.filter(is_active=True).exclude(id=request.user.id).order_by('first_name', 'last_name')
    total_unread = Message.objects.filter(recipient=request.user, read=False, is_deleted=False).count()

    return render(request, 'projects/inbox.html', {
        'conversations': conversations,
        'all_users': all_users,
        'total_unread': total_unread,
    })




@login_required
def chat_widget(request):
    """A bare, standalone page combining private Messages and EZR Chat
    behind two toggle buttons — no sidebar, no nav — meant to be
    installed as its own app/window."""
    from django.db.models import Q as _Q
    users_messaged = User.objects.filter(
        _Q(sent_messages__recipient=request.user) |
        _Q(received_messages__sender=request.user)
    ).distinct().exclude(id=request.user.id)

    conversations = []
    for u in users_messaged:
        last_msg = Message.objects.filter(
            _Q(sender=request.user, recipient=u) |
            _Q(sender=u, recipient=request.user)
        ).order_by('-timestamp').first()
        unread = Message.objects.filter(sender=u, recipient=request.user, read=False, is_deleted=False).count()
        conversations.append({'user': u, 'last_msg': last_msg, 'unread': unread})
    conversations.sort(key=lambda x: x['last_msg'].timestamp if x['last_msg'] else date.today(), reverse=True)

    all_users = User.objects.filter(is_active=True).exclude(id=request.user.id).order_by('first_name', 'last_name')
    total_unread_private = Message.objects.filter(recipient=request.user, read=False, is_deleted=False).count()

    team_messages = TeamMessage.objects.select_related('user').order_by('-timestamp')[:100]
    team_messages = list(reversed(list(team_messages)))
    latest_team_pk = TeamMessage.objects.order_by('-pk').values_list('pk', flat=True).first() or 0

    return render(request, 'projects/chat_widget.html', {
        'conversations': conversations,
        'all_users': all_users,
        'total_unread_private': total_unread_private,
        'team_messages': team_messages,
        'latest_team_pk': latest_team_pk,
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
                **_attach_json(m, f'/messages/photo/{m.pk}/'),
                'thumbs_up_count': m.thumbs_up_by.count(),
                'i_reacted': m.thumbs_up_by.filter(pk=request.user.pk).exists(),
                'sender': m.sender.get_full_name() or m.sender.username,
                'timestamp': timezone.localtime(m.timestamp).strftime('%d %b %Y, %H:%M'),
                'is_me': m.sender_id == request.user.id,
                'deleted': m.is_deleted,
                'quote': None if m.is_deleted else _quote_json(m, request.user),
            }
            for m in new_msgs
        ],
        'deleted_ids': _recent_deleted_ids(Message, Q(sender=request.user, recipient=other) | Q(sender=other, recipient=request.user)),
    })



MAX_PDF_BYTES = 15 * 1024 * 1024


def _attach_upload(msg, upload):
    """Store an uploaded image or PDF on a (Team)Message. Returns an error
    string, or None if fine."""
    data = upload.read()
    mime = (upload.content_type or '').lower()
    name = (upload.name or '')[:200]
    is_pdf = mime == 'application/pdf' or name.lower().endswith('.pdf')
    if is_pdf:
        if not data.startswith(b'%PDF'):
            return "That doesn't look like a valid PDF."
        if len(data) > MAX_PDF_BYTES:
            return 'PDF is too large (15 MB maximum).'
        msg.photo_data = data
        msg.photo_mime = 'application/pdf'
        msg.file_name = name or 'document.pdf'
        return None
    if not mime.startswith('image/'):
        return 'Only photos and PDF files can be attached.'
    msg.photo_data = data
    msg.photo_mime = mime or 'image/jpeg'
    msg.file_name = ''
    return None


def _quote_json(msg, me):
    q = msg.quote_info
    if q:
        q = dict(q, is_me=(q['author_id'] == me.pk))
    return q


def _attach_json(msg, url):
    has = bool(msg.photo_data)
    return {
        'has_photo': has,
        'photo_url': url if has else None,
        'is_pdf': has and msg.photo_mime == 'application/pdf',
        'file_name': msg.file_name if has else '',
    }


def _serve_attachment(msg):
    if not msg.photo_data:
        return HttpResponse(status=404)
    resp = HttpResponse(bytes(msg.photo_data), content_type=msg.photo_mime or 'image/jpeg')
    if msg.photo_mime == 'application/pdf':
        safe = (msg.file_name or 'document.pdf').replace('"', '').replace('\n', ' ')
        resp['Content-Disposition'] = f'inline; filename="{safe}"'
    return resp



def _recent_deleted_ids(model, flt):
    """pks of deleted messages among the 300 most recent in this thread, so
    open pages can swap them for the 'deleted' placeholder live."""
    recent = list(model.objects.filter(flt).order_by('-pk').values_list('pk', 'is_deleted')[:300])
    return [pk for pk, d in recent if d]


@login_required
@require_POST
def message_delete(request, pk):
    """Sender erases their own private message for good; a placeholder remains."""
    msg = get_object_or_404(Message, pk=pk, sender=request.user)
    msg.text = ''
    msg.photo_data = None
    msg.photo_mime = ''
    msg.file_name = ''
    msg.is_deleted = True
    msg.reply_to = None
    msg.read = True
    msg.save()
    msg.thumbs_up_by.clear()
    Notification.objects.filter(
        user=msg.recipient, type='message', link=f"/messages/{request.user.pk}/", read=False
    ).delete()
    return JsonResponse({'ok': True, 'id': msg.pk})


@login_required
@require_POST
def team_message_delete(request, pk):
    """Author erases their own EZR Chat message for good; a placeholder remains."""
    msg = get_object_or_404(TeamMessage, pk=pk, user=request.user)
    msg.text = ''
    msg.photo_data = None
    msg.photo_mime = ''
    msg.file_name = ''
    msg.is_deleted = True
    msg.reply_to = None
    msg.save()
    msg.thumbs_up_by.clear()
    return JsonResponse({'ok': True, 'id': msg.pk})


@login_required
def message_photo(request, pk):
    """Serve a photo attached to a private message. Scoped to sender or
    recipient only, so nobody can view a photo from a conversation they're
    not part of by guessing a pk."""
    msg = get_object_or_404(Message, pk=pk)
    if request.user.id not in (msg.sender_id, msg.recipient_id):
        return HttpResponse(status=404)
    return _serve_attachment(msg)


@login_required
@require_POST
def message_react(request, pk):
    """Toggle the current user's thumbs-up on a private message. Scoped to
    sender or recipient only, matching message_photo's own scoping."""
    msg = get_object_or_404(Message, pk=pk, is_deleted=False)
    if request.user.id not in (msg.sender_id, msg.recipient_id):
        return HttpResponse(status=404)
    if msg.thumbs_up_by.filter(pk=request.user.pk).exists():
        msg.thumbs_up_by.remove(request.user)
        reacted = False
    else:
        msg.thumbs_up_by.add(request.user)
        reacted = True
    return JsonResponse({'reacted': reacted, 'count': msg.thumbs_up_by.count()})


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
        photo = request.FILES.get('photo')
        if photo:
            text = request.POST.get('text', '').strip()
            reply_id = request.POST.get('reply_to')
        else:
            data = json.loads(request.body)
            text = data.get('text', '').strip()
            reply_id = data.get('reply_to')
        if text or photo:
            msg = Message(sender=request.user, recipient=other, text=text)
            if reply_id:
                try:
                    msg.reply_to = Message.objects.filter(
                        Q(sender=request.user, recipient=other) | Q(sender=other, recipient=request.user),
                        pk=int(reply_id)).first()
                except (ValueError, TypeError):
                    pass
            if photo:
                err = _attach_upload(msg, photo)
                if err:
                    return JsonResponse({'error': err}, status=400)
            msg.save()
            # Create notification for recipient
            sender_name = request.user.get_full_name() or request.user.username
            notif_text = f"{sender_name} sent you a message: {text[:80]}" if text else f"{sender_name} sent you {'a PDF' if msg.photo_mime == 'application/pdf' else 'a photo'}"
            Notification.objects.create(
                user=other, type='message',
                text=notif_text,
                link=f"/messages/{request.user.pk}/",
            )
            return JsonResponse({
                'id': msg.pk, 'text': msg.text,
                **_attach_json(msg, f'/messages/photo/{msg.pk}/'),
                'thumbs_up_count': 0, 'i_reacted': False,
                'quote': _quote_json(msg, request.user),
                'sender': sender_name,
                'timestamp': timezone.localtime(msg.timestamp).strftime('%d %b %Y, %H:%M'),
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
        unread = Message.objects.filter(sender=u, recipient=request.user, read=False, is_deleted=False).count()
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
        'total_unread': Message.objects.filter(recipient=request.user, read=False, is_deleted=False).count(),
    })




@login_required
def team_chat(request):
    if request.method == 'POST':
        import re
        photo = request.FILES.get('photo')
        if photo:
            text = request.POST.get('text', '').strip()
            reply_id = request.POST.get('reply_to')
        else:
            data = json.loads(request.body)
            text = data.get('text', '').strip()
            reply_id = data.get('reply_to')
        if not text and not photo:
            return JsonResponse({'error': 'Empty'}, status=400)
        # Render mentions/#project-links to their final display form before
        # storing, so historic, freshly-sent, and polled messages all show
        # identically (previously this stored unprintable placeholder bytes).
        display_text = re.sub(r'@\[([^\]]+)\]\(\d+\)', r'@\1', text)
        display_text = re.sub(r'#\[([^\]]+)\]\((\d+)\)', r'<a href="/project/\2/edit/" style="color:var(--acc);font-weight:600">#\1</a>', display_text)
        msg = TeamMessage(user=request.user, text=display_text)
        if reply_id:
            try:
                msg.reply_to = TeamMessage.objects.filter(pk=int(reply_id)).first()
            except (ValueError, TypeError):
                pass
        if photo:
            err = _attach_upload(msg, photo)
            if err:
                return JsonResponse({'error': err}, status=400)
        msg.save()
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
            **_attach_json(msg, f'/chat/photo/{msg.pk}/'),
            'thumbs_up_count': 0, 'i_reacted': False,
            'quote': _quote_json(msg, request.user),
            'user': sender_name,
            'initials': _initials(sender_name),
            'timestamp': timezone.localtime(msg.timestamp).strftime('%d %b %Y, %H:%M'),
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
def team_message_photo(request, pk):
    """Serve a photo attached to a team chat message - visible to any
    logged-in staff member, matching team chat's own shared-channel nature."""
    msg = get_object_or_404(TeamMessage, pk=pk)
    return _serve_attachment(msg)


@login_required
@require_POST
def team_message_react(request, pk):
    """Toggle the current user's thumbs-up on a team chat message - any
    logged-in staff member can react, matching team chat's shared nature."""
    msg = get_object_or_404(TeamMessage, pk=pk, is_deleted=False)
    if msg.thumbs_up_by.filter(pk=request.user.pk).exists():
        msg.thumbs_up_by.remove(request.user)
        reacted = False
    else:
        msg.thumbs_up_by.add(request.user)
        reacted = True
    return JsonResponse({'reacted': reacted, 'count': msg.thumbs_up_by.count()})


@login_required
def team_chat_poll(request):
    """Return team chat messages newer than `after` for live updates."""
    after = request.GET.get('after', 0)
    try:
        after = int(after)
    except (ValueError, TypeError):
        after = 0
    new_msgs = TeamMessage.objects.select_related('user', 'reply_to', 'reply_to__user').filter(pk__gt=after).order_by('timestamp')
    return JsonResponse({
        'messages': [
            {
                'id': m.pk,
                'text': m.text,
                **_attach_json(m, f'/chat/photo/{m.pk}/'),
                'thumbs_up_count': m.thumbs_up_by.count(),
                'i_reacted': m.thumbs_up_by.filter(pk=request.user.pk).exists(),
                'user': m.user.get_full_name() or m.user.username,
                'user_id': m.user_id,
                'initials': _initials(m.user.get_full_name() or m.user.username),
                'timestamp': timezone.localtime(m.timestamp).strftime('%d %b %Y, %H:%M'),
                'is_me': m.user_id == request.user.id,
                'deleted': m.is_deleted,
                'quote': None if m.is_deleted else _quote_json(m, request.user),
            }
            for m in new_msgs
        ],
        'deleted_ids': _recent_deleted_ids(TeamMessage, Q()),
    })


