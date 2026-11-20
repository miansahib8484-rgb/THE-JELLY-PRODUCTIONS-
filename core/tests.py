import io
import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (Batch, Certificate, ClassRecording, ContactSubmission, Course, CurriculumLecture, CurriculumModule,
                         Enrollment, LiveClass, NewsletterSubscriber, Notification, Payment, PromoCode)

User = get_user_model()
MEDIA = tempfile.mkdtemp()
PRIVATE = tempfile.mkdtemp()


def png(name='x.png'):
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (40, 25), 'blue').save(buf, 'PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


def make_user(email, role='student', password='pass12345-Xy', **kw):
    u = User.objects.create_user(username=email, email=email, password=password, first_name=kw.get('first', 'Test'), last_name=role.title())
    u.profile.role = role
    u.profile.save()
    return u


@override_settings(MEDIA_ROOT=MEDIA, PRIVATE_MEDIA_ROOT=PRIVATE)
class BaseCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)
        shutil.rmtree(PRIVATE, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user('admin@t.com', 'admin')
        cls.teacher = make_user('teacher@t.com', 'teacher')
        cls.teacher2 = make_user('teacher2@t.com', 'teacher')
        cls.student = make_user('student@t.com', 'student')
        cls.other = make_user('other@t.com', 'student')
        cls.course = Course.objects.create(title='UI Systems', teacher=cls.teacher, price=50000, original_price=70000, description='x')
        cls.course2 = Course.objects.create(title='Other teacher course', teacher=cls.teacher2, price=30000)
        cls.batch = Batch.objects.create(course=cls.course, start_date=timezone.localdate() + timedelta(days=10), seats_total=2, status='open')
        m = CurriculumModule.objects.create(course=cls.course, title='M1')
        cls.lec1 = CurriculumLecture.objects.create(module=m, title='L1')
        cls.lec2 = CurriculumLecture.objects.create(module=m, title='L2')
        cls.enr = Enrollment.objects.create(user=cls.student, course=cls.course, batch=cls.batch, status='active')
        PromoCode.objects.create(code='JELLY10', percent_off=10)

    def login(self, user):
        self.client.force_login(user)


class PublicSiteTests(BaseCase):
    def test_public_pages_open_without_login(self):
        for name, args in [('website:home', []), ('website:courses', []), ('website:course_detail', [self.course.slug]), ('website:about', []),
                           ('website:contact', []), ('website:blog', []), ('accounts:login', []), ('accounts:register', []),
                           ('accounts:password_reset', [])]:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200, name)

    def test_course_search_and_level_filter(self):
        r = self.client.get(reverse('website:courses'), {'q': 'systems'})
        self.assertContains(r, 'UI Systems')
        self.assertNotContains(r, 'Other teacher course')
        self.assertNotContains(self.client.get(reverse('website:courses'), {'level': 'advanced'}), self.course.get_absolute_url())

    def test_unpublished_course_is_hidden(self):
        Course.objects.filter(pk=self.course.pk).update(is_published=False)
        self.assertEqual(self.client.get(reverse('website:course_detail', args=[self.course.slug])).status_code, 404)
        self.assertNotContains(self.client.get(reverse('website:courses')), self.course.get_absolute_url())

    def test_contact_form_saves_message_and_notifies_admin(self):
        r = self.client.post(reverse('website:contact'), {'name': 'A', 'email': 'a@b.com', 'topic': 'Refunds or billing', 'message': 'Hi'})
        self.assertRedirects(r, reverse('website:contact'))
        self.assertEqual(ContactSubmission.objects.count(), 1)
        self.assertTrue(Notification.objects.filter(user=self.admin).exists())

    def test_newsletter_subscribe_is_idempotent_and_validates(self):
        for _ in range(2):
            self.client.post(reverse('website:newsletter_subscribe'), {'email': 'x@y.com'})
        self.assertEqual(NewsletterSubscriber.objects.count(), 1)
        self.client.post(reverse('website:newsletter_subscribe'), {'email': 'not-an-email'})
        self.assertEqual(NewsletterSubscriber.objects.count(), 1)

    def test_promo_check(self):
        self.assertTrue(self.client.get(reverse('website:promo_check'), {'code': 'jelly10'}).json()['valid'])
        self.assertFalse(self.client.get(reverse('website:promo_check'), {'code': 'nope'}).json()['valid'])

    def test_blog_posts_come_from_database(self):
        from core.models import BlogPost
        BlogPost.objects.create(title='Hello World', body='## Hi\n\nBody text', category='news', author_name='Team', excerpt='Short')
        BlogPost.objects.create(title='Hidden draft', body='x', is_published=False)
        BlogPost.objects.create(title='Scheduled', body='x', published_at=timezone.now() + timedelta(days=3))
        r = self.client.get(reverse('website:blog'))
        self.assertContains(r, 'Hello World')
        self.assertNotContains(r, 'Hidden draft')
        self.assertNotContains(r, 'Scheduled')
        r = self.client.get(reverse('website:blog_detail', args=['hello-world']))
        self.assertContains(r, '<h2>Hi</h2>')
        self.assertEqual(self.client.get(reverse('website:blog_detail', args=['hidden-draft'])).status_code, 404)
        self.assertEqual(self.client.get(reverse('website:blog_detail', args=['missing'])).status_code, 404)

    def test_register_creates_student_with_profile(self):
        r = self.client.post(reverse('accounts:register'), {
            'account_type': 'student', 'first_name': 'New', 'last_name': 'User', 'email': 'new@t.com', 'phone': '0300 1234567',
            'date_of_birth': '2000-01-01', 'city': 'Lahore', 'country': 'Pakistan', 'occupation': 'Student', 'experience_level': 'beginner',
            'password1': 'Str0ng-pass!', 'password2': 'Str0ng-pass!', 'terms': 'on', 'newsletter': 'on'})
        self.assertRedirects(r, reverse('student:dashboard'))
        u = User.objects.get(email='new@t.com')
        self.assertEqual(u.profile.role, 'student')
        self.assertEqual(u.profile.city, 'Lahore')
        self.assertTrue(NewsletterSubscriber.objects.filter(email='new@t.com').exists())

    def test_asking_to_teach_does_not_grant_teacher_role(self):
        self.client.post(reverse('accounts:register'), {
            'account_type': 'teacher', 'first_name': 'Sneaky', 'last_name': 'User', 'email': 's@t.com', 'phone': '0300 1234567',
            'date_of_birth': '2000-01-01', 'city': 'X', 'country': 'Pakistan', 'occupation': 'Freelancer', 'experience_level': 'working',
            'password1': 'Str0ng-pass!', 'password2': 'Str0ng-pass!', 'terms': 'on'})
        self.assertEqual(User.objects.get(email='s@t.com').profile.role, 'student')
        self.assertTrue(ContactSubmission.objects.filter(topic='Teacher application').exists())

    def test_register_rejects_duplicate_email_and_weak_password(self):
        r = self.client.post(reverse('accounts:register'), {'email': 'student@t.com', 'password1': '123', 'password2': '123'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(User.objects.filter(email='student@t.com').count(), 1)

    def test_login_by_email_redirects_to_role_dashboard(self):
        for user, target, role in [(self.student, 'student:dashboard', 'student'), (self.teacher, 'teacher:dashboard', 'teacher'), (self.admin, 'adminpanel:overview', 'admin')]:
            self.client.logout()
            r = self.client.post(reverse('accounts:login'), {'username': user.email, 'password': 'pass12345-Xy', 'role': role})
            self.assertRedirects(r, reverse(target))

    def test_login_wrong_password(self):
        r = self.client.post(reverse('accounts:login'), {'username': 'student@t.com', 'password': 'wrong'})
        self.assertContains(r, 'Incorrect')

    def test_login_ignores_external_next_url(self):
        r = self.client.post(reverse('accounts:login') + '?next=https://evil.example/', {'username': 'student@t.com', 'password': 'pass12345-Xy'})
        self.assertRedirects(r, reverse('student:dashboard'))

    def test_logout_requires_post(self):
        self.login(self.student)
        self.assertEqual(self.client.get(reverse('accounts:logout')).status_code, 405)
        self.client.post(reverse('accounts:logout'))
        self.assertEqual(self.client.get(reverse('student:dashboard')).status_code, 302)


class RoleAccessTests(BaseCase):
    def test_anonymous_is_sent_to_login(self):
        for name in ('student:dashboard', 'teacher:dashboard', 'adminpanel:overview', 'accounts:profile'):
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 302, name)
            self.assertIn('/account/login/', r['Location'])

    def test_roles_cannot_open_other_dashboards(self):
        self.login(self.student)
        for name in ('teacher:dashboard', 'adminpanel:overview', 'adminpanel:payments', 'teacher:live'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 302, name)
        self.login(self.teacher)
        for name in ('student:dashboard', 'adminpanel:overview', 'adminpanel:settings'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 302, name)
        self.login(self.admin)
        self.assertEqual(self.client.get(reverse('teacher:dashboard')).status_code, 302)

    def test_every_dashboard_page_renders_for_its_role(self):
        pages = {
            'student': [('student:dashboard', []), ('student:courses', []), ('student:player', [self.course.slug]), ('student:live', []),
                        ('student:certificates', []), ('student:reviews', []), ('student:billing', []), ('accounts:profile', []),
                        ('accounts:settings', []), ('accounts:notifications', [])],
            'teacher': [('teacher:dashboard', []), ('teacher:courses', []), ('teacher:course_create', []), ('teacher:course_edit', [self.course.slug]),
                        ('teacher:builder', [self.course.slug]), ('teacher:students', []), ('teacher:reviews', []), ('teacher:live', []),
                        ('teacher:live_create', []), ('teacher:certificates', [])],
            'admin': [('adminpanel:overview', []), ('adminpanel:reports', []), ('adminpanel:courses', []), ('adminpanel:course_create', []),
                      ('adminpanel:teachers', []), ('adminpanel:students', []), ('adminpanel:enrollments', []), ('adminpanel:live', []),
                      ('adminpanel:certificates', []), ('adminpanel:payments', []), ('adminpanel:contact', []), ('adminpanel:newsletter', []),
                      ('adminpanel:settings', []), ('adminpanel:newsletter_export', [])],
        }
        users = {'student': self.student, 'teacher': self.teacher, 'admin': self.admin}
        for role, items in pages.items():
            self.login(users[role])
            for name, args in items:
                self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200, f'{role}: {name}')


