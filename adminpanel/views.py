from datetime import timedelta

from core.csvutil import csv_response
from core.progress import attach_progress

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Avg, Count, Q, Sum
from django.http import Http404, JsonResponse
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core import services
from core.models import (ContactSubmission, Course, Enrollment, LiveClass, NewsletterSubscriber, Payment,
                         PaymentAccountInfo, PromoCode, Review, SiteStat, SiteSettings, Testimonial, BlogPost, Faq, GalleryImage, HeroSlide, LegalPage, Milestone, PageQuote, Partner, TeamMember)
from core.permissions import role_required
from core.utils import notify, paginate

from . import stats
from .forms import (BlogForm, FaqForm, GalleryForm, HeroSlideForm, MilestoneForm, PageQuoteForm, TeamMemberForm, PartnerForm, PaymentInfoForm, PromoForm, SiteSettingsForm, SiteStatForm,
                    StudentCreateForm, TeacherCreateForm, TestimonialForm, UserEditForm)

User = get_user_model()
admin_only = role_required('admin')


def _students():
    return User.objects.filter(profile__role='student').select_related('profile')       # the list cards show the avatar: no query per row


def _claim_if_edited(obj, form):
    """A sample row the admin changed now belongs to the admin: 'Remove sample content' must never delete it."""
    if obj is not None and getattr(obj, 'is_sample', False) and form.has_changed():
        type(obj).objects.filter(pk=obj.pk).update(is_sample=False)


def _search(qs, q, *fields):
    if q:
        cond = Q()
        for f in fields:
            cond |= Q(**{f'{f}__icontains': q})
        qs = qs.filter(cond)
    return qs


@admin_only
def overview(request):
    since = timezone.now() - timedelta(hours=12)
    upcoming = [c for c in LiveClass.objects.filter(ended_early=False, starts_at__gte=since).select_related('course').order_by('starts_at')
                if c.status != 'ended'][:4]
    pending = Payment.objects.filter(status='pending')
    k = stats.kpis()
    revenue_by_month = stats.monthly(stats.paid_payments(), 'paid_on', Sum('amount'), 6)
    return render(request, 'adminpanel/overview.html', {
        'page_title': 'Overview', 'k': k, 'upcoming': upcoming,
        'to_review': pending.select_related('enrollment__user__profile', 'enrollment__course')[:3],
        'latest': Enrollment.objects.select_related('user__profile', 'course')[:5], 'activity': stats.activity(8),
        'chart': {'labels': [m for m, _ in revenue_by_month],
                  'revenue': [v for _, v in revenue_by_month],
                  'enrollments': [v for _, v in stats.monthly(Enrollment.objects.filter(status__in=['active', 'completed']), 'enrolled_at', Count('id'), 6)],
                  'students': [v for _, v in stats.monthly(stats.students(), 'date_joined', Count('id'), 6)]},
        'top_courses': Course.objects.filter(is_published=True).annotate(
            n=Count('enrollments', filter=Q(enrollments__status__in=['active', 'completed']))).order_by('-n', '-rating_avg')[:4]})


@admin_only
def api_summary(request):
    """Small JSON feed the dashboard polls every 30 seconds so numbers stay current without a reload."""
    k = stats.kpis()
    return JsonResponse({'students': k['students']['value'], 'revenue': f"Rs {int(k['revenue']['value']):,}",
                         'enrollments': k['enrollments']['value'], 'courses': k['courses']['value'],
                         'pending': k['pending']['value'], 'messages': k['messages']['value'],
                         'subscribers': k['subscribers']['value'], 'teachers': k['teachers']['value']})


