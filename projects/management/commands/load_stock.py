from django.core.management.base import BaseCommand

class Command(BaseCommand):
    help = 'Load stock from fixture — updates prices if products already exist'

    def handle(self, *args, **kwargs):
        from projects.models import Product
        if not Product.objects.exists():
            from django.core.management import call_command
            call_command('loaddata', 'stock')
            self.stdout.write(f'Stock loaded: {Product.objects.count()} products.')
        else:
            # Update prices from fixture
            import json, os
            fixture_path = os.path.join(os.path.dirname(__file__), '../../fixtures/stock.json')
            fixture_path = os.path.normpath(fixture_path)
            with open(fixture_path) as f:
                items = json.load(f)
            updated = 0
            for item in items:
                fields = item['fields']
                if fields.get('sales_price', 0) > 0:
                    Product.objects.filter(code=fields['code']).update(sales_price=fields['sales_price'])
                    updated += 1
            self.stdout.write(f'Prices updated for {updated} products.')
