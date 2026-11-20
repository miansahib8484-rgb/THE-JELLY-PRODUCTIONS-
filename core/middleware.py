from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.cache import add_never_cache_headers


class ForcePasswordChangeMiddleware:
    """Only for an account whose administrator ticked "Ask them to choose their own password": it must do so before using the site."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated:
            profile = getattr(user, 'profile', None)
            if profile and profile.must_change_password:
                settings_url, logout_url = reverse('accounts:settings'), reverse('accounts:logout')
                if request.path not in (settings_url, logout_url) and not request.path.startswith(('/static/', '/media/')):
                    messages.info(request, 'Please choose your own password before you continue.')
                    return redirect(settings_url)
        return self.get_response(request)


class ContentSecurityPolicyMiddleware:
    """Content-Security-Policy with a per-request nonce: even if an attacker manages to inject HTML, the browser refuses to run any
    script that does not carry this request's nonce, load code from other sites, or submit forms elsewhere.

    DJANGO_CSP=enforce (default) | report (Content-Security-Policy-Report-Only: log violations in the browser console only) | off.
    Templates add  nonce="{{ request.csp_nonce }}"  to their own inline <script> blocks."""

    EMBED_HOSTS = 'https://www.youtube.com https://www.youtube-nocookie.com https://player.vimeo.com https://drive.google.com'

    def __init__(self, get_response):
        self.get_response = get_response
        self.mode = getattr(settings, 'CSP_MODE', 'enforce')

    def policy(self, nonce):
        return '; '.join([
            "default-src 'self'",
            f"script-src 'self' 'nonce-{nonce}'",
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",     # many inline style="" attributes in the templates
            "font-src 'self' https://fonts.gstatic.com data:",
            "img-src 'self' data: https:",                                          # course thumbnails may be linked from any https site
            "media-src 'self' blob:",
            f"frame-src {self.EMBED_HOSTS}",                                       # YouTube / Vimeo / Google Drive lesson embeds only
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ])

    def __call__(self, request):
        import secrets
        request.csp_nonce = secrets.token_urlsafe(16)
        response = self.get_response(request)
        # the site never needs the camera, microphone, location or payment APIs: switch them off for every page and embedded frame
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated and 'text/html' in response.get('Content-Type', '') and 'Cache-Control' not in response:
            add_never_cache_headers(response)      # after signing out, the Back button must not show somebody's dashboard from the cache
        response.headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=(), payment=(), usb=()')
        if self.mode == 'off' or request.path.startswith('/' + settings.DJANGO_ADMIN_URL) or 'Content-Security-Policy' in response:
            return response
        header = 'Content-Security-Policy-Report-Only' if self.mode == 'report' else 'Content-Security-Policy'
        response[header] = self.policy(request.csp_nonce)
        return response
