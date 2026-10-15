import math
import random
from django.utils import timezone
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.db.models import Q, F as models_F, Count, Prefetch
from django.views.decorators.http import require_POST
from datetime import date, timedelta
import json

from ..models import Project, ProjectLog, Customer, Comment, Message, Notification, TeamMessage, StaffProfile, LeaveRequest, InstallationReport, ReportPhoto, SatisfactionNote, CustomerProfile, ProjectDocument, Product, PickingList, PickingListItem, PickingTemplate, PickingTemplateItem, MaterialPrice, ProjectCost, ProjectCostLine, UprightAccessory, AccessoryOverride, Reminder, FittingCrew, Supplier, PurchaseOrder, PurchaseOrderLine, StockMovement, FittingNote, ProjectQuote, PriceListItem, QuotePhoto, QuoteAttachedPhoto, ProformaInvoice, DeliveryPhase, ProjectPresence, SurveyPhoto
from ..forms import RegisterForm, ProjectForm


# ── Auth ──────────────────────────────────────────────────────────────────────

from django.contrib.auth.views import (
    PasswordResetView, PasswordResetDoneView,
    PasswordResetConfirmView, PasswordResetCompleteView,
)

@login_required
def install_report(request, pk):
    project = get_object_or_404(Project, pk=pk)
    report, _ = InstallationReport.objects.get_or_create(
        project=project,
        defaults={'created_by': request.user}
    )

    if request.method == 'POST':
        report.notes          = request.POST.get('notes', '').strip()
        report.issues         = request.POST.get('issues', '').strip()
        report.job_tasks      = request.POST.get('job_tasks', '').strip()
        report.fitting_crew        = request.POST.get('fitting_crew', '').strip()
        report.fitting_crew_phone  = request.POST.get('fitting_crew_phone', '').strip()
        report.site_contact   = request.POST.get('site_contact', '').strip()
        report.contact_number = request.POST.get('contact_number', '').strip()
        sc = request.POST.get('site_cleared', '')
        report.site_cleared = True if sc == 'yes' else (False if sc == 'no' else None)
        report.site_cleared_notes = request.POST.get('site_cleared_notes', '').strip()
        wc = request.POST.get('work_completed', '')
        report.work_completed = True if wc == 'yes' else (False if wc == 'no' else None)
        report.work_completed_notes = request.POST.get('work_completed_notes', '').strip()
        rv = request.POST.get('return_visit_required', '')
        report.return_visit_required = True if rv == 'yes' else (False if rv == 'no' else None)
        report.save()

        # Handle photo uploads
        for photo in request.FILES.getlist('photos'):
            ReportPhoto.objects.create(
                report=report,
                file_data=photo.read(),
                file_mime=photo.content_type or '',
                file_original_name=photo.name,
                caption=request.POST.get('photo_caption', ''),
            )

        # Handle satisfaction note upload (PDF or photo)
        if 'satisfaction_note' in request.FILES:
            f = request.FILES['satisfaction_note']
            SatisfactionNote.objects.create(
                report=report,
                file_data=f.read(),
                file_mime=f.content_type or '',
                file_original_name=f.name,
                caption=request.POST.get('sat_caption', '')
            )
        messages.success(request, 'Report saved.')
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept',''):
            return JsonResponse({'ok': True})
        return redirect('install_report', pk=pk)

    return render(request, 'projects/install_report.html', {
        'project': project, 'report': report,
        'photos': report.photos.defer('file_data'),
        'satisfaction_notes': report.satisfaction_notes.all(),
        'project_pk': project.pk,
        'fitting_crews': FittingCrew.objects.all(),
    })




@login_required
@require_POST
def delete_report_photo(request, pk):
    photo = get_object_or_404(ReportPhoto, pk=pk)
    if photo.image:
        photo.image.delete()
    photo.delete()
    return JsonResponse({'ok': True})




@login_required
@require_POST
def delete_satisfaction_note(request, pk):
    note = get_object_or_404(SatisfactionNote, pk=pk)
    if note.file:
        note.file.delete()
    note.delete()
    return JsonResponse({'ok': True})




