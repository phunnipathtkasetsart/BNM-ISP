from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import CourseTA, Enrollment, Material, PostAttachment


def _remove_file(instance):
    storage, name = instance.file.storage, instance.file.name
    if name:
        # After commit, so a rolled-back delete never loses a file that is
        # still referenced by a surviving row.
        transaction.on_commit(lambda: storage.delete(name))


@receiver(post_delete, sender=Enrollment)
def drop_ta_with_enrollment(sender, instance, **kwargs):
    """Removed student loses TA role. Rejoin must not revive it."""
    CourseTA.objects.filter(course_id=instance.course_id, user_id=instance.student_id).delete()