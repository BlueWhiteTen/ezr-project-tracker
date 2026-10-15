import base64
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import EmailMessage
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone

from ..models import (Project, ProjectCost, ProjectQuote, ProjectLog, CustomerProfile,
                      QuoteShareLink, Reminder)
from .pdf import _pdf_bytes
from .quotes import _quote_safe_html, quote_ref


def _display(quote, project, cost, signer):
    """Same fallback wording the public quote page uses."""
    import datetime as _dt
    cp = project.customer_profile or CustomerProfile.objects.filter(name__iexact=project.customer).first()
    first = (cp.contact_name.split()[0] if cp and cp.contact_name else '')
    label = project.project_name
    if project.location and project.location not in project.project_name:
        label = f"{project.project_name} - {project.location}"
    d = cost and float(cost.delivery) > 0
    i = cost and float(cost.labour) > 0
    verb = "To supply, deliver and install" if d and i else "To supply and deliver" if d else "To supply only (ex. works)"
    addr = []
    if cp:
        if cp.contact_name:
            addr.append(cp.contact_name)
        addr.append(cp.name)
        addr += [x for x in [cp.address_line1, cp.address_line2, cp.town, cp.county, cp.postcode] if x]
    return {
        'quote_date': quote.quote_date or _dt.date.today().strftime('%d %B %Y').lstrip('0'),
        'address_block': quote.address_block or '\n'.join(addr),
        'header_ref': quote.header_ref or quote_ref(project),
        'greeting': quote.greeting or (f"Dear {first}," if first else "Dear Sir/Madam,"),
        'thank_you': quote.thank_you or f"Thank you for your enquiry for shelving at {label}, for which we now have the pleasure of quoting as follows:",
        'supply_line': quote.supply_line or f"{verb} Trimline shelving to give the layout as shown on the drawing {project.drawing_number or '[drawing number]'}, and as described below:",
        'closing': quote.closing or "We trust the above meets with your approval and if you require any further information, please do not hesitate to contact me.",
        'signature_name': quote.signature_name or (signer.get_full_name() or signer.username),
        'main_price_label': quote.main_price_label or f'Our price to {verb.lower().replace("to ", "")}:',
    }, first


def build_quote_pdf(quote, project, cost, signer):
    display, _ = _display(quote, project, cost, signer)
    srcs = []
    for p in quote.attached_photos.all():
        if p.file_data:
            srcs.append(f"data:{p.file_mime or 'image/jpeg'};base64," + base64.b64encode(bytes(p.file_data)).decode('ascii'))
    return _pdf_bytes('projects/quote_pdf.html', {
        'quote': quote, 'project': project, 'cost': cost, 'display': display,
        'capacity_html': _quote_safe_html(quote.capacity),
        'bay_breakdown_html': _quote_safe_html(quote.bay_breakdown),
        'photo_srcs': srcs,
    })


def _customer_emails(project):
    cp = project.customer_profile or CustomerProfile.objects.filter(name__iexact=project.customer).first()
    out = []
    if cp:
        for e in [cp.email, cp.email2, cp.email3]:
            if e:
                out.append((e, cp.contact_name or cp.name))
        for c in cp.contacts.all():
            if c.email:
                out.append((c.email, c.name + (f" ({c.role})" if c.role else '')))
    seen, res = set(), []
    for e, n in out:
        if e.lower() not in seen:
            seen.add(e.lower())
            res.append({'email': e, 'name': n})
    return res


def _pick_cost(project, cost_pk):
    if cost_pk:
        return get_object_or_404(ProjectCost, pk=cost_pk, project=project)
    return project.costs.filter(is_accepted=True).first() or project.costs.first()


def _fresh_link(quote, user):
    QuoteShareLink.objects.filter(quote=quote, is_retired=False).update(is_retired=True)
    return QuoteShareLink.objects.create(quote=quote, token=secrets.token_urlsafe(24),
                                         quote_updated_snapshot=quote.updated_at, created_by=user)


