import math
import random
from django.utils import timezone
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.db.models import Q, F as models_F, Count
from django.views.decorators.http import require_POST
from datetime import date, timedelta
import json

from ..models import Project, ProjectLog, Customer, Comment, Message, Notification, TeamMessage, StaffProfile, LeaveRequest, InstallationReport, ReportPhoto, SatisfactionNote, CustomerProfile, ProjectDocument, Product, PickingList, PickingListItem, PickingTemplate, PickingTemplateItem, MaterialPrice, ProjectCost, ProjectCostLine, UprightAccessory, AccessoryOverride, Reminder, FittingCrew, Supplier, PurchaseOrder, PurchaseOrderLine, StockMovement, FittingNote, ProjectQuote, PriceListItem, QuotePhoto, QuoteAttachedPhoto, ProformaInvoice, DeliveryPhase, ProjectPresence, QuoteShareLink
from ..forms import RegisterForm, ProjectForm
from .utils import (_calc_sell_price, require_feature, _client_ip)


# ── Auth ──────────────────────────────────────────────────────────────────────

from django.contrib.auth.views import (
    PasswordResetView, PasswordResetDoneView,
    PasswordResetConfirmView, PasswordResetCompleteView,
)

@login_required
def customer_quote(request, pk, cost_pk=None):
    project = get_object_or_404(Project, pk=pk)
    if cost_pk:
        cost = get_object_or_404(ProjectCost, pk=cost_pk, project=project)
    else:
        cost = project.costs.filter(is_accepted=True).first() or project.costs.first()
        if not cost:
            cost, _ = ProjectCost.objects.get_or_create(project=project)
    lines = cost.lines.select_related('product').all()
    buying_total = sum(l.line_total for l in lines)
    uprights = sum(int(float(l.quantity))*2 for l in lines if l.line_type=='frame')
    for acc in cost.accessories.all():
        buying_total += float(acc.unit_price) * uprights
    markup_amount = buying_total * float(cost.markup) / 100
    sell_price = round(buying_total + markup_amount + float(cost.labour) + float(cost.delivery), 2)

    # Customer profile for address + contact
    cp = CustomerProfile.objects.filter(name__iexact=project.customer).first()
    quote, created = ProjectQuote.objects.get_or_create(cost=cost, defaults={'project': project})


    if request.method == 'POST':
        quote.greeting       = request.POST.get('greeting', '').strip()
        quote.thank_you      = request.POST.get('thank_you', '').strip()
        quote.closing        = request.POST.get('closing', '').strip()
        quote.signature_name = request.POST.get('signature_name', '').strip()
        quote.quote_date     = request.POST.get('quote_date', '').strip()
        quote.address_block  = request.POST.get('address_block', '').strip()
        quote.header_ref     = request.POST.get('header_ref', '').strip()
        quote.supply_line    = request.POST.get('supply_line', '').strip()
        quote.capacity       = request.POST.get('capacity', '').strip()
        quote.spec_note      = request.POST.get('spec_note', '').strip()
        quote.main_price_label = request.POST.get('main_price_label', '').strip()
        quote.main_price     = request.POST.get('main_price') or 0
        quote.extra_label    = request.POST.get('extra_label', '').strip()
        ep = request.POST.get('extra_price', '').strip()
        quote.extra_price    = ep if ep else None
        quote.lead_time      = request.POST.get('lead_time', '').strip()
        quote.payment_terms  = request.POST.get('payment_terms', '').strip()
        quote.terms_text     = request.POST.get('terms_text', '').strip()
        try:
            quote.photo_columns = int(request.POST.get('photo_columns', 2)) or 2
        except (ValueError, TypeError):
            quote.photo_columns = 2
        quote.save()
        if request.POST.get('action') == 'print':
            return redirect(f"{request.path}?print=1")
        messages.success(request, 'Quote saved.')
        return redirect('customer_quote', pk=pk)

    # Contact first name for "Dear X"
    contact_name = cp.contact_name if cp and cp.contact_name else ''
    first_name = contact_name.split()[0] if contact_name else ''

    # Project label for the enquiry line: "Rieker - Doncaster"
    proj_label = project.project_name
    if project.location and project.location not in project.project_name:
        proj_label = f"{project.project_name} - {project.location}"

    # Determine supply phrasing from costing
    has_delivery = float(cost.delivery) > 0
    has_install = float(cost.labour) > 0
    drawing_ref = project.drawing_number or '[drawing number]'
    if has_delivery and has_install:
        supply_verb = "To supply, deliver and install"
    elif has_delivery and not has_install:
        supply_verb = "To supply and deliver"
    else:
        supply_verb = "To supply only (ex. works)"
    default_supply_line = (
        f"{supply_verb} Trimline shelving to give the layout as shown on the drawing "
        f"{drawing_ref}, and as described below:"
    )

    # On first creation, seed defaults
    if created:
        quote.main_price = sell_price
        quote.main_price_label = f'Our price to {supply_verb.lower().replace("to ", "")}:'
        quote.lead_time = '4-5 weeks from receipt of PO and approved drawing.'
        quote.payment_terms = '30 days from the date of invoice, please'
        terms_parts = ["Our prices are exclusive of VAT and remain open for acceptance for 30 days."]
        if has_install:
            terms_parts.append("Our price is subject to site survey and is based on a clear and level site with light and power, good access and normal working hours.")
        terms_parts.append("Title of all goods supplied remains the property of E-Z-Rect Ltd t/a EZR Shelving until paid for in full. Our trading terms apply.")
        terms_parts.append("We have not made any allowance for MCD or retention within our costs.")
        quote.terms_text = "\n\n".join(terms_parts)
        quote.intro = ''
        quote.save()

    # Auto opening lines (always live from project data)
    thank_you_line = f"Thank you for your enquiry for shelving at {proj_label}, for which we now have the pleasure of quoting as follows:"

    # Build address block lines
    addr_lines = []
    if cp:
        if cp.contact_name: addr_lines.append(cp.contact_name)
        addr_lines.append(cp.name)
        for part in [cp.address_line1, cp.address_line2, cp.town, cp.county, cp.postcode]:
            if part: addr_lines.append(part)

    default_address_block = '\n'.join(addr_lines)
    import datetime as _dt
    today = _dt.date.today()
    default_date = today.strftime('%d %B %Y').lstrip('0')
    default_ref = f"{project.customer}\n{project.location}" if project.location else project.customer
    default_greeting = f"Dear {first_name}," if first_name else "Dear Sir/Madam,"
    default_closing = "We trust the above meets with your approval and if you require any further information, please do not hesitate to contact me."
    sig = request.user.get_full_name() or request.user.username

    attached_photos = quote.attached_photos.all()
    library_photos = QuotePhoto.objects.all()

    def _legacy_safe_html(text):
        """Quotes saved before rich-text editing was added hold plain text
        with literal newlines; quotes saved since hold real HTML (from the
        bold/italic/underline editor). Told apart by whether the text
        contains a '<' at all — plain text never does in practice, and the
        editor always produces real tags for any actual formatting or line
        break. This avoids needing a one-off data migration for old quotes."""
        from django.utils.html import escape
        from django.utils.safestring import mark_safe
        if not text:
            return ''
        if '<' in text:
            return mark_safe(text)
        return mark_safe(escape(text).replace('\n', '<br>'))

    return render(request, 'projects/customer_quote.html', {
        'project': project,
        'cost': cost,
        'quote': quote,
        'customer_profile': cp,
        'contact_name': contact_name,
        'first_name': first_name,
        'thank_you_line': quote.thank_you or thank_you_line,
        'supply_line': quote.supply_line or default_supply_line,
        'supply_verb': supply_verb,
        'default_price_label': f'Our price to {supply_verb.lower().replace("to ", "")}:',
        'greeting': quote.greeting or default_greeting,
        'closing': quote.closing or default_closing,
        'signature': quote.signature_name or sig,
        'addr_lines': addr_lines,
        'quote_date': quote.quote_date or default_date,
        'address_block': quote.address_block or default_address_block,
        'header_ref': quote.header_ref or default_ref,
        'capacity_html': _legacy_safe_html(quote.capacity),
        'bay_breakdown_html': _legacy_safe_html(quote.bay_breakdown),
        'sell_price': sell_price,
        'today': today,
        'attached_photos': attached_photos,
        'library_photos': library_photos,
        'print_mode': request.GET.get('print') == '1',
        'project_pk': project.pk if not request.GET.get('print') else None,
        'current_share_link': QuoteShareLink.objects.filter(quote=quote, is_retired=False).order_by('-created_at').first(),
    })


