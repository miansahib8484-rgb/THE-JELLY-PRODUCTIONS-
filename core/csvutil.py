"""CSV downloads that are safe to open in Excel / Google Sheets."""
import csv

from django.http import HttpResponse

_DANGEROUS = ('=', '+', '-', '@', '\t', '\r')


def safe_cell(value):
    """Text that starts with = + - @ would be run as a formula by spreadsheets (CSV injection). Prefix it with an apostrophe.
    Real numbers and dates are left alone."""
    if value is None:
        return ''
    if isinstance(value, str) and value[:1] in _DANGEROUS:
        return "'" + value
    return value


def csv_response(filename, header, rows):
    resp = HttpResponse(content_type='text/csv')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    resp.write('\ufeff')            # byte-order mark: without it Excel shows non-English names (Urdu, 'Müller') as garbage
    writer = csv.writer(resp)
    writer.writerow(header)
    for row in rows:
        writer.writerow([safe_cell(v) for v in row])
    return resp
