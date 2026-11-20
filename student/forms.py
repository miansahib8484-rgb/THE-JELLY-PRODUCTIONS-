from django import forms
from django.core.files.uploadedfile import UploadedFile

from core.forms import BootstrapMixin, account_number_validator, reference_validator
from core.models import Payment, Review
from core.utils import validate_upload


class PaymentProofForm(BootstrapMixin, forms.ModelForm):
    reject_html = True
    extra_validators = {'transaction_reference': [reference_validator], 'sender_account_number': [account_number_validator]}
    class Meta:
        model = Payment
        fields = ['gateway', 'transaction_reference', 'sender_account_number', 'proof_file']

    def clean_proof_file(self):
        f = self.cleaned_data.get('proof_file')
        if isinstance(f, UploadedFile):          # only a file sent just now: the stored one may no longer exist on disk
            err = validate_upload(f, 8, ('.png', '.jpg', '.jpeg', '.pdf', '.webp'))
            if err:
                raise forms.ValidationError(err)
        return f

    def clean(self):
        data = super().clean()
        if not data.get('transaction_reference') and not data.get('proof_file') and not self.instance.proof_file:
            raise forms.ValidationError('Add a transaction ID or upload a screenshot of your payment.')
        return data


class StudentReviewForm(BootstrapMixin, forms.ModelForm):
    limits = {'comment': 2000}
    reject_html = True
    rating = forms.ChoiceField(choices=[(5, '5 - Excellent'), (4, '4 - Good'), (3, '3 - Average'), (2, '2 - Poor'), (1, '1 - Bad')])
    comment = forms.CharField(max_length=2000, widget=forms.Textarea(attrs={'rows': 4}))     # no unlimited text from one form post

    class Meta:
        model = Review
        fields = ['rating', 'comment']
