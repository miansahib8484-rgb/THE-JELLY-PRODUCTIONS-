"""Regression tests for the v11 code-review fixes (one test per bug)."""
import csv
import io
import tempfile
from datetime import timedelta
from io import StringIO

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core import sample_content, services
from core.models import (Batch, Certificate, ClassRecording, ContactSubmission, Course, CurriculumLecture, CurriculumModule, Enrollment,
                         LiveClass, NewsletterSubscriber, Payment, PaymentAccountInfo, PromoCode, Review, initials_avatar)
from core.portal_forms import CourseForm
from teacher import stats as teacher_stats

User = get_user_model()
TMP = tempfile.mkdtemp()


def mk(email, role='student', **kw):
    u = User.objects.create_user(username=email, email=email, password='Passw0rd!x', first_name=email.split('@')[0], **kw)
    u.profile.role = role
    u.profile.save()
    return u


COURSE_POST = {'title': 'x', 'category': 'a', 'level': 'beginner', 'duration_weeks': 4, 'hours_per_week': 4, 'price': 10}


class CourseFormYoutube(TestCase):
    def test_full_youtube_link_longer_than_50_chars_is_accepted_and_reduced_to_the_id(self):
        link = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabcdefghijk'      # 62 characters
        form = CourseForm(dict(COURSE_POST, preview_video_id=link), role='teacher')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['preview_video_id'], 'dQw4w9WgXcQ')

    def test_garbage_is_still_refused(self):
        form = CourseForm(dict(COURSE_POST, preview_video_id='not a video'), role='teacher')
        self.assertFalse(form.is_valid())


class AvatarEscaping(TestCase):
    def test_special_characters_cannot_break_the_svg(self):
        from urllib.parse import unquote
        svg = unquote(initials_avatar('&Co <b>'))
        self.assertIn('&amp;', svg)
        self.assertIn('&lt;', svg)
        self.assertNotIn('>&<', svg)


@override_settings(PRIVATE_MEDIA_ROOT=TMP, MEDIA_ROOT=TMP)
class EnrollmentFixes(TestCase):
    def setUp(self):
        self.teacher = mk('t@x.com', 'teacher')
        self.course = Course.objects.create(title='C1', price=1000, teacher=self.teacher)
        self.batch = Batch.objects.create(course=self.course, start_date=timezone.localdate() + timedelta(days=5), seats_total=5, status='open')
        self.student = mk('s1@x.com')

    def post_enroll(self, **extra):
        self.client.force_login(self.student)
        data = {'course': str(self.course.pk), 'batch': str(self.batch.pk), 'phone': '0300 1234567', 'city': 'Lahore', 'gateway': 'easypaisa',
                'terms': 'on', 'transaction_reference': ''}
        data.update(extra)
        return self.client.post(reverse('website:enroll'), data)

    def test_invalid_promo_code_is_an_error_not_silently_dropped(self):
        r = self.post_enroll(promo_code='NOSUCHCODE')
        self.assertEqual(r.status_code, 200)                        # form shown again with an error
        self.assertFalse(Enrollment.objects.filter(user=self.student).exists())
        self.assertContains(r, 'not valid')

    def test_valid_promo_code_still_works(self):
        PromoCode.objects.create(code='SAVE10', percent_off=10)
        r = self.post_enroll(promo_code='save10')
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Payment.objects.get(enrollment__user=self.student).amount, 900)

    def test_re_reserving_a_cancelled_seat_starts_a_new_72_hour_hold(self):
        self.post_enroll()
        e = Enrollment.objects.get(user=self.student)
        services.cancel_enrollment(e, quiet=True)
        Enrollment.objects.filter(pk=e.pk).update(enrolled_at=timezone.now() - timedelta(days=10))      # an old reservation
        self.post_enroll()
        e.refresh_from_db()
        self.assertEqual(e.status, 'pending_payment')
        self.assertGreater(e.enrolled_at, timezone.now() - timedelta(minutes=5))
        call_command('expire_reservations', stdout=StringIO())
        e.refresh_from_db()
        self.assertEqual(e.status, 'pending_payment')               # the cron job must not cancel it again at once


class CurriculumOrdering(TestCase):
    def setUp(self):
        self.teacher = mk('t@x.com', 'teacher')
        self.course = Course.objects.create(title='C1', price=1000, teacher=self.teacher)

    def test_new_module_gets_a_position_after_the_last_one_even_after_a_delete(self):
        self.client.force_login(self.teacher)
        for title in ('A', 'B', 'C'):
            self.client.post(reverse('teacher:module_add', args=[self.course.slug]), {'title': title})
        CurriculumModule.objects.get(title='A').delete()
        self.client.post(reverse('teacher:module_add', args=[self.course.slug]), {'title': 'D'})
        orders = list(CurriculumModule.objects.filter(course=self.course).order_by('sort_order', 'id').values_list('title', 'sort_order'))
        self.assertEqual([t for t, _ in orders], ['B', 'C', 'D'])
        self.assertEqual(len({o for _, o in orders}), 3)            # no two modules share a position

    def test_player_keeps_modules_together_when_positions_tie(self):
        m1 = CurriculumModule.objects.create(course=self.course, title='M1', sort_order=1)
        m2 = CurriculumModule.objects.create(course=self.course, title='M2', sort_order=1)       # same position (old data)
        a1 = CurriculumLecture.objects.create(module=m1, title='a1', sort_order=0)
        b1 = CurriculumLecture.objects.create(module=m2, title='b1', sort_order=0)
        CurriculumLecture.objects.create(module=m1, title='a2', sort_order=1)
        CurriculumLecture.objects.create(module=m2, title='b2', sort_order=1)
        student = mk('s@x.com')
        e = Enrollment.objects.create(user=student, course=self.course, status='active')
        from student import stats
        # finish the first lecture of module 1: the next one must be a2 (same module), not b1
        from core.models import LectureProgress
        LectureProgress.objects.create(user=student, lecture=a1, completed=True, completed_at=timezone.now())
        self.assertEqual(stats.next_lecture(e).title, 'a2')
        self.assertIsNotNone(b1)


@override_settings(PRIVATE_MEDIA_ROOT=TMP, MEDIA_ROOT=TMP)
class TeacherStatsFixes(TestCase):
    def test_only_recent_unrecorded_classes_are_listed(self):
        t = mk('t@x.com', 'teacher')
        c = Course.objects.create(title='C1', price=1000, teacher=t)
        now = timezone.now()
        recent = LiveClass.objects.create(course=c, title='recent', starts_at=now - timedelta(days=3), duration_minutes=60)
        LiveClass.objects.create(course=c, title='ancient', starts_at=now - timedelta(days=400), duration_minutes=60)
        self.assertEqual([x.pk for x in teacher_stats.classes_needing_recording(t)], [recent.pk])


class CsvLocalDates(TestCase):
    def test_export_uses_pakistan_dates_not_utc(self):
        admin = User.objects.create_superuser('admin@x.com', 'admin@x.com', 'Passw0rd!x')
        s = mk('s@x.com')
        # 23:30 UTC on 1 June is already 04:30 on 2 June in Pakistan (UTC+5)
        User.objects.filter(pk=s.pk).update(date_joined=timezone.datetime(2026, 6, 1, 23, 30, tzinfo=timezone.timezone.utc))
        self.client.force_login(admin)
        r = self.client.get(reverse('adminpanel:export', args=['students']))
        rows = list(csv.reader(io.StringIO(r.content.decode())))
        mine = [row for row in rows if row[2] == 's@x.com'][0]
        self.assertEqual(mine[0], '2026-06-02')


