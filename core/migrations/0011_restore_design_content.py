import os

from django.conf import settings
from django.db import migrations

ORIGINAL = {
    'tagline': 'A small, human-run school for people who want to build real things.',
    'email': 'hello@thejellyproductions.com',
    'phone': '+92 321 1234567',
    'address': 'Remote-first \u2014 based in Lahore, Pakistan',
    'reply_time': 'Within one business day, most often same-day',
    'hero_chip': 'Welcome to The Jelly Productions',
    'hero_title': 'Learn a skill the way you\u2019d learn it from someone who\u2019s done it.',
    'hero_text': 'A one-instructor school. No rotating cast, no outsourced grading \u2014 just Arshad, a working product designer, '
                 'walking a small batch through real project work every week.',
    'batch_cap': 18,
    'refund_days': 14,
    'ticker_text': '\U0001F3AF One instructor, every lesson\n\U0001F4C1 Finish with a real project\n\U0001F4B3 Pay with EasyPaisa, JazzCash or bank transfer',
    'about_chip': 'One instructor, teaching since 2021',
    'about_title': 'Most courses are built to be sold. This one isn\u2019t.',
    'about_lead': 'Started in 2021 as a Friday-night critique for six people. Still me teaching every batch, myself.',
    'founder_name': 'Arshad Nadeem',
    'founder_title': 'Senior Product Designer at Ridgeline \u00b7 teaches every batch himself',
    'founder_quote': 'I\u2019d finished eleven online courses and couldn\u2019t point to a single thing I\u2019d built. I could recite the syllabus. '
                     'I just couldn\u2019t do the job.',
    'about_statement': 'A course is only as good as the person grading it. So I decided to stay the only person grading mine \u2014 '
                       'no matter how big this gets.',
}
# placeholder wording an earlier build of the demo data wrote over the design's own copy
GENERIC = {
    'tagline': {'Live design courses with recordings and certificates.'},
    'phone': {'+92 300 0000000'},
    'address': {'Lahore, Pakistan'},
    'reply_time': {'Within one business day'},
    'hero_chip': {'Welcome'},
    'hero_title': {'Learn design by building real projects.', 'Learn a skill by doing real project work.'},
    'hero_text': {'Live classes, recordings of every session and a certificate when you finish.'},
    'about_title': {'About The Jelly Productions', 'About us'},
}


def restore_original_copy(apps, schema_editor):
    """Blank fields (and the generic placeholder wording) get the original design's text back, so the site looks like the design."""
    Site = apps.get_model('core', 'SiteSettings')
    for row in Site.objects.all():
        for name, value in ORIGINAL.items():
            current = getattr(row, name)
            if current in ('', None) or current in GENERIC.get(name, ()):
                setattr(row, name, value)
        row.save()


def load_sample(apps, schema_editor):
    if getattr(settings, 'TESTING', False) or os.environ.get('JELLY_NO_SAMPLE') == '1':       # tests start from an empty site; JELLY_NO_SAMPLE=1 skips it
        return
    from core.sample_content import load_sample_content
    load_sample_content(apps)


class Migration(migrations.Migration):
    dependencies = [('core', '0010_restore_design')]
    operations = [
        migrations.RunPython(restore_original_copy, migrations.RunPython.noop),
        migrations.RunPython(load_sample, migrations.RunPython.noop),
    ]
