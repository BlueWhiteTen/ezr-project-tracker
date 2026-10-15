from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0107_costing_drafts'),
    ]

    operations = [
        migrations.AddField(
            model_name='staffprofile',
            name='show_completed',
            field=models.BooleanField(
                default=True,
                help_text='Dashboard: include completed projects in the list (each person chooses for themselves)',
            ),
        ),
    ]
