"""A tiny per-request memo: `SiteSettings.get()` is called from templates, helpers and model properties; within one request it
hits the database once. Outside a request (management commands, migrations, tests that call the model directly) nothing is cached."""
from contextvars import ContextVar

_store = ContextVar('jelly_request_cache', default=None)


def memo(key, factory):
    cache = _store.get()
    if cache is None:
        return factory()
    if key not in cache:
        cache[key] = factory()
    return cache[key]


def per_request(fn):
    """Decorator: inside ONE page request (GET / HEAD only) the function runs once per distinct arguments, and later calls reuse the
    result. The dashboards call the same helper from the sidebar badge, the KPI cards and the "needs attention" box; without this each
    call repeated the same SQL. Not applied to POST (data may change in the middle of the request), nor outside a request."""
    from functools import wraps

    @wraps(fn)
    def wrapper(*args):
        cache = _store.get()
        if not cache or not cache.get('__cacheable__'):
            return fn(*args)
        key = (fn.__module__, fn.__qualname__) + tuple(getattr(a, 'pk', a) for a in args)
        if key not in cache:
            cache[key] = fn(*args)
        return cache[key]
    return wrapper


def safe_memo(key, factory):
    """Like memo(), but only while rendering a page (GET / HEAD): a POST request may change the data half-way through."""
    cache = _store.get()
    if not cache or not cache.get('__cacheable__'):
        return factory()
    if key not in cache:
        cache[key] = factory()
    return cache[key]


def forget(key):
    cache = _store.get()
    if cache is not None:
        cache.pop(key, None)


class RequestCacheMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _store.set({'__cacheable__': request.method in ('GET', 'HEAD')})
        try:
            return self.get_response(request)
        finally:
            _store.reset(token)
