from django.db import migrations

def backfill(apps, schema_editor):
    Project = apps.get_model('projects', 'Project')
    qs = Project.objects.order_by('id')
    for i, p in enumerate(qs, start=1):
        p.project_number = i
        p.save(update_fields=['project_number'])

def noop(apps, schema_editor):
    pass

class Migration(migrations.Migration):
    dependencies = [('projects', '0054_project_project_number')]
    operations = [migrations.RunPython(backfill, noop)]