class EnrollAndPaymentTests(BaseCase):
    def enroll(self, **extra):
        data = {'course': str(self.course.pk), 'batch': self.batch.pk, 'phone': '0300 1234567', 'city': 'Lahore', 'gateway': 'easypaisa',
                'transaction_reference': 'TX1', 'terms': 'on'}
        data.update(extra)
        return self.client.post(reverse('website:enroll'), data)

    def test_enroll_requires_login_and_student_role(self):
        self.assertEqual(self.client.get(reverse('website:enroll')).status_code, 302)
        self.login(self.teacher)
        self.assertRedirects(self.client.get(reverse('website:enroll')), reverse('website:courses'))

    def test_enroll_creates_pending_enrollment_payment_and_takes_a_seat(self):
        self.login(self.other)
        self.assertRedirects(self.enroll(promo_code='JELLY10'), reverse('student:billing'))
        e = Enrollment.objects.get(user=self.other, course=self.course)
        self.assertEqual(e.status, 'pending_payment')
        self.assertEqual(e.payment.amount, 45000)  # 10% off 50,000
        self.assertEqual(e.payment.promo_code, 'JELLY10')
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.seats_filled, 2)       # the fixture's active student + this reservation

    def test_enroll_twice_does_not_double_count_seats(self):
        self.login(self.other)
        self.enroll()
        self.enroll()
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.seats_filled, 2)
        self.assertEqual(Enrollment.objects.filter(user=self.other, course=self.course).count(), 1)

    def test_full_batch_is_refused(self):
        Batch.objects.filter(pk=self.batch.pk).update(seats_filled=2)
        self.login(self.other)
        self.enroll()
        self.assertFalse(Enrollment.objects.filter(user=self.other, course=self.course).exists())

    def test_admin_approves_payment_and_student_unlocks_course(self):
        self.login(self.other)
        self.enroll()
        pay = Payment.objects.get(enrollment__user=self.other)
        player = reverse('student:player', args=[self.course.slug])
        self.assertRedirects(self.client.get(player), reverse('student:billing'))  # locked while pending
        self.login(self.admin)
        self.client.post(reverse('adminpanel:payment_review', args=[pay.pk]), {'action': 'approve'})
        pay.refresh_from_db()
        self.assertEqual((pay.status, pay.enrollment.status), ('paid', 'active'))
        self.login(self.other)
        self.assertEqual(self.client.get(player).status_code, 200)
        self.assertTrue(Notification.objects.filter(user=self.other, title='Payment approved').exists())

    def test_admin_rejects_payment_with_reason(self):
        self.login(self.other)
        self.enroll()
        pay = Payment.objects.get(enrollment__user=self.other)
        self.login(self.admin)
        self.client.post(reverse('adminpanel:payment_review', args=[pay.pk]), {'action': 'reject', 'note': 'Blurry screenshot'})
        pay.refresh_from_db()
        self.assertEqual((pay.status, pay.admin_note), ('failed', 'Blurry screenshot'))

    def test_cancel_enrollment_releases_the_seat(self):
        self.login(self.other)
        self.enroll()
        e = Enrollment.objects.get(user=self.other)
        self.login(self.admin)
        self.client.post(reverse('adminpanel:enrollment_cancel', args=[e.pk]))
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.seats_filled, 1)       # only the fixture's active student still holds a seat

    def test_student_can_toggle_lecture_progress(self):
        self.login(self.student)
        self.client.post(reverse('student:toggle_lecture', args=[self.lec1.pk]), {'next': 'https://evil.example/'})
        self.assertEqual(self.enr.progress_pct, 50)
        self.client.post(reverse('student:toggle_lecture', args=[self.lec1.pk]))
        self.assertEqual(self.enr.progress_pct, 0)

    def test_review_only_for_enrolled_students_once(self):
        url = reverse('website:submit_review', args=[self.course.slug])
        self.login(self.other)
        self.client.post(url, {'rating': 5, 'comment': 'x'})
        self.assertEqual(self.course.reviews.count(), 0)
        self.login(self.student)
        self.client.post(url, {'rating': 4, 'comment': 'Good'})
        self.client.post(url, {'rating': 1, 'comment': 'Again'})
        self.assertEqual(self.course.reviews.count(), 1)
        self.course.refresh_from_db()
        self.assertEqual(float(self.course.rating_avg), 4.0)


