from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('projects', '0122_surveyphoto_id_state'),
    ]

    operations = [
        migrations.CreateModel(
            name='CalendarEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=200)),
                ('kind', models.CharField(choices=[('meeting', 'Meeting'), ('site_visit', 'Site visit'), ('zoom', 'Zoom meeting'), ('other', 'Other')], default='meeting', max_length=12)),
                ('date', models.DateField(db_index=True)),
                ('all_day', models.BooleanField(default=False)),
                ('start_time', models.TimeField(blank=True, null=True)),
                ('end_time', models.TimeField(blank=True, null=True)),
                ('location', models.CharField(blank=True, help_text='Place, address or meeting link', max_length=300)),
                ('notes', models.TextField(blank=True)),
                ('visibility', models.CharField(choices=[('private', 'Private (only me)'), ('everyone', 'Visible to everyone')], default='private', max_length=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='calendar_events', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['date', '-all_day', 'start_time', 'id'],
            },
        ),
    ]
