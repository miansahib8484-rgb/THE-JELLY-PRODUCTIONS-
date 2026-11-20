"""Regression tests for the issues found in the v4 review. Each test fails on the unfixed code."""
from datetime import timedelta
from decimal import Decimal

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (Batch, Certificate, ContactSubmission, Course, Enrollment, LiveClass, Notification, Payment, PromoCode, Review)
from django.contrib.auth import get_user_model

from .tests import BaseCase, make_user

User = get_user_model()


class MoneyAndAccessBugs(BaseCase):
    def _paid(self, amount=50000):
        return Payment.objects.create(enrollment=self.enr, amount=amount, status='paid')

    # bug 1
    def test_teacher_earnings_not_multiplied_by_reviews(self):
        from teacher import stats
        self._paid(50000)
        for i, u in enumerate([self.student, self.other, make_user('r3@t.com', 'student')]):
            Review.objects.create(course=self.course, user=u, rating=5, comment=f'c{i}')
        row = next(r for r in stats.course_performance(self.teacher) if r['course'] == self.course)
        self.assertEqual(row['earned'], 50000)
        self.assertEqual(row['n_reviews'], 3)
        self.assertEqual(stats.kpis(self.teacher)['earnings']['value'], 50000)

    # bug 3
    def test_admin_enrollment_kpi_total_and_month_use_same_filter(self):
        from adminpanel import stats
        Enrollment.objects.create(user=self.other, course=self.course2, status='pending_payment')
        k = stats.kpis()['enrollments']
        self.assertLessEqual(k['month'], k['value'])
        self.assertEqual(k['value'], Enrollment.objects.filter(status__in=['active', 'completed']).count())

    # bug 4
    def test_cannot_reject_an_approved_payment(self):
        self.login(self.admin)
        p = self._paid()
        self.client.post(reverse('adminpanel:payment_review', args=[p.pk]), {'action': 'reject', 'note': 'x'})
        p.refresh_from_db()
        self.assertEqual(p.status, 'paid')

    def test_cannot_approve_twice_or_approve_rejected_without_resubmission(self):
        self.login(self.admin)
        p = Payment.objects.create(enrollment=self.enr, amount=1, status='failed')
        self.client.post(reverse('adminpanel:payment_review', args=[p.pk]), {'action': 'approve'})
        p.refresh_from_db()
        self.assertEqual(p.status, 'failed')

    # bug 5
    def test_cancelled_enrollment_cannot_be_repaid_or_reapproved(self):
        e = Enrollment.objects.create(user=self.other, course=self.course2, status='cancelled')
        pay = Payment.objects.create(enrollment=e, amount=1000, status='pending', transaction_reference='T')
        self.login(self.other)
        r = self.client.get(reverse('student:pay', args=[e.pk]))
        self.assertEqual(r.status_code, 302)
        self.client.logout()
        from core import services
        with self.assertRaises(services.PaymentStateError):
            services.approve_payment(pay, self.admin)
        e.refresh_from_db()
        self.assertEqual(e.status, 'cancelled')


class SettingsDefaults(BaseCase):
    # bug 2
    def test_debug_defaults_off_and_secret_must_be_set(self):
        import os, sys
        env = {k: v for k, v in os.environ.items() if k not in ('DJANGO_DEBUG', 'DJANGO_SECRET_KEY')}
        import subprocess
        code = "import os; os.environ.pop('DJANGO_DEBUG',None); os.environ.pop('DJANGO_SECRET_KEY',None)\nimport config.settings"
        r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=env, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.assertNotEqual(r.returncode, 0, 'production without a secret key must refuse to start')
        env['DJANGO_DEBUG'] = '1'
        r = subprocess.run([sys.executable, '-c', "import config.settings as s; print(s.DEBUG)"], capture_output=True, text=True, env=env,
                           cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.assertEqual(r.stdout.strip(), 'True')


class CounterAndRefundBugs(BaseCase):
    # bug 27: public "enrolled" counts paid learners only
    def test_public_enrolled_count_ignores_unpaid_reservations(self):
        self.login(self.other)
        Enrollment.objects.create(user=self.other, course=self.course, batch=self.batch, status='pending_payment')
        self.course.refresh_from_db()
        self.assertEqual(self.course.students_enrolled_count, 1)       # only the fixture's active student
        Enrollment.objects.filter(user=self.other).update(status='active')
        from core import services
        services.recount(self.course.pk, self.batch.pk)
        self.course.refresh_from_db()
        self.assertEqual(self.course.students_enrolled_count, 2)

    def test_rejecting_a_payment_does_not_change_public_count(self):
        e = Enrollment.objects.create(user=self.other, course=self.course, batch=self.batch, status='pending_payment')
        p = Payment.objects.create(enrollment=e, amount=1, status='pending', transaction_reference='X')
        from core import services
        services.reject_payment(p, self.admin, 'blurry')
        self.course.refresh_from_db()
        self.assertEqual(self.course.students_enrolled_count, 1)

    # bug 16: a batch becomes full automatically and opens again when a seat frees up
    def test_batch_auto_full_and_reopen(self):
        Batch.objects.filter(pk=self.batch.pk).update(seats_total=2)
        Enrollment.objects.create(user=self.other, course=self.course, batch=self.batch, status='pending_payment')
        self.batch.refresh_from_db()
        self.assertEqual((self.batch.seats_left, self.batch.status), (0, 'full'))
        Enrollment.objects.filter(user=self.other).update(status='cancelled')
        from core import services
        services.recount(self.course.pk, self.batch.pk)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, 'open')

    # bug 13: refunds are recorded properly
    def test_refund_marks_payment_and_enrollment_and_frees_seat(self):
        p = Payment.objects.create(enrollment=self.enr, amount=50000, status='paid')
        self.login(self.admin)
        self.client.post(reverse('adminpanel:enrollment_refund', args=[self.enr.pk]), {'note': 'Requested'})
        p.refresh_from_db(); self.enr.refresh_from_db(); self.batch.refresh_from_db()
        self.assertEqual((p.status, self.enr.status, self.batch.seats_filled), ('refunded', 'refunded', 0))
        self.assertTrue(Notification.objects.filter(user=self.student, title='Payment refunded').exists())

    def test_cancel_refuses_paid_enrollment(self):
        Payment.objects.create(enrollment=self.enr, amount=50000, status='paid')
        self.login(self.admin)
        self.client.post(reverse('adminpanel:enrollment_cancel', args=[self.enr.pk]))
        self.enr.refresh_from_db()
        self.assertEqual(self.enr.status, 'active')

    def test_cancel_marks_pending_payment_so_it_leaves_the_review_queue(self):
        e = Enrollment.objects.create(user=self.other, course=self.course2, status='pending_payment')
        p = Payment.objects.create(enrollment=e, amount=1, status='pending', transaction_reference='T')
        self.login(self.admin)
        self.client.post(reverse('adminpanel:enrollment_cancel', args=[e.pk]))
        p.refresh_from_db()
        self.assertEqual(p.status, 'failed')


class CsvInjection(BaseCase):
    def test_formula_cells_are_neutralised(self):
        from core.csvutil import safe_cell
        for evil in ['=HYPERLINK("http://x")', '+1+1', '-2+3', '@SUM(A1)', '\tcmd']:
            self.assertTrue(safe_cell(evil).startswith("'"))
        self.assertEqual(safe_cell(-5), -5)
        self.assertEqual(safe_cell(None), '')

    def test_exports_escape_user_controlled_text(self):
        make_user('=cmd|calc@t.com', 'student')
        u = make_user('evil@t.com', 'student')
        u.first_name = '=1+1'
        u.save()
        self.login(self.admin)
        body = self.client.get(reverse('adminpanel:export', args=['students'])).content.decode()
        self.assertIn("'=1+1", body)
        self.assertNotIn(',=1+1', body)


class BuilderEditing(BaseCase):
    def test_teacher_can_edit_batch_module_and_lecture(self):
        self.login(self.teacher)
        r = self.client.post(reverse('teacher:batch_edit', args=[self.batch.pk]), {
            'start_date': '2030-01-05', 'schedule_label': 'Sat 7pm', 'seats_total': 20, 'status': 'closed', 'meeting_link': ''})
        self.assertEqual(r.status_code, 302)
        self.batch.refresh_from_db()
        self.assertEqual((self.batch.seats_total, self.batch.status), (20, 'closed'))
        mod = self.lec1.module
        self.client.post(reverse('teacher:module_edit', args=[mod.pk]), {'title': 'Renamed', 'sort_order': 3})
        mod.refresh_from_db()
        self.assertEqual((mod.title, mod.sort_order), ('Renamed', 3))
        self.client.post(reverse('teacher:lecture_edit', args=[self.lec1.pk]), {'title': 'New name', 'type': 'video', 'duration_label': '5 min', 'video_url': '', 'sort_order': 1})
        self.lec1.refresh_from_db()
        self.assertEqual(self.lec1.title, 'New name')

    def test_batch_cannot_shrink_below_held_seats(self):
        self.login(self.teacher)
        Enrollment.objects.create(user=self.other, course=self.course, batch=self.batch, status='pending_payment')
        r = self.client.post(reverse('teacher:batch_edit', args=[self.batch.pk]), {
            'start_date': '2030-01-05', 'schedule_label': '', 'seats_total': 1, 'status': 'open', 'meeting_link': ''})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'already hold a seat')

    def test_teacher_cannot_edit_another_teachers_batch(self):
        self.login(self.teacher2)
        self.assertEqual(self.client.get(reverse('teacher:batch_edit', args=[self.batch.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('teacher:lecture_edit', args=[self.lec1.pk])).status_code, 404)


class TeacherApplicationAndAccounts(BaseCase):
    # bug 17
    def test_admin_approves_teacher_application_from_inbox(self):
        applicant = make_user('wannateach@t.com', 'student')
        sub = ContactSubmission.objects.create(name='W', email='wannateach@t.com', topic='Teacher application', message='please')
        self.login(self.admin)
        r = self.client.get(reverse('adminpanel:contact'))
        self.assertContains(r, 'Approve as teacher')
        self.client.post(reverse('adminpanel:teacher_application_approve', args=[sub.pk]))
        applicant.refresh_from_db(); sub.refresh_from_db()
        self.assertEqual((applicant.profile.role, sub.status), ('teacher', 'replied'))
        self.assertTrue(Notification.objects.filter(user=applicant, title='You are now a teacher').exists())

    def test_admin_can_change_role_but_not_demote_teacher_with_courses(self):
        stu = make_user('promo@t.com', 'student')
        self.login(self.admin)
        self.client.post(reverse('adminpanel:user_set_role', args=[stu.pk]), {'role': 'teacher'})
        stu.refresh_from_db()
        self.assertEqual(stu.profile.role, 'teacher')
        self.client.post(reverse('adminpanel:user_set_role', args=[self.teacher.pk]), {'role': 'student'})
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.profile.role, 'teacher')            # still teaches a course
        self.assertEqual(self.client.post(reverse('adminpanel:user_set_role', args=[self.admin.pk]), {'role': 'student'}).status_code, 404)

    def test_admin_sends_password_reset_link(self):
        from django.core import mail
        self.login(self.admin)
        self.client.post(reverse('adminpanel:user_password_reset', args=[self.student.pk]))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.student.email, mail.outbox[0].to)

    # bug 14
    def test_temporary_password_must_be_strong_and_is_emailed_without_the_password(self):
        from django.core import mail
        self.login(self.admin)
        r = self.client.post(reverse('adminpanel:teachers'), {'first_name': 'A', 'last_name': 'B', 'email': 'weak@t.com', 'password': '12345678'})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(email='weak@t.com').exists())
        # the welcome e-mail is queued with transaction.on_commit (sent only once the request succeeded); TestCase never commits, so run the callbacks
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('adminpanel:teachers'), {'first_name': 'A', 'last_name': 'B', 'email': 'ok@t.com', 'password': 'Strong-Temp-4821'})
        self.assertFalse(User.objects.get(email='ok@t.com').profile.must_change_password)      # the given password is used as it is
        self.assertEqual(len(mail.outbox), 1)
        self.assertNotIn('Strong-Temp-4821', mail.outbox[0].body)


