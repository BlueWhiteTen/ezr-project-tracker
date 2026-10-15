"""Fitter link: a private, no-login phone page for outsourced fitters.

Access is by possession of an unguessable token (like the customer quote link).
The link works until the project is Completed/Cancelled or it is revoked.
"""
import base64
import io
import json
import re
import secrets

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from ..models import (FitterLink, FitterSubmission, InstallationReport, Notification, Project,
                      ProjectDocument, ProjectLog, ReportPhoto, SatisfactionNote)
from .pdf import _pdf_bytes

MAX_PHOTO_BYTES = 15 * 1024 * 1024
MAX_PHOTOS_PER_LINK = 80
MAX_SIGNATURE_BYTES = 600 * 1024


# ── staff side ───────────────────────────────────────────────────────────────

def _link_json(request, link):
    return {
        'url': request.build_absolute_uri(reverse('fitter_page', kwargs={'token': link.token})),
        'created': timezone.localtime(link.created_at).strftime('%d %b %Y'),
    }


def _active_link(project):
    for l in project.fitter_links.filter(is_revoked=False).order_by('-created_at'):
        if l.is_active:
            return l
    return None


@login_required
def fitter_link_info(request, pk):
    project = get_object_or_404(Project, pk=pk)
    link = _active_link(project)
    subs = [{
        'id': s.pk, 'name': s.fitter_name, 'when': timezone.localtime(s.submitted_at).strftime('%d %b %Y %H:%M'),
        'signed': s.signed, 'reason': s.no_signature_reason, 'photos': s.photo_count,
        'completed': s.work_completed, 'return_visit': s.return_visit,
        'pdf': reverse('document_download', kwargs={'pk': s.document_id}) if s.document_id else '',
    } for s in project.fitter_submissions.all()[:10]]
    return JsonResponse({
        'link': _link_json(request, link) if link else None,
        'closed': project.status in ('completed', 'cancelled'),
        'no_signature': project.no_customer_signature,
        'submissions': subs,
        'project': project.project_name,
        'ref': project.project_number or '',
    })


@login_required
@require_POST
def fitter_link_create(request, pk):
    project = get_object_or_404(Project, pk=pk)
    link = _active_link(project)
    if not link:
        link = FitterLink.objects.create(project=project, token=secrets.token_urlsafe(24), created_by=request.user)
        ProjectLog.objects.create(project=project, user=request.user, field='Fitter link', old_value='', new_value='Link created')
    return JsonResponse({'ok': True, 'link': _link_json(request, link)})


@login_required
@require_POST
def fitter_link_revoke(request, pk):
    project = get_object_or_404(Project, pk=pk)
    n = project.fitter_links.filter(is_revoked=False).update(is_revoked=True)
    if n:
        ProjectLog.objects.create(project=project, user=request.user, field='Fitter link', old_value='', new_value='Link turned off')
    return JsonResponse({'ok': True})


@login_required
@require_POST
def fitter_clear_flag(request, pk):
    project = get_object_or_404(Project, pk=pk)
    project.no_customer_signature = False
    project.save(update_fields=['no_customer_signature'])
    ProjectLog.objects.create(project=project, user=request.user, field='No customer signature', old_value='Flagged', new_value='Cleared')
    return JsonResponse({'ok': True})


# ── public (token) side ──────────────────────────────────────────────────────

def _get_link(token):
    return FitterLink.objects.select_related('project').filter(token=token).first()


def _closed(request, why):
    return render(request, 'projects/fitter_closed.html', {'why': why}, status=410)


def _ref(project):
    return f"#{project.project_number}" if project.project_number else ''


def fitter_page(request, token):
    link = _get_link(token)
    if not link:
        return _closed(request, 'This link is not valid.')
    if not link.is_active:
        return _closed(request, 'This job has been closed, so this link no longer works. Thank you!')
    project = link.project
    report = InstallationReport.objects.filter(project=project).first()
    addr = [x for x in [project.addr_line1, project.addr_line2, project.addr_city, project.addr_county, project.addr_postcode] if x]
    inst = project.get_effective_installation_date()
    photos = []
    if report:
        photos = list(ReportPhoto.objects.filter(report=report, fitter_link=link).order_by('uploaded_at').values_list('pk', flat=True))
    maps_q = ', '.join(addr)
    return render(request, 'projects/fitter_page.html', {
        'link': link, 'project': project, 'ref': _ref(project), 'addr': addr, 'maps_q': maps_q,
        'inst': inst, 'tasks': (report.job_tasks if report and report.job_tasks else project.description),
        'contact': project.addr_fao, 'phone': project.addr_phone,
        'photo_ids': photos,
    })


