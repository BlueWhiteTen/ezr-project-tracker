"""
Post the 'This week' summary into EZR Chat.
Run every Monday morning, e.g. crontab:  30 7 * * 1  cd /var/www/ezr && venv/bin/python manage.py monday_summary
Use --dry-run to just print it.
"""
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from projects.models import TeamMessage
from projects.weekly import weekly_summary, summary_text


class Command(BaseCommand):
    help = 'Post the weekly summary to EZR Chat'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--user', help='Username to post as (default: first superuser)')

    def handle(self, *args, **o):
        text = summary_text(weekly_summary())
        if o['dry_run']:
            self.stdout.write(text)
            return
        qs = User.objects.filter(is_active=True)
        poster = qs.filter(username=o['user']).first() if o.get('user') else None
        poster = poster or qs.filter(is_superuser=True).order_by('pk').first() or qs.order_by('pk').first()
        if not poster:
            self.stderr.write('No user to post as.')
            return
        TeamMessage.objects.create(user=poster, text=text)
        self.stdout.write(f'Posted as {poster.username}.')
