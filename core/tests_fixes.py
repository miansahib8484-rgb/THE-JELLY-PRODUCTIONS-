"""Regression tests for the v10 review fixes (one test per bug)."""
import tempfile
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from adminpanel import stats as admin_stats
from core import services
from core.models import Batch, Certificate, Course, Enrollment, Faq, Payment, PromoCode
from core.sample_content import load_sample_content

User = get_user_model()
TMP = tempfile.mkdtemp()


def mk(email, role='student'):
    u = User.objects.create_user(username=email, email=email, password='Passw0rd!x', first_name=email.split('@')[0])
    u.profile.role = role
    u.profile.save()
    return u


@override_settings(PRIVATE_MEDIA_ROOT=TMP, MEDIA_ROOT=TMP)
class ReviewFixes(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin@x.com', 'admin@x.com', 'Passw0rd!x')
        self.teacher = mk('t@x.com', 'teacher')
        self.course = Course.objects.create(title='C1', price=1000, teacher=self.teacher)
        self.batch = Batch.objects.create(course=self.course, start_date=timezone.localdate() + timedelta(days=5), seats_total=5, status='open')
        self.s1, self.s2 = mk('s1@x.com'), mk('s2@x.com')

    def reserve(self, user, batch=None, **extra):
        self.client.force_login(user)
        data = dict(course=self.course.pk, batch=(batch or self.batch).pk, phone='0300 1111111', city='x', gateway='easypaisa', terms='on', **extra)
        return self.client.post(reverse('website:enroll'), data)

    def test_promo_limit_counts_pending_reservations(self):
        PromoCode.objects.create(code='ONCE', percent_off=10, max_uses=1)
        self.reserve(self.s1, promo_code='ONCE', transaction_reference='A1')
        self.reserve(self.s2, promo_code='ONCE', transaction_reference='A2')
        self.assertEqual(Payment.objects.filter(promo_code='ONCE').count(), 1)       # second student: code already promised

    def test_promo_limit_enforced_at_approval(self):
        PromoCode.objects.create(code='ONCE', percent_off=10, max_uses=1)
        self.reserve(self.s1, promo_code='ONCE', transaction_reference='A1')
        self.reserve(self.s2, transaction_reference='A2')
        p2 = Payment.objects.get(enrollment__user=self.s2)
        Payment.objects.filter(pk=p2.pk).update(promo_code='ONCE')                   # e.g. limit lowered after reserving
        services.approve_payment(Payment.objects.get(enrollment__user=self.s1), self.admin)
        with self.assertRaises(services.PaymentStateError):
            services.approve_payment(Payment.objects.get(pk=p2.pk), self.admin)
        self.assertEqual(PromoCode.objects.get(code='ONCE').uses_count, 1)

    def test_own_pending_code_is_not_counted_against_self(self):
        PromoCode.objects.create(code='ONCE', percent_off=10, max_uses=1)
        self.reserve(self.s1, promo_code='ONCE', transaction_reference='A1')
        self.reserve(self.s1, promo_code='ONCE', transaction_reference='A1b')        # same student resubmits
        self.assertEqual(Payment.objects.get().promo_code, 'ONCE')

    def test_admin_course_edit_keeps_inactive_instructor(self):
        self.teacher.is_active = False
        self.teacher.save()
        self.client.force_login(self.admin)
        self.client.post(reverse('adminpanel:course_edit', args=[self.course.slug]), {
            'title': 'C1', 'category': 'X', 'level': 'beginner', 'duration_weeks': 4, 'hours_per_week': 4, 'price': 1000,
            'is_published': 'on', 'teacher': self.teacher.pk})
        self.course.refresh_from_db()
        self.assertEqual(self.course.teacher_id, self.teacher.pk)

    def test_edited_sample_row_survives_clear(self):
        load_sample_content()
        faq = Faq.objects.filter(is_sample=True).first()
        untouched = Faq.objects.filter(is_sample=True).exclude(pk=faq.pk).first()
        self.client.force_login(self.admin)
        self.client.post(reverse('adminpanel:content_item_save', args=['faq', faq.pk]),
                         {'faq-page': 'all', 'faq-question': 'My own question', 'faq-answer': 'mine', 'faq-sort_order': 0})
        self.client.post(reverse('adminpanel:sample_content'), {'action': 'clear'})
        self.assertTrue(Faq.objects.filter(pk=faq.pk).exists())
        self.assertFalse(Faq.objects.filter(pk=untouched.pk).exists())

    def test_duplicate_promo_in_other_case_is_a_form_error(self):
        PromoCode.objects.create(code='SAVE10', percent_off=10)
        self.client.force_login(self.admin)
        r = self.client.post(reverse('adminpanel:promo_add'), {'promo-code': 'save10', 'promo-percent_off': 10}, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(PromoCode.objects.count(), 1)

    @override_settings(THROTTLE_ENABLED=True)
    def test_password_reset_is_throttled(self):
        codes = [self.client.post(reverse('accounts:password_reset'), {'email': 's1@x.com'}).status_code for _ in range(12)]
        self.assertIn(429, codes)
        self.assertLessEqual(len(mail.outbox), 10)

    def test_profile_email_clashing_with_other_username(self):
        User.objects.create_user(username='shared@x.com', email='other@x.com', password='x')
        self.client.force_login(self.s1)
        r = self.client.post(reverse('accounts:profile'), {'first_name': 'a', 'email': 'shared@x.com'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'used by another account')

    def test_register_rejects_future_birth_date_and_similar_password(self):
        base = dict(account_type='student', first_name='Ayesha', last_name='Khan', email='ayesha.khan@x.com', phone='0300 1111111', city='L', country='Pakistan',
                    occupation='Student', experience_level='beginner', terms='on', date_of_birth='2000-01-01')
        r = self.client.post(reverse('accounts:register'), dict(base, date_of_birth=str(timezone.localdate() + timedelta(days=1)),
                                                                password1='Passw0rd!x', password2='Passw0rd!x'))
        self.assertContains(r, 'real date of birth')
        r = self.client.post(reverse('accounts:register'), dict(base, password1='ayeshakhan', password2='ayeshakhan'))
        self.assertContains(r, 'too similar')

    def test_range_requests(self):
        self.reserve(self.s1, transaction_reference='A1')
        p = Payment.objects.get()
        p.proof_file.save('x.png', ContentFile(b'\x89PNG\r\n\x1a\n' + b'0' * 92))
        url = reverse('files:proof', args=[p.pk])
        r = self.client.get(url, HTTP_RANGE='bytes=-500')                       # file shorter than the suffix: whole file
        self.assertEqual(r.status_code, 206)
        self.assertEqual(r['Content-Range'], 'bytes 0-99/100')
        self.assertEqual(self.client.get(url, HTTP_RANGE='bytes=0-9,20-29').status_code, 200)   # several ranges: whole file
        r = self.client.get(url, HTTP_RANGE='bytes=500-')
        self.assertEqual((r.status_code, r['Content-Range']), (416, 'bytes */100'))
        self.assertEqual(self.client.get(url, HTTP_RANGE='bytes=10-19').status_code, 206)

    def test_revenue_belongs_to_approval_month(self):
        self.reserve(self.s1, transaction_reference='A1')
        p = Payment.objects.get()
        Payment.objects.filter(pk=p.pk).update(created_at=timezone.now() - timedelta(days=45))
        services.approve_payment(Payment.objects.get(), self.admin)
        k = admin_stats.kpis()
        self.assertEqual(k['revenue']['month'], 1000)
        self.assertEqual(k['revenue']['value'], 1000)
        self.assertEqual(admin_stats.report('30d')['revenue'], 1000)

    def test_deleting_certificate_reopens_enrollment(self):
        self.reserve(self.s1, transaction_reference='A1')
        services.approve_payment(Payment.objects.get(), self.admin)
        c = Certificate.objects.create(student=self.s1, course=self.course, file=ContentFile(b'%PDF-1.4', name='c.pdf'))
        Enrollment.objects.update(status='completed')
        self.client.force_login(self.admin)
        self.client.post(reverse('adminpanel:certificate_delete', args=[c.pk]))
        self.assertEqual(Enrollment.objects.get().status, 'active')

    def test_switching_batch_sends_no_cancellation_mail(self):
        b2 = Batch.objects.create(course=self.course, start_date=timezone.localdate() + timedelta(days=9), seats_total=5, status='open')
        self.reserve(self.s1, transaction_reference='A1')
        mail.outbox.clear()
        self.reserve(self.s1, batch=b2, transaction_reference='A2')
        self.assertFalse([m for m in mail.outbox if 'cancelled' in m.subject.lower()])

    def test_expire_reservations(self):
        stale = mk('stale@x.com'); fresh = mk('fresh@x.com'); proof = mk('proof@x.com')
        self.reserve(stale); self.reserve(fresh); self.reserve(proof, transaction_reference='T1')
        old = timezone.now() - timedelta(hours=100)
        Enrollment.objects.filter(user__in=[stale, proof]).update(enrolled_at=old)
        call_command('expire_reservations', stdout=StringIO())
        self.assertEqual(Enrollment.objects.get(user=stale).status, 'cancelled')
        self.assertEqual(Enrollment.objects.get(user=fresh).status, 'pending_payment')
        self.assertEqual(Enrollment.objects.get(user=proof).status, 'pending_payment')   # proof sent: never expired
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.seats_filled, 2)
