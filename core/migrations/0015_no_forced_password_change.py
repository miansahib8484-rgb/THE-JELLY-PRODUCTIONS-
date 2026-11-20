from django.db import migrations, models


def clear_forced_changes(apps, schema_editor):
    """Accounts that an administrator created earlier were all marked "must change the password at first sign-in".
    Passwords handed over by the administrator are now used as they are, so the old marks are cleared."""
    apps.get_model('core', 'Profile').objects.filter(must_change_password=True).update(must_change_password=False)


class Migration(migrations.Migration):
    dependencies = [('core', '0014_review_unique_per_user')]
    operations = [
        migrations.RunPython(clear_forced_changes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='profile', name='must_change_password',
            field=models.BooleanField(default=False, help_text='Only when an administrator asked the person to choose their own password at the first sign-in')),
    ]
