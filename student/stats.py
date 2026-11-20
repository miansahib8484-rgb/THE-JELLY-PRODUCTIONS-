"""Numbers behind the student dashboard. Everything is scoped to the signed-in student and computed from the database."""
from datetime import timedelta

from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from core.progress import progress_map
from core.request_cache import per_request
from core.models import (Announcement, Certificate, CurriculumLecture, Enrollment, LectureProgress, LiveClass, Notification, Payment, Review)

LEARNING = ['active', 'completed']


def learning(user):
    return Enrollment.objects.filter(user=user, status__in=LEARNING).select_related('course', 'batch')


@per_request
def learning_list(user):
    """The same rows as learning(), evaluated once per page request."""
    return list(learning(user))


@per_request
def _lecture_totals(user):
    courses = [e.course_id for e in learning_list(user)]
    total = CurriculumLecture.objects.filter(module__course_id__in=courses).count()
    done = LectureProgress.objects.filter(user=user, completed=True, lecture__module__course_id__in=courses).count()
    return done, total


@per_request
def due_payments(user):
    """Payments the student still has to act on: nothing sent yet, or rejected by the admin."""
    return [p for p in Payment.objects.filter(enrollment__user=user, status__in=['pending', 'failed']).select_related('enrollment__course')
            if p.status == 'failed' or not p.has_proof]


@per_request
def upcoming_classes(user):
    since = timezone.now() - timedelta(hours=12)
    return [c for c in LiveClass.visible_to(user).filter(ended_early=False, starts_at__gte=since) if c.status != 'ended']


def kpis(user):
    enrolls = learning_list(user)
    done, total = _lecture_totals(user)
    due = due_payments(user)
    week = timezone.now() + timedelta(days=7)
    upcoming = upcoming_classes(user)
    return {
        'courses': {'value': len(enrolls), 'completed': sum(1 for e in enrolls if e.status == 'completed')},
        'lectures': {'done': done, 'total': total, 'note': f'of {total} in your courses', 'pct': round(done / total * 100) if total else 0},
        'avg': {'value': round(sum(progress_map(enrolls).values()) / len(enrolls)) if enrolls else 0},
        'certs': {'value': Certificate.objects.filter(student=user).count()},
        'due': {'value': len(due), 'amount': sum(p.amount for p in due)},
        'week': {'value': sum(1 for c in upcoming if c.starts_at <= week)},
        'live_now': {'value': sum(1 for c in upcoming if c.status == 'live')},
        'unread': {'value': Notification.objects.filter(user=user, is_read=False).count()},
        'streak': {'value': streak(user)},
    }


def badges(user):
    return {'student:billing': len(due_payments(user)), 'student:live': sum(1 for c in upcoming_classes(user) if c.status == 'live')}


def streak(user):
    """Consecutive days (ending today or yesterday) on which the student completed at least one lecture."""
    days = set(LectureProgress.objects.filter(user=user, completed=True, completed_at__isnull=False)
               .annotate(d=TruncDate('completed_at')).values_list('d', flat=True))
    day = timezone.localdate()
    if day not in days:
        day -= timedelta(days=1)
    n = 0
    while day in days:
        n += 1
        day -= timedelta(days=1)
    return n


def next_lecture(enrollment):
    """First lecture of the course the student has not completed (None when everything is done or there is no curriculum)."""
    done = set(LectureProgress.objects.filter(user=enrollment.user, completed=True, lecture__module__course=enrollment.course).values_list('lecture_id', flat=True))
    lectures = CurriculumLecture.objects.filter(module__course=enrollment.course).select_related('module').order_by('module__sort_order', 'module_id', 'sort_order', 'id')
    return next((l for l in lectures if l.pk not in done), None)


