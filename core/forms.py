import re

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator, MaxValueValidator, MinValueValidator, RegexValidator

# ---- input validators used by every form -------------------------------------------------------------------------------------------
# Control characters (null byte, escape ...), "right-to-left override" characters (they make text look different from what it is) and, in
# one-line fields, line breaks (used to forge e-mail headers and log lines) are never legitimate in what people type.
_BAD = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u202a-\u202e\u2066-\u2069]')
_LINE_BREAK = re.compile('[\r\n\u2028\u2029]')
_TAG = re.compile(r'<\s*[/!a-zA-Z]')


def no_control_chars(value):
    if _BAD.search(value):
        raise ValidationError('This text contains characters that are not allowed.', code='control_chars')


def no_line_breaks(value):
    no_control_chars(value)
    if _LINE_BREAK.search(value):
        raise ValidationError('Please write this on one line.', code='line_break')


def no_html_tags(value):
    """Text typed by visitors and students is plain text: anything that looks like an HTML tag is refused (it is also escaped on output)."""
    if _TAG.search(value):
        raise ValidationError('Please remove the < > characters: HTML is not allowed here.', code='html')


_PHONE_SHAPE = re.compile(r'^\+?[0-9(][0-9 ()\-]{6,28}$')


def phone_validator(value):
    """A phone number has 7 to 15 digits (the international limit) and only digits, spaces, ( ) - and a leading +."""
    digits = sum(ch.isdigit() for ch in value)
    if not _PHONE_SHAPE.match(value) or not 7 <= digits <= 15:
        raise ValidationError('Enter a valid phone number, for example 0300 1234567 or +92 300 1234567.', code='phone')
reference_validator = RegexValidator(r'^[A-Za-z0-9][A-Za-z0-9 _./#\-]{0,99}$', 'Use only letters, digits, spaces and - _ . / # (up to 100 characters).')
account_number_validator = RegexValidator(r'^[A-Za-z0-9][A-Za-z0-9 +\-]{3,38}$', 'Use only letters, digits, spaces, + and - (4 to 40 characters).')
promo_code_validator = RegexValidator(r'^[A-Za-z0-9_\-]{1,30}$', 'A promo code has only letters, digits, - and _.')


class BootstrapMixin:
    """Adds Bootstrap 5 classes to every widget, plus the limits that every form needs.

    A form may declare
        limits = {'field': 500, 'number_field': (1, 100)}   # text: maximum characters; number: (minimum, maximum), either may be None
        extra_validators = {'field': [validator, ...]}
        reject_html = True                                   # visitor/student text: no HTML tags
    The limits are checked on the server and also copied into the HTML (maxlength / min / max) so the browser warns early.
    """
    limits = {}
    extra_validators = {}
    reject_html = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            w = field.widget
            if isinstance(w, forms.CheckboxInput):
                css = 'form-check-input'
            elif isinstance(w, (forms.Select, forms.SelectMultiple)):
                css = 'form-select'
            elif isinstance(w, forms.RadioSelect):
                continue
            else:
                css = 'form-control'
            w.attrs['class'] = f"{w.attrs.get('class', '')} {css}".strip()
        # screen readers need a name for every control, also where a template draws the field without a <label for=...>
        for name, field in self.fields.items():
            w = field.widget
            if not isinstance(w, (forms.HiddenInput, forms.CheckboxInput, forms.RadioSelect)) and 'aria-label' not in w.attrs:
                w.attrs['aria-label'] = str(field.label or name.replace('_', ' ').capitalize())
        self._apply_limits()

    def _apply_limits(self):
        for name, field in self.fields.items():
            w = field.widget
            if isinstance(field, forms.CharField) and not isinstance(w, forms.PasswordInput):
                field.validators.append(no_control_chars if isinstance(w, forms.Textarea) else no_line_breaks)
                if self.reject_html:
                    field.validators.append(no_html_tags)
            for v in self.extra_validators.get(name, ()):
                field.validators.append(v)
            limit = self.limits.get(name)
            if limit is None:
                continue
            if isinstance(limit, tuple):
                low, high = limit
                if low is not None:
                    field.min_value = low
                    field.validators.append(MinValueValidator(low))
                    w.attrs['min'] = low
                if high is not None:
                    field.max_value = high
                    field.validators.append(MaxValueValidator(high))
                    w.attrs['max'] = high
            else:
                field.max_length = limit
                field.validators.append(MaxLengthValidator(limit))
                w.attrs['maxlength'] = limit