@login_required
@require_POST
def quote_generate_link(request, pk, cost_pk=None):
    """Generate a fresh customer-facing share link for this quote.
    Retires any previous still-live link for the same quote, so there's
    only ever one genuinely working link at a time — a customer can't
    accept via a stray old link after a new one's been issued.

    If the quote's already been accepted, this doesn't just barrel ahead
    — it reports who accepted it and asks the caller to confirm before
    generating another (matches: 'if changes in the quote/costing' being
    the only real reason to reopen an already-accepted quote)."""
    import secrets
    project = get_object_or_404(Project, pk=pk)
    if cost_pk:
        cost = get_object_or_404(ProjectCost, pk=cost_pk, project=project)
    else:
        cost = project.costs.filter(is_accepted=True).first() or project.costs.first()
    quote = get_object_or_404(ProjectQuote, cost=cost)

    confirmed = False
    if request.body:
        try:
            confirmed = json.loads(request.body).get('confirm') is True
        except (json.JSONDecodeError, AttributeError):
            confirmed = False

    if cost.is_accepted and not confirmed:
        return JsonResponse({
            'ok': False, 'needs_confirm': True,
            'accepted_by': cost.accepted_by_name,
            'accepted_at': timezone.localtime(cost.accepted_online_at).strftime('%d %b %Y, %H:%M') if cost.accepted_online_at else '',
        })

    # Correct a couple of things that can otherwise go stale or blank on
    # the public page, which has no logged-in user to fall back on the
    # way the internal editor does — fixed here, at the moment of
    # generating a link, rather than guessed at display time.
    quote_changed = False
    if not quote.signature_name:
        quote.signature_name = request.user.get_full_name() or request.user.username
        quote_changed = True
    has_install_now = float(cost.labour) > 0
    site_survey_line = "Our price is subject to site survey and is based on a clear and level site with light and power, good access and normal working hours."
    if not has_install_now and site_survey_line in quote.terms_text:
        quote.terms_text = quote.terms_text.replace(f"\n\n{site_survey_line}", "").replace(site_survey_line, "").strip()
        quote_changed = True
    if quote_changed:
        quote.save()

    QuoteShareLink.objects.filter(quote=quote, is_retired=False).update(is_retired=True)
    token = secrets.token_urlsafe(24)
    link = QuoteShareLink.objects.create(
        quote=quote, token=token, quote_updated_snapshot=quote.updated_at, created_by=request.user,
    )
    share_url = request.build_absolute_uri(reverse('quote_public_view', kwargs={'token': token}))
    return JsonResponse({'ok': True, 'url': share_url, 'expires_at': link.expires_at.strftime('%d %b %Y')})


