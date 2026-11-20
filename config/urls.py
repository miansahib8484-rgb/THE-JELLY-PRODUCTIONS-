from django.conf import settings
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path, re_path
from django.views.static import serve

from website.sitemaps import SITEMAPS
from website.views import healthz, robots_txt

handler400 = 'website.views.bad_request'
handler403 = 'website.views.permission_denied'
handler404 = 'website.views.not_found'
handler500 = 'website.views.server_error'

urlpatterns = [
    path(settings.DJANGO_ADMIN_URL, admin.site.urls),
    path('files/', include('core.file_urls')),
    path('account/', include('accounts.urls')),
    path('student/', include('student.urls')),
    path('teacher/', include('teacher.urls')),
    path('admin-panel/', include('adminpanel.urls')),
    path('sitemap.xml', sitemap, {'sitemaps': SITEMAPS}, name='sitemap'),
    path('robots.txt', robots_txt, name='robots'),
    path('healthz/', healthz, name='healthz'),
    path('', include('website.urls')),   # public site last: owns '/'
]


def public_media(request, path):
    """Public uploads only (course images, avatars, blog covers). Certificates, payment proofs and recordings live in
    PRIVATE_MEDIA_ROOT and are never served here. The folder is read per request so it always matches the settings."""
    return serve(request, path, document_root=settings.MEDIA_ROOT)


if settings.DEBUG or settings.SERVE_MEDIA:
    urlpatterns += [re_path(r'^%s(?P<path>.*)$' % settings.MEDIA_URL.lstrip('/'), public_media)]
