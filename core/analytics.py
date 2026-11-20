"""Date and trend helpers shared by the admin and teacher dashboards."""
from datetime import date

from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone


def month_starts(n):
    """First day of each of the last n months, oldest first (current month last)."""
    today = timezone.localdate()
    out = []
    for i in range(n - 1, -1, -1):
        m, y = today.month - i, today.year
        while m <= 0:
            m, y = m + 12, y - 1
        out.append(date(y, m, 1))
    return out


def monthly(qs, field, agg, n=6):
    """[(label, value)] for the last n months, with zeros for empty months."""
    starts = month_starts(n)
    rows = (qs.filter(**{f'{field}__date__gte': starts[0]}).annotate(_m=TruncMonth(field)).values('_m')
            .annotate(v=agg).order_by('_m'))
    found = {(r['_m'].year, r['_m'].month): r['v'] or 0 for r in rows}
    return [(d.strftime('%b %Y' if n > 6 else '%b'), float(found.get((d.year, d.month), 0))) for d in starts]


def pct_change(cur, prev):
    """Percent change vs the previous period, or None when there is nothing to compare."""
    if not prev:
        return None
    return round((cur - prev) / prev * 100)


def paid_payments(qs=None):
    """Approved payments, each with `paid_on` = the moment the money was approved (older rows without it fall back to the
    day the payment was created). Revenue belongs to the month it was approved in, not the month the student reserved the seat."""
    from .models import Payment
    qs = Payment.objects.all() if qs is None else qs
    return qs.filter(status='paid').annotate(paid_on=Coalesce('reviewed_at', 'created_at'))
