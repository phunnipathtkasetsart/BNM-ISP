from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models.functions import Lower, Trim
from django.utils import timezone

from .models import (
    Course,
    CourseAnswer,
    CoursePost,
    CourseQuestion,
    CourseTA,
    Enrollment,
    Material,
    Topic,
    generate_class_code,
)

DUPLICATE_CLASS = "You already have a class with this name and section. Use a different name or section."


def duplicate_class(owner, name, section, exclude_pk=None):
    return Course.objects.filter(owner=owner).exclude(pk=exclude_pk).annotate(
        normalized_name=Lower(Trim("name")),
        normalized_section=Lower(Trim("section")),
    ).filter(
        normalized_name=name.strip().lower(),
        normalized_section=section.strip().lower(),
    ).exists()


def create_course(*, owner, name, section=""):
    name, section = name.strip(), section.strip()
    if duplicate_class(owner, name, section):
        raise ValidationError(DUPLICATE_CLASS)
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
    return (
        Topic.objects.annotate(key=Lower(Trim("title")))
        .filter(course=course, key=title.lower())
        .first()
    )


def get_or_create_topic(course, title):
    title = " ".join(title.split())[:120]
    found = _topic_match(course, title)
    if found:
        return found
    try:
        with transaction.atomic():
            return Topic.objects.create(course=course, title=title)
    except IntegrityError:
        return _topic_match(course, title)


def add_materials(course, topic, files, user):
    created = []
    try:
        with transaction.atomic():
            for upload in files:
                created.append(
                    Material.objects.create(
                        course=course,
                        topic=topic,
                        file=upload,
                        title=upload.name[:150],
                        created_by=user,
                    )
                )
    except Exception:
        for material in created:
            material.file.storage.delete(material.file.name)
        raise
    return created


def assign_ta(course, student_id):
    User = get_user_model()
    target_id = str(student_id).strip()

    student = User.objects.filter(
        models.Q(nisit_id=target_id) | models.Q(pk=target_id),
        is_active=True,
    ).first()

    if not student:
        raise ValidationError(f"No active student found with ID '{target_id}'. Check the Nisit ID and try again.")
    if student.is_staff or student.is_superuser:
        raise ValidationError("Staff or department users cannot be assigned as TAs.")

    with transaction.atomic():
        Enrollment.objects.get_or_create(course=course, student=student)
        _, created = CourseTA.objects.get_or_create(course=course, user=student)

    return created


def remove_ta(course, student_id):
    deleted, _ = CourseTA.objects.filter(course=course, user_id=student_id).delete()
    return bool(deleted)


def qa_author_role_label(user, course):
    if user.is_superuser:
        return "Department"
    if CourseTA.objects.filter(course=course, user=user).exists():
        return "Teaching Assistant"
    if user.is_staff:
        return "Lecturer"
    return "Student"


def qa_notification_hook(created_item):
    return None


def create_course_question(*, course, author, title, body):
    with transaction.atomic():
        question = CourseQuestion.objects.create(
            course=course,
            author=author,
            author_label=author.get_full_name().strip(),
            author_role_label=qa_author_role_label(author, course),
            title=title,
            body=body,
        )
        transaction.on_commit(lambda: qa_notification_hook(question))
    return question


def create_course_answer(*, question, author, body):
    with transaction.atomic():
        answer = CourseAnswer.objects.create(
            question=question,
            author=author,
            author_label=author.get_full_name().strip(),
            author_role_label=qa_author_role_label(author, question.course),
            body=body,
        )
        transaction.on_commit(lambda: qa_notification_hook(answer))
    return answer


# --- Soft-delete / Archive Helpers ---

def archive_post(post, user):
    """Mark an announcement as archived."""
    post.archived_at = timezone.now()
    post.archived_by = user
    post.save(update_fields=["archived_at", "archived_by", "updated_at"])


def restore_post(post):
    """Restore an archived announcement to active state."""
    post.archived_at = None
    post.archived_by = None
    post.save(update_fields=["archived_at", "archived_by", "updated_at"])


def archive_material(material, user):
    """Mark a material file as archived."""
    material.archived_at = timezone.now()
    material.archived_by = user
    material.save(update_fields=["archived_at", "archived_by", "updated_at"])


def restore_material(material):
    """Restore an archived material file to active state."""
    material.archived_at = None
    material.archived_by = None
    material.save(update_fields=["archived_at", "archived_by", "updated_at"])