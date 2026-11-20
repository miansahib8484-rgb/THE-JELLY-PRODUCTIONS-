"""Private file storage for certificates, payment proofs and class recordings.

Files live in PRIVATE_MEDIA_ROOT, which is NOT served at any URL. They can only be opened through
the permission-checked views in core/files.py.
"""
import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.signals import setting_changed
from django.dispatch import receiver
from django.utils.functional import cached_property


class PrivateStorage(FileSystemStorage):
    @cached_property
    def base_location(self):
        return str(settings.PRIVATE_MEDIA_ROOT)

    @cached_property
    def location(self):
        return os.path.abspath(self.base_location)

    @cached_property
    def file_permissions_mode(self):
        return 0o600           # payment proofs, certificates and recordings: readable by the app user only

    @cached_property
    def directory_permissions_mode(self):
        return 0o700

    @property
    def base_url(self):
        return None

    def url(self, name):
        raise ValueError('Private files have no public URL. Use the protected download view.')


@receiver(setting_changed)
def _reset_private_storage(*, setting, **kwargs):
    if setting == 'PRIVATE_MEDIA_ROOT':
        for obj in list(_instances):
            obj.__dict__.pop('base_location', None)
            obj.__dict__.pop('location', None)


_instances = []
_orig_init = PrivateStorage.__init__


def _tracking_init(self, *a, **kw):
    _orig_init(self, *a, **kw)
    _instances.append(self)


PrivateStorage.__init__ = _tracking_init