def _quote_safe_html(text):
    """Same rule as the internal quote editor: tell real HTML (from the
    rich-text editor) apart from older plain text by whether it contains
    a '<' at all."""
    from django.utils.html import escape
    from django.utils.safestring import mark_safe
    if not text:
        return ''
    if '<' in text:
        return mark_safe(text)
    return mark_safe(escape(text).replace('\n', '<br>'))


def quote_public_view(request, token):
    """No-login, customer-facing view of a quote — access is by
    possession of the token, not by account.

    Quote fields are usually blank in the DB and rely on computed
    fallback defaults (see customer_quote) unless staff have actually
    edited and saved them — this mirrors that same fallback logic so
    the public page shows the same content staff see internally,
    without touching that view's own working code."""
    from django.middleware.csrf import get_token
    get_token(request)  # ensures the CSRF cookie is set for the Accept button's POST
    link = get_object_or_404(QuoteShareLink, token=token)
    quote = link.quote
    project = quote.project
    cost = quote.cost

    cp = CustomerProfile.objects.filter(name__iexact=project.customer).first()
    contact_name = cp.contact_name if cp and cp.contact_name else ''
    first_name = contact_name.split()[0] if contact_name else ''

    proj_label = project.project_name
    if project.location and project.location not in project.project_name:
        proj_label = f"{project.project_name} - {project.location}"

    has_delivery = cost and float(cost.delivery) > 0
    has_install = cost and float(cost.labour) > 0
    if has_delivery and has_install:
        default_supply_verb = "To supply, deliver and install"
    elif has_delivery and not has_install:
        default_supply_verb = "To supply and deliver"
    else:
        default_supply_verb = "To supply only (ex. works)"
    drawing_ref = project.drawing_number or '[drawing number]'
    default_supply_line = (
        f"{default_supply_verb} Trimline shelving to give the layout as shown on the drawing "
        f"{drawing_ref}, and as described below:"
    )

    addr_lines = []
    if cp:
        if cp.contact_name: addr_lines.append(cp.contact_name)
        addr_lines.append(cp.name)
        for part in [cp.address_line1, cp.address_line2, cp.town, cp.county, cp.postcode]:
            if part: addr_lines.append(part)
    default_address_block = '\n'.join(addr_lines)

    import datetime as _dt
    default_date = _dt.date.today().strftime('%d %B %Y').lstrip('0')
    default_ref = f"{project.customer}\n{project.location}" if project.location else project.customer
    default_greeting = f"Dear {first_name}," if first_name else "Dear Sir/Madam,"
    default_thank_you = f"Thank you for your enquiry for shelving at {proj_label}, for which we now have the pleasure of quoting as follows:"
    default_closing = "We trust the above meets with your approval and if you require any further information, please do not hesitate to contact me."
    default_price_label = f'Our price to {default_supply_verb.lower().replace("to ", "")}:'

    display = {
        'quote_date': quote.quote_date or default_date,
        'address_block': quote.address_block or default_address_block,
        'header_ref': quote.header_ref or default_ref,
        'greeting': quote.greeting or default_greeting,
        'thank_you': quote.thank_you or default_thank_you,
        'supply_line': quote.supply_line or default_supply_line,
        'closing': quote.closing or default_closing,
        'signature_name': quote.signature_name or (link.created_by.get_full_name() or link.created_by.username if link.created_by else ''),
        'main_price_label': quote.main_price_label or default_price_label,
    }

    return render(request, 'projects/quote_public.html', {
        'link': link, 'quote': quote, 'project': project, 'cost': cost, 'display': display,
        'is_expired': link.is_expired, 'is_stale': link.is_stale, 'is_retired': link.is_retired,
        'capacity_html': _quote_safe_html(quote.capacity),
        'bay_breakdown_html': _quote_safe_html(quote.bay_breakdown),
        'is_accepted': cost.is_accepted if cost else False,
        'accepted_by_name': cost.accepted_by_name if cost else '',
        'accepted_online_at': timezone.localtime(cost.accepted_online_at) if cost and cost.accepted_online_at else None,
        'photos': quote.attached_photos.all(),
    })


