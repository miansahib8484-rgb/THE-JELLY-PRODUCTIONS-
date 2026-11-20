"""Shared domain models used by the public site and all three dashboards.

Blog posts are NOT stored here: they are Markdown files (see website/blog.py).
"""
import re
import uuid

from datetime import timedelta
from html import escape as _esc

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.text import slugify

from .storage import PrivateStorage

User = settings.AUTH_USER_MODEL


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
def stock_photo(seed, width, height):
    """A stock photo (picsum.photos, the pictures the original design shipped with). The same seed always gives the same picture."""
    return f'https://picsum.photos/seed/{seed}/{width}/{height}'


class HttpsURLField(models.URLField):
    """URL field whose form input assumes https:// when a link is typed without a scheme (the Django 6 default)."""

    def formfield(self, **kwargs):
        kwargs.setdefault('assume_scheme', 'https')
        return super().formfield(**kwargs)


def new_certificate_code():
    """Short, unambiguous, unguessable (no 0/O/1/I): e.g. JP-7K3Q9XAB"""
    import secrets
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    return 'JP-' + ''.join(secrets.choice(alphabet) for _ in range(8))


def initials_avatar(name, seed=0):
    """A small inline SVG with the person's initials: no external image service, nothing random."""
    from urllib.parse import quote
    parts = [w for w in (name or '?').replace('@', ' ').split() if w]
    letters = _esc(((parts[0][0] + (parts[1][0] if len(parts) > 1 else '')) if parts else '?').upper())   # '&' or '<' must not break the SVG
    bg = ['#1B5FA8', '#17A360', '#E0A916', '#D6453B', '#7B5EA7', '#0E8F9E'][seed % 6]
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120"><rect width="120" height="120" fill="{bg}"/>'
           f'<text x="60" y="60" dy=".35em" text-anchor="middle" font-family="Arial,sans-serif" font-size="46" font-weight="700" fill="#fff">{letters}</text></svg>')
    return 'data:image/svg+xml;utf8,' + quote(svg)



class Profile(models.Model):
    ROLE_CHOICES = [('student', 'Student'), ('teacher', 'Teacher'), ('admin', 'Admin')]
    EXPERIENCE_CHOICES = [('beginner', 'Complete beginner'), ('some', 'Some experience'), ('working', 'Working designer')]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='student')
    phone = models.CharField(max_length=30, blank=True)
    city = models.CharField(max_length=80, blank=True)
    country = models.CharField(max_length=80, blank=True, default='Pakistan')
    date_of_birth = models.DateField(null=True, blank=True)
    occupation = models.CharField(max_length=80, blank=True)
    experience_level = models.CharField(max_length=20, choices=EXPERIENCE_CHOICES, blank=True)
    goal = models.TextField(blank=True)
    heard_from = models.CharField(max_length=60, blank=True)
    bio = models.TextField(blank=True)
    title = models.CharField(max_length=120, blank=True, help_text='e.g. Senior Product Designer')
    avatar = models.ImageField(upload_to='avatars/', blank=True)
    wants_newsletter = models.BooleanField(default=True)
    interested_course = models.ForeignKey('Course', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    must_change_password = models.BooleanField(default=False, help_text='Only when an administrator asked the person to choose their own password at the first sign-in')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.user.get_username()} ({self.role})'

    @property
    def avatar_url(self):
        if self.avatar:
            return self.avatar.url
        if SiteSettings.stock_enabled():
            return stock_photo(f'jelly-user-{self.user_id}', 120, 120)           # the original design's avatars
        return initials_avatar(self.user.get_full_name() or self.user.get_username(), self.user_id or 0)


