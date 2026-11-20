from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import redirect

from .models import Notification
from .permissions import dashboard_url_name


def paginate(request, queryset, per_page=10):
    """Return a Page for ?page=N. Works with querysets and lists."""
    return Paginator(queryset, per_page).get_page(request.GET.get('page'))


def notify(users, title, body='', url='', email=False):
    """Create an in-app notification for each user (accepts a user, list or queryset); with email=True also e-mail them."""
    if hasattr(users, 'pk'):
        users = [users]
    users = list(users)
    Notification.objects.bulk_create([Notification(user=u, title=title, body=body, url=url) for u in users])
    if email:
        from .emails import absolute, send_many
        link = absolute(url)
        outgoing = [(u.email, title, f'Hi {u.first_name or "there"},\n\n{body}\n\n{link}' if body else f'Hi {u.first_name or "there"},\n\n{link}')
                    for u in users if u.email]
        # Only after the surrounding transaction has committed: no SMTP call while database rows are locked, and no
        # e-mail for a change that was rolled back. Outside a transaction this runs immediately.
        transaction.on_commit(lambda: send_many(outgoing))


def go_dashboard(user):
    return redirect(dashboard_url_name(user))


_SIGNATURES = {
    '.png': (b'\x89PNG\r\n\x1a\n',), '.jpg': (b'\xff\xd8\xff',), '.jpeg': (b'\xff\xd8\xff',),
    '.gif': (b'GIF87a', b'GIF89a'), '.pdf': (b'%PDF-',),
}


def _looks_like(file, ext):
    """Check the first bytes against the extension, so a script or HTML file renamed to .png/.pdf is refused."""
    head = b''
    try:
        pos = file.tell()
        head = file.read(16)
        file.seek(pos)
    except (AttributeError, OSError, ValueError):
        return True            # cannot read it here: Django's own image/file validation still applies
    if ext == '.webp':
        return head[:4] == b'RIFF' and head[8:12] == b'WEBP'
    sigs = _SIGNATURES.get(ext)
    return True if sigs is None else any(head.startswith(sig) for sig in sigs)


def validate_upload(file, max_mb, allowed_ext):
    """Return an error string or None. Checks the extension, the size and (for images and PDFs) the real file type."""
    name = file.name.lower()
    if not name.endswith(tuple(allowed_ext)):
        return f"Unsupported file type. Allowed: {', '.join(allowed_ext)}"
    if file.size > max_mb * 1024 * 1024:
        return f'File is too large (max {max_mb} MB).'
    ext = '.' + name.rsplit('.', 1)[-1]
    if not _looks_like(file, ext):
        return f'This does not look like a real {ext[1:].upper()} file. Export it again and re-upload.'
    return None