@login_required
def quote_email(request, pk, cost_pk=None):
    project = get_object_or_404(Project, pk=pk)
    cost = _pick_cost(project, cost_pk)
    if not cost:
        messages.error(request, 'Create a costing and quote first.')
        return redirect('project_edit', pk=pk)
    quote = ProjectQuote.objects.filter(cost=cost).first()
    if not quote:
        messages.error(request, 'Open and save the quote first, then email it.')
        return redirect('customer_quote', pk=pk)
    user = request.user
    display, first = _display(quote, project, cost, user)
    contacts = _customer_emails(project)
    sender = user.get_full_name() or user.username
    ref = f"#{project.project_number} " if project.project_number else ''
    label = project.project_name
    default = {
        'to': contacts[0]['email'] if contacts else '',
        'subject': f"Quotation – {ref}{label} – EZR Shelving",
        'body': (f"{display['greeting']}\n\n"
                 f"Please find attached our quotation for {label}.\n\n"
                 f"If you have any questions, or would like to go ahead, just reply to this email.\n\n"
                 f"Kind regards,\n{sender}\nEZR Shelving\n01993 779494"),
    }
    errors = []
    form = dict(default, cc_me=bool(user.email), accept_link=True, mark_quoted=project.status == 'enquiry', chase=True)
    if request.method == 'POST':
        P = request.POST
        form = {
            'to': P.get('to', '').strip(), 'subject': P.get('subject', '').strip(), 'body': P.get('body', '').strip(),
            'cc_me': bool(P.get('cc_me')), 'accept_link': bool(P.get('accept_link')),
            'mark_quoted': bool(P.get('mark_quoted')), 'chase': bool(P.get('chase')),
        }
        to = [e.strip() for e in re.split(r'[,;\s]+', form['to']) if e.strip()]
        if not to:
            errors.append('Please enter an email address to send to.')
        for e in to:
            try:
                validate_email(e)
            except ValidationError:
                errors.append(f'"{e}" is not a valid email address.')
        if not form['subject']:
            errors.append('Please add a subject.')
        if not form['body']:
            errors.append('Please write a message.')
        if not user.email:
            errors.append('Your account has no email address, so customer replies would not reach you. Add it on your Staff Profile first.')
        if not errors:
            body = form['body']
            link = None
            if form['accept_link']:
                link = _fresh_link(quote, user)
                url = request.build_absolute_uri(reverse('quote_public_view', kwargs={'token': link.token}))
                body += f"\n\nYou can also view and accept this quote online:\n{url}"
            try:
                pdf = build_quote_pdf(quote, project, cost, user)
            except Exception as exc:
                errors.append(f'Could not create the PDF: {exc}')
        if not errors:
            fname = re.sub(r'[^\w\- ]+', '', f"Quotation {ref}{label}").strip() + '.pdf'
            from_name = sender.replace('"', '').replace('<', '').replace('>', '')
            m = re.search(r'<([^>]+)>', settings.DEFAULT_FROM_EMAIL)
            from_addr = m.group(1) if m else settings.DEFAULT_FROM_EMAIL
            msg = EmailMessage(subject=form['subject'], body=body,
                               from_email=f"{from_name} (EZR Shelving) <{from_addr}>",
                               to=to, cc=[user.email] if form['cc_me'] else [], reply_to=[user.email])
            msg.attach(fname, pdf, 'application/pdf')
            try:
                msg.send()
            except Exception as exc:
                errors.append(f'The email could not be sent: {exc}')
        if not errors:
            ProjectLog.objects.create(project=project, user=user, field='Quote emailed',
                                      old_value='', new_value=f"Sent to {', '.join(to)} ({cost.label}) with PDF{' and online link' if link else ''}")
            if form['mark_quoted'] and project.status == 'enquiry':
                ProjectLog.objects.create(project=project, user=user, field='Status', old_value='Enquiry', new_value='Quoted')
                project.status = 'quoted'
                project.last_edited_by = user
                project.save()
            if form['chase']:
                Reminder.objects.create(project=project, notify_user=user, created_by=user,
                                        message=f"Chase quote – {label} (sent {timezone.localdate():%d %b})",
                                        remind_at=timezone.now() + timedelta(days=7))
            messages.success(request, f"Quote emailed to {', '.join(to)}. Replies will come to {user.email}.")
            return redirect('project_edit', pk=pk)
    return render(request, 'projects/quote_email.html', {
        'project': project, 'cost': cost, 'quote': quote, 'contacts': contacts, 'form': form,
        'errors': errors, 'sender_email': user.email, 'cost_pk': cost.pk,
    })


@login_required
def quote_pdf_download(request, pk, cost_pk=None):
    """Preview exactly the PDF that gets emailed."""
    from django.http import HttpResponse
    project = get_object_or_404(Project, pk=pk)
    cost = _pick_cost(project, cost_pk)
    quote = get_object_or_404(ProjectQuote, cost=cost)
    resp = HttpResponse(build_quote_pdf(quote, project, cost, request.user), content_type='application/pdf')
    resp['Content-Disposition'] = 'inline; filename="quotation.pdf"'
    return resp