# ---------------------------------------------------------------------------
# Courses
# ---------------------------------------------------------------------------
class Course(models.Model):
    LEVEL_CHOICES = [('beginner', 'Beginner'), ('intermediate', 'Intermediate'), ('advanced', 'Advanced')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    teacher = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='courses_taught')
    title = models.CharField(max_length=200)
    subtitle = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default='beginner')
    category = models.CharField(max_length=100, default='Product Design')
    price = models.DecimalField(max_digits=10, decimal_places=0, help_text='Price in PKR')
    original_price = models.DecimalField(max_digits=10, decimal_places=0, null=True, blank=True,
                                         help_text='Shown with a line through it')
    duration_weeks = models.PositiveIntegerField(default=4)
    hours_per_week = models.PositiveIntegerField(default=4)
    thumbnail = models.ImageField(upload_to='courses/', blank=True, help_text='Shown on the website course cards (16:10 works best)')
    thumbnail_url = HttpsURLField(max_length=500, blank=True, help_text='Used only if no image is uploaded')
    preview_video_id = models.CharField(max_length=50, blank=True, help_text='YouTube video ID')
    rating_avg = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    students_enrolled_count = models.PositiveIntegerField(default=0)
    is_published = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['is_published', 'teacher'], name='course_pub_teacher_idx')]
        constraints = [models.CheckConstraint(condition=models.Q(price__gte=0) & models.Q(original_price__gte=0), name='course_prices_not_negative')]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title)[:120] or 'course'
            slug, n = base, 2
            while Course.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug, n = f'{base}-{n}', n + 1
            self.slug = slug
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse('website:course_detail', kwargs={'slug': self.slug})

    @property
    def image_url(self):
        if self.thumbnail:
            return self.thumbnail.url
        if self.thumbnail_url:
            return self.thumbnail_url
        if SiteSettings.stock_enabled():
            return stock_photo(f'jelly-{self.slug}', 800, 500)          # the original design's course photos
        from django.templatetags.static import static
        return static('img/course-placeholder.svg')

    @property
    def discount_percent(self):
        if self.original_price and self.original_price > self.price:
            return round((1 - self.price / self.original_price) * 100)
        return 0

    @cached_property
    def review_count(self):
        return self.reviews.count()

    @property
    def instructor_name(self):
        t = self.teacher
        return (t.get_full_name() or t.get_username()) if t else 'The Jelly Productions team'

    @property
    def instructor_title(self):
        t = self.teacher
        return t.profile.title if t and hasattr(t, 'profile') else ''

    @property
    def instructor_bio(self):
        t = self.teacher
        return t.profile.bio if t and hasattr(t, 'profile') else ''

    @property
    def next_batch(self):
        return self.batches.filter(status__in=['upcoming', 'open']).order_by('start_date').first()


class CurriculumModule(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='modules')
    title = models.CharField(max_length=200)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'id']

    def __str__(self):
        return f'{self.course.title}: {self.title}'


class CurriculumLecture(models.Model):
    TYPE_CHOICES = [('video', 'Video'), ('project', 'Project'), ('live_review', 'Live review')]

    module = models.ForeignKey(CurriculumModule, on_delete=models.CASCADE, related_name='lectures')
    title = models.CharField(max_length=255)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='video')
    duration_label = models.CharField(max_length=30, default='10 min')
    video_url = HttpsURLField(max_length=500, blank=True, help_text='YouTube / Vimeo / Drive link (optional)')
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'id']

    def __str__(self):
        return self.title

    @property
    def embed_url(self):
        return embed_from_url(self.video_url)


class Batch(models.Model):
    STATUS_CHOICES = [('upcoming', 'Upcoming'), ('open', 'Open'), ('full', 'Full'), ('closed', 'Closed')]

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='batches')
    start_date = models.DateField()
    schedule_label = models.CharField(max_length=120, blank=True, help_text='e.g. Mon & Thu, 7:00 pm PKT')
    seats_total = models.PositiveIntegerField(default=18)
    seats_filled = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    meeting_link = HttpsURLField(max_length=500, blank=True, help_text='Default Meet/Zoom link for this batch')

    class Meta:
        ordering = ['start_date']
        verbose_name_plural = 'batches'
        indexes = [models.Index(fields=['course', 'status'], name='batch_course_status_idx')]
        constraints = [models.CheckConstraint(condition=models.Q(seats_total__gte=1) & models.Q(seats_filled__gte=0), name='batch_seats_valid')]

    def __str__(self):
        return f'{self.course.title}: starts {self.start_date}'

    @property
    def seats_left(self):
        return max(self.seats_total - self.seats_filled, 0)

    @property
    def label(self):
        base = self.start_date.strftime('Starts %b %d')
        return f'{base} ({self.schedule_label})' if self.schedule_label else base


