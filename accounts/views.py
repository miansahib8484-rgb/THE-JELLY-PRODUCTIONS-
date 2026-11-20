from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core.errors import error_response
from core.models import ContactSubmission, NewsletterSubscriber, Notification
from core.permissions import dashboard_url_name, get_role
from core.security import digest, event
from core.throttle import client_ip, honeypot_tripped, limited, record_failure, throttle
from core.utils import notify, paginate

from .forms import LoginForm, ProfileForm, RegisterForm, StyledPasswordChangeForm

User = get_user_model()


def _safe_next(request):
    nxt = request.POST.get('next') or request.GET.get('next') or ''
    ok = url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure())
    return nxt if ok else ''


@throttle('register', 120, 3600)          # per address; a classroom behind one address registers together (see THROTTLE_MULTIPLIER)
def register_view(request):
    if request.user.is_authenticated:
        return redirect(dashboard_url_name(request.user))
    if request.method == 'POST' and honeypot_tripped(request):
        return redirect('website:home')      # a bot filled the hidden field: pretend it worked, create nothing
    form = RegisterForm(request.POST or None, initial={'account_type': 'teacher' if request.GET.get('type') == 'teacher' else 'student'})
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        user = User.objects.create_user(username=d['email'], email=d['email'], password=d['password1'],
                                        first_name=d['first_name'], last_name=d['last_name'])
        p = user.profile
        p.phone, p.city, p.country = d['phone'], d['city'], d['country']
        p.date_of_birth, p.occupation = d['date_of_birth'], d['occupation']
        p.experience_level, p.goal, p.heard_from = d['experience_level'], d['goal'], d['heard_from']
        p.wants_newsletter = d['newsletter']
        p.interested_course = d.get('interested_course')
        p.save()
        if d['newsletter']:
            NewsletterSubscriber.objects.get_or_create(email=d['email'])
        # Nobody can make themselves a teacher: it creates a student account and an application for the admin to review.
        if d['account_type'] == 'teacher':
            ContactSubmission.objects.create(name=user.get_full_name(), email=d['email'], topic='Teacher application',
                                             message=f"{user.get_full_name()} registered and asked to teach.\nOccupation: {d['occupation']}\nGoal: {d['goal']}")
            messages.info(request, 'Thanks! We will review your request to teach and get back to you. Meanwhile you have a student account.')
        login(request, user)
        notify(User.objects.filter(is_superuser=True) | User.objects.filter(profile__role='admin'),
               'New student registered', user.get_full_name(), '')
        messages.success(request, f'Welcome, {user.first_name}! Your account is ready.')
        return redirect(_safe_next(request) or dashboard_url_name(user))
    return render(request, 'accounts/register.html', {'form': form, 'next': _safe_next(request)})


LOGIN_WINDOW = 600      # seconds
IP_FAILURE_LIMIT = 30   # failed sign-ins per address (generous: a classroom may share one address)
USER_FAILURE_LIMIT = 8  # failed sign-ins per address and account
ACCOUNT_FAILURE_LIMIT = 80  # failed sign-ins for one account from ALL addresses together (a botnet guessing one password list); high enough that
                            # locking somebody out takes a sustained attack, low enough that guessing stays hopeless


SIGN_IN_ROLES = {
    'student': {'label': 'Student', 'icon': '🎓', 'heading': 'Welcome back. Your next class is waiting.', 'sub': 'Sign in to see your courses, class timings and join links.',
                'intro': 'For students: sign in to continue learning.'},
    'teacher': {'label': 'Teacher', 'icon': '🧑‍🏫', 'heading': 'Welcome back. Your students are waiting.', 'sub': 'Sign in to manage your classes, recordings and students.',
                'intro': 'For teachers: sign in to manage your courses and students.'},
    'admin': {'label': 'Admin', 'icon': '🛠️', 'heading': 'Administrator sign-in.', 'sub': 'Sign in to run the school: courses, payments, people and the website.',
              'intro': 'For administrators only.'},
}


REMEMBER_COOKIE = 'jelly_signin'          # which sign-in this browser used last (student / teacher / admin)


def _account_with_this_password(identifier, password):
    """The account that the typed name/e-mail and password belong to, or None. Used ONLY after a refused sign-in, to tell a person whose
    password is right *why* they were refused (wrong sign-in tab, deactivated account) instead of a puzzling "incorrect password"."""
    User = get_user_model()
    ident = (identifier or '').strip()
    user = User.objects.filter(username__iexact=ident).first() or User.objects.filter(email__iexact=ident).first()
    return user if user is not None and user.check_password(password) else None


def _sign_in_role(request):
    """Which of the three sign-ins is this? The form says so (hidden field), the address says so (?as=teacher), and a page that sent
    the visitor here (?next=/teacher/...) says so; student is the default."""
    asked = (request.POST.get('role') if request.method == 'POST' else request.GET.get('as')) or ''
    if asked in SIGN_IN_ROLES:
        return asked
    if request.method == 'POST':
        return 'student'
    remembered = request.COOKIES.get(REMEMBER_COOKIE)
    target = _safe_next(request) or ''
    for role, url_name in (('admin', 'adminpanel:overview'), ('teacher', 'teacher:dashboard'), ('student', 'student:dashboard')):
        if target.startswith(reverse(url_name)):
            return role
    return remembered if remembered in SIGN_IN_ROLES else 'student'