class SeedGuard(TestCase):
    @override_settings(DEBUG=False)
    def test_seed_data_refuses_to_run_when_debug_is_off(self):
        with self.assertRaises(CommandError):
            call_command('seed_data', stdout=StringIO())
        self.assertFalse(User.objects.filter(email='admin@thejellyproductions.com').exists())

    @override_settings(DEBUG=True)
    def test_seed_data_does_not_reset_the_password_of_an_existing_account(self):
        u = User.objects.create_user('admin@thejellyproductions.com', 'admin@thejellyproductions.com', 'MyOwnSecret!77')
        call_command('seed_data', stdout=StringIO())
        u.refresh_from_db()
        self.assertTrue(u.check_password('MyOwnSecret!77'))


class ServerErrorPage(TestCase):
    def test_500_page_renders_without_touching_the_database(self):
        from django.test import RequestFactory
        from website.views import server_error
        with self.assertNumQueries(0):
            r = server_error(RequestFactory().get('/boom/'))
        self.assertEqual(r.status_code, 500)
        self.assertIn(b'Something went wrong', r.content)


class ReviewCommentLimit(TestCase):
    def test_comment_over_2000_characters_is_refused(self):
        from student.forms import StudentReviewForm
        self.assertFalse(StudentReviewForm({'rating': '5', 'comment': 'x' * 2001}).is_valid())
        self.assertTrue(StudentReviewForm({'rating': '5', 'comment': 'x' * 2000}).is_valid())
        self.assertEqual(Review.objects.count(), 0)


class RegisterEmailLength(TestCase):
    def test_email_longer_than_150_characters_is_refused_by_the_form(self):
        from accounts.forms import RegisterForm
        form = RegisterForm({'email': ('a' * 145) + '@x.com'})
        form.is_valid()
        self.assertIn('email', form.errors)


class DatabaseUrl(TestCase):
    def test_sslmode_in_database_url_is_kept(self):
        from config.settings import _database_from_url
        cfg = _database_from_url('postgres://u:p%40ss@db.example.com:5432/app?sslmode=require&evil=1')
        self.assertEqual(cfg['OPTIONS'], {'sslmode': 'require'})
        self.assertEqual(cfg['PASSWORD'], 'p@ss')


# ---------------------------------------------------------------- second round: the items that were "not changed" in the first report
from django.db import IntegrityError, transaction as _tx   # noqa: E402


@override_settings(PRIVATE_MEDIA_ROOT=TMP, MEDIA_ROOT=TMP)
class SecondRound(TestCase):
    def setUp(self):
        self.teacher = mk('t@x.com', 'teacher')
        self.course = Course.objects.create(title='C1', price=1000, teacher=self.teacher)
        self.student = mk('s@x.com')
        self.admin = User.objects.create_superuser('admin@x.com', 'admin@x.com', 'Passw0rd!x')

    def test_database_refuses_a_second_review_of_the_same_course(self):
        Review.objects.create(course=self.course, user=self.student, comment='a')
        with self.assertRaises(IntegrityError), _tx.atomic():
            Review.objects.create(course=self.course, user=self.student, comment='b')
        Review.objects.create(course=self.course, user=None, author_name='Anon', comment='c')       # anonymous reviews are not limited
        Review.objects.create(course=self.course, user=None, author_name='Anon2', comment='d')

    def test_refund_gives_the_promo_use_back(self):
        promo = PromoCode.objects.create(code='SAVE10', percent_off=10, max_uses=5)
        batch = Batch.objects.create(course=self.course, start_date=timezone.localdate() + timedelta(days=5), seats_total=5, status='open')
        e = Enrollment.objects.create(user=self.student, course=self.course, batch=batch)
        p = Payment.objects.create(enrollment=e, amount=900, promo_code='SAVE10', transaction_reference='T1')
        services.approve_payment(p, self.admin)
        promo.refresh_from_db()
        self.assertEqual(promo.uses_count, 1)
        services.refund_enrollment(e, self.admin)
        promo.refresh_from_db()
        self.assertEqual(promo.uses_count, 0)

    def test_upcoming_batch_keeps_its_status_when_it_fills_and_frees(self):
        batch = Batch.objects.create(course=self.course, start_date=timezone.localdate() + timedelta(days=5), seats_total=1, status='upcoming')
        e = Enrollment.objects.create(user=self.student, course=self.course, batch=batch, status='pending_payment')
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'upcoming')
        self.assertEqual(batch.seats_filled, 1)
        services.cancel_enrollment(e, quiet=True)
        batch.refresh_from_db()
        self.assertEqual((batch.status, batch.seats_filled), ('upcoming', 0))

    def test_repeated_transaction_id_is_flagged_for_the_admin(self):
        other = mk('s2@x.com')
        for u in (self.student, other):
            e = Enrollment.objects.create(user=u, course=self.course)
            Payment.objects.create(enrollment=e, amount=1000, transaction_reference='TXN-777', status='pending')
        e3 = Enrollment.objects.create(user=mk('s3@x.com'), course=self.course)
        Payment.objects.create(enrollment=e3, amount=1000, transaction_reference='UNIQUE-1', status='pending')
        self.client.force_login(self.admin)
        r = self.client.get(reverse('adminpanel:payments'))
        self.assertContains(r, 'Same transaction id used on another payment', count=2)

    def test_live_class_tabs_are_decided_in_the_database(self):
        now = timezone.now()
        mkc = lambda t, **kw: LiveClass.objects.create(course=self.course, title=t, duration_minutes=60, **kw)   # noqa: E731
        mkc('future', starts_at=now + timedelta(days=1))
        mkc('running', starts_at=now - timedelta(minutes=10))
        mkc('old', starts_at=now - timedelta(days=3))
        mkc('stopped', starts_at=now - timedelta(minutes=10), ended_early=True)
        self.client.force_login(self.admin)
        titles = lambda tab: {c.title for c in self.client.get(reverse('adminpanel:live'), {'status': tab}).context['page']}   # noqa: E731
        self.assertEqual(titles('upcoming'), {'future', 'running'})
        self.assertEqual(titles('past'), {'old', 'stopped'})
        self.assertEqual(titles('all'), {'future', 'running', 'old', 'stopped'})

    def test_blog_list_is_paginated(self):
        from core.models import BlogPost
        for i in range(12):
            BlogPost.objects.create(title=f'Post {i}', slug=f'post-{i}', body='x', excerpt='e', category='design', is_published=True,
                                    published_at=timezone.now() - timedelta(days=i))
        page1 = self.client.get(reverse('website:blog'))
        self.assertEqual(len(page1.context['posts']), 9)
        self.assertEqual(len(self.client.get(reverse('website:blog'), {'page': 2}).context['posts']), 3)

    def test_password_reset_with_broken_smtp_shows_the_normal_done_page(self):
        from unittest import mock
        with mock.patch('django.core.mail.message.EmailMultiAlternatives.send', side_effect=OSError('smtp down')):
            r = self.client.post(reverse('accounts:password_reset'), {'email': 's@x.com'})
        self.assertRedirects(r, reverse('accounts:password_reset_done'))