class LectureProgress(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='lecture_progress')
    lecture = models.ForeignKey(CurriculumLecture, on_delete=models.CASCADE, related_name='progress_entries')
    completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [('user', 'lecture')]

    def __str__(self):
        return f'{self.user_id} / {self.lecture_id}: {"done" if self.completed else "open"}'


# ---------------------------------------------------------------------------
# Enrollment, payments
# ---------------------------------------------------------------------------
class Enrollment(models.Model):
    STATUS_CHOICES = [('pending_payment', 'Pending payment'), ('active', 'Active'), ('completed', 'Completed'),
                      ('cancelled', 'Cancelled'), ('refunded', 'Refunded')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name='enrollments')
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name='enrollments')
    batch = models.ForeignKey(Batch, on_delete=models.SET_NULL, null=True, blank=True, related_name='enrollments')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending_payment')
    notes = models.TextField(blank=True, help_text='What the student wrote when reserving the seat')
    enrolled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-enrolled_at']
        unique_together = [('user', 'course', 'batch')]
        indexes = [models.Index(fields=['course', 'status'], name='enr_course_status_idx'),
                   models.Index(fields=['user', 'status'], name='enr_user_status_idx'),
                   models.Index(fields=['batch', 'status'], name='enr_batch_status_idx')]

    def __str__(self):
        return f'{self.user} -> {self.course} ({self.status})'

    @property
    def is_learning(self):
        return self.status in ('active', 'completed')

    @property
    def progress_pct(self):
        if hasattr(self, '_progress_cache'):          # filled in bulk by core.progress.attach_progress for list pages
            return self._progress_cache
        total = CurriculumLecture.objects.filter(module__course=self.course).count()
        if not total:
            return 0
        done = LectureProgress.objects.filter(user=self.user, completed=True, lecture__module__course=self.course).count()
        return round(done / total * 100)


class PaymentAccountInfo(models.Model):
    """Business payment details shown to students (single row, edited in Admin > Settings)."""
    easypaisa_number = models.CharField(max_length=20, blank=True)
    easypaisa_account_name = models.CharField(max_length=120, blank=True)
    jazzcash_number = models.CharField(max_length=20, blank=True)
    jazzcash_account_name = models.CharField(max_length=120, blank=True)
    bank_name = models.CharField(max_length=120, blank=True)
    bank_account_title = models.CharField(max_length=120, blank=True)
    bank_account_number = models.CharField(max_length=40, blank=True)
    bank_iban = models.CharField(max_length=40, blank=True)
    instructions = models.TextField(blank=True)

    class Meta:
        verbose_name = verbose_name_plural = 'Payment account info'

    def __str__(self):
        return 'Payment account details'

    @classmethod
    def get(cls):
        return cls.objects.first() or cls.objects.create()



class PromoCode(models.Model):
    code = models.CharField(max_length=30, unique=True)
    percent_off = models.PositiveSmallIntegerField(default=10)
    is_active = models.BooleanField(default=True)
    valid_until = models.DateField(null=True, blank=True)
    max_uses = models.PositiveIntegerField(null=True, blank=True, help_text='Leave empty for unlimited use')
    uses_count = models.PositiveIntegerField(default=0, editable=False)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(percent_off__gte=1) & models.Q(percent_off__lte=100), name='promo_percent_1_to_100')]

    def reserved(self, user=None):
        """Seats reserved with this code that are still waiting for approval (the student's own pending payment is not counted)."""
        qs = Payment.objects.filter(promo_code=self.code, status='pending', enrollment__status='pending_payment')
        if user is not None:
            qs = qs.exclude(enrollment__user=user)
        return qs.count()

    @property
    def exhausted(self):
        return self.max_uses is not None and self.uses_count >= self.max_uses

    def exhausted_for(self, user=None):
        """Used up counting approved uses AND codes already promised to other pending reservations."""
        return self.max_uses is not None and self.uses_count + self.reserved(user) >= self.max_uses

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.code} ({self.percent_off}%)'

    @classmethod
    def lookup(cls, code, user=None):
        code = (code or '').strip().upper()
        if not code:
            return None
        promo = cls.objects.filter(code=code, is_active=True).first()
        if promo and ((promo.valid_until and promo.valid_until < timezone.localdate()) or promo.exhausted_for(user)):
            return None
        return promo


