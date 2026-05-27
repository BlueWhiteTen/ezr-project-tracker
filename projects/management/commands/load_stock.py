from django.core.management.base import BaseCommand

class Command(BaseCommand):
    help = 'Load stock from fixture if stock table is empty'

    def handle(self, *args, **kwargs):
        from projects.models import Product
        if Product.objects.exists():
            self.stdout.write('Stock already loaded, skipping.')
            return
        from django.core.management import call_command
        call_command('loaddata', 'stock')
        self.stdout.write(f'Stock loaded: {Product.objects.count()} products.')
