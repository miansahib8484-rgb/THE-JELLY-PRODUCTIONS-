import uuid
from datetime import timedelta

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.contrib.auth import get_user_model

from core.models import (Certificate, Course, CurriculumLecture, Enrollment, LectureProgress, LiveClass, Payment, PaymentAccountInfo,
                         Review)
from core.permissions import role_required
from core.throttle import throttle
from core.utils import notify, paginate

from core.progress import attach_progress

from . import stats
from .forms import PaymentProofForm, StudentReviewForm

User = get_user_model()
student_only = role_required('student')


@student_only
def dashboard(request):
    me = request.user
    visible = LiveClass.visible_to(me)
    upcoming = stats.upcoming_classes(me)
    next_batch = (Enrollment.objects.filter(user=me, status__in=['active', 'pending_payment'], batch__start_date__gt=timezone.localdate())
                  .select_related('course', 'batch').order_by('batch__start_date').first())
    return render(request, 'student/dashboard.html', {
        'page_title': 'Student dashboard', 'k': stats.kpis(me), 'next_class': upcoming[0] if upcoming else None, 'next_batch': next_batch,
        'timings': stats.schedule(me, 4), 'recordings': visible.filter(recording__isnull=False).order_by('-starts_at')[:3],
        'learning': attach_progress(stats.learning(me).filter(status='active')[:3]), 'resume': stats.continue_item(me),
        'attention': stats.attention(me), 'announcements': stats.announcements(me), 'activity': stats.activity_chart(me),
        'course_progress': stats.course_progress(me),
        'certificates': Certificate.objects.filter(student=me).select_related('course')[:3]})


@student_only
def api_summary(request):
    """Polled every 30 seconds so the join button, badges and numbers stay current."""
    k = stats.kpis(request.user)
    nxt = next(iter(stats.upcoming_classes(request.user)), None)
    return JsonResponse({'courses': k['courses']['value'], 'lectures': k['lectures']['done'], 'avg': f"{k['avg']['value']}%",
                         'certs': k['certs']['value'], 'due': k['due']['value'], 'week': k['week']['value'],
                         'live_now': k['live_now']['value'], 'unread': k['unread']['value'], 'streak': k['streak']['value'],
                         'next_class_live': bool(nxt and nxt.status == 'live')})


@student_only
def courses(request):
    tab, q = request.GET.get('status', 'all'), request.GET.get('q', '').strip()[:100]
    qs = Enrollment.objects.filter(user=request.user).exclude(status__in=['cancelled', 'refunded']).select_related('course', 'batch')
    if tab == 'progress':
        qs = qs.filter(status='active')
    elif tab == 'done':
        qs = qs.filter(status='completed')
    if q:
        qs = qs.filter(course__title__icontains=q)
    page = paginate(request, qs, 9)
    attach_progress(page)
    return render(request, 'student/courses.html', {
        'page': page, 'status': tab, 'q': q, 'page_title': 'My courses',
        'tabs': [('all', 'All'), ('progress', 'In progress'), ('done', 'Completed')]})


@student_only
def player(request, slug):
    course = get_object_or_404(Course, slug=slug)
    enrollment = Enrollment.objects.filter(user=request.user, course=course, status__in=['active', 'completed']).first()
    if not enrollment:
        messages.info(request, 'Complete your payment to unlock this course.')
        return redirect('student:billing')
    lectures = list(CurriculumLecture.objects.filter(module__course=course).select_related('module').order_by('module__sort_order', 'module_id', 'sort_order', 'id'))
    done_ids = set(LectureProgress.objects.filter(user=request.user, completed=True, lecture__module__course=course).values_list('lecture_id', flat=True))
    current = next((l for l in lectures if str(l.pk) == request.GET.get('lecture')), None) or \
        next((l for l in lectures if l.pk not in done_ids), lectures[0] if lectures else None)
    modules = []
    for m in course.modules.all():
        modules.append({'module': m, 'lectures': [{'obj': l, 'done': l.pk in done_ids} for l in lectures if l.module_id == m.pk]})
    recaps = LiveClass.visible_to(request.user).filter(course=course, recording__isnull=False).order_by('-starts_at')
    return render(request, 'student/player.html', {
        'course': course, 'enrollment': enrollment, 'current': current, 'modules': modules, 'recaps': recaps,
        'page_title': course.title, 'progress': enrollment.progress_pct})


@student_only
@require_POST
def toggle_lecture(request, pk):
    lecture = get_object_or_404(CurriculumLecture, pk=pk)
    if not Enrollment.objects.filter(user=request.user, course=lecture.module.course, status__in=['active', 'completed']).exists():
        return redirect('student:billing')
    obj, _ = LectureProgress.objects.get_or_create(user=request.user, lecture=lecture)
    obj.completed = not obj.completed
    obj.completed_at = timezone.now() if obj.completed else None
    obj.save()
    nxt = request.POST.get('next', '')
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        nxt = reverse('student:courses')
    return redirect(nxt)


