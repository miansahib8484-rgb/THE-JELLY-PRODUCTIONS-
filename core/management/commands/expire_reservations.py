"""Release seats held by reservations that never sent any payment proof, so unpaid sign-ups cannot keep a batch "full"."""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core import services
from core.models import Enrollment


class Command(BaseCommand):
    help = 'Cancel pending reservations older than RESERVATION_HOLD_HOURS that have no transaction id or payment screenshot.'

    def handle(self, *args, **options):
        hours = settings.RESERVATION_HOLD_HOURS
        if hours <= 0:
            self.stdout.write('RESERVATION_HOLD_HOURS is 0: nothing to do.')
            return
        cutoff = timezone.now() - timedelta(hours=hours)
        released = 0
        for e in Enrollment.objects.filter(status='pending_payment', enrolled_at__lt=cutoff).select_related('user', 'course', 'payment'):
            payment = getattr(e, 'payment', None)
            if payment is not None and payment.has_proof:
                continue            # proof was sent: an admin is reviewing it, never expire that
            services.cancel_enrollment(e)
            released += 1
        self.stdout.write(f'Released {released} unpaid reservation(s) older than {hours} hours.')