class Payment(models.Model):
    GATEWAY_CHOICES = [('easypaisa', 'EasyPaisa'), ('jazzcash', 'JazzCash'), ('bank', 'Bank transfer')]
    STATUS_CHOICES = [('pending', 'Pending review'), ('paid', 'Approved'), ('failed', 'Rejected'), ('refunded', 'Refunded')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    enrollment = models.OneToOneField(Enrollment, on_delete=models.PROTECT, related_name='payment')
    amount = models.DecimalField(max_digits=10, decimal_places=0)
    currency = models.CharField(max_length=10, default='PKR')
    gateway = models.CharField(max_length=20, choices=GATEWAY_CHOICES, default='easypaisa')
    promo_code = models.CharField(max_length=30, blank=True)
    sender_account_number = models.CharField(max_length=40, blank=True)
    transaction_reference = models.CharField(max_length=100, blank=True)
    proof_file = models.FileField(upload_to='payment_proofs/%Y/%m/', blank=True, null=True, storage=PrivateStorage)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    admin_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'created_at'], name='pay_status_created_idx')]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gte=0), name='payment_amount_not_negative')]

    def __str__(self):
        return f'{self.enrollment} - {self.amount} {self.currency} ({self.status})'

    @property
    def proof_url(self):
        return reverse('files:proof', args=[self.pk]) if self.proof_file else ''

    @property
    def has_proof(self):
        return bool(self.transaction_reference or self.proof_file)


# ---------------------------------------------------------------------------
# Reviews, testimonials, contact, newsletter, site stats
# ---------------------------------------------------------------------------
class Review(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='reviews')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    author_name = models.CharField(max_length=120, blank=True)
    author_role_label = models.CharField(max_length=120, blank=True)
    rating = models.PositiveSmallIntegerField(default=5)
    comment = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['course', '-created_at'], name='review_course_idx')]
        constraints = [models.CheckConstraint(condition=models.Q(rating__gte=1) & models.Q(rating__lte=5), name='review_rating_1_to_5'),
                       # a double click can no longer create two reviews of the same course by the same student
                       models.UniqueConstraint(fields=['user', 'course'], condition=models.Q(user__isnull=False), name='review_one_per_student_course')]

    def __str__(self):
        return f'{self.course.title}: {self.rating}★'

    @property
    def display_name(self):
        if self.user:
            return self.user.get_full_name() or self.user.get_username()
        return self.author_name or 'Anonymous'


class Testimonial(models.Model):
    is_sample = models.BooleanField(default=False, editable=False)
    name = models.CharField(max_length=120)
    role_label = models.CharField(max_length=150, blank=True)
    quote = models.TextField()
    course = models.ForeignKey(Course, on_delete=models.SET_NULL, null=True, blank=True, related_name='testimonials')
    is_featured = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    @property
    def avatar_url(self):
        if SiteSettings.stock_enabled():
            return stock_photo(f'jelly-t-{self.pk}', 100, 100)
        return initials_avatar(self.name, self.pk or 0)


class ContactSubmission(models.Model):
    STATUS_CHOICES = [('new', 'New'), ('replied', 'Replied'), ('archived', 'Archived')]

    name = models.CharField(max_length=120)
    email = models.EmailField()
    topic = models.CharField(max_length=150, blank=True)
    message = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', '-created_at'], name='contact_status_idx')]

    def __str__(self):
        return f'{self.name}: {self.topic}'


class NewsletterSubscriber(models.Model):
    email = models.EmailField(unique=True)
    status = models.CharField(max_length=20, choices=[('active', 'Active'), ('unsubscribed', 'Unsubscribed')], default='active')
    token = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    subscribed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-subscribed_at']

    def __str__(self):
        return self.email


class SiteStat(models.Model):
    label = models.CharField(max_length=120)
    value = models.CharField(max_length=30)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order']

    def __str__(self):
        return f'{self.value} {self.label}'


# ---------------------------------------------------------------------------
# Live classes, recordings, certificates, notifications
# ---------------------------------------------------------------------------
YOUTUBE_RE = re.compile(r'(?:youtu\.be/|v=|embed/)([\w-]{11})')
DRIVE_RE = re.compile(r'drive\.google\.com/file/d/([\w-]+)')
VIMEO_RE = re.compile(r'vimeo\.com/(\d+)')


