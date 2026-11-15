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
    PostAttachment,
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
    from notifications.services import notify_roster_addition

    for student in students:
        enrollment, created = Enrollment.objects.get_or_create(course=course, student=student)
        if created:
            notify_roster_addition(course, student, enrollment.pk)
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


def resolve_topic(course, choice, new_topic=""):
    """Return the chosen topic, creating it for the "new topic" option.

    ``choice`` is the topic select value: a topic pk, the form's NEW_TOPIC
    marker, or empty for no topic. An unknown pk raises Http404.
    """
    from django.shortcuts import get_object_or_404

    from .forms import MaterialUploadForm

    if choice == MaterialUploadForm.NEW_TOPIC:
        return get_or_create_topic(course, new_topic or "")
    if choice:
        return get_object_or_404(course.topics, pk=choice)
    return None


def attach_uploads(post, uploads, topic, user):
    """Attach each upload to ``post``; with a topic, also file it under Materials.

    Returns the Material rows created (empty when ``topic`` is None).
    """
    materials = []
    for upload in uploads:
        attachment = PostAttachment.objects.create(post=post, file=upload)
        if topic is None:
            continue
        materials.append(Material.objects.create(
            course=post.course,
            topic=topic,
            created_by=user,
            title=attachment.original_name[:150],
            file=attachment.file.name,
            original_name=attachment.original_name,
            size=attachment.size,
        ))
    return materials


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
    from notifications.services import notify_course_material

    for material in created:
        notify_course_material(material)
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
        enrollment, enrolled = Enrollment.objects.get_or_create(course=course, student=student)
        _, created = CourseTA.objects.get_or_create(course=course, user=student)
        if enrolled:
            from notifications.services import notify_roster_addition

            notify_roster_addition(course, student, enrollment.pk)

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
    from notifications.services import create_notification

    if isinstance(created_item, CourseQuestion):
        course = created_item.course
        recipients = [course.owner]
        recipients.extend(link.user for link in course.ta_links.select_related("user"))
        for recipient in recipients:
            if recipient.pk == created_item.author_id:
                continue
            create_notification(
                recipient=recipient,
                kind="course_question",
                event_key=f"course-question:{created_item.pk}:{recipient.pk}",
                title="New course question",
                message=created_item.title,
                url=f"/course/{course.pk}/",
            )
    elif isinstance(created_item, CourseAnswer):
        question = created_item.question
        if question.author_id and question.author_id != created_item.author_id:
            create_notification(
                recipient=question.author,
                kind="course_answer",
                event_key=f"course-answer:{created_item.pk}",
                title="New answer to your question",
                message=question.title,
                url=f"/course/{question.course_id}/",
            )


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
    """Archive a course announcement and any materials created from its attachments."""
    now = timezone.now()
    post.archived_at = now
    post.archived_by = user
    post.save(update_fields=["archived_at", "archived_by", "updated_at"])

    # Find and archive materials sharing the same uploaded file path
    attachment_files = list(post.attachments.values_list("file", flat=True))
    if attachment_files:
        Material.objects.filter(
            course=post.course,
            file__in=attachment_files,
            archived_at__isnull=True,
        ).update(archived_at=now, archived_by=user)


def restore_post(post):
    """Restore an archived announcement and its corresponding materials."""
    post.archived_at = None
    post.archived_by = None
    post.save(update_fields=["archived_at", "archived_by", "updated_at"])

    # Restore materials sharing the same uploaded file path
    attachment_files = list(post.attachments.values_list("file", flat=True))
    if attachment_files:
        Material.objects.filter(
            course=post.course,
            file__in=attachment_files,
            archived_at__isnull=False,
        ).update(archived_at=None, archived_by=None)


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

def archive_course(course, user):
    """Mark a course as archived."""
    course.archived_at = timezone.now()
    course.archived_by = user
    course.save(update_fields=["archived_at", "archived_by"])

def restore_course(course):
    """Restore an archived course to active state."""
    course.archived_at = None
    course.archived_by = None
    course.save(update_fields=["archived_at", "archived_by"])