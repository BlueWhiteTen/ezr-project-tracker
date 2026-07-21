from django.core.management.base import BaseCommand
from django.db import transaction
from projects.models import TeamMessage, Message, Notification


class Command(BaseCommand):
    help = "Clears all team chat and direct messages (one-time reset before inviting new users)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--yes', action='store_true',
            help='Skip the confirmation prompt (use in scripts/CI).',
        )

    def handle(self, *args, **options):
        team_count = TeamMessage.objects.count()
        dm_count = Message.objects.count()
        notif_count = Notification.objects.filter(type='message').count()

        self.stdout.write(
            f"This will permanently delete {team_count} team message(s), "
            f"{dm_count} direct message(s), and {notif_count} related notification(s)."
        )

        if not options['yes']:
            confirm = input("Type 'yes' to continue: ")
            if confirm.strip().lower() != 'yes':
                self.stdout.write(self.style.WARNING("Cancelled — nothing was deleted."))
                return

        with transaction.atomic():
            TeamMessage.objects.all().delete()
            Message.objects.all().delete()
            Notification.objects.filter(type='message').delete()

        self.stdout.write(self.style.SUCCESS("Chat history cleared."))