def _clean_image(f):
    """Validate it really is an image; re-encode to JPEG (also strips huge originals)."""
    from PIL import Image, ImageOps
    if f.size > MAX_PHOTO_BYTES:
        return None, 'That photo is too large (max 15MB).'
    try:
        img = Image.open(f)
        img.load()
        img = ImageOps.exif_transpose(img)
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        img.thumbnail((1800, 1800))
        out = io.BytesIO()
        img.save(out, 'JPEG', quality=82, optimize=True)
        return out.getvalue(), None
    except Exception:
        return None, 'That file does not look like a photo.'


@csrf_exempt
@require_POST
def fitter_photo_upload(request, token):
    link = _get_link(token)
    if not link or not link.is_active:
        return JsonResponse({'ok': False, 'error': 'This link is no longer active.'}, status=410)
    f = request.FILES.get('photo')
    if not f:
        return JsonResponse({'ok': False, 'error': 'No photo received.'}, status=400)
    report, _ = InstallationReport.objects.get_or_create(project=link.project)
    if ReportPhoto.objects.filter(report=report, fitter_link=link).count() >= MAX_PHOTOS_PER_LINK:
        return JsonResponse({'ok': False, 'error': 'Photo limit reached for this job.'}, status=400)
    data, err = _clean_image(f)
    if err:
        return JsonResponse({'ok': False, 'error': err}, status=400)
    name = re.sub(r'[^\w.\- ]+', '', (f.name or 'photo'))[:120] or 'photo.jpg'
    if not name.lower().endswith(('.jpg', '.jpeg')):
        name = re.sub(r'\.\w+$', '', name) + '.jpg'
    ph = ReportPhoto.objects.create(report=report, file_data=data, file_mime='image/jpeg',
                                    file_original_name=name, caption='Fitter photo', fitter_link=link)
    return JsonResponse({'ok': True, 'id': ph.pk, 'thumb': reverse('fitter_photo_thumb', kwargs={'token': token, 'pk': ph.pk})})


def _own_photo(link, pk):
    return ReportPhoto.objects.filter(pk=pk, fitter_link=link).first()


def fitter_photo_thumb(request, token, pk):
    link = _get_link(token)
    if not link or link.is_revoked:
        return HttpResponse(status=404)
    ph = _own_photo(link, pk)
    if not ph or not ph.file_data:
        return HttpResponse(status=404)
    from PIL import Image
    img = Image.open(io.BytesIO(bytes(ph.file_data)))
    img.thumbnail((360, 360))
    out = io.BytesIO()
    img.convert('RGB').save(out, 'JPEG', quality=70)
    resp = HttpResponse(out.getvalue(), content_type='image/jpeg')
    resp['Cache-Control'] = 'private, max-age=3600'
    return resp


@csrf_exempt
@require_POST
def fitter_photo_delete(request, token, pk):
    link = _get_link(token)
    if not link or not link.is_active:
        return JsonResponse({'ok': False}, status=410)
    ph = _own_photo(link, pk)
    if ph:
        ph.delete()
    return JsonResponse({'ok': True})


def _tri(v):
    return v if isinstance(v, bool) else None


def _signature_bytes(data_url):
    m = re.match(r'^data:image/png;base64,([A-Za-z0-9+/=]+)$', data_url or '')
    if not m:
        return None
    try:
        raw = base64.b64decode(m.group(1), validate=True)
    except Exception:
        return None
    if len(raw) > MAX_SIGNATURE_BYTES or raw[:8] != b'\x89PNG\r\n\x1a\n':
        return None
    return raw


def _photo_srcs(photos, limit=24):
    from PIL import Image
    srcs = []
    for ph in photos[:limit]:
        try:
            img = Image.open(io.BytesIO(bytes(ph.file_data)))
            img.thumbnail((800, 800))
            out = io.BytesIO()
            img.convert('RGB').save(out, 'JPEG', quality=70)
            srcs.append('data:image/jpeg;base64,' + base64.b64encode(out.getvalue()).decode('ascii'))
        except Exception:
            continue
    return srcs


