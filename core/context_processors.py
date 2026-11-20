from django.utils.functional import SimpleLazyObject

from .menus import MENUS
from .models import Notification
from .permissions import dashboard_url_name, get_role

DASH_PREFIXES = ('/student/', '/teacher/', '/admin-panel/', '/account/')


def popular_searches():
    """Categories of the real, published courses (a callable, so it only runs on pages that render the search box)."""
    from .models import Course
    return list(Course.objects.filter(is_published=True).order_by('category').values_list('category', flat=True).distinct()[:6])


def site_settings():
    from .models import SiteSettings
    return SiteSettings.get()


def _admin_badges():
    from adminpanel.stats import badges
    return badges()


def _teacher_badges(user):
    from teacher.stats import badges
    return badges(user)


def _student_badges(user):
    from student.stats import badges
    return badges(user)


def portal(request):
    """Adds role, dashboard link, and (inside dashboards) the sidebar menu + notifications."""
    user = request.user
    if not user.is_authenticated:
        return {'popular_searches': SimpleLazyObject(popular_searches), 'site': SimpleLazyObject(site_settings)}
    role = get_role(user)
    ctx = {'role': role, 'dashboard_home': dashboard_url_name(user), 'popular_searches': SimpleLazyObject(popular_searches), 'site': SimpleLazyObject(site_settings)}
    if request.path.startswith(DASH_PREFIXES):
        match = getattr(request, 'resolver_match', None)
        current = f'{match.namespace}:{match.url_name}' if match else ''
        badges = _admin_badges() if role == 'admin' else _teacher_badges(user) if role == 'teacher' else _student_badges(user) if role == 'student' else {}
        menu = []
        for item in MENUS.get(role, []):
            if item[0] == 'heading':
                menu.append({'heading': item[1]})
            else:
                also = item[3] if len(item) > 3 else []
                menu.append({'url': item[0], 'icon': item[1], 'label': item[2], 'badge': badges.get(item[0], 0), 'badge_on': item[0] in badges,
                             'active': current == item[0] or current in also})
        unread = Notification.objects.filter(user=user, is_read=False)
        ctx.update({'menu': menu, 'unread_count': unread.count(),
                    'recent_notifications': Notification.objects.filter(user=user)[:5]})
    return ctx
