"""Views shared by the teacher and admin dashboards (wired into both URLconfs).

A teacher only ever sees their own courses; an admin sees everything. The scoping lives in
`scoped_courses()` so the two dashboards cannot drift apart.
"""
from datetime import timedelta

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (Batch, Certificate, ClassRecording, Course, CurriculumLecture, CurriculumModule, Enrollment,
                     LiveClass)
from .portal_forms import (BatchForm, CertificateForm, CourseForm, LectureEditForm, LectureForm, LiveClassForm, ModuleEditForm, ModuleForm,
                           RecordingForm)
from .utils import notify, paginate


def ns(request):
    return 'adminpanel' if request.role == 'admin' else 'teacher'


def scoped_courses(request):
    qs = Course.objects.all() if request.role == 'admin' else Course.objects.filter(teacher=request.user)
    return qs.select_related('teacher').order_by('-created_at')


def _course_or_404(request, slug):
    return get_object_or_404(scoped_courses(request), slug=slug)


def _students_of(course, batch=None):
    qs = Enrollment.objects.filter(course=course, status__in=['active', 'completed']).select_related('user')
    if batch:
        qs = qs.filter(batch=batch)
    return [e.user for e in qs]


# ------------------------------------------------------------------ courses
def courses_list(request):
    qs = scoped_courses(request).annotate(n_students=Count('enrollments', filter=Q(enrollments__status__in=['active', 'completed'])))
    status, q = request.GET.get('status', 'all'), request.GET.get('q', '').strip()[:100]
    if status == 'published':
        qs = qs.filter(is_published=True)
    elif status == 'draft':
        qs = qs.filter(is_published=False)
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(category__icontains=q))
    return render(request, 'dashboard/portal/course_list.html', {
        'page': paginate(request, qs, 10), 'status': status, 'q': q, 'page_title': 'Courses',
        'tabs': [('all', 'All'), ('published', 'Published'), ('draft', 'Drafts')]})


def course_form(request, slug=None):
    course = _course_or_404(request, slug) if slug else None
    form = CourseForm(request.POST or None, request.FILES or None, instance=course, role=request.role)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        if request.role == 'teacher':
            obj.teacher = request.user
        obj.save()
        messages.success(request, 'Course saved. ' + ('It is live on the website.' if obj.is_published else 'It is saved as a draft.'))
        return redirect(f'{ns(request)}:builder', slug=obj.slug)
    return render(request, 'dashboard/portal/course_form.html', {
        'form': form, 'course': course, 'page_title': 'Edit course' if course else 'Add a course'})


@require_POST
def course_toggle(request, slug):
    c = _course_or_404(request, slug)
    c.is_published = not c.is_published
    c.save(update_fields=['is_published'])
    messages.success(request, f'"{c.title}" is now {"live on the website" if c.is_published else "hidden from the website"}.')
    return redirect(f'{ns(request)}:courses')


@require_POST
def course_delete(request, slug):
    c = _course_or_404(request, slug)
    if c.enrollments.exists() or c.certificates.exists():
        messages.error(request, 'This course has enrollment, payment or certificate records that would be deleted with it. Hide it from the website instead.')
    else:
        c.delete()
        messages.success(request, 'Course deleted.')
    return redirect(f'{ns(request)}:courses')


def builder(request, slug):
    course = _course_or_404(request, slug)
    return render(request, 'dashboard/portal/builder.html', {
        'course': course, 'modules': course.modules.prefetch_related('lectures'), 'batches': course.batches.annotate(n=Count('enrollments')),
        'module_form': ModuleForm(), 'lecture_form': LectureForm(), 'batch_form': BatchForm(), 'page_title': 'Course builder'})


def _back(request, slug):
    return redirect(f'{ns(request)}:builder', slug=slug)


@require_POST
def module_add(request, slug):
    course = _course_or_404(request, slug)
    form = ModuleForm(request.POST)
    if form.is_valid():
        m = form.save(commit=False)
        m.course, m.sort_order = course, (course.modules.aggregate(m=Max('sort_order'))['m'] or 0) + 1 if course.modules.exists() else 0
        m.save()
        messages.success(request, 'Module added.')
    else:
        messages.error(request, 'Give the module a title.')
    return _back(request, slug)


@require_POST
def module_delete(request, pk):
    m = get_object_or_404(CurriculumModule, pk=pk, course__in=scoped_courses(request))
    slug = m.course.slug
    m.delete()
    messages.success(request, 'Module deleted.')
    return _back(request, slug)