@admin_only
def reports(request):
    r = stats.report(request.GET.get('range', '12m'))
    enrolls = Enrollment.objects.filter(status__in=['active', 'completed']).select_related('user__profile', 'course')
    _, _, since = stats.range_start(request.GET.get('range', '12m'))
    if since:
        enrolls = enrolls.filter(enrolled_at__date__gte=since)
    q = request.GET.get('q', '').strip()[:100]
    enrolls = _search(enrolls, q, 'user__first_name', 'user__last_name', 'user__email', 'course__title')
    return render(request, 'adminpanel/reports.html', {
        'page_title': 'Reports', 'r': r, 'q': q,
        'top': Course.objects.filter(is_published=True).order_by('-rating_avg')[:4],
        'page': _with_progress(paginate(request, enrolls, 8))})


def _with_progress(page):
    attach_progress(page)
    return page


def _csv(filename, header, rows):
    return csv_response(filename, header, rows)


@admin_only
def export(request, kind):
    """CSV downloads for the Reports page: payments, enrollments, students."""
    if kind == 'payments':
        qs = Payment.objects.select_related('enrollment__user', 'enrollment__course').order_by('-created_at')
        return _csv('payments.csv', ['date', 'student', 'email', 'course', 'gateway', 'reference', 'amount', 'promo', 'status'],
                    [(timezone.localtime(p.created_at).date(), p.enrollment.user.get_full_name(), p.enrollment.user.email, p.enrollment.course.title,
                      p.get_gateway_display(), p.transaction_reference, p.amount, p.promo_code, p.status) for p in qs])
    if kind == 'enrollments':
        qs = attach_progress(Enrollment.objects.select_related('user', 'course', 'batch'))
        return _csv('enrollments.csv', ['date', 'student', 'email', 'course', 'batch start', 'status', 'progress %'],
                    [(timezone.localtime(e.enrolled_at).date(), e.user.get_full_name(), e.user.email, e.course.title, e.batch.start_date if e.batch else '', e.status, e.progress_pct) for e in qs])
    if kind == 'students':
        qs = stats.students().select_related('profile').order_by('-date_joined')
        return _csv('students.csv', ['joined', 'name', 'email', 'phone', 'city', 'active'],
                    [(timezone.localtime(u.date_joined).date(), u.get_full_name(), u.email, u.profile.phone, u.profile.city, u.is_active) for u in qs])
    raise Http404


# ------------------------------------------------------------------ people
def _create_account(d, role, **profile_fields):
    """Create a teacher or student account for the person and send a welcome e-mail (the e-mail never contains the password)."""
    from core.emails import absolute, send_plain
    u = User.objects.create_user(username=d['email'], email=d['email'], password=d['password'], first_name=d['first_name'], last_name=d['last_name'])
    p = u.profile
    p.role = role
    p.must_change_password = bool(d.get('ask_to_change'))        # off unless the administrator ticked the box
    for name, value in profile_fields.items():
        setattr(p, name, value)
    p.save()
    body = (f'Hi {u.first_name},\n\nAn account was created for you. Sign in at {absolute(reverse("accounts:login"))} with your e-mail address '
            'and the password your administrator gave you. You can change the password any time under Settings.')
    if p.must_change_password:
        body += ' You will be asked to choose your own password when you sign in.'
    # after the request's transaction has committed: no SMTP wait while the database is locked, no e-mail if the request fails
    transaction.on_commit(lambda: send_plain(u.email, f'Your {role} account is ready', body))
    return u


@admin_only
def teachers(request):
    form = TeacherCreateForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        u = _create_account(d, 'teacher', title=d['title'])
        messages.success(request, f'Teacher account created for {u.get_full_name()}. Give them the password you typed; they can sign in right away.'
                         + (' They will be asked to choose their own password first.' if d['ask_to_change'] else ''))
        return redirect('adminpanel:teachers')
    q = request.GET.get('q', '').strip()[:100]
    qs = User.objects.filter(profile__role='teacher').select_related('profile').annotate(n_courses=Count('courses_taught', distinct=True)).order_by('first_name', 'id')
    qs = _search(qs, q, 'first_name', 'last_name', 'email')
    return render(request, 'adminpanel/teachers.html', {'page': paginate(request, qs, 10), 'form': form, 'q': q, 'page_title': 'Teachers'})