@override_settings(PRIVATE_MEDIA_ROOT=TMP, MEDIA_ROOT=TMP)
class ThirdRound(TestCase):
    def test_same_student_reusing_a_reference_is_not_flagged_as_duplicate(self):
        admin = User.objects.create_superuser('admin@x.com', 'admin@x.com', 'Passw0rd!x')
        teacher = mk('t@x.com', 'teacher')
        c1 = Course.objects.create(title='C1', price=1000, teacher=teacher)
        c2 = Course.objects.create(title='C2', price=1000, teacher=teacher)
        s = mk('s@x.com')
        for c in (c1, c2):          # one student, two enrollments, same reference (e.g. a cancelled enrollment he re-booked elsewhere)
            Payment.objects.create(enrollment=Enrollment.objects.create(user=s, course=c), amount=1000, transaction_reference='SAME-1')
        self.client.force_login(admin)
        self.assertNotContains(self.client.get(reverse('adminpanel:payments'), {'status': 'all'}), 'Same transaction id used')

    def test_course_form_keeps_its_original_label_and_help_text(self):
        f = CourseForm(role='teacher').fields['preview_video_id']
        self.assertEqual((f.label, f.help_text), ('Preview video id', 'YouTube video ID'))     # the original design text

    def test_review_migrations_are_split_into_data_and_schema(self):
        import importlib
        d = importlib.import_module('core.migrations.0013_dedupe_reviews').Migration
        s = importlib.import_module('core.migrations.0014_review_unique_per_user').Migration
        self.assertEqual(s.dependencies, [('core', '0013_dedupe_reviews')])
        self.assertTrue(all(op.__class__.__name__ == 'RunPython' for op in d.operations))
        self.assertTrue(all(op.__class__.__name__ == 'AddConstraint' for op in s.operations))


class SampleContentRobust(TestCase):
    def test_article_with_unclosed_front_matter_does_not_crash_the_loader(self):
        import pathlib
        from unittest import mock
        from core import sample_content
        from core.models import BlogPost
        d = pathlib.Path(tempfile.mkdtemp())
        (d / 'broken.md').write_text('---\ntitle: Broken\nno closing line', encoding='utf-8')
        with mock.patch.object(sample_content, '_blog_files', return_value=[d / 'broken.md']):
            made = sample_content.load_sample_content()
        self.assertEqual(made.get('BlogPost'), 1)
        self.assertTrue(BlogPost.objects.filter(slug='broken').exists())


class FourthRound(TestCase):
    def test_new_batch_cannot_start_in_the_past_but_an_old_batch_can_be_edited(self):
        from core.portal_forms import BatchForm
        data = {'schedule_label': 'Mon', 'seats_total': 5, 'status': 'open'}
        old = timezone.localdate() - timedelta(days=30)
        self.assertFalse(BatchForm(dict(data, start_date=old.isoformat())).is_valid())
        self.assertTrue(BatchForm(dict(data, start_date=timezone.localdate().isoformat())).is_valid())
        course = Course.objects.create(title='C', price=1)
        started = Batch.objects.create(course=course, start_date=old, seats_total=5, status='open')
        self.assertTrue(BatchForm(dict(data, start_date=old.isoformat(), seats_total=9), instance=started).is_valid())

    def test_registration_needs_a_plausible_age(self):
        from accounts.forms import RegisterForm
        f = RegisterForm({'date_of_birth': (timezone.localdate() - timedelta(days=365 * 6)).isoformat()})
        f.is_valid()
        self.assertIn('date_of_birth', f.errors)
        f = RegisterForm({'date_of_birth': (timezone.localdate() - timedelta(days=365 * 25)).isoformat()})
        f.is_valid()
        self.assertNotIn('date_of_birth', f.errors)


@override_settings(PRIVATE_MEDIA_ROOT=TMP, MEDIA_ROOT=TMP)
class TemplateFixes(TestCase):
    def setUp(self):
        self.teacher = mk('t@x.com', 'teacher')
        self.student = mk('s@x.com')

    def test_og_image_is_a_valid_absolute_url_for_stock_and_uploaded_pictures(self):
        c = Course.objects.create(title='NoPic', price=1, is_published=True)
        html = self.client.get(c.get_absolute_url()).content.decode()
        self.assertIn('og:image" content="https://picsum.photos/', html)
        self.assertNotIn('testserverhttps', html)

    def test_empty_course_description_shows_the_coming_soon_text(self):
        c = Course.objects.create(title='Empty', price=1, is_published=True, description='')
        self.assertContains(self.client.get(c.get_absolute_url()), 'Details coming soon.')
        c2 = Course.objects.create(title='Full', price=1, is_published=True, description='Real text here')
        self.assertContains(self.client.get(c2.get_absolute_url()), 'Real text here')

    def test_dashboard_uses_the_editable_site_name(self):
        from core.models import SiteSettings
        s = SiteSettings.get(); s.site_name = 'Acme Academy'; s.save()
        self.client.force_login(self.student)
        html = self.client.get(reverse('student:dashboard')).content.decode()
        self.assertIn('Acme Academy', html)
        self.assertNotIn('The Jelly Productions', html)

    def test_enroll_and_pay_pages_do_not_show_empty_payment_accounts(self):
        c = Course.objects.create(title='C', price=100, is_published=True)
        e = Enrollment.objects.create(user=self.student, course=c)
        Payment.objects.create(enrollment=e, amount=100)
        self.client.force_login(self.student)
        enroll = self.client.get(reverse('website:enroll')).content.decode()
        self.assertNotIn('Send to <b></b>', enroll)
        self.assertIn('not set up yet', enroll)
        pay = self.client.get(reverse('student:pay', args=[e.pk])).content.decode()
        self.assertIn('have not been set up yet', pay)
        info = PaymentAccountInfo.get(); info.easypaisa_number = '0300-1234567'; info.easypaisa_account_name = 'Acme'; info.save()
        pay = self.client.get(reverse('student:pay', args=[e.pk])).content.decode()
        self.assertIn('0300-1234567', pay)
        self.assertNotIn('have not been set up yet', pay)


class FifthRound(TestCase):
    def test_csv_export_opens_correctly_in_excel(self):
        admin = User.objects.create_superuser('admin@x.com', 'admin@x.com', 'Passw0rd!x')
        s = mk('s@x.com'); s.last_name = 'Müller ☺'; s.save()
        self.client.force_login(admin)
        r = self.client.get(reverse('adminpanel:export', args=['students']))
        self.assertTrue(r.content.startswith(b'\xef\xbb\xbf'))
        self.assertIn('Müller ☺', r.content.decode('utf-8-sig'))
        self.assertNotIn('\ufeff', r.content.decode('utf-8-sig'))      # exactly one BOM

    def test_permissions_policy_header_is_sent(self):
        self.assertIn('camera=()', self.client.get('/').headers['Permissions-Policy'])

    def test_site_url_and_sender_are_derived_from_the_allowed_host_when_not_set(self):
        import os
        import subprocess
        import sys
        from django.conf import settings as dj
        env = {k: v for k, v in os.environ.items() if k not in ('SITE_URL', 'DEFAULT_FROM_EMAIL', 'DJANGO_DEBUG')}
        env.update(DJANGO_SECRET_KEY='k' * 60, DJANGO_ALLOWED_HOSTS='school.example.org,www.school.example.org')
        code = 'from config import settings as s; print(s.SITE_URL, s.DEFAULT_FROM_EMAIL)'
        out = subprocess.run([sys.executable, '-c', code], cwd=dj.BASE_DIR, env=env, capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, 'https://school.example.org The Jelly Productions <no-reply@school.example.org>')
        env['SITE_URL'] = 'https://custom.example.com'
        out = subprocess.run([sys.executable, '-c', code], cwd=dj.BASE_DIR, env=env, capture_output=True, text=True).stdout.strip()
        self.assertTrue(out.startswith('https://custom.example.com '))
        env.update(DJANGO_DEBUG='1')
        env.pop('SITE_URL')
        out = subprocess.run([sys.executable, '-c', code], cwd=dj.BASE_DIR, env=env, capture_output=True, text=True).stdout.strip()
        self.assertTrue(out.startswith('http://localhost:8000 '))


