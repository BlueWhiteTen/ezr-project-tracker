from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0110_chat_file_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='staffprofile',
            name='home_layout',
            field=models.JSONField(blank=True, default=dict, help_text='Home page: personal box order and hidden boxes'),
        ),
    ]