@require_POST
def lecture_add(request, pk):
    m = get_object_or_404(CurriculumModule, pk=pk, course__in=scoped_courses(request))
    form = LectureForm(request.POST)
    if form.is_valid():
        l = form.save(commit=False)
        l.module, l.sort_order = m, (m.lectures.aggregate(x=Max('sort_order'))['x'] or 0) + 1 if m.lectures.exists() else 0
        l.save()
        messages.success(request, 'Lecture added.')
    else:
        messages.error(request, 'Please check the lecture details.')
    return _back(request, m.course.slug)


@require_POST
def lecture_delete(request, pk):
    l = get_object_or_404(CurriculumLecture, pk=pk, module__course__in=scoped_courses(request))
    slug = l.module.course.slug
    l.delete()
    messages.success(request, 'Lecture deleted.')
    return _back(request, slug)


@require_POST
def batch_add(request, slug):
    course = _course_or_404(request, slug)
    form = BatchForm(request.POST)
    if form.is_valid():
        b = form.save(commit=False)
        b.course = course
        b.save()
        messages.success(request, 'Batch added.')
    else:
        messages.error(request, 'Please check the batch details.')
    return _back(request, slug)


@require_POST
def batch_delete(request, pk):
    b = get_object_or_404(Batch, pk=pk, course__in=scoped_courses(request))
    slug = b.course.slug
    if b.enrollments.exists():
        messages.error(request, 'This batch has students. Open Edit and set its status to Closed instead.')
    else:
        b.delete()
        messages.success(request, 'Batch deleted.')
    return _back(request, slug)


# ------------------------------------------------------------------ live classes
def _classes(request):
    return LiveClass.objects.filter(course__in=scoped_courses(request)).select_related('course', 'batch', 'created_by', 'recording')


def live_list(request):
    tab, q = request.GET.get('status', 'upcoming'), request.GET.get('q', '').strip()[:100]
    qs = _classes(request)
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(course__title__icontains=q))
    # same rule as LiveClass.status, decided by the database so only one page of rows is loaded: a class that started longer ago than the
    # longest class ever scheduled has certainly ended; the few classes inside that window are checked with the model's own rule
    now = timezone.now()
    longest = LiveClass.objects.aggregate(m=Max('duration_minutes'))['m'] or 0
    in_window = LiveClass.objects.filter(ended_early=False, starts_at__lte=now, starts_at__gt=now - timedelta(minutes=longest))
    ended = Q(ended_early=True) | Q(starts_at__lte=now - timedelta(minutes=longest)) | Q(pk__in=[c.pk for c in in_window if c.status == 'ended'])
    if tab == 'upcoming':
        qs = qs.exclude(ended)
    elif tab == 'past':
        qs = qs.filter(ended).order_by('-starts_at')
    return render(request, 'dashboard/portal/live_list.html', {
        'page': paginate(request, qs, 10), 'status': tab, 'q': q, 'page_title': 'Live classes',
        'tabs': [('upcoming', 'Upcoming'), ('past', 'Past and recordings'), ('all', 'All')]})


def live_form(request, pk=None):
    obj = get_object_or_404(_classes(request), pk=pk) if pk else None
    form = LiveClassForm(request.POST or None, instance=obj, courses=scoped_courses(request))
    if request.method == 'POST' and form.is_valid():
        c = form.save(commit=False)
        creating = obj is None
        if creating:
            c.created_by = request.user
        c.save()
        if creating:
            notify(_students_of(c.course, c.batch), f'New live class: {c.title}',
                   f'{timezone.localtime(c.starts_at):%a %d %b, %I:%M %p}', reverse('student:live'))
        elif {'starts_at', 'duration_minutes', 'meeting_link', 'title'} & set(form.changed_data):
            notify(_students_of(c.course, c.batch), f'Class updated: {c.title}',
                   f'Now {timezone.localtime(c.starts_at):%a %d %b, %I:%M %p}. Check the link before you join.', reverse('student:live'), email='starts_at' in form.changed_data)
        messages.success(request, 'Live class saved. Students can see it now.' if creating else 'Live class updated.')
        return redirect(f'{ns(request)}:live')
    return render(request, 'dashboard/portal/live_form.html', {
        'form': form, 'obj': obj, 'page_title': 'Edit live class' if obj else 'Schedule a live class'})


@require_POST
def live_delete(request, pk):
    get_object_or_404(_classes(request), pk=pk).delete()
    messages.success(request, 'Class deleted.')
    return redirect(f'{ns(request)}:live')


@require_POST
def live_end(request, pk):
    c = get_object_or_404(_classes(request), pk=pk)
    c.ended_early = True
    c.save(update_fields=['ended_early'])
    messages.success(request, 'Class ended. You can upload the recording now.')
    return redirect(f'{ns(request)}:live')