@student_only
def live(request):
    tab = request.GET.get('status', 'upcoming')
    visible = LiveClass.visible_to(request.user)
    since = timezone.now() - timedelta(hours=12)                      # a class lasts at most 8 h: older ones are over
    upcoming = [c for c in visible.filter(ended_early=False, starts_at__gte=since) if c.status != 'ended']
    recs = visible.filter(recording__isnull=False).order_by('-starts_at')[:100]
    return render(request, 'student/live.html', {'upcoming': upcoming, 'recordings': recs, 'tab': tab, 'page_title': 'Live classes'})


@student_only
def watch(request, pk):
    c = get_object_or_404(LiveClass.visible_to(request.user).filter(recording__isnull=False), pk=pk)
    return render(request, 'student/watch.html', {'c': c, 'rec': c.recording, 'page_title': c.title})


@student_only
def certificates(request):
    return render(request, 'student/certificates.html', {
        'certs': Certificate.objects.filter(student=request.user).select_related('course'), 'page_title': 'My certificates'})


@student_only
def reviews(request):
    reviewed = Review.objects.filter(user=request.user).select_related('course')
    options = Course.objects.filter(enrollments__user=request.user, enrollments__status__in=['active', 'completed']).exclude(
        pk__in=reviewed.values('course_id')).distinct()
    form = StudentReviewForm(request.POST or None)
    if request.method == 'POST':
        try:
            course_id = uuid.UUID(request.POST.get('course', ''))
        except ValueError:
            course_id = None                       # garbage in the form: treat as "nothing chosen" instead of crashing
        course = options.filter(pk=course_id).first() if course_id else None
        if not course:
            messages.error(request, 'Choose one of your courses to review.')
        elif form.is_valid():
            r = form.save(commit=False)
            r.course, r.user = course, request.user
            try:
                with transaction.atomic():
                    r.save()
            except IntegrityError:                  # a second click arrived at the same moment
                messages.info(request, "You've already reviewed this course.")
                return redirect('student:reviews')
            messages.success(request, 'Thanks for the review!')
            return redirect('student:reviews')
    return render(request, 'student/reviews.html', {'form': form, 'options': options, 'reviewed': reviewed, 'page_title': 'My reviews'})


@student_only
def billing(request):
    payments = Payment.objects.filter(enrollment__user=request.user).select_related('enrollment__course')
    return render(request, 'student/billing.html', {'payments': payments, 'page_title': 'Billing',
        'totals': {'paid': sum(p.amount for p in payments if p.status == 'paid'),
                   'review': sum(p.amount for p in payments if p.status == 'pending' and p.has_proof),
                   'due': sum(p.amount for p in payments if p.status == 'pending' and not p.has_proof)}})


@student_only
@throttle('pay', 120, 3600)
def pay(request, enrollment_id):
    enrollment = get_object_or_404(Enrollment, pk=enrollment_id, user=request.user)
    if enrollment.status in ('cancelled', 'refunded'):
        messages.error(request, 'This reservation was cancelled. Reserve a seat again to continue.')
        return redirect(f"{reverse('website:enroll')}?course={enrollment.course.slug}")
    payment, _ = Payment.objects.get_or_create(enrollment=enrollment, defaults={'amount': enrollment.course.price})
    if payment.status == 'paid':
        messages.info(request, 'This payment is already approved.')
        return redirect('student:billing')
    form = PaymentProofForm(request.POST or None, request.FILES or None, instance=payment)
    if request.method == 'POST' and form.is_valid():
        p = form.save(commit=False)
        p.status, p.admin_note = 'pending', ''
        p.save()
        notify(User.objects.filter(Q(is_superuser=True) | Q(profile__role='admin')).distinct(), 'Payment proof submitted',
               f'{request.user.get_full_name()}: {enrollment.course.title}', reverse('adminpanel:payments'))
        messages.success(request, 'Payment proof submitted. An admin will review it shortly.')
        return redirect('student:billing')
    return render(request, 'student/pay.html', {'form': form, 'payment': payment, 'enrollment': enrollment,
                                                 'info': PaymentAccountInfo.get(), 'page_title': 'Submit payment proof'})


@student_only
@require_POST
def review_delete(request, pk):
    r = get_object_or_404(Review, pk=pk, user=request.user)
    r.delete()      # a signal recalculates the course rating
    messages.success(request, 'Your review was removed.')
    return redirect('student:reviews')


@student_only
def review_edit(request, pk):
    r = get_object_or_404(Review, pk=pk, user=request.user)
    form = StudentReviewForm(request.POST or None, instance=r)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Review updated.')
        return redirect('student:reviews')
    return render(request, 'student/review_edit.html', {'form': form, 'review': r, 'page_title': 'Edit review'})


@student_only
@require_POST
def cancel_reservation(request, enrollment_id):
    """Give up a seat that has not been paid for. Only allowed before any payment proof was sent."""
    e = get_object_or_404(Enrollment, pk=enrollment_id, user=request.user)
    payment = Payment.objects.filter(enrollment=e).first()
    if e.status != 'pending_payment' or (payment and (payment.status == 'paid' or payment.has_proof)):
        messages.error(request, 'This reservation can no longer be cancelled here. Please contact us.')
        return redirect('student:billing')
    if payment:
        payment.delete()
    e.status = 'cancelled'
    e.save(update_fields=['status'])      # a signal frees the seat and recounts
    messages.success(request, f'Your seat in {e.course.title} was released.')
    return redirect('student:billing')
