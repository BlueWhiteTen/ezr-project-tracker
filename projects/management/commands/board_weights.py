"""Work out the weight of chipboard and MFC sheets in Stock from the size in their description.

    python manage.py board_weights                      # preview only (numbered list)
    python manage.py board_weights --exclude 3,7,12     # preview without those numbers
    python manage.py board_weights --apply --exclude 3  # save weights (only empty ones)
    python manage.py board_weights --apply --overwrite  # also replace weights already entered

Weight (kg) = length x width x thickness (mm, from the description) x density.
If the description has no thickness: second dimension over 24" (610mm) = 18mm, otherwise 15mm.
Inactive products are skipped. Default density 680 kg/m3. Nothing is saved without --apply.
"""
import re
from django.core.management.base import BaseCommand
from projects.models import Product

RX = re.compile(r'(\d+(?:\.\d+)?)\s*X\s*(\d+(?:\.\d+)?)(?:\s*X\s*(\d+(?:\.\d+)?))?\s*(?:mm)?\s*(?:CHIPBOARD|MFC)\b', re.I)


class Command(BaseCommand):
    help = 'Calculate and fill chipboard weights from the size in the description'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Save the weights (default is preview only)')
        parser.add_argument('--overwrite', action='store_true', help='Also replace weights that are already set')
        parser.add_argument('--exclude', default='', help='Comma-separated list numbers to leave out')
        parser.add_argument('--density', type=float, default=680.0, help='kg per cubic metre (default 680)')

    def handle(self, *args, **o):
        rows = []
        qs = Product.objects.filter(is_active=True).filter(description__iregex=r'chipboard|mfc')
        for p in qs:
            desc = p.description or ''
            if p.code.upper().startswith('LSCB') or desc.upper().startswith('TRIMLINE'):
                continue
            m = RX.search(desc)
            if not m:
                continue
            L, W = float(m.group(1)), float(m.group(2))
            T = float(m.group(3)) if m.group(3) else (18.0 if W > 615 else 15.0)
            if T > 40 and not re.search(r'%dmm' % T, desc, re.I):
                continue
            guessed = '' if m.group(3) else '*'
            rows.append((T, L, W, p.code, p, round(L * W * T / 1e9 * o['density'], 2), guessed))
        rows.sort(key=lambda r: (r[0], r[1], r[2], r[3]))
        skip = {int(x) for x in o['exclude'].split(',') if x.strip().isdigit()}

        self.stdout.write(f"{'#':>4}  {'Code':<24}{'Size mm':<22}{'Calc kg':>8}  {'Existing kg':>11}  Action")
        saved = 0
        for i, (T, L, W, code, p, calc, guessed) in enumerate(rows, 1):
            cur = float(p.weight) if p.weight is not None else None
            if i in skip:
                action = 'excluded'
            elif cur is not None and not o['overwrite']:
                action = 'keep existing'
            elif cur is not None and abs(cur - calc) < 0.005:
                action = 'same'
            else:
                action = 'SET' if o['apply'] else 'would set'
                if o['apply']:
                    p.weight = calc
                    p.save(update_fields=['weight'])
                    saved += 1
            size = f'{L:g} x {W:g} x {T:g}{guessed}'
            self.stdout.write(f"{i:>4}  {code:<24}{size:<22}{calc:>8.2f}  {('%.2f' % cur) if cur is not None else '-':>11}  {action}")
        self.stdout.write(f"\n* = thickness not in the description, assumed from the width rule.\n{len(rows)} boards found, density {o['density']:g} kg/m3."
                          + (f" Saved {saved}." if o['apply'] else ' Preview only - nothing saved.'))
