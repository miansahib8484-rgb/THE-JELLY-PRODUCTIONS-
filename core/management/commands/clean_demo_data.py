"""Remove the demo data so you can start with your own.

    python manage.py clean_demo_data                   # only SHOWS what would be removed
    python manage.py clean_demo_data --yes             # removes the demo records
    python manage.py clean_demo_data --yes --reset-site   # ... and blanks the placeholder texts in Site content

What it removes (only records it can recognise as demo; everything you added yourself stays):
  * the demo users  (admin@, arshad@, and ayesha/hamza/sana/bilal/mina/usman/zara/daniyal @example.com) whose password is STILL the demo one,
  * the 8 demo courses with their batches, lessons, classes, recordings, enrollments, payments, certificates and reviews,
  * the demo contact messages, newsletter addresses, promo code JELLY10 and the demo payment account numbers,
  * the sample hero pictures, partners, timeline, team, gallery, FAQs, quotes and blog articles (the rows flagged as sample).
With --reset-site, every text in Admin > Site content that still has the original placeholder value is emptied (phone, e-mail, founder
text ...), the batch size / refund days are hidden and stock photos are switched off. Your uploaded pictures are never touched.

The demo ADMIN is kept while it is the only administrator, so you cannot lock yourself out: run `createsuperuser` first, then this command
again, and it will be removed too.
"""
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import models, transaction

from core.management.commands.seed_data import COURSES, STUDENTS
from core.models import (Announcement, Certificate, ContactSubmission, Course, Enrollment, NewsletterSubscriber, Payment,
                         PaymentAccountInfo, PromoCode, Review, SiteSettings)
from core.sample_content import SAMPLE_MODELS, clear_sample_content

User = get_user_model()

DEMO_ADMIN = 'admin@thejellyproductions.com'
DEMO_TEACHER = 'arshad@thejellyproductions.com'
DEMO_STUDENTS = [f'{full.split()[0].lower()}@example.com' for full in STUDENTS]
DEMO_PASSWORDS = {DEMO_ADMIN: 'admin12345', DEMO_TEACHER: 'teacher12345', **{e: 'student12345' for e in DEMO_STUDENTS}}
DEMO_COURSE_TITLES = [c[1] for c in COURSES]
DEMO_CONTACT_EMAILS = ['usman@example.com', 'mina@example.com', 'zara@example.com']
DEMO_NEWSLETTER = [f'{n}@example.com' for n in ('areeba', 'faizan', 'hira', 'ibrahim', 'javeria')]
DEMO_REVIEW_AUTHORS = ['Marcus Bell', 'Halima Yusuf']
KEEP_ALWAYS = {'site_name'}          # your brand name is not a placeholder
# A page header with no words at all looks broken, so these fields get neutral starter text (no names, no numbers, no claims) that you replace
# in Admin > Site content. Every other placeholder (phone, e-mail, founder text ...) is simply emptied; the site hides what is empty.
STARTER = {
    'hero_chip': 'Welcome to {site_name}',
    'hero_title': 'Learn practical skills in live, hands-on classes.',
    'hero_text': 'Browse our courses, pick a batch that suits you and learn from instructors who review your real work.',
    'about_chip': 'About us',
    'about_title': 'Learn by doing, with people who guide you.',
    'about_lead': 'We run live, hands-on courses in small batches, with real feedback on real work.',
}