def embed_from_url(u):
    """YouTube / Google Drive / Vimeo link -> iframe-able URL ('' if the link can't be embedded)."""
    for rx, tpl in ((YOUTUBE_RE, 'https://www.youtube.com/embed/{}?rel=0'),
                    (DRIVE_RE, 'https://drive.google.com/file/d/{}/preview'),
                    (VIMEO_RE, 'https://player.vimeo.com/video/{}')):
        m = rx.search(u or '')
        if m:
            return tpl.format(m.group(1))
    return ''


class LiveClass(models.Model):
    PLATFORM_CHOICES = [('meet', 'Google Meet'), ('zoom', 'Zoom'), ('teams', 'Microsoft Teams'), ('other', 'Other')]

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='live_classes')
    batch = models.ForeignKey(Batch, on_delete=models.SET_NULL, null=True, blank=True, related_name='live_classes',
                              help_text='Leave empty to show the class to every enrolled student of the course')
    title = models.CharField(max_length=200)
    starts_at = models.DateTimeField()
    duration_minutes = models.PositiveIntegerField(default=60)
    platform = models.CharField(max_length=10, choices=PLATFORM_CHOICES, default='meet')
    meeting_link = HttpsURLField(max_length=500, blank=True)
    ended_early = models.BooleanField(default=False)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['starts_at']
        indexes = [models.Index(fields=['course', 'starts_at'], name='live_course_start_idx')]
        constraints = [models.CheckConstraint(condition=models.Q(duration_minutes__gt=0), name='live_duration_positive')]

    def __str__(self):
        return f'{self.title} ({self.starts_at:%d %b %H:%M})'

    @property
    def ends_at(self):
        return self.starts_at + timedelta(minutes=self.duration_minutes)

    @property
    def status(self):
        now = timezone.now()
        if self.ended_early or now >= self.ends_at:
            return 'ended'
        return 'live' if now >= self.starts_at else 'upcoming'

    @property
    def can_join(self):
        if not self.meeting_link or self.status == 'ended':
            return False
        return timezone.now() >= self.starts_at - timedelta(minutes=10)

    @property
    def has_recording(self):
        return hasattr(self, 'recording')

    @classmethod
    def visible_to(cls, user):
        """Classes a student can see: courses they are actively enrolled in (and batch, when set)."""
        enrolls = Enrollment.objects.filter(user=user, status__in=['active', 'completed'])
        q = models.Q(pk__in=[])
        for e in enrolls:
            q |= models.Q(course_id=e.course_id) & (models.Q(batch__isnull=True) | models.Q(batch_id=e.batch_id))
        return cls.objects.filter(q).select_related('course')


class ClassRecording(models.Model):
    live_class = models.OneToOneField(LiveClass, on_delete=models.CASCADE, related_name='recording')
    video_url = HttpsURLField(max_length=500, blank=True, help_text='YouTube, Google Drive or Vimeo link')
    video_file = models.FileField(upload_to='recordings/%Y/%m/', blank=True, storage=PrivateStorage)
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'Recording: {self.live_class.title}'

    def clean(self):
        if not self.video_url and not self.video_file:
            raise ValidationError('Add a recording link or upload a video file.')

    @property
    def embed_url(self):
        return embed_from_url(self.video_url)

    @property
    def file_url(self):
        return reverse('files:recording', args=[self.pk]) if self.video_file else ''


class Certificate(models.Model):
    student = models.ForeignKey(User, on_delete=models.PROTECT, related_name='certificates')
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name='certificates')
    issued_on = models.DateField(default=timezone.localdate)
    file = models.FileField(upload_to='certificates/%Y/%m/', storage=PrivateStorage)
    issued_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    code = models.CharField(max_length=16, unique=True, default=new_certificate_code, editable=False,
                            help_text='Public verification code, printed on the certificate')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['student', 'course'], name='one_certificate_per_student_course')]

    def __str__(self):
        return f'{self.student}: {self.course}'

    def get_verify_url(self):
        return reverse('website:verify_certificate', args=[self.code])

    @property
    def file_url(self):
        return reverse('files:certificate', args=[self.pk])

    @property
    def download_url(self):
        return self.file_url + '?download=1'

    @property
    def is_image(self):
        return self.file.name.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg'))


