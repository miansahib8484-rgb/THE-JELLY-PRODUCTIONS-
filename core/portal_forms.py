"""Forms used by both the teacher and the admin dashboards."""
import re
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from .forms import BootstrapMixin
from .models import Batch, Certificate, Course, CurriculumLecture, CurriculumModule, LiveClass
from .utils import validate_upload

User = get_user_model()
DT_FORMAT = '%Y-%m-%dT%H:%M'


YOUTUBE_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')
YOUTUBE_URL = re.compile(r'(?:youtu\.be/|youtube\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/))([A-Za-z0-9_-]{11})')


def clean_youtube_id(value):
    """Accept an 11-character YouTube id or a normal YouTube link; return just the id (or '')."""
    value = (value or '').strip()
    if not value:
        return ''
    if YOUTUBE_ID.match(value):
        return value
    m = YOUTUBE_URL.search(value)
    if m:
        return m.group(1)
    raise forms.ValidationError('Paste a YouTube link or the 11-character video id (for example dQw4w9WgXcQ).')


class CourseForm(BootstrapMixin, forms.ModelForm):
    limits = {'duration_weeks': (1, 104), 'hours_per_week': (1, 60), 'price': (0, 10000000), 'original_price': (0, 10000000), 'description': 8000}
    # declared here (not taken from the model) so a full YouTube link, often longer than the 50-character id column, is accepted;
    # clean_preview_video_id() reduces it to the 11-character id before it is saved
    preview_video_id = forms.CharField(max_length=300, required=False, label='Preview video id', help_text='YouTube video ID')

    def clean_preview_video_id(self):
        return clean_youtube_id(self.cleaned_data.get('preview_video_id'))

    class Meta:
        model = Course
        fields = ['title', 'subtitle', 'category', 'level', 'duration_weeks', 'hours_per_week', 'price', 'original_price',
                  'description', 'thumbnail', 'preview_video_id', 'is_published']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 5}),
            'thumbnail': forms.FileInput(attrs={'accept': 'image/*', 'data-preview': '#thumbPrev'}),
        }

    def __init__(self, *args, role='teacher', **kwargs):
        super().__init__(*args, **kwargs)
        self.role = role
        if self.instance._state.adding:            # (the pk is a UUID, so a new course already has one)
            # the model's default category is 'Product Design': a new course in any other subject was silently labelled with it. Ask instead.
            self.initial['category'] = ''
        self.fields['price'].label = 'Price (PKR)'
        self.fields['original_price'].label = 'Original price (PKR), shown with a line through it'
        self.fields['thumbnail'].label = 'Course image'
        self.fields['is_published'].label = 'Show on the website'
        if role == 'admin':
            self.fields['teacher'] = forms.ModelChoiceField(
                queryset=User.objects.filter(Q(profile__role='teacher', is_active=True) | Q(pk=self.instance.teacher_id)).distinct(),
                required=False, label='Instructor',
                initial=self.instance.teacher_id, widget=forms.Select(attrs={'class': 'form-select'}))
            self.fields['is_featured'] = forms.BooleanField(required=False, label='Feature on the home page', initial=self.instance.is_featured,
                                                            widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))

    def clean_thumbnail(self):
        f = self.cleaned_data.get('thumbnail')
        if f and hasattr(f, 'size') and f.size > settings.MAX_IMAGE_MB * 1024 * 1024:
            raise forms.ValidationError(f'Image is too large (max {settings.MAX_IMAGE_MB} MB).')
        return f

    def clean(self):
        data = super().clean()
        price, orig = data.get('price'), data.get('original_price')
        if price is not None and orig and orig <= price:
            self.add_error('original_price', 'The original price must be higher than the price.')
        return data

    def save(self, commit=True):
        course = super().save(commit=False)
        if self.role == 'admin':
            course.teacher = self.cleaned_data.get('teacher')
            course.is_featured = self.cleaned_data.get('is_featured', False)
        if commit:
            course.save()
        return course


class ModuleForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = CurriculumModule
        fields = ['title']


class LectureForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = CurriculumLecture
        fields = ['title', 'type', 'duration_label', 'video_url']


class ModuleEditForm(BootstrapMixin, forms.ModelForm):
    limits = {'sort_order': (0, 9999)}
    class Meta:
        model = CurriculumModule
        fields = ['title', 'sort_order']
        labels = {'sort_order': 'Position (0 = first)'}


class LectureEditForm(BootstrapMixin, forms.ModelForm):
    limits = {'sort_order': (0, 9999)}
    class Meta:
        model = CurriculumLecture
        fields = ['title', 'type', 'duration_label', 'video_url', 'sort_order']
        labels = {'sort_order': 'Position in the module (0 = first)'}


