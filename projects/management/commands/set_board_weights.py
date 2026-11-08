"""Set the weight (kg) of chipboard / MFC sheets in Stock from a fixed, reviewed list.

    python manage.py set_board_weights --dry-run     # show what would change
    python manage.py set_board_weights               # save (only products with no weight yet)
    python manage.py set_board_weights --overwrite   # also replace weights already entered

Weight = length x width x thickness x 680 kg/m3 (thickness from the description; where it is
missing, width over 24" = 18mm, otherwise 15mm). Codes not found, or deactivated, are skipped.
"""
from django.core.management.base import BaseCommand
from projects.models import Product

WEIGHTS = {
    '36X303': 2.25,
    '36X341': 2.53,
    '36X379': 2.82,
    '39.5X303': 2.47,
    '39.5X341': 2.78,
    '39.5X379': 3.09,
    '48X303': 3.01,
    '48X341': 3.38,
    '48X379': 3.76,
    '146X914': 1.37,
    '381X300UE': 1.17,
    '381X329UE': 1.28,
    '457X300UE': 1.4,
    '24X5': 0.77,
    '598X127': 0.77,
    '24X12': 1.89,
    '24X15': 2.36,
    '24X15MFC2SE': 2.36,
    '24X18': 2.82,
    '24X24': 3.77,
    '30X12': 2.36,
    '30X15MFC2SE': 2.95,
    '30X15MFCUE': 2.95,
    '30X24': 4.72,
    '30X30MFC2SE': 5.89,
    '903X127': 1.17,
    '36X5': 1.18,
    '36X12': 2.83,
    '36X12EAR': 2.83,
    '36X12MFC': 2.83,
    '36X12WHITEMFC-UE': 2.83,
    '36X15': 3.54,
    '36X15EAR': 3.54,
    '36X15MFC': 3.54,
    '36X15MFC2SE': 3.54,
    '36X18': 4.25,
    '36X18CMFC': 4.25,
    '36X18MFC': 4.25,
    '36X18UE': 4.25,
    '36X21': 4.95,
    '36X24': 5.67,
    '36X24EAR': 5.67,
    '36X24MFC': 5.67,
    '36X14': 3.35,
    '36X36MFC2LE': 8.54,
    '39.5X12': 3.11,
    '39.5X12MFC/L2SE': 3.11,
    '39.5X12MFCUE': 3.11,
    '39.5X15': 3.89,
    '39.5X15EAR': 3.89,
    '39.5X15MFC/L2SE': 3.89,
    '39.5X15WHITEMFC-UE18MMCHIPBOAR': 3.89,
    '39.5X18': 4.66,
    '39.5X18MFC/L1SE': 4.66,
    '39.5X18MFC/L2SE': 4.66,
    '39.5X18UE': 4.66,
    '39.5X21': 5.44,
    '39.5X24': 6.22,
    '39.5X24MFCL2SEWHITE': 6.22,
    '39.5X24UE': 6.22,
    '42X12': 3.32,
    '42X15': 4.14,
    '42X18': 4.97,
    '42X24': 6.63,
    '43.5X15MFC2SE': 4.27,
    '43.5X18': 5.13,
    '43.5X18UE': 5.13,
    '43.5X30MFC2SE': 8.55,
    '48X5': 1.56,
    '48X12': 3.78,
    '48X12MFC': 3.78,
    '48X12MFC/L2SE': 3.78,
    '48X12WHITEMFC-UE18MMCHIPBOARD': 3.78,
    '48X15EAR': 4.69,
    '48X15': 4.73,
    '48X15MFC': 4.73,
    '48X15MFC/L2SE': 4.73,
    '48X18': 5.67,
    '48X18CMFC': 5.67,
    '48X18MFC': 5.67,
    '48X18MFC2SE': 5.67,
    '48X18WHITEMFCUNEDGED': 5.67,
    '48X21': 6.61,
    '48X21C': 6.61,
    '48X24': 7.57,
    '48X24MFC': 7.57,
    '48X24MFC-UE': 7.57,
    '48X24MFC2SE': 7.57,
    '1260X361': 4.64,
    '60X12': 4.73,
    '60X15': 5.91,
    '60X15EAR': 5.91,
    '60X18': 7.09,
    '60X21': 8.27,
    '60X24': 9.46,
    '60X24MFC': 9.49,
    '66X12': 5.21,
    '66X15': 6.51,
    '66X18': 7.81,
    '66X24': 10.43,
    '72X12': 5.68,
    '72X12MFC': 5.68,
    '72X15': 7.09,
    '72X15MFC': 7.09,
    '72X18': 8.51,
    '72X18MFC': 8.51,
    '72X24': 11.36,
    '72X24MFC': 11.36,
    '96X15MFC': 9.47,
    '96X18MFC': 11.36,
    '96X24MFC': 15.16,
    '36X30': 8.5,
    '36X30CMFC': 8.5,
    '36X30EAR': 8.5,
    '36X30MFC': 8.5,
    '36X36': 10.2,
    '36X36MFC': 10.2,
    '36X27': 7.67,
    '945X595': 6.88,
    '945X895': 10.35,
    '945X1195': 13.82,
    '39.5X377': 4.61,
    '39.5X27': 8.38,
    '39.5X27MFC1SE': 8.4,
    '39.5X30': 9.33,
    '39.5X30EAR': 9.33,
    '39.5X30MFC/L2SE': 9.33,
    '39.5X36': 11.2,
    '39.5X36MFC/2SE': 11.2,
    '42X30': 9.94,
    '42X36': 11.94,
    '1145X595': 8.34,
    '1145X895': 12.54,
    '1145X1195': 16.75,
    '1195X1195': 17.48,
    '48X377': 5.61,
    '48X15WHITEMFC-UE18MMCHIPBOARD': 5.67,
    '48X24-X18MM': 9.08,
    '48X27': 10.2,
    '48X27MFCEAR': 10.2,
    '48X27MFC2SE': 10.21,
    '48X30': 11.34,
    '48X30CMFC': 11.34,
    '48X30EAR': 11.34,
    '48X30MFC': 11.34,
    '48X30MFC2SE': 11.34,
    '48X30WHITEMFC-UE18MMCHIPBOARD': 11.34,
    '1216X775': 11.53,
    '48X36': 13.62,
    '48X36MFC': 13.62,
    '48X36MFC-UE': 13.62,
    '48X48': 18.16,
    '1347X1195': 19.7,
    '1495X595': 10.89,
    '1495X895': 16.38,
    '1495X1195': 21.87,
    '60X18MFC': 8.51,
    '60X30': 14.19,
    '60X36': 17.03,
    '60X48': 22.71,
    '66X18WHITEMFC-UE18MMCHIPBOARD': 9.38,
    '66X30': 15.63,
    '66X36': 18.77,
    '1795X595': 13.07,
    '1795X895': 19.66,
    '1795X1195': 26.26,
    '72X30': 17.02,
    '72X30MFC': 17.02,
    '72X36': 20.44,
    '72X36MFC': 20.44,
    '1845X595': 13.44,
    '1845X895': 20.21,
    '2245X595': 16.35,
    '2245X895': 24.59,
    '2245X1195': 32.84,
    '2395X595': 17.44,
    '2395X895': 26.24,
    '2395X1195': 35.03,
    '96X30MFC': 22.72,
    '96X36MFC': 27.28,
    '2400X600/38MM': 37.21,
}


