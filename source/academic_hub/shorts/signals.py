from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Short


@receiver(post_delete, sender=Short)
def delete_clip_with_row(sender, instance, **kwargs):
    """Django never deletes stored files itself; without this, deleting a
    Short would leave its clip in the bucket forever."""
    if not instance.clip.name:
        return
    storage, name = instance.clip.storage, instance.clip.name
    transaction.on_commit(lambda: storage.delete(name))
