"""
Send 2-week reminder emails for upcoming deliveries and installations.
Schedule this to run daily: python manage.py send_reminders
"""
from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.conf import settings
from django.db.models import Q
from datetime import date, timedelta
from projects.models import Project


class Command(BaseCommand):
    help = 'Send 2-week reminder emails for deliveries and installations'

    def handle(self, *args, **kwargs):
        target = date.today() + timedelta(days=14)
        sent = 0

        # Delivery reminders
        deliveries = Project.objects.filter(
            delivery_date=target,
            delivery_required=True,
            assigned_to__isnull=False,
            assigned_to__email__isnull=False,
        ).exclude(status__in=['completed','cancelled'])

        for p in deliveries:
            if not p.assigned_to.email:
                continue
            subject = f"[EZR] Delivery in 2 weeks — {p.customer} / {p.location}"
            body = f"""Hi {p.assigned_to.first_name or p.assigned_to.username},

This is a reminder that the following project has a delivery scheduled in 2 weeks:

  Customer:  {p.customer}
  Location:  {p.location}
  SO Number: {p.sales_order or '—'}
  Delivery:  {p.delivery_date.strftime('%d %B %Y')}
  Status:    {p.get_status_display()}

Please make sure everything is prepared and the delivery is booked.

EZR Project Tracker
"""
            try:
                send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [p.assigned_to.email])
                self.stdout.write(f"  ✓ Delivery reminder → {p.assigned_to.email} ({p.customer})")
                sent += 1
            except Exception as e:
                self.stdout.write(f"  ✗ Failed: {e}")

        # Installation reminders
        installations = Project.objects.filter(
            installation_date=target,
            installation_required=True,
            assigned_to__isnull=False,
        ).exclude(status__in=['completed','cancelled'])

        for p in installations:
            if not p.assigned_to.email:
                continue
            subject = f"[EZR] Installation in 2 weeks — {p.customer} / {p.location}"
            body = f"""Hi {p.assigned_to.first_name or p.assigned_to.username},

This is a reminder that the following project has an installation scheduled in 2 weeks:

  Customer:  {p.customer}
  Location:  {p.location}
  SO Number: {p.sales_order or '—'}
  Install:   {p.installation_date.strftime('%d %B %Y')}
  Status:    {p.get_status_display()}

Please ensure the team and equipment are arranged.

EZR Project Tracker
"""
            try:
                send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [p.assigned_to.email])
                self.stdout.write(f"  ✓ Install reminder → {p.assigned_to.email} ({p.customer})")
                sent += 1
            except Exception as e:
                self.stdout.write(f"  ✗ Failed: {e}")

        self.stdout.write(f"\nDone — {sent} reminder(s) sent for {target.strftime('%d %B %Y')}")
