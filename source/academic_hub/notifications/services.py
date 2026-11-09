from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from .models import Notification


def create_notification(*, recipient, kind, event_key, title, message="", url="/"):
    if not recipient.is_active:
        return None, False
    return Notification.objects.get_or_create(
        recipient=recipient,
        event_key=event_key,
        defaults={
            "kind": kind,
            "title": title[:160],
            "message": message[:500],
            "url": url[:500],
        },
    )


def notify_course_students(*, course, kind, event_key, title, message="", url=None,
                           exclude_user_id=None):
    created = []
    for enrollment in course.enrollments.select_related("student").iterator():
        student = enrollment.student
        if student.pk == exclude_user_id or not student.is_active:
            continue
        notice, was_created = create_notification(
            recipient=student,
            kind=kind,
            event_key=event_key,
            title=title,
            message=message,
            url=url or reverse("course:detail", args=[course.pk]),
        )
        if was_created:
            created.append(notice)
    return created


def notify_announcement(announcement):
    if not announcement.is_published or announcement.published_at > timezone.now():
        return []

    User = get_user_model()
    from announcements.models import Announcement

    visible = Announcement.objects.filter(pk=announcement.pk)
    created = []
    for user in User.objects.filter(is_active=True).exclude(nisit_id__startswith="G").iterator():
        if user.pk == announcement.author_id:
            continue
        if not visible.visible_to(user).filter(pk=announcement.pk).exists():
            continue
        notice, was_created = create_notification(
            recipient=user,
            kind="announcement",
            event_key=f"announcement:{announcement.pk}:{announcement.published_at.isoformat()}",
            title="New announcement",
            message=announcement.title,
            url=reverse("announcements:public_board"),
        )
        if was_created:
            created.append(notice)
        if announcement.deadline is not None:
            notice, was_created = create_notification(
                recipient=user,
                kind="deadline",
                event_key=(
                    f"announcement-deadline:{announcement.pk}:"
                    f"{announcement.deadline.isoformat()}"
                ),
                title="Announcement deadline",
                message=(
                    f"{announcement.title} — "
                    f"{timezone.localtime(announcement.deadline):%b %d, %H:%M}"
                ),
                url=reverse("announcements:public_board"),
            )
            if was_created:
                created.append(notice)
    return created


def notify_forum_reply(reply):
    question = reply.question
    recipient = question.author
    if recipient is None or recipient.pk == reply.author_id:
        return None, False

    return create_notification(
        recipient=recipient,
        kind="forum_reply",
        event_key=f"forum-reply:{reply.pk}",
        title="New comment on your FAQ question",
        message=reply.author_label,
        url=reverse("announcements:forum_question_detail", args=[question.pk]),
    )


def notify_course_post(post):
    return notify_course_students(
        course=post.course,
        kind="course_post",
        event_key=f"course-post:{post.pk}:{post.updated_at.isoformat()}",
        title="New course announcement",
        message=post.title,
        url=reverse("course:detail", args=[post.course_id]),
        exclude_user_id=post.author_id,
    )


def notify_course_material(material):
    return notify_course_students(
        course=material.course,
        kind="course_material",
        event_key=f"course-material:{material.pk}",
        title="New course material",
        message=material.title,
        url=reverse("course:detail", args=[material.course_id]),
        exclude_user_id=material.created_by_id,
    )


def notify_deadline(post):
    if post.deadline is None:
        return []
    return notify_course_students(
        course=post.course,
        kind="deadline",
        event_key=f"course-deadline:{post.pk}:{post.deadline.isoformat()}",
        title="Course deadline updated",
        message=f"{post.title} — {timezone.localtime(post.deadline):%b %d, %H:%M}",
        url=reverse("course:detail", args=[post.course_id]),
        exclude_user_id=post.author_id,
    )


def notify_roster_addition(course, student, enrollment_id):
    return create_notification(
        recipient=student,
        kind="course_enrollment",
        event_key=f"course-enrollment:{enrollment_id}",
        title="Added to a course",
        message=f"You have been added to {course.name}.",
        url=reverse("course:detail", args=[course.pk]),
    )


def notify_group_invitation(invitation):
    return create_notification(
        recipient=invitation.invitee,
        kind="group_invitation",
        event_key=f"group-invitation:{invitation.pk}",
        title="Group invitation",
        message=f"{invitation.group.name} invited you to {invitation.group_work.title}.",
        url=reverse("course:group_work", args=[
            invitation.group_work.course_id, invitation.group_work_id,
        ]),
    )


def refresh_time_sensitive_notifications(user, *, now=None):
    now = now or timezone.now()
    local_date = timezone.localdate(now)

    from calendar_app.models import DayNote
    from course.models import Enrollment
    from announcements.models import Announcement

    for note in DayNote.objects.filter(user=user, notify=True, date=local_date):
        create_notification(
            recipient=user,
            kind="calendar_reminder",
            event_key=f"calendar-note:{note.pk}:{note.date.isoformat()}",
            title="Calendar reminder",
            message=note.title,
            url=reverse("calendar:month"),
        )

    enrollments = Enrollment.objects.filter(student=user).select_related("course")
    for enrollment in enrollments:
        posts = enrollment.course.posts.filter(
            deadline__gt=now,
            deadline__lte=now + timedelta(hours=24),
        )
        for post in posts.iterator():
            if post.updated_at > post.deadline - timedelta(hours=24):
                continue
            create_notification(
                recipient=user,
                kind="deadline",
                event_key=f"course-deadline-24h:{post.pk}:{post.deadline.isoformat()}",
                title="Deadline in 24 hours",
                message=post.title,
                url=reverse("course:detail", args=[post.course_id]),
            )

    announcements = Announcement.objects.visible_to(user).filter(
        is_published=True,
        published_at__lte=now,
        deadline__gt=now,
        deadline__lte=now + timedelta(hours=24),
    )
    for announcement in announcements.iterator():
        if announcement.updated_at > announcement.deadline - timedelta(hours=24):
            continue
        create_notification(
            recipient=user,
            kind="deadline",
            event_key=(
                f"announcement-deadline-24h:{announcement.pk}:"
                f"{announcement.deadline.isoformat()}"
            ),
            title="Announcement deadline in 24 hours",
            message=announcement.title,
            url=reverse("announcements:public_board"),
        )

    # Invitation notifications are created when the invite is sent; this poll
    # also repairs any notices missed by an interrupted request.
    from course.models import GroupInvitation

    invites = GroupInvitation.objects.filter(
        invitee=user, status=GroupInvitation.Status.PENDING
    ).select_related("group", "group_work", "group_work__course")
    for invitation in invites.iterator():
        notify_group_invitation(invitation)