@login_required
@require_POST
def report_photo_upload(request, pk):
    """Instant AJAX upload of as-built photos to a project's install report."""
    project = get_object_or_404(Project, pk=pk)
    report, _ = InstallationReport.objects.get_or_create(project=project)
    created = []
    for photo in request.FILES.getlist('photos'):
        p = ReportPhoto.objects.create(
            report=report,
            file_data=photo.read(),
            file_mime=photo.content_type or '',
            file_original_name=photo.name,
            caption=request.POST.get('photo_caption', ''),
        )
        created.append({'pk': p.pk, 'url': f'/report/photo/{p.pk}/file/', 'thumb': f'/report/photo/{p.pk}/thumb/'})
    return JsonResponse({'ok': True, 'photos': created})




@login_required
@require_POST
def report_sat_upload(request, pk):
    """Instant AJAX upload of a satisfaction note (PDF or image)."""
    project = get_object_or_404(Project, pk=pk)
    report, _ = InstallationReport.objects.get_or_create(project=project)
    f = request.FILES.get('satisfaction_note')
    if not f:
        return JsonResponse({'error': 'No file'}, status=400)
    note = SatisfactionNote.objects.create(
        report=report,
        file_data=f.read(),
        file_mime=f.content_type or '',
        file_original_name=f.name,
        caption=request.POST.get('sat_caption', ''),
    )
    return JsonResponse({'ok': True, 'pk': note.pk, 'name': note.file_original_name,
                         'url': f'/report/sat/{note.pk}/file/'})




def _thumbnail_response(data, mime, size):
    """Small JPEG preview of an image blob (so galleries don't download full-size photos).
    Falls back to the original bytes if it can't be decoded."""
    import io
    try:
        from PIL import Image, ImageOps
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        img.thumbnail((size, size))
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        buf = io.BytesIO()
        img.save(buf, 'JPEG', quality=78, optimize=True)
        resp = HttpResponse(buf.getvalue(), content_type='image/jpeg')
    except Exception:
        resp = HttpResponse(data, content_type=mime or 'image/jpeg')
    resp['Cache-Control'] = 'private, max-age=604800'
    return resp


@login_required
def report_photo_file(request, pk):
    from django.http import HttpResponse, FileResponse
    photo = get_object_or_404(ReportPhoto, pk=pk)
    if photo.file_data:
        resp = HttpResponse(bytes(photo.file_data), content_type=photo.file_mime or 'image/jpeg')
        resp['Content-Disposition'] = f'inline; filename="{photo.file_original_name or "photo.jpg"}"'
        resp['Cache-Control'] = 'private, max-age=86400'
        return resp
    if photo.image:
        return FileResponse(photo.image.open(), content_type='image/jpeg')
    return HttpResponse('Not found', status=404)


@login_required
def report_photo_thumb(request, pk):
    """Small preview used by the photo library, installation report grid and homepage slideshow."""
    try:
        size = max(60, min(int(request.GET.get('s', 400)), 1600))
    except ValueError:
        size = 400
    photo = get_object_or_404(ReportPhoto, pk=pk)
    if photo.file_data:
        return _thumbnail_response(bytes(photo.file_data), photo.file_mime, size)
    return report_photo_file(request, pk)


@login_required
def survey_photo_thumb(request, pk):
    photo = get_object_or_404(SurveyPhoto, pk=pk)
    if not photo.file_data or photo.is_video:
        return HttpResponse('Not found', status=404)
    return _thumbnail_response(bytes(photo.file_data), photo.file_mime, 300)




@login_required
def survey_photos(request, pk):
    """List (GET) or bulk-upload (POST) survey photos/videos for a project."""
    project = get_object_or_404(Project, pk=pk)
    if request.method == 'POST':
        created = []
        for f in request.FILES.getlist('survey_photos'):
            p = SurveyPhoto.objects.create(
                project=project,
                file_data=f.read(),
                file_mime=f.content_type or '',
                file_original_name=f.name,
                uploaded_by=request.user,
            )
            created.append({'pk': p.pk, 'url': f'/survey-photo/{p.pk}/file/', 'thumb': f'/survey-photo/{p.pk}/thumb/', 'is_video': p.is_video, 'name': p.file_original_name})
        return JsonResponse({'ok': True, 'photos': created})

    photos = project.survey_photos.defer('file_data').select_related('uploaded_by')
    return JsonResponse([{
        'pk': p.pk,
        'url': f'/survey-photo/{p.pk}/file/',
        'thumb': f'/survey-photo/{p.pk}/thumb/',
        'is_video': p.is_video,
        'name': p.file_original_name,
        'uploaded_by': p.uploaded_by.get_full_name() if p.uploaded_by and p.uploaded_by.get_full_name() else (p.uploaded_by.username if p.uploaded_by else ''),
    } for p in photos], safe=False)


