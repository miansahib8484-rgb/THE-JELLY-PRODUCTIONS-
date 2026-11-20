"""Project settings for The Jelly Productions.

Development:  set DJANGO_DEBUG=1 (a throw-away secret key is used).
Production:   DJANGO_DEBUG is off by default and DJANGO_SECRET_KEY is required; see .env.example for every variable.
"""
import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.environ.get('DJANGO_DEBUG', '0') == '1'
TESTING = sys.argv[1:2] == ['test'] or 'pytest' in sys.modules      # only `manage.py test`, never a server whose command line happens to contain the word

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', '')
if not SECRET_KEY:
    if DEBUG or TESTING:
        # development and the test runner only: production refuses to start without a real key (see below)
        SECRET_KEY = 'dev-only-change-me-before-deploying'  # nosec B105
    else:
        raise ImproperlyConfigured('Set the DJANGO_SECRET_KEY environment variable (or DJANGO_DEBUG=1 for local development).')
ALLOWED_HOSTS = [h.strip() for h in os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get('DJANGO_CSRF_TRUSTED_ORIGINS', '').split(',') if o.strip()]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # project apps
    'core',        # shared models, permissions, menus, template tags
    'accounts',    # register / login / password reset / profile / settings
    'website',     # public site (no login needed)
    'django.contrib.sitemaps',
    'student',     # student dashboard
    'teacher',     # teacher dashboard
    'adminpanel',  # admin dashboard
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'core.request_cache.RequestCacheMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'core.middleware.ForcePasswordChangeMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'core.middleware.ContentSecurityPolicyMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [BASE_DIR / 'templates'],
    'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.debug',
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
        'core.context_processors.portal',
    ]},
}]

WSGI_APPLICATION = 'config.wsgi.application'

def _database_from_url(url):
    """postgres://user:password@host:5432/dbname  ->  Django DATABASES entry (no extra dependency needed)."""
    from urllib.parse import parse_qs, unquote, urlparse
    u = urlparse(url)
    if u.scheme not in ('postgres', 'postgresql'):
        raise ImproperlyConfigured('DATABASE_URL must start with postgres:// (SQLite is the default when it is not set).')
    cfg = {'ENGINE': 'django.db.backends.postgresql', 'NAME': u.path.lstrip('/'), 'USER': unquote(u.username or ''),
           'PASSWORD': unquote(u.password or ''), 'HOST': u.hostname or '', 'PORT': str(u.port or ''), 'CONN_MAX_AGE': 60}
    # managed PostgreSQL (Neon, Supabase, Render, RDS ...) usually needs  ?sslmode=require  : keep the options that matter
    query = {k: v[-1] for k, v in parse_qs(u.query).items()}
    options = {k: query[k] for k in ('sslmode', 'sslrootcert', 'sslcert', 'sslkey', 'connect_timeout') if k in query}
    if options:
        cfg['OPTIONS'] = options
    return cfg


if os.environ.get('DATABASE_URL'):
    DATABASES = {'default': _database_from_url(os.environ['DATABASE_URL'])}
else:
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'db.sqlite3'}}
# Every request runs in one transaction: a view that fails half way never leaves half-written data behind.
# (E-mails are queued with transaction.on_commit, so they are only sent for requests that succeeded.)
DATABASES['default']['ATOMIC_REQUESTS'] = True

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
AUTHENTICATION_BACKENDS = ['accounts.backends.EmailOrUsernameBackend']

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Karachi'   # class times are shown in Pakistan time (PKT)
USE_I18N = True
USE_TZ = True
PASSWORD_RESET_TIMEOUT = 2 * 60 * 60          # reset links stay valid for two hours (Django's default is three days)

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATIC_ROOT.mkdir(exist_ok=True)       # keeps WhiteNoise quiet before the first collectstatic
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage' if (DEBUG or TESTING)
                    else 'whitenoise.storage.CompressedStaticFilesStorage'},
}
WHITENOISE_USE_FINDERS = DEBUG
SERVE_MEDIA = os.environ.get('DJANGO_SERVE_MEDIA', '1') == '1'   # public uploads via Django; use nginx/S3 for heavy traffic (see README)
MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'                 # public uploads: course images, avatars
PRIVATE_MEDIA_ROOT = BASE_DIR / 'private_media'  # NOT served: certificates, payment proofs, recordings
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = 'accounts:login'
LOGIN_REDIRECT_URL = 'accounts:redirect'
LOGOUT_REDIRECT_URL = 'website:home'

# Uploads: recordings can be large; Django streams big files to disk.
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
# Unpaid seat reservations (no transaction id / screenshot sent) are released after this many hours by
# `python manage.py expire_reservations` (run it hourly from cron). 0 switches the clean-up off.
RESERVATION_HOLD_HOURS = int(os.environ.get('RESERVATION_HOLD_HOURS', '72'))
MAX_RECORDING_MB = 500
MAX_IMAGE_MB = 5
MAX_CERTIFICATE_MB = 10