class LiveClassTests(BaseCase):
    def schedule(self, user, when, **extra):
        self.login(user)
        data = {'course': self.course.pk, 'title': 'Weekly critique', 'starts_at': when.strftime('%Y-%m-%dT%H:%M'), 'duration_minutes': 60,
                'platform': 'meet', 'meeting_link': 'https://meet.google.com/abc-defg-hij'}
        data.update(extra)
        return self.client.post(reverse('teacher:live_create' if user.profile.role == 'teacher' else 'adminpanel:live_create'), data)

    def test_teacher_schedules_class_and_enrolled_student_sees_time_and_link(self):
        future = timezone.localtime() + timedelta(days=1)
        r = self.schedule(self.teacher, future)
        self.assertRedirects(r, reverse('teacher:live'))
        c = LiveClass.objects.get()
        self.assertEqual(c.status, 'upcoming')
        self.assertTrue(Notification.objects.filter(user=self.student, title__startswith='New live class').exists())
        self.login(self.student)
        page = self.client.get(reverse('student:live'))
        self.assertContains(page, 'Weekly critique')
        self.assertContains(page, 'PKT')
        self.assertContains(self.client.get(reverse('student:dashboard')), 'Weekly critique')

    def test_unenrolled_student_does_not_see_the_class(self):
        self.schedule(self.teacher, timezone.localtime() + timedelta(days=1))
        self.login(self.other)
        self.assertNotContains(self.client.get(reverse('student:live')), 'Weekly critique')

    def test_join_button_opens_ten_minutes_before_start(self):
        c = LiveClass.objects.create(course=self.course, title='T', starts_at=timezone.now() + timedelta(minutes=30), meeting_link='https://zoom.us/j/1')
        self.assertFalse(c.can_join)
        c.starts_at = timezone.now() + timedelta(minutes=5)
        self.assertTrue(c.can_join)
        c.meeting_link = ''
        self.assertFalse(c.can_join)

    def test_teacher_cannot_schedule_for_another_teachers_course(self):
        self.login(self.teacher)
        r = self.client.post(reverse('teacher:live_create'), {'course': self.course2.pk, 'title': 'x', 'starts_at': '2030-01-01T10:00',
                                                               'duration_minutes': 60, 'platform': 'meet'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(LiveClass.objects.count(), 0)

    def test_recording_flow_link_then_student_watches(self):
        past = LiveClass.objects.create(course=self.course, title='Past', starts_at=timezone.now() - timedelta(days=1), meeting_link='https://zoom.us/j/1')
        self.login(self.teacher)
        r = self.client.post(reverse('teacher:recording', args=[past.pk]), {'rec_type': 'link', 'video_url': 'https://youtu.be/dQw4w9WgXcQ'})
        self.assertRedirects(r, reverse('teacher:live'))
        self.assertTrue(Notification.objects.filter(user=self.student, title__startswith='Recording ready').exists())
        self.login(self.student)
        page = self.client.get(reverse('student:watch', args=[past.pk]))
        self.assertContains(page, 'youtube.com/embed/dQw4w9WgXcQ')
        self.assertContains(self.client.get(reverse('student:live'), {'status': 'recordings'}), 'Past')
        self.login(self.other)
        self.assertEqual(self.client.get(reverse('student:watch', args=[past.pk])).status_code, 404)

    def test_recording_file_upload_and_validation(self):
        past = LiveClass.objects.create(course=self.course, title='Past', starts_at=timezone.now() - timedelta(days=1))
        self.login(self.admin)
        bad = self.client.post(reverse('adminpanel:recording', args=[past.pk]), {'rec_type': 'file', 'video_file': SimpleUploadedFile('x.exe', b'1')})
        self.assertEqual(bad.status_code, 200)
        self.assertFalse(ClassRecording.objects.exists())
        ok = self.client.post(reverse('adminpanel:recording', args=[past.pk]),
                              {'rec_type': 'file', 'video_file': SimpleUploadedFile('lecture.mp4', b'0' * 1000, content_type='video/mp4')})
        self.assertRedirects(ok, reverse('adminpanel:live'))
        self.assertTrue(ClassRecording.objects.get().video_file.name.endswith('.mp4'))

    def test_recording_blocked_until_class_has_ended_and_end_button_works(self):
        c = LiveClass.objects.create(course=self.course, title='Soon', starts_at=timezone.now() + timedelta(hours=2))
        self.login(self.teacher)
        self.client.post(reverse('teacher:recording', args=[c.pk]), {'rec_type': 'link', 'video_url': 'https://youtu.be/dQw4w9WgXcQ'})
        self.assertFalse(ClassRecording.objects.exists())
        self.client.post(reverse('teacher:live_end', args=[c.pk]))
        c.refresh_from_db()
        self.assertEqual(c.status, 'ended')

    def test_admin_can_schedule_for_any_course(self):
        r = self.schedule(self.admin, timezone.localtime() + timedelta(days=2))
        self.assertRedirects(r, reverse('adminpanel:live'))


class CertificateTests(BaseCase):
    def test_teacher_issues_certificate_student_downloads_and_course_completes(self):
        self.login(self.teacher)
        r = self.client.post(reverse('teacher:certificates'), {'student': self.student.pk, 'course': self.course.pk, 'issued_on': '2026-10-01',
                                                               'file': SimpleUploadedFile('cert.pdf', b'%PDF-1.4 test', content_type='application/pdf')})
        self.assertRedirects(r, reverse('teacher:certificates'))
        cert = Certificate.objects.get()
        self.enr.refresh_from_db()
        self.assertEqual(self.enr.status, 'completed')
        self.login(self.student)
        page = self.client.get(reverse('student:certificates'))
        self.assertContains(page, cert.file_url)
        self.assertEqual(self.client.get(cert.file_url).status_code, 200)
        self.assertIn('attachment', self.client.get(cert.download_url)['Content-Disposition'])

    def test_certificate_needs_enrolled_student_and_safe_file_type(self):
        self.login(self.teacher)
        r = self.client.post(reverse('teacher:certificates'), {'student': self.other.pk, 'course': self.course.pk, 'issued_on': '2026-10-01',
                                                               'file': SimpleUploadedFile('c.pdf', b'x')})
        self.assertEqual(r.status_code, 200)
        r = self.client.post(reverse('teacher:certificates'), {'student': self.student.pk, 'course': self.course.pk, 'issued_on': '2026-10-01',
                                                               'file': SimpleUploadedFile('c.exe', b'x')})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Certificate.objects.count(), 0)


class CourseManagementTests(BaseCase):
    def data(self, **extra):
        d = {'title': 'Brand identity basics', 'category': 'Design', 'level': 'beginner', 'duration_weeks': 4, 'hours_per_week': 4,
             'price': 35999, 'original_price': 49999, 'description': 'd', 'is_published': 'on', 'thumbnail': png()}
        d.update(extra)
        return d

    def test_teacher_adds_course_with_image_and_it_shows_on_the_website(self):
        self.login(self.teacher)
        r = self.client.post(reverse('teacher:course_create'), self.data())
        c = Course.objects.get(title='Brand identity basics')
        self.assertRedirects(r, reverse('teacher:builder', args=[c.slug]))
        self.assertEqual(c.teacher, self.teacher)
        self.assertTrue(c.thumbnail.name.startswith('courses/'))
        self.client.logout()
        page = self.client.get(reverse('website:courses'))
        self.assertContains(page, 'Brand identity basics')
        self.assertContains(page, c.thumbnail.url)
        self.assertContains(page, '-28%')

    def test_draft_course_is_not_public_and_toggle_publishes_it(self):
        self.login(self.teacher)
        d = self.data()
        d.pop('is_published')
        self.client.post(reverse('teacher:course_create'), d)
        c = Course.objects.get(title='Brand identity basics')
        self.assertNotContains(self.client.get(reverse('website:courses')), 'Brand identity basics')
        self.client.post(reverse('teacher:course_toggle', args=[c.slug]))
        self.assertContains(self.client.get(reverse('website:courses')), 'Brand identity basics')

    def test_original_price_must_be_higher_and_non_image_is_rejected(self):
        self.login(self.teacher)
        r = self.client.post(reverse('teacher:course_create'), self.data(original_price=100))
        self.assertEqual(r.status_code, 200)
        r = self.client.post(reverse('teacher:course_create'), self.data(thumbnail=SimpleUploadedFile('a.txt', b'not an image')))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Course.objects.filter(title='Brand identity basics').exists())

    def test_teacher_cannot_touch_another_teachers_course(self):
        self.login(self.teacher)
        self.assertEqual(self.client.get(reverse('teacher:course_edit', args=[self.course2.slug])).status_code, 404)
        self.assertEqual(self.client.post(reverse('teacher:course_delete', args=[self.course2.slug])).status_code, 404)
        self.assertEqual(self.client.get(reverse('teacher:builder', args=[self.course2.slug])).status_code, 404)

    def test_admin_sees_all_courses_and_can_assign_instructor(self):
        self.login(self.admin)
        page = self.client.get(reverse('adminpanel:courses'))
        self.assertContains(page, 'UI Systems')
        self.assertContains(page, 'Other teacher course')
        r = self.client.post(reverse('adminpanel:course_edit', args=[self.course2.slug]), {
            'title': 'Other teacher course', 'category': 'Design', 'level': 'beginner', 'duration_weeks': 4, 'hours_per_week': 4, 'price': 30000,
            'teacher': self.teacher.pk, 'is_published': 'on', 'is_featured': 'on'})
        self.assertEqual(r.status_code, 302)
        self.course2.refresh_from_db()
        self.assertEqual(self.course2.teacher, self.teacher)
        self.assertTrue(self.course2.is_featured)

    def test_course_with_students_cannot_be_deleted(self):
        self.login(self.teacher)
        self.client.post(reverse('teacher:course_delete', args=[self.course.slug]))
        self.assertTrue(Course.objects.filter(pk=self.course.pk).exists())

    def test_builder_adds_module_lecture_and_batch(self):
        self.login(self.teacher)
        self.client.post(reverse('teacher:module_add', args=[self.course.slug]), {'title': 'Module 2'})
        m = self.course.modules.get(title='Module 2')
        self.client.post(reverse('teacher:lecture_add', args=[m.pk]), {'title': 'Intro', 'type': 'video', 'duration_label': '5 min'})
        self.assertEqual(m.lectures.count(), 1)
        self.client.post(reverse('teacher:batch_add', args=[self.course.slug]), {'start_date': '2030-01-01', 'seats_total': 10, 'status': 'open'})
        self.assertEqual(self.course.batches.count(), 2)
        self.client.post(reverse('teacher:batch_delete', args=[self.batch.pk]))  # has a student: must stay
        self.assertTrue(Batch.objects.filter(pk=self.batch.pk).exists())