@admin_only
def students(request):
    form = StudentCreateForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        u = _create_account(d, 'student', phone=d['phone'])
        messages.success(request, f'Student account created for {u.get_full_name()}. Give them the password you typed; they can sign in right away.'
                         + (' They will be asked to choose their own password first.' if d['ask_to_change'] else ''))
        return redirect('adminpanel:students')
    tab, q = request.GET.get('status', 'all'), request.GET.get('q', '').strip()[:100]
    qs = _students().annotate(n_courses=Count('enrollments', filter=Q(enrollments__status__in=['active', 'completed']), distinct=True)).order_by('-date_joined')
    if tab == 'active':
        qs = qs.filter(is_active=True)
    elif tab == 'inactive':
        qs = qs.filter(is_active=False)
    qs = _search(qs, q, 'first_name', 'last_name', 'email')
    return render(request, 'adminpanel/students.html', {
        'form': form,
        'page': paginate(request, qs, 10), 'status': tab, 'q': q, 'page_title': 'Students',
        'tabs': [('all', 'All'), ('active', 'Active'), ('inactive', 'Inactive')]})


@admin_only
@require_POST
def user_toggle(request, pk):
    u = get_object_or_404(User.objects.exclude(pk=request.user.pk), pk=pk, profile__role__in=['student', 'teacher'])
    u.is_active = not u.is_active
    u.save(update_fields=['is_active'])
    messages.success(request, f'{u.get_full_name() or u.username} is now {"active" if u.is_active else "deactivated"}.')
    return redirect('adminpanel:teachers' if u.profile.role == 'teacher' else 'adminpanel:students')


def _promote_to_teacher(request, u):
    if u.profile.role == 'admin':
        raise services.PaymentStateError('Admin accounts cannot be changed here.')
    u.profile.role = 'teacher'
    u.profile.save(update_fields=['role'])
    notify(u, 'You are now a teacher', 'Your account was approved for teaching. Open your teacher dashboard to create your first course.',
           reverse('teacher:dashboard'), email=True)


@admin_only
@require_POST
def user_set_role(request, pk):
    """Approve a student as a teacher, or move a teacher back to student (not allowed while they still teach courses)."""
    u = get_object_or_404(User.objects.exclude(pk=request.user.pk).select_related('profile'), pk=pk, profile__role__in=['student', 'teacher'])
    role = request.POST.get('role')
    if role == 'teacher' and u.profile.role == 'student':
        _promote_to_teacher(request, u)
        messages.success(request, f'{u.get_full_name() or u.username} is now a teacher.')
    elif role == 'student' and u.profile.role == 'teacher':
        n = u.courses_taught.count()
        if n:
            messages.error(request, f'This teacher still has {n} course{"s" if n != 1 else ""}. Reassign or delete them first.')
        else:
            u.profile.role = 'student'
            u.profile.save(update_fields=['role'])
            messages.success(request, f'{u.get_full_name() or u.username} is now a student.')
    return redirect('adminpanel:user_detail', pk=u.pk)


@admin_only
@require_POST
def user_password_reset(request, pk):
    """E-mail the user a password-reset link (the admin never sees or sets their password)."""
    from django.contrib.auth.forms import PasswordResetForm
    u = get_object_or_404(User, pk=pk, profile__role__in=['student', 'teacher'])
    form = PasswordResetForm({'email': u.email})
    if not u.is_active:
        messages.error(request, 'This account is deactivated: Django sends no reset link to a deactivated account. Activate it first.')
    elif form.is_valid():
        try:
            form.save(request=request, email_template_name='accounts/emails/password_reset_email.txt',
                      subject_template_name='accounts/emails/password_reset_subject.txt', use_https=request.is_secure())
            messages.success(request, f'A password-reset link was sent to {u.email}.')
        except Exception:                      # SMTP down / refused: tell the admin instead of showing a 500 page
            import logging
            logging.getLogger(__name__).exception('Could not send the password-reset e-mail to user %s', u.pk)
            messages.error(request, 'The e-mail could not be sent. Check the e-mail settings and try again.')
    else:
        messages.error(request, 'This account has no valid e-mail address.')
    return redirect('adminpanel:user_detail', pk=u.pk)