class PerRequestCache(TestCase):
    def test_helper_runs_once_per_page_but_not_on_post_or_outside_a_request(self):
        from django.test import RequestFactory
        from core.request_cache import RequestCacheMiddleware, per_request
        calls = []

        @per_request
        def heavy(user_id):
            calls.append(user_id)
            return len(calls)

        def run(method):
            calls.clear()
            seen = []

            def view(request):
                seen.extend([heavy(1), heavy(1), heavy(2)])
                from django.http import HttpResponse
                return HttpResponse('x')
            RequestCacheMiddleware(view)(getattr(RequestFactory(), method)('/'))
            return list(calls), seen
        self.assertEqual(run('get')[0], [1, 2])                # same argument: one call; another argument: its own call
        self.assertEqual(run('head')[0], [1, 2])
        self.assertEqual(run('post')[0], [1, 1, 2])            # a POST may change data half-way: never cached
        calls.clear()
        heavy(1), heavy(1)
        self.assertEqual(calls, [1, 1])                        # outside a request nothing is cached

    def test_student_dashboard_repeats_no_progress_queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        t = mk('t@x.com', 'teacher')
        c = Course.objects.create(title='C', price=1, teacher=t, is_published=True)
        m = CurriculumModule.objects.create(course=c, title='M')
        CurriculumLecture.objects.create(module=m, title='L')
        s = mk('s@x.com')
        Enrollment.objects.create(user=s, course=c, status='active')
        self.client.force_login(s)
        with CaptureQueriesContext(connection) as q:
            self.assertEqual(self.client.get(reverse('student:dashboard')).status_code, 200)
        import collections
        sql = collections.Counter(x['sql'] for x in q.captured_queries if 'core_curriculumlecture' in x['sql'] or 'core_lectureprogress' in x['sql'])
        self.assertEqual([n for n in sql.values() if n > 1], [])      # the same progress query used to run up to 4 times

    def test_compose_file_schedules_the_housekeeping_jobs(self):
        import pathlib
        try:
            import yaml
        except ImportError:
            self.skipTest('PyYAML not installed')
        from django.conf import settings as dj
        data = yaml.safe_load((pathlib.Path(dj.BASE_DIR) / 'docker-compose.yml').read_text())
        cron = data['services']['cron']
        self.assertIn('expire_reservations', cron['command'][-1])
        self.assertIn('clearsessions', cron['command'][-1])
        self.assertEqual(cron['depends_on']['web']['condition'], 'service_healthy')
        self.assertEqual(cron['env_file'], data['services']['web']['env_file'])


