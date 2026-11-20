from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404, HttpResponseServerError, JsonResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core.throttle import honeypot_tripped, throttle

from core.models import (ContactSubmission, Course, Enrollment, NewsletterSubscriber, Payment, PaymentAccountInfo,
                         PromoCode, Testimonial, Batch)
from core.permissions import get_role
from core.errors import error_response
from core.security import event
from core.utils import notify, paginate

from core import services
from core import site as site_data

from . import blog
from .forms import ContactForm, EnrollForm, ReviewForm

User = get_user_model()


def _admins():
    return User.objects.filter(Q(is_superuser=True) | Q(profile__role='admin')).distinct()


def home(request):
    return render(request, 'website/home.html', {
        'courses': Course.objects.filter(is_published=True).select_related('teacher__profile').order_by('-is_featured', '-created_at')[:6],
        'testimonials': Testimonial.objects.filter(is_featured=True)[:3],
        'posts': blog.all_posts()[:3], 'mentor': (m := site_data.mentor()), 'first': m['first'] if m else '',
        'instructors_count': site_data.instructors().count(),
    })


def courses(request):
    qs = Course.objects.filter(is_published=True).select_related('teacher__profile')
    level, q = request.GET.get('level', ''), request.GET.get('q', '').strip()[:100]
    if level in dict(Course.LEVEL_CHOICES):
        qs = qs.filter(level=level)
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(subtitle__icontains=q) | Q(description__icontains=q) | Q(category__icontains=q))
    return render(request, 'website/courses.html', {
        'courses': qs, 'level': level, 'q': q, 'levels': Course.LEVEL_CHOICES,
        'total': Course.objects.filter(is_published=True).count(),
        'first': (site_data.mentor() or {}).get('first', ''), 'instructors_count': site_data.instructors().count(),
    })


def course_detail(request, slug):
    course = get_object_or_404(Course.objects.prefetch_related('modules__lectures'), slug=slug, is_published=True)
    enrollment = review_form = None
    can_review = False
    if request.user.is_authenticated:
        enrollment = Enrollment.objects.filter(user=request.user, course=course).exclude(status__in=['cancelled', 'refunded']).first()
        can_review = bool(enrollment and enrollment.is_learning and not course.reviews.filter(user=request.user).exists())
        review_form = ReviewForm() if can_review else None
    return render(request, 'website/course_detail.html', {
        'course': course, 'batch': course.next_batch, 'reviews': course.reviews.select_related('user')[:6],
        'related': Course.objects.filter(is_published=True).exclude(pk=course.pk).select_related('teacher__profile')[:3],
        'enrollment': enrollment, 'review_form': review_form,
        'total_lectures': sum(m.lectures.count() for m in course.modules.all()),
        'course_schema': _course_schema(request, course),
    })


@login_required
@require_POST
@throttle('review', 60, 3600)
def submit_review(request, slug):
    course = get_object_or_404(Course, slug=slug, is_published=True)
    enrolled = Enrollment.objects.filter(user=request.user, course=course, status__in=['active', 'completed']).exists()
    if not enrolled:
        messages.error(request, "You can only review a course you're enrolled in.")
    elif course.reviews.filter(user=request.user).exists():
        messages.info(request, "You've already reviewed this course.")
    else:
        form = ReviewForm(request.POST)
        if form.is_valid():
            review = form.save(commit=False)
            review.course, review.user = course, request.user
            try:
                with transaction.atomic():
                    review.save()
            except IntegrityError:                  # double click: the first request already saved it
                messages.info(request, "You've already reviewed this course.")
                return redirect(course.get_absolute_url() + '#t4')
            messages.success(request, 'Thanks for the review!')
        else:
            messages.error(request, 'Please check your review and try again.')
    return redirect(course.get_absolute_url() + '#t4')


def about(request):
    return render(request, 'website/about.html', {
        'mentor': (m := site_data.mentor()), 'first': m['first'] if m else '', 'instructors_count': site_data.instructors().count(),
        'team': (t := site_data.team())[0], 'team_staff': t[1]})


@throttle('contact', 6, 3600)
def contact(request):
    if request.method == 'POST' and honeypot_tripped(request):
        messages.success(request, 'Thanks! Your message is in. We usually reply within a day.')
        return redirect('website:contact')
    initial = {}
    if request.user.is_authenticated:
        initial = {'name': request.user.get_full_name(), 'email': request.user.email}
    form = ContactForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        ContactSubmission.objects.create(**form.cleaned_data)
        notify(_admins(), 'New contact message', form.cleaned_data['topic'] or 'Contact form', reverse('adminpanel:contact'))
        messages.success(request, 'Thanks! Your message is in. We usually reply within a day.')
        return redirect('website:contact')
    return render(request, 'website/contact.html', {'form': form, 'first': (site_data.mentor() or {}).get('first', '')})


