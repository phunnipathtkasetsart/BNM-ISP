from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.functions import Lower, Trim

from .models import Course, Enrollment, Material, Topic, generate_class_code

DUPLICATE_CLASS = "You already have a class with this name and section. Use a different name or section."


def duplicate_class(owner, name, section, exclude_pk=None):
    return Course.objects.filter(owner=owner).exclude(pk=exclude_pk).annotate(
        normalized_name=Lower(Trim("name")), normalized_section=Lower(Trim("section")),
    ).filter(normalized_name=name.strip().lower(), normalized_section=section.strip().lower()).exists()


def create_course(*, owner, name, section=""):
    name, section = name.strip(), section.strip()
    if duplicate_class(owner, name, section):
        raise ValidationError(DUPLICATE_CLASS)
    # The unique constraint arbitrates concurrent requests, not an exists() precheck.
    for _ in range(10):
        code = generate_class_code()
        try:
            with transaction.atomic():
                return Course.objects.create(owner=owner, name=name, section=section, class_code=code)
        except IntegrityError:
            if duplicate_class(owner, name, section):
                raise ValidationError(DUPLICATE_CLASS)
            if not Course.objects.filter(class_code=code).exists():
                raise
    raise ValidationError("Could not allocate a class code. Please try again.")


def update_course(course, *, name, section):
    name, section = name.strip(), section.strip()
    if duplicate_class(course.owner, name, section, course.pk):
        raise ValidationError(DUPLICATE_CLASS)
    try:
        with transaction.atomic():
            Course.objects.filter(pk=course.pk).update(name=name, section=section)
    except IntegrityError:
        if duplicate_class(course.owner, name, section, course.pk):
            raise ValidationError(DUPLICATE_CLASS)
        raise
    course.refresh_from_db()


@transaction.atomic
def add_students(course, students):
    added = 0
    for student in students:
        _, created = Enrollment.objects.get_or_create(course=course, student=student)
        added += int(created)
    return added

def _topic_match(course, title):
    return (Topic.objects.annotate(key=Lower(Trim("title")))
            .filter(course=course, key=title.lower()).first())


def get_or_create_topic(course, title):
    title = " ".join(title.split())[:120]
    found = _topic_match(course, title)
    if found:
        return found
    try:
        with transaction.atomic():
            return Topic.objects.create(course=course, title=title)
    except IntegrityError:  # another request created it first
        return _topic_match(course, title)


def add_materials(course, topic, files, user):
    created = []
    try:
        with transaction.atomic():
            for upload in files:
                created.append(Material.objects.create(
                    course=course, topic=topic, file=upload,
                    title=upload.name[:150], created_by=user))
    except Exception:
        # Rows rolled back; remove the files already written to disk.
        for material in created:
            material.file.storage.delete(material.file.name)
        raise
    return created