class AdminListsDoNotQueryPerRow(TestCase):
    def test_query_count_does_not_grow_with_the_number_of_rows(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        admin = User.objects.create_superuser('admin@x.com', 'admin@x.com', 'Passw0rd!x')
        self.client.force_login(admin)
        c = Course.objects.create(title='C', price=1, is_published=True)

        def counts():
            out = {}
            for name in ('adminpanel:teachers', 'adminpanel:students', 'adminpanel:enrollments'):
                with CaptureQueriesContext(connection) as q:
                    self.client.get(reverse(name))
                out[name] = len(q)
            return out
        mk('first_t@x.com', 'teacher'); first = mk('first_s@x.com'); Enrollment.objects.create(user=first, course=c)
        counts()                         # warm-up: the very first request fills the site-settings cache
        few = counts()
        for i in range(8):
            mk(f't{i}@x.com', 'teacher'); Enrollment.objects.create(user=mk(f's{i}@x.com'), course=c)
        self.assertEqual(counts(), few)


class AccessibleNames(TestCase):
    def test_every_bootstrap_form_control_has_an_accessible_name(self):
        from core.portal_forms import BatchForm
        f = BatchForm()
        for name in ('start_date', 'seats_total', 'status'):
            self.assertTrue(f.fields[name].widget.attrs.get('aria-label'), name)
        self.assertEqual(f.fields['seats_total'].widget.attrs['aria-label'], str(f.fields['seats_total'].label or 'Seats total'))

    def test_hero_carousel_indicators_and_blog_image_links_are_named_or_hidden(self):
        from core.models import BlogPost
        BlogPost.objects.create(title='T', slug='t', body='x', excerpt='e', category='design', is_published=True, published_at=timezone.now())
        html = self.client.get(reverse('website:blog')).content.decode()
        self.assertIn('tabindex="-1" aria-hidden="true"><img', html)


@override_settings(PRIVATE_MEDIA_ROOT=TMP, MEDIA_ROOT=TMP)
class StatBandGrammar(TestCase):
    def test_singular_and_plural_labels(self):
        from core import site
        t = mk('t@x.com', 'teacher')
        c = Course.objects.create(title='C', price=1, teacher=t, is_published=True)
        s = mk('s@x.com')
        Enrollment.objects.create(user=s, course=c, status='active')
        from core.models import Certificate
        Certificate.objects.create(student=s, course=c, file='certificates/x.pdf')
        labels = {i['label'] for i in site.live_stats()}
        self.assertIn('student learning with us', labels)
        self.assertIn('certificate issued', labels)
        self.assertNotIn('students learning with us', labels)
        s2 = mk('s2@x.com'); Enrollment.objects.create(user=s2, course=c, status='active')
        Certificate.objects.create(student=s2, course=c, file='certificates/y.pdf')
        labels = {i['label'] for i in site.live_stats()}
        self.assertIn('students learning with us', labels)
        self.assertIn('certificates issued', labels)


@override_settings(PRIVATE_MEDIA_ROOT=TMP, MEDIA_ROOT=TMP, DEBUG=True)
class CleanDemoData(TestCase):
    def seed(self):
        call_command('seed_data', stdout=StringIO())

    def clean(self, *args):
        out = StringIO()
        call_command('clean_demo_data', *args, stdout=out)
        return out.getvalue()

    def test_dry_run_changes_nothing(self):
        self.seed()
        before = (User.objects.count(), Course.objects.count(), Payment.objects.count())
        out = self.clean()
        self.assertIn('dry run', out)
        self.assertEqual((User.objects.count(), Course.objects.count(), Payment.objects.count()), before)

    def test_removes_all_demo_data_but_keeps_your_own(self):
        self.seed()
        real_teacher = mk('real_t@school.pk', 'teacher')
        real_course = Course.objects.create(title='My real course', price=5000, teacher=real_teacher, is_published=True)
        real_student = mk('real_s@school.pk')
        real_enrollment = Enrollment.objects.create(user=real_student, course=real_course, status='active')
        owner = User.objects.create_superuser('owner@school.pk', 'owner@school.pk', 'Passw0rd!x')
        self.clean('--yes')
        self.assertEqual(set(User.objects.values_list('username', flat=True)), {'real_t@school.pk', 'real_s@school.pk', 'owner@school.pk'})
        self.assertEqual(list(Course.objects.values_list('title', flat=True)), ['My real course'])
        self.assertTrue(Enrollment.objects.filter(pk=real_enrollment.pk).exists())
        for model in (Payment, Certificate, ContactSubmission, NewsletterSubscriber, PromoCode, Batch, LiveClass, ClassRecording):
            self.assertEqual(model.objects.count(), 0, model.__name__)
        self.assertEqual(Review.objects.count(), 0)
        for name in sample_content.SAMPLE_MODELS:
            self.assertEqual(apps.get_model('core', name).objects.filter(is_sample=True).count(), 0, name)
        self.assertTrue(owner.pk)

    def test_the_only_admin_is_never_removed(self):
        self.seed()
        out = self.clean('--yes')
        self.assertTrue(User.objects.filter(username='admin@thejellyproductions.com').exists())
        self.assertIn('createsuperuser', out)
        User.objects.create_superuser('owner@school.pk', 'owner@school.pk', 'Passw0rd!x')
        self.clean('--yes')
        self.assertFalse(User.objects.filter(username='admin@thejellyproductions.com').exists())

    def test_a_demo_account_whose_password_you_changed_is_kept(self):
        self.seed()
        User.objects.create_superuser('owner@school.pk', 'owner@school.pk', 'Passw0rd!x')
        mine = User.objects.get(username='ayesha@example.com')
        mine.set_password('A-new-private-password-1'); mine.save()
        out = self.clean('--yes')
        self.assertTrue(User.objects.filter(username='ayesha@example.com').exists())
        self.assertIn('treated as a real account', out)

    def test_reset_site_empties_placeholders_and_keeps_pages_working(self):
        from core.models import SiteSettings
        self.seed()
        User.objects.create_superuser('owner@school.pk', 'owner@school.pk', 'Passw0rd!x')
        self.clean('--yes', '--reset-site')
        s = SiteSettings.objects.get()
        self.assertEqual((s.phone, s.email, s.founder_name, s.founder_title, s.home_quote), ('', '', '', '', ''))
        self.assertIsNone(s.batch_cap)
        self.assertFalse(s.use_stock_photos)
        self.assertIn('Learn practical skills', s.hero_title)                  # a page header is never left empty
        self.assertEqual(s.site_name, 'The Jelly Productions')                 # your brand name is not a placeholder
        for name in ('website:home', 'website:about', 'website:contact', 'website:courses', 'website:blog'):
            html = self.client.get(reverse(name))
            self.assertEqual(html.status_code, 200, name)
            for fake in ('Ridgeline', '1234567', 'Arshad', 'Lahore'):
                self.assertNotContains(html, fake, msg_prefix=name)
        # the first page view must not bring the placeholders back
        self.assertEqual(SiteSettings.objects.count(), 1)
        self.assertEqual(SiteSettings.objects.get().phone, '')

    def test_running_it_twice_is_harmless(self):
        self.seed()
        User.objects.create_superuser('owner@school.pk', 'owner@school.pk', 'Passw0rd!x')
        self.clean('--yes', '--reset-site')
        out = self.clean('--yes', '--reset-site')
        self.assertIn('Done.', out)
        self.assertEqual(User.objects.count(), 1)


class NewCourseHasNoPreselectedCategory(TestCase):
    def test_category_must_be_typed_for_a_new_course_but_stays_for_an_existing_one(self):
        self.assertEqual(CourseForm(role='teacher').initial.get('category'), '')
        data = dict(COURSE_POST); data.pop('category')
        self.assertFalse(CourseForm(data, role='teacher').is_valid())
        c = Course.objects.create(title='Excel', price=1, category='Accounting')
        self.assertEqual(CourseForm(instance=c, role='teacher').initial['category'], 'Accounting')


# ================================================================== security hardening: limits, error pages, logging, lock-outs
import warnings  # noqa: E402

from django.core.cache import cache  # noqa: E402
from django.core.exceptions import ValidationError  # noqa: E402


class InputLimits(TestCase):
    def test_text_fields_have_a_maximum_on_the_server_and_in_the_html(self):
        from accounts.forms import LoginForm, RegisterForm
        from adminpanel.forms import BlogForm, PromoForm, SiteSettingsForm
        from website.forms import EnrollForm
        self.assertEqual(RegisterForm().fields['password1'].widget.attrs['maxlength'], 128)
        self.assertEqual(LoginForm().fields['username'].widget.attrs['maxlength'], 254)
        for form, name, limit in ((RegisterForm, 'goal', 1000), (EnrollForm, 'notes', 500), (BlogForm, 'body', 60000), (SiteSettingsForm, 'hero_text', 400)):
            f = form().fields[name]
            self.assertEqual(f.max_length, limit, name)
            with self.assertRaises(ValidationError):
                f.clean('x' * (limit + 1))
            f.clean('x' * limit) if f.required else f.clean('x' * limit)
        self.assertEqual(PromoForm().fields['max_uses'].max_value, 100000)

    def test_a_huge_password_is_refused_before_it_is_hashed(self):
        from accounts.forms import RegisterForm
        f = RegisterForm({'password1': 'a' * 5000, 'password2': 'a' * 5000})
        f.is_valid()
        self.assertIn('password1', f.errors)

    def test_numbers_have_sensible_upper_limits(self):
        data = dict(COURSE_POST, price=10 ** 9)
        f = CourseForm(data, role='teacher'); f.is_valid()
        self.assertIn('price', f.errors)
        f = CourseForm(dict(COURSE_POST, duration_weeks=500), role='teacher'); f.is_valid()
        self.assertIn('duration_weeks', f.errors)
        f = CourseForm(dict(COURSE_POST, hours_per_week=200), role='teacher'); f.is_valid()
        self.assertIn('hours_per_week', f.errors)
        from core.portal_forms import BatchForm
        f = BatchForm({'start_date': (timezone.localdate() + timedelta(days=3)).isoformat(), 'seats_total': 9999, 'status': 'open', 'schedule_label': 'x'}); f.is_valid()
        self.assertIn('seats_total', f.errors)

    def test_recording_link_longer_than_the_database_column_is_refused(self):
        from core.portal_forms import RecordingForm
        f = RecordingForm({'rec_type': 'link', 'video_url': 'https://example.com/' + 'a' * 300, 'title': 'x'}); f.is_valid()
        self.assertIn('video_url', f.errors)

    def test_phone_reference_account_and_promo_formats(self):
        from core.forms import account_number_validator, phone_validator, promo_code_validator, reference_validator
        for ok in ('0300 1234567', '+92 300 1234567', '(042) 111-222-333'):
            phone_validator(ok)
        for bad in ('abc', '0300', '+', "0300'; DROP TABLE", '<script>'):
            with self.assertRaises(ValidationError):
                phone_validator(bad)
        for ok in ('12345678901', 'TXN-2026/10 #7', 'A1'):
            reference_validator(ok)
        for bad in ('', '<b>', 'a\nb', '; rm -rf', 'x' * 101):
            with self.assertRaises(ValidationError):
                reference_validator(bad)
        account_number_validator('PK36 SCBL 0000 0011 2345 6702')
        with self.assertRaises(ValidationError):
            account_number_validator('12<3')
        promo_code_validator('EID-20_b')
        with self.assertRaises(ValidationError):
            promo_code_validator('EID 20!')


class DangerousCharacters(TestCase):
    def test_one_line_fields_refuse_line_breaks_null_bytes_and_text_direction_tricks(self):
        from website.forms import ContactForm
        base = {'name': 'Ali', 'email': 'a@b.com', 'topic': '', 'message': 'hello'}
        for bad_name in ('Ali\r\nBcc: x@y.com', 'Ali\x00', 'Ali\u202eevil'):
            f = ContactForm(dict(base, name=bad_name)); f.is_valid()
            self.assertIn('name', f.errors, repr(bad_name))
        f = ContactForm(dict(base, message='line one\nline two')); f.is_valid()
        self.assertNotIn('message', f.errors)                     # a message box may have several lines

    def test_visitor_text_cannot_contain_html_tags_but_admin_content_may_show_a_less_than_sign(self):
        from adminpanel.forms import SiteSettingsForm
        from website.forms import ContactForm
        f = ContactForm({'name': 'Ali', 'email': 'a@b.com', 'message': 'hi <script>alert(1)</script>'}); f.is_valid()
        self.assertIn('message', f.errors)
        f = ContactForm({'name': 'Ali', 'email': 'a@b.com', 'message': 'I scored 3 < 5 and love <3'}); f.is_valid()
        self.assertNotIn('message', f.errors)
        self.assertNotIn('hero_text', (lambda ff: (ff.is_valid(), ff.errors)[1])(SiteSettingsForm({'hero_text': 'Replies in <24h'})))


class ErrorPages(TestCase):
    def test_missing_csrf_token_gives_a_friendly_page_not_a_bare_403(self):
        from django.test import Client
        r = Client(enforce_csrf_checks=True).post(reverse('accounts:login'), {'username': 'a', 'password': 'b'})
        self.assertEqual(r.status_code, 403)
        self.assertContains(r, 'This page has expired', status_code=403)

    def test_a_too_large_form_gives_a_styled_400_page(self):
        with override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=1000):
            r = self.client.post(reverse('website:contact'), {'message': 'x' * 5000, 'name': 'a', 'email': 'a@b.com'})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, 'could not process that request', status_code=400)

    def test_error_handlers_render_even_when_the_database_is_the_problem(self):
        from unittest import mock
        from django.test import RequestFactory
        from website import views
        req = RequestFactory().get('/x/')
        with mock.patch('core.errors.render', side_effect=RuntimeError('database down')):
            r = views.permission_denied(req)
        self.assertEqual(r.status_code, 403)
        self.assertIn(b'not allowed', r.content)

    @override_settings(THROTTLE_ENABLED=True)
    def test_rate_limit_page_is_styled_and_says_how_long_to_wait(self):
        cache.clear()
        for _ in range(7):
            r = self.client.post(reverse('website:contact'), {'name': 'a', 'email': 'a@b.com', 'message': 'hi'})
        self.assertEqual(r.status_code, 429)
        self.assertContains(r, 'Too many attempts', status_code=429)
        self.assertEqual(r['Retry-After'], '3600')
        cache.clear()


