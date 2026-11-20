"""Plain-text e-mails for the moments that matter. They never break a request: a mail error is logged, not raised."""
import logging

from django.conf import settings
from django.core.mail import EmailMessage, get_connection

log = logging.getLogger(__name__)


def absolute(path):
    """Turn '/student/billing/' into a full link (SITE_URL comes from the environment)."""
    if not path or path.startswith('http'):
        return path or settings.SITE_URL
    return settings.SITE_URL.rstrip('/') + path


def send_many(messages):
    """messages = [(to_address, subject, body), ...] sent over a single SMTP connection. Returns how many were sent."""
    from .models import SiteSettings
    name = SiteSettings.get().site_name
    batch = [EmailMessage(subject, f'{body}\n\n{name}', settings.DEFAULT_FROM_EMAIL, [to]) for to, subject, body in messages if to]
    if not batch:
        return 0
    try:
        return get_connection(fail_silently=False).send_messages(batch)
    except Exception:       # SMTP down, bad address, ... the in-app notification still exists
        log.exception('Could not send %s e-mail(s), first subject "%s"', len(batch), batch[0].subject)
        return 0


def send_plain(to, subject, body):
    return bool(send_many([(to, subject, body)]))
