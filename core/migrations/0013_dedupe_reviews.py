from django.db import migrations
from django.db.models import Avg, Count


def remove_duplicate_reviews(apps, schema_editor):
    """Keep only the newest review of each (student, course) pair; then refresh the rating of the courses that changed."""
    Review, Course = apps.get_model('core', 'Review'), apps.get_model('core', 'Course')
    touched = set()
    dups = (Review.objects.filter(user__isnull=False).values('user_id', 'course_id').annotate(n=Count('id')).filter(n__gt=1))
    for d in dups:
        rows = list(Review.objects.filter(user_id=d['user_id'], course_id=d['course_id']).order_by('-created_at', '-id'))
        Review.objects.filter(pk__in=[r.pk for r in rows[1:]]).delete()
        touched.add(d['course_id'])
    for cid in touched:
        avg = Review.objects.filter(course_id=cid).aggregate(a=Avg('rating'))['a']
        Course.objects.filter(pk=cid).update(rating_avg=round(avg, 2) if avg else 0)


class Migration(migrations.Migration):
    dependencies = [('core', '0012_home_quote')]
    # data only: the constraint is added by the NEXT migration (data and schema changes in one PostgreSQL transaction can fail with
    # "cannot ALTER TABLE because it has pending trigger events")
    operations = [migrations.RunPython(remove_duplicate_reviews, migrations.RunPython.noop)]
