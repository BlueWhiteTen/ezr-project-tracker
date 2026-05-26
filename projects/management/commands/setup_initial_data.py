from django.core.management.base import BaseCommand

class Command(BaseCommand):
    help = 'Seeds required initial data (material prices, accessories)'

    def handle(self, *args, **kwargs):
        from projects.models import MaterialPrice, UprightAccessory
        MaterialPrice.objects.get_or_create(name='chipboard', defaults={'price_per_sqft': 0.50})
        MaterialPrice.objects.get_or_create(name='melamine',  defaults={'price_per_sqft': 0.75})
        UprightAccessory.objects.get_or_create(name='SM Footplates',     defaults={'code': 'SMFOOT', 'unit_price': 2.50, 'sort_order': 1})
        UprightAccessory.objects.get_or_create(name='Trimline Top Caps', defaults={'code': 'TTC',    'unit_price': 0.27, 'sort_order': 2})
        self.stdout.write('Initial data ready.')
