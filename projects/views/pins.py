from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..models import Project, PinnedProject, Comment, ProjectLog


@login_required
@require_POST
def pin_toggle(request, pk):
    """Pin / unpin a project for the current user only."""
    project = get_object_or_404(Project, pk=pk)
    obj, created = PinnedProject.objects.get_or_create(user=request.user, project=project)
    if not created:
        obj.delete()
    return JsonResponse({'ok': True, 'pinned': created})


def _name(u):
    if not u:
        return 'Someone'
    return u.get_full_name() or u.username


@login_required
def project_activity(request, pk):
    """Comments + change history for one project, newest first (for the side panel)."""
    project = get_object_or_404(Project, pk=pk)
    items = []
    for c in project.comments.select_related('user').order_by('-timestamp')[:60]:
        items.append({'kind': 'comment', 'user': _name(c.user), 'user_id': c.user_id,
                      'text': c.text, 'ts': c.timestamp})
    for l in project.logs.select_related('user').order_by('-timestamp')[:60]:
        if l.field == 'Project created':
            text = 'created the project'
        elif l.old_value or l.new_value:
            text = 'changed %s: %s → %s' % (l.field, (l.old_value or '—')[:60], (l.new_value or '—')[:60])
        else:
            text = l.field
        items.append({'kind': 'change', 'user': _name(l.user), 'user_id': l.user_id,
                      'text': text, 'ts': l.timestamp})
    items.sort(key=lambda i: i['ts'], reverse=True)
    items = items[:80]
    out = []
    for i in items:
        i['when'] = timezone.localtime(i.pop('ts')).strftime('%d %b %H:%M')
        out.append(i)
    return JsonResponse({'items': out})
