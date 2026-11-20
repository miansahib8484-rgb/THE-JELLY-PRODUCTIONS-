"""Permission-checked downloads for private files (with HTTP Range support so videos can seek)."""
import mimetypes
import os

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404

from .models import Certificate, ClassRecording, LiveClass, Payment
from .permissions import get_role


def _chunks(path, start, length, size=64 * 1024):
    with open(path, 'rb') as f:
        f.seek(start)
        while length > 0:
            data = f.read(min(size, length))
            if not data:
                break
            length -= len(data)
            yield data


def serve(request, fieldfile, download=False):
    try:
        path = fieldfile.path
        total = os.path.getsize(path)
    except (ValueError, OSError) as exc:
        raise Http404 from exc
    name = os.path.basename(fieldfile.name)
    ctype = mimetypes.guess_type(name)[0] or 'application/octet-stream'
    rng = request.headers.get('Range', '')
    if rng.startswith('bytes=') and ',' not in rng and not download:     # several ranges at once: just send the whole file (allowed)
        unsatisfiable = HttpResponse(status=416)
        unsatisfiable['Content-Range'] = f'bytes */{total}'
        try:
            a, b = rng[6:].split('-', 1)
            if a:
                start = int(a)
                end = min(int(b), total - 1) if b else total - 1
            else:                                   # "last N bytes"; a file shorter than N is sent whole
                if not b or int(b) <= 0:
                    return unsatisfiable
                start, end = max(total - int(b), 0), total - 1
        except ValueError:
            return unsatisfiable
        if start < 0 or start > end:
            return unsatisfiable
        resp = StreamingHttpResponse(_chunks(path, start, end - start + 1), status=206, content_type=ctype)
        resp['Content-Range'] = f'bytes {start}-{end}/{total}'
        resp['Content-Length'] = str(end - start + 1)
    else:
        resp = FileResponse(open(path, 'rb'), content_type=ctype, as_attachment=download, filename=name)
    resp['Accept-Ranges'] = 'bytes'
    resp['X-Content-Type-Options'] = 'nosniff'
    resp['Cache-Control'] = 'private, max-age=0'
    return resp


@login_required
def certificate_file(request, pk):
    cert = get_object_or_404(Certificate.objects.select_related('course'), pk=pk)
    role = get_role(request.user)
    allowed = (cert.student_id == request.user.pk or role == 'admin' or (role == 'teacher' and cert.course.teacher_id == request.user.pk))
    if not allowed:
        raise Http404
    return serve(request, cert.file, download=bool(request.GET.get('download')))


@login_required
def payment_proof(request, pk):
    pay = get_object_or_404(Payment.objects.select_related('enrollment'), pk=pk)
    if not (get_role(request.user) == 'admin' or pay.enrollment.user_id == request.user.pk):
        raise Http404
    return serve(request, pay.proof_file)


@login_required
def recording_file(request, pk):
    rec = get_object_or_404(ClassRecording.objects.select_related('live_class__course'), pk=pk)
    role, course = get_role(request.user), rec.live_class.course
    allowed = (role == 'admin' or (role == 'teacher' and course.teacher_id == request.user.pk) or
               (role == 'student' and LiveClass.visible_to(request.user).filter(pk=rec.live_class_id).exists()))
    if not allowed or not rec.video_file:
        raise Http404
    return serve(request, rec.video_file)