class BatchForm(BootstrapMixin, forms.ModelForm):
    limits = {'seats_total': (None, 5000)}          # the minimum has its own friendly message in clean_seats_total
    class Meta:
        model = Batch
        fields = ['start_date', 'schedule_label', 'seats_total', 'status', 'meeting_link']
        widgets = {'start_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')}

    def clean_start_date(self):
        start = self.cleaned_data['start_date']
        # a NEW batch cannot start in the past (a typo such as 2025 for 2026 used to create an 'open' batch nobody could attend);
        # an existing batch may keep its date, e.g. when only the seats are edited after it began
        if not self.instance.pk and start < timezone.localdate():
            raise forms.ValidationError('The start date is in the past. Pick today or a later date.')
        return start

    def clean_seats_total(self):
        total = self.cleaned_data['seats_total']
        if total < 1:
            raise forms.ValidationError('A batch needs at least one seat.')
        held = self.instance.seats_filled if self.instance.pk else 0
        if total < held:
            raise forms.ValidationError(f'{held} students already hold a seat in this batch: the total cannot be lower.')
        return total


class LiveClassForm(BootstrapMixin, forms.ModelForm):
    limits = {'duration_minutes': (None, 480)}      # the 10-minute minimum has its own message in clean()
    class Meta:
        model = LiveClass
        fields = ['course', 'batch', 'title', 'starts_at', 'duration_minutes', 'platform', 'meeting_link']
        widgets = {'starts_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format=DT_FORMAT)}

    def __init__(self, *args, courses=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['starts_at'].input_formats = [DT_FORMAT, '%Y-%m-%d %H:%M']
        self.fields['course'].queryset = courses
        self.fields['batch'].queryset = Batch.objects.filter(course__in=courses).select_related('course')
        self.fields['batch'].required = False
        self.fields['batch'].empty_label = 'All batches of this course'
        self.fields['starts_at'].label = 'Start time (Pakistan time)'
        self.fields['meeting_link'].help_text = 'Google Meet, Zoom or Teams link. Students see a Join button 10 minutes before the class.'

    def clean(self):
        data = super().clean()
        course, batch = data.get('course'), data.get('batch')
        if course and batch and batch.course_id != course.pk:
            self.add_error('batch', 'That batch belongs to a different course.')
        starts, mins = data.get('starts_at'), data.get('duration_minutes')
        if mins is not None and not 10 <= mins <= 480:
            self.add_error('duration_minutes', 'A class lasts between 10 minutes and 8 hours.')
        elif starts and mins and course:
            if not self.instance.pk and starts < timezone.now() - timedelta(minutes=5):
                self.add_error('starts_at', 'This time is in the past. Pick a future time.')
            end = starts + timedelta(minutes=mins)
            same_person = Q(course__teacher=course.teacher) if course.teacher_id else Q(course=course)
            near = (LiveClass.objects.filter(same_person, ended_early=False, starts_at__lt=end, starts_at__gte=starts - timedelta(hours=8))
                    .exclude(pk=self.instance.pk).select_related('course'))
            clash = next((c for c in near if c.ends_at > starts), None)
            if clash:
                self.add_error('starts_at', f'This overlaps "{clash.title}" ({timezone.localtime(clash.starts_at):%a %d %b, %I:%M %p}).')
        return data


VIDEO_EXT = ('.mp4', '.webm', '.mov', '.mkv', '.m4v')


class RecordingForm(BootstrapMixin, forms.Form):
    limits = {'video_url': 200}          # the model column holds 200 characters: a longer link crashed the save on PostgreSQL
    rec_type = forms.ChoiceField(choices=[('link', 'Paste a link'), ('file', 'Upload a video file')], initial='link', widget=forms.RadioSelect)
    video_url = forms.URLField(required=False, assume_scheme='https', label='Recording link', help_text='YouTube, Google Drive or Vimeo. Tip: use an Unlisted YouTube video.')
    video_file = forms.FileField(required=False, label='Video file')

    def clean(self):
        data = super().clean()
        kind = data.get('rec_type')
        if kind == 'link' and not data.get('video_url'):
            self.add_error('video_url', 'Paste the recording link.')
        if kind == 'file':
            f = data.get('video_file')
            if not f:
                self.add_error('video_file', 'Choose a video file.')
            else:
                err = validate_upload(f, settings.MAX_RECORDING_MB, VIDEO_EXT)
                if err:
                    self.add_error('video_file', err)
        return data


class StudentChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, u):
        return f'{u.get_full_name() or u.username} ({u.email})'


class CertificateForm(BootstrapMixin, forms.Form):
    student = StudentChoiceField(queryset=User.objects.none())
    course = forms.ModelChoiceField(queryset=Course.objects.none())
    issued_on = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    file = forms.FileField(label='Certificate file (PDF or image)')

    def __init__(self, *args, courses, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['course'].queryset = courses
        self.fields['student'].queryset = User.objects.filter(
            enrollments__course__in=courses, enrollments__status__in=['active', 'completed']).distinct().order_by('first_name', 'last_name', 'pk')

    def clean_file(self):
        f = self.cleaned_data['file']
        err = validate_upload(f, settings.MAX_CERTIFICATE_MB, ('.pdf', '.png', '.jpg', '.jpeg', '.webp'))
        if err:
            raise forms.ValidationError(err)
        return f

    def clean(self):
        data = super().clean()
        s, c = data.get('student'), data.get('course')
        if s and c and not s.enrollments.filter(course=c, status__in=['active', 'completed']).exists():
            self.add_error('student', 'This student is not enrolled in that course.')
        elif s and c and Certificate.objects.filter(student=s, course=c).exists():
            self.add_error('student', 'A certificate was already issued to this student for that course. Delete it first if it needs replacing.')
        return data