class AdminPanelTests(BaseCase):
    def test_admin_creates_teacher_who_can_sign_in_as_teacher(self):
        self.login(self.admin)
        self.client.post(reverse('adminpanel:teachers'), {'first_name': 'New', 'last_name': 'Teacher', 'email': 'nt@t.com', 'password': 'Temp-pass-123'})
        self.assertEqual(User.objects.get(email='nt@t.com').profile.role, 'teacher')
        self.client.logout()
        r = self.client.post(reverse('accounts:login'), {'username': 'nt@t.com', 'password': 'Temp-pass-123', 'role': 'teacher'})
        self.assertRedirects(r, reverse('teacher:dashboard'), target_status_code=200)     # the password the administrator gave works as it is
        self.assertEqual(self.client.get(reverse('teacher:dashboard')).status_code, 200)

    def test_deactivated_student_cannot_sign_in(self):
        self.login(self.admin)
        self.client.post(reverse('adminpanel:user_toggle', args=[self.student.pk]))
        self.client.logout()
        r = self.client.post(reverse('accounts:login'), {'username': 'student@t.com', 'password': 'pass12345-Xy'})
        self.assertContains(r, 'deactivated')                          # the right password: the person is told why
        self.assertNotIn('_auth_user_id', self.client.session)
        r = self.client.post(reverse('accounts:login'), {'username': 'student@t.com', 'password': 'wrong-password'})
        self.assertContains(r, 'Incorrect')                            # a wrong password reveals nothing

    def test_contact_status_and_newsletter_export(self):
        s = ContactSubmission.objects.create(name='A', email='a@b.com', message='m')
        NewsletterSubscriber.objects.create(email='sub@t.com')
        self.login(self.admin)
        self.client.post(reverse('adminpanel:contact_update', args=[s.pk]), {'status': 'replied'})
        s.refresh_from_db()
        self.assertEqual(s.status, 'replied')
        csv_resp = self.client.get(reverse('adminpanel:newsletter_export'))
        self.assertIn('sub@t.com', csv_resp.content.decode())

    def test_settings_payment_info_and_promo(self):
        self.login(self.admin)
        self.client.post(reverse('adminpanel:promo_add'), {'promo-code': 'eid20', 'promo-percent_off': 20})
        self.assertTrue(PromoCode.objects.filter(code='EID20').exists())
        self.client.post(reverse('adminpanel:promo_toggle', args=[PromoCode.objects.get(code='EID20').pk]))
        self.assertIsNone(PromoCode.lookup('EID20'))

    def test_profile_and_password_change(self):
        self.login(self.student)
        self.client.post(reverse('accounts:profile'), {'first_name': 'Ayesha', 'last_name': 'K', 'email': 'student@t.com', 'phone': '0300 1111111', 'bio': 'hi'})
        self.student.refresh_from_db()
        self.assertEqual(self.student.first_name, 'Ayesha')
        r = self.client.post(reverse('accounts:settings'), {'old_password': 'pass12345-Xy', 'new_password1': 'N3w-secret-pass', 'new_password2': 'N3w-secret-pass'})
        self.assertRedirects(r, reverse('accounts:settings'))
        self.student.refresh_from_db()
        self.assertTrue(self.student.check_password('N3w-secret-pass'))

    def test_notifications_open_mark_read_and_cannot_read_others(self):
        n = Notification.objects.create(user=self.student, title='Hi', url='/student/')
        self.login(self.other)
        self.client.get(reverse('accounts:notification_open', args=[n.pk]))
        n.refresh_from_db()
        self.assertFalse(n.is_read)
        self.login(self.student)
        self.client.get(reverse('accounts:notification_open', args=[n.pk]))
        n.refresh_from_db()
        self.assertTrue(n.is_read)


class PrivateFileTests(BaseCase):
    """Certificates, payment proofs and recordings must not be reachable by guessing a URL."""

    def make_cert(self):
        return Certificate.objects.create(student=self.student, course=self.course, issued_by=self.teacher,
                                          file=SimpleUploadedFile('cert.pdf', b'%PDF-1.4 secret', content_type='application/pdf'))

    def test_certificate_visible_only_to_owner_teacher_and_admin(self):
        cert = self.make_cert()
        self.assertEqual(self.client.get(cert.file_url).status_code, 302)           # anonymous
        for user, code in [(self.student, 200), (self.teacher, 200), (self.admin, 200), (self.other, 404), (self.teacher2, 404)]:
            self.login(user)
            self.assertEqual(self.client.get(cert.file_url).status_code, code, user.email)

    def test_private_files_have_no_public_media_url(self):
        cert = self.make_cert()
        with self.assertRaises(ValueError):
            cert.file.url

    def test_payment_proof_visible_only_to_owner_and_admin(self):
        pay = Payment.objects.create(enrollment=self.enr, amount=1, proof_file=SimpleUploadedFile('p.png', b'png', content_type='image/png'))
        for user, code in [(self.student, 200), (self.admin, 200), (self.other, 404), (self.teacher, 404)]:
            self.login(user)
            self.assertEqual(self.client.get(pay.proof_url).status_code, code, user.email)

    def test_recording_file_for_enrolled_students_only_and_supports_range(self):
        past = LiveClass.objects.create(course=self.course, title='Past', starts_at=timezone.now() - timedelta(days=1))
        rec = ClassRecording.objects.create(live_class=past, video_file=SimpleUploadedFile('r.mp4', b'0123456789', content_type='video/mp4'))
        self.login(self.other)
        self.assertEqual(self.client.get(rec.file_url).status_code, 404)
        self.login(self.student)
        r = self.client.get(rec.file_url, HTTP_RANGE='bytes=2-5')
        self.assertEqual(r.status_code, 206)
        self.assertEqual(b''.join(r.streaming_content), b'2345')
        self.assertEqual(r['Content-Range'], 'bytes 2-5/10')
        self.assertEqual(self.client.get(rec.file_url, HTTP_RANGE='bytes=50-60').status_code, 416)


