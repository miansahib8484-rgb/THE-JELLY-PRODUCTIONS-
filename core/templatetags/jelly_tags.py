from django import template
from django.urls import reverse

register = template.Library()

ROLE_NS = {'student': 'student', 'teacher': 'teacher', 'admin': 'adminpanel'}


@register.filter
def pkr(value):
    """49999 -> 'Rs 49,999'"""
    try:
        return f'Rs {int(round(float(value))):,}'
    except (TypeError, ValueError):
        return ''


@register.simple_tag(takes_context=True)
def rurl(context, name, *args):
    """Reverse a URL inside the current user's dashboard namespace: {% rurl 'live_edit' obj.pk %}"""
    ns = ROLE_NS.get(context.get('role'), 'student')
    return reverse(f'{ns}:{name}', args=args)


@register.simple_tag(takes_context=True)
def qs(context, **kwargs):
    """Current query string with some params replaced/removed (value '' removes): {% qs page=2 %}"""
    params = context['request'].GET.copy()
    for key, val in kwargs.items():
        if val in ('', None):
            params.pop(key, None)
        else:
            params[key] = val
    encoded = params.urlencode()
    return f'?{encoded}' if encoded else '?'


@register.filter
def status_class(status):
    return {
        'active': 'green', 'paid': 'green', 'completed': 'green', 'replied': 'green', 'open': 'green', 'live': 'red',
        'upcoming': 'green', 'published': 'green',
        'pending': 'warm', 'pending_payment': 'warm', 'new': 'warm', 'draft': 'warm',
        'failed': 'red', 'cancelled': 'red', 'refunded': 'red', 'full': 'red', 'inactive': 'red',
    }.get(status, '')


@register.filter
def level_class(level):
    return {'beginner': 'green', 'intermediate': 'warm', 'advanced': 'red'}.get(level, '')


@register.filter
def stars(rating):
    n = int(round(float(rating or 0)))
    return '★' * n + '☆' * (5 - n)



@register.simple_tag
def live_stats():
    """{% live_stats as stats %}: real counts for the public stat band."""
    from core.site import live_stats as f
    return f()


@register.simple_tag
def ticker_items():
    from core.site import ticker_items as f
    return f()


@register.simple_tag
def site_faqs(page):
    from core.site import faqs
    return faqs(page)


@register.simple_tag
def site_instructors():
    from core.site import instructors
    return instructors()


@register.simple_tag
def site_partners():
    from core.models import Partner
    return Partner.objects.all()


@register.simple_tag
def legal_links():
    """Terms / Privacy / Refund pages that have been written (empty ones are hidden everywhere)."""
    from core.models import LegalPage
    order = {k: i for i, (k, _) in enumerate(LegalPage.SLUG_CHOICES)}
    return sorted(LegalPage.objects.exclude(body=''), key=lambda p: order[p.slug])


@register.simple_tag
def legal_url(slug):
    """URL of a written legal page, or '' when the admin has not written it yet."""
    from core.models import LegalPage
    from django.urls import reverse
    return reverse(f'website:{slug}') if LegalPage.objects.filter(slug=slug).exclude(body='').exists() else ''


@register.simple_tag
def hero_slides():
    from core.site import hero_slides as f
    return f()


@register.simple_tag
def site_team():
    from core.site import team as f
    return f()[0]


@register.simple_tag
def site_milestones():
    from core.site import milestones as f
    return f()


@register.simple_tag
def site_gallery():
    from core.site import gallery as f
    return f()


@register.simple_tag
def site_quote(page):
    from core.site import page_quote as f
    return f(page)


@register.simple_tag
def site_mentor():
    from core.site import mentor as f
    return f()


@register.simple_tag
def page_hero(seed):
    """Background picture of a page header: the uploaded header image, else a stock photo (if enabled), else '' (plain gradient)."""
    from core.models import SiteSettings, stock_photo
    site = SiteSettings.get()
    if site.hero_image:
        return site.hero_image.url
    return stock_photo(seed, 1600, 800) if SiteSettings.stock_enabled() else ''


@register.filter
def number_word(value):
    """2 -> 'two' (1-10), anything else stays a number."""
    words = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten']
    try:
        n = int(value)
    except (TypeError, ValueError):
        return value
    return words[n] if 0 <= n <= 10 else n