@admin_only
def user_detail(request, pk):
    """One student or teacher: profile, enrollments, payments, certificates (students) or courses (teachers)."""
    u = get_object_or_404(User.objects.select_related('profile'), pk=pk, profile__role__in=['student', 'teacher'])
    form = UserEditForm(request.POST or None, user=u)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Profile updated.')
        return redirect('adminpanel:user_detail', pk=u.pk)
    enrollments = attach_progress(u.enrollments.select_related('course', 'batch'))
    return render(request, 'adminpanel/user_detail.html', {
        'person': u, 'form': form, 'page_title': u.get_full_name() or u.username, 'enrollments': enrollments,
        'payments': Payment.objects.filter(enrollment__user=u).select_related('enrollment__course'),
        'certificates': u.certificates.select_related('course'),
        'taught': u.courses_taught.annotate(n=Count('enrollments', filter=Q(enrollments__status__in=['active', 'completed']))),
        'spent': Payment.objects.filter(enrollment__user=u, status='paid').aggregate(s=Sum('amount'))['s'] or 0,
        'back': 'adminpanel:teachers' if u.profile.role == 'teacher' else 'adminpanel:students'})


# ------------------------------------------------------------------ enrollments & payments
@admin_only
def enrollments(request):
    tab, q = request.GET.get('status', 'all'), request.GET.get('q', '').strip()[:100]
    qs = Enrollment.objects.select_related('user__profile', 'course', 'batch')
    if tab in dict(Enrollment.STATUS_CHOICES):
        qs = qs.filter(status=tab)
    qs = _search(qs, q, 'user__first_name', 'user__last_name', 'user__email', 'course__title')
    return render(request, 'adminpanel/enrollments.html', {
        'page': paginate(request, qs, 10), 'status': tab, 'q': q, 'page_title': 'Enrollments',
        'tabs': [('all', 'All'), ('pending_payment', 'Pending payment'), ('active', 'Active'), ('completed', 'Completed'), ('cancelled', 'Cancelled')]})


@admin_only
@require_POST
def enrollment_mark_paid(request, pk):
    try:
        services.mark_enrollment_paid(get_object_or_404(Enrollment, pk=pk), request.user)
        messages.success(request, 'Marked as paid. The student can start learning.')
    except services.PaymentStateError as exc:
        messages.error(request, str(exc))
    nxt = request.POST.get('next', '')
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(nxt)                         # never an off-site address supplied by the form
    return redirect('adminpanel:enrollments')


@admin_only
@require_POST
def enrollment_cancel(request, pk):
    try:
        services.cancel_enrollment(get_object_or_404(Enrollment, pk=pk))
        messages.success(request, 'Enrollment cancelled and the seat released.')
    except services.PaymentStateError as exc:
        messages.error(request, str(exc))
    return redirect('adminpanel:enrollments')


@admin_only
@require_POST
def enrollment_refund(request, pk):
    try:
        services.refund_enrollment(get_object_or_404(Enrollment, pk=pk), request.user, request.POST.get('note', '').strip())
        messages.success(request, 'Refund recorded. The payment and enrollment are marked refunded and the seat is free.')
    except services.PaymentStateError as exc:
        messages.error(request, str(exc))
    return redirect('adminpanel:enrollments')