class Command(BaseCommand):
    help = 'Remove the demo users, courses, payments and sample content (dry run unless --yes).'

    def add_arguments(self, parser):
        parser.add_argument('--yes', action='store_true', help='Really delete (without it the command only shows what it would do)')
        parser.add_argument('--reset-site', action='store_true', help='Also blank the placeholder texts in Site content and switch stock photos off')

    # ------------------------------------------------------------------ what is demo?
    def demo_users(self):
        """Demo accounts whose password is still the published demo password (an account you changed is YOURS and is kept)."""
        found, kept = [], []
        for u in User.objects.filter(username__in=DEMO_PASSWORDS):
            (found if u.check_password(DEMO_PASSWORDS[u.username]) else kept).append(u)
        return found, kept

    def plan(self):
        users, changed = self.demo_users()
        admins_left = User.objects.filter(is_superuser=True, is_active=True).exclude(pk__in=[u.pk for u in users])
        keep_admin = None
        if not admins_left.exists():
            keep_admin = next((u for u in users if u.is_superuser), None)
            users = [u for u in users if u is not keep_admin]
        user_ids = [u.pk for u in users]
        courses = Course.objects.filter(title__in=DEMO_COURSE_TITLES)
        enrollments = Enrollment.objects.filter(models.Q(user_id__in=user_ids) | models.Q(course__in=courses))
        # real people who joined a demo course: they lose that enrollment together with the course. Tell the owner.
        outsiders = enrollments.exclude(user_id__in=user_ids).count()
        return {
            'users': users, 'changed': changed, 'keep_admin': keep_admin, 'courses': courses, 'enrollments': enrollments, 'outsiders': outsiders,
            'payments': Payment.objects.filter(enrollment__in=enrollments),
            'certificates': Certificate.objects.filter(models.Q(student_id__in=user_ids) | models.Q(course__in=courses)),
            'reviews': Review.objects.filter(models.Q(user_id__in=user_ids) | models.Q(author_name__in=DEMO_REVIEW_AUTHORS, user__isnull=True)
                                              | models.Q(course__in=courses)),
            'announcements': Announcement.objects.filter(models.Q(teacher_id__in=user_ids) | models.Q(course__in=courses)),
            'contact': ContactSubmission.objects.filter(email__in=DEMO_CONTACT_EMAILS),
            'newsletter': NewsletterSubscriber.objects.filter(email__in=DEMO_NEWSLETTER),
            'promos': PromoCode.objects.filter(code='JELLY10', uses_count=0),
            'accounts': PaymentAccountInfo.objects.filter(easypaisa_number='0300 1234567'),
        }

    def site_placeholders(self):
        """(field, current value) for every Site-content field that still holds the original placeholder."""
        # the row is created by the first page view; until then the placeholders are simply the model defaults
        site = SiteSettings.objects.first() or SiteSettings()
        out = []
        for f in SiteSettings._meta.fields:
            if f.name in KEEP_ALWAYS or f.primary_key or not f.has_default():
                continue
            if isinstance(f, (models.CharField, models.TextField)) and not isinstance(f, models.ImageField):
                if f.default not in ('', None) and getattr(site, f.name) == f.default:
                    out.append((f, getattr(site, f.name)))
            elif isinstance(f, models.PositiveSmallIntegerField) and f.default is not None and getattr(site, f.name) == f.default:
                out.append((f, getattr(site, f.name)))
        return out

    # ------------------------------------------------------------------ run
    def handle(self, *args, **opts):
        p = self.plan()
        sample = {m: apps.get_model('core', m).objects.filter(is_sample=True).count() for m in SAMPLE_MODELS}
        sample = {k: v for k, v in sample.items() if v}
        placeholders = self.site_placeholders()
        stock_on = (SiteSettings.objects.first() or SiteSettings()).use_stock_photos

        rows = [('demo users', len(p['users'])), ('demo courses', p['courses'].count()), ('enrollments', p['enrollments'].count()),
                ('payments', p['payments'].count()), ('certificates', p['certificates'].count()), ('reviews', p['reviews'].count()),
                ('announcements', p['announcements'].count()), ('contact messages', p['contact'].count()),
                ('newsletter addresses', p['newsletter'].count()), ('demo promo code', p['promos'].count()),
                ('demo payment accounts', p['accounts'].count()), ('sample content rows', sum(sample.values()))]
        self.stdout.write(self.style.MIGRATE_HEADING('Demo data found:'))
        for label, n in rows:
            self.stdout.write(f'  {label:24s} {n}')
        if p['keep_admin']:
            self.stdout.write(self.style.WARNING(
                f"\n  KEPT: {p['keep_admin'].username} is the only administrator. Run `python manage.py createsuperuser` to create YOUR admin,\n"
                '        then run this command again to remove the demo admin as well.'))
        for u in p['changed']:
            self.stdout.write(f'  KEPT: {u.username} (its password is no longer the demo password, so it is treated as a real account)')
        if p['outsiders']:
            self.stdout.write(self.style.WARNING(f"  NOTE: {p['outsiders']} enrollment(s) of real users are in demo courses and will be removed with them."))
        if opts['reset_site']:
            self.stdout.write(self.style.MIGRATE_HEADING('\nSite content that will be emptied (placeholder text):'))
            for f, v in placeholders:
                self.stdout.write(f'  {f.verbose_name[:34]:34s} {str(v)[:50]!r}' + ('  -> neutral starter text' if f.name in STARTER else '  -> emptied'))
            if stock_on:
                self.stdout.write('  stock photos                       ON -> OFF')
        elif placeholders:
            self.stdout.write(f'\n  {len(placeholders)} Site-content field(s) still hold placeholder text (phone, e-mail, founder ...). Add --reset-site to empty them,\n'
                              '  or edit them in Admin > Site content.')

        if not opts['yes']:
            self.stdout.write(self.style.WARNING('\nNothing was changed (dry run). Run again with --yes to delete.'))
            return

        with transaction.atomic():
            # children first: Payment, Enrollment and Certificate are protected against deletion of their parents
            p['payments'].delete()
            p['certificates'].delete()
            p['reviews'].delete()
            p['announcements'].delete()
            p['enrollments'].delete()
            p['courses'].delete()
            for u in p['users']:
                u.delete()
            p['contact'].delete()
            p['newsletter'].delete()
            p['promos'].delete()
            p['accounts'].delete()
            clear_sample_content()
            if opts['reset_site']:
                site = SiteSettings.objects.first() or SiteSettings.objects.create()      # create it now, or the first page view would bring the placeholders back
                for f, _ in placeholders:
                    if f.name in STARTER:
                        setattr(site, f.name, STARTER[f.name].format(site_name=site.site_name))
                    else:
                        setattr(site, f.name, None if isinstance(f, models.PositiveSmallIntegerField) else '')
                site.use_stock_photos = False
                site.save()
        self.stdout.write(self.style.SUCCESS('\nDone. The site is empty now: add your own data in the admin dashboard (see GO_LIVE_CHECKLIST.md).'))
