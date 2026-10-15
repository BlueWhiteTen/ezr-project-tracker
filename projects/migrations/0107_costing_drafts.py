from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0106_alter_staffprofile_theme'),
    ]

    operations = [
        migrations.AddField(
            model_name='projectcost',
            name='draft_of',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='drafts', to='projects.projectcost',
            ),
        ),
        migrations.AddField(
            model_name='projectcostline',
            name='source_line_id',
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
