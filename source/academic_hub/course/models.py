import secrets

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.functions import Lower, Trim


def generate_class_code():
    # O/0 and I/1 are omitted so codes are easier to read aloud.
    return "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))


class Course(models.Model):
    name = models.CharField(max_length=120)
    section = models.CharField(max_length=20, blank=True, default="")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name="owned_courses")
    class_code = models.CharField(max_length=6, unique=True, default=generate_class_code,
                                  editable=False, validators=[RegexValidator(r"^[A-Z0-9]{6}$")])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [models.UniqueConstraint(
            models.F("owner"), Lower(Trim("name")), Lower(Trim("section")),
            name="unique_owner_class_section",
        )]

    def __str__(self):
        return self.name


class Enrollment(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="enrollments")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="course_enrollments")
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["course", "student"],
                                               name="unique_course_student")]
        ordering = ["student__first_name", "student__last_name", "student_id"]