def blog_list(request):
    cat = request.GET.get('cat', '')
    posts = blog.all_posts()
    shown = [p for p in posts if not cat or p.category == cat]
    featured = next((p for p in posts if p.featured), posts[0] if posts else None)
    shown = paginate(request, shown, 9)
    return render(request, 'website/blog.html', {
        'posts': shown, 'featured': featured, 'cat': cat, 'categories': blog.CATEGORY_LABELS.items(), 'all_count': len(posts),
        'first': (site_data.mentor() or {}).get('first', '')})


def blog_detail(request, slug):
    post = blog.get_post(slug)
    if not post:
        raise Http404
    return render(request, 'website/blog_detail.html', {'post': post, 'more': [p for p in blog.all_posts() if p.slug != slug][:3]})


@require_POST
@throttle('newsletter', 6, 3600)
def newsletter_subscribe(request):
    email = request.POST.get('email', '').strip().lower()
    try:
        from django.core.validators import validate_email
        validate_email(email)
        if honeypot_tripped(request):
            messages.success(request, "You're subscribed. Welcome!")
        else:
            sub, created = NewsletterSubscriber.objects.get_or_create(email=email)
            if not created and sub.status == 'unsubscribed':
                sub.status = 'active'
                sub.save(update_fields=['status'])
                created = True
            messages.success(request, "You're subscribed. Welcome!")        # same answer either way: never reveal who is on the list
    except ValidationError:
        messages.error(request, 'Please enter a valid email address.')
    nxt = request.POST.get('next', '')
    ok = url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure())
    return redirect(nxt if ok else 'website:home')


@throttle('promo', 20, 60, methods=('GET',), json=True)     # 20 guesses a minute per address: codes cannot be farmed
def promo_check(request):
    promo = PromoCode.lookup(request.GET.get('code'))
    if promo:
        return JsonResponse({'valid': True, 'percent': promo.percent_off, 'message': f'Code applied: {promo.percent_off}% off'})
    return JsonResponse({'valid': False, 'percent': 0, 'message': 'This code is not valid'})


@login_required
@throttle('enroll', 120, 3600)
def enroll(request):
    if get_role(request.user) != 'student':
        messages.info(request, 'Enrollment is for student accounts. Sign in with a student account to reserve a seat.')
        return redirect('website:courses')
    if not Course.objects.filter(is_published=True).exists():
        messages.info(request, 'No course is open for enrollment yet. Please check back soon.')
        return redirect('website:courses')

    course_qs = Course.objects.filter(is_published=True).prefetch_related('batches')
    initial_course = course_qs.filter(slug=request.GET.get('course', '')).first() or course_qs.first()
    form = EnrollForm(request.POST or None, request.FILES or None, user=request.user,
                      initial={'course': initial_course, 'phone': request.user.profile.phone, 'city': request.user.profile.city})
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        course, batch = d['course'], d['batch'] or d['course'].next_batch
        promo = PromoCode.lookup(d['promo_code'], user=request.user)
        amount = (course.price * (100 - (promo.percent_off if promo else 0)) / 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP)   # whole rupees, same rounding as the page's JavaScript
        with transaction.atomic():
            if batch:
                batch = Batch.objects.select_for_update().get(pk=batch.pk)       # serialises concurrent reservations of the last seats
            mine = Enrollment.objects.select_for_update().filter(user=request.user, course=course)
            target = mine.filter(batch=batch).first()                            # my record for exactly this batch (any status)
            if target and target.status in ('active', 'completed'):
                messages.info(request, f"You're already enrolled in {course.title}.")
                return redirect('student:courses')
            other = mine.exclude(status__in=('cancelled', 'refunded')).exclude(pk=target.pk if target else None).first()
            if other and other.status in ('active', 'completed'):
                messages.info(request, f"You're already enrolled in {course.title}. Ask us if you want to change your batch.")
                return redirect('student:courses')
            needs_seat = target is None or target.status in ('cancelled', 'refunded')
            if batch and needs_seat and batch.seats_left <= 0:
                messages.error(request, f'Sorry, that batch of {course.title} is full. Pick another batch.')
                return redirect(request.get_full_path())
            if other:                                                            # one seat per student per course: moving batch releases the old seat
                services.cancel_enrollment(other, quiet=True)
            if target is None:
                enrollment = Enrollment.objects.create(user=request.user, course=course, batch=batch)
            else:
                enrollment = target
                if target.status in ('cancelled', 'refunded'):
                    # a re-reserved seat starts a new hold: without this the old date made `expire_reservations` cancel it again at once
                    enrollment.status, enrollment.enrolled_at = 'pending_payment', timezone.now()
                    enrollment.save(update_fields=['status', 'enrolled_at'])
            if d['notes']:
                enrollment.notes = d['notes']
                enrollment.save(update_fields=['notes'])
            payment, _ = Payment.objects.get_or_create(enrollment=enrollment, defaults={'amount': amount})
            payment.amount, payment.gateway = amount, d['gateway']
            payment.promo_code = promo.code if promo else ''
            payment.sender_account_number = d['sender_account_number']
            payment.transaction_reference = d['transaction_reference']
            if d['proof_file']:
                payment.proof_file = d['proof_file']
            payment.status = 'pending'
            payment.save()
        p = request.user.profile
        p.phone, p.city = d['phone'], d['city']
        p.save(update_fields=['phone', 'city'])
        notify(_admins(), 'New seat reservation', f'{request.user.get_full_name()} reserved {course.title}', reverse('adminpanel:payments'))
        messages.success(request, f'Your seat in {course.title} is reserved. An admin will confirm your payment shortly.')
        return redirect('student:billing')

    course_data = []
    for c in course_qs:
        course_data.append({'course': c, 'batches': [b for b in c.batches.all() if b.status in ('upcoming', 'open')]})
    return render(request, 'website/enroll.html', {
        'form': form, 'course_data': course_data, 'selected': initial_course, 'pay': PaymentAccountInfo.get(),
        'gateways': Payment.GATEWAY_CHOICES,
    })


