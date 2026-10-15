from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0112_chat_reply_to'),
    ]

    operations = [
        migrations.AddField(
            model_name='todoitem',
            name='due_date',
            field=models.DateField(blank=True, null=True),
        ),
    ]
