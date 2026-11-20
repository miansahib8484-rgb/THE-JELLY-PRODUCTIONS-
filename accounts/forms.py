import logging

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordChangeForm, PasswordResetForm
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone

from core.forms import BootstrapMixin, phone_validator
from core.models import Course, Profile

User = get_user_model()
TEXT = {'class': 'form-control'}


class RegisterForm(BootstrapMixin, forms.Form):
    limits = {'goal': 1000, 'password1': 128, 'password2': 128}
    reject_html = True
    ACCOUNT_CHOICES = [('student', 'I want to learn'), ('teacher', 'I want to teach')]
    OCCUPATIONS = [('', 'Choose one'), ('Student', 'Student'), ('Fresh graduate', 'Fresh graduate'),
                   ('Working professional', 'Working professional'), ('Freelancer', 'Freelancer'), ('Business owner', 'Business owner')]
    HEARD = [('Instagram', 'Instagram'), ('YouTube', 'YouTube'), ('A friend', 'A friend'), ('Google search', 'Google search'), ('Other', 'Other')]
    COUNTRIES = [(c, c) for c in ['Pakistan', 'United Arab Emirates', 'Saudi Arabia', 'United Kingdom', 'United States', 'Other']]

    account_type = forms.ChoiceField(choices=ACCOUNT_CHOICES, initial='student', widget=forms.RadioSelect)
    first_name = forms.CharField(max_length=60)
    last_name = forms.CharField(max_length=60)
    email = forms.EmailField(max_length=150)       # the e-mail is stored as the username, which holds 150 characters at most (PostgreSQL enforces it)
    phone = forms.CharField(max_length=30, label='Phone / WhatsApp', validators=[phone_validator])
    date_of_birth = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    city = forms.CharField(max_length=80)
    country = forms.ChoiceField(choices=COUNTRIES, initial='Pakistan')
    occupation = forms.ChoiceField(choices=OCCUPATIONS, label='I am currently a')
    interested_course = forms.ModelChoiceField(queryset=Course.objects.none(), required=False, empty_label='Not sure yet',
                                               label='Course I am interested in')
    experience_level = forms.ChoiceField(choices=Profile.EXPERIENCE_CHOICES, initial='beginner', widget=forms.RadioSelect,
                                         label='My design experience')
    goal = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}), label='What do you want to achieve?')
    heard_from = forms.ChoiceField(choices=HEARD, required=False, label='How did you hear about us?')
    password1 = forms.CharField(widget=forms.PasswordInput, label='Password')
    password2 = forms.CharField(widget=forms.PasswordInput, label='Confirm password')
    terms = forms.BooleanField(error_messages={'required': 'You need to accept to continue.'})
    newsletter = forms.BooleanField(required=False, initial=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['interested_course'].queryset = Course.objects.filter(is_published=True)

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exists() or User.objects.filter(username__iexact=email).exists():
            raise forms.ValidationError('An account with this email already exists. Try signing in.')
        return email

    def clean_date_of_birth(self):
        dob = self.cleaned_data['date_of_birth']
        today = timezone.localdate()
        if dob > today or dob.year < 1900:
            raise forms.ValidationError('Enter your real date of birth.')
        if (today - dob).days < 10 * 365:          # the courses are for teenagers and adults: a 6-year-old's birth date is a typo
            raise forms.ValidationError('You must be at least 10 years old to register.')
        return dob

    def clean(self):
        data = super().clean()
        p1, p2 = data.get('password1'), data.get('password2')
        if p1 and p2 and p1 != p2:
            self.add_error('password2', 'Passwords do not match.')
        elif p1:
            try:
                # give the validators the person's details so "similar to your name/e-mail" passwords are refused
                candidate = User(username=data.get('email', ''), email=data.get('email', ''),
                                 first_name=data.get('first_name', ''), last_name=data.get('last_name', ''))
                validate_password(p1, user=candidate)
            except forms.ValidationError as e:
                self.add_error('password1', e)
        return data


class SafePasswordResetForm(PasswordResetForm):
    """Django's form, but an SMTP failure is logged instead of showing a 500 page (the visitor always sees the same 'check your inbox' page,
    which also keeps the form from revealing which addresses have an account)."""

    def send_mail(self, *args, **kwargs):
        try:
            super().send_mail(*args, **kwargs)
        except Exception:
            logging.getLogger(__name__).exception('Password-reset e-mail could not be sent')


class LoginForm(BootstrapMixin, forms.Form):
    limits = {'username': 254, 'password': 128}
    username = forms.CharField(label='Email or username', widget=forms.TextInput(attrs={'autofocus': True, 'autocomplete': 'username'}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}))
    remember = forms.BooleanField(required=False, initial=True, label='Keep me signed in on this device')
    role = forms.ChoiceField(choices=[('student', 'Student'), ('teacher', 'Teacher'), ('admin', 'Admin')], initial='student', required=False, widget=forms.HiddenInput)


class ProfileForm(BootstrapMixin, forms.Form):
    limits = {'bio': 600}
    reject_html = True
    first_name = forms.CharField(max_length=60)
    last_name = forms.CharField(max_length=60, required=False)
    email = forms.EmailField(max_length=150)
    phone = forms.CharField(max_length=30, required=False, validators=[phone_validator])
    city = forms.CharField(max_length=80, required=False)
    country = forms.CharField(max_length=80, required=False)
    title = forms.CharField(max_length=120, required=False, label='Job title')
    bio = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 4}))
    avatar = forms.ImageField(required=False)

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        others = User.objects.exclude(pk=self.user.pk)
        if others.filter(email__iexact=email).exists() or others.filter(username__iexact=email).exists():
            raise forms.ValidationError('That email is used by another account.')
        return email

    def clean_avatar(self):
        f = self.cleaned_data.get('avatar')
        if f and hasattr(f, 'size') and f.size > settings.MAX_IMAGE_MB * 1024 * 1024:
            raise forms.ValidationError(f'Image is too large (max {settings.MAX_IMAGE_MB} MB).')
        return f

    def save(self):
        u, p, d = self.user, self.user.profile, self.cleaned_data
        u.first_name, u.last_name, u.email = d['first_name'], d['last_name'], d['email']
        if u.username == u.email or '@' in u.username:
            u.username = d['email']
        u.save()
        for f in ('phone', 'city', 'country', 'title', 'bio'):
            setattr(p, f, d[f])
        if d.get('avatar'):
            p.avatar = d['avatar']
        p.save()
        return u


class StyledPasswordChangeForm(BootstrapMixin, PasswordChangeForm):
    limits = {'old_password': 128, 'new_password1': 128, 'new_password2': 128}
    pass
