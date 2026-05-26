from django.db import migrations

def seed_accessories(apps, schema_editor):
    UprightAccessory = apps.get_model('projects', 'UprightAccessory')
    UprightAccessory.objects.get_or_create(
        name='SM Footplates',
        defaults={'code': 'SMFOOT', 'unit_price': 2.50, 'sort_order': 1}
    )
    UprightAccessory.objects.get_or_create(
        name='Trimline Top Caps',
        defaults={'code': 'TTC', 'unit_price': 0.27, 'sort_order': 2}
    )

def reverse_seed(apps, schema_editor):
    pass

class Migration(migrations.Migration):
    dependencies = [
        ('projects', '0021_uprightaccessory_projectcost_accessories'),
    ]
    operations = [
        migrations.RunPython(seed_accessories, reverse_seed),
    ]