@require_POST
def quote_public_accept(request, token):
    """No-login accept action from the public quote page."""
    link = get_object_or_404(QuoteShareLink, token=token)
    if link.is_expired or link.is_stale or link.is_retired:
        return JsonResponse({'error': 'This link is no longer valid.'}, status=400)
    data = json.loads(request.body)
    name_role = (data.get('name_role') or '').strip()
    if not name_role:
        return JsonResponse({'error': 'Please enter your name and role.'}, status=400)
    quote = link.quote
    cost = quote.cost
    if not cost:
        return JsonResponse({'error': 'No costing found for this quote.'}, status=400)
    if cost.is_accepted:
        # Someone beat them to it — never silently overwrite who gets
        # credited for accepting, since that's the whole point of capturing it.
        when = timezone.localtime(cost.accepted_online_at).strftime('%d %b %Y, %H:%M') if cost.accepted_online_at else ''
        return JsonResponse({'error': f'This quote was already accepted by {cost.accepted_by_name}{f" on {when}" if when else ""}.'}, status=409)
    project = cost.project
    ip = _client_ip(request)
    project.costs.exclude(pk=cost.pk).update(is_accepted=False)
    cost.is_accepted = True
    cost.accepted_online = True
    cost.accepted_by_name = name_role
    cost.accepted_ip = ip
    cost.accepted_online_at = timezone.now()
    cost.save(update_fields=['is_accepted', 'accepted_online', 'accepted_by_name', 'accepted_ip', 'accepted_online_at'])
    ProjectLog.objects.create(project=project, user=None, field='Quote accepted',
        old_value='', new_value=f'Accepted online via share link ({cost.label}) by {name_role} from {ip or "unknown IP"}')
    if project.assigned_to_id:
        Notification.objects.create(
            user=project.assigned_to, type='quote_accepted',
            text=f'{name_role} accepted the quote for {project.project_name}',
            link=reverse('project_edit', kwargs={'pk': project.pk}),
        )
    return JsonResponse({'ok': True})


