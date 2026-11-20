from django.db import migrations


def recount_all(apps, schema_editor):
    Course, Batch, Enrollment = apps.get_model('core', 'Course'), apps.get_model('core', 'Batch'), apps.get_model('core', 'Enrollment')
    for c in Course.objects.all():
        Course.objects.filter(pk=c.pk).update(students_enrolled_count=Enrollment.objects.filter(course=c, status__in=['active', 'completed']).count())
    for b in Batch.objects.all():
        held = Enrollment.objects.filter(batch=b, status__in=['pending_payment', 'active', 'completed']).count()
        status = b.status
        if status in ('open', 'upcoming') and held >= b.seats_total:
            status = 'full'
        elif status == 'full' and held < b.seats_total:
            status = 'open'
        Batch.objects.filter(pk=b.pk).update(seats_filled=held, status=status)


class Migration(migrations.Migration):
    dependencies = [('core', '0005_blogpost')]
    operations = [migrations.RunPython(recount_all, migrations.RunPython.noop)]
