from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0105_surveyphoto'),
    ]

    operations = [
        migrations.AlterField(
            model_name='staffprofile',
            name='theme',
            field=models.CharField(
                choices=[
                    ('default', 'Default'),
                    ('works_order', 'Works Order'),
                    ('site_signage', 'Site Signage'),
                    ('ledger', 'Ledger'),
                    ('ops_console', 'Ops Console'),
                ],
                default='default',
                help_text='Personal visual style — set on your own profile',
                max_length=20,
            ),
        ),
    ]