@login_required
@require_POST
def quote_refresh_price(request, pk, cost_pk=None):
    """Recompute sell price from the current costing and update the quote."""
    project = get_object_or_404(Project, pk=pk)
    if cost_pk:
        cost = get_object_or_404(ProjectCost, pk=cost_pk, project=project)
    else:
        cost = project.costs.filter(is_accepted=True).first() or project.costs.first()
    if not cost:
        return JsonResponse({'error': 'No costing found for this project.'}, status=400)
    sell_price = _calc_sell_price(cost)
    has_delivery = float(cost.delivery or 0) > 0
    has_install  = float(cost.labour   or 0) > 0
    if has_delivery and has_install:
        supply_verb = "supply, deliver and install"
    elif has_delivery:
        supply_verb = "supply and deliver"
    else:
        supply_verb = "supply only (ex. works)"
    price_label = f'Our price to {supply_verb}:'
    quote, _ = ProjectQuote.objects.get_or_create(cost=cost, defaults={'project': project})
    quote.main_price = sell_price
    quote.main_price_label = price_label
    quote.save(update_fields=['main_price', 'main_price_label'])
    return JsonResponse({'ok': True, 'sell_price': float(sell_price), 'price_label': price_label})




@login_required
@require_feature('proforma_invoice')
def proforma_invoice(request, pk, cost_pk=None):
    project = get_object_or_404(Project, pk=pk)
    if cost_pk:
        cost = get_object_or_404(ProjectCost, pk=cost_pk, project=project)
    else:
        cost = project.costs.filter(is_accepted=True).first()
        if not cost:
            messages.error(request, 'Please approve a costing option before creating a Pro-Forma invoice.')
            return redirect('project_edit', pk=pk)
    lines = cost.lines.select_related('product').all()
    buying_total = sum(l.line_total for l in lines)
    uprights = sum(int(float(l.quantity))*2 for l in lines if l.line_type=='frame')
    for acc in cost.accessories.all():
        buying_total += float(acc.unit_price) * uprights
    markup_amount = buying_total * float(cost.markup) / 100
    sell_price = round(buying_total + markup_amount + float(cost.labour) + float(cost.delivery), 2)

    cp = CustomerProfile.objects.filter(name__iexact=project.customer).first()

    pf, created = ProformaInvoice.objects.get_or_create(cost=cost, defaults={'project': project})
    if created:
        pf.order_no = project.sales_order or ''
        from datetime import date
        pf.invoice_date = date.today().strftime('%B %d, %Y').upper()
        # Invoice-to block from customer profile
        block = []
        if cp:
            block.append(cp.name or project.customer)
            for part in [cp.address_line1, cp.address_line2, cp.town, cp.county, cp.postcode]:
                if part:
                    block.append(part)
            if cp.country and cp.country != 'United Kingdom':
                block.append(cp.country)
            if cp.contact_name:
                block.append(f'ATTN – {cp.contact_name}')
        else:
            block.append(project.customer)
        pf.invoice_to = '\n'.join(block)
        pf.ezr_contact = (request.user.get_full_name() or request.user.username)
        pf.description = f'{project.project_name}'
        if project.drawing_number:
            pf.description += f'\n{project.drawing_number}'
        pf.goods_total = sell_price
        pf.deposit_pct = 50
        pf.save()

    if request.method == 'POST':
        pf.order_no      = request.POST.get('order_no', '').strip()
        pf.invoice_date  = request.POST.get('invoice_date', '').strip()
        pf.invoice_to    = request.POST.get('invoice_to', '').strip()
        pf.delivery_to   = request.POST.get('delivery_to', '').strip()
        pf.ezr_contact   = request.POST.get('ezr_contact', '').strip()
        pf.po_number     = request.POST.get('po_number', '').strip()
        pf.requisitioner = request.POST.get('requisitioner', '').strip()
        pf.shipped_via   = request.POST.get('shipped_via', '').strip()
        pf.fob_point     = request.POST.get('fob_point', '').strip()
        pf.terms         = request.POST.get('terms', '').strip()
        pf.comments      = request.POST.get('comments', '').strip()
        pf.description   = request.POST.get('description', '').strip()
        try:
            pf.goods_total = float(request.POST.get('goods_total') or 0)
        except (ValueError, TypeError):
            pf.goods_total = 0
        try:
            pf.deposit_pct = int(request.POST.get('deposit_pct') or 50)
        except (ValueError, TypeError):
            pf.deposit_pct = 50
        sh = request.POST.get('shipping', '').strip()
        pf.shipping = float(sh) if sh else None
        pf.save()
        if request.POST.get('action') == 'print':
            return redirect(f"{request.path}?print=1")
        messages.success(request, 'Pro-forma saved.')
        return redirect('proforma_invoice', pk=pk)

    goods = float(pf.goods_total)
    vat = round(goods * float(pf.vat_rate) / 100, 2)
    shipping = float(pf.shipping) if pf.shipping else 0
    total = round(goods + vat + shipping, 2)
    deposit_amount = round(goods * pf.deposit_pct / 100, 2)
    balance_amount = round(goods - deposit_amount, 2)

    return render(request, 'projects/proforma_invoice.html', {
        'project': project, 'cost': cost, 'pf': pf,
        'goods': goods, 'vat': vat, 'shipping': shipping, 'total': total,
        'deposit_amount': deposit_amount, 'balance_amount': balance_amount,
        'balance_pct': 100 - pf.deposit_pct,
        'print_mode': request.GET.get('print') == '1',
        'project_pk': project.pk if not request.GET.get('print') else None,
    })