class LostFormData(BaseCase):
    # bug 18
    def test_register_saves_interested_course(self):
        r = self.client.post(reverse('accounts:register'), {
            'account_type': 'student', 'first_name': 'Z', 'last_name': 'Y', 'email': 'zy@t.com', 'phone': '0300 1234567', 'date_of_birth': '2000-01-01',
            'city': 'Lahore', 'country': 'Pakistan', 'occupation': 'Student', 'interested_course': self.course.pk, 'experience_level': 'beginner',
            'password1': 'Sturdy-pass-9911', 'password2': 'Sturdy-pass-9911', 'terms': 'on'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(User.objects.get(email='zy@t.com').profile.interested_course, self.course)

    def test_enroll_saves_notes_and_admin_sees_them(self):
        self.login(self.other)
        self.client.post(reverse('website:enroll'), {'course': self.course.pk, 'batch': self.batch.pk, 'phone': '0300 1234567', 'city': 'X',
                         'notes': 'Please call me after 6pm', 'gateway': 'easypaisa', 'transaction_reference': 'T1', 'terms': 'on'})
        self.assertEqual(Enrollment.objects.get(user=self.other).notes, 'Please call me after 6pm')
        self.login(self.admin)
        self.assertContains(self.client.get(reverse('adminpanel:user_detail', args=[self.other.pk])), 'Please call me after 6pm')


class PublicAbuseProtection(BaseCase):
    # bugs 21, 22
    @override_settings(THROTTLE_ENABLED=True)
    def test_login_contact_newsletter_and_promo_are_rate_limited(self):
        from django.core.cache import cache
        cache.clear()
        for _ in range(12):
            self.client.post(reverse('accounts:login'), {'username': 'nobody@t.com', 'password': 'x'})
        self.assertEqual(self.client.post(reverse('accounts:login'), {'username': 'nobody@t.com', 'password': 'x'}).status_code, 429)
        cache.clear()
        for _ in range(6):
            self.client.post(reverse('website:newsletter_subscribe'), {'email': 'a@b.com'})
        self.assertEqual(self.client.post(reverse('website:newsletter_subscribe'), {'email': 'a@b.com'}).status_code, 429)
        cache.clear()
        for _ in range(30):
            self.client.get(reverse('website:promo_check'), {'code': 'x'})
        r = self.client.get(reverse('website:promo_check'), {'code': 'x'})
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.json()['valid'] if 'valid' in r.json() else False, False)
        cache.clear()

    def test_honeypot_blocks_bots_silently(self):
        self.client.post(reverse('website:contact'), {'name': 'Bot', 'email': 'bot@t.com', 'topic': 'x', 'message': 'buy now', 'website': 'http://spam'})
        self.assertFalse(ContactSubmission.objects.filter(email='bot@t.com').exists())
        self.client.post(reverse('website:newsletter_subscribe'), {'email': 'bot@t.com', 'website': 'x'})
        from .models import NewsletterSubscriber
        self.assertFalse(NewsletterSubscriber.objects.filter(email='bot@t.com').exists())
        self.client.post(reverse('accounts:register'), {'email': 'botreg@t.com', 'website': 'x'})
        self.assertFalse(User.objects.filter(email='botreg@t.com').exists())

    def test_promo_usage_limit(self):
        promo = PromoCode.objects.get(code='JELLY10')
        promo.max_uses = 1
        promo.save()
        self.assertIsNotNone(PromoCode.lookup('JELLY10'))
        e = Enrollment.objects.create(user=self.other, course=self.course, batch=self.batch, status='pending_payment')
        p = Payment.objects.create(enrollment=e, amount=45000, status='pending', promo_code='JELLY10', transaction_reference='X')
        from core import services
        services.approve_payment(p, self.admin)
        promo.refresh_from_db()
        self.assertEqual(promo.uses_count, 1)
        self.assertIsNone(PromoCode.lookup('JELLY10'))            # limit reached

    def test_unsubscribe_flow_and_resubscribe(self):
        from .models import NewsletterSubscriber
        sub = NewsletterSubscriber.objects.create(email='n@t.com')
        self.client.post(reverse('website:newsletter_unsubscribe', args=[sub.token]))
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'unsubscribed')
        self.client.post(reverse('website:newsletter_subscribe'), {'email': 'n@t.com'})
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'active')