@admin_only
def payments(request):
    tab, q = request.GET.get('status', 'pending'), request.GET.get('q', '').strip()[:100]
    qs = Payment.objects.select_related('enrollment__user__profile', 'enrollment__course')
    if tab in dict(Payment.STATUS_CHOICES):
        qs = qs.filter(status=tab)
    qs = _search(qs, q, 'enrollment__user__first_name', 'enrollment__user__last_name', 'transaction_reference', 'enrollment__course__title')
    page = paginate(request, qs, 10)
    refs = [p.transaction_reference for p in page if p.transaction_reference]
    # the same transaction id on more than one payment is the classic way to cheat: mark those rows so the admin looks twice
    # (counted per STUDENT: one student who cancelled and re-enrolled with the same reference is not a duplicate)
    repeated = set(Payment.objects.filter(transaction_reference__in=refs).values('transaction_reference')
                   .annotate(n=Count('enrollment__user', distinct=True)).filter(n__gt=1)
                   .values_list('transaction_reference', flat=True)) if refs else set()
    for p in page:
        p.repeated_ref = p.transaction_reference in repeated
    return render(request, 'adminpanel/payments.html', {
        'page': page, 'status': tab, 'q': q, 'page_title': 'Payments',
        'tabs': [('pending', 'Pending review'), ('paid', 'Approved'), ('failed', 'Rejected'), ('all', 'All')],
        'totals': {'pending': Payment.objects.filter(status='pending').count(),
                   'paid': Payment.objects.filter(status='paid').aggregate(s=Sum('amount'))['s'] or 0,
                   'failed': Payment.objects.filter(status='failed').count()}})


@admin_only
@require_POST
def payment_review(request, pk):
    p = get_object_or_404(Payment, pk=pk)
    action = request.POST.get('action')
    try:
        if action == 'approve':
            services.approve_payment(p, request.user)
            messages.success(request, 'Payment approved. The student can start learning.')
        elif action == 'reject':
            services.reject_payment(p, request.user, request.POST.get('note', '').strip())
            messages.success(request, 'Payment rejected. The student was notified.')
    except services.PaymentStateError as exc:
        messages.error(request, str(exc))
    return redirect('adminpanel:payments')


# ------------------------------------------------------------------ inbox
@admin_only
def contact(request):
    tab, q = request.GET.get('status', 'new'), request.GET.get('q', '').strip()[:100]
    qs = ContactSubmission.objects.all()
    if tab in dict(ContactSubmission.STATUS_CHOICES):
        qs = qs.filter(status=tab)
    qs = _search(qs, q, 'name', 'email', 'topic', 'message')
    page = paginate(request, qs, 10)
    from django.db.models.functions import Lower
    applicants = {u.email.lower(): u for u in User.objects.annotate(email_l=Lower('email'))
                  .filter(email_l__in=[s_.email.lower() for s_ in page], profile__role='student')}
    for s_ in page:
        s_.applicant = applicants.get(s_.email.lower()) if s_.topic == 'Teacher application' else None
    return render(request, 'adminpanel/contact.html', {
        'page': page, 'status': tab, 'q': q, 'page_title': 'Contact inbox',
        'tabs': [('new', 'New'), ('replied', 'Replied'), ('archived', 'Archived'), ('all', 'All')]})


@admin_only
@require_POST
def teacher_application_approve(request, pk):
    s = get_object_or_404(ContactSubmission, pk=pk, topic='Teacher application')
    u = User.objects.filter(email__iexact=s.email).select_related('profile').first()
    if not u:
        messages.error(request, 'No account uses this e-mail address.')
    elif u.profile.role != 'student':
        messages.info(request, f'{u.get_full_name() or u.username} is already a {u.profile.get_role_display().lower()}.')
    else:
        _promote_to_teacher(request, u)
        s.status = 'replied'
        s.save(update_fields=['status'])
        messages.success(request, f'{u.get_full_name() or u.username} is now a teacher and was notified.')
    return redirect('adminpanel:contact')


@admin_only
@require_POST
def contact_update(request, pk):
    s = get_object_or_404(ContactSubmission, pk=pk)
    if request.POST.get('status') in dict(ContactSubmission.STATUS_CHOICES):
        s.status = request.POST['status']
        s.save(update_fields=['status'])
        messages.success(request, f'Message marked as {s.get_status_display().lower()}.')
    return redirect('adminpanel:contact')


