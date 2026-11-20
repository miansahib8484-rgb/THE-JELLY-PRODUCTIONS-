from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from django.utils import timezone

from core.models import BlogPost, Course, LegalPage


class StaticSitemap(Sitemap):
    changefreq, priority = 'weekly', 0.7

    def items(self):
        return ['website:home', 'website:courses', 'website:about', 'website:contact', 'website:blog']

    def location(self, name):
        return reverse(name)


class CourseSitemap(Sitemap):
    changefreq, priority = 'weekly', 0.9

    def items(self):
        return Course.objects.filter(is_published=True).order_by('pk')


class BlogSitemap(Sitemap):
    changefreq, priority = 'monthly', 0.6

    def items(self):
        return BlogPost.objects.filter(is_published=True, published_at__lte=timezone.now()).order_by('pk')

    def lastmod(self, obj):
        return obj.published_at


class LegalSitemap(Sitemap):
    changefreq, priority = 'yearly', 0.2

    def items(self):
        return LegalPage.objects.exclude(body='').order_by('pk')

    def lastmod(self, obj):
        return obj.updated_at


SITEMAPS = {'pages': StaticSitemap, 'courses': CourseSitemap, 'blog': BlogSitemap, 'legal': LegalSitemap}