@login_required
def survey_photo_file(request, pk):
    from django.http import HttpResponse
    photo = get_object_or_404(SurveyPhoto, pk=pk)
    resp = HttpResponse(bytes(photo.file_data or b''), content_type=photo.file_mime or 'application/octet-stream')
    resp['Content-Disposition'] = f'inline; filename="{photo.file_original_name or "file"}"'
    resp['Cache-Control'] = 'private, max-age=86400'
    return resp


@login_required
@require_POST
def survey_photo_delete(request, pk):
    photo = get_object_or_404(SurveyPhoto, pk=pk)
    photo.delete()
    return JsonResponse({'ok': True})


@login_required
def photo_library(request):
    """Library of installed-project photos, grouped into one 'folder' per project."""
    q = request.GET.get('q', '').strip()
    reports = (InstallationReport.objects
               .filter(photos__isnull=False)
               .select_related('project')
               .prefetch_related(Prefetch('photos', queryset=ReportPhoto.objects.defer('file_data')))
               .distinct()
               .order_by('-updated_at'))
    if q:
        reports = reports.filter(
            Q(project__project_name__icontains=q) | Q(project__customer__icontains=q) |
            Q(project__location__icontains=q)
        )
    folders = []
    for r in reports:
        photos = list(r.photos.all())
        if not photos:
            continue
        latest_upload = max(p.uploaded_at for p in photos)
        folders.append({
            'project': r.project,
            'cover': photos[0],
            'count': len(photos),
            'updated_at': latest_upload,
        })
    folders.sort(key=lambda f: _natural_key(f['project'].project_name))
    quote_photo_count = QuotePhoto.objects.count()
    return render(request, 'projects/photo_library.html', {
        'folders': folders, 'query': q, 'quote_photo_count': quote_photo_count,
    })


def _natural_key(text):
    """Case-insensitive 'natural' sort key so IMG_2 comes before IMG_10."""
    import re
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', text or '')]


def _photo_sort_key(p):
    return (_natural_key(p.file_original_name or p.caption or ''), p.pk)


@login_required
def photo_library_project(request, pk):
    """All install-report photos for a single project, sorted by file name A-Z."""
    project = get_object_or_404(Project, pk=pk)
    report = get_object_or_404(InstallationReport, project=project)
    photos = sorted(report.photos.defer('file_data'), key=_photo_sort_key)
    return render(request, 'projects/photo_library_project.html', {
        'project': project, 'photos': photos,
    })


@login_required
def photo_library_latest(request):
    """Most recently uploaded as-built photos across all projects, newest first."""
    try:
        n = max(20, min(int(request.GET.get('n', 100)), 1000))
    except ValueError:
        n = 100
    total = ReportPhoto.objects.count()
    photos = list(ReportPhoto.objects.defer('file_data')
                  .select_related('report__project')
                  .order_by('-uploaded_at', '-pk')[:n])
    return render(request, 'projects/photo_library_latest.html', {
        'photos': photos, 'n': n, 'more': total > n, 'next_n': n + 100, 'total': total,
    })


def _unique_zip_name(name, used, fallback):
    import os
    name = os.path.basename(name or '') or fallback
    base, ext = os.path.splitext(name)
    candidate, i = name, 1
    while candidate.lower() in used:
        i += 1
        candidate = f'{base} ({i}){ext}'
    used.add(candidate.lower())
    return candidate


def _zip_response(photos, zip_name):
    """Build a zip of the given ReportPhoto rows, loading one photo at a time to keep memory low."""
    import re, tempfile, zipfile
    from django.http import FileResponse
    tmp = tempfile.TemporaryFile()
    used = set()
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_STORED, allowZip64=True) as zf:
        for meta in photos:
            p = ReportPhoto.objects.filter(pk=meta.pk).only('file_data', 'file_original_name', 'file_mime').first()
            if not p or not p.file_data:
                continue
            zf.writestr(_unique_zip_name(p.file_original_name, used, f'photo-{p.pk}.jpg'), bytes(p.file_data))
    tmp.seek(0)
    safe = re.sub(r'[^A-Za-z0-9._ -]+', '_', zip_name).strip() or 'photos'
    return FileResponse(tmp, as_attachment=True, filename=f'{safe}.zip', content_type='application/zip')


