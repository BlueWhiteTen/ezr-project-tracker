from django.db import migrations, models


class Migration(migrations.Migration):
    """Brings Django's idea of SurveyPhoto.id (BigAutoField) in line with the model so the
    'changes not reflected in a migration' warning goes away. State only: nothing is changed
    in the database, so the table (which holds the photo data) is not rewritten."""

    dependencies = [
        ('projects', '0121_project_bin'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name='surveyphoto',
                    name='id',
                    field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
            ],
            database_operations=[],
        ),
    ]