@override_settings(THROTTLE_ENABLED=True)
class LoginProtection(TestCase):
    def setUp(self):
        cache.clear()
        self.user = mk('victim@x.com')

    def tearDown(self):
        cache.clear()

    def post(self, ip, password='wrong', username='victim@x.com'):
        return self.client.post(reverse('accounts:login'), {'username': username, 'password': password}, REMOTE_ADDR=ip)

    def test_failed_sign_ins_are_logged_without_the_password(self):
        with self.assertLogs('jelly.security', level='WARNING') as cm:
            self.post('10.0.0.1', password='SuperSecret123')
        self.assertTrue(any('login_failed' in line for line in cm.output))
        self.assertFalse(any('SuperSecret123' in line for line in cm.output))

    def test_one_account_is_locked_even_when_the_guesses_come_from_many_addresses(self):
        for i in range(80):
            self.post(f'10.1.{i // 250}.{i % 250 + 1}')
        r = self.post('10.9.9.9', password='Passw0rd!x')               # the CORRECT password, from a new address
        self.assertEqual(r.status_code, 429)
        self.assertContains(r, 'Too many failed sign-in attempts', status_code=429)

    def test_odd_user_names_do_not_break_the_cache_or_the_log(self):
        with warnings.catch_warnings():
            warnings.simplefilter('error')                                # a CacheKeyWarning would be raised here
            r = self.post('10.0.0.2', username='a b\r\nFAKE LOG LINE\x00' + 'z' * 500)       # refused by the form: no crash
            self.assertEqual(r.status_code, 200)
            with self.assertLogs('jelly.security', level='WARNING') as cm:
                self.post('10.0.0.3', username='FAKE LOG LINE with spaces ' + 'z' * 150)       # a legal but odd name
        self.assertTrue(any('login_failed' in line for line in cm.output))
        self.assertEqual(len([line for line in cm.output if 'FAKE LOG LINE' in line]), 0)       # the typed name is never copied into the log

    def test_denied_access_is_logged(self):
        self.client.force_login(self.user)
        with self.assertLogs('jelly.security', level='WARNING') as cm:
            self.client.get(reverse('adminpanel:overview'))
        self.assertTrue(any('access_denied' in line for line in cm.output))


