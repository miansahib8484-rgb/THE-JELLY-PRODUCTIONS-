"""Bulk progress maths: one or two queries for many enrollments instead of two queries per enrollment."""
from django.db.models import Count

from .models import CurriculumLecture, LectureProgress
from .request_cache import safe_memo


def progress_map(enrollments):
    """{enrollment.pk: percent complete} for any iterable of enrollments."""
    enrollments = list(enrollments)
    if not enrollments:
        return {}
    # a dashboard asks for the progress of the same enrollments from several cards: calculate it once per page
    return safe_memo(('progress_map', tuple(sorted(str(e.pk) for e in enrollments))), lambda: _progress_map(enrollments))


def _progress_map(enrollments):
    course_ids = {e.course_id for e in enrollments}
    user_ids = {e.user_id for e in enrollments}
    totals = dict(CurriculumLecture.objects.filter(module__course_id__in=course_ids)
                  .values_list('module__course_id').annotate(n=Count('id')))
    done = {(u, c): n for u, c, n in LectureProgress.objects.filter(user_id__in=user_ids, completed=True, lecture__module__course_id__in=course_ids)
            .values_list('user_id', 'lecture__module__course_id').annotate(n=Count('id'))}
    out = {}
    for e in enrollments:
        total = totals.get(e.course_id, 0)
        out[e.pk] = min(100, round(done.get((e.user_id, e.course_id), 0) / total * 100)) if total else 0
    return out


def attach_progress(enrollments):
    """Compute progress for a whole list at once and cache it on each enrollment (so `e.progress_pct` costs no query)."""
    enrollments = list(enrollments)
    pmap = progress_map(enrollments)
    for e in enrollments:
        e._progress_cache = pmap[e.pk]
    return enrollments
