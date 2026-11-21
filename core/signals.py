from django.conf import settings
import logging

from django.db import models as dj_models
from django.db import transaction
from django.db.models import Avg
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .models import Batch, Enrollment, Profile, Review


log = logging.getLogger(__name__)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_profile(sender, instance, created, raw=False, **kwargs):
    """Every user gets a Profile. Superusers are admins."""
    if raw:
        return

    profile, _ = Profile.objects.get_or_create(user=instance)
    if created and instance.is_superuser and profile.role != 'admin':
        profile.role = 'admin'
        profile.save(update_fields=['role'])


@receiver([post_save, post_delete], sender=Review)
def refresh_course_rating(sender, instance, **kwargs):
    avg = instance.course.reviews.aggregate(a=Avg('rating'))['a'] or 0
    type(instance.course).objects.filter(pk=instance.course_id).update(rating_avg=round(avg, 2))


@receiver([post_save, post_delete], sender=Enrollment)
def recount_for_enrollment(sender, instance, **kwargs):
    from .services import recount
    recount(instance.course_id, instance.batch_id)


@receiver(post_save, sender=Batch)
def recount_for_batch(sender, instance, **kwargs):
    from .services import recount
    recount(instance.course_id, instance.pk)


# ---------------------------------------------------------------------------
# Uploaded files: remove them from disk when their record is deleted or the file is replaced
# (certificates, payment proofs, recordings, thumbnails, avatars, blog covers, gallery images ...)
# ---------------------------------------------------------------------------
def _file_fields(model):
    return [f for f in model._meta.get_fields() if isinstance(f, dj_models.FileField)]


def _remove(storage, name):
    def run():
        try:
            storage.delete(name)
        except Exception:      # a missing or locked file must never break the request
            log.warning('Could not delete file %s', name, exc_info=True)
    transaction.on_commit(run)


@receiver(post_delete)
def delete_files_with_record(sender, instance, **kwargs):
    if sender._meta.app_label != 'core':
        return
    for field in _file_fields(sender):
        f = getattr(instance, field.name, None)
        if f and f.name:
            _remove(f.storage, f.name)


@receiver(pre_save)
def delete_replaced_files(sender, instance, raw=False, **kwargs):
    if raw or sender._meta.app_label != 'core' or not instance.pk:
        return
    fields = _file_fields(sender)
    if not fields:
        return
    old = sender.objects.filter(pk=instance.pk).first()
    if not old:
        return
    for field in fields:
        before, after = getattr(old, field.name), getattr(instance, field.name)
        if before and before.name and before.name != (after.name if after else ''):
            _remove(before.storage, before.name)