class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=160)
    body = models.CharField(max_length=300, blank=True)
    url = models.CharField(max_length=300, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', 'is_read'], name='notif_user_read_idx'),
                   models.Index(fields=['user', '-created_at'], name='notif_user_created_idx')]

    def __str__(self):
        return f'{self.user}: {self.title}'


class Announcement(models.Model):
    """A message a teacher sends to the students of one course (optionally one batch). Each student also gets a Notification."""
    teacher = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='announcements')
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='announcements')
    batch = models.ForeignKey(Batch, on_delete=models.SET_NULL, null=True, blank=True, related_name='announcements')
    title = models.CharField(max_length=160)
    body = models.CharField(max_length=300)
    recipients = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['course', '-created_at'], name='announce_course_idx')]

    def __str__(self):
        return f'{self.title} -> {self.course}'


# ---------------------------------------------------------------------------
# Public site content (edited in Admin > Site content; every public page reads from here)
# ---------------------------------------------------------------------------
class SiteSettings(models.Model):
    """One row, edited in Admin > Site content. The defaults are the original design's own copy, so a fresh install looks exactly
    like the design; change any of it and the public site follows."""
    site_name = models.CharField(max_length=80, default='The Jelly Productions')
    tagline = models.CharField(max_length=200, blank=True, default='A small, human-run school for people who want to build real things.',
                               help_text='Short line under the logo in the footer')
    email = models.EmailField(blank=True, default='hello@thejellyproductions.com')
    phone = models.CharField(max_length=40, blank=True, default='+92 321 1234567', help_text='Phone / WhatsApp')
    address = models.CharField(max_length=200, blank=True, default='Remote-first \u2014 based in Lahore, Pakistan')
    reply_time = models.CharField(max_length=120, blank=True, default='Within one business day, most often same-day',
                                  help_text='e.g. Within one business day')
    instagram_url = HttpsURLField(blank=True)
    facebook_url = HttpsURLField(blank=True)
    linkedin_url = HttpsURLField(blank=True)
    youtube_url = HttpsURLField(blank=True)

    hero_chip = models.CharField(max_length=80, blank=True, default='Welcome to The Jelly Productions')
    hero_title = models.CharField(max_length=200, blank=True, default='Learn a skill the way you\u2019d learn it from someone who\u2019s done it.')
    hero_text = models.TextField(blank=True, default='A one-instructor school. No rotating cast, no outsourced grading \u2014 just Arshad, a working product designer, '
                                                      'walking a small batch through real project work every week.')
    hero_image = models.ImageField(upload_to='site/', blank=True, help_text='Background of the About, Contact, Courses and Blog headers (optional)')

    batch_cap = models.PositiveSmallIntegerField(null=True, blank=True, default=18, help_text='Max students per batch, shown on the site. Leave empty to hide.')
    refund_days = models.PositiveSmallIntegerField(null=True, blank=True, default=14, help_text='Refund window in days, shown on the site. Leave empty to hide.')
    ticker_text = models.TextField(blank=True, default='\U0001F3AF One instructor, every lesson\n\U0001F4C1 Finish with a real project\n\U0001F4B3 Pay with EasyPaisa, JazzCash or bank transfer',
                                   help_text='Extra scrolling announcements, one per line. Next batch, seats left, discounts, batch size and refund window are added automatically.')

    about_chip = models.CharField(max_length=80, blank=True, default='One instructor, teaching since 2021')
    about_title = models.CharField(max_length=200, blank=True, default='Most courses are built to be sold. This one isn\u2019t.')
    about_lead = models.TextField(blank=True, default='Started in 2021 as a Friday-night critique for six people. Still me teaching every batch, myself.')
    founder_name = models.CharField(max_length=120, blank=True, default='Arshad Nadeem')
    founder_title = models.CharField(max_length=160, blank=True, default='Senior Product Designer at Ridgeline \u00b7 teaches every batch himself')
    founder_quote = models.TextField(blank=True, default='I\u2019d finished eleven online courses and couldn\u2019t point to a single thing I\u2019d built. I could recite the syllabus. '
                                                         'I just couldn\u2019t do the job.')
    founder_photo = models.ImageField(upload_to='site/', blank=True)
    home_quote = models.TextField(blank=True, default='I don\u2019t grade assignments. I open the file and tell students exactly what I\u2019d change if this '
                                                      'were going live on Monday.', help_text='Quote beside the instructor photo on the home page')
    home_byline = models.CharField(max_length=160, blank=True, default='Senior Product Designer, Ridgeline \u00b7 teaches UI Systems')
    about_statement = models.TextField(blank=True, help_text='A bold closing statement on the About page',
                                       default='A course is only as good as the person grading it. So I decided to stay the only person grading mine \u2014 '
                                               'no matter how big this gets.')
    use_stock_photos = models.BooleanField(default=True, help_text='Fill every picture slot you have not uploaded with a stock photo '
                                                                  '(the look of the original design). Turn off to use plain colour backgrounds instead.')

    class Meta:
        verbose_name = verbose_name_plural = 'Site settings'

    def __str__(self):
        return 'Site settings'

    @classmethod
    def get(cls):
        from .request_cache import memo
        return memo('site-settings', lambda: cls.objects.first() or cls.objects.create())

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        from django.core.cache import cache
        from .request_cache import forget
        cache.delete('site:stock')
        forget('site-settings')

    @classmethod
    def stock_enabled(cls):
        """Cached (course cards ask for it many times per page)."""
        from django.core.cache import cache
        value = cache.get('site:stock')
        if value is None:
            value = cls.get().use_stock_photos
            cache.set('site:stock', value, 300)
        return value

    @property
    def ticker_lines(self):
        return [l.strip() for l in self.ticker_text.splitlines() if l.strip()]

    @property
    def socials(self):
        pairs = [('Instagram', self.instagram_url), ('Facebook', self.facebook_url), ('LinkedIn', self.linkedin_url), ('YouTube', self.youtube_url)]
        return [(n, u) for n, u in pairs if u]