class Command(BaseCommand):
    help = 'Set chipboard / MFC weights from the reviewed list'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--overwrite', action='store_true', help='Replace weights already set')

    def handle(self, *args, **o):
        n_set = kept = same = 0
        missing = []
        verb = 'would set' if o['dry_run'] else 'set'
        for code, kg in WEIGHTS.items():
            p = Product.objects.filter(code=code, is_active=True).first()
            if not p:
                missing.append(code)
                continue
            cur = float(p.weight) if p.weight is not None else None
            if cur is not None and abs(cur - kg) < 0.005:
                same += 1
            elif cur is not None and not o['overwrite']:
                kept += 1
                self.stdout.write('keep  %-28s existing %.2f (list %.2f)' % (code, cur, kg))
            else:
                n_set += 1
                self.stdout.write('%-9s %-28s %.2f%s' % (verb, code, kg, (' (was %.2f)' % cur) if cur is not None else ''))
                if not o['dry_run']:
                    p.weight = kg
                    p.save(update_fields=['weight'])
        self.stdout.write('\n%d in list: %d %s, %d kept (existing weight), %d already correct, %d not found/inactive.'
                          % (len(WEIGHTS), n_set, 'would be set' if o['dry_run'] else 'set', kept, same, len(missing)))
        if missing:
            self.stdout.write('Not found / inactive: ' + ', '.join(missing))
