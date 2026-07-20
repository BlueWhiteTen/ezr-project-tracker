from django.db import migrations

def backfill(apps, schema_editor):
    Project = apps.get_model('projects', 'Project')
    for p in Project.objects.filter(assigned_to__isnull=True, created_by__isnull=False):
        p.assigned_to_id = p.created_by_id
        p.save(update_fields=['assigned_to'])

def noop(apps, schema_editor):
    pass

class Migration(migrations.Migration):
    dependencies = [('projects', '0056_backfill_missing_project_numbers')]
    operations = [migrations.RunPython(backfill, noop)]