class Partner(models.Model):
    """A company where alumni or the instructor work: shown as a logo-text strip when at least one exists."""
    is_sample = models.BooleanField(default=False, editable=False)
    name = models.CharField(max_length=80)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'id']

    def __str__(self):
        return self.name


class Milestone(models.Model):
    is_sample = models.BooleanField(default=False, editable=False)
    year = models.CharField(max_length=10)
    title = models.CharField(max_length=160)
    text = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'year', 'id']

    def __str__(self):
        return f'{self.year}: {self.title}'


class GalleryImage(models.Model):
    is_sample = models.BooleanField(default=False, editable=False)
    image = models.ImageField(upload_to='gallery/', blank=True)
    image_url = HttpsURLField(blank=True, help_text='Or link a picture that is already online')
    caption = models.CharField(max_length=160, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    @property
    def src(self):
        return _picture_src(self)

    class Meta:
        ordering = ['sort_order', '-id']

    def __str__(self):
        return self.caption or f'Image {self.pk}'


class Faq(models.Model):
    is_sample = models.BooleanField(default=False, editable=False)
    PAGE_CHOICES = [('all', 'Every page that shows FAQs'), ('courses', 'Courses page'), ('about', 'About page'), ('contact', 'Contact page'), ('course', 'Course detail pages')]
    page = models.CharField(max_length=10, choices=PAGE_CHOICES, default='all')
    question = models.CharField(max_length=200)
    answer = models.TextField()
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'id']

    def __str__(self):
        return self.question

    @property
    def icon(self):
        """A leading emoji (a first word without letters or digits), shown in its own span like in the original design."""
        first = self.question.split(' ', 1)[0]
        return first if first and not any(c.isalnum() for c in first) else ''

    @property
    def text(self):
        return self.question.split(' ', 1)[1] if self.icon and ' ' in self.question else self.question