def recording(request, pk):
    c = get_object_or_404(_classes(request), pk=pk)
    if c.status != 'ended':
        messages.info(request, 'You can upload the recording after the class has ended.')
        return redirect(f'{ns(request)}:live')
    existing = getattr(c, 'recording', None)
    form = RecordingForm(request.POST or None, request.FILES or None,
                         initial={'rec_type': 'file' if existing and existing.video_file else 'link', 'video_url': existing.video_url if existing else ''})
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        rec = existing or ClassRecording(live_class=c)
        rec.uploaded_by = request.user
        if d['rec_type'] == 'link':
            rec.video_url, rec.video_file = d['video_url'], ''
        else:
            rec.video_file, rec.video_url = d['video_file'], ''
        rec.save()
        if not existing:
            notify(_students_of(c.course, c.batch), f'Recording ready: {c.title}', 'Missed the class? Watch it now.', reverse('student:watch', args=[c.pk]))
        messages.success(request, 'Recording published. Enrolled students can watch it now.')
        return redirect(f'{ns(request)}:live')
    return render(request, 'dashboard/portal/recording_form.html', {'form': form, 'obj': c, 'existing': existing, 'page_title': 'Class recording'})


@require_POST
def recording_delete(request, pk):
    c = get_object_or_404(_classes(request), pk=pk)
    if c.has_recording:
        c.recording.delete()
        messages.success(request, 'Recording removed.')
    return redirect(f'{ns(request)}:live')


# ------------------------------------------------------------------ certificates
def certificates(request):
    courses = scoped_courses(request)
    form = CertificateForm(request.POST or None, request.FILES or None, courses=courses,
                           initial={'issued_on': timezone.localdate()})
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        try:
            with transaction.atomic():      # a double click must not turn the unique constraint into a 500
                Certificate.objects.create(student=d['student'], course=d['course'], issued_on=d['issued_on'], file=d['file'],
                                           issued_by=request.user)
        except IntegrityError:
            messages.info(request, 'A certificate was already issued to this student for that course.')
            return redirect(f'{ns(request)}:certificates')
        Enrollment.objects.filter(user=d['student'], course=d['course'], status='active').update(status='completed')
        notify(d['student'], 'Your certificate is ready', d['course'].title, reverse('student:certificates'))
        messages.success(request, f'Certificate issued to {d["student"].get_full_name() or d["student"].username}.')
        return redirect(f'{ns(request)}:certificates')
    q = request.GET.get('q', '').strip()[:100]
    qs = Certificate.objects.filter(course__in=courses).select_related('student', 'course')
    if q:
        qs = qs.filter(Q(student__first_name__icontains=q) | Q(student__last_name__icontains=q) | Q(course__title__icontains=q))
    return render(request, 'dashboard/portal/certificates.html', {
        'form': form, 'page': paginate(request, qs, 10), 'q': q, 'page_title': 'Certificates'})


@require_POST
def certificate_delete(request, pk):
    cert = get_object_or_404(Certificate, pk=pk, course__in=scoped_courses(request))
    student_id, course_id = cert.student_id, cert.course_id
    cert.delete()
    # issuing marked the enrollment completed; without the certificate it is active again
    Enrollment.objects.filter(user_id=student_id, course_id=course_id, status='completed').update(status='active')
    messages.success(request, 'Certificate deleted.')
    return redirect(f'{ns(request)}:certificates')



# ------------------------------------------------------------------ edit screens for curriculum and batches
def _edit(request, obj, form_cls, course, title, saved_message):
    form = form_cls(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, saved_message)
        return _back(request, course.slug)
    return render(request, 'dashboard/portal/simple_edit.html', {
        'form': form, 'course': course, 'page_title': title, 'back': f'{ns(request)}:builder'})


def batch_edit(request, pk):
    b = get_object_or_404(Batch, pk=pk, course__in=scoped_courses(request))
    return _edit(request, b, BatchForm, b.course, 'Edit batch', 'Batch updated.')


def module_edit(request, pk):
    m = get_object_or_404(CurriculumModule, pk=pk, course__in=scoped_courses(request))
    return _edit(request, m, ModuleEditForm, m.course, 'Edit module', 'Module updated.')


def lecture_edit(request, pk):
    l = get_object_or_404(CurriculumLecture.objects.select_related('module__course'), pk=pk, module__course__in=scoped_courses(request))
    return _edit(request, l, LectureEditForm, l.module.course, 'Edit lecture', 'Lecture updated.')
