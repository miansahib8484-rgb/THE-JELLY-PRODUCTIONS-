from django import forms
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import UploadedFile

from core.forms import BootstrapMixin, phone_validator
from django.conf import settings as dj_settings
from django.contrib.auth.password_validation import validate_password

from core.models import BlogPost, Faq, GalleryImage, HeroSlide, Milestone, PageQuote, Partner, TeamMember, PaymentAccountInfo, PromoCode, SiteSettings, SiteStat, Testimonial
from core.utils import validate_upload

User = get_user_model()


class _AccountCreateForm(BootstrapMixin, forms.Form):
    """An administrator creates an account and tells the person the password. That password is used as it is: nobody is forced to change it
    (tick the box only if you want the person to choose their own password at the first sign-in)."""
    limits = {'password': 128}
    first_name = forms.CharField(max_length=60)
    last_name = forms.CharField(max_length=60)
    email = forms.EmailField(max_length=150)            # the e-mail becomes the username (150 characters at most)
    password = forms.CharField(min_length=8, widget=forms.PasswordInput, label='Password',
                               help_text='Tell them this password. They can sign in with it right away and change it any time in Settings.')
    ask_to_change = forms.BooleanField(required=False, label='Ask them to choose their own password at the first sign-in')

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exists() or User.objects.filter(username__iexact=email).exists():
            raise forms.ValidationError('An account with this email already exists. Find it under Students or Teachers instead of creating it again.')
        return email

    def clean_password(self):
        pw = self.cleaned_data['password']
        validate_password(pw)
        return pw


class TeacherCreateForm(_AccountCreateForm):
    title = forms.CharField(max_length=120, required=False, label='Job title')


class StudentCreateForm(_AccountCreateForm):
    phone = forms.CharField(max_length=30, required=False, label='Phone / WhatsApp', validators=[phone_validator])


class PaymentInfoForm(BootstrapMixin, forms.ModelForm):
    limits = {'instructions': 1000}
    class Meta:
        model = PaymentAccountInfo
        fields = ['easypaisa_number', 'easypaisa_account_name', 'jazzcash_number', 'jazzcash_account_name', 'bank_name', 'bank_account_title',
                  'bank_account_number', 'bank_iban', 'instructions']
        widgets = {'instructions': forms.Textarea(attrs={'rows': 2})}


class TestimonialForm(BootstrapMixin, forms.ModelForm):
    limits = {'quote': 600}
    class Meta:
        model = Testimonial
        fields = ['name', 'role_label', 'quote', 'is_featured']
        widgets = {'quote': forms.Textarea(attrs={'rows': 3})}


class SiteStatForm(BootstrapMixin, forms.ModelForm):
    limits = {'sort_order': (0, 9999)}
    class Meta:
        model = SiteStat
        fields = ['value', 'label', 'sort_order']


class PromoForm(BootstrapMixin, forms.ModelForm):
    limits = {'max_uses': (1, 100000)}          # percent_off has its own range check and message
    class Meta:
        model = PromoCode
        fields = ['code', 'percent_off', 'valid_until', 'max_uses']
        widgets = {'valid_until': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')}

    def clean_code(self):
        code = self.cleaned_data['code'].strip().upper()          # the model stores codes in capitals
        if PromoCode.objects.filter(code__iexact=code).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('A promo code with this name already exists.')
        return code

    def clean_percent_off(self):
        v = self.cleaned_data['percent_off']
        if not 1 <= v <= 90:
            raise forms.ValidationError('Enter a percentage between 1 and 90.')
        return v


class UserEditForm(BootstrapMixin, forms.Form):
    """Admin edits a student's or teacher's basic details."""
    first_name = forms.CharField(max_length=60)
    last_name = forms.CharField(max_length=60, required=False)
    email = forms.EmailField(max_length=150)
    phone = forms.CharField(max_length=30, required=False)
    city = forms.CharField(max_length=80, required=False)
    title = forms.CharField(max_length=120, required=False, label='Job title')
    bio = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), required=False)

    def __init__(self, *args, user, **kwargs):
        self.user = user
        p = user.profile
        kwargs.setdefault('initial', {'first_name': user.first_name, 'last_name': user.last_name, 'email': user.email,
                                      'phone': p.phone, 'city': p.city, 'title': p.title, 'bio': p.bio})
        super().__init__(*args, **kwargs)
        if p.role != 'teacher':
            del self.fields['title']

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        clash = User.objects.filter(email__iexact=email).exclude(pk=self.user.pk) | User.objects.filter(username__iexact=email).exclude(pk=self.user.pk)
        if clash.exists():
            raise forms.ValidationError('Another account already uses this email.')
        return email

    def save(self):
        d, u = self.cleaned_data, self.user
        u.first_name, u.last_name, u.email = d['first_name'], d.get('last_name', ''), d['email']
        if u.username == u.email or '@' in u.username:   # accounts sign in by email: keep username in step
            u.username = d['email']
        u.save()
        p = u.profile
        p.phone, p.city, p.bio = d.get('phone', ''), d.get('city', ''), d.get('bio', '')
        if 'title' in d:
            p.title = d['title']
        p.save()
        return u


IMAGE_EXT = ('.png', '.jpg', '.jpeg', '.webp', '.gif')


