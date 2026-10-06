from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import CourseTA, Enrollment, Material, PostAttachment



def _remove_file(instance):
    if not getattr(instance, "file", None) or not instance.file.name:
        return
    storage, name = instance.file.storage, instance.file.name

    def cleanup():
        if (Material.objects.filter(file=name).exists()
                or PostAttachment.objects.filter(file=name).exists()):
            return
        storage.delete(name)

    transaction.on_commit(cleanup)


@receiver(post_delete, sender=Material)
@receiver(post_delete, sender=PostAttachment)
def delete_file_with_row(sender, instance, **kwargs):
    """Django never removes files itself; deleting a row would orphan it."""
    _remove_file(instance)


@receiver(post_delete, sender=Enrollment)
def drop_ta_with_enrollment(sender, instance, **kwargs):
    """Removed student loses TA role. Rejoin must not revive it."""
    CourseTA.objects.filter(course_id=instance.course_id, user_id=instance.student_id).delete()