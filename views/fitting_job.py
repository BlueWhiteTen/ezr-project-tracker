import io
import json
import re

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from ..models import FittingNote, Project, ProjectFittingFile, ProjectHiddenFittingNote, ProjectLog
from ..fitting import sanitize_steps

MAX_FILE_BYTES = 15 * 1024 * 1024


@login_required
@require_POST
def fitting_steps_save(request, pk):
    project = get_object_or_404(Project, pk=pk)
    try:
        html = json.loads(request.body).get('html', '')
    except Exception:
        return JsonResponse({'ok': False}, status=400)
    clean = sanitize_steps(html)
    if clean != project.install_steps:
        project.install_steps = clean
        project.save(update_fields=['install_steps'])
        ProjectLog.objects.create(project=project, user=request.user, field='Installation steps', old_value='', new_value='Updated')
    return JsonResponse({'ok': True, 'html': clean})


@login_required
@require_POST
def fitting_note_hide(request, pk, note_pk):
    project = get_object_or_404(Project, pk=pk)
    note = get_object_or_404(FittingNote, pk=note_pk)
    ProjectHiddenFittingNote.objects.get_or_create(project=project, note=note)
    return JsonResponse({'ok': True})


@login_required
@require_POST
def fitting_note_unhide(request, pk, note_pk):
    project = get_object_or_404(Project, pk=pk)
    ProjectHiddenFittingNote.objects.filter(project=project, note_id=note_pk).delete()
    return JsonResponse({'ok': True})


@login_required
@require_POST
def fitting_file_upload(request, pk):
    project = get_object_or_404(Project, pk=pk)
    f = request.FILES.get('file')
    if not f:
        return JsonResponse({'ok': False, 'error': 'No file received.'}, status=400)
    if f.size > MAX_FILE_BYTES:
        return JsonResponse({'ok': False, 'error': 'File too large (max 15MB).'}, status=400)
    data = f.read()
    name = re.sub(r'[^\w.\- ()]+', '', f.name or 'drawing')[:150] or 'drawing'
    if data[:4] == b'%PDF':
        mime = 'application/pdf'
    else:
        try:
            from PIL import Image, ImageOps
            img = Image.open(io.BytesIO(data))
            img.load()
            mime = 'image/jpeg'
            img = ImageOps.exif_transpose(img).convert('RGB')   # always JPEG: PDF printing can't handle transparency
            img.thumbnail((2600, 2600))
            out = io.BytesIO()
            img.save(out, 'JPEG', quality=85, optimize=True)
            data = out.getvalue()
            name = re.sub(r'\.\w+$', '', name) + '.jpg'
        except Exception:
            return JsonResponse({'ok': False, 'error': 'Please upload a PDF or a photo/image.'}, status=400)
    title = (request.POST.get('title') or '').strip()[:200] or re.sub(r'\.\w+$', '', name)
    pf = ProjectFittingFile.objects.create(project=project, title=title, file_data=data, file_mime=mime,
                                           file_original_name=name, uploaded_by=request.user)
    return JsonResponse({'ok': True, 'id': pf.pk})


@login_required
@require_POST
def fitting_file_delete(request, pk, file_pk):
    project = get_object_or_404(Project, pk=pk)
    ProjectFittingFile.objects.filter(project=project, pk=file_pk).delete()
    return JsonResponse({'ok': True})


@login_required
def fitting_file_download(request, pk):
    pf = get_object_or_404(ProjectFittingFile, pk=pk)
    resp = HttpResponse(bytes(pf.file_data or b''), content_type=pf.file_mime or 'application/octet-stream')
    resp['Content-Disposition'] = f'inline; filename="{pf.file_original_name or pf.title}"'
    return resp
