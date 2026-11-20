"""One friendly, styled page for every error status (400, 403, 429 ...) instead of the browser's bare text page."""
from django.http import HttpResponse
from django.shortcuts import render


def error_response(request, status, heading, message, retry_after=None):
    try:
        resp = render(request, 'error.html', {'code': status, 'heading': heading, 'message': message}, status=status)
    except Exception:            # most likely the database is the reason for the error: never fail inside the error page
        resp = HttpResponse(f'{status} {heading}\n{message}', status=status, content_type='text/plain; charset=utf-8')
    if retry_after:
        resp['Retry-After'] = str(retry_after)
    return resp
