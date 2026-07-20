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
        'photos': report.photos.all(),
        'satisfaction_notes': report.satisfaction_notes.all(),
        'project_pk': project.pk,
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
        created.append({'pk': p.pk, 'url': f'/report/photo/{p.pk}/file/'})
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




def report_photo_file(request, pk):
    from django.http import HttpResponse, FileResponse
    photo = get_object_or_404(ReportPhoto, pk=pk)
    if photo.file_data:
        resp = HttpResponse(bytes(photo.file_data), content_type=photo.file_mime or 'image/jpeg')
        resp['Content-Disposition'] = f'inline; filename="{photo.file_original_name or "photo.jpg"}"'
        return resp
    if photo.image:
        return FileResponse(photo.image.open(), content_type='image/jpeg')
    return HttpResponse('Not found', status=404)




@login_required
def photo_library(request):
    """Library of installed-project photos, grouped into one 'folder' per project."""
    q = request.GET.get('q', '').strip()
    reports = (InstallationReport.objects
               .filter(photos__isnull=False)
               .select_related('project')
               .prefetch_related('photos')
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
    folders.sort(key=lambda f: f['updated_at'], reverse=True)
    quote_photo_count = QuotePhoto.objects.count()
    return render(request, 'projects/photo_library.html', {
        'folders': folders, 'query': q, 'quote_photo_count': quote_photo_count,
    })




@login_required
def photo_library_project(request, pk):
    """All install-report photos for a single project."""
    project = get_object_or_404(Project, pk=pk)
    report = get_object_or_404(InstallationReport, project=project)
    photos = report.photos.all()
    return render(request, 'projects/photo_library_project.html', {
        'project': project, 'photos': photos,
    })




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




def fitting_note_download(request, pk):
    note = get_object_or_404(FittingNote, pk=pk)
    if note.file_data:
        from django.http import HttpResponse
        response = HttpResponse(bytes(note.file_data), content_type=note.file_mime or 'application/octet-stream')
        response['Content-Disposition'] = f'inline; filename="{note.file_original_name or note.title}"'
        return response
    from django.http import HttpResponse
    return HttpResponse('File not found', status=404)


