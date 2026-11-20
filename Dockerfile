FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt "psycopg[binary]==3.2.3" "redis==5.2.0"
COPY . .
# the app never runs as root; uploads (media, private_media) live in volumes owned by it
RUN useradd --system --create-home --uid 10001 app \
    && mkdir -p /app/media /app/private_media /app/staticfiles \
    && DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput \
    && chown -R app:app /app/media /app/private_media /app/staticfiles
USER app
EXPOSE 8000
# The probe talks to 127.0.0.1, but Django only answers hosts listed in DJANGO_ALLOWED_HOSTS (otherwise HTTP 400 and the container stays
# "unhealthy" for ever), so it sends the first allowed host name as its Host header.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD python -c "import os, urllib.request; h = (os.environ.get('DJANGO_ALLOWED_HOSTS') or 'localhost').split(',')[0].strip().lstrip('.'); h = 'localhost' if h in ('', '*') else h; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000/healthz/', headers={'Host': h}), timeout=4)" || exit 1
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 120 --access-logfile -"]
