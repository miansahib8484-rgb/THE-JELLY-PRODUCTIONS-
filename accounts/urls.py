from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from core.throttle import throttle

from .forms import SafePasswordResetForm

from . import views

app_name = 'accounts'

urlpatterns = [
    path('register/', views.register_view, name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('go/', views.dashboard_redirect, name='redirect'),

    path('profile/', views.profile_view, name='profile'),
    path('settings/', views.settings_view, name='settings'),
    path('notifications/', views.notifications_view, name='notifications'),
    path('notifications/<int:pk>/', views.notification_open, name='notification_open'),

    # Django's built-in password reset flow, with site-themed templates
    # throttled: otherwise anyone could e-mail-bomb an address by posting the form in a loop
    path('password-reset/', throttle('pwreset', 10, 3600)(auth_views.PasswordResetView.as_view(
        form_class=SafePasswordResetForm, template_name='accounts/password_reset.html',
        email_template_name='accounts/emails/password_reset_email.txt',
        subject_template_name='accounts/emails/password_reset_subject.txt',
        success_url=reverse_lazy('accounts:password_reset_done'))), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='accounts/password_reset_done.html'), name='password_reset_done'),
    path('password-reset/confirm/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='accounts/password_reset_confirm.html',
        success_url=reverse_lazy('accounts:password_reset_complete')), name='password_reset_confirm'),
    path('password-reset/complete/', auth_views.PasswordResetCompleteView.as_view(
        template_name='accounts/password_reset_complete.html'), name='password_reset_complete'),
]
