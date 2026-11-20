from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0123_calendarevent'),
    ]

    operations = [
        migrations.AddField(
            model_name='calendarevent',
            name='remind',
            field=models.CharField(blank=True, choices=[('', 'No reminder'), ('0', 'At the start time'), ('15', '15 minutes before'), ('60', '1 hour before'), ('1440', '1 day before'), ('morning', 'Morning of the day (8:00)')], default='', max_length=8),
        ),
        migrations.AddField(
            model_name='reminder',
            name='event',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='reminders', to='projects.calendarevent'),
        ),
        migrations.AlterField(
            model_name='reminder',
            name='project',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='reminders', to='projects.project'),
        ),
    ]