# Blog posts are plain Markdown files in this folder (see README).
BLOG_CONTENT_DIR = BASE_DIR / 'content' / 'sample_blog'  # only used by seed_data

# Email: set EMAIL_HOST (and the other EMAIL_* variables) to send real mail; otherwise mail prints to the console.
if os.environ.get('EMAIL_HOST'):
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_HOST = os.environ['EMAIL_HOST']
    EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
    EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
    EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
    EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', '1') == '1'
else:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
EMAIL_TIMEOUT = 10        # seconds: a hung SMTP server must never tie up a worker forever
# The first real host name of DJANGO_ALLOWED_HOSTS. When SITE_URL / DEFAULT_FROM_EMAIL are forgotten on a live server, e-mails would
# otherwise carry links to http://localhost:8000 and come from no-reply@localhost; this fills in the real site instead.
_PRIMARY_HOST = '' if DEBUG else next((h for h in ALLOWED_HOSTS if h not in ('localhost', '127.0.0.1', '*') and not h.startswith('.')), '')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL') or f'The Jelly Productions <no-reply@{_PRIMARY_HOST or "localhost"}>'
SERVER_EMAIL = DEFAULT_FROM_EMAIL
SITE_URL = os.environ.get('SITE_URL') or (f'https://{_PRIMARY_HOST}' if _PRIMARY_HOST else 'http://localhost:8000')   # used in e-mail links

if TESTING:  # fast password hashing for the test-suite only
    PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    if os.environ.get('DJANGO_BEHIND_PROXY', '0') == '1':      # e.g. nginx / a PaaS terminating HTTPS
        SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    if not TESTING and os.environ.get('DJANGO_SSL_REDIRECT', '1') == '1':
        SECURE_SSL_REDIRECT = True
        SECURE_HSTS_SECONDS = int(os.environ.get('DJANGO_HSTS_SECONDS', '31536000'))
        SECURE_HSTS_INCLUDE_SUBDOMAINS = True
        SECURE_HSTS_PRELOAD = os.environ.get('DJANGO_HSTS_PRELOAD', '0') == '1'     # only after you read hstspreload.org
    SECURE_REDIRECT_EXEMPT = [r'^healthz/$']          # health probes arrive over plain http inside the network
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'


# Rate limiting of public forms (see core/throttle.py). Switched off while running the test-suite.
THROTTLE_ENABLED = not TESTING
# All per-address limits are multiplied by this number. Many students behind ONE address (a classroom, a lab, an office, mobile data) all count as
# one visitor: raise it (for example THROTTLE_MULTIPLIER=3) on a workshop day, lower it never below 1.
THROTTLE_MULTIPLIER = max(1, int(os.environ.get('THROTTLE_MULTIPLIER', '1') or 1))
TRUST_X_FORWARDED_FOR = os.environ.get('DJANGO_BEHIND_PROXY', '0') == '1'

# Cache: per-process memory by default. Set REDIS_URL (and `pip install redis`) when you run several workers.
if os.environ.get('REDIS_URL'):
    CACHES = {'default': {'BACKEND': 'django.core.cache.backends.redis.RedisCache', 'LOCATION': os.environ['REDIS_URL']}}
else:
    CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'jelly'}}


# Logging: everything to the console (Docker / systemd / PaaS collect it); errors from our own code are always visible.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {'plain': {'format': '%(asctime)s %(levelname)s %(name)s: %(message)s'}},
    'handlers': {'console': {'class': 'logging.StreamHandler', 'formatter': 'plain'}},
    'root': {'handlers': ['console'], 'level': os.environ.get('DJANGO_LOG_LEVEL', 'INFO')},
    'loggers': {'django.request': {'handlers': ['console'], 'level': 'ERROR', 'propagate': False},
                # failed sign-ins, lock-outs, blocked requests, denied access: one line each (see core/security.py)
                'jelly.security': {'handlers': ['console'], 'level': 'INFO', 'propagate': False}},
}


# Optional: e-mail the traceback of every 500 error to these people. DJANGO_ADMINS="Name:me@example.com,Other:you@example.com"
ADMINS = [tuple(part.strip() for part in a.split(':', 1)) for a in os.environ.get('DJANGO_ADMINS', '').split(',') if ':' in a]
if ADMINS and not DEBUG:
    LOGGING['handlers']['mail_admins'] = {'class': 'django.utils.log.AdminEmailHandler', 'level': 'ERROR', 'include_html': False}
    LOGGING['loggers']['django.request']['handlers'].append('mail_admins')

# A page whose security token (CSRF) is old or missing gets a friendly page instead of the browser's bare 403.
CSRF_FAILURE_VIEW = 'website.views.csrf_failure'

# Content-Security-Policy: enforce (default), report (browser console only) or off. See core/middleware.py.
CSP_MODE = os.environ.get('DJANGO_CSP', 'enforce')

# Where Django's built-in admin lives. Change it in production to keep bots from probing the default address.
DJANGO_ADMIN_URL = os.environ.get('DJANGO_ADMIN_URL', 'django-admin/').strip('/') + '/'