@csrf_exempt
@require_POST
def fitter_submit(request, token):
    link = _get_link(token)
    if not link or not link.is_active:
        return JsonResponse({'ok': False, 'error': 'This link is no longer active.'}, status=410)
    try:
        d = json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Bad request.'}, status=400)
    name = (d.get('fitter_name') or '').strip()[:200]
    wc, sc, rv = _tri(d.get('work_completed')), _tri(d.get('site_clear')), _tri(d.get('return_visit'))
    snags = (d.get('snags') or '').strip()[:3000]
    cust = (d.get('customer_name') or '').strip()[:200]
    reason = (d.get('no_signature_reason') or '').strip()[:300]
    not_on_site = bool(d.get('customer_not_on_site'))
    sig = _signature_bytes(d.get('signature')) if not not_on_site else None
    errs = []
    if not name:
        errs.append('Please add your name.')
    if wc is None or sc is None or rv is None:
        errs.append('Please answer the three completion questions.')
    if not not_on_site and not sig:
        errs.append('Please get the customer to sign, or tick "Customer not on site".')
    if not_on_site and not reason:
        errs.append('Please add a short reason why there is no signature.')
    if errs:
        return JsonResponse({'ok': False, 'error': ' '.join(errs)}, status=400)

    project = link.project
    now = timezone.localtime()
    stamp = now.strftime('%d %b %Y %H:%M')
    report, _ = InstallationReport.objects.get_or_create(project=project)
    report.work_completed = wc
    report.site_cleared = sc
    report.return_visit_required = rv
    if not report.fitting_crew:
        report.fitting_crew = name
    if cust and not report.site_contact:
        report.site_contact = cust
    if snags:
        report.issues = (report.issues + '\n\n' if report.issues else '') + f"[Fitter {name}, {stamp}] {snags}"
    report.notes = (report.notes + '\n\n' if report.notes else '') + f"Fitter report from phone by {name}, {stamp}."
    report.save()

    photos = list(ReportPhoto.objects.filter(report=report, fitter_link=link).order_by('uploaded_at'))
    if sig:
        SatisfactionNote.objects.create(report=report, file_data=sig, file_mime='image/png',
                                        file_original_name='customer-signature.png',
                                        caption=f"Customer sign-off{' — ' + cust if cust else ''} — {stamp}")
    sig_src = ('data:image/png;base64,' + base64.b64encode(sig).decode('ascii')) if sig else ''

    sub = FitterSubmission.objects.create(
        link=link, project=project, fitter_name=name, work_completed=wc, site_clear=sc, return_visit=rv,
        snags=snags, customer_name=cust, signed=bool(sig), no_signature_reason='' if sig else reason,
        photo_count=len(photos))
    try:
        pdf = _pdf_bytes('projects/fitter_report_pdf.html', {
            'project': project, 'ref': _ref(project), 'sub': sub, 'stamp': stamp, 'sig_src': sig_src,
            'photo_srcs': _photo_srcs(photos),
            'addr': ', '.join(x for x in [project.addr_line1, project.addr_city, project.addr_postcode] if x),
        })
        fname = re.sub(r'[^\w\- ]+', '', f"Install report {_ref(project)} {project.project_name} {now:%Y-%m-%d}").strip() + '.pdf'
        doc = ProjectDocument.objects.create(project=project, name=f"Fitter report {now:%d %b %Y}", doc_type='other',
                                             file_data=pdf, file_mime='application/pdf', file_original_name=fname,
                                             notes=f"Auto-generated from the fitter link ({name})")
        sub.document = doc
        sub.save(update_fields=['document'])
    except Exception:
        pass  # the report data is already saved; a PDF hiccup must never lose the fitter's work

    if not sig:
        project.no_customer_signature = True
        project.save(update_fields=['no_customer_signature'])
    ProjectLog.objects.create(project=project, user=None, field='Fitter report',
        old_value='', new_value=f"{name}: work {'complete' if wc else 'NOT complete'}, site {'clear' if sc else 'NOT clear'}, "
                                f"{'return visit needed' if rv else 'no return visit'}, {len(photos)} photos, "
                                f"{'customer signed' if sig else 'NO customer signature (' + reason + ')'}")

    recipients = {u.pk: u for u in User.objects.filter(is_active=True).filter(
        Q(pk=project.assigned_to_id) | Q(pk=link.created_by_id) | Q(is_superuser=True))}
    flags = []
    if not wc:
        flags.append('work NOT complete')
    if rv:
        flags.append('return visit needed')
    if not sig:
        flags.append('no customer signature')
    text = f"Fitter report in for {project.project_name} from {name}" + (f" ⚠ {', '.join(flags)}" if flags else ' ✓')
    for u in recipients.values():
        Notification.objects.create(user=u, type='tag', text=text[:250], link=reverse('project_edit', kwargs={'pk': project.pk}))
    return JsonResponse({'ok': True, 'signed': bool(sig), 'photos': len(photos), 'has_pdf': bool(sub.document_id)})