@admin_only
def newsletter(request):
    q = request.GET.get('q', '').strip()[:100]
    qs = _search(NewsletterSubscriber.objects.all(), q, 'email')
    return render(request, 'adminpanel/newsletter.html', {
        'page': paginate(request, qs, 12), 'q': q, 'page_title': 'Newsletter',
        'active': NewsletterSubscriber.objects.filter(status='active').count()})


@admin_only
def newsletter_export(request):
    rows = [(s.email, s.status, s.subscribed_at.isoformat()) for s in NewsletterSubscriber.objects.all()]
    return csv_response('newsletter-subscribers.csv', ['email', 'status', 'subscribed_at'], rows)


@admin_only
@require_POST
def newsletter_delete(request, pk):
    get_object_or_404(NewsletterSubscriber, pk=pk).delete()
    messages.success(request, 'Subscriber removed.')
    return redirect('adminpanel:newsletter')


# ------------------------------------------------------------------ reviews
@admin_only
def reviews(request):
    rating, q = request.GET.get('status', 'all'), request.GET.get('q', '').strip()[:100]
    qs = Review.objects.select_related('course', 'user')
    if rating in {'1', '2', '3', '4', '5'}:
        qs = qs.filter(rating=int(rating))
    qs = _search(qs, q, 'comment', 'author_name', 'user__first_name', 'user__last_name', 'course__title')
    return render(request, 'adminpanel/reviews.html', {
        'page': paginate(request, qs, 10), 'status': rating, 'q': q, 'page_title': 'Reviews',
        'avg': Review.objects.aggregate(a=Avg('rating'))['a'] or 0, 'total': Review.objects.count(),
        'tabs': [('all', 'All'), ('5', '5★'), ('4', '4★'), ('3', '3★'), ('2', '2★'), ('1', '1★')]})


@admin_only
@require_POST
def review_delete(request, pk):
    r = get_object_or_404(Review, pk=pk)
    r.delete()                      # the post_delete signal in core/signals.py recalculates the course rating
    messages.success(request, 'Review removed and the course rating recalculated.')
    return redirect('adminpanel:reviews')


# ------------------------------------------------------------------ settings
@admin_only
def settings_view(request):
    info = PaymentAccountInfo.get()
    form = PaymentInfoForm(request.POST or None, instance=info, prefix='pay')
    if request.method == 'POST' and 'save_payment' in request.POST and form.is_valid():
        form.save()
        messages.success(request, 'Payment accounts saved.')
        return redirect('adminpanel:settings')
    return render(request, 'adminpanel/settings.html', {
        'form': form, 'stats': SiteStat.objects.all(), 'stat_form': SiteStatForm(prefix='stat'),
        'testimonials': Testimonial.objects.all(), 'testimonial_form': TestimonialForm(prefix='t'),
        'promos': PromoCode.objects.all(), 'promo_form': PromoForm(prefix='promo'), 'page_title': 'Site settings'})


def _save_simple(request, form_cls, prefix, ok_msg, instance=None):
    form = form_cls(request.POST, prefix=prefix, instance=instance)
    if form.is_valid():
        changed = form.has_changed()
        form.save()
        if changed:
            _claim_if_edited(instance, form)
        messages.success(request, ok_msg)
    else:
        messages.error(request, 'Please check the form: ' + '; '.join(f'{k}: {v[0]}' for k, v in form.errors.items()))
    return redirect('adminpanel:settings')


@admin_only
@require_POST
def stat_save(request, pk=None):
    inst = get_object_or_404(SiteStat, pk=pk) if pk else None
    return _save_simple(request, SiteStatForm, 'stat', 'Stat saved.', inst)


@admin_only
@require_POST
def stat_delete(request, pk):
    get_object_or_404(SiteStat, pk=pk).delete()
    messages.success(request, 'Stat removed.')
    return redirect('adminpanel:settings')


@admin_only
@require_POST
def testimonial_add(request):
    return _save_simple(request, TestimonialForm, 't', 'Testimonial added.')


