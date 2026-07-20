"""PDF generation views using xhtml2pdf."""
import io
import os
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.contrib.auth.decorators import login_required
from django.conf import settings

from ..models import PurchaseOrder, Project, PickingList, ProjectCost, ProjectQuote, ProformaInvoice


def _render_pdf(template_name, context, filename):
    """Render a Django template to a PDF download response."""
    from xhtml2pdf import pisa

    def link_callback(uri, rel):
        if uri.startswith(settings.STATIC_URL):
            path = uri.replace(settings.STATIC_URL, '', 1)
            for d in getattr(settings, 'STATICFILES_DIRS', []):
                full = os.path.join(d, path)
                if os.path.exists(full):
                    return full
            if settings.STATIC_ROOT:
                full = os.path.join(settings.STATIC_ROOT, path)
                if os.path.exists(full):
                    return full
        if settings.MEDIA_URL and uri.startswith(settings.MEDIA_URL):
            return os.path.join(settings.MEDIA_ROOT, uri.replace(settings.MEDIA_URL, '', 1))
        return uri

    html = render_to_string(template_name, context)
    buf = io.BytesIO()
    pisa.CreatePDF(html, dest=buf, link_callback=link_callback)
    response = HttpResponse(buf.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
def po_pdf(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk)
    lines = po.lines.select_related('product').all()
    delivery_lines = [v for f in ['del_company','del_line1','del_line2','del_city','del_county','del_postcode']
                      if (v := getattr(po, f, ''))]
    filename = f"{po.po_number} - {po.supplier.name} - EZR Shelving Purchase Order.pdf"
    return _render_pdf('projects/po_print.html',
                       {'po': po, 'lines': lines, 'delivery_lines': delivery_lines},
                       filename)


@login_required
def picking_list_pdf(request, project_pk):
    project = get_object_or_404(Project, pk=project_pk)
    pl = get_object_or_404(PickingList, project=project)
    items = pl.items.select_related('product').all()
    return _render_pdf('projects/picking_list_print.html',
                       {'project': project, 'pl': pl, 'items': items},
                       f'PickingList-{project.project_number or project.pk}.pdf')


@login_required
def quote_pdf(request, pk, cost_pk=None):
    """Render the quote as PDF by delegating to customer_quote view logic."""
    from .quotes import customer_quote as quote_view
    # Get the HTML response from the existing view, then convert to PDF
    response = quote_view(request, pk=pk, cost_pk=cost_pk)
    if hasattr(response, 'content'):
        html = response.content.decode('utf-8')
        project = get_object_or_404(Project, pk=pk)
        buf = io.BytesIO()
        from xhtml2pdf import pisa
        pisa.CreatePDF(html, dest=buf)
        pdf = HttpResponse(buf.getvalue(), content_type='application/pdf')
        pdf['Content-Disposition'] = f'attachment; filename="Quote-{project.project_number or pk}.pdf"'
        return pdf
    return response


@login_required
def proforma_pdf(request, pk, cost_pk=None):
    """Render the proforma as PDF."""
    from .quotes import proforma_invoice as proforma_view
    response = proforma_view(request, pk=pk, cost_pk=cost_pk)
    if hasattr(response, 'content'):
        html = response.content.decode('utf-8')
        project = get_object_or_404(Project, pk=pk)
        buf = io.BytesIO()
        from xhtml2pdf import pisa
        pisa.CreatePDF(html, dest=buf)
        pdf = HttpResponse(buf.getvalue(), content_type='application/pdf')
        pdf['Content-Disposition'] = f'attachment; filename="Proforma-{project.project_number or pk}.pdf"'
        return pdf
    return response
