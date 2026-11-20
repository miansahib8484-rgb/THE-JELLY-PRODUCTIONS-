"""Role checks shared by every dashboard."""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect

from .security import event


def get_role(user):
    if not user.is_authenticated:
        return None
    if user.is_superuser:
        return 'admin'
    profile = getattr(user, 'profile', None)
    return profile.role if profile else 'student'


def dashboard_url_name(user):
    return {'student': 'student:dashboard', 'teacher': 'teacher:dashboard', 'admin': 'adminpanel:overview'}.get(get_role(user), 'website:home')


def role_required(*roles):
    """Decorator: user must be signed in and have one of `roles`."""
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            role = get_role(request.user)
            if role not in roles:
                event(request, 'access_denied', role=role, needs='/'.join(roles))
                messages.error(request, "You don't have access to that page.")
                return redirect(dashboard_url_name(request.user))
            request.role = role
            return view(request, *args, **kwargs)
        return wrapper
    return decorator
