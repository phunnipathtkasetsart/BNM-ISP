from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from .models import Course, Enrollment, generate_class_code


def create_course(*, owner, name, section=""):
    # The unique constraint arbitrates concurrent requests, not an exists() precheck.
    for _ in range(10):
        code = generate_class_code()
        try:
            with transaction.atomic():
                return Course.objects.create(owner=owner, name=name, section=section, class_code=code)
        except IntegrityError:
            if not Course.objects.filter(class_code=code).exists():
                raise
    raise ValidationError("Could not allocate a class code. Please try again.")


@transaction.atomic
def add_students(course, students):
    added = 0
    for student in students:
        _, created = Enrollment.objects.get_or_create(course=course, student=student)
        added += int(created)
    return added