def login_view(request):
    if request.user.is_authenticated:
        return redirect(dashboard_url_name(request.user))
    role = _sign_in_role(request)
    form = LoginForm(request.POST or None, initial={'role': role})
    error, error_tab = '', ''
    if request.method == 'POST':
        ip = client_ip(request)
        account = digest(request.POST.get('username') or '')        # a fixed-length key: the typed text may hold spaces, line breaks, 10 000 characters
        who = f'{ip}|{account}'
        if limited('login-ip', ip, IP_FAILURE_LIMIT) or limited('login-user', who, USER_FAILURE_LIMIT) or limited('login-account', account, ACCOUNT_FAILURE_LIMIT):
            event(request, 'login_locked_out')
            return error_response(request, 429, 'Too many failed sign-in attempts', 'Please wait ten minutes, or use "Forgot password".', retry_after=LOGIN_WINDOW)
        if form.is_valid():
            user = authenticate(request, username=form.cleaned_data['username'], password=form.cleaned_data['password'])
            # An administrator signs in at "Admin", a teacher at "Teacher", a student at "Student".
            actual = get_role(user) if user else None
            if user and actual == role:
                login(request, user)
                request.session.set_expiry(None if form.cleaned_data['remember'] else 0)
                response = redirect(_safe_next(request) or dashboard_url_name(user))
                response.set_cookie(REMEMBER_COOKIE, role, max_age=365 * 24 * 3600, httponly=True, samesite='Lax', secure=not settings.DEBUG)
                return response
            record_failure('login-ip', ip, LOGIN_WINDOW)
            record_failure('login-user', who, LOGIN_WINDOW)
            record_failure('login-account', account, LOGIN_WINDOW)
            # Why was it refused? A person whose PASSWORD is right is told the real reason; a wrong password reveals nothing about the account.
            right_password_for = user or _account_with_this_password(form.cleaned_data['username'], form.cleaned_data['password'])
            reason = 'wrong_password'
            if right_password_for is not None and not right_password_for.is_active:
                reason = 'deactivated'
                error = 'This account has been deactivated. Please contact the school to have it activated again.'
            elif user is not None:
                reason = 'wrong_sign_in'
                label = SIGN_IN_ROLES[actual]['label']
                error = f'These details belong to {"an" if actual == "admin" else "a"} {label} account. Please use the {label} sign-in.'
                error_tab = reverse('accounts:login') + '?as=' + actual + (('&next=' + quote(_safe_next(request), safe='/')) if _safe_next(request) else '')
            else:
                error = 'Incorrect email/username or password. Please check them, and that you chose the right sign-in above.'
            event(request, 'login_failed', sign_in=role, reason=reason)
    nxt = _safe_next(request)
    tabs = [{'key': k, 'label': v['label'], 'icon': v['icon'], 'active': k == role,
             'url': reverse('accounts:login') + '?as=' + k + ('&next=' + quote(nxt, safe='/') if nxt else '')} for k, v in SIGN_IN_ROLES.items()]
    return render(request, 'accounts/login.html', {'form': form, 'error': error, 'next': nxt, 'role': role, 'role_info': SIGN_IN_ROLES[role], 'tabs': tabs,
                                                   'error_tab': error_tab, 'error_tab_label': SIGN_IN_ROLES[actual]['label'] if error_tab else ''})


@require_POST
def logout_view(request):
    logout(request)
    messages.info(request, 'You have been signed out.')
    return redirect('website:home')


@login_required
def dashboard_redirect(request):
    return redirect(dashboard_url_name(request.user))


# ---- shared by all three dashboards ----
@login_required
def profile_view(request):
    p = request.user.profile
    initial = {'first_name': request.user.first_name, 'last_name': request.user.last_name, 'email': request.user.email,
               'phone': p.phone, 'city': p.city, 'country': p.country, 'title': p.title, 'bio': p.bio}
    form = ProfileForm(request.POST or None, request.FILES or None, initial=initial, user=request.user)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Profile updated.')
        return redirect('accounts:profile')
    return render(request, 'accounts/profile.html', {'form': form, 'page_title': 'Profile'})


@login_required
@throttle('settings', 20, 3600)          # a stolen session cannot guess the old password
def settings_view(request):
    form = StyledPasswordChangeForm(request.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        if user.profile.must_change_password:
            user.profile.must_change_password = False
            user.profile.save(update_fields=['must_change_password'])
        messages.success(request, 'Password updated.')
        return redirect('accounts:settings')
    return render(request, 'accounts/settings.html', {'form': form, 'page_title': 'Settings & password'})


@login_required
def notifications_view(request):
    if request.method == 'POST':
        Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return redirect('accounts:notifications')
    page = paginate(request, Notification.objects.filter(user=request.user), 15)
    return render(request, 'accounts/notifications.html', {'page': page, 'page_title': 'Notifications'})


@login_required
def notification_open(request, pk):
    n = Notification.objects.filter(pk=pk, user=request.user).first()
    if not n:
        return redirect('accounts:notifications')
    n.is_read = True
    n.save(update_fields=['is_read'])
    return redirect(n.url or 'accounts:notifications')
