from django.db import migrations

def link_quotes_to_costs(apps, schema_editor):
    """Link existing ProjectQuote and ProformaInvoice records to their
    project's ProjectCost (now 'Option A'), and label that cost."""
    ProjectCost = apps.get_model('projects', 'ProjectCost')
    ProjectQuote = apps.get_model('projects', 'ProjectQuote')
    ProformaInvoice = apps.get_model('projects', 'ProformaInvoice')

    # Label all existing costs as Option A
    ProjectCost.objects.filter(label='').update(label='Option A')
    ProjectCost.objects.all().update(label='Option A')

    # Link each quote to its project's cost
    for quote in ProjectQuote.objects.filter(cost__isnull=True, project__isnull=False):
        cost = ProjectCost.objects.filter(project=quote.project).first()
        if cost:
            quote.cost = cost
            quote.save(update_fields=['cost'])

    # Link each proforma to its project's cost
    for pf in ProformaInvoice.objects.filter(cost__isnull=True, project__isnull=False):
        cost = ProjectCost.objects.filter(project=pf.project).first()
        if cost:
            pf.cost = cost
            pf.save(update_fields=['cost'])

def reverse_migrate(apps, schema_editor):
    pass

class Migration(migrations.Migration):
    dependencies = [
        ('projects', '0069_alter_projectcost_options_proformainvoice_cost_and_more'),
    ]
    operations = [
        migrations.RunPython(link_quotes_to_costs, reverse_migrate),
    ]