class AdminDashboardDynamicTests(BaseCase):
    def setUp(self):
        self.login(self.admin)

    def _paid(self, amount=50000):
        p = Payment.objects.create(enrollment=self.enr, amount=amount, status='paid')
        return p

    def test_every_admin_page_renders(self):
        self._paid()
        for name in ['overview', 'reports', 'teachers', 'students', 'enrollments', 'payments', 'contact', 'newsletter',
                     'reviews', 'settings', 'courses', 'live', 'certificates']:
            r = self.client.get(reverse(f'adminpanel:{name}'))
            self.assertEqual(r.status_code, 200, name)
        for rng in ['30d', '90d', '12m', 'all', 'bogus']:
            self.assertEqual(self.client.get(reverse('adminpanel:reports'), {'range': rng}).status_code, 200)

    def test_overview_numbers_come_from_database(self):
        self._paid(50000)
        r = self.client.get(reverse('adminpanel:overview'))
        self.assertEqual(r.context['k']['revenue']['value'], 50000)
        self.assertEqual(r.context['k']['students']['value'], 2)
        self.assertContains(r, 'Rs 50,000')
        Payment.objects.update(amount=75000)
        self.assertContains(self.client.get(reverse('adminpanel:overview')), 'Rs 75,000')

    def test_api_summary_is_live_and_admin_only(self):
        self._paid(1000)
        data = self.client.get(reverse('adminpanel:api_summary')).json()
        self.assertEqual(data['revenue'], 'Rs 1,000')
        self.assertEqual(data['pending'], 0)
        ContactSubmission.objects.create(name='A', email='a@b.com', message='hi')
        self.assertEqual(self.client.get(reverse('adminpanel:api_summary')).json()['messages'], 1)
        self.client.logout()
        self.login(self.student)
        self.assertEqual(self.client.get(reverse('adminpanel:api_summary')).status_code, 302)

    def test_sidebar_badges_count_pending_work(self):
        e2 = Enrollment.objects.create(user=self.other, course=self.course2, status='pending_payment')
        Payment.objects.create(enrollment=e2, amount=30000, status='pending')
        r = self.client.get(reverse('adminpanel:overview'))
        self.assertTrue(any(i.get('badge') == 1 for i in r.context['menu'] if 'url' in i and i['url'] == 'adminpanel:payments'))

    def test_csv_exports(self):
        self._paid(50000)
        for kind in ['payments', 'enrollments', 'students']:
            r = self.client.get(reverse('adminpanel:export', args=[kind]))
            self.assertEqual(r['Content-Type'], 'text/csv')
        self.assertIn('50000', self.client.get(reverse('adminpanel:export', args=['payments'])).content.decode())
        self.assertEqual(self.client.get(reverse('adminpanel:export', args=['nope'])).status_code, 404)

    def test_exports_blocked_for_non_admin(self):
        self.client.logout()
        self.login(self.teacher)
        self.assertEqual(self.client.get(reverse('adminpanel:export', args=['payments'])).status_code, 302)

    def test_user_detail_and_edit(self):
        r = self.client.get(reverse('adminpanel:user_detail', args=[self.student.pk]))
        self.assertEqual(r.status_code, 200)
        r = self.client.post(reverse('adminpanel:user_detail', args=[self.student.pk]), {
            'first_name': 'Renamed', 'last_name': 'Student', 'email': 'student@t.com', 'phone': '0300 1234567', 'city': 'Lahore', 'bio': ''})
        self.assertEqual(r.status_code, 302)
        self.student.refresh_from_db()
        self.assertEqual(self.student.first_name, 'Renamed')
        self.assertEqual(self.student.profile.city, 'Lahore')

    def test_user_edit_rejects_duplicate_email(self):
        r = self.client.post(reverse('adminpanel:user_detail', args=[self.student.pk]), {
            'first_name': 'X', 'last_name': '', 'email': 'other@t.com'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'already uses this email')

    def test_cannot_open_admin_user_detail(self):
        self.assertEqual(self.client.get(reverse('adminpanel:user_detail', args=[self.admin.pk])).status_code, 404)

    def test_review_delete_recalculates_course_rating(self):
        from core.models import Review
        a = Review.objects.create(course=self.course, user=self.student, rating=5, comment='great')
        Review.objects.create(course=self.course, user=self.other, rating=3, comment='ok')
        self.assertEqual(self.client.post(reverse('adminpanel:review_delete', args=[a.pk])).status_code, 302)
        self.course.refresh_from_db()
        self.assertEqual(float(self.course.rating_avg), 3.0)
        self.assertEqual(self.client.get(reverse('adminpanel:reviews'), {'status': '3'}).status_code, 200)

    def test_promo_edit_and_delete(self):
        promo = PromoCode.objects.get(code='JELLY10')
        self.client.post(reverse('adminpanel:promo_save', args=[promo.pk]), {'promo-code': 'jelly25', 'promo-percent_off': 25, 'promo-valid_until': ''})
        promo.refresh_from_db()
        self.assertEqual((promo.code, promo.percent_off), ('JELLY25', 25))
        self.client.post(reverse('adminpanel:promo_delete', args=[promo.pk]))
        self.assertFalse(PromoCode.objects.filter(pk=promo.pk).exists())

    def test_testimonial_edit(self):
        from core.models import Testimonial
        t = Testimonial.objects.create(name='A', quote='old')
        self.client.post(reverse('adminpanel:testimonial_save', args=[t.pk]), {'t-name': 'B', 't-quote': 'new'})
        t.refresh_from_db()
        self.assertEqual((t.name, t.quote, t.is_featured), ('B', 'new', False))

    def test_activity_feed_mixes_events(self):
        from adminpanel import stats
        self._paid()
        ContactSubmission.objects.create(name='Z', email='z@z.com', message='m')
        icons = {a['icon'] for a in stats.activity(20)}
        self.assertTrue({'person_add', 'payments', 'mail'} <= icons)


class TeacherDashboardDynamicTests(BaseCase):
    def setUp(self):
        self.login(self.teacher)

    def _paid(self, amount=50000):
        return Payment.objects.create(enrollment=self.enr, amount=amount, status='paid')

    def test_every_teacher_page_renders(self):
        self._paid()
        for name in ['dashboard', 'students', 'reviews', 'performance', 'announcements', 'courses', 'live', 'certificates']:
            self.assertEqual(self.client.get(reverse(f'teacher:{name}')).status_code, 200, name)

    def test_empty_teacher_shows_zeros_not_dummy_data(self):
        self.client.logout()
        self.login(make_user('brandnew@t.com', 'teacher'))
        r = self.client.get(reverse('teacher:dashboard'))
        self.assertEqual(r.context['k']['students']['value'], 0)
        self.assertEqual(r.context['k']['earnings']['value'], 0)
        self.assertContains(r, 'All clear')

    def test_kpis_only_count_my_own_courses(self):
        self._paid(50000)
        e2 = Enrollment.objects.create(user=self.other, course=self.course2, status='active')
        Payment.objects.create(enrollment=e2, amount=30000, status='paid')
        k = self.client.get(reverse('teacher:dashboard')).context['k']
        self.assertEqual(k['earnings']['value'], 50000)
        self.assertEqual(k['students']['value'], 1)
        self.assertEqual(k['courses']['value'], 1)

    def test_api_summary_live_and_teacher_only(self):
        self._paid(2500)
        self.assertEqual(self.client.get(reverse('teacher:api_summary')).json()['earnings'], 'Rs 2,500')
        self.client.logout()
        self.login(self.student)
        self.assertEqual(self.client.get(reverse('teacher:api_summary')).status_code, 302)

    def test_recording_badge_and_attention(self):
        past = timezone.now() - timedelta(days=1)
        LiveClass.objects.create(course=self.course, title='Done class', starts_at=past, duration_minutes=60, meeting_link='https://meet.google.com/a')
        r = self.client.get(reverse('teacher:dashboard'))
        self.assertEqual(r.context['k']['recordings']['value'], 1)
        self.assertContains(r, 'Upload the recording for')
        self.assertTrue(any(i.get('badge') == 1 for i in r.context['menu'] if i.get('url') == 'teacher:live'))

    def test_student_detail_scoped_to_teacher(self):
        self.assertEqual(self.client.get(reverse('teacher:student_detail', args=[self.enr.pk])).status_code, 200)
        e2 = Enrollment.objects.create(user=self.other, course=self.course2, status='active')
        self.assertEqual(self.client.get(reverse('teacher:student_detail', args=[e2.pk])).status_code, 404)

    def test_student_detail_shows_real_progress(self):
        from core.models import LectureProgress
        LectureProgress.objects.create(user=self.student, lecture=self.lec1, completed=True)
        r = self.client.get(reverse('teacher:student_detail', args=[self.enr.pk]))
        self.assertEqual(r.context['modules'][0]['done'], 1)
        self.assertContains(r, '50%')

    def test_students_filter_by_course_and_export(self):
        Enrollment.objects.create(user=self.other, course=self.course2, status='active')
        r = self.client.get(reverse('teacher:students'), {'course': self.course.slug})
        self.assertEqual([e.pk for e in r.context['page']], [self.enr.pk])
        csv_resp = self.client.get(reverse('teacher:students'), {'export': '1'})
        self.assertEqual(csv_resp['Content-Type'], 'text/csv')
        self.assertIn('student@t.com', csv_resp.content.decode())
        self.assertNotIn('other@t.com', csv_resp.content.decode())

    def test_performance_export_has_earnings(self):
        self._paid(50000)
        body = self.client.get(reverse('teacher:performance'), {'export': '1'}).content.decode()
        self.assertIn('UI Systems', body)
        self.assertIn('50000', body)
        self.assertNotIn('Other teacher course', body)

    def test_reviews_filters(self):
        from core.models import Review
        Review.objects.create(course=self.course, user=self.student, rating=5, comment='great')
        Review.objects.create(course=self.course, user=self.other, rating=2, comment='meh')
        Review.objects.create(course=self.course2, user=self.student, rating=1, comment='not mine')
        r = self.client.get(reverse('teacher:reviews'), {'rating': '2'})
        self.assertEqual([x.comment for x in r.context['page']], ['meh'])
        self.assertEqual(self.client.get(reverse('teacher:reviews')).context['total'], 2)

    def test_announcement_notifies_enrolled_students_only(self):
        Enrollment.objects.create(user=self.other, course=self.course2, status='active')
        r = self.client.post(reverse('teacher:announcements'), {'course': self.course.pk, 'batch': '', 'title': 'Hello', 'body': 'Class at 8'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Notification.objects.filter(title='Hello').count(), 1)
        self.assertTrue(Notification.objects.filter(title='Hello', user=self.student).exists())
        from core.models import Announcement
        self.assertEqual(Announcement.objects.get().recipients, 1)

    def test_cannot_announce_to_someone_elses_course(self):
        r = self.client.post(reverse('teacher:announcements'), {'course': self.course2.pk, 'title': 'x', 'body': 'y'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Notification.objects.filter(title='x').count(), 0)

    def test_pictures_follow_the_stock_photo_switch(self):
        from core.models import SiteSettings
        self.assertIn('picsum.photos/seed/jelly-user-', self.student.profile.avatar_url)              # original design: photo avatars
        self.assertIn('picsum.photos/seed/jelly-', self.course.image_url)
        site = SiteSettings.get(); site.use_stock_photos = False; site.save()
        self.assertTrue(self.student.profile.avatar_url.startswith('data:image/svg+xml'))             # switch off: initials and a plain placeholder
        self.assertTrue(self.course.image_url.endswith('course-placeholder.svg'))


class StudentDashboardDynamicTests(BaseCase):
    def setUp(self):
        self.login(self.student)

    def test_every_student_page_renders(self):
        for name in ['dashboard', 'courses', 'live', 'certificates', 'reviews', 'billing']:
            self.assertEqual(self.client.get(reverse(f'student:{name}')).status_code, 200, name)

    def test_new_student_sees_zeros_and_empty_states(self):
        self.client.logout()
        self.login(make_user('fresh@t.com', 'student'))
        r = self.client.get(reverse('student:dashboard'))
        self.assertEqual(r.context['k']['courses']['value'], 0)
        self.assertEqual(r.context['k']['lectures']['total'], 0)
        self.assertContains(r, 'all caught up')
        self.assertContains(r, 'You have no active courses')

    def test_kpis_follow_real_progress(self):
        from core.models import LectureProgress
        LectureProgress.objects.create(user=self.student, lecture=self.lec1, completed=True, completed_at=timezone.now())
        r = self.client.get(reverse('student:dashboard'))
        k = r.context['k']
        self.assertEqual((k['lectures']['done'], k['lectures']['total'], k['avg']['value'], k['streak']['value']), (1, 2, 50, 1))
        self.assertEqual(r.context['resume']['lecture'], self.lec2)
        self.assertEqual(r.context['activity']['total'], 1)

    def test_other_students_data_is_not_mixed_in(self):
        from core.models import LectureProgress
        LectureProgress.objects.create(user=self.other, lecture=self.lec1, completed=True, completed_at=timezone.now())
        self.assertEqual(self.client.get(reverse('student:dashboard')).context['k']['lectures']['done'], 0)

    def test_payment_due_shows_in_attention_and_badge(self):
        e2 = Enrollment.objects.create(user=self.student, course=self.course2, status='pending_payment')
        Payment.objects.create(enrollment=e2, amount=30000, status='pending')
        r = self.client.get(reverse('student:dashboard'))
        self.assertEqual(r.context['k']['due']['value'], 1)
        self.assertContains(r, 'Submit your payment')
        self.assertTrue(any(i.get('badge') == 1 for i in r.context['menu'] if i.get('url') == 'student:billing'))
        self.assertEqual(self.client.get(reverse('student:api_summary')).json()['due'], 1)

    def test_api_summary_student_only(self):
        self.assertEqual(self.client.get(reverse('student:api_summary')).status_code, 200)
        self.client.logout()
        self.login(self.teacher)
        self.assertEqual(self.client.get(reverse('student:api_summary')).status_code, 302)

    def test_announcements_reach_only_enrolled_students(self):
        from core.models import Announcement
        Announcement.objects.create(teacher=self.teacher, course=self.course, title='Class moved', body='8 pm', recipients=1)
        self.assertContains(self.client.get(reverse('student:dashboard')), 'Class moved')
        self.client.logout()
        self.login(self.other)
        self.assertNotContains(self.client.get(reverse('student:dashboard')), 'Class moved')

    def test_cancel_reservation_releases_seat(self):
        e2 = Enrollment.objects.create(user=self.student, course=self.course2, status='pending_payment')
        Payment.objects.create(enrollment=e2, amount=30000, status='pending')
        Course.objects.filter(pk=self.course2.pk).update(students_enrolled_count=1)
        self.client.post(reverse('student:cancel_reservation', args=[e2.pk]))
        e2.refresh_from_db()
        self.assertEqual(e2.status, 'cancelled')
        self.assertFalse(Payment.objects.filter(enrollment=e2).exists())
        self.course2.refresh_from_db()
        self.assertEqual(self.course2.students_enrolled_count, 0)

    def test_cannot_cancel_after_proof_was_sent(self):
        e2 = Enrollment.objects.create(user=self.student, course=self.course2, status='pending_payment')
        Payment.objects.create(enrollment=e2, amount=30000, status='pending', transaction_reference='TX1')
        self.client.post(reverse('student:cancel_reservation', args=[e2.pk]))
        e2.refresh_from_db()
        self.assertEqual(e2.status, 'pending_payment')

    def test_review_edit_and_delete_update_public_rating(self):
        from core.models import Review
        r = Review.objects.create(course=self.course, user=self.student, rating=5, comment='great')
        self.client.post(reverse('student:review_edit', args=[r.pk]), {'rating': 3, 'comment': 'ok now'})
        self.course.refresh_from_db()
        self.assertEqual(float(self.course.rating_avg), 3.0)
        self.assertContains(self.client.get(reverse('website:course_detail', args=[self.course.slug])), 'ok now')
        self.client.post(reverse('student:review_delete', args=[r.pk]))
        self.course.refresh_from_db()
        self.assertEqual(float(self.course.rating_avg), 0.0)

    def test_cannot_edit_someone_elses_review(self):
        from core.models import Review
        r = Review.objects.create(course=self.course, user=self.other, rating=4, comment='mine')
        self.assertEqual(self.client.get(reverse('student:review_edit', args=[r.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('student:review_delete', args=[r.pk])).status_code, 404)


class OriginalDesignIsRestored(TestCase):
    """The public site keeps the original design: its own copy, sections and photo-rich look. Everything is editable in Admin > Site
    content, and one switch turns the stock photos off."""
    PAGES = ['website:home', 'website:about', 'website:contact', 'website:courses', 'website:blog', 'accounts:login', 'accounts:register']

    def test_original_copy_is_in_place_on_a_new_install(self):
        home = self.client.get(reverse('website:home')).content.decode()
        for text in ['Welcome to The Jelly Productions', 'Learn a skill the way you’d learn it from someone who’s done it.', 'Capped at 18 students',
                     '14-day honest window', 'Explore courses', 'Meet your instructor', 'Arshad Nadeem']:
            self.assertIn(text, home)
        about = self.client.get(reverse('website:about')).content.decode()
        for text in ['Most courses are built to be sold. This one isn’t.', 'Founder note', 'Founder &amp; Instructor, The Jelly Productions']:
            self.assertIn(text, about)
        contact = self.client.get(reverse('website:contact')).content.decode()
        for text in ['hello@thejellyproductions.com', '+92 321 1234567', 'Within one business day, most often same-day']:
            self.assertIn(text, contact)

    def test_sections_without_rows_are_hidden_so_pages_never_look_broken(self):
        home = self.client.get(reverse('website:home')).content.decode()
        for hidden in ['Student voices', 'Latest articles', 'Our alumni now build things at', 'stat-band']:
            self.assertNotIn(hidden, home)
        self.assertNotIn('How we got here', self.client.get(reverse('website:about')).content.decode())
        for name in self.PAGES:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_sample_content_restores_the_complete_original_pages(self):
        from core.sample_content import load_sample_content
        made = load_sample_content()
        self.assertEqual((made['HeroSlide'], made['Partner'], made['TeamMember'], made['GalleryImage']), (3, 5, 3, 6))
        home = self.client.get(reverse('website:home')).content.decode()
        self.assertEqual(home.count('class="carousel-item'), 3)                       # three-photo hero carousel
        self.assertIn('picsum.photos/seed/fieldnote-hero-1/1600/900', home)
        for name in ['Northwind', 'Palette', 'Ridgeline', 'Kettlebell', 'Verso', 'Marcus Bell', 'Halima Yusuf', 'Latest articles' if False else 'From the notebook']:
            self.assertIn(name, home, name)
        about = self.client.get(reverse('website:about')).content.decode()
        for text in ['How we got here', 'A short, honest timeline', '2,400+ students, still one instructor', 'A look inside', 'Lena Wu', 'Tomás Reyes',
                     'Who keeps it running', 'Is it really just you teaching everything?']:
            self.assertIn(text, about, text)
        self.assertEqual(about.count('fieldnote-gallery-'), 6)
        courses = self.client.get(reverse('website:courses')).content.decode()
        self.assertIn('Before you pick a course', courses)
        self.assertIn('Owen Chase', courses)
        contact = self.client.get(reverse('website:contact')).content.decode()
        self.assertIn('Hania Malik', contact)
        self.assertIn('A few quick answers', contact)

    def test_loading_twice_never_duplicates_and_never_touches_your_rows(self):
        from core.models import Partner
        from core.sample_content import load_sample_content
        Partner.objects.create(name='My Own Company')
        made = load_sample_content()
        self.assertNotIn('Partner', made)                       # that list already had a row of yours
        self.assertEqual(Partner.objects.count(), 1)
        from core.models import HeroSlide
        self.assertEqual(HeroSlide.objects.count(), 3)          # the first call filled every other (empty) list
        self.assertEqual(load_sample_content(), {})             # the second call finds nothing empty: no duplicates

    def test_removing_sample_content_keeps_what_you_added(self):
        from core.models import Faq, Partner
        from core.sample_content import clear_sample_content, load_sample_content
        load_sample_content()
        Partner.objects.create(name='My Own Company')
        Faq.objects.create(page='about', question='My own question?', answer='Yes.')
        gone = clear_sample_content()
        self.assertGreater(sum(gone.values()), 20)
        self.assertEqual(list(Partner.objects.values_list('name', flat=True)), ['My Own Company'])
        about = self.client.get(reverse('website:about')).content.decode()
        self.assertIn('My own question?', about)
        self.assertNotIn('Is it really just you teaching everything?', about)
        self.assertNotIn('picsum.photos/seed/fieldnote-gallery', about)

    def test_stock_photo_switch_removes_every_stock_picture(self):
        from core.models import Course, SiteSettings, Testimonial
        from core.sample_content import load_sample_content
        load_sample_content()
        teacher = make_user('t@x.com', 'teacher')
        Course.objects.create(title='Pic Course', slug='pic-course', teacher=teacher, price=1, original_price=2, duration_weeks=1, hours_per_week=1, is_published=True)
        for name in self.PAGES + ['website:course_detail']:
            args = ['pic-course'] if name == 'website:course_detail' else []
            self.assertIn('picsum.photos', self.client.get(reverse(name, args=args)).content.decode(), name) if name in ('website:home', 'website:courses') else None
        site = SiteSettings.get()
        site.use_stock_photos = False
        site.save()
        for name in self.PAGES + ['website:course_detail']:
            args = ['pic-course'] if name == 'website:course_detail' else []
            body = self.client.get(reverse(name, args=args)).content.decode()
            self.assertNotIn('picsum.photos', body, name)
        self.assertEqual(self.client.get(reverse('website:home')).content.decode().count('class="carousel-item'), 1)      # plain colour header
        self.assertTrue(Testimonial.objects.first().avatar_url.startswith('data:image/svg+xml'))
        self.assertTrue(Course.objects.get(slug='pic-course').image_url.endswith('course-placeholder.svg'))

    def test_instructor_claims_follow_the_real_number_of_teachers(self):
        from core.models import Course
        t1 = make_user('one@x.com', 'teacher'); t2 = make_user('two@x.com', 'teacher')
        for i, t in enumerate((t1, t2)):
            Course.objects.create(title=f'C{i}', slug=f'c{i}', teacher=t, price=1, original_price=2, duration_weeks=1, hours_per_week=1, is_published=True)
        home = self.client.get(reverse('website:home')).content.decode()
        self.assertIn('2 Instructors', home)
        self.assertNotIn('One Instructor', home)


class PublicSiteIsLinkedToDashboards(BaseCase):
    def test_site_settings_drive_public_pages(self):
        from core.models import SiteSettings
        s = SiteSettings.get()
        s.site_name, s.email, s.phone, s.address = 'Acme Academy', 'hi@acme.test', '0300-1112223', 'Karachi'
        s.hero_title, s.hero_text, s.batch_cap, s.refund_days = 'Build with Acme', 'Hero words here', 12, 7
        s.about_title, s.founder_name, s.founder_quote = 'About Acme', 'Sara Khan', 'We care'
        s.save()
        home = self.client.get(reverse('website:home')).content.decode()
        for t in ['Acme Academy', 'Build with Acme', 'Hero words here', 'capped at 12', '7 days', 'Sara Khan']:
            self.assertIn(t, home)
        contact = self.client.get(reverse('website:contact')).content.decode()
        for t in ['hi@acme.test', '0300-1112223', 'Karachi']:
            self.assertIn(t, contact)
        self.assertIn('About Acme', self.client.get(reverse('website:about')).content.decode())

    def test_lists_appear_only_when_added(self):
        from core.models import Faq, Milestone, Partner
        about = lambda: self.client.get(reverse('website:about')).content.decode()
        self.assertNotIn('How we got here', about())
        Partner.objects.create(name='Globex')
        Milestone.objects.create(year='2024', title='First batch')
        Faq.objects.create(page='about', question='Is it live?', answer='Yes it is')
        Faq.objects.create(page='contact', question='Contact only?', answer='yes')
        body = about()
        for t in ['Globex', 'First batch', 'Is it live?']:
            self.assertIn(t, body)
        self.assertNotIn('Contact only?', body)
        self.assertIn('Contact only?', self.client.get(reverse('website:contact')).content.decode())

    def test_stat_band_and_ticker_use_real_numbers(self):
        from core.site import live_stats, ticker_items
        labels = {s['label'] for s in live_stats()}
        self.assertTrue(labels & {'student learning with us', 'students learning with us'})        # 'student' when there is exactly one
        self.assertTrue(any('Next batch: UI Systems' in t for t in ticker_items()))
        self.assertTrue(any('Save up to' in t for t in ticker_items()))     # 50000 vs 70000 original

    def test_admin_blog_and_content_screens_publish_to_site(self):
        self.login(self.admin)
        r = self.client.post(reverse('adminpanel:blog_new'), {
            'title': 'Launch day', 'category': 'news', 'author_name': '', 'excerpt': 'We launched', 'body': 'Big **news**',
            'is_published': 'on', 'published_at': timezone.localtime().strftime('%Y-%m-%dT%H:%M')})
        self.assertEqual(r.status_code, 302)
        self.assertContains(self.client.get(reverse('website:blog')), 'Launch day')
        self.assertContains(self.client.get(reverse('website:blog_detail', args=['launch-day'])), '<strong>news</strong>')
        self.assertContains(self.client.get(reverse('website:home')), 'Launch day')
        post = __import__('core.models', fromlist=['BlogPost']).BlogPost.objects.get(slug='launch-day')
        self.client.post(reverse('adminpanel:blog_toggle', args=[post.pk]))
        self.assertEqual(self.client.get(reverse('website:blog_detail', args=['launch-day'])).status_code, 404)

    def test_admin_content_page_saves_and_adds_items(self):
        self.login(self.admin)
        for name in ['content', 'blog', 'blog_new']:
            self.assertEqual(self.client.get(reverse(f'adminpanel:{name}')).status_code, 200, name)
        from core.models import Faq, Partner, SiteSettings
        self.client.post(reverse('adminpanel:content'), {'site_name': 'Zeta School', 'email': 'z@z.test'})
        self.assertEqual(SiteSettings.get().site_name, 'Zeta School')
        self.client.post(reverse('adminpanel:content_item_add', args=['partner']), {'partner-name': 'Initech', 'partner-sort_order': 1})
        self.assertTrue(Partner.objects.filter(name='Initech').exists())
        self.client.post(reverse('adminpanel:content_item_add', args=['faq']), {'faq-page': 'all', 'faq-question': 'Q?', 'faq-answer': 'A.', 'faq-sort_order': 0})
        f = Faq.objects.get()
        self.client.post(reverse('adminpanel:content_item_delete', args=['faq', f.pk]))
        self.assertFalse(Faq.objects.exists())
        self.assertEqual(self.client.post(reverse('adminpanel:content_item_add', args=['nope']), {}).status_code, 404)

    def test_non_admin_cannot_open_content_screens(self):
        self.login(self.teacher)
        for name in ['content', 'blog', 'blog_new']:
            self.assertEqual(self.client.get(reverse(f'adminpanel:{name}')).status_code, 302)

    def test_header_links_each_role_to_its_own_dashboard(self):
        for user, path in [(self.student, '/student/'), (self.teacher, '/teacher/'), (self.admin, '/admin-panel/')]:
            self.client.force_login(user)
            self.assertContains(self.client.get(reverse('website:home')), f'href="{path}"')
        self.client.logout()

    def test_full_lifecycle_public_to_student_teacher_admin(self):
        from core.models import LectureProgress
        student = make_user('newbie@t.com', 'student')
        # 1) public enrol form -> pending payment visible to admin
        self.client.force_login(student)
        r = self.client.post(reverse('website:enroll'), {'course': self.course.pk, 'batch': self.batch.pk, 'phone': '0300 1234567', 'city': 'Lahore',
                             'gateway': 'easypaisa', 'transaction_reference': 'TX900', 'terms': 'on'})
        self.assertEqual(r.status_code, 302)
        enr = Enrollment.objects.get(user=student, course=self.course)
        self.assertEqual(enr.status, 'pending_payment')
        self.assertEqual(self.client.get(reverse('student:api_summary')).json()['due'], 0)       # proof sent: nothing left to do
        self.assertContains(self.client.get(reverse('student:dashboard')), 'is being reviewed')
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('adminpanel:api_summary')).json()['pending'], 1)
        self.assertContains(self.client.get(reverse('adminpanel:payments')), 'newbie@t.com')
        # 2) admin approves -> student unlocked, teacher sees student, public numbers move
        pay = Payment.objects.get(enrollment=enr)
        self.client.post(reverse('adminpanel:payment_review', args=[pay.pk]), {'action': 'approve'})
        self.client.force_login(student)
        self.assertEqual(self.client.get(reverse('student:player', args=[self.course.slug])).status_code, 200)
        self.assertEqual(self.client.get(reverse('student:dashboard')).context['k']['courses']['value'], 1)
        self.assertContains(self.client.get(reverse('website:course_detail', args=[self.course.slug])), 'Go to my course')
        self.client.force_login(self.teacher)
        self.assertIn(student.email, self.client.get(reverse('teacher:students')).content.decode())
        self.assertEqual(self.client.get(reverse('teacher:api_summary')).json()['students'], 2)
        # 3) student learns and reviews -> teacher, admin and public page all show it
        self.client.force_login(student)
        LectureProgress.objects.create(user=student, lecture=self.lec1, completed=True, completed_at=timezone.now())
        self.client.post(reverse('website:submit_review', args=[self.course.slug]), {'rating': 5, 'comment': 'Brilliant lessons'})
        self.assertContains(self.client.get(reverse('website:course_detail', args=[self.course.slug])), 'Brilliant lessons')
        for user, name in [(self.teacher, 'teacher:reviews'), (self.admin, 'adminpanel:reviews')]:
            self.client.force_login(user)
            self.assertContains(self.client.get(reverse(name)), 'Brilliant lessons')
        # 4) public contact + newsletter land in the admin inbox
        self.client.logout()
        self.client.post(reverse('website:contact'), {'name': 'Visitor', 'email': 'v@v.test', 'topic': 'Something else', 'message': 'Hello admin'})
        self.client.post(reverse('website:newsletter_subscribe'), {'email': 'sub@v.test'})
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('adminpanel:contact')), 'Hello admin')
        self.assertContains(self.client.get(reverse('adminpanel:newsletter')), 'sub@v.test')
        self.assertEqual(self.client.get(reverse('adminpanel:api_summary')).json()['messages'], 1)

    def test_teacher_publishing_a_course_puts_it_on_the_site(self):
        self.login(self.teacher)
        Course.objects.filter(pk=self.course.pk).update(is_published=False)
        self.assertNotContains(self.client.get(reverse('website:courses')), self.course.get_absolute_url())
        Course.objects.filter(pk=self.course.pk).update(is_published=True)
        self.assertContains(self.client.get(reverse('website:courses')), self.course.get_absolute_url())
        about = self.client.get(reverse('website:about')).content.decode()
        self.assertIn('Who keeps it running', about)
        self.assertIn(self.teacher.get_full_name(), about)    # the real teacher account is on the About page
