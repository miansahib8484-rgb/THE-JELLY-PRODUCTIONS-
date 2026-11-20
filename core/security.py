"""Security logging helpers. Events go to the `jelly.security` logger (console by default), one line each, so that failed sign-ins,
lock-outs, denied access and blocked requests are visible in the server log (and can be watched by fail2ban or a log service)."""
import hashlib
import logging

log = logging.getLogger('jelly.security')


def _clean(value):
    """One line, limited length: a user cannot forge extra log lines with line breaks in a name or URL."""
    return str(value).replace('\r', ' ').replace('\n', ' ')[:200]


def digest(value):
    """A fixed-length, safe cache/log key for any user-supplied text (an e-mail with spaces, line breaks, 10 000 characters ...)."""
    return hashlib.sha256(str(value).strip().lower().encode('utf-8', 'replace')).hexdigest()[:32]


def event(request, what, **extra):
    from .throttle import client_ip                      # (imported here: throttle also logs)
    user = getattr(request, 'user', None)
    uid = user.pk if user is not None and getattr(user, 'is_authenticated', False) else '-'
    detail = ' '.join(f'{k}={_clean(v)}' for k, v in extra.items())
    log.warning('%s ip=%s user=%s method=%s path=%s %s', what, client_ip(request), uid, request.method, _clean(request.path), detail)
