"""Demo data for development:  python manage.py seed_data   (add --force to re-seed)

Logins created (change these before going live):
  admin    admin@thejellyproductions.com   / admin12345
  teacher  arshad@thejellyproductions.com  / teacher12345
  student  ayesha@example.com              / student12345   (and 7 more students)

To remove all of this again and start with your own data:  python manage.py clean_demo_data --yes --reset-site
"""
import io
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import (Batch, Certificate, ClassRecording, ContactSubmission, Course, CurriculumLecture,
                         CurriculumModule, Enrollment, LectureProgress, LiveClass, NewsletterSubscriber, Notification,
                         Payment, PaymentAccountInfo, PromoCode, Review)

User = get_user_model()

COURSES = [  # level, title, weeks, price, original, featured
    ('beginner', 'UI Systems: from wireframe to shipped screen', 8, 49999, 69999, True),
    ('intermediate', 'Design Systems at Scale', 6, 64999, 89999, True),
    ('beginner', 'Portfolio Sprint: ship in 4 weeks', 4, 39999, 54999, True),
    ('beginner', 'Research foundations: talk to users without dread', 5, 34999, 49999, False),
    ('advanced', 'Motion & prototyping that feels real', 5, 56999, 79999, False),
    ('intermediate', 'Dev handoff: specs engineers actually thank you for', 4, 45999, 64999, False),
    ('advanced', 'Leading design critique without the ego', 6, 59999, 84999, False),
    ('beginner', 'Figma fundamentals for total beginners', 3, 29999, 42999, False),
]
MODULES = {
    0: [('Wireframes that answer questions', [('Why wireframes fail', '12 min'), ('Flows before screens', '18 min'), ('Wireframe review task', 'Task')]),
        ('Layout and spacing', [('Building a 4pt scale', '15 min'), ('Grids in Figma', '20 min'), ('Responsive layout', '17 min')]),
        ('Type and colour', [('Type scales that hold up', '14 min'), ('Colour tokens', '19 min')]),
        ('Components', [('Auto layout in depth', '22 min'), ('Variants and states', '18 min')]),
        ('Shipping the screen', [('Dev-ready handoff', '16 min'), ('Final critique', 'Live')])],
}
STUDENTS = ['Ayesha Khan', 'Hamza Riaz', 'Sana Malik', 'Bilal Ahmed', 'Mina Farooq', 'Usman Tariq', 'Zara Hussain', 'Daniyal Shah']


def png_certificate(name, course):
    from PIL import Image, ImageDraw
    img = Image.new('RGB', (1000, 700), '#FFF8E6')
    d = ImageDraw.Draw(img)
    d.rectangle([24, 24, 976, 676], outline='#1B5FA8', width=6)
    d.rectangle([44, 44, 956, 656], outline='#FFC94A', width=2)
    for y, text in ((140, 'Certificate of Completion'), (260, 'This certifies that'), (330, name), (420, 'has successfully completed'),
                    (480, course), (600, 'The Jelly Productions')):
        w = d.textlength(text)
        d.text(((1000 - w) / 2, y), text, fill='#0E3A63')
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return ContentFile(buf.getvalue(), name='certificate.png')


