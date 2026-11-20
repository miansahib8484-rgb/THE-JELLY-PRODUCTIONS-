"""Numbers behind the teacher dashboard. Everything is scoped to the signed-in teacher's own courses
and computed from the database on each request."""
from datetime import timedelta

from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone

from core.analytics import monthly, paid_payments, pct_change
from core.progress import progress_map
from core.request_cache import per_request
from core.models import (Certificate, Course, CurriculumLecture, Enrollment, LectureProgress, LiveClass, Payment, Review)

LEARNING = ['active', 'completed']


def my_courses(user):
    return Course.objects.filter(teacher=user)


def my_enrollments(user):
    return Enrollment.objects.filter(course__teacher=user)


def my_payments(user, status='paid'):
    qs = Payment.objects.filter(enrollment__course__teacher=user)
    return paid_payments(qs) if status == 'paid' else qs.filter(status=status)


def _month_counts(qs, field):
    from core.analytics import month_starts
    this_start, last_start = month_starts(2)[1], month_starts(2)[0]
    this = qs.filter(**{f'{field}__date__gte': this_start}).count()
    last = qs.filter(**{f'{field}__date__gte': last_start, f'{field}__date__lt': this_start}).count()
    return this, last


@per_request
def classes_needing_recording(user):
    """Ended classes of the last 60 days that still have no recording uploaded. (Without the limit every page of the dashboard, via the
    sidebar badge, loaded the teacher's whole class history, and a class from last year kept asking for a recording forever.)"""
    now = timezone.now()
    return [c for c in LiveClass.objects.filter(course__teacher=user, recording__isnull=True, starts_at__lte=now,
                                                starts_at__gte=now - timedelta(days=60)).select_related('course')
            .order_by('-starts_at') if c.status == 'ended']


@per_request
def upcoming_classes(user):
    """Classes that have not ended. The database drops the ones that ended long ago before status is checked in Python."""
    since = timezone.now() - timedelta(hours=12)
    return [c for c in LiveClass.objects.filter(course__teacher=user, ended_early=False, starts_at__gte=since).select_related('course')
            if c.status != 'ended']


def kpis(user):
    courses = my_courses(user)
    learning = my_enrollments(user).filter(status__in=LEARNING)
    this, last = _month_counts(learning, 'enrolled_at')
    earn = my_payments(user)
    from core.analytics import month_starts
    s_this, s_last = month_starts(2)[1], month_starts(2)[0]
    e_now = earn.filter(paid_on__date__gte=s_this).aggregate(s=Sum('amount'))['s'] or 0
    e_prev = earn.filter(paid_on__date__gte=s_last, paid_on__date__lt=s_this).aggregate(s=Sum('amount'))['s'] or 0
    reviews = Review.objects.filter(course__teacher=user)
    upcoming = upcoming_classes(user)
    total = learning.count()
    done = learning.filter(status='completed').count()
    return {
        'courses': {'value': courses.count(), 'note': f"{courses.filter(is_published=True).count()} live, {courses.filter(is_published=False).count()} draft"},
        'students': {'value': learning.values('user').distinct().count(), 'month': this, 'trend': pct_change(this, last)},
        'earnings': {'value': earn.aggregate(s=Sum('amount'))['s'] or 0, 'month': e_now, 'trend': pct_change(e_now, e_prev)},
        'rating': {'value': round(reviews.aggregate(a=Avg('rating'))['a'] or 0, 1), 'count': reviews.count()},
        'upcoming': {'value': len(upcoming)},
        'completion': {'value': round(done / total * 100) if total else 0, 'done': done},
        'pending': {'value': my_enrollments(user).filter(status='pending_payment').count()},
        'recordings': {'value': len(classes_needing_recording(user))},
    }


def badges(user):
    return {'teacher:live': len(classes_needing_recording(user))}


@per_request
def course_performance(user):
    """One row per course: learners, completion, average progress, rating, earnings.

    Money, review counts and lecture counts are fetched with separate grouped queries: joining them in one
    annotate() would multiply the payment sum by the number of reviews."""
    courses = list(my_courses(user).annotate(
        learners=Count('enrollments', filter=Q(enrollments__status__in=LEARNING), distinct=True),
        completed=Count('enrollments', filter=Q(enrollments__status='completed'), distinct=True)).order_by('-learners', 'title'))
    ids = [c.pk for c in courses]
    earned = dict(Payment.objects.filter(enrollment__course_id__in=ids, status='paid').values_list('enrollment__course_id').annotate(s=Sum('amount')))
    n_reviews = dict(Review.objects.filter(course_id__in=ids).values_list('course_id').annotate(n=Count('id')))
    lectures = dict(CurriculumLecture.objects.filter(module__course_id__in=ids).values_list('module__course_id').annotate(n=Count('id')))
    learning = list(Enrollment.objects.filter(course_id__in=ids, status__in=LEARNING).select_related('user'))
    pmap = progress_map(learning)
    by_course = {}
    for e in learning:
        by_course.setdefault(e.course_id, []).append(pmap[e.pk])
    rows = []
    for c in courses:
        vals = by_course.get(c.pk, [])
        rows.append({'course': c, 'learners': c.learners, 'completed': c.completed, 'earned': earned.get(c.pk, 0), 'rating': c.rating_avg,
                     'n_reviews': n_reviews.get(c.pk, 0), 'lectures': lectures.get(c.pk, 0),
                     'avg_progress': round(sum(vals) / len(vals)) if vals else 0})
    return rows