@login_required
@require_POST
def quote_photo_attach(request, pk):
    """Upload a photo directly to a quote, or attach one from the library."""
    project = get_object_or_404(Project, pk=pk)
    quote, _ = ProjectQuote.objects.get_or_create(project=project)
    sort = quote.attached_photos.count()

    # From library?
    lib_id = request.POST.get('library_id')
    if lib_id:
        lib = get_object_or_404(QuotePhoto, pk=lib_id)
        photo = QuoteAttachedPhoto.objects.create(
            quote=quote,
            file_data=lib.file_data,
            file_mime=lib.file_mime,
            file_original_name=lib.file_original_name,
            sort_order=sort,
        )
        return JsonResponse({'ok': True, 'pk': photo.pk})

    f = request.FILES.get('file')
    if not f:
        return JsonResponse({'error': 'No file'}, status=400)
    photo = QuoteAttachedPhoto.objects.create(
        quote=quote,
        file_data=f.read(),
        file_mime=f.content_type or '',
        file_original_name=f.name,
        sort_order=sort,
    )
    return JsonResponse({'ok': True, 'pk': photo.pk, 'url': f'/quote/attached-photo/{photo.pk}/file/', 'size': photo.size})




@login_required
@require_POST
def quote_attached_photo_update(request, pk):
    photo = get_object_or_404(QuoteAttachedPhoto, pk=pk)
    data = json.loads(request.body)
    if 'size' in data and data['size'] in ('small', 'medium', 'large'):
        photo.size = data['size']
    if 'sort_order' in data:
        try:
            photo.sort_order = int(data['sort_order'])
        except (ValueError, TypeError):
            pass
    photo.save()
    return JsonResponse({'ok': True})