def continue_item(user):
    """Where to pick up: the course the student studied most recently, else the first active course."""
    last = (LectureProgress.objects.filter(user=user, completed=True, lecture__module__course__enrollments__user=user,
                                           lecture__module__course__enrollments__status='active')
            .select_related('lecture__module__course').order_by('-completed_at').first())
    e = None
    if last:
        e = learning(user).filter(course=last.lecture.module.course, status='active').first()
    e = e or learning(user).filter(status='active').first()
    if not e:
        return None
    lec = next_lecture(e)
    return {'enrollment': e, 'lecture': lec, 'pct': e.progress_pct} if lec else None


def attention(user):
    """What the student should do next, built from live data (empty list = nothing to do)."""
    items = []
    for p in due_payments(user)[:3]:
        if p.status == 'failed':
            items.append({'icon': 'error', 'tone': 'r', 'text': f'Payment for "{p.enrollment.course.title}" was rejected: fix and resubmit', 'url': 'student:pay', 'arg': p.enrollment.pk})
        else:
            items.append({'icon': 'receipt_long', 'tone': 'y', 'text': f'Submit your payment for "{p.enrollment.course.title}" to unlock it', 'url': 'student:pay', 'arg': p.enrollment.pk})
    under_review = Payment.objects.filter(enrollment__user=user, status='pending').exclude(transaction_reference='', proof_file='')
    for p in under_review.select_related('enrollment__course')[:2]:
        items.append({'icon': 'hourglass_top', 'tone': 'b', 'text': f'Payment for "{p.enrollment.course.title}" is being reviewed', 'url': 'student:billing', 'arg': None})
    reviewed = set(Review.objects.filter(user=user).values_list('course_id', flat=True))
    have_cert = set(Certificate.objects.filter(student=user).values_list('course_id', flat=True))
    enrolls = learning_list(user)
    pmap = progress_map(enrolls)
    for e in enrolls:
        if pmap[e.pk] == 100 and e.course_id not in have_cert:
            items.append({'icon': 'workspace_premium', 'tone': 'g', 'text': f'You finished "{e.course.title}". Your certificate will appear once your teacher uploads it', 'url': 'student:certificates', 'arg': None})
        if pmap[e.pk] >= 50 and e.course_id not in reviewed:
            items.append({'icon': 'star', 'tone': 'y', 'text': f'Rate "{e.course.title}" and help other students', 'url': 'student:reviews', 'arg': None})
    live = [c for c in upcoming_classes(user) if c.status == 'live']
    for c in live[:2]:
        items.append({'icon': 'videocam', 'tone': 'r', 'text': f'"{c.title}" is live now', 'url': 'student:live', 'arg': None})
    return items[:7]


def announcements(user, limit=4):
    q = Q(pk__in=[])
    for e in learning_list(user):
        q |= Q(course_id=e.course_id) & (Q(batch__isnull=True) | Q(batch_id=e.batch_id))
    return Announcement.objects.filter(q).select_related('course', 'teacher')[:limit]


def activity_chart(user, days=14):
    """Lectures completed per day for the last `days` days (zeros included)."""
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    rows = (LectureProgress.objects.filter(user=user, completed=True, completed_at__date__gte=start)
            .annotate(d=TruncDate('completed_at')).values('d').annotate(n=Count('id')))
    found = {r['d']: r['n'] for r in rows}
    labels, values = [], []
    for i in range(days):
        d = start + timedelta(days=i)
        labels.append(d.strftime('%d %b'))
        values.append(found.get(d, 0))
    return {'labels': labels, 'values': values, 'total': sum(values)}


def course_progress(user):
    """Per-course progress for the bar chart: [(title, pct)]."""
    enrolls = learning_list(user)
    pmap = progress_map(enrolls)
    return [(e.course.title, pmap[e.pk]) for e in enrolls]


def schedule(user, limit=5):
    """The next class of each course the student is in, soonest first."""
    seen, out = set(), []
    for c in upcoming_classes(user):
        if c.course_id not in seen:
            seen.add(c.course_id)
            out.append(c)
    return out[:limit]