@per_request
def attention(user):
    """What needs the teacher's action right now, built from live data (empty list = all clear)."""
    items = []
    now = timezone.now()
    for c in classes_needing_recording(user)[:3]:
        items.append({'icon': 'upload', 'tone': 'r', 'text': f'Upload the recording for "{c.title}"', 'url': 'teacher:recording', 'arg': c.pk})
    soon = LiveClass.objects.filter(course__teacher=user, meeting_link='', starts_at__gte=now).order_by('starts_at')[:3]
    for c in soon:
        items.append({'icon': 'link_off', 'tone': 'y', 'text': f'Add a meeting link to "{c.title}" ({c.starts_at:%a %d %b})', 'url': 'teacher:live_edit', 'arg': c.pk})
    for c in my_courses(user).filter(is_published=True).annotate(n=Count('modules')).filter(n=0)[:2]:
        items.append({'icon': 'construction', 'tone': 'b', 'text': f'"{c.title}" is live but has no curriculum yet', 'url': 'teacher:builder', 'arg': c.slug})
    for c in my_courses(user).filter(is_published=True).exclude(batches__status__in=['upcoming', 'open']).distinct()[:2]:
        items.append({'icon': 'event', 'tone': 'b', 'text': f'"{c.title}" has no open batch for new students', 'url': 'teacher:builder', 'arg': c.slug})
    active = list(my_enrollments(user).filter(status='active').select_related('user', 'course'))
    pmap = progress_map(active)
    have = set(Certificate.objects.filter(course__teacher=user).values_list('student_id', 'course_id'))
    for e in active:
        if pmap[e.pk] == 100 and (e.user_id, e.course_id) not in have:
            items.append({'icon': 'workspace_premium', 'tone': 'g', 'text': f'{e.user.get_full_name() or e.user.username} finished "{e.course.title}": issue a certificate',
                          'url': 'teacher:certificates', 'arg': None})
            if len(items) > 12:
                break
    return items[:8]


def activity(user, limit=8):
    items = []
    for e in my_enrollments(user).filter(status__in=LEARNING).select_related('user', 'course').order_by('-enrolled_at')[:limit]:
        items.append({'when': e.enrolled_at, 'icon': 'person_add', 'tone': 'b', 'text': f'{e.user.get_full_name() or e.user.username} joined {e.course.title}'})
    for r in Review.objects.filter(course__teacher=user).select_related('course', 'user').order_by('-created_at')[:limit]:
        items.append({'when': r.created_at, 'icon': 'star', 'tone': 'y', 'text': f'{r.display_name} rated {r.course.title} {r.rating}★'})
    for c in Certificate.objects.filter(course__teacher=user).select_related('student', 'course').order_by('-created_at')[:limit]:
        items.append({'when': c.created_at, 'icon': 'workspace_premium', 'tone': 'g', 'text': f'Certificate issued to {c.student.get_full_name() or c.student.username}'})
    items.sort(key=lambda i: i['when'], reverse=True)
    return items[:limit]


def chart(user, n=6):
    paid = my_payments(user)
    learning = my_enrollments(user).filter(status__in=LEARNING)
    rev = monthly(paid, 'paid_on', Sum('amount'), n)
    return {'labels': [m for m, _ in rev], 'earnings': [v for _, v in rev],
            'students': [v for _, v in monthly(learning, 'enrolled_at', Count('id'), n)]}


def student_progress(enrollment):
    """Module-by-module completion for one enrollment (used on the student detail page)."""
    done = set(LectureProgress.objects.filter(user=enrollment.user, completed=True, lecture__module__course=enrollment.course)
               .values_list('lecture_id', flat=True))
    rows = []
    for m in enrollment.course.modules.prefetch_related('lectures'):
        lectures = list(m.lectures.all())
        n_done = sum(1 for l in lectures if l.pk in done)
        rows.append({'module': m, 'total': len(lectures), 'done': n_done,
                     'pct': round(n_done / len(lectures) * 100) if lectures else 0,
                     'lectures': [(l, l.pk in done) for l in lectures]})
    return rows
