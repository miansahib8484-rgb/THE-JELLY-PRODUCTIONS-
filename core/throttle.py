"""Small rate limiter for public forms (login, register, contact, newsletter, promo check). Uses Django's cache, so
point CACHES at Redis/Memcached in production when you run more than one worker (see .env.example)."""
from functools import wraps

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse

from .errors import error_response
from .security import event


def client_ip(request):
    """The connecting address. Behind a proxy, set DJANGO_BEHIND_PROXY=1 and have the proxy overwrite X-Forwarded-For."""
    if getattr(settings, 'TRUST_X_FORWARDED_FOR', False):
        fwd = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if fwd:
            return fwd.split(',')[-1].strip()
    return request.META.get('REMOTE_ADDR', 'unknown')


def hit(scope, ident, limit, window):
    """Count one attempt. Returns True while the caller is still under the limit."""
    key = f'throttle:{scope}:{ident}'
    cache.add(key, 0, window)
    try:
        n = cache.incr(key)
    except ValueError:            # key expired between add and incr
        cache.set(key, 1, window)
        n = 1
    return n <= limit


def throttle(scope, limit, window, methods=('POST',), json=False):
    """Allow at most `limit` requests per `window` seconds per IP address for the given HTTP methods."""
    def deco(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            allowed = limit * getattr(settings, 'THROTTLE_MULTIPLIER', 1)
            if getattr(settings, 'THROTTLE_ENABLED', True) and request.method in methods and not hit(scope, client_ip(request), allowed, window):
                msg = 'Too many attempts. Please wait a few minutes and try again.'
                event(request, 'rate_limited', scope=scope)
                if json:
                    resp = JsonResponse({'error': msg}, status=429)
                    resp['Retry-After'] = str(window)
                    return resp
                return error_response(request, 429, 'Too many attempts', msg, retry_after=window)
            return view(request, *args, **kwargs)
        return wrapper
    return deco


def honeypot_tripped(request, field='website'):
    """Bots fill every input. The hidden `website` field must stay empty."""
    return bool(request.POST.get(field, '').strip())


def limited(scope, ident, limit):
    """True when `ident` already used up `limit` failures. Does not count anything itself."""
    if not getattr(settings, 'THROTTLE_ENABLED', True):
        return False
    return (cache.get(f'throttle:{scope}:{ident}') or 0) >= limit


def record_failure(scope, ident, window):
    if getattr(settings, 'THROTTLE_ENABLED', True):
        hit(scope, ident, 10 ** 9, window)