@login_required
def photo_library_project_zip(request, pk):
    """Download every photo of a project as one zip."""
    project = get_object_or_404(Project, pk=pk)
    report = get_object_or_404(InstallationReport, project=project)
    photos = sorted(report.photos.defer('file_data'), key=_photo_sort_key)
    return _zip_response(photos, f'{project.project_name} photos')


@login_required
@require_POST
def photo_library_bulk(request, pk):
    """Bulk actions on selected photos of one project: download | caption | delete."""
    project = get_object_or_404(Project, pk=pk)
    report = get_object_or_404(InstallationReport, project=project)
    action = request.POST.get('action', '')
    try:
        ids = [int(i) for i in request.POST.getlist('ids')]
    except ValueError:
        return JsonResponse({'ok': False, 'error': 'Bad selection'}, status=400)
    qs = report.photos.filter(pk__in=ids)
    if not ids or not qs.exists():
        return JsonResponse({'ok': False, 'error': 'No photos selected'}, status=400)

    if action == 'download':
        photos = sorted(qs.defer('file_data'), key=_photo_sort_key)
        return _zip_response(photos, f'{project.project_name} selected photos')
    if action == 'caption':
        caption = request.POST.get('caption', '').strip()[:200]
        n = qs.update(caption=caption)
        return JsonResponse({'ok': True, 'count': n, 'caption': caption})
    if action == 'delete':
        n = 0
        for p in qs.defer('file_data'):
            if p.image:
                p.image.delete()
            p.delete()
            n += 1
        return JsonResponse({'ok': True, 'count': n})
    return JsonResponse({'ok': False, 'error': 'Unknown action'}, status=400)




@login_required
def satisfaction_note_file(request, pk):
    from django.http import HttpResponse, FileResponse
    note = get_object_or_404(SatisfactionNote, pk=pk)
    if note.file_data:
        resp = HttpResponse(bytes(note.file_data), content_type=note.file_mime or 'application/octet-stream')
        resp['Content-Disposition'] = f'inline; filename="{note.file_original_name or "note"}"'
        return resp
    if note.file:
        return FileResponse(note.file.open(), content_type='application/octet-stream')
    return HttpResponse('Not found', status=404)


# ── Customer Database ─────────────────────────────────────────────────────────



@login_required
def fitting_notes_list(request):
    notes = FittingNote.objects.prefetch_related('products', 'templates').all()
    products = Product.objects.filter(is_active=True).order_by('code')
    templates = PickingTemplate.objects.all()
    return render(request, 'projects/fitting_notes_list.html', {
        'notes': notes, 'products': products, 'templates': templates,
    })




@login_required
@require_POST
def fitting_note_upload(request):
    title = request.POST.get('title', '').strip()
    f = request.FILES.get('file')
    product_ids = request.POST.getlist('product_ids')
    if not f:
        return JsonResponse({'error': 'File required'}, status=400)
    if not title:
        title = f.name
    mime = f.content_type or ''
    note = FittingNote.objects.create(
        title=title,
        file_data=f.read(),
        file_mime=mime,
        file_original_name=f.name,
        notes=request.POST.get('notes', '').strip(),
        uploaded_by=request.user,
    )
    if product_ids:
        note.products.set(Product.objects.filter(pk__in=product_ids))
    template_ids = request.POST.getlist('template_ids')
    if template_ids:
        note.templates.set(PickingTemplate.objects.filter(pk__in=template_ids))
    return JsonResponse({'ok': True, 'pk': note.pk})




@login_required
@require_POST
def fitting_note_delete(request, pk):
    note = get_object_or_404(FittingNote, pk=pk)
    note.delete()
    return JsonResponse({'ok': True})




@login_required
@require_POST
def fitting_note_update(request, pk):
    note = get_object_or_404(FittingNote, pk=pk)
    data = json.loads(request.body)
    if 'title' in data:
        note.title = data['title'].strip() or note.title
    if 'notes' in data:
        note.notes = data['notes'].strip()
    if 'template_ids' in data:
        note.templates.set(PickingTemplate.objects.filter(pk__in=data['template_ids']))
    note.save()
    return JsonResponse({'ok': True})




@login_required
def fitting_note_download(request, pk):
    note = get_object_or_404(FittingNote, pk=pk)
    if note.file_data:
        from django.http import HttpResponse
        response = HttpResponse(bytes(note.file_data), content_type=note.file_mime or 'application/octet-stream')
        response['Content-Disposition'] = f'inline; filename="{note.file_original_name or note.title}"'
        return response
    from django.http import HttpResponse
    return HttpResponse('File not found', status=404)


