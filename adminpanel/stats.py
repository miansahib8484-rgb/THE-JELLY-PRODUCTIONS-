"""Numbers behind the admin dashboard. Everything is computed from the database on each request."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.utils import timezone

from core.analytics import month_starts, monthly, paid_payments, pct_change  # noqa: F401  (re-exported for callers)
from core.models import (Certificate, ContactSubmission, Course, Enrollment, NewsletterSubscriber, Payment, Review)

User = get_user_model()

RANGES = [('30d', 'Last 30 days', 30), ('90d', 'Last 90 days', 90), ('12m', 'Last 12 months', 365), ('all', 'All time', None)]


def students():
    return User.objects.filter(profile__role='student')


def _this_and_last_month(qs, field, agg=None):
    this_start = month_starts(1)[0]
    last_start = month_starts(2)[0]
    this = qs.filter(**{f'{field}__date__gte': this_start})
    last = qs.filter(**{f'{field}__date__gte': last_start, f'{field}__date__lt': this_start})
    if agg is None:
        return this.count(), last.count()
    return this.aggregate(s=agg)['s'] or 0, last.aggregate(s=agg)['s'] or 0


def kpis():
    """Headline numbers with month-over-month trend."""
    paid = paid_payments()
    rev_now, rev_prev = _this_and_last_month(paid, 'paid_on', Sum('amount'))
    stu_now, stu_prev = _this_and_last_month(students(), 'date_joined')
    enr_now, enr_prev = _this_and_last_month(Enrollment.objects.filter(status__in=['active', 'completed']), 'enrolled_at')
    return {
        'students': {'value': students().count(), 'month': stu_now, 'trend': pct_change(stu_now, stu_prev)},
        'revenue': {'value': paid.aggregate(s=Sum('amount'))['s'] or 0, 'month': rev_now, 'trend': pct_change(rev_now, rev_prev)},
        'enrollments': {'value': Enrollment.objects.filter(status__in=['active', 'completed']).count(), 'month': enr_now,
                        'trend': pct_change(enr_now, enr_prev)},
        'courses': {'value': Course.objects.filter(is_published=True).count(), 'drafts': Course.objects.filter(is_published=False).count()},
        'teachers': {'value': User.objects.filter(profile__role='teacher', is_active=True).count()},
        'pending': {'value': Payment.objects.filter(status='pending').count()},
        'messages': {'value': ContactSubmission.objects.filter(status='new').count()},
        'subscribers': {'value': NewsletterSubscriber.objects.filter(status='active').count()},
    }


def badges():
    """Small counters for the sidebar (cheap: two COUNT queries)."""
    return {'adminpanel:payments': Payment.objects.filter(status='pending').count(),
            'adminpanel:contact': ContactSubmission.objects.filter(status='new').count()}


def activity(limit=10):
    """Mixed, newest-first feed of what just happened on the platform."""
    items = []
    for e in Enrollment.objects.select_related('user', 'course').order_by('-enrolled_at')[:limit]:
        items.append({'when': e.enrolled_at, 'icon': 'person_add', 'tone': 'b', 'text': f'{e.user.get_full_name() or e.user.username} enrolled in {e.course.title}',
                      'url': 'adminpanel:enrollments'})
    for p in Payment.objects.select_related('enrollment__user').order_by('-created_at')[:limit]:
        who = p.enrollment.user.get_full_name() or p.enrollment.user.username
        verb = {'pending': 'submitted a payment of', 'paid': 'payment approved:', 'failed': 'payment rejected:'}.get(p.status, 'payment')
        items.append({'when': p.created_at, 'icon': 'payments', 'tone': 'y', 'text': f'{who} {verb} Rs {int(p.amount):,}', 'url': 'adminpanel:payments'})
    for c in ContactSubmission.objects.order_by('-created_at')[:limit]:
        items.append({'when': c.created_at, 'icon': 'mail', 'tone': 'r', 'text': f'{c.name} sent a message: {c.topic or "Contact form"}', 'url': 'adminpanel:contact'})
    for c in Certificate.objects.select_related('student', 'course').order_by('-created_at')[:limit]:
        items.append({'when': c.created_at, 'icon': 'workspace_premium', 'tone': 'g',
                      'text': f'Certificate issued to {c.student.get_full_name() or c.student.username}', 'url': 'adminpanel:certificates'})
    for r in Review.objects.select_related('course', 'user').order_by('-created_at')[:limit]:
        items.append({'when': r.created_at, 'icon': 'star', 'tone': 'y', 'text': f'{r.display_name} rated {r.course.title} {r.rating}★', 'url': 'adminpanel:reviews'})
    items.sort(key=lambda i: i['when'], reverse=True)
    return items[:limit]


def _paid_since(start):
    """Q for 'payment approved on/after start' across the course -> enrollment -> payment join (same fallback as paid_payments)."""
    return (Q(enrollments__payment__reviewed_at__date__gte=start) |
            Q(enrollments__payment__reviewed_at__isnull=True, enrollments__payment__created_at__date__gte=start))


def range_start(key):
    """(key, label, start_date or None) for a ?range= value, defaulting to 12 months."""
    for k, label, days in RANGES:
        if k == key:
            return k, label, (timezone.localdate() - timedelta(days=days)) if days else None
    return '12m', 'Last 12 months', timezone.localdate() - timedelta(days=365)


def report(key):
    """Everything the Reports page shows for the chosen date range."""
    key, label, start = range_start(key)
    paid = paid_payments()
    enrolls = Enrollment.objects.filter(status__in=['active', 'completed'])
    if start:
        paid = paid.filter(paid_on__date__gte=start)
        enrolls = enrolls.filter(enrolled_at__date__gte=start)
    gateways = [{'label': dict(Payment.GATEWAY_CHOICES).get(r['gateway'], r['gateway']), 'total': float(r['total'] or 0), 'n': r['n']}
                for r in paid.values('gateway').annotate(total=Sum('amount'), n=Count('id')).order_by('-total')]
    by_course = (Course.objects.annotate(
        revenue=Sum('enrollments__payment__amount', filter=Q(enrollments__payment__status='paid') & (_paid_since(start) if start else Q())),
        learners=Count('enrollments', filter=Q(enrollments__status__in=['active', 'completed'], **({'enrollments__enrolled_at__date__gte': start} if start else {})), distinct=True))
        .filter(Q(revenue__gt=0) | Q(learners__gt=0)).order_by('-revenue', '-learners')[:6])
    in_range = Enrollment.objects.filter(enrolled_at__date__gte=start) if start else Enrollment.objects.all()
    revenue12 = monthly(paid_payments(), 'paid_on', Sum('amount'), 12)
    funnel = [{'key': s, 'label': l, 'n': in_range.filter(status=s).count()} for s, l in Enrollment.STATUS_CHOICES]
    total_enr = sum(f['n'] for f in funnel) or 1
    completion = round(next(f['n'] for f in funnel if f['key'] == 'completed') / total_enr * 100)
    return {
        'range': key, 'range_label': label, 'ranges': [(k, l) for k, l, _ in RANGES],
        'revenue': paid.aggregate(s=Sum('amount'))['s'] or 0, 'payments': paid.count(),
        'avg_order': round((paid.aggregate(s=Sum('amount'))['s'] or 0) / paid.count()) if paid.count() else 0,
        'new_enrollments': enrolls.count(), 'new_students': students().filter(**({'date_joined__date__gte': start} if start else {})).count(),
        'gateways': gateways, 'by_course': by_course, 'funnel': funnel, 'completion': completion,
        'chart': {'labels': [m for m, _ in revenue12],
                  'revenue': [v for _, v in revenue12],
                  'enrollments': [v for _, v in monthly(Enrollment.objects.filter(status__in=['active', 'completed']), 'enrolled_at', Count('id'), 12)],
                  'gateway_labels': [g['label'] for g in gateways], 'gateway_values': [g['total'] for g in gateways]},
    }
