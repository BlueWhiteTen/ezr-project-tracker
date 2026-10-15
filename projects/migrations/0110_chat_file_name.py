from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0109_message_is_deleted'),
    ]

    operations = [
        migrations.AddField(
            model_name='message',
            name='file_name',
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name='teammessage',
            name='file_name',
            field=models.CharField(blank=True, max_length=200),
        ),
    ]