# ------------------------------------------------------------------ legal pages, certificate check, unsubscribe, robots
def legal(request, slug):
    """Terms / Privacy / Refund pages. They exist only once the admin has written them in Site content."""
    from core.models import LegalPage
    page = LegalPage.objects.filter(slug=slug).exclude(body='').first()
    if not page:
        raise Http404
    return render(request, 'website/legal.html', {'page': page})


@throttle('verify', 30, 60, methods=('GET',))      # a certificate code cannot be guessed by trying many
def verify_certificate(request, code):
    """Public check that a certificate code is real. Shows only the name, course and date, never the file."""
    from core.models import Certificate
    cert = Certificate.objects.filter(code=code.strip().upper()).select_related('student', 'course').first()
    return render(request, 'website/verify_certificate.html', {'cert': cert, 'code': code}, status=200 if cert else 404)


def newsletter_unsubscribe(request, token):
    sub = get_object_or_404(NewsletterSubscriber, token=token)
    if request.method == 'POST':
        sub.status = 'unsubscribed'
        sub.save(update_fields=['status'])
        messages.success(request, 'You have been unsubscribed. You will not receive our e-mails any more.')
        return redirect('website:home')
    return render(request, 'website/unsubscribe.html', {'sub': sub})


def robots_txt(request):
    from django.http import HttpResponse
    lines = ['User-agent: *', 'Disallow: /admin-panel/', 'Disallow: /teacher/', 'Disallow: /student/', 'Disallow: /account/', 'Disallow: /files/',
             f'Disallow: /{settings.DJANGO_ADMIN_URL}', f'Sitemap: {request.build_absolute_uri(reverse("sitemap"))}']
    return HttpResponse('\n'.join(lines) + '\n', content_type='text/plain')


def not_found(request, exception=None):
    return render(request, '404.html', status=404)


def bad_request(request, exception=None):
    # also what Django sends for an over-large form or too many fields (SuspiciousOperation)
    return error_response(request, 400, 'We could not process that request', 'The information sent was invalid or too large. Please go back and try again.')


def permission_denied(request, exception=None):
    event(request, 'permission_denied')
    return error_response(request, 403, 'You are not allowed to do that', 'Your account does not have access to this page.')


def csrf_failure(request, reason=''):
    event(request, 'csrf_failure', reason=reason)
    return error_response(request, 403, 'This page has expired', 'For your safety the form was not sent. Please reload the page and try again.')


def server_error(request):
    # Rendered WITHOUT the request: context processors would touch the session and database again, which fails exactly when the
    # database is the reason for this error page.
    return HttpResponseServerError(render_to_string('500.html'))


def _course_schema(request, course):
    """schema.org Course data for search engines (JSON, safe to place in a script tag)."""
    import json
    data = {'@context': 'https://schema.org', '@type': 'Course', 'name': course.title,
            'description': (course.subtitle or course.description or course.title)[:300],
            'provider': {'@type': 'Organization', 'name': site_data.SiteSettings.get().site_name, 'sameAs': request.build_absolute_uri('/')},
            'offers': {'@type': 'Offer', 'price': str(course.price), 'priceCurrency': 'PKR', 'url': request.build_absolute_uri(course.get_absolute_url())}}
    if course.review_count:
        data['aggregateRating'] = {'@type': 'AggregateRating', 'ratingValue': str(course.rating_avg), 'reviewCount': course.review_count}
    # escape '<' as the six characters \u003c (chr(92) = backslash) so a title can never close the <script> tag
    return json.dumps(data).replace('<', chr(92) + 'u003c')


def healthz(request):
    """Load-balancer / Docker health probe: 200 only when the app can reach its database. No data is exposed."""
    from django.db import connection
    from django.http import HttpResponse
    try:
        with connection.cursor() as cur:
            cur.execute('SELECT 1')
    except Exception:
        return HttpResponse('database unavailable', status=503, content_type='text/plain')
    return HttpResponse('ok', content_type='text/plain')