@override_settings(THROTTLE_ENABLED=True)
class AbuseLimits(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_certificate_codes_cannot_be_tried_in_bulk(self):
        codes = [self.client.get(reverse('website:verify_certificate', args=[f'JP-{i:08d}'])).status_code for i in range(33)]
        self.assertEqual(codes[:30], [404] * 30)
        self.assertEqual(codes[30:], [429] * 3)

    def test_enrolment_form_is_rate_limited(self):
        s = mk('s@x.com')
        self.client.force_login(s)
        last = [self.client.post(reverse('website:enroll'), {}).status_code for _ in range(122)]
        self.assertNotIn(429, last[:120])          # 120 an hour per address
        self.assertEqual(last[-1], 429)


class PrivatePagesAreNotCached(TestCase):
    def test_signed_in_pages_send_no_store_and_public_pages_do_not(self):
        s = mk('s@x.com')
        self.client.force_login(s)
        h = self.client.get(reverse('student:dashboard'))['Cache-Control']
        self.assertIn('no-store', h)
        self.assertIn('private', h)
        self.client.logout()
        self.assertNotIn('no-store', self.client.get(reverse('website:home')).get('Cache-Control', ''))


class SettingsCannotFallBackToTheTestKeyInProduction(TestCase):
    def run_py(self, argv, env_extra=None):
        import os
        import subprocess
        import sys
        from django.conf import settings as dj
        env = {k: v for k, v in os.environ.items() if k not in ('DJANGO_SECRET_KEY', 'DJANGO_DEBUG')}
        env.update(env_extra or {})
        code = f"import sys; sys.argv = {argv!r}\nfrom config import settings as s; print('KEY', s.SECRET_KEY[:12])"
        return subprocess.run([sys.executable, '-c', code], cwd=dj.BASE_DIR, env=env, capture_output=True, text=True)

    def test_a_server_command_line_that_contains_the_word_test_still_demands_a_real_secret_key(self):
        r = self.run_py(['gunicorn', 'config.wsgi:application', '--name', 'test'])
        self.assertNotIn('KEY dev-only', r.stdout)
        self.assertIn('DJANGO_SECRET_KEY', r.stderr)
        r = self.run_py(['manage.py', 'test'])
        self.assertIn('KEY dev-only', r.stdout)


# ================================================================== administrator creates teachers and students; the given password is kept
class AdminCreatesAccounts(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin@x.com', 'admin@x.com', 'Passw0rd!x')
        self.client.force_login(self.admin)

    def create(self, kind, **extra):
        data = {'first_name': 'Sana', 'last_name': 'Malik', 'email': 'sana@x.com', 'password': 'Given-Pass-4821'}
        data.update(extra)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse(f'adminpanel:{kind}'), data)

    def test_admin_adds_a_student_who_signs_in_at_once_with_the_given_password(self):
        from django.core import mail
        r = self.create('students', phone='0300 1234567')
        self.assertRedirects(r, reverse('adminpanel:students'))
        u = User.objects.get(email='sana@x.com')
        self.assertEqual((u.profile.role, u.profile.phone, u.profile.must_change_password), ('student', '0300 1234567', False))
        self.assertEqual(len(mail.outbox), 1)
        self.assertNotIn('Given-Pass-4821', mail.outbox[0].body)
        self.assertIn('Your student account is ready', mail.outbox[0].subject)
        self.client.logout()
        r = self.client.post(reverse('accounts:login'), {'username': 'sana@x.com', 'password': 'Given-Pass-4821'})
        self.assertRedirects(r, reverse('student:dashboard'), target_status_code=200)         # straight to the dashboard
        self.assertEqual(self.client.get(reverse('student:courses')).status_code, 200)         # nothing forces a password change

    def test_the_same_for_a_teacher(self):
        self.create('teachers', title='Accounts trainer')
        u = User.objects.get(email='sana@x.com')
        self.assertEqual((u.profile.role, u.profile.title, u.profile.must_change_password), ('teacher', 'Accounts trainer', False))
        self.client.logout()
        self.client.login(username='sana@x.com', password='Given-Pass-4821')
        self.assertEqual(self.client.get(reverse('teacher:dashboard')).status_code, 200)

    def test_the_administrator_can_still_ask_for_a_new_password_with_one_tick(self):
        self.create('students', ask_to_change='on')
        u = User.objects.get(email='sana@x.com')
        self.assertTrue(u.profile.must_change_password)
        self.client.logout()
        self.client.login(username='sana@x.com', password='Given-Pass-4821')
        self.assertRedirects(self.client.get(reverse('student:dashboard')), reverse('accounts:settings'))

    def test_weak_password_duplicate_email_and_bad_phone_are_refused(self):
        self.assertEqual(self.create('students', password='12345678').status_code, 200)
        self.assertEqual(self.create('students', phone='abc').status_code, 200)
        self.assertFalse(User.objects.filter(email='sana@x.com').exists())
        self.create('students')
        r = self.create('students')                                   # same e-mail again
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'already exists')
        r = self.create('teachers')                                   # the e-mail is taken by the student
        self.assertContains(r, 'already exists')
        self.assertEqual(User.objects.filter(email='sana@x.com').count(), 1)

    def test_only_an_administrator_can_add_accounts(self):
        self.client.logout()
        s = mk('s@x.com')
        self.client.force_login(s)
        before = User.objects.count()
        self.client.post(reverse('adminpanel:students'), {'first_name': 'Evil', 'last_name': 'X', 'email': 'evil@x.com', 'password': 'Given-Pass-4821'})
        self.assertEqual(User.objects.count(), before)
        self.client.logout()
        self.client.post(reverse('adminpanel:teachers'), {'first_name': 'Evil', 'last_name': 'X', 'email': 'evil@x.com', 'password': 'Given-Pass-4821'})
        self.assertEqual(User.objects.count(), before)

    def test_students_page_shows_the_add_form_and_the_list(self):
        mk('listed@x.com')
        html = self.client.get(reverse('adminpanel:students')).content.decode()
        self.assertIn('Add a student', html)
        self.assertIn('Create student account', html)
        self.assertIn('name="first_name"', html)                       # the inputs are really drawn (not just the heading)
        self.assertIn('name="password"', html)
        self.assertIn('name="ask_to_change"', html)
        self.assertIn('listed@x.com', html)

    def test_old_forced_marks_are_cleared_by_the_migration(self):
        import importlib
        mig = importlib.import_module('core.migrations.0015_no_forced_password_change')
        t = mk('old@x.com', 'teacher')
        t.profile.must_change_password = True
        t.profile.save()
        mig.clear_forced_changes(apps, None)
        t.profile.refresh_from_db()
        self.assertFalse(t.profile.must_change_password)


class SecondLookFixes(TestCase):
    def test_phone_numbers_need_seven_to_fifteen_digits(self):
        from core.forms import phone_validator
        for ok in ('0300 1234567', '+92 300 1234567', '(042) 111-222-333', '+1 (555) 010-9999'):
            phone_validator(ok)
        for bad in ('(((((((', '+---------', '123456', '+9' + '1' * 15, '0300 123 abc 45'):
            with self.assertRaises(ValidationError):
                phone_validator(bad)

    def test_the_go_back_button_does_not_use_a_javascript_link(self):
        from django.test import Client
        html = Client(enforce_csrf_checks=True).post(reverse('accounts:login'), {}).content.decode()
        self.assertIn('data-back', html)
        self.assertNotIn('javascript:', html)

    def test_a_classroom_behind_one_address_can_register_together(self):
        cache.clear()
        with override_settings(THROTTLE_ENABLED=True):
            codes = [self.client.post(reverse('accounts:register'), {}, REMOTE_ADDR='10.7.7.7').status_code for _ in range(60)]
        self.assertNotIn(429, codes)
        cache.clear()

    @override_settings(THROTTLE_ENABLED=True, THROTTLE_MULTIPLIER=3)
    def test_the_multiplier_raises_every_per_address_limit(self):
        cache.clear()
        codes = [self.client.post(reverse('website:contact'), {'name': 'a', 'email': 'a@b.com', 'message': 'hi'}).status_code for _ in range(19)]
        self.assertNotIn(429, codes[:18])                 # 6 x 3 = 18 a hour
        self.assertEqual(codes[18], 429)
        cache.clear()

    def test_a_wrong_host_name_gets_a_plain_400_not_a_crash(self):
        from django.test import Client
        with override_settings(ALLOWED_HOSTS=['example.com'], DEBUG=False):
            r = Client(raise_request_exception=False).get('/', HTTP_HOST='evil.com')
        self.assertEqual(r.status_code, 400)


