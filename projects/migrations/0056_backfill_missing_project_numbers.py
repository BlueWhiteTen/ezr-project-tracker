from django.db import migrations

def backfill(apps, schema_editor):
    Project = apps.get_model('projects', 'Project')
    last = Project.objects.filter(project_number__isnull=False).order_by('-project_number').first()
    next_num = (last.project_number + 1) if last else 1
    missing = Project.objects.filter(project_number__isnull=True).order_by('id')
    for p in missing:
        p.project_number = next_num
        p.save(update_fields=['project_number'])
        next_num += 1

def noop(apps, schema_editor):
    pass

class Migration(migrations.Migration):
    dependencies = [('projects', '0055_backfill_project_number')]
    operations = [migrations.RunPython(backfill, noop)]
