from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from .models import Notification

if TYPE_CHECKING:
    from accounts.models import User
    from announcements.models import Announcement, ForumReply
    from course.models import (
        Course,
        CoursePost,
        GroupInvitation,
        GroupJoinRequest,
        GroupMembership,
        Material,
        StudentGroup,
    )


def create_notification(
    *,
    recipient: User,
    kind: str,
    event_key: str,
    title: str,
    message: str = "",
    url: str = "/",
) -> tuple[Notification | None, bool]:
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


def notify_course_students(
    *,
    course: Course,
    kind: str,
    event_key: str,
    title: str,
    message: str = "",
    url: str | None = None,
    exclude_user_id: str | None = None,
) -> list[Notification]:
    created: list[Notification] = []
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
        if was_created and notice is not None:
            created.append(notice)
    return created


def _notify_announcement_recipient(
    announcement: Announcement,
    recipient: User,
) -> list[Notification]:
    from announcements.models import Announcement as AnnouncementModel

    visible = AnnouncementModel.objects.filter(pk=announcement.pk)
    if not visible.visible_to(recipient).filter(pk=announcement.pk).exists():
        return []

    created: list[Notification] = []
    notice, was_created = create_notification(
        recipient=recipient,
        kind="announcement",
        event_key=(
            f"announcement:{announcement.pk}:"
            f"{announcement.published_at.isoformat()}"
        ),
        title="New announcement",
        message=announcement.title,
        url=reverse("announcements:public_board"),
    )
    if was_created and notice is not None:
        created.append(notice)

    if announcement.deadline is None:
        return created

    notice, was_created = create_notification(
        recipient=recipient,
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
    if was_created and notice is not None:
        created.append(notice)
    return created


def notify_announcement(
    announcement: Announcement,
) -> list[Notification]:
    if (
        not announcement.is_published
        or announcement.published_at > timezone.now()
    ):
        return []

    User = get_user_model()
    created: list[Notification] = []
    recipients = User.objects.filter(is_active=True).exclude(
        nisit_id__startswith="G"
    )
    for user in recipients.iterator():
        if user.pk == announcement.author_id:
            continue
        created.extend(_notify_announcement_recipient(announcement, user))
    return created


def notify_forum_reply(
    reply: ForumReply,
) -> tuple[Notification | None, bool]:
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


def notify_course_post(post: CoursePost) -> list[Notification]:
    return notify_course_students(
        course=post.course,
        kind="course_post",
        event_key=f"course-post:{post.pk}:{post.updated_at.isoformat()}",
        title="New course announcement",
        message=post.title,
        url=reverse("course:detail", args=[post.course_id]),
        exclude_user_id=post.author_id,
    )


def notify_course_material(material: Material) -> list[Notification]:
    return notify_course_students(
        course=material.course,
        kind="course_material",
        event_key=f"course-material:{material.pk}",
        title="New course material",
        message=material.title,
        url=reverse("course:detail", args=[material.course_id]),
        exclude_user_id=material.created_by_id,
    )


def notify_deadline(post: CoursePost) -> list[Notification]:
    if post.deadline is None:
        return []
    return notify_course_students(
        course=post.course,
        kind="deadline",
        event_key=f"course-deadline:{post.pk}:{post.deadline.isoformat()}",
        title="Course deadline updated",
        message=(
            f"{post.title} — "
            f"{timezone.localtime(post.deadline):%b %d, %H:%M}"
        ),
        url=reverse("course:detail", args=[post.course_id]),
        exclude_user_id=post.author_id,
    )


def notify_roster_addition(
    course: Course,
    student: User,
    enrollment_id: int,
) -> tuple[Notification | None, bool]:
    return create_notification(
        recipient=student,
        kind="course_enrollment",
        event_key=f"course-enrollment:{enrollment_id}",
        title="Added to a course",
        message=f"You have been added to {course.name}.",
        url=reverse("course:detail", args=[course.pk]),
    )


def notify_group_invitation(
    invitation: GroupInvitation,
) -> tuple[Notification | None, bool]:
    return create_notification(
        recipient=invitation.invitee,
        kind="group_invitation",
        event_key=f"group-invitation:{invitation.pk}",
        title="Group invitation",
        message=(
            f"{invitation.group.name} invited you to "
            f"{invitation.group_work.title}."
        ),
        url=reverse("course:group_work", args=[
            invitation.group_work.course_id, invitation.group_work_id,
        ]),
    )


def notify_group_invitation_response(
    invitation: GroupInvitation,
) -> tuple[Notification | None, bool]:
    return create_notification(
        recipient=invitation.group.head,
        kind="group_invitation_response",
        event_key=(
            f"group-invitation-response:{invitation.pk}:{invitation.status}"
        ),
        title="Group invitation response",
        message=(
            f"{invitation.invitee.get_full_name()} {invitation.status} "
            "your invitation."
        ),
        url=reverse("course:group_work", args=[
            invitation.group_work.course_id, invitation.group_work_id,
        ]),
    )


def notify_group_join_request(
    join_request: GroupJoinRequest,
) -> tuple[Notification | None, bool]:
    return create_notification(
        recipient=join_request.group.head,
        kind="group_join_request",
        event_key=f"group-join-request:{join_request.pk}",
        title="Group join request",
        message=(
            f"{join_request.requester.get_full_name()} requested to join "
            f"{join_request.group.name}."
        ),
        url=reverse("course:group_work", args=[
            join_request.group_work.course_id, join_request.group_work_id,
        ]),
    )


def notify_group_join_request_response(
    join_request: GroupJoinRequest,
) -> tuple[Notification | None, bool]:
    decision = "accepted" if join_request.status == "accepted" else "ignored"
    return create_notification(
        recipient=join_request.requester,
        kind="group_join_request_response",
        event_key=(
            f"group-join-request-response:{join_request.pk}:"
            f"{join_request.status}"
        ),
        title=f"Group join request {decision}",
        message=(
            f"Your request to join {join_request.group.name} was {decision}."
        ),
        url=reverse("course:group_work", args=[
            join_request.group_work.course_id, join_request.group_work_id,
        ]),
    )


def notify_public_group_join(
    group: StudentGroup,
    student: User,
    membership: GroupMembership,
) -> tuple[Notification | None, bool]:
    return create_notification(
        recipient=group.head,
        kind="group_member_joined",
        event_key=f"group-member-joined:{membership.pk}",
        title="New group member",
        message=f"{student.get_full_name()} joined {group.name}.",
        url=reverse("course:group_work", args=[
            group.group_work.course_id, group.group_work_id,
        ]),
    )


def notify_group_member_kicked(
    group: StudentGroup,
    student: User,
    membership: GroupMembership,
) -> tuple[Notification | None, bool]:
    return create_notification(
        recipient=student,
        kind="group_member_removed",
        event_key=f"group-member-removed:{membership.pk}",
        title="Removed from group",
        message=f"You were removed from {group.name}.",
        url=reverse("course:group_work", args=[
            group.group_work.course_id, group.group_work_id,
        ]),
    )


def _create_calendar_reminders(
    user: User,
    local_date: date,
) -> None:
    from calendar_app.models import DayNote

    notes = DayNote.objects.filter(
        user=user,
        notify=True,
        date=local_date,
    )
    for note in notes:
        create_notification(
            recipient=user,
            kind="calendar_reminder",
            event_key=f"calendar-note:{note.pk}:{note.date.isoformat()}",
            title="Calendar reminder",
            message=note.title,
            url=reverse("calendar:month"),
        )


def _create_course_deadline_reminders(
    user: User,
    now: datetime,
) -> None:
    from course.models import Enrollment

    enrollments = Enrollment.objects.filter(student=user).select_related(
        "course"
    )
    for enrollment in enrollments:
        posts = enrollment.course.posts.filter(
            deadline__gt=now,
            deadline__lte=now + timedelta(hours=24),
        )
        for post in posts.iterator():
            if (
                post.deadline is None
                or post.updated_at > post.deadline - timedelta(hours=24)
            ):
                continue
            create_notification(
                recipient=user,
                kind="deadline",
                event_key=(
                    f"course-deadline-24h:{post.pk}:"
                    f"{post.deadline.isoformat()}"
                ),
                title="Deadline in 24 hours",
                message=post.title,
                url=reverse("course:detail", args=[post.course_id]),
            )


def _create_announcement_deadline_reminders(
    user: User,
    now: datetime,
) -> None:
    from announcements.models import Announcement

    announcements = Announcement.objects.visible_to(user).filter(
        is_published=True,
        published_at__lte=now,
        deadline__gt=now,
        deadline__lte=now + timedelta(hours=24),
    )
    for announcement in announcements.iterator():
        if (
            announcement.deadline is None
            or announcement.updated_at
            > announcement.deadline - timedelta(hours=24)
        ):
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


def _repair_pending_invitation_notifications(user: User) -> None:
    from course.models import GroupInvitation

    # Invitation notifications are created when the invite is sent; this poll
    # also repairs any notices missed by an interrupted request.
    invites = GroupInvitation.objects.filter(
        invitee=user, status=GroupInvitation.Status.PENDING
    ).select_related("group", "group_work", "group_work__course")
    for invitation in invites.iterator():
        notify_group_invitation(invitation)


def refresh_time_sensitive_notifications(
    user: User,
    *,
    now: datetime | None = None,
) -> None:
    current_time = now or timezone.now()
    _create_calendar_reminders(user, timezone.localdate(current_time))
    _create_course_deadline_reminders(user, current_time)
    _create_announcement_deadline_reminders(user, current_time)
    _repair_pending_invitation_notifications(user)
