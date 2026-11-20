"""Blog access for the public site. Articles live in the database (Admin > Blog)."""
from django.utils import timezone

from core.models import BlogPost

CATEGORY_LABELS = dict(BlogPost.CATEGORY_CHOICES)


def all_posts():
    """Published posts, newest first (scheduled posts appear once their time arrives)."""
    return list(BlogPost.objects.filter(is_published=True, published_at__lte=timezone.now()).defer('body'))


def get_post(slug):
    return BlogPost.objects.filter(slug=slug, is_published=True, published_at__lte=timezone.now()).first()
