from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0108_staffprofile_show_completed'),
    ]

    operations = [
        migrations.AddField(
            model_name='message',
            name='is_deleted',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='teammessage',
            name='is_deleted',
            field=models.BooleanField(default=False),
        ),
    ]