class BlogPost(models.Model):
    """Blog articles, written in Admin > Blog (Markdown). The public blog and the home page read from here."""
    CATEGORY_CHOICES = [('design', 'Design'), ('careers', 'Careers'), ('teaching', 'Teaching'), ('learning', 'Learning'), ('news', 'News')]
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='news')
    author_name = models.CharField(max_length=120, blank=True)
    excerpt = models.CharField(max_length=300, blank=True)
    body = models.TextField(help_text='Markdown is supported')
    cover = models.ImageField(upload_to='blog/', blank=True)
    is_featured = models.BooleanField(default=False)
    is_published = models.BooleanField(default=True)
    is_sample = models.BooleanField(default=False, editable=False)
    published_at = models.DateTimeField(default=timezone.now)
    read_time = models.PositiveSmallIntegerField(default=1, editable=False)

    class Meta:
        ordering = ['-published_at']
        indexes = [models.Index(fields=['is_published', '-published_at'], name='blog_pub_idx')]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title)[:200] or 'post'
            slug, n = base, 2
            while BlogPost.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug, n = f'{base}-{n}', n + 1
            self.slug = slug
        self.read_time = max(1, round(len(self.body.split()) / 200))
        super().save(*args, **kwargs)

    # the public templates read these names
    @property
    def featured(self):
        return self.is_featured

    @property
    def author(self):
        return self.author_name or SiteSettings.get().site_name

    @property
    def date(self):
        return timezone.localtime(self.published_at).date()

    @property
    def image(self):
        if self.cover:
            return self.cover.url
        if SiteSettings.stock_enabled():
            return stock_photo(f'blog-{self.slug}', 1000, 560)
        from django.templatetags.static import static
        return static('img/course-placeholder.svg')

    @property
    def category_label(self):
        return dict(self.CATEGORY_CHOICES).get(self.category, self.category.title())

    @property
    def html(self):
        from .markdown_safe import render_markdown
        return render_markdown(self.body)

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('website:blog_detail', args=[self.slug])



class LegalPage(models.Model):
    """Terms, Privacy and Refund policy, written by the admin in Site content (Markdown)."""
    SLUG_CHOICES = [('terms', 'Terms of use'), ('privacy', 'Privacy policy'), ('refund', 'Refund policy')]
    slug = models.CharField(max_length=20, choices=SLUG_CHOICES, unique=True)
    body = models.TextField(blank=True, help_text='Markdown is supported. Leave empty to hide the page and its links.')
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.get_slug_display()

    @property
    def title(self):
        return self.get_slug_display()

    @property
    def html(self):
        from .markdown_safe import render_markdown
        return render_markdown(self.body)

    def get_absolute_url(self):
        return reverse(f'website:{self.slug}')



def _picture_src(row):
    """Uploaded picture first, then a linked URL. Sample rows that point at stock photos disappear when stock photos are switched off."""
    if getattr(row, 'image', None):
        return row.image.url
    url = getattr(row, 'image_url', '') or getattr(row, 'photo_url', '')
    if url and row.is_sample and not SiteSettings.stock_enabled():
        return ''
    return url


class HeroSlide(models.Model):
    """The pictures that fade in and out behind the home page headline."""
    is_sample = models.BooleanField(default=False, editable=False)
    image = models.ImageField(upload_to='hero/', blank=True)
    image_url = HttpsURLField(blank=True, help_text='Or link a picture that is already online')
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'id']

    def __str__(self):
        return f'Slide {self.pk}'

    @property
    def src(self):
        return _picture_src(self)


class TeamMember(models.Model):
    """People shown on the About page next to the instructors (support, operations ...)."""
    is_sample = models.BooleanField(default=False, editable=False)
    name = models.CharField(max_length=120)
    role = models.CharField(max_length=160, blank=True)
    photo = models.ImageField(upload_to='team/', blank=True)
    photo_url = HttpsURLField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'id']

    def __str__(self):
        return self.name

    @property
    def src(self):
        if self.photo:
            return self.photo.url
        if self.is_sample and not SiteSettings.stock_enabled():
            return initials_avatar(self.name, self.pk or 0)
        return self.photo_url or initials_avatar(self.name, self.pk or 0)


class PageQuote(models.Model):
    """The large quote card on the About, Courses and Contact pages."""
    PAGE_CHOICES = [('about', 'About page'), ('courses', 'Courses page'), ('contact', 'Contact page')]
    is_sample = models.BooleanField(default=False, editable=False)
    page = models.CharField(max_length=10, choices=PAGE_CHOICES)
    quote = models.TextField()
    name = models.CharField(max_length=120)
    role = models.CharField(max_length=160, blank=True)
    photo_url = HttpsURLField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'id']

    def __str__(self):
        return f'{self.name} ({self.page})'

    @property
    def avatar(self):
        if self.photo_url and not (self.is_sample and not SiteSettings.stock_enabled()):
            return self.photo_url
        return initials_avatar(self.name, self.pk or 0)