def _check_image(form, name):
    f = form.cleaned_data.get(name)
    if isinstance(f, UploadedFile):           # only a picture sent just now (a stored one may be gone from disk)
        err = validate_upload(f, dj_settings.MAX_IMAGE_MB, IMAGE_EXT)
        if err:
            raise forms.ValidationError(err)
    return f


SITE_SETTINGS_GROUPS = [
    ('General & contact', ['site_name', 'tagline', 'email', 'phone', 'address', 'reply_time', 'instagram_url', 'facebook_url', 'linkedin_url', 'youtube_url', 'use_stock_photos']),
    ('Homepage', ['hero_chip', 'hero_title', 'hero_text', 'hero_image', 'home_quote', 'home_byline', 'batch_cap', 'refund_days', 'ticker_text']),
    ('About page', ['about_chip', 'about_title', 'about_lead', 'founder_name', 'founder_title', 'founder_quote', 'founder_photo', 'about_statement']),
]


class SiteSettingsForm(BootstrapMixin, forms.ModelForm):
    limits = {'hero_text': 400, 'home_quote': 400, 'ticker_text': 300, 'about_lead': 600, 'founder_quote': 600, 'about_statement': 2000,
              'batch_cap': (1, 500), 'refund_days': (1, 365)}
    GROUPS = SITE_SETTINGS_GROUPS

    class Meta:
        model = SiteSettings
        fields = [name for _, names in SITE_SETTINGS_GROUPS for name in names]   # explicit: a new model field is never editable by accident
        widgets = {'hero_text': forms.Textarea(attrs={'rows': 3}), 'ticker_text': forms.Textarea(attrs={'rows': 3}),
                   'about_lead': forms.Textarea(attrs={'rows': 3}), 'founder_quote': forms.Textarea(attrs={'rows': 3}),
                   'about_statement': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():          # fields live in several tab panes outside the <form> tag
            f.widget.attrs['form'] = 'siteForm'

    def grouped(self):
        return [(title, [self[n] for n in names]) for title, names in self.GROUPS]

    def clean_hero_image(self):
        return _check_image(self, 'hero_image')

    def clean_founder_photo(self):
        return _check_image(self, 'founder_photo')


class PartnerForm(BootstrapMixin, forms.ModelForm):
    limits = {'sort_order': (0, 9999)}
    class Meta:
        model = Partner
        fields = ['name', 'sort_order']


class MilestoneForm(BootstrapMixin, forms.ModelForm):
    limits = {'text': 500, 'sort_order': (0, 9999)}
    class Meta:
        model = Milestone
        fields = ['year', 'title', 'text', 'sort_order']
        widgets = {'text': forms.Textarea(attrs={'rows': 2})}


class FaqForm(BootstrapMixin, forms.ModelForm):
    limits = {'answer': 2000, 'sort_order': (0, 9999)}
    class Meta:
        model = Faq
        fields = ['page', 'question', 'answer', 'sort_order']
        widgets = {'answer': forms.Textarea(attrs={'rows': 3})}


class _PictureForm(BootstrapMixin, forms.ModelForm):
    """Forms whose row needs a picture: either an upload or a link."""
    limits = {'sort_order': (0, 9999)}
    def clean(self):
        data = super().clean()
        if not data.get('image') and not data.get('image_url') and not (self.instance.pk and self.instance.image):
            raise forms.ValidationError('Upload a picture or paste a link to one.')
        return data

    def clean_image(self):
        return _check_image(self, 'image')


class GalleryForm(_PictureForm):
    class Meta:
        model = GalleryImage
        fields = ['image', 'image_url', 'caption', 'sort_order']


class HeroSlideForm(_PictureForm):
    class Meta:
        model = HeroSlide
        fields = ['image', 'image_url', 'sort_order']


class TeamMemberForm(BootstrapMixin, forms.ModelForm):
    limits = {'sort_order': (0, 9999)}
    class Meta:
        model = TeamMember
        fields = ['name', 'role', 'photo', 'photo_url', 'sort_order']

    def clean_photo(self):
        return _check_image(self, 'photo')


class PageQuoteForm(BootstrapMixin, forms.ModelForm):
    limits = {'quote': 600, 'sort_order': (0, 9999)}
    class Meta:
        model = PageQuote
        fields = ['page', 'quote', 'name', 'role', 'photo_url', 'sort_order']
        widgets = {'quote': forms.Textarea(attrs={'rows': 3})}


class BlogForm(BootstrapMixin, forms.ModelForm):
    limits = {'body': 60000}
    published_at = forms.DateTimeField(widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
                                       input_formats=['%Y-%m-%dT%H:%M'], label='Publish at')

    class Meta:
        model = BlogPost
        fields = ['title', 'category', 'author_name', 'excerpt', 'cover', 'body', 'is_featured', 'is_published', 'published_at']
        widgets = {'body': forms.Textarea(attrs={'rows': 16}), 'excerpt': forms.Textarea(attrs={'rows': 2})}
        labels = {'author_name': 'Author (leave empty to use the site name)', 'body': 'Article (Markdown)', 'is_featured': 'Feature on the blog page', 'is_published': 'Published'}

    def clean_cover(self):
        return _check_image(self, 'cover')