@admin_only
@require_POST
def testimonial_toggle(request, pk):
    t = get_object_or_404(Testimonial, pk=pk)
    t.is_featured = not t.is_featured
    t.save(update_fields=['is_featured'])
    messages.success(request, 'Testimonial updated.')
    return redirect('adminpanel:settings')


@admin_only
@require_POST
def testimonial_delete(request, pk):
    get_object_or_404(Testimonial, pk=pk).delete()
    messages.success(request, 'Testimonial deleted.')
    return redirect('adminpanel:settings')


@admin_only
@require_POST
def promo_add(request):
    return _save_simple(request, PromoForm, 'promo', 'Promo code added.')


@admin_only
@require_POST
def promo_toggle(request, pk):
    p = get_object_or_404(PromoCode, pk=pk)
    p.is_active = not p.is_active
    p.save(update_fields=['is_active'])
    messages.success(request, f'{p.code} is now {"active" if p.is_active else "off"}.')
    return redirect('adminpanel:settings')


@admin_only
@require_POST
def promo_delete(request, pk):
    get_object_or_404(PromoCode, pk=pk).delete()
    messages.success(request, 'Promo code deleted.')
    return redirect('adminpanel:settings')


@admin_only
@require_POST
def promo_save(request, pk):
    return _save_simple(request, PromoForm, 'promo', 'Promo code updated.', get_object_or_404(PromoCode, pk=pk))


@admin_only
@require_POST
def testimonial_save(request, pk):
    return _save_simple(request, TestimonialForm, 't', 'Testimonial updated.', get_object_or_404(Testimonial, pk=pk))



# ------------------------------------------------------------------ site content (everything the public website shows)
CONTENT_KINDS = {'partner': (Partner, PartnerForm, 'Partner'), 'milestone': (Milestone, MilestoneForm, 'Timeline item'),
                 'faq': (Faq, FaqForm, 'FAQ'), 'gallery': (GalleryImage, GalleryForm, 'Gallery image'),
                 'hero': (HeroSlide, HeroSlideForm, 'Hero slide'), 'team': (TeamMember, TeamMemberForm, 'Team member'),
                 'quote': (PageQuote, PageQuoteForm, 'Quote')}


def _sample_row_count():
    from core.sample_content import SAMPLE_MODELS
    from django.apps import apps
    return sum(apps.get_model('core', n).objects.filter(is_sample=True).count() for n in SAMPLE_MODELS)


@admin_only
@require_POST
def sample_content(request):
    """Load the original design's sample content into the lists that are still empty, or delete every sample row."""
    from core.sample_content import clear_sample_content, load_sample_content
    if request.POST.get('action') == 'clear':
        gone = clear_sample_content()
        messages.success(request, f'Removed {sum(gone.values())} sample row(s). Rows you added or edited were not touched.' if gone else 'There was no sample content to remove.')
    else:
        made = load_sample_content()
        messages.success(request, f'Loaded {sum(made.values())} sample row(s) into the empty lists.' if made else 'Every list already has content, so nothing was loaded.')
    return redirect('adminpanel:content')


@admin_only
def content(request):
    site = SiteSettings.get()
    form = SiteSettingsForm(request.POST or None, request.FILES or None, instance=site)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Site content saved. The public website is updated.')
        return redirect('adminpanel:content')
    return render(request, 'adminpanel/content.html', {
        'form': form, 'groups': form.grouped(), 'page_title': 'Site content', 'partner_form': PartnerForm(prefix='partner'),
        'partners': Partner.objects.all(), 'milestones': Milestone.objects.all(), 'faqs': Faq.objects.all(), 'gallery': GalleryImage.objects.all(),
        'legal': [(slug, label, getattr(LegalPage.objects.filter(slug=slug).first(), 'body', '')) for slug, label in LegalPage.SLUG_CHOICES],
        'milestone_form': MilestoneForm(prefix='milestone'), 'faq_form': FaqForm(prefix='faq'), 'gallery_form': GalleryForm(prefix='gallery'),
        'slides': HeroSlide.objects.all(), 'team': TeamMember.objects.all(), 'quotes': PageQuote.objects.all(),
        'quote_pages': PageQuote.PAGE_CHOICES, 'sample_rows': _sample_row_count()})


