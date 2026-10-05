from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Material, PostAttachment


def _remove_file(instance):
    storage, name = instance.file.storage, instance.file.name
    if name:
        # After commit, so a rolled-back delete never loses a file that is
        # still referenced by a surviving row.
        transaction.on_commit(lambda: storage.delete(name))


@receiver(post_delete, sender=Material)
@receiver(post_delete, sender=PostAttachment)
def delete_file_with_row(sender, instance, **kwargs):
    """Django never removes files itself; deleting a row would orphan it."""
    _remove_file(instance)
