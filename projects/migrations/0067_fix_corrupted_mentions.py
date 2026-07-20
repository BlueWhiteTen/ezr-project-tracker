from django.db import migrations
import re

def fix_mentions(apps, schema_editor):
    """Fix comments and messages that were stored with \x01 control chars
    instead of the actual mentioned name, due to a Python regex bug."""
    Comment = apps.get_model('projects', 'Comment')
    Message = apps.get_model('projects', 'Message')
    TeamMessage = apps.get_model('projects', 'TeamMessage')

    for model in (Comment, Message, TeamMessage):
        for obj in model.objects.filter(text__contains='\x01'):
            # \x01 is a corrupted backreference — we can't recover the original
            # name from the stored text, but we can strip the control char cleanly
            obj.text = obj.text.replace('\x01', '').replace('@(', '@').replace('# ', '#')
            # Clean up any double spaces
            obj.text = re.sub(r'  +', ' ', obj.text).strip()
            obj.save(update_fields=['text'])

def reverse_fix(apps, schema_editor):
    pass  # Can't reverse a data cleanup

class Migration(migrations.Migration):
    dependencies = [
        ('projects', '0066_projectpresence'),
    ]
    operations = [
        migrations.RunPython(fix_mentions, reverse_fix),
    ]
