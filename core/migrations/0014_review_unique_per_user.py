from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0013_dedupe_reviews')]
    operations = [
        migrations.AddConstraint(
            model_name='review',
            constraint=models.UniqueConstraint(fields=('user', 'course'), condition=models.Q(user__isnull=False), name='review_one_per_student_course')),
    ]