@login_required
@require_POST
def quote_attached_photo_delete(request, pk):
    get_object_or_404(QuoteAttachedPhoto, pk=pk).delete()
    return JsonResponse({'ok': True})




@login_required
def quote_attached_photo_file(request, pk):
    from django.http import HttpResponse
    photo = get_object_or_404(QuoteAttachedPhoto, pk=pk)
    if photo.file_data:
        resp = HttpResponse(bytes(photo.file_data), content_type=photo.file_mime or 'image/jpeg')
        resp['Content-Disposition'] = f'inline; filename="{photo.file_original_name or "photo.jpg"}"'
        return resp
    return HttpResponse('Not found', status=404)




@login_required
def quote_photos_list(request):
    photos = QuotePhoto.objects.all()
    products = Product.objects.filter(is_active=True).order_by('code')
    return render(request, 'projects/quote_photos_list.html', {'photos': photos, 'products': products})




@login_required
@require_POST
def quote_photo_upload(request):
    title = request.POST.get('title', '').strip()
    f = request.FILES.get('file')
    if not f:
        return JsonResponse({'error': 'Photo required'}, status=400)
    if not title:
        title = f.name
    photo = QuotePhoto.objects.create(
        title=title,
        codes=request.POST.get('codes', '').strip(),
        file_data=f.read(),
        file_mime=f.content_type or '',
        file_original_name=f.name,
        uploaded_by=request.user,
    )
    return JsonResponse({'ok': True, 'pk': photo.pk})




@login_required
@require_POST
def quote_photo_update(request, pk):
    photo = get_object_or_404(QuotePhoto, pk=pk)
    data = json.loads(request.body)
    if 'codes' in data:
        photo.codes = data['codes'].strip()
    if 'title' in data:
        photo.title = data['title'].strip()
    photo.save()
    return JsonResponse({'ok': True})




@login_required
@require_POST
def quote_photo_delete(request, pk):
    get_object_or_404(QuotePhoto, pk=pk).delete()
    return JsonResponse({'ok': True})




@login_required
def quote_photo_file(request, pk):
    from django.http import HttpResponse
    photo = get_object_or_404(QuotePhoto, pk=pk)
    if photo.file_data:
        resp = HttpResponse(bytes(photo.file_data), content_type=photo.file_mime or 'image/jpeg')
        resp['Content-Disposition'] = f'inline; filename="{photo.file_original_name or photo.title}"'
        return resp
    return HttpResponse('Not found', status=404)