@admin_only
@require_POST
def content_item_save(request, kind, pk=None):
    if kind not in CONTENT_KINDS:
        raise Http404
    model, form_cls, label = CONTENT_KINDS[kind]
    inst = get_object_or_404(model, pk=pk) if pk else None
    form = form_cls(request.POST, request.FILES, prefix=kind, instance=inst)
    if form.is_valid():
        changed = form.has_changed()
        form.save()
        if changed:
            _claim_if_edited(inst, form)
        messages.success(request, f'{label} saved.')
    else:
        messages.error(request, 'Please check the form: ' + '; '.join(f'{k}: {v[0]}' for k, v in form.errors.items()))
    return redirect('adminpanel:content')


@admin_only
@require_POST
def legal_save(request):
    """Terms, Privacy and Refund policy text (Markdown). An empty text hides that page and every link to it."""
    from core.models import LegalPage
    for slug, _ in LegalPage.SLUG_CHOICES:
        LegalPage.objects.update_or_create(slug=slug, defaults={'body': request.POST.get(f'legal-{slug}', '').strip()})
    messages.success(request, 'Legal pages saved.')
    return redirect('adminpanel:content')


@admin_only
@require_POST
def content_item_delete(request, kind, pk):
    if kind not in CONTENT_KINDS:
        raise Http404
    get_object_or_404(CONTENT_KINDS[kind][0], pk=pk).delete()
    messages.success(request, 'Removed.')
    return redirect('adminpanel:content')


# ------------------------------------------------------------------ blog
@admin_only
def blog_list(request):
    tab, q = request.GET.get('status', 'all'), request.GET.get('q', '').strip()[:100]
    qs = BlogPost.objects.all()
    if tab == 'published':
        qs = qs.filter(is_published=True)
    elif tab == 'draft':
        qs = qs.filter(is_published=False)
    qs = _search(qs, q, 'title', 'excerpt', 'author_name')
    return render(request, 'adminpanel/blog_list.html', {
        'page': paginate(request, qs, 10), 'status': tab, 'q': q, 'page_title': 'Blog', 'now': timezone.now(),
        'tabs': [('all', 'All'), ('published', 'Published'), ('draft', 'Drafts')]})


@admin_only
def blog_edit(request, pk=None):
    post = get_object_or_404(BlogPost, pk=pk) if pk else None
    initial = {} if post else {'published_at': timezone.localtime().replace(second=0, microsecond=0),
                              'author_name': request.user.get_full_name()}
    form = BlogForm(request.POST or None, request.FILES or None, instance=post, initial=initial)
    if post:
        form.initial['published_at'] = timezone.localtime(post.published_at)
    if request.method == 'POST' and form.is_valid():
        changed = form.has_changed()
        saved = form.save()
        if changed:
            _claim_if_edited(post, form)
        messages.success(request, 'Article saved.')
        return redirect('adminpanel:blog_edit', pk=saved.pk)
    return render(request, 'adminpanel/blog_form.html', {'form': form, 'post': post, 'page_title': 'Edit article' if post else 'New article'})


@admin_only
@require_POST
def blog_toggle(request, pk):
    p = get_object_or_404(BlogPost, pk=pk)
    p.is_published = not p.is_published
    p.save(update_fields=['is_published'])
    messages.success(request, 'Article published.' if p.is_published else 'Article moved to drafts.')
    return redirect('adminpanel:blog')


@admin_only
@require_POST
def blog_delete(request, pk):
    get_object_or_404(BlogPost, pk=pk).delete()
    messages.success(request, 'Article deleted.')
    return redirect('adminpanel:blog')