# ================================================================== three sign-ins: Student / Teacher / Admin
class ThreeSignIns(TestCase):
    PW = 'Passw0rd!x'

    def setUp(self):
        cache.clear()
        self.student = mk('stu@x.com')
        self.teacher = mk('tea@x.com', 'teacher')
        self.admin = User.objects.create_superuser('adm@x.com', 'adm@x.com', self.PW)
        self.profile_admin = mk('padm@x.com', 'admin')            # an administrator by role (not a Django superuser)

    def sign_in(self, who, role, **extra):
        return self.client.post(reverse('accounts:login'), dict({'username': who.email, 'password': self.PW, 'role': role}, **extra))

    def test_each_person_signs_in_at_their_own_sign_in_and_lands_on_their_dashboard(self):
        for who, role, target in ((self.student, 'student', 'student:dashboard'), (self.teacher, 'teacher', 'teacher:dashboard'),
                                  (self.admin, 'admin', 'adminpanel:overview'), (self.profile_admin, 'admin', 'adminpanel:overview')):
            self.client.logout()
            self.assertRedirects(self.sign_in(who, role), reverse(target), fetch_redirect_response=False)

    def test_the_wrong_sign_in_is_refused_and_says_which_sign_in_to_use(self):
        wrong = [(self.teacher, 'student', 'Teacher'), (self.teacher, 'admin', 'Teacher'), (self.student, 'teacher', 'Student'),
                 (self.student, 'admin', 'Student'), (self.admin, 'teacher', 'Admin'), (self.admin, 'student', 'Admin')]
        for who, role, actual in wrong:
            self.client.logout()
            r = self.sign_in(who, role)
            self.assertEqual(r.status_code, 200, (who.email, role))
            self.assertNotIn('_auth_user_id', self.client.session)                      # NOT signed in
            self.assertIn(f'{actual} account', r.context['error'])                       # the person is told what to do
            self.assertEqual(r.context['error_tab'], reverse('accounts:login') + f'?as={actual.lower()}')
            self.assertContains(r, f'Go to the {actual} sign-in')

    def test_a_wrong_password_reveals_nothing_about_the_account(self):
        for who in (self.student, self.teacher, self.admin):
            r = self.client.post(reverse('accounts:login'), {'username': who.email, 'password': 'wrong-password', 'role': 'student'})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.context['error_tab'], '')
            for word in ('Teacher account', 'Admin account', 'Student account', 'deactivated'):
                self.assertNotIn(word, r.context['error'])
        r = self.client.post(reverse('accounts:login'), {'username': 'nobody@x.com', 'password': 'wrong-password', 'role': 'student'})
        self.assertIn('Incorrect email/username or password', r.context['error'])

    def test_a_deactivated_account_is_told_so_only_with_the_right_password(self):
        self.student.is_active = False
        self.student.save()
        r = self.sign_in(self.student, 'student')
        self.assertIn('deactivated', r.context['error'])
        r = self.client.post(reverse('accounts:login'), {'username': self.student.email, 'password': 'wrong-password', 'role': 'student'})
        self.assertNotIn('deactivated', r.context['error'])

    def test_user_name_and_e_mail_both_work_for_every_role_at_the_matching_sign_in(self):
        admin = User.objects.create_superuser('hafiz', 'hafiz@gmail.com', self.PW)
        for ident in ('hafiz', 'HAFIZ', '  hafiz  ', 'hafiz@gmail.com', 'Hafiz@Gmail.com'):
            self.client.logout()
            r = self.client.post(reverse('accounts:login'), {'username': ident, 'password': self.PW, 'role': 'admin'})
            self.assertRedirects(r, reverse('adminpanel:overview'), fetch_redirect_response=False, msg_prefix=repr(ident))
        self.assertTrue(admin.pk)

    def test_the_browser_remembers_the_last_sign_in_that_worked(self):
        self.assertIn('name="role" value="student"', self.client.get(reverse('accounts:login')).content.decode())
        from django.test import Client
        r = self.sign_in(self.teacher, 'teacher')
        self.assertEqual(r.cookies['jelly_signin'].value, 'teacher')
        later = Client()                                              # the same browser on another day (not signed in, cookie kept)
        later.cookies['jelly_signin'] = 'teacher'
        self.assertIn('name="role" value="teacher"', later.get(reverse('accounts:login')).content.decode())
        self.assertIn('name="role" value="admin"', later.get(reverse('accounts:login') + '?as=admin').content.decode())     # an explicit choice wins
        later.cookies['jelly_signin'] = 'superuser'                                                                          # a forged value is ignored
        self.assertIn('name="role" value="student"', later.get(reverse('accounts:login')).content.decode())

    def test_wrong_sign_in_counts_as_a_failed_attempt(self):
        with override_settings(THROTTLE_ENABLED=True):
            for _ in range(8):
                self.sign_in(self.student, 'admin')
            r = self.sign_in(self.student, 'student')            # 8 failures for this address and account: locked
        self.assertEqual(r.status_code, 429)

    def test_missing_or_invented_role_means_the_student_sign_in(self):
        self.assertRedirects(self.client.post(reverse('accounts:login'), {'username': 'stu@x.com', 'password': self.PW}), reverse('student:dashboard'),
                             fetch_redirect_response=False)
        self.client.logout()
        r = self.client.post(reverse('accounts:login'), {'username': 'tea@x.com', 'password': self.PW, 'role': 'superuser'})
        self.assertEqual(r.status_code, 200)

    def test_the_page_shows_three_sign_ins_and_the_right_one_is_selected(self):
        for role, title in (('student', 'Student sign in'), ('teacher', 'Teacher sign in'), ('admin', 'Admin sign in')):
            html = self.client.get(reverse('accounts:login') + f'?as={role}').content.decode()
            self.assertIn(title, html)
            self.assertEqual(html.count('class="active" aria-current="page"'), 1)
            self.assertIn(f'name="role" value="{role}"', html)
            for other in ('student', 'teacher', 'admin'):
                self.assertIn(f'?as={other}', html)

    def test_a_protected_page_opens_the_matching_sign_in(self):
        for path, role in (('/admin-panel/', 'admin'), ('/teacher/courses/', 'teacher'), ('/student/billing/', 'student')):
            r = self.client.get(path, follow=True)
            self.assertIn(f'name="role" value="{role}"', r.content.decode(), path)

    def test_header_and_mobile_menu_offer_the_three_sign_ins(self):
        html = self.client.get(reverse('website:home')).content.decode()
        for label in ('Student sign in', 'Teacher sign in', 'Admin sign in'):
            self.assertGreaterEqual(html.count(label), 2)         # header drop-down and phone menu

    def test_teacher_sign_in_links_to_the_teacher_application(self):
        html = self.client.get(reverse('accounts:login') + '?as=teacher').content.decode()
        self.assertIn('register/?type=teacher', html)
        reg = self.client.get(reverse('accounts:register') + '?type=teacher').content.decode()
        self.assertRegex(reg, r'value="teacher"[^>]*checked|checked[^>]*value="teacher"')

    def test_next_page_is_kept_when_switching_sign_in_and_foreign_addresses_are_dropped(self):
        html = self.client.get(reverse('accounts:login') + '?as=teacher&next=/teacher/live/').content.decode()
        self.assertIn('?as=admin&amp;next=/teacher/live/', html)
        html = self.client.get(reverse('accounts:login') + '?as=teacher&next=https://evil.example/').content.decode()
        self.assertNotIn('evil.example', html)


class CheckLoginCommand(TestCase):
    def run_cmd(self, *args):
        out = StringIO()
        call_command('check_login', *args, stdout=out)
        return out.getvalue()

    def test_it_explains_an_account_and_the_sign_in_to_use(self):
        User.objects.create_superuser('hafiz', 'hafiz@gmail.com', 'Passw0rd!x')
        for ident in ('hafiz', 'hafiz@gmail.com'):
            out = self.run_cmd(ident)
            self.assertIn('USE THE SIGN-IN      ADMIN', out)
            self.assertIn('?as=admin', out)
        self.assertIn('password             RIGHT', self.run_cmd('hafiz', '--password', 'Passw0rd!x'))
        self.assertIn('password             WRONG', self.run_cmd('hafiz', '--password', 'nope'))

    def test_it_says_when_the_account_is_deactivated_or_missing(self):
        u = mk('gone@x.com')
        u.is_active = False
        u.save()
        self.assertIn('NO: deactivated', self.run_cmd('gone@x.com'))
        with self.assertRaises(CommandError):
            self.run_cmd('nobody@x.com')

    def test_it_can_set_a_new_strong_password_only(self):
        u = mk('someone@x.com', 'teacher')
        with self.assertRaises(CommandError):
            self.run_cmd('someone@x.com', '--set-password', '12345678')
        self.run_cmd('someone@x.com', '--set-password', 'A-new-Strong-pass-91')
        u.refresh_from_db()
        self.assertTrue(u.check_password('A-new-Strong-pass-91'))