class LiveClassRules(BaseCase):
    def _post(self, **kw):
        data = {'course': self.course.pk, 'batch': '', 'title': 'Q&A', 'starts_at': (timezone.localtime() + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M'),
                'duration_minutes': 60, 'platform': 'meet', 'meeting_link': 'https://meet.google.com/abc'}
        data.update(kw)
        self.login(self.teacher)
        return self.client.post(reverse('teacher:live_create'), data)

    # bug 23
    def test_past_time_and_bad_duration_are_rejected(self):
        past = (timezone.localtime() - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        self.assertContains(self._post(starts_at=past), 'in the past')
        self.assertContains(self._post(duration_minutes=0), 'between 10 minutes')
        self.assertEqual(LiveClass.objects.filter(title='Q&A').count(), 0)

    def test_overlapping_classes_of_the_same_teacher_are_rejected(self):
        self.assertEqual(self._post().status_code, 302)
        self.assertContains(self._post(title='Clash'), 'overlaps')

    # bug 15
    def test_students_are_told_when_a_class_time_changes(self):
        self._post()
        c = LiveClass.objects.get(title='Q&A')
        new = (timezone.localtime() + timedelta(days=4)).strftime('%Y-%m-%dT%H:%M')
        self.client.post(reverse('teacher:live_edit', args=[c.pk]), {
            'course': self.course.pk, 'batch': '', 'title': 'Q&A', 'starts_at': new, 'duration_minutes': 60, 'platform': 'meet',
            'meeting_link': 'https://meet.google.com/abc'})
        self.assertTrue(Notification.objects.filter(user=self.student, title='Class updated: Q&A').exists())

    def test_duplicate_certificate_is_refused(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.login(self.teacher)
        data = {'student': self.student.pk, 'course': self.course.pk, 'issued_on': '2030-01-01'}
        with override_settings(PRIVATE_MEDIA_ROOT=__import__('tempfile').mkdtemp()):
            self.client.post(reverse('teacher:certificates'), {**data, 'file': SimpleUploadedFile('c.pdf', b'%PDF-1.4 x')})
            r = self.client.post(reverse('teacher:certificates'), {**data, 'file': SimpleUploadedFile('d.pdf', b'%PDF-1.4 y')})
        self.assertContains(r, 'already issued')
        self.assertEqual(Certificate.objects.filter(student=self.student, course=self.course).count(), 1)


class FilesAndDeletion(BaseCase):
    # bug 20
    def test_files_are_removed_from_disk_when_record_is_deleted_or_replaced(self):
        import os, tempfile
        from django.core.files.uploadedfile import SimpleUploadedFile
        with override_settings(PRIVATE_MEDIA_ROOT=tempfile.mkdtemp()):
            with self.captureOnCommitCallbacks(execute=True):
                cert = Certificate.objects.create(student=self.student, course=self.course, file=SimpleUploadedFile('a.pdf', b'%PDF-1.4 aaa'))
            first = cert.file.path
            self.assertTrue(os.path.exists(first))
            with self.captureOnCommitCallbacks(execute=True):
                cert.file = SimpleUploadedFile('b.pdf', b'%PDF-1.4 bbb')
                cert.save()
            self.assertFalse(os.path.exists(first))
            second = cert.file.path
            self.assertTrue(os.path.exists(second))
            with self.captureOnCommitCallbacks(execute=True):
                cert.delete()
            self.assertFalse(os.path.exists(second))

    # bug 24
    def test_course_with_any_enrollment_or_certificate_cannot_be_deleted(self):
        c = Course.objects.create(title='Old', slug='old', teacher=self.teacher, price=1, original_price=1, duration_weeks=1, hours_per_week=1)
        e = Enrollment.objects.create(user=self.other, course=c, status='cancelled')
        self.login(self.teacher)
        self.client.post(reverse('teacher:course_delete', args=[c.slug]))
        self.assertTrue(Course.objects.filter(pk=c.pk).exists())
        e.delete()
        self.client.post(reverse('teacher:course_delete', args=[c.slug]))
        self.assertFalse(Course.objects.filter(pk=c.pk).exists())


class SeoLegalAndSafety(BaseCase):
    # bugs 29, 30
    def test_head_has_description_canonical_og_and_favicon(self):
        body = self.client.get(reverse('website:course_detail', args=[self.course.slug])).content.decode()
        for tag in ['name="description"', 'rel="canonical"', 'property="og:title"', 'rel="icon"', 'application/ld+json', '"@type": "Course"']:
            self.assertIn(tag, body)
        self.assertIn('noindex', self.client.get(reverse('accounts:login')).content.decode())

    def test_sitemap_robots_and_custom_404(self):
        sm = self.client.get('/sitemap.xml')
        self.assertEqual(sm.status_code, 200)
        self.assertIn(self.course.slug, sm.content.decode())
        robots = self.client.get('/robots.txt').content.decode()
        self.assertIn('Disallow: /admin-panel/', robots)
        self.assertIn('Sitemap:', robots)
        r = self.client.get('/no-such-page/')
        self.assertContains(r, 'could not find that page', status_code=404)

    # bug 19
    def test_legal_pages_appear_only_when_written_and_are_linked(self):
        self.assertEqual(self.client.get('/terms/').status_code, 404)
        self.assertNotContains(self.client.get(reverse('accounts:register')), 'href="/terms/"')
        self.login(self.admin)
        self.client.post(reverse('adminpanel:legal_save'), {'legal-terms': '## Terms\n\nBe kind.', 'legal-privacy': '', 'legal-refund': 'Refunds in 7 days'})
        self.client.logout()
        self.assertContains(self.client.get('/terms/'), 'Be kind.')
        self.assertEqual(self.client.get('/privacy/').status_code, 404)
        self.assertContains(self.client.get('/refund-policy/'), '7 days')
        self.assertContains(self.client.get(reverse('accounts:register')), 'href="/terms/"')
        self.assertContains(self.client.get(reverse('website:home')), 'Terms of use')

    def test_certificate_verification_page(self):
        cert = Certificate.objects.create(student=self.student, course=self.course, file='certificates/x.pdf')
        r = self.client.get(reverse('website:verify_certificate', args=[cert.code]))
        self.assertContains(r, 'genuine')
        self.assertContains(r, self.course.title)
        self.assertContains(self.client.get(reverse('website:verify_certificate', args=['JP-NOPE0000'])), 'No certificate matches', status_code=404)

    # bug 31
    def test_blog_html_is_sanitised(self):
        from .models import BlogPost
        BlogPost.objects.create(title='XSS', body='Hi <script>alert(1)</script> <img src=x onerror=alert(2)> [ok](https://good.test) [bad](javascript:alert(3))')
        body = self.client.get(reverse('website:blog_detail', args=['xss'])).content.decode()
        self.assertNotIn('<script>alert', body)
        self.assertNotIn('onerror', body)
        self.assertNotIn('javascript:alert', body)
        self.assertIn('https://good.test', body)

    # bug 32
    def test_preview_video_must_be_a_youtube_id_or_link(self):
        from .portal_forms import clean_youtube_id
        from django import forms
        self.assertEqual(clean_youtube_id('https://www.youtube.com/watch?v=dQw4w9WgXcQ'), 'dQw4w9WgXcQ')
        self.assertEqual(clean_youtube_id('https://youtu.be/dQw4w9WgXcQ?t=5'), 'dQw4w9WgXcQ')
        self.assertEqual(clean_youtube_id('dQw4w9WgXcQ'), 'dQw4w9WgXcQ')
        for bad in ['"><script>', 'javascript:alert(1)', 'https://evil.test/x']:
            with self.assertRaises(forms.ValidationError):
                clean_youtube_id(bad)

    # bug 33
    def test_enroll_page_without_courses_redirects_politely(self):
        Course.objects.update(is_published=False)
        self.login(self.other)
        self.assertRedirects(self.client.get(reverse('website:enroll')), reverse('website:courses'))

    # bug 12 + 8 + 26
    def test_report_range_applies_to_progress_table_and_keeps_search_term(self):
        old = Enrollment.objects.create(user=self.other, course=self.course2, status='active')
        Enrollment.objects.filter(pk=old.pk).update(enrolled_at=timezone.now() - timedelta(days=200))
        self.login(self.admin)
        r = self.client.get(reverse('adminpanel:reports'), {'range': '30d', 'q': 'UI'})
        self.assertNotIn(old.pk, [e.pk for e in r.context['page']])
        self.assertContains(r, 'range=90d&amp;q=UI')


class QueryCounts(BaseCase):
    """Bugs 6 and 10: a dashboard must cost the same number of queries for 12 students as for 40 (lists are capped, so
    compare two sizes that are both above the cap)."""
    def _add_students(self, start, n):
        for i in range(start, start + n):
            u = make_user(f'bulk{i}@t.com', 'student')
            Enrollment.objects.create(user=u, course=self.course, batch=self.batch, status='active')

    def _count(self, user, name):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.login(user)
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        return len(ctx)

    def _flat(self, user, name, page=''):
        self._add_students(0, 12)
        small = self._count(user, name)
        self._add_students(12, 28)
        big = self._count(user, name)
        self.assertLessEqual(big, small + 2, f'{name}: {small} queries with 12 students, {big} with 40')

    def test_teacher_dashboard_query_count_is_flat(self):
        self._flat(self.teacher, 'teacher:dashboard')

    def test_teacher_students_page_query_count_is_flat(self):
        self._flat(self.teacher, 'teacher:students')

    def test_teacher_performance_query_count_is_flat(self):
        self._flat(self.teacher, 'teacher:performance')

    def test_admin_reports_query_count_is_flat(self):
        self._flat(self.admin, 'adminpanel:reports')

    def test_student_dashboard_does_not_grow_with_courses(self):
        def add(a, b):
            for i in range(a, b):
                c = Course.objects.create(title=f'C{i}', slug=f'c{i}', teacher=self.teacher, price=1, original_price=1, duration_weeks=1, hours_per_week=1)
                Enrollment.objects.create(user=self.student, course=c, status='active')
        add(0, 5)
        small = self._count(self.student, 'student:dashboard')
        add(5, 12)
        big = self._count(self.student, 'student:dashboard')
        self.assertLessEqual(big, small + 2, f'student dashboard: {small} -> {big}')
        small = self._count(self.student, 'student:courses')
        add(12, 20)
        self.assertLessEqual(self._count(self.student, 'student:courses'), small + 2)


class JsonLdCannotBeBrokenOut(BaseCase):
    """The Course JSON-LD sits inside a <script> tag: a title with </script> must not close it (stored XSS)."""
    def test_hostile_titles_are_escaped_in_json_ld(self):
        import json, re
        evil = '</script><script>alert(1)</script><img src=x onerror=alert(2)>'
        Course.objects.filter(pk=self.course.pk).update(title=evil, subtitle=evil)
        from .models import SiteSettings
        s = SiteSettings.get(); s.site_name = evil; s.save()
        html = self.client.get(reverse('website:course_detail', args=[self.course.slug])).content.decode()
        block = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1)
        self.assertNotIn('<', block)
        self.assertEqual(json.loads(block)['name'], evil)                    # still the exact text for search engines
        rest = html.replace(block, '')
        self.assertNotIn('<script>alert(1)', rest)                           # and nowhere else on the page either
        self.assertNotIn('<img src=x onerror', rest)


class AuditFixes(BaseCase):
    def test_review_form_with_garbage_course_id_does_not_crash(self):
        self.login(self.student)
        for bad in ['999999', 'abc', '', "' OR 1=1 --", '00000000-0000-0000-0000-000000000000']:
            r = self.client.post(reverse('student:reviews'), {'course': bad, 'rating': 5, 'comment': 'x'})
            self.assertEqual(r.status_code, 200, bad)
            self.assertContains(r, 'Choose one of your courses')

    @override_settings(THROTTLE_ENABLED=True)
    def test_login_throttle_counts_only_failures_so_a_classroom_can_sign_in(self):
        from django.core.cache import cache
        cache.clear()
        users = [make_user(f'class{i}@t.com', 'student') for i in range(15)]
        for u in users:                                                    # 15 successful logins from the same address
            c = type(self.client)()
            r = c.post(reverse('accounts:login'), {'username': u.email, 'password': 'pass12345-Xy'})
            self.assertEqual(r.status_code, 302, u.email)
        bad = type(self.client)()
        for _ in range(8):
            self.assertEqual(bad.post(reverse('accounts:login'), {'username': 'class0@t.com', 'password': 'wrong'}).status_code, 200)
        self.assertEqual(bad.post(reverse('accounts:login'), {'username': 'class0@t.com', 'password': 'wrong'}).status_code, 429)
        self.assertEqual(bad.post(reverse('accounts:login'), {'username': 'class0@t.com', 'password': 'pass12345-Xy'}).status_code, 429)   # locked for this account
        other = type(self.client)()
        self.assertEqual(other.post(reverse('accounts:login'), {'username': 'class1@t.com', 'password': 'pass12345-Xy'}).status_code, 302)  # others unaffected
        cache.clear()

    def test_uploads_are_checked_by_content_not_only_by_name(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .utils import validate_upload
        exts = ('.png', '.jpg', '.jpeg', '.pdf', '.webp')
        png = SimpleUploadedFile('ok.png', b'\x89PNG\r\n\x1a\n' + b'0' * 20)
        self.assertIsNone(validate_upload(png, 5, exts))
        self.assertEqual(png.read(8), b'\x89PNG\r\n\x1a\n')                      # file position restored for saving
        for name, data in [('evil.png', b'<html><script>alert(1)</script>'), ('evil.pdf', b'MZ\x90\x00'), ('fake.jpg', b'GIF89a...'),
                           ('fake.webp', b'RIFF0000WAVE')]:
            self.assertIn('does not look like', validate_upload(SimpleUploadedFile(name, data), 5, exts), name)
        self.assertIsNone(validate_upload(SimpleUploadedFile('real.webp', b'RIFF\x00\x00\x00\x00WEBPVP8 '), 5, exts))
        self.assertIsNone(validate_upload(SimpleUploadedFile('real.pdf', b'%PDF-1.7 ...'), 5, exts))
        self.assertIsNone(validate_upload(SimpleUploadedFile('real.jpg', b'\xff\xd8\xff\xe0' + b'0' * 20), 5, exts))

    def test_payment_proof_form_refuses_html_renamed_to_png(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        e = Enrollment.objects.create(user=self.other, course=self.course2, status='pending_payment')
        self.login(self.other)
        r = self.client.post(reverse('student:pay', args=[e.pk]), {'gateway': 'easypaisa', 'transaction_reference': '',
                             'proof_file': SimpleUploadedFile('proof.png', b'<script>alert(1)</script>')})
        self.assertContains(r, 'does not look like')

    def test_bulk_email_goes_out_once_per_student_over_one_connection(self):
        from django.core import mail
        from unittest import mock
        from .utils import notify
        users = [make_user(f'mail{i}@t.com', 'student') for i in range(12)]
        with mock.patch('core.emails.get_connection', wraps=__import__('django.core.mail', fromlist=['get_connection']).get_connection) as gc, \
                self.captureOnCommitCallbacks(execute=True):
            notify(users, 'Class moved', 'Now 8 pm', '/student/live/', email=True)
        self.assertEqual(len(mail.outbox), 12)
        self.assertEqual(gc.call_count, 1)
        self.assertIn('http://localhost:8000/student/live/', mail.outbox[0].body)

    def test_refund_and_cancel_emails_read_naturally(self):
        from django.core import mail
        Payment.objects.create(enrollment=self.enr, amount=1, status='paid')
        from . import services
        with self.captureOnCommitCallbacks(execute=True):
            services.refund_enrollment(self.enr, self.admin, '')
        self.assertIn('Your payment for UI Systems', mail.outbox[-1].body)
        self.assertNotIn(': \n', mail.outbox[-1].body)

    def test_time_labels_follow_the_configured_timezone(self):
        self.login(self.student)
        body = self.client.get(reverse('student:live')).content.decode()
        self.assertIn('Pakistan time (PKT)', body)       # rendered from {% now "T" %} for Asia/Karachi


class EnrollSeatRules(BaseCase):
    """Found during the audit: seat rules at the public enroll form."""
    def _post(self, user, batch, **extra):
        self.login(user)
        data = {'course': self.course.pk, 'batch': batch.pk if batch else '', 'phone': '0300 1234567', 'city': 'L', 'gateway': 'easypaisa',
                'transaction_reference': 'TX1', 'terms': 'on'}
        data.update(extra)
        return self.client.post(reverse('website:enroll'), data)

    def _second_batch(self):
        return Batch.objects.create(course=self.course, start_date=timezone.localdate() + timedelta(days=60), seats_total=5, status='open')

    def test_cancelled_student_cannot_retake_a_batch_that_filled_up(self):
        old = Enrollment.objects.create(user=self.other, course=self.course, batch=self.batch, status='cancelled')
        # The fixture's active student holds the only seat. A stale 'open' status (no signal ran) must not let the view overbook.
        Batch.objects.filter(pk=self.batch.pk).update(seats_total=1, seats_filled=1, status='open')
        self._post(self.other, self.batch)
        old.refresh_from_db()
        self.assertEqual(old.status, 'cancelled')
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.seats_filled, 1)

    def test_closed_or_full_batch_cannot_be_chosen_by_posting_its_id(self):
        Batch.objects.filter(pk=self.batch.pk).update(status='closed')
        r = self._post(self.other, self.batch)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Enrollment.objects.filter(user=self.other, course=self.course).exists())

    def test_one_seat_per_student_per_course_choosing_another_batch_moves_the_reservation(self):
        b2 = self._second_batch()
        self._post(self.other, self.batch)
        self._post(self.other, b2)
        live = Enrollment.objects.filter(user=self.other, course=self.course).exclude(status__in=['cancelled', 'refunded'])
        self.assertEqual(live.count(), 1)
        self.assertEqual(live.get().batch, b2)
        self.batch.refresh_from_db(); b2.refresh_from_db()
        self.assertEqual((self.batch.seats_filled, b2.seats_filled), (1, 1))     # old seat released (fixture student only), new seat held
        self.assertEqual(Payment.objects.filter(enrollment__user=self.other, status='pending').count(), 1)   # one payment to review, not two

    def test_switching_back_to_an_earlier_batch_reuses_the_old_record(self):
        b2 = self._second_batch()
        self._post(self.other, self.batch)
        self._post(self.other, b2)
        self._post(self.other, self.batch)
        live = Enrollment.objects.filter(user=self.other, course=self.course).exclude(status__in=['cancelled', 'refunded'])
        self.assertEqual((live.count(), live.get().batch), (1, self.batch))

    def test_already_active_student_is_not_given_a_second_seat(self):
        b2 = self._second_batch()
        self._post(self.student, b2)                                          # fixture student is active in the first batch
        self.assertEqual(Enrollment.objects.filter(user=self.student, course=self.course).exclude(status__in=['cancelled', 'refunded']).count(), 1)


class EveryDashboardBranchRenders(BaseCase):
    """Triggers every 'needs your attention' item on every dashboard so a broken {% url %} in a rarely used branch cannot hide."""
    def test_teacher_dashboard_with_every_attention_item(self):
        from .models import CurriculumLecture, LectureProgress
        now = timezone.now()
        LiveClass.objects.create(course=self.course, title='Past one', starts_at=now - timedelta(days=2), duration_minutes=60, meeting_link='https://x.test/a')
        LiveClass.objects.create(course=self.course, title='No link yet', starts_at=now + timedelta(days=2), duration_minutes=60, meeting_link='')
        Course.objects.create(title='Empty live course', slug='empty-live', teacher=self.teacher, price=1, original_price=1,
                                      duration_weeks=1, hours_per_week=1, is_published=True)
        for lec in CurriculumLecture.objects.filter(module__course=self.course):
            LectureProgress.objects.create(user=self.student, lecture=lec, completed=True, completed_at=now)
        self.login(self.teacher)
        r = self.client.get(reverse('teacher:dashboard'))
        self.assertEqual(r.status_code, 200)
        icons = {a['icon'] for a in r.context['attention']}
        self.assertTrue({'upload', 'link_off', 'construction', 'event', 'workspace_premium'} <= icons, icons)
        for a in r.context['attention']:
            self.assertContains(r, a['text'][:25].replace('"', '&quot;'))

    def test_student_dashboard_with_every_attention_item(self):
        from .models import CurriculumLecture, LectureProgress
        now = timezone.now()
        for lec in CurriculumLecture.objects.filter(module__course=self.course):
            LectureProgress.objects.create(user=self.student, lecture=lec, completed=True, completed_at=now)     # finished -> certificate pending + review prompt
        e1 = Enrollment.objects.create(user=self.student, course=self.course2, status='pending_payment')
        Payment.objects.create(enrollment=e1, amount=1000, status='failed', admin_note='blurry')                 # rejected
        c3 = Course.objects.create(title='Third', slug='third', teacher=self.teacher, price=1, original_price=1, duration_weeks=1, hours_per_week=1, is_published=True)
        e2 = Enrollment.objects.create(user=self.student, course=c3, status='pending_payment')
        Payment.objects.create(enrollment=e2, amount=1000, status='pending')                                     # nothing sent yet
        c4 = Course.objects.create(title='Fourth', slug='fourth', teacher=self.teacher, price=1, original_price=1, duration_weeks=1, hours_per_week=1, is_published=True)
        e3 = Enrollment.objects.create(user=self.student, course=c4, status='pending_payment')
        Payment.objects.create(enrollment=e3, amount=1000, status='pending', transaction_reference='TX9')        # under review
        LiveClass.objects.create(course=self.course, title='On air', starts_at=now - timedelta(minutes=5), duration_minutes=90, meeting_link='https://x.test/live')
        self.login(self.student)
        r = self.client.get(reverse('student:dashboard'))
        self.assertEqual(r.status_code, 200)
        icons = {a['icon'] for a in r.context['attention']}
        self.assertTrue({'error', 'receipt_long', 'hourglass_top', 'workspace_premium', 'star', 'videocam'} <= icons, icons)

    def test_admin_dashboard_with_every_activity_type(self):
        from .models import Review
        Payment.objects.create(enrollment=self.enr, amount=5, status='failed')
        Review.objects.create(course=self.course, user=self.student, rating=4, comment='ok')
        ContactSubmission.objects.create(name='A', email='a@b.com', message='m')
        Certificate.objects.create(student=self.student, course=self.course, file='certificates/x.pdf')
        self.login(self.admin)
        r = self.client.get(reverse('adminpanel:overview'))
        self.assertEqual(r.status_code, 200)
        self.assertTrue({'person_add', 'payments', 'mail', 'workspace_premium', 'star'} <= {a['icon'] for a in r.context['activity']})

    def test_payment_amount_with_odd_percentages_is_stored_cleanly(self):
        Course.objects.filter(pk=self.course.pk).update(price=Decimal('49999'))
        PromoCode.objects.create(code='SEVEN', percent_off=7)
        self.login(self.other)
        self.client.post(reverse('website:enroll'), {'course': self.course.pk, 'batch': self.batch.pk, 'phone': '0300 1234567', 'city': 'L', 'gateway': 'easypaisa',
                         'transaction_reference': 'T', 'promo_code': 'SEVEN', 'terms': 'on'})
        p = Payment.objects.get(enrollment__user=self.other)
        self.assertEqual(p.amount, Decimal('46499'))                       # 46499.07 rounds to whole rupees
        self.login(self.admin)
        self.assertContains(self.client.get(reverse('adminpanel:payments')), '46,499')

    def test_half_rupee_rounds_up_like_the_enroll_page_javascript(self):
        Course.objects.filter(pk=self.course.pk).update(price=Decimal('45'))
        PromoCode.objects.create(code='TEN', percent_off=10)               # 45 * 0.9 = 40.5 -> JavaScript Math.round gives 41
        self.login(self.other)
        self.client.post(reverse('website:enroll'), {'course': self.course.pk, 'batch': self.batch.pk, 'phone': '0300 1234567', 'city': 'L', 'gateway': 'easypaisa',
                         'transaction_reference': 'T', 'promo_code': 'TEN', 'terms': 'on'})
        self.assertEqual(Payment.objects.get(enrollment__user=self.other).amount, Decimal('41'))


class PublicAndAdminListsStayFlat(BaseCase):
    """Found by the audit: the blog list made one query per post, admin lists one per row."""
    def _count(self, url, user=None):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        if user:
            self.login(user)
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.client.get(url).status_code, 200)
        return len(ctx)

    def test_blog_and_admin_blog_list_do_not_query_per_post(self):
        from .models import BlogPost
        for i in range(12):
            BlogPost.objects.create(title=f'P{i}', body='b')
        small_pub, small_adm = self._count(reverse('website:blog')), self._count(reverse('adminpanel:blog'), self.admin)
        for i in range(12, 40):
            BlogPost.objects.create(title=f'P{i}', body='b')
        self.client.logout()
        self.assertLessEqual(self._count(reverse('website:blog')), small_pub + 1)
        self.assertLessEqual(self._count(reverse('adminpanel:blog'), self.admin), small_adm + 1)

    def test_admin_overview_payments_and_live_lists_stay_flat(self):
        from .models import Review
        def add(a, b):
            for i in range(a, b):
                u = make_user(f'flat{i}@t.com', 'student')
                e = Enrollment.objects.create(user=u, course=self.course, batch=self.batch, status='pending_payment')
                Payment.objects.create(enrollment=e, amount=1, status='pending', transaction_reference=f'T{i}')
                Review.objects.create(course=self.course, user=u, rating=4, comment='c')
                LiveClass.objects.create(course=self.course, title=f'L{i}', starts_at=timezone.now() + timedelta(days=i + 1), duration_minutes=60, meeting_link='https://x.test')
        add(0, 12)
        names = ['adminpanel:overview', 'adminpanel:payments', 'adminpanel:live']
        small = [self._count(reverse(n), self.admin) for n in names]
        add(12, 40)
        big = [self._count(reverse(n), self.admin) for n in names]
        for n, a, b in zip(names, small, big):
            self.assertLessEqual(b, a + 2, f'{n}: {a} -> {b}')


class ImageUploadFlows(BaseCase):
    """Real PNGs through every image upload, then served back; replaced files disappear; fake images are refused."""
    def _png(self, name='pic.png', size=(40, 30), color='navy'):
        import io
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        buf = io.BytesIO()
        Image.new('RGB', size, color).save(buf, 'PNG')
        return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')

    def test_admin_content_images_are_saved_served_and_replaced_cleanly(self):
        import os
        from .models import GalleryImage, SiteSettings
        self.login(self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.post(reverse('adminpanel:content'), {'site_name': 'Pix', 'hero_image': self._png('hero1.png')})
        self.assertEqual(r.status_code, 302)
        site = SiteSettings.get()
        first = site.hero_image.path
        self.assertTrue(os.path.exists(first))
        self.assertEqual(self.client.get(site.hero_image.url).status_code, 200)             # public file is served
        self.assertContains(self.client.get(reverse('website:home')), site.hero_image.url)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('adminpanel:content'), {'site_name': 'Pix', 'hero_image': self._png('hero2.png')})
        self.assertFalse(os.path.exists(first))                                                # old file removed from disk
        self.client.post(reverse('adminpanel:content_item_add', args=['gallery']), {'gallery-image': self._png('g.png'), 'gallery-caption': 'Hi', 'gallery-sort_order': 0})
        g = GalleryImage.objects.get()
        self.assertContains(self.client.get(reverse('website:about')), g.image.url)
        with self.captureOnCommitCallbacks(execute=True):
            gp = g.image.path
            self.client.post(reverse('adminpanel:content_item_delete', args=['gallery', g.pk]))
        self.assertFalse(os.path.exists(gp))

    def test_fake_and_oversized_images_are_refused(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .models import SiteSettings
        self.login(self.admin)
        r = self.client.post(reverse('adminpanel:content'), {'site_name': 'Fake', 'hero_image': SimpleUploadedFile('evil.png', b'<script>alert(1)</script>')})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(SiteSettings.get().hero_image)
        r = self.client.post(reverse('adminpanel:content'), {'site_name': 'Fake', 'hero_image': SimpleUploadedFile('page.html', b'<html></html>')})
        self.assertEqual(r.status_code, 200)

    def test_blog_cover_course_thumbnail_and_avatar_uploads(self):
        from .models import BlogPost
        self.login(self.admin)
        self.client.post(reverse('adminpanel:blog_new'), {'title': 'With cover', 'category': 'news', 'body': 'x', 'cover': self._png('cover.png'),
                         'is_published': 'on', 'published_at': timezone.localtime().strftime('%Y-%m-%dT%H:%M')})
        post = BlogPost.objects.get(title='With cover')
        self.assertTrue(post.cover)
        self.assertEqual(self.client.get(post.cover.url).status_code, 200)
        self.login(self.teacher)
        r = self.client.post(reverse('teacher:course_edit', args=[self.course.slug]), {
            'title': self.course.title, 'subtitle': 's', 'category': 'Design', 'level': 'beginner', 'duration_weeks': 4, 'hours_per_week': 3,
            'price': '50000', 'original_price': '70000', 'description': 'd', 'thumbnail': self._png('thumb.png'), 'preview_video_id': '', 'is_published': 'on'})
        self.assertEqual(r.status_code, 302)
        self.course.refresh_from_db()
        self.assertTrue(self.course.thumbnail)
        self.assertEqual(self.client.get(self.course.image_url).status_code, 200)
        self.assertContains(self.client.get(reverse('website:courses')), self.course.image_url)


class LinksAndRecordings(BaseCase):
    def test_javascript_and_other_dangerous_links_are_refused(self):
        self.login(self.teacher)
        starts = (timezone.localtime() + timedelta(days=9)).strftime('%Y-%m-%dT%H:%M')
        for evil in ['javascript:alert(1)', 'data:text/html,<script>alert(1)</script>', 'vbscript:x', 'file:///etc/passwd']:
            r = self.client.post(reverse('teacher:live_create'), {'course': self.course.pk, 'batch': '', 'title': 'L', 'starts_at': starts,
                                 'duration_minutes': 60, 'platform': 'meet', 'meeting_link': evil})
            self.assertEqual(r.status_code, 200, evil)
        self.assertFalse(LiveClass.objects.filter(title='L').exists())
        r = self.client.post(reverse('teacher:lecture_edit', args=[self.lec1.pk]), {'title': 'x', 'type': 'video', 'duration_label': '1', 'video_url': 'javascript:alert(1)', 'sort_order': 0})
        self.assertEqual(r.status_code, 200)

    def test_teacher_uploads_a_recording_students_watch_it_and_deleting_removes_the_file(self):
        import os
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .models import ClassRecording
        lc = LiveClass.objects.create(course=self.course, title='Done', starts_at=timezone.now() - timedelta(days=1), duration_minutes=60, meeting_link='https://x.test')
        self.login(self.teacher)
        video = SimpleUploadedFile('lesson.mp4', b'\x00\x00\x00\x18ftypmp42' + b'0' * 4000, content_type='video/mp4')
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.post(reverse('teacher:recording', args=[lc.pk]), {'rec_type': 'file', 'video_file': video, 'video_url': ''})
        self.assertEqual(r.status_code, 302)
        rec = ClassRecording.objects.get(live_class=lc)
        path = rec.video_file.path
        self.assertTrue(os.path.exists(path))
        self.assertTrue(Notification.objects.filter(user=self.student, title__icontains='recording').exists())
        self.login(self.student)
        self.assertEqual(self.client.get(reverse('student:watch', args=[lc.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('files:recording', args=[rec.pk]), HTTP_RANGE='bytes=0-9').status_code, 206)
        self.login(self.other)
        self.assertEqual(self.client.get(reverse('files:recording', args=[rec.pk])).status_code, 404)           # not enrolled
        self.login(self.teacher)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('teacher:recording_delete', args=[lc.pk]))
        self.assertFalse(os.path.exists(path))


class DatabaseIntegrity(BaseCase):
    """Django-expert audit: the database itself refuses bad data and protects financial records."""
    def test_payments_enrollments_and_certificates_survive_user_or_course_deletion(self):
        from django.db.models.deletion import ProtectedError
        Payment.objects.create(enrollment=self.enr, amount=100, status='paid')
        Certificate.objects.create(student=self.student, course=self.course, file='certificates/x.pdf')
        for obj in (self.student, self.course, self.enr):
            with self.assertRaises(ProtectedError):
                obj.delete()
        self.assertEqual((Payment.objects.count(), Enrollment.objects.filter(pk=self.enr.pk).count(), Certificate.objects.count()), (1, 1, 1))

    def test_user_without_records_can_still_be_deleted(self):
        u = make_user('gone@t.com', 'student')
        Notification.objects.create(user=u, title='x')
        u.delete()
        self.assertFalse(User.objects.filter(email='gone@t.com').exists())

    def test_database_rejects_values_the_forms_would_never_send(self):
        from django.db import IntegrityError, transaction
        bad = {
            'rating 7': lambda: Review.objects.create(course=self.course, user=self.student, rating=7, comment=''),
            'rating 0': lambda: Review.objects.create(course=self.course, user=self.student, rating=0, comment=''),
            'promo 0%': lambda: PromoCode.objects.create(code='Z0', percent_off=0),
            'promo 101%': lambda: PromoCode.objects.create(code='Z1', percent_off=101),
            'negative price': lambda: Course.objects.filter(pk=self.course.pk).update(price=-1),
            'zero seats': lambda: Batch.objects.create(course=self.course, start_date=timezone.localdate(), seats_total=0, status='open'),
            'negative payment': lambda: Payment.objects.create(enrollment=self.enr, amount=-5),
            'zero-minute class': lambda: LiveClass.objects.create(course=self.course, title='z', starts_at=timezone.now(), duration_minutes=0),
        }
        for label, make in bad.items():
            with self.assertRaises(IntegrityError, msg=label), transaction.atomic():
                make()

    def test_hot_query_indexes_exist(self):
        from django.db import connection
        wanted = {'core_enrollment': {'enr_course_status_idx', 'enr_user_status_idx', 'enr_batch_status_idx'},
                  'core_payment': {'pay_status_created_idx'}, 'core_notification': {'notif_user_read_idx', 'notif_user_created_idx'},
                  'core_liveclass': {'live_course_start_idx'}, 'core_contactsubmission': {'contact_status_idx'}, 'core_blogpost': {'blog_pub_idx'},
                  'core_review': {'review_course_idx'}, 'core_batch': {'batch_course_status_idx'}}
        with connection.cursor() as cur:
            for table, names in wanted.items():
                have = set(connection.introspection.get_constraints(cur, table))
                self.assertTrue(names <= have, f'{table} is missing {names - have}')

    def test_links_typed_without_a_scheme_become_https(self):
        self.login(self.teacher)
        starts = (timezone.localtime() + timedelta(days=11)).strftime('%Y-%m-%dT%H:%M')
        r = self.client.post(reverse('teacher:live_create'), {'course': self.course.pk, 'batch': '', 'title': 'Scheme', 'starts_at': starts,
                             'duration_minutes': 60, 'platform': 'meet', 'meeting_link': 'meet.google.com/abc-defg-hij'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(LiveClass.objects.get(title='Scheme').meeting_link, 'https://meet.google.com/abc-defg-hij')


class EmailsWaitForTheCommit(BaseCase):
    """No SMTP call while database rows are locked, and no e-mail for a change that was rolled back."""
    def test_email_is_not_sent_until_the_transaction_commits(self):
        from django.core import mail
        from django.db import transaction
        from .utils import notify
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            with transaction.atomic():
                notify(self.student, 'Hello', 'Body', '/student/', email=True)
                self.assertEqual(len(mail.outbox), 0)              # still inside the transaction: nothing sent yet
        self.assertEqual(len(callbacks), 1)
        callbacks[0]()                                              # the commit happens
        self.assertEqual(len(mail.outbox), 1)

    def test_rolled_back_change_sends_no_email(self):
        from django.core import mail
        from django.db import transaction
        from . import services
        pay = Payment.objects.create(enrollment=Enrollment.objects.create(user=self.other, course=self.course2, status='pending_payment'),
                                     amount=1, status='pending', transaction_reference='X')
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    services.approve_payment(pay, self.admin)
                    raise RuntimeError('something later in the request fails')
            except RuntimeError:
                pass
        self.assertEqual(len(mail.outbox), 0)
        pay.refresh_from_db()
        self.assertEqual(pay.status, 'pending')                     # database change rolled back too


class FrameworkHardening(BaseCase):
    def test_private_files_are_owner_only_on_disk(self):
        import os, stat
        from django.core.files.uploadedfile import SimpleUploadedFile
        cert = Certificate.objects.create(student=self.student, course=self.course, file=SimpleUploadedFile('c.pdf', b'%PDF-1.4 x'))
        self.assertEqual(stat.S_IMODE(os.stat(cert.file.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(cert.file.path)).st_mode), 0o700)

    def test_a_request_that_fails_half_way_leaves_nothing_behind(self):
        """ATOMIC_REQUESTS: issuing a certificate creates the record, completes the enrollment and notifies; if the last step
        explodes, the first two must be rolled back."""
        from unittest import mock
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.login(self.teacher)
        self.client.raise_request_exception = False
        with mock.patch('core.portal.notify', side_effect=RuntimeError('boom')):
            r = self.client.post(reverse('teacher:certificates'), {'student': self.student.pk, 'course': self.course.pk, 'issued_on': '2030-01-01',
                                 'file': SimpleUploadedFile('c.pdf', b'%PDF-1.4 x')})
        self.assertEqual(r.status_code, 500)
        self.assertEqual(Certificate.objects.count(), 0)
        self.enr.refresh_from_db()
        self.assertEqual(self.enr.status, 'active')

    def test_safe_framework_settings(self):
        from django.conf import settings
        self.assertTrue(settings.DATABASES['default']['ATOMIC_REQUESTS'])
        self.assertEqual(settings.EMAIL_TIMEOUT, 10)
        self.assertLessEqual(settings.PASSWORD_RESET_TIMEOUT, 3 * 3600)
        self.assertFalse(settings.DEBUG)


class CoverageGapsServices(BaseCase):
    """Found by measuring coverage: business rules that no test exercised."""
    def test_admin_next_parameter_cannot_redirect_off_site(self):
        e = Enrollment.objects.create(user=self.other, course=self.course2, status='pending_payment')
        self.login(self.admin)
        for evil in ['https://evil.test/', '//evil.test', 'javascript:alert(1)', 'http://localhost.evil.test/']:
            r = self.client.post(reverse('adminpanel:enrollment_mark_paid', args=[e.pk]), {'next': evil})
            self.assertEqual(r.status_code, 302)
            self.assertNotIn('evil', r['Location'], evil)
        e2 = Enrollment.objects.create(user=make_user('np@t.com'), course=self.course2, status='pending_payment')
        r = self.client.post(reverse('adminpanel:enrollment_mark_paid', args=[e2.pk]), {'next': reverse('adminpanel:payments')})
        self.assertEqual(r['Location'], reverse('adminpanel:payments'))

    def test_mark_paid_service_rules(self):
        from . import services
        e = Enrollment.objects.create(user=self.other, course=self.course2, status='pending_payment')
        services.mark_enrollment_paid(e, self.admin)                     # creates the payment record itself when there is none
        e.refresh_from_db()
        self.assertEqual((e.status, e.payment.status, e.payment.reviewed_by), ('active', 'paid', self.admin))
        self.assertTrue(Notification.objects.filter(user=self.other, title='Payment approved').exists())
        services.mark_enrollment_paid(e, self.admin)                     # second time: harmless no-op
        self.assertEqual(Notification.objects.filter(user=self.other, title='Payment approved').count(), 1)
        e.status = 'cancelled'; e.save()
        with self.assertRaises(services.PaymentStateError):
            services.mark_enrollment_paid(e, self.admin)

    def test_mark_paid_after_a_rejection_reopens_and_approves(self):
        from . import services
        e = Enrollment.objects.create(user=self.other, course=self.course2, status='pending_payment')
        Payment.objects.create(enrollment=e, amount=1, status='failed', admin_note='blurry')
        services.mark_enrollment_paid(e, self.admin)
        e.refresh_from_db()
        self.assertEqual((e.status, e.payment.status, e.payment.admin_note), ('active', 'paid', ''))

    def test_cancel_and_refund_edge_cases(self):
        from . import services
        e = Enrollment.objects.create(user=self.other, course=self.course2, status='cancelled')
        services.cancel_enrollment(e)                                    # already cancelled: no-op, no error
        with self.assertRaises(services.PaymentStateError):
            services.refund_enrollment(e, self.admin)                    # nothing to refund
        e2 = Enrollment.objects.create(user=make_user('rf@t.com'), course=self.course2, status='pending_payment')
        with self.assertRaises(services.PaymentStateError):
            services.refund_enrollment(e2, self.admin)                   # unpaid enrollments are cancelled, not refunded

    def test_recount_ignores_a_batch_that_no_longer_exists(self):
        from . import services
        services.recount(self.course.pk, 999999)                         # must not raise

    def test_email_failure_never_breaks_the_request(self):
        from unittest import mock
        from .emails import send_many
        with mock.patch('core.emails.get_connection') as gc:
            gc.return_value.send_messages.side_effect = OSError('smtp down')
            self.assertEqual(send_many([('a@b.com', 'Hi', 'Body')]), 0)
        self.assertEqual(send_many([]), 0)
        self.assertEqual(send_many([('', 'Hi', 'Body')]), 0)
        from .emails import absolute
        self.assertEqual(absolute('https://x.test/a'), 'https://x.test/a')
        self.assertTrue(absolute('').startswith('http'))

    def test_client_ip_uses_the_last_forwarded_address_only_behind_a_trusted_proxy(self):
        from django.test import RequestFactory
        from .throttle import client_ip
        req = RequestFactory().get('/', HTTP_X_FORWARDED_FOR='6.6.6.6, 203.0.113.9', REMOTE_ADDR='10.0.0.1')
        self.assertEqual(client_ip(req), '10.0.0.1')                     # header ignored unless a proxy is trusted (anti-spoofing)
        with override_settings(TRUST_X_FORWARDED_FOR=True):
            self.assertEqual(client_ip(req), '203.0.113.9')              # the address the trusted proxy appended, not the client-supplied one

    def test_throttle_survives_a_cache_key_expiring_between_calls(self):
        from unittest import mock
        from django.core.cache import cache
        from .throttle import hit
        with mock.patch.object(cache, 'incr', side_effect=ValueError):
            self.assertTrue(hit('race', 'x', 5, 60))


class CoverageGapsStudent(BaseCase):
    def test_student_submits_payment_proof_and_admins_are_told(self):
        e = Enrollment.objects.create(user=self.other, course=self.course2, status='pending_payment')
        Payment.objects.create(enrollment=e, amount=1000, status='failed', admin_note='blurry')
        self.login(self.other)
        r = self.client.post(reverse('student:pay', args=[e.pk]), {'gateway': 'easypaisa', 'transaction_reference': 'TX-777', 'sender_account_number': '0300'})
        self.assertEqual(r.status_code, 302)
        p = Payment.objects.get(enrollment=e)
        self.assertEqual((p.status, p.admin_note, p.transaction_reference), ('pending', '', 'TX-777'))
        self.assertTrue(Notification.objects.filter(user=self.admin, title='Payment proof submitted').exists())
        self.assertEqual(self.client.get(reverse('student:pay', args=[e.pk])).status_code, 200)

    def test_pay_page_for_an_already_approved_payment_just_informs(self):
        Payment.objects.create(enrollment=self.enr, amount=1, status='paid')
        self.login(self.student)
        r = self.client.get(reverse('student:pay', args=[self.enr.pk]), follow=True)
        self.assertRedirects(r, reverse('student:billing'))
        self.assertContains(r, 'already approved')

    def test_student_writes_a_review_for_a_course_they_study(self):
        from .models import Review
        self.login(self.student)
        self.assertEqual(self.client.get(reverse('student:reviews')).status_code, 200)
        r = self.client.post(reverse('student:reviews'), {'course': self.course.pk, 'rating': 4, 'comment': 'Solid course'})
        self.assertRedirects(r, reverse('student:reviews'))
        self.assertTrue(Review.objects.filter(course=self.course, user=self.student, rating=4).exists())
        self.assertContains(self.client.get(reverse('website:course_detail', args=[self.course.slug])), 'Solid course')
        r = self.client.post(reverse('student:reviews'), {'course': self.course2.pk, 'rating': 5, 'comment': 'not enrolled there'})
        self.assertContains(r, 'Choose one of your courses')

    def test_progress_toggle_requires_an_active_enrollment(self):
        self.login(self.other)
        r = self.client.post(reverse('student:toggle_lecture', args=[self.lec1.pk]))
        self.assertRedirects(r, reverse('student:billing'))
        from .models import LectureProgress
        self.assertFalse(LectureProgress.objects.filter(user=self.other).exists())

    def test_my_courses_tabs_and_search(self):
        Enrollment.objects.create(user=self.student, course=self.course2, status='completed')
        self.login(self.student)
        titles = lambda **q: [e.course.title for e in self.client.get(reverse('student:courses'), q).context['page']]
        self.assertEqual(set(titles()), {self.course.title, self.course2.title})
        self.assertEqual(titles(status='progress'), [self.course.title])
        self.assertEqual(titles(status='done'), [self.course2.title])
        self.assertEqual(titles(q='ui sys'), [self.course.title])


class CoverageGapsAdminAndTeacher(BaseCase):
    def test_admin_lists_filter_and_search(self):
        self.login(self.admin)
        self.assertContains(self.client.get(reverse('adminpanel:students'), {'status': 'active', 'q': 'student@t.com'}), 'student@t.com')
        self.assertNotContains(self.client.get(reverse('adminpanel:students'), {'status': 'inactive'}), 'student@t.com')
        self.assertEqual(self.client.get(reverse('adminpanel:enrollments'), {'status': 'active', 'q': 'UI Sys'}).status_code, 200)
        self.assertEqual(self.client.get(reverse('adminpanel:enrollments'), {'status': 'bogus'}).status_code, 200)

    def test_admin_can_demote_a_teacher_without_courses_and_reset_needs_an_email(self):
        t = make_user('freelance@t.com', 'teacher')
        self.login(self.admin)
        self.client.post(reverse('adminpanel:user_set_role', args=[t.pk]), {'role': 'student'})
        t.refresh_from_db()
        self.assertEqual(t.profile.role, 'student')
        t.email = ''; t.save()
        r = self.client.post(reverse('adminpanel:user_password_reset', args=[t.pk]), follow=True)
        self.assertContains(r, 'no valid e-mail')

    def test_admin_settings_crud_paths(self):
        from .models import NewsletterSubscriber, PaymentAccountInfo, SiteStat
        self.login(self.admin)
        sub = NewsletterSubscriber.objects.create(email='s@t.com')
        self.client.post(reverse('adminpanel:newsletter_delete', args=[sub.pk]))
        self.assertFalse(NewsletterSubscriber.objects.filter(pk=sub.pk).exists())
        self.client.post(reverse('adminpanel:stat_add'), {'stat-value': '12 yrs', 'stat-label': 'teaching', 'stat-sort_order': 1})
        st = SiteStat.objects.get()
        self.client.post(reverse('adminpanel:stat_save', args=[st.pk]), {'stat-value': '13 yrs', 'stat-label': 'teaching', 'stat-sort_order': 1})
        st.refresh_from_db()
        self.assertEqual(st.value, '13 yrs')
        self.client.post(reverse('adminpanel:stat_delete', args=[st.pk]))
        self.assertFalse(SiteStat.objects.exists())
        self.client.post(reverse('adminpanel:settings'), {'save_payment': '1', 'pay-easypaisa_number': '0300-1', 'pay-easypaisa_account_name': 'Acme',
                         'pay-instructions': 'Send proof'})
        self.assertEqual(PaymentAccountInfo.get().easypaisa_number, '0300-1')

    def test_teacher_curriculum_batch_class_and_certificate_deletes(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .models import CurriculumLecture, CurriculumModule
        self.login(self.teacher)
        mod = self.lec1.module
        self.client.post(reverse('teacher:lecture_delete', args=[self.lec1.pk]))
        self.assertFalse(CurriculumLecture.objects.filter(pk=self.lec1.pk).exists())
        self.client.post(reverse('teacher:module_delete', args=[mod.pk]))
        self.assertFalse(CurriculumModule.objects.filter(pk=mod.pk).exists())
        r = self.client.post(reverse('teacher:batch_delete', args=[self.batch.pk]), follow=True)       # has students: refused
        self.assertContains(r, 'has students')
        spare = Batch.objects.create(course=self.course, start_date=timezone.localdate() + timedelta(days=90), seats_total=3, status='open')
        self.client.post(reverse('teacher:batch_delete', args=[spare.pk]))
        self.assertFalse(Batch.objects.filter(pk=spare.pk).exists())
        lc = LiveClass.objects.create(course=self.course, title='Bye', starts_at=timezone.now() + timedelta(days=5), duration_minutes=60, meeting_link='https://x.test')
        self.client.post(reverse('teacher:live_delete', args=[lc.pk]))
        self.assertFalse(LiveClass.objects.filter(pk=lc.pk).exists())
        cert = Certificate.objects.create(student=self.student, course=self.course, file=SimpleUploadedFile('c.pdf', b'%PDF-1.4 x'))
        self.assertContains(self.client.get(reverse('teacher:certificates'), {'q': 'UI Sys'}), self.student.first_name)
        self.client.post(reverse('teacher:certificate_delete', args=[cert.pk]))
        self.assertFalse(Certificate.objects.exists())

    def test_teacher_lists_filters_and_batch_announcements(self):
        from .models import Announcement, Review
        Review.objects.create(course=self.course, user=self.student, rating=5, comment='c')
        self.login(self.teacher)
        for params in ({'status': 'active'}, {'status': 'completed'}, {'status': 'pending'}, {'course': self.course.slug}, {'q': 'student'}):
            self.assertEqual(self.client.get(reverse('teacher:students'), params).status_code, 200, params)
        self.assertEqual(self.client.get(reverse('teacher:reviews'), {'course': self.course.slug, 'rating': '5'}).context['total'], 1)
        # an announcement to one batch reaches only that batch
        b2 = Batch.objects.create(course=self.course, start_date=timezone.localdate() + timedelta(days=40), seats_total=5, status='open')
        Enrollment.objects.create(user=self.other, course=self.course, batch=b2, status='active')
        self.client.post(reverse('teacher:announcements'), {'course': self.course.pk, 'batch': b2.pk, 'title': 'Batch only', 'body': 'Hi'})
        self.assertTrue(Notification.objects.filter(user=self.other, title='Batch only').exists())
        self.assertFalse(Notification.objects.filter(user=self.student, title='Batch only').exists())
        a = Announcement.objects.get(title='Batch only')
        self.client.post(reverse('teacher:announcement_delete', args=[a.pk]))
        self.assertFalse(Announcement.objects.filter(pk=a.pk).exists())
        self.assertTrue(Notification.objects.filter(user=self.other, title='Batch only').exists())     # students keep what they received

    def test_live_class_tabs_search_and_past_ordering(self):
        now = timezone.now()
        LiveClass.objects.create(course=self.course, title='Old A', starts_at=now - timedelta(days=9), duration_minutes=60, meeting_link='https://x.test')
        LiveClass.objects.create(course=self.course, title='Old B', starts_at=now - timedelta(days=3), duration_minutes=60, meeting_link='https://x.test')
        LiveClass.objects.create(course=self.course, title='Soon', starts_at=now + timedelta(days=2), duration_minutes=60, meeting_link='https://x.test')
        self.login(self.teacher)
        past = [c.title for c in self.client.get(reverse('teacher:live'), {'status': 'past'}).context['page']]
        self.assertEqual(past, ['Old B', 'Old A'])                         # newest first
        self.assertEqual([c.title for c in self.client.get(reverse('teacher:live'), {'status': 'upcoming'}).context['page']], ['Soon'])
        self.assertEqual([c.title for c in self.client.get(reverse('teacher:live'), {'q': 'old a', 'status': 'past'}).context['page']], ['Old A'])


class AccountSecurityDetails(BaseCase):
    def test_unknown_account_costs_a_password_hash_like_a_wrong_password(self):
        """Login must not reveal which e-mail addresses exist through response time."""
        from unittest import mock
        from django.contrib.auth import authenticate
        with mock.patch.object(User, 'set_password') as dummy_hash:
            self.assertIsNone(authenticate(username='nobody@nowhere.test', password='whatever-123'))
        dummy_hash.assert_called_once()                      # the hasher ran although the account does not exist
        with mock.patch.object(User, 'set_password') as dummy_hash:
            self.assertIsNone(authenticate(username=self.student.email, password='wrong'))
            self.assertEqual(dummy_hash.call_count, 0)       # a real account is checked with check_password instead
        self.assertEqual(authenticate(username=self.student.email, password='pass12345-Xy'), self.student)
        self.assertEqual(authenticate(username=self.student.email.upper(), password='pass12345-Xy'), self.student)   # case-insensitive

    def test_inactive_account_cannot_sign_in_and_a_wrong_password_reveals_nothing(self):
        from django.contrib.auth import authenticate
        User.objects.filter(pk=self.student.pk).update(is_active=False)
        self.assertIsNone(authenticate(username=self.student.email, password='pass12345-Xy'))        # never signed in
        # only a person who knows the RIGHT password is told that the account is deactivated
        r = self.client.post(reverse('accounts:login'), {'username': self.student.email, 'password': 'pass12345-Xy'})
        self.assertContains(r, 'deactivated')
        r = self.client.post(reverse('accounts:login'), {'username': self.student.email, 'password': 'not-the-password'})
        self.assertContains(r, 'Incorrect email/username or password')
        self.assertNotContains(r, 'deactivated')

    def test_roles_for_superuser_and_for_a_user_without_a_profile(self):
        from .models import Profile
        from .permissions import get_role
        su = User.objects.create_superuser('root@t.com', 'root@t.com', 'pw12345678-Zz')
        self.assertEqual(get_role(su), 'admin')
        self.assertEqual(su.profile.role, 'admin')
        Profile.objects.filter(user=self.other).delete()
        self.other = User.objects.get(pk=self.other.pk)
        self.assertEqual(get_role(self.other), 'student')
        from django.contrib.auth.models import AnonymousUser
        self.assertIsNone(get_role(AnonymousUser()))

    def test_logged_in_users_skip_login_and_register_pages(self):
        self.login(self.student)
        self.assertRedirects(self.client.get(reverse('accounts:login')), reverse('student:dashboard'))
        self.assertRedirects(self.client.get(reverse('accounts:register')), reverse('student:dashboard'))

    def test_notifications_can_all_be_marked_read(self):
        Notification.objects.create(user=self.student, title='a'); Notification.objects.create(user=self.student, title='b')
        self.login(self.student)
        self.client.post(reverse('accounts:notifications'))
        self.assertEqual(Notification.objects.filter(user=self.student, is_read=False).count(), 0)


class FileServingEdgeCases(BaseCase):
    def _recording(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .models import ClassRecording
        lc = LiveClass.objects.create(course=self.course, title='R', starts_at=timezone.now() - timedelta(days=1), duration_minutes=60, meeting_link='https://x.test')
        return ClassRecording.objects.create(live_class=lc, video_file=SimpleUploadedFile('v.mp4', b'0123456789' * 100))

    def test_a_record_whose_file_vanished_from_disk_is_a_clean_404(self):
        import os
        rec = self._recording()
        os.remove(rec.video_file.path)
        self.login(self.admin)
        self.assertEqual(self.client.get(reverse('files:recording', args=[rec.pk])).status_code, 404)

    def test_malformed_and_unsatisfiable_ranges(self):
        rec = self._recording()
        self.login(self.admin)
        url = reverse('files:recording', args=[rec.pk])
        for bad in ['bytes=abc', 'bytes=-', 'bytes=x-y', 'bytes=5000-6000']:
            self.assertEqual(self.client.get(url, HTTP_RANGE=bad).status_code, 416, bad)
        r = self.client.get(url, HTTP_RANGE='bytes=-10')
        self.assertEqual((r.status_code, r['Content-Range']), (206, 'bytes 990-999/1000'))
        r = self.client.get(url, HTTP_RANGE='bytes=0-99999')                     # end past the file is trimmed
        self.assertEqual((r.status_code, r['Content-Range']), (206, 'bytes 0-999/1000'))


class ContentSecurityPolicyAndVendoring(BaseCase):
    def _policy(self, response):
        return response['Content-Security-Policy']

    def test_every_html_page_carries_a_nonce_policy_without_unsafe_script(self):
        self.login(self.admin)
        for name in ['website:home', 'adminpanel:overview', 'adminpanel:reports', 'accounts:login']:
            r = self.client.get(reverse(name), follow=True)
            pol = self._policy(r)
            self.assertRegex(pol, r"script-src 'self' 'nonce-[\w-]{16,}'")
            self.assertNotIn("'unsafe-inline'", pol.split('style-src')[0])          # scripts: never; (only style-src allows inline)
            self.assertNotIn("'unsafe-eval'", pol)
            for rule in ("object-src 'none'", "base-uri 'self'", "form-action 'self'", "frame-ancestors 'none'", "connect-src 'self'"):
                self.assertIn(rule, pol, name)

    def test_inline_scripts_use_this_requests_nonce_and_it_changes_every_time(self):
        import re
        self.login(self.admin)
        nonces = set()
        for _ in range(3):
            r = self.client.get(reverse('adminpanel:overview'))
            header_nonce = re.search(r"'nonce-([^']+)'", self._policy(r)).group(1)
            html = r.content.decode()
            inline = re.findall(r'<script nonce="([^"]*)">', html)
            self.assertTrue(inline)                                                  # the dashboard has inline scripts ...
            self.assertEqual(set(inline), {header_nonce})                            # ... and they all carry the header's nonce
            self.assertNotRegex(html, r'<script>')                                   # none without a nonce
            nonces.add(header_nonce)
        self.assertEqual(len(nonces), 3)

    def test_no_third_party_code_is_loaded_and_vendored_files_exist(self):
        from django.contrib.staticfiles import finders
        self.login(self.admin)
        html = self.client.get(reverse('adminpanel:reports')).content.decode() + self.client.get(reverse('website:home')).content.decode()
        self.assertNotIn('jsdelivr', html)
        self.assertNotIn('cdnjs', html)
        self.assertNotIn('unpkg', html)
        for path in ['vendor/bootstrap-5.3.3/bootstrap.min.css', 'vendor/bootstrap-5.3.3/bootstrap.bundle.min.js', 'vendor/chartjs-4.4.3/chart.umd.js',
                     'vendor/bootstrap-5.3.3/LICENSE', 'vendor/chartjs-4.4.3/LICENSE']:
            self.assertTrue(finders.find(path), path)
        self.assertIn('/static/vendor/bootstrap-5.3.3/bootstrap.min.css', html)

    def test_modes_and_exemptions(self):
        from django.http import HttpResponse
        from django.test import RequestFactory
        from .middleware import ContentSecurityPolicyMiddleware
        req = RequestFactory().get('/')
        for mode, header in [('enforce', 'Content-Security-Policy'), ('report', 'Content-Security-Policy-Report-Only')]:
            with override_settings(CSP_MODE=mode):
                resp = ContentSecurityPolicyMiddleware(lambda r: HttpResponse('ok'))(req)
            self.assertIn(header, resp)
        with override_settings(CSP_MODE='off'):
            self.assertNotIn('Content-Security-Policy', ContentSecurityPolicyMiddleware(lambda r: HttpResponse('ok'))(RequestFactory().get('/')))
        self.assertNotIn('Content-Security-Policy', self.client.get('/django-admin/login/'))        # Django's own admin is left alone

    def test_error_pages_also_get_the_policy(self):
        self.assertIn('Content-Security-Policy', self.client.get('/definitely/not/here/'))

    def test_json_ld_and_json_script_blocks_are_data_not_code(self):
        html = self.client.get(reverse('website:course_detail', args=[self.course.slug])).content.decode()
        self.assertIn('type="application/ld+json"', html)


class DjangoAdminSiteIsReadOnlyForMoney(BaseCase):
    def setUp(self):
        self.root = User.objects.create_superuser('root@t.com', 'root@t.com', 'pw12345678-Zz')
        self.client.force_login(self.root)
        self.pay = Payment.objects.create(enrollment=self.enr, amount=50000, status='pending', transaction_reference='T1')

    def test_payment_status_cannot_be_changed_from_the_django_admin(self):
        url = reverse('admin:core_payment_change', args=[self.pay.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.post(url, {'status': 'paid', 'amount': 1, 'admin_note': 'looked at it', 'gateway': 'easypaisa', 'transaction_reference': 'HACK'})
        self.pay.refresh_from_db()
        self.assertEqual((self.pay.status, self.pay.amount, self.pay.transaction_reference), ('pending', 50000, 'T1'))   # untouched
        self.assertEqual(self.pay.admin_note, 'looked at it')                                                           # only the note is editable

    def test_money_and_enrollment_records_cannot_be_added_or_deleted_there(self):
        for model, obj in [('payment', self.pay), ('enrollment', self.enr)]:
            self.assertEqual(self.client.get(reverse(f'admin:core_{model}_add')).status_code, 403)
            self.assertEqual(self.client.post(reverse(f'admin:core_{model}_delete', args=[obj.pk]), {'post': 'yes'}).status_code, 403)
        self.assertTrue(Payment.objects.filter(pk=self.pay.pk).exists())

    def test_admin_lists_do_not_query_per_row(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        for i in range(15):
            u = make_user(f'adm{i}@t.com')
            e = Enrollment.objects.create(user=u, course=self.course2, status='pending_payment')
            Payment.objects.create(enrollment=e, amount=1, status='pending')
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.client.get(reverse('admin:core_payment_changelist')).status_code, 200)
        self.assertLess(len(ctx), 15)

    def test_admin_address_is_configurable(self):
        from django.conf import settings
        self.assertTrue(settings.DJANGO_ADMIN_URL.endswith('/'))
        self.assertIn(settings.DJANGO_ADMIN_URL, self.client.get('/robots.txt').content.decode())


class OperationalEndpoints(BaseCase):
    def test_healthz_reports_ok_without_logging_in_and_503_when_the_database_is_down(self):
        from unittest import mock
        from django.db.utils import OperationalError
        r = self.client.get('/healthz/')
        self.assertEqual((r.status_code, r.content), (200, b'ok'))
        from django.test import RequestFactory
        from website.views import healthz
        with mock.patch('django.db.backends.utils.CursorWrapper.execute', side_effect=OperationalError('down')):
            self.assertEqual(healthz(RequestFactory().get('/healthz/')).status_code, 503)      # called directly: middleware also needs the database

    def test_health_probe_is_not_redirected_to_https_in_production(self):
        import os, subprocess, sys
        env = dict(os.environ, DJANGO_DEBUG='0', DJANGO_SECRET_KEY='x' * 50, DJANGO_ALLOWED_HOSTS='localhost')
        code = "import django, os; os.environ['DJANGO_SETTINGS_MODULE']='config.settings'; django.setup(); from django.conf import settings as s; print(s.SECURE_SSL_REDIRECT, s.SECURE_REDIRECT_EXEMPT)"
        out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=env, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))).stdout
        self.assertIn('True', out)
        self.assertIn('healthz', out)


class TeacherLevelsCardIsBack(BaseCase):
    def test_students_by_level_counts_real_enrollments_of_my_courses(self):
        self.login(self.teacher)
        r = self.client.get(reverse('teacher:dashboard'))
        self.assertContains(r, 'Students by level')
        levels = dict(r.context['levels'])
        self.assertEqual(sum(levels.values()), 1)                       # the fixture's single active student
        self.assertEqual(levels[dict(Course.LEVEL_CHOICES)[self.course.level]], 1)
        Enrollment.objects.create(user=self.other, course=self.course2, status='active')   # another teacher's course: not counted
        self.assertEqual(sum(dict(self.client.get(reverse('teacher:dashboard')).context['levels']).values()), 1)


class RestoredDesignAdminAndData(BaseCase):
    def test_admin_manages_hero_slides_team_and_quote_cards(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        import io
        from PIL import Image
        from .models import HeroSlide, PageQuote, TeamMember
        buf = io.BytesIO(); Image.new('RGB', (40, 30), 'navy').save(buf, 'PNG')
        self.login(self.admin)
        self.assertEqual(self.client.get(reverse('adminpanel:content')).status_code, 200)
        self.client.post(reverse('adminpanel:content_item_add', args=['hero']), {'hero-image': SimpleUploadedFile('s.png', buf.getvalue()), 'hero-sort_order': 0})
        self.client.post(reverse('adminpanel:content_item_add', args=['hero']), {'hero-image_url': 'https://cdn.test/two.jpg', 'hero-sort_order': 1})
        self.assertEqual(self.client.post(reverse('adminpanel:content_item_add', args=['hero']), {'hero-sort_order': 2}).status_code, 302)   # refused: no picture
        self.assertEqual(HeroSlide.objects.count(), 2)
        home = self.client.get(reverse('website:home')).content.decode()
        self.assertEqual(home.count('class="carousel-item'), 2)
        self.assertIn('https://cdn.test/two.jpg', home)
        self.client.post(reverse('adminpanel:content_item_add', args=['team']), {'team-name': 'Sana Malik', 'team-role': 'Support', 'team-sort_order': 0})
        self.client.post(reverse('adminpanel:content_item_add', args=['quote']), {'quote-page': 'about', 'quote-quote': 'Great school', 'quote-name': 'Zed', 'quote-role': 'Alum', 'quote-sort_order': 0})
        self.assertEqual((TeamMember.objects.count(), PageQuote.objects.count()), (1, 1))
        about = self.client.get(reverse('website:about')).content.decode()
        for text in ['Sana Malik', 'Support', 'Great school', 'Zed']:
            self.assertIn(text, about)
        self.client.post(reverse('adminpanel:content_item_delete', args=['hero', HeroSlide.objects.first().pk]))
        self.assertEqual(HeroSlide.objects.count(), 1)

    def test_sample_content_buttons_and_the_stock_photo_switch_in_the_admin(self):
        from .models import Faq, SiteSettings
        self.login(self.admin)
        r = self.client.post(reverse('adminpanel:sample_content'), {'action': 'load'}, follow=True)
        self.assertContains(r, 'Loaded')
        self.assertGreater(Faq.objects.filter(is_sample=True).count(), 10)
        self.assertContains(self.client.post(reverse('adminpanel:sample_content'), {'action': 'load'}, follow=True), 'nothing was loaded')
        self.client.post(reverse('adminpanel:content'), {'site_name': 'Pix'})                    # checkbox absent = off
        self.assertFalse(SiteSettings.get().use_stock_photos)
        self.assertNotIn('picsum.photos', self.client.get(reverse('website:about')).content.decode())
        self.client.post(reverse('adminpanel:content'), {'site_name': 'Pix', 'use_stock_photos': 'on'})
        self.assertIn('picsum.photos', self.client.get(reverse('website:about')).content.decode())
        r = self.client.post(reverse('adminpanel:sample_content'), {'action': 'clear'}, follow=True)
        self.assertContains(r, 'Removed')
        self.assertEqual(Faq.objects.count(), 0)

    def test_only_admins_can_touch_sample_content(self):
        self.login(self.teacher)
        self.assertEqual(self.client.post(reverse('adminpanel:sample_content'), {'action': 'load'}).status_code, 302)
        from .models import Faq
        self.assertEqual(Faq.objects.count(), 0)

    def test_an_instructor_takes_the_role_of_the_matching_team_card(self):
        from .models import TeamMember
        lead = make_user('lead@t.com', 'teacher', first='Zainab')
        lead.last_name = 'Qureshi'; lead.save()
        Course.objects.create(title='Lead Course', slug='lead-course', teacher=lead, price=1, original_price=2, duration_weeks=1, hours_per_week=1, is_published=True)
        TeamMember.objects.create(name='Zainab Qureshi', role='Founder & Instructor', photo_url='https://x.test/p.jpg', is_sample=True)
        TeamMember.objects.create(name='Ops Person', role='Operations')
        from .site import team
        cards, staff = team()
        names = [c['name'] for c in cards]
        self.assertEqual(names.count('Zainab Qureshi'), 1)                                   # the team row and the teacher account are one card
        card = next(c for c in cards if c['name'] == 'Zainab Qureshi')
        self.assertEqual(card['role'], 'Founder & Instructor')                               # role from the team card ...
        self.assertEqual(names[-1], 'Ops Person')                                            # ... staff come after the instructors
        self.assertEqual(staff, 1)

    def test_sample_blog_posts_keep_their_dates_and_read_times(self):
        from .models import BlogPost
        from .sample_content import load_sample_content
        load_sample_content()
        posts = list(BlogPost.objects.all())                       # newest first
        self.assertEqual(len(posts), 7)
        self.assertEqual(posts[0].published_at.date().isoformat(), '2026-08-03')
        self.assertEqual(sorted({p.read_time for p in posts}), [4, 5, 6, 7, 8])
        self.assertTrue(all(p.is_sample for p in posts))

    def test_original_hero_copy_survives_blank_fields_after_migration(self):
        import importlib
        mod = importlib.import_module('core.migrations.0011_restore_design_content')
        from django.apps import apps
        from .models import SiteSettings
        s = SiteSettings.get()
        SiteSettings.objects.filter(pk=s.pk).update(hero_title='', hero_chip='Welcome', batch_cap=None, phone='+92 300 0000000', founder_name='')
        mod.restore_original_copy(apps, None)
        s.refresh_from_db()
        self.assertEqual((s.hero_title[:7], s.hero_chip, s.batch_cap, s.phone, s.founder_name), ('Learn a', 'Welcome to The Jelly Productions', 18, '+92 321 1234567', 'Arshad Nadeem'))

    def test_headers_and_cards_are_photo_rich_by_default_and_plain_when_switched_off(self):
        from .models import SiteSettings
        self.assertIn('picsum.photos/seed/fieldnote-about-hero/1600/800', self.client.get(reverse('website:about')).content.decode())
        site = SiteSettings.get(); site.use_stock_photos = False; site.save()
        self.assertNotIn('picsum.photos', self.client.get(reverse('website:about')).content.decode())


class MentorFollowsTheData(BaseCase):
    def test_without_a_founder_the_real_busiest_teacher_is_the_instructor_on_the_site(self):
        from .models import SiteSettings
        SiteSettings.objects.update_or_create(pk=SiteSettings.get().pk, defaults={'founder_name': ''})
        Course.objects.filter(teacher=self.teacher2).update(is_published=False)          # only one teacher teaches a live course
        self.teacher.first_name, self.teacher.last_name = 'Zara', 'Ahmed'; self.teacher.save()
        self.teacher.profile.title, self.teacher.profile.bio = 'Brand designer', 'I review every file myself.'
        self.teacher.profile.save()
        from .site import mentor
        m = mentor()
        self.assertEqual((m['name'], m['first'], m['title'], m['home_quote']), ('Zara Ahmed', 'Zara', 'Brand designer', 'I review every file myself.'))
        home = self.client.get(reverse('website:home')).content.decode()
        self.assertIn('Zara Ahmed', home)
        self.assertIn('Meet your instructor', home)
        self.assertNotIn('Arshad Nadeem', home)                          # the founder block is gone once the founder fields are cleared

    def test_with_neither_founder_nor_teacher_there_is_no_instructor_block(self):
        from .models import SiteSettings
        Course.objects.update(is_published=False)
        SiteSettings.objects.filter(pk=SiteSettings.get().pk).update(founder_name='')
        from .site import mentor
        self.assertIsNone(mentor())
        home = self.client.get(reverse('website:home')).content.decode()
        self.assertNotIn('Meet your instructor', home)
        self.assertIn('About us', home)                                   # the hero button falls back to a plain link
        self.assertEqual(self.client.get(reverse('website:about')).status_code, 200)

    def test_ticker_always_has_a_line_and_number_words_work(self):
        from .site import ticker_items
        from .templatetags.jelly_tags import number_word
        Batch.objects.update(status='closed')
        self.assertTrue(any('Next batch starts soon' in t for t in ticker_items()))
        self.assertEqual([number_word(i) for i in (0, 2, 10, 11, 'x')], ['zero', 'two', 'ten', 11, 'x'])
