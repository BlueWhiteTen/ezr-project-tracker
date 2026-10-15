"""Helpers for the per-job Fitting Notes page and the fitter link's Installation notes."""
from html.parser import HTMLParser

from django.db.models import Q

from .models import FittingNote, ProjectDocument

ALLOWED = {'b', 'strong', 'i', 'em', 'u', 'br', 'p', 'div', 'ul', 'ol', 'li', 'h3', 'h4'}
VOID = {'br'}


class _Clean(HTMLParser):
    """Tiny allow-list sanitiser: keeps basic formatting tags, drops every attribute and everything else."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.stack, self.skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1
            return
        if tag == 'span' or tag == 'font':
            return
        if tag in ALLOWED and not self.skip:
            self.out.append(f'<{tag}>')
            if tag not in VOID:
                self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip = max(0, self.skip - 1)
            return
        if tag in ALLOWED and tag not in VOID and tag in self.stack and not self.skip:
            while self.stack:
                t = self.stack.pop()
                self.out.append(f'</{t}>')
                if t == tag:
                    break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))

    def close_all(self):
        while self.stack:
            self.out.append(f'</{self.stack.pop()}>')


def sanitize_steps(html):
    c = _Clean()
    c.feed(html or '')
    c.close()
    c.close_all()
    return ''.join(c.out).strip()[:20000]


def latest_picking_list(project):
    return project.picking_lists.order_by('-created_at', '-pk').first()


def matched_notes(pl):
    """Library fitting notes that match the products/templates of a picking list."""
    if not pl:
        return FittingNote.objects.none()
    product_ids = set(pl.items.filter(item_type='stock', product__isnull=False).values_list('product_id', flat=True))
    template_ids = set(pl.templates_used.values_list('pk', flat=True))
    return FittingNote.objects.filter(
        Q(products__in=product_ids) | Q(templates__in=template_ids)
    ).distinct().prefetch_related('products')


def job_notes(project, pl=None):
    """(visible matched notes, hidden matched notes) for one job."""
    pl = pl or latest_picking_list(project)
    hidden_ids = set(project.hidden_fitting_notes.values_list('note_id', flat=True))
    notes = list(matched_notes(pl))
    return [n for n in notes if n.pk not in hidden_ids], [n for n in notes if n.pk in hidden_ids]


def latest_drawing(project):
    return project.documents.filter(doc_type='drawing').order_by('-uploaded_at').first()


def rams_docs(project):
    return list(project.documents.filter(doc_type='rams').order_by('-uploaded_at'))
