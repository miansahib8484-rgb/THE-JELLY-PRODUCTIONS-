from django import forms

from core.forms import BootstrapMixin
from core.models import Announcement, Batch, Course


class AnnouncementForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Announcement
        fields = ['course', 'batch', 'title', 'body']
        widgets = {'body': forms.Textarea(attrs={'rows': 4, 'maxlength': 300}),
                   'title': forms.TextInput(attrs={'placeholder': 'e.g. Class moved to 8 pm tomorrow'})}
        labels = {'body': 'Message', 'batch': 'Batch (optional)'}

    def __init__(self, *args, teacher, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['course'].queryset = Course.objects.filter(teacher=teacher)
        self.fields['batch'].queryset = Batch.objects.filter(course__teacher=teacher).select_related('course')
        self.fields['batch'].required = False
        self.fields['batch'].empty_label = 'Every student of the course'
        self.fields['batch'].label_from_instance = lambda b: f'{b.course.title[:30]}: starts {b.start_date:%d %b %Y}'

    def clean(self):
        data = super().clean()
        course, batch = data.get('course'), data.get('batch')
        if course and batch and batch.course_id != course.pk:
            self.add_error('batch', 'That batch belongs to a different course.')
        return data
