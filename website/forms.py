from django import forms

from core.forms import BootstrapMixin, account_number_validator, phone_validator, promo_code_validator, reference_validator
from core.models import Batch, Course, Payment, PromoCode, Review
from core.utils import validate_upload


class ContactForm(BootstrapMixin, forms.Form):
    reject_html = True
    TOPICS = [('', 'Choose a topic'), ('A specific course or batch date', 'A specific course or batch date'),
              ('Refunds or billing', 'Refunds or billing'), ('A guest workshop or collaboration', 'A guest workshop or collaboration'),
              ('Corporate / team training', 'Corporate / team training'), ('Something else', 'Something else')]
    name = forms.CharField(max_length=120, label='Full name')
    email = forms.EmailField()
    topic = forms.ChoiceField(choices=TOPICS, label="What's this about?")
    message = forms.CharField(widget=forms.Textarea(attrs={'rows': 5}), max_length=3000)


class ReviewForm(BootstrapMixin, forms.ModelForm):
    limits = {'comment': 2000}
    reject_html = True
    rating = forms.ChoiceField(choices=[(5, '5 - Excellent'), (4, '4 - Good'), (3, '3 - Average'), (2, '2 - Poor'), (1, '1 - Bad')])
    comment = forms.CharField(max_length=2000, widget=forms.Textarea(attrs={'rows': 3}))

    class Meta:
        model = Review
        fields = ['rating', 'comment']


class EnrollForm(BootstrapMixin, forms.Form):
    limits = {'notes': 500}
    reject_html = True
    extra_validators = {'phone': [phone_validator], 'transaction_reference': [reference_validator],
                        'sender_account_number': [account_number_validator], 'promo_code': [promo_code_validator]}
    course = forms.ModelChoiceField(queryset=Course.objects.none(), empty_label=None)
    batch = forms.ModelChoiceField(queryset=Batch.objects.none(), required=False, widget=forms.RadioSelect)
    phone = forms.CharField(max_length=30, label='Phone / WhatsApp')
    city = forms.CharField(max_length=80)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))
    gateway = forms.ChoiceField(choices=Payment.GATEWAY_CHOICES, widget=forms.RadioSelect, initial='easypaisa')
    transaction_reference = forms.CharField(max_length=100, required=False, label='Transaction ID')
    sender_account_number = forms.CharField(max_length=40, required=False, label='Number you paid from')
    proof_file = forms.FileField(required=False, label='Payment screenshot')
    promo_code = forms.CharField(max_length=30, required=False)
    terms = forms.BooleanField(error_messages={'required': 'Please accept to continue.'})

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields['course'].queryset = Course.objects.filter(is_published=True)
        self.fields['batch'].queryset = Batch.objects.filter(status__in=['upcoming', 'open'])

    def clean_promo_code(self):
        """A code that is typed but not valid (wrong, switched off, expired, used up) is an error: it used to be dropped silently, so the
        student paid the full price while the page had shown a discount."""
        code = (self.cleaned_data.get('promo_code') or '').strip()
        if code and PromoCode.lookup(code, user=self.user) is None:
            raise forms.ValidationError('This promo code is not valid any more. Remove it or check the spelling.')
        return code

    def clean_proof_file(self):
        f = self.cleaned_data.get('proof_file')
        if f:
            err = validate_upload(f, 8, ('.png', '.jpg', '.jpeg', '.pdf', '.webp'))
            if err:
                raise forms.ValidationError(err)
        return f

    def clean(self):
        data = super().clean()
        course, batch = data.get('course'), data.get('batch')
        if course and batch and batch.course_id != course.pk:
            self.add_error('batch', 'That batch belongs to a different course.')
        return data