class Command(BaseCommand):
    help = 'Create demo users, courses, classes and payments.'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true', help='Seed even if courses already exist')
        parser.add_argument('--allow-production', action='store_true', help='Really seed although DJANGO_DEBUG is off (creates accounts with PUBLIC passwords)')

    def handle(self, *args, **opts):
        if not settings.DEBUG and not opts['allow_production']:
            raise CommandError('seed_data creates an admin account with a publicly known password. Run it only with DJANGO_DEBUG=1 on a development '
                               'machine. (Pass --allow-production only if you understand that and will change the passwords at once.)')
        if Course.objects.exists() and not opts['force']:
            self.stdout.write('Already seeded. Use --force to add demo data anyway.')
            return
        now = timezone.now()

        admin, made = User.objects.get_or_create(username='admin@thejellyproductions.com', defaults={
            'email': 'admin@thejellyproductions.com', 'first_name': 'Site', 'last_name': 'Admin', 'is_superuser': True, 'is_staff': True})
        if made:                       # an account that already exists keeps its own password
            admin.set_password('admin12345'); admin.save()
        admin.profile.role = 'admin'; admin.profile.save()

        teacher, made = User.objects.get_or_create(username='arshad@thejellyproductions.com', defaults={
            'email': 'arshad@thejellyproductions.com', 'first_name': 'Arshad', 'last_name': 'Nadeem'})
        if made:
            teacher.set_password('teacher12345'); teacher.save()
        tp = teacher.profile
        tp.role, tp.title = 'teacher', 'Senior Product Designer, Ridgeline'
        tp.bio = 'Arshad still ships product work every week and teaches every batch himself. He reviews your actual files.'
        tp.save()

        students = []
        for i, full in enumerate(STUDENTS):
            first, last = full.split()
            email = f'{first.lower()}@example.com'
            u, made = User.objects.get_or_create(username=email, defaults={'email': email, 'first_name': first, 'last_name': last})
            if made:
                u.set_password('student12345'); u.save()
            u.profile.city, u.profile.phone = 'Lahore', f'0300 123456{i}'
            u.profile.save()
            students.append(u)

        courses = []
        for i, (level, title, weeks, price, orig, feat) in enumerate(COURSES):
            c = Course.objects.create(
                teacher=teacher, title=title, level=level, price=price, original_price=orig, duration_weeks=weeks, is_featured=feat,
                subtitle='One real project, a written review every week, and a small live batch.',
                description='Go from a rough idea to something you can ship. You work on one real project across the whole course and get a review from your instructor every week.\n\nNo prior experience is needed for beginner courses, just a laptop and time you can protect.',
                preview_video_id='dQw4w9WgXcQ' if i == 0 else '')
            courses.append(c)
            for k in range(2):
                Batch.objects.create(course=c, start_date=(now + timedelta(days=14 + 14 * k + i)).date(), seats_total=18,
                                     seats_filled=11 if k == 0 else 3, status='open', schedule_label='Mon & Thu, 7:00 pm PKT' if k == 0 else 'Saturdays, 5:00 pm PKT',
                                     meeting_link='https://meet.google.com/jel-lyuk-abc')
            for m_i, (mt, lectures) in enumerate(MODULES.get(i, [('Getting started', [('Welcome', '8 min'), ('Project brief', '12 min')]), ('Core skills', [('Lesson one', '15 min'), ('Lesson two', '18 min')]), ('Ship it', [('Final review', 'Live')])])):
                mod = CurriculumModule.objects.create(course=c, title=mt, sort_order=m_i)
                for l_i, (lt, dur) in enumerate(lectures):
                    CurriculumLecture.objects.create(module=mod, title=lt, duration_label=dur, sort_order=l_i,
                                                     type='project' if dur == 'Task' else 'live_review' if dur == 'Live' else 'video')

        # enrollments + payments
        ayesha = students[0]
        for idx in (0, 7, 2):
            c = courses[idx]
            e = Enrollment.objects.create(user=ayesha, course=c, batch=c.batches.first(), status='active' if idx != 2 else 'pending_payment')
            Payment.objects.create(enrollment=e, amount=c.price, gateway='easypaisa', transaction_reference=f'TX{880 + idx}',
                                   status='paid' if idx != 2 else 'pending')
        for lec in CurriculumLecture.objects.filter(module__course=courses[0])[:4]:
            LectureProgress.objects.create(user=ayesha, lecture=lec, completed=True, completed_at=now)
        for j, u in enumerate(students[1:6], start=1):
            c = courses[j % 4]
            e = Enrollment.objects.create(user=u, course=c, batch=c.batches.first(), status='active' if j % 2 else 'pending_payment')
            Payment.objects.create(enrollment=e, amount=c.price, gateway=('jazzcash', 'bank', 'easypaisa')[j % 3],
                                   transaction_reference=f'TX{990 + j}', status='paid' if j % 2 else 'pending')

        # live classes: some upcoming, two finished (one with a recording)
        link = 'https://meet.google.com/jel-lyuk-abc'
        def klass(course, title, days, hour, dur, platform='meet', url=link):
            start = (now + timedelta(days=days)).astimezone().replace(hour=hour, minute=0, second=0, microsecond=0)
            return LiveClass.objects.create(course=course, title=title, starts_at=start, duration_minutes=dur,
                                            platform=platform, meeting_link=url, created_by=teacher)
        klass(courses[0], 'Weekly critique', 1, 19, 90)
        klass(courses[0], 'Design tokens clinic', 4, 19, 60, 'zoom', 'https://zoom.us/j/1234567890')
        klass(courses[7], 'Figma Q&A', 2, 20, 60, url='')
        klass(courses[2], 'Portfolio review', 5, 19, 90)
        old_with = klass(courses[0], 'Spacing and grids workshop', -3, 19, 90)
        klass(courses[0], 'Design tokens Q&A', -1, 20, 60)
        ClassRecording.objects.create(live_class=old_with, video_url='https://www.youtube.com/watch?v=dQw4w9WgXcQ', uploaded_by=teacher)

        Certificate.objects.create(student=ayesha, course=courses[7], issued_by=teacher, file=png_certificate('Ayesha Khan', courses[7].title))

        # reviews, testimonials, stats
        for c, n, role, txt in [(courses[0], 'Marcus Bell', 'UI Systems, batch 14', 'Arshad caught three things in my first submission that no YouTube tutorial ever mentioned.'),
                                (courses[2], 'Halima Yusuf', 'Portfolio Sprint, batch 9', 'A real person waiting on my Friday submission made all the difference.')]:
            Review.objects.create(course=c, author_name=n, author_role_label=role, rating=5, comment=txt)
        # the design's own sample content (hero pictures, partners, timeline, team, gallery, FAQs, quotes, testimonials, blog posts):
        # loaded into any list that is still empty, as editable rows (Admin > Site content)
        from core.sample_content import load_sample_content
        load_sample_content()

        PaymentAccountInfo.objects.all().delete()
        PaymentAccountInfo.objects.create(easypaisa_number='0300 1234567', easypaisa_account_name='The Jelly Productions', jazzcash_number='0321 1234567',
                                          jazzcash_account_name='The Jelly Productions', bank_name='Meezan Bank', bank_account_title='The Jelly Productions',
                                          bank_account_number='0123 4567 8901', instructions='Please add your name in the transfer note.')
        PromoCode.objects.get_or_create(code='JELLY10', defaults={'percent_off': 10})

        for n, e, t in [('Usman Tariq', 'usman@example.com', 'A specific course or batch date'), ('Mina Farooq', 'mina@example.com', 'Refunds or billing'), ('Zara Hussain', 'zara@example.com', 'Corporate / team training')]:
            ContactSubmission.objects.create(name=n, email=e, topic=t, message='Hello, I have a question about the next batch.')
        for e in ['areeba', 'faizan', 'hira', 'ibrahim', 'javeria']:
            NewsletterSubscriber.objects.get_or_create(email=f'{e}@example.com')
        Notification.objects.create(user=ayesha, title='Payment approved', body='Your payment for UI Systems was approved.', url='/student/billing/')
        Notification.objects.create(user=teacher, title='New student joined your batch', body='Hamza Riaz joined.', url='/teacher/students/')
        self.stdout.write(self.style.SUCCESS('Demo data created. See the docstring of this command for logins.'))
