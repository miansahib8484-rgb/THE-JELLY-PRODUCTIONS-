from core.csvutil import csv_response

from django.contrib import messages
from django.db.models import Avg, Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import Announcement, Certificate, Course, Enrollment, Review
from core.permissions import role_required
from core.utils import notify, paginate

from core.progress import attach_progress

from . import stats
from .forms import AnnouncementForm

teacher_only = role_required('teacher')


def _enrollments(user):
    return Enrollment.objects.filter(course__teacher=user).select_related('user__profile', 'course', 'batch')


def _csv(filename, header, rows):
    return csv_response(filename, header, rows)


def _students_by_level(user):
    """[(label, number of active/completed enrollments)] for beginner / intermediate / advanced courses."""
    rows = {r['course__level']: r['n'] for r in _enrollments(user).filter(status__in=stats.LEARNING).values('course__level').annotate(n=Count('id'))}
    return [(label, rows.get(key, 0)) for key, label in Course.LEVEL_CHOICES]


@teacher_only
def dashboard(request):
    me = request.user
    upcoming = stats.upcoming_classes(me)
    return render(request, 'teacher/dashboard.html', {
        'page_title': 'Teacher dashboard', 'k': stats.kpis(me), 'chart': stats.chart(me), 'attention': stats.attention(me),
        'activity': stats.activity(me), 'upcoming': upcoming[:4], 'next_class': upcoming[0] if upcoming else None,
        'performance': stats.course_performance(me)[:5],
        'recent': attach_progress(_enrollments(me).filter(status__in=stats.LEARNING)[:6]),
        'recent_reviews': Review.objects.filter(course__teacher=me).select_related('course', 'user')[:3],
        'levels': _students_by_level(me)})


@teacher_only
def api_summary(request):
    """Polled every 30 seconds by the dashboard so the numbers stay current."""
    k = stats.kpis(request.user)
    return JsonResponse({'courses': k['courses']['value'], 'students': k['students']['value'],
                         'earnings': f"Rs {int(k['earnings']['value']):,}", 'rating': k['rating']['value'],
                         'upcoming': k['upcoming']['value'], 'completion': f"{k['completion']['value']}%",
                         'pending': k['pending']['value'], 'recordings': k['recordings']['value']})


@teacher_only
def students(request):
    me = request.user
    tab, q, course = request.GET.get('status', 'all'), request.GET.get('q', '').strip()[:100], request.GET.get('course', '')
    qs = _enrollments(me).exclude(status__in=['cancelled', 'refunded'])
    if tab in ('active', 'completed'):
        qs = qs.filter(status=tab)
    elif tab == 'pending':
        qs = qs.filter(status='pending_payment')
    if course:
        qs = qs.filter(course__slug=course)
    if q:
        qs = qs.filter(Q(user__first_name__icontains=q) | Q(user__last_name__icontains=q) | Q(user__email__icontains=q) | Q(course__title__icontains=q))
    if request.GET.get('export') == '1':
        qs = attach_progress(qs)
        return _csv('my-students.csv', ['student', 'email', 'phone', 'course', 'batch start', 'status', 'progress %', 'enrolled'],
                    [(e.user.get_full_name(), e.user.email, e.user.profile.phone, e.course.title, e.batch.start_date if e.batch else '',
                      e.status, e.progress_pct, timezone.localtime(e.enrolled_at).date()) for e in qs])
    page = paginate(request, qs, 10)
    attach_progress(page)
    return render(request, 'teacher/students.html', {
        'page': page, 'status': tab, 'q': q, 'course': course, 'page_title': 'Students',
        'courses': Course.objects.filter(teacher=me).order_by('title'),
        'tabs': [('all', 'All'), ('active', 'Active'), ('completed', 'Completed'), ('pending', 'Pending payment')]})


@teacher_only
def student_detail(request, pk):
    e = get_object_or_404(_enrollments(request.user), pk=pk)
    payment = getattr(e, 'payment', None)
    return render(request, 'teacher/student_detail.html', {
        'e': e, 'person': e.user, 'page_title': e.user.get_full_name() or e.user.username, 'modules': stats.student_progress(e),
        'payment': payment, 'certificate': Certificate.objects.filter(student=e.user, course=e.course).first(),
        'other': attach_progress(_enrollments(request.user).filter(user=e.user).exclude(pk=e.pk)),
        'review': Review.objects.filter(course=e.course, user=e.user).first()})


@teacher_only
def reviews(request):
    me = request.user
    course, rating = request.GET.get('course', ''), request.GET.get('rating', '')
    base = Review.objects.filter(course__teacher=me).select_related('course', 'user')
    if course:
        base = base.filter(course__slug=course)
    qs = base.filter(rating=int(rating)) if rating in {'1', '2', '3', '4', '5'} else base
    total = base.count()
    dist = {n: base.filter(rating=n).count() for n in (5, 4, 3, 2, 1)}
    per_course = Course.objects.filter(teacher=me).annotate(n=Count('reviews'), a=Avg('reviews__rating')).filter(n__gt=0).order_by('-a')
    return render(request, 'teacher/reviews.html', {
        'page': paginate(request, qs, 8), 'page_title': 'Reviews', 'total': total, 'course': course, 'rating': rating,
        'avg': round(base.aggregate(a=Avg('rating'))['a'] or 0, 1), 'per_course': per_course,
        'courses': Course.objects.filter(teacher=me).order_by('title'),
        'dist': [(n, c, round(c / total * 100) if total else 0) for n, c in dist.items()]})


@teacher_only
def performance(request):
    me = request.user
    rows = stats.course_performance(me)
    if request.GET.get('export') == '1':
        return _csv('my-courses-performance.csv', ['course', 'learners', 'completed', 'avg progress %', 'rating', 'reviews', 'earned (PKR)'],
                    [(r['course'].title, r['learners'], r['completed'], r['avg_progress'], r['rating'], r['n_reviews'], r['earned']) for r in rows])
    k = stats.kpis(me)
    payments = stats.my_payments(me).select_related('enrollment__user__profile', 'enrollment__course').order_by('-created_at')
    return render(request, 'teacher/performance.html', {
        'page_title': 'Performance & earnings', 'rows': rows, 'k': k, 'chart': stats.chart(me, 12),
        'page': paginate(request, payments, 8)})


@teacher_only
def announcements(request):
    me = request.user
    form = AnnouncementForm(request.POST or None, teacher=me, initial={'course': request.GET.get('course')})
    if request.method == 'POST' and form.is_valid():
        a = form.save(commit=False)
        a.teacher = me
        enrolls = Enrollment.objects.filter(course=a.course, status__in=stats.LEARNING)
        if a.batch_id:
            enrolls = enrolls.filter(batch=a.batch)
        users = list({e.user_id: e.user for e in enrolls.select_related('user')}.values())      # one notification per student, even with two enrollments
        a.recipients = len(users)
        a.save()
        notify(users, a.title, a.body, reverse('student:player', args=[a.course.slug]))
        messages.success(request, f'Sent to {a.recipients} student{"s" if a.recipients != 1 else ""}.' if a.recipients
                         else 'Saved, but there are no enrolled students to notify yet.')
        return redirect('teacher:announcements')
    return render(request, 'teacher/announcements.html', {
        'form': form, 'page_title': 'Announcements',
        'page': paginate(request, Announcement.objects.filter(teacher=me).select_related('course', 'batch'), 8)})


@teacher_only
@require_POST
def announcement_delete(request, pk):
    get_object_or_404(Announcement, pk=pk, teacher=request.user).delete()
    messages.success(request, 'Removed from your history. Students keep the notification they already received.')
    return redirect('teacher:announcements')
