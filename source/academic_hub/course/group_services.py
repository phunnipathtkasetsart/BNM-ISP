"""Group-work rules: groups, invitations, join requests and membership changes.

These functions hold the business rules so the views only translate requests
and results. A broken rule raises GroupRuleError (shown to the user) and a
missing right raises GroupForbidden (403); the views turn both into responses.
"""
from typing import Optional

from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from notifications.models import Notification
from notifications.services import (
    notify_group_invitation,
    notify_group_invitation_response,
    notify_group_join_request,
    notify_group_join_request_response,
    notify_group_member_kicked,
    notify_public_group_join,
)

from .models import (
    CourseTA,
    Enrollment,
    GroupInvitation,
    GroupJoinRequest,
    GroupMembership,
    GroupWork,
    StudentGroup,
)
from .permissions import is_student_user

TITLE_MAX = 150
GROUP_NAME_MAX = 120
CONTACT_MAX = 500
INVITE_PENDING = GroupInvitation.Status.PENDING
REQUEST_PENDING = GroupJoinRequest.Status.PENDING

ALREADY_IN_GROUP = "You already belong to a group for this work."
ALREADY_IN_GROUP_HTML = "You already belong to a group for this group work."
NO_FREE_SEAT = "That group is full or all remaining seats are reserved."
GROUP_CHANGED = "The group changed while you responded. Please review it and try again."


class GroupRuleError(Exception):
    """A rule the request broke. ``html_message`` is the browser wording, if different."""

    def __init__(self, message: str, *, status: int = 409, html_message: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.html_message = html_message or message


class GroupForbidden(Exception):
    """The user has no right to perform this group action."""


def work_url(course_id, work_id):
    return reverse("course:group_work", args=[course_id, work_id])


# --- who may do what -------------------------------------------------------------

def is_student(course, user):
    return is_student_user(user) and not course.ta_links.filter(user=user).exists()


def student_can_join(course, user):
    return is_student(course, user) and Enrollment.objects.filter(course=course, student=user).exists()


def is_member(group, user):
    return GroupMembership.objects.filter(group=group, student=user).exists()


def is_head_member(group, user):
    return group.head_id == user.pk and is_member(group, user)


def has_group(work, user):
    return GroupMembership.objects.filter(group_work=work, student=user).exists()


def has_pending_invite(group, user):
    return GroupInvitation.objects.filter(group=group, invitee=user, status=INVITE_PENDING).exists()


def has_pending_request(group, user):
    return GroupJoinRequest.objects.filter(group=group, requester=user, status=REQUEST_PENDING).exists()


def _require_no_group(work, user):
    if has_group(work, user):
        raise GroupRuleError(ALREADY_IN_GROUP, html_message=ALREADY_IN_GROUP_HTML)


# --- seats -------------------------------------------------------------------------

def group_counts(group, work):
    """Members, pending invites/requests, and seats still free.

    A pending invitation or join request reserves a seat; one student holding
    both still reserves only one.
    """
    member_count = len(group.memberships.all())
    pending_invites = [i for i in group.invitations.all() if i.status == INVITE_PENDING]
    pending_requests = [r for r in group.join_requests.all() if r.status == REQUEST_PENDING]
    reserved = {i.invitee_id for i in pending_invites} | {r.requester_id for r in pending_requests}
    return {
        "member_count": member_count,
        "pending_invite_count": len(pending_invites),
        "pending_request_count": len(pending_requests),
        "pending_reservation_count": len(reserved),
        "remaining_slots": max(0, work.max_members - member_count - len(reserved)),
    }


def _no_free_seat(group, work):
    counts = group_counts(group, work)
    return counts["member_count"] >= work.max_members or counts["remaining_slots"] == 0


def cancel_competing_join_paths(work, student, *, now=None):
    """Once a student joins a group, cancel their other pending invites and requests."""
    now = now or timezone.now()
    invitations = list(GroupInvitation.objects.filter(
        group_work=work, invitee=student, status=INVITE_PENDING,
    ).values_list("pk", flat=True))
    requests = list(GroupJoinRequest.objects.filter(
        group_work=work, requester=student, status=REQUEST_PENDING,
    ).values_list("pk", flat=True))

    if invitations:
        GroupInvitation.objects.filter(pk__in=invitations).update(
            status=GroupInvitation.Status.CANCELLED, responded_at=now,
        )
        Notification.objects.filter(
            recipient=student,
            event_key__in=[f"group-invitation:{pk}" for pk in invitations],
            read_at__isnull=True,
        ).update(read_at=now)
    if requests:
        GroupJoinRequest.objects.filter(pk__in=requests).update(
            status=GroupJoinRequest.Status.CANCELLED, responded_at=now,
        )
        Notification.objects.filter(
            event_key__in=[f"group-join-request:{pk}" for pk in requests],
            read_at__isnull=True,
        ).update(read_at=now)


def _lock(work, group=None):
    work = GroupWork.objects.select_for_update().get(pk=work.pk)
    if group is None:
        return work
    return work, StudentGroup.objects.select_for_update().get(pk=group.pk)


# --- group works -------------------------------------------------------------------

def create_group_work(course, user, title, max_members):
    title = title.strip()
    try:
        max_members = int(max_members)
    except (TypeError, ValueError):
        max_members = 0
    if not title or len(title) > TITLE_MAX:
        raise GroupRuleError("Enter a group work title no longer than 150 characters.", status=400)
    if max_members < 1:
        raise GroupRuleError("The maximum group size must be at least one.", status=400)
    try:
        with transaction.atomic():
            return GroupWork.objects.create(
                course=course, title=title, max_members=max_members, created_by=user,
            )
    except IntegrityError:
        raise GroupRuleError("A group work with that title already exists.", status=400)


# --- creating and joining groups ---------------------------------------------------

def create_group(work, user, name, public_value):
    public_value = public_value.strip().lower()
    if public_value not in {"true", "false"}:
        raise GroupRuleError("Choose whether the group is public or private.", status=400)
    name = name.strip()
    if not name or len(name) > GROUP_NAME_MAX:
        raise GroupRuleError("Enter a group name no longer than 120 characters.", status=400)
    try:
        with transaction.atomic():
            work = _lock(work)
            _require_no_group(work, user)
            group = StudentGroup.objects.create(
                group_work=work, name=name, head=user, is_public=public_value == "true",
            )
            GroupMembership.objects.create(group_work=work, group=group, student=user)
            cancel_competing_join_paths(work, user)
            return group
    except IntegrityError:
        raise GroupRuleError("That group name is already in use for this group work.")


def join_public_group(work, group, user):
    """Join a public group directly. Returns (group, membership, members before joining)."""
    if not group.is_public:
        raise GroupRuleError("Only public groups can be joined directly.",
                             html_message="Private groups require a join request.")
    try:
        with transaction.atomic():
            work, group = _lock(work, group)
            _require_no_group(work, user)
            counts = group_counts(group, work)
            # A student holding an invitation to this group may use the seat it reserves.
            full = counts["member_count"] >= work.max_members
            if full or (counts["remaining_slots"] == 0 and not has_pending_invite(group, user)):
                raise GroupRuleError(NO_FREE_SEAT)
            membership = GroupMembership.objects.create(group_work=work, group=group, student=user)
            cancel_competing_join_paths(work, user)
            notify_public_group_join(group, user, membership)
    except IntegrityError:
        raise GroupRuleError("The group changed while you joined. Please review it and try again.")
    return group, membership, counts["member_count"]


def request_to_join(work, group, user):
    """Ask the head of a private group to let ``user`` in. Returns (group, join request)."""
    if group.is_public:
        raise GroupRuleError("Public groups can be joined directly.")
    try:
        with transaction.atomic():
            work, group = _lock(work, group)
            _require_no_group(work, user)
            if has_pending_invite(group, user):
                raise GroupRuleError("You already have an invitation to this group.")
            if has_pending_request(group, user):
                raise GroupRuleError("You already requested to join this group.")
            if _no_free_seat(group, work):
                raise GroupRuleError(NO_FREE_SEAT)
            join_request = GroupJoinRequest.objects.create(group_work=work, group=group, requester=user)
            notify_group_join_request(join_request)
    except IntegrityError:
        raise GroupRuleError("That request could not be created. Please review the group and try again.")
    return group, join_request


# --- invitations -------------------------------------------------------------------

def find_invitee(course, nisit_id):
    invitee = User.objects.filter(nisit_id=nisit_id.strip(), is_active=True).first()
    if invitee is None or invitee.is_staff or invitee.is_superuser:
        raise GroupRuleError("Enter the Nisit ID of an active student in this course.", status=400)
    if not Enrollment.objects.filter(course=course, student=invitee).exists():
        raise GroupRuleError("That student is not enrolled in this course.", status=400)
    if CourseTA.objects.filter(course=course, user=invitee).exists():
        raise GroupRuleError("Teaching assistants cannot join student groups.", status=400)
    return invitee


def invite_member(course, work, group, inviter, nisit_id):
    invitee = find_invitee(course, nisit_id)
    already_invited = "That student already has a pending invitation."
    try:
        with transaction.atomic():
            work, group = _lock(work, group)
            if has_group(work, invitee):
                raise GroupRuleError("That student already belongs to a group for this work.")
            if has_pending_invite(group, invitee):
                raise GroupRuleError(already_invited)
            if has_pending_request(group, invitee):
                raise GroupRuleError("That student has already requested to join this group.")
            if _no_free_seat(group, work):
                raise GroupRuleError("Your group has reached its maximum size.")
            invitation = GroupInvitation.objects.create(
                group_work=work, group=group, invitee=invitee, invited_by=inviter,
            )
            notify_group_invitation(invitation)
    except IntegrityError:
        raise GroupRuleError(already_invited)
    return invitation


def _mark_read(recipient, event_key):
    Notification.objects.filter(
        recipient=recipient, event_key=event_key, read_at__isnull=True,
    ).update(read_at=timezone.now())


def _accept_into(work, group, student, *, already_message, already_html=None):
    if has_group(work, student):
        raise GroupRuleError(already_message, html_message=already_html)
    if group.memberships.count() >= work.max_members:
        raise GroupRuleError("That group has reached its maximum size.")
    GroupMembership.objects.create(group_work=work, group=group, student=student)


def respond_to_invitation(course, work, invitation_id, user, accept):
    """Accept or decline a pending invitation addressed to ``user``."""
    try:
        with transaction.atomic():
            work = _lock(work)
            invitation = get_object_or_404(
                GroupInvitation.objects.select_for_update().select_related("group", "group__head", "invitee"),
                pk=invitation_id, group_work=work, invitee=user, status=INVITE_PENDING,
            )
            if not student_can_join(course, user):
                raise GroupForbidden
            if accept:
                _accept_into(work, invitation.group, user, already_message=ALREADY_IN_GROUP)
                invitation.status = GroupInvitation.Status.ACCEPTED
            else:
                invitation.status = GroupInvitation.Status.DECLINED
            invitation.responded_at = timezone.now()
            invitation.save(update_fields=["status", "responded_at"])
            _mark_read(user, f"group-invitation:{invitation.pk}")
            notify_group_invitation_response(invitation)
            if accept:
                cancel_competing_join_paths(work, user)
    except IntegrityError:
        raise GroupRuleError(GROUP_CHANGED)
    return invitation


def respond_to_join_request(work, request_id, head, accept):
    """The group head accepts or ignores a pending join request. Returns (request, group)."""
    try:
        with transaction.atomic():
            work = _lock(work)
            join_request = get_object_or_404(
                GroupJoinRequest.objects.select_for_update().select_related("group", "group__head", "requester"),
                pk=request_id, group_work=work, group__head=head, status=REQUEST_PENDING,
            )
            group = StudentGroup.objects.select_for_update().get(pk=join_request.group_id)
            requester = join_request.requester
            if accept:
                _accept_into(work, group, requester,
                             already_message="That student already belongs to a group for this work.",
                             already_html="That student already belongs to a group for this group work.")
                join_request.status = GroupJoinRequest.Status.ACCEPTED
            else:
                join_request.status = GroupJoinRequest.Status.IGNORED
            join_request.responded_at = timezone.now()
            join_request.save(update_fields=["status", "responded_at"])
            _mark_read(group.head, f"group-join-request:{join_request.pk}")
            notify_group_join_request_response(join_request)
            if accept:
                cancel_competing_join_paths(work, requester)
    except IntegrityError:
        raise GroupRuleError(GROUP_CHANGED)
    return join_request, group


def update_contact(group, contact):
    contact = contact.strip()
    if len(contact) > CONTACT_MAX:
        raise GroupRuleError("Third-party contact must be 500 characters or fewer.", status=400)
    group.third_party_contact = contact
    group.save(update_fields=["third_party_contact"])


# --- membership --------------------------------------------------------------------

def disband(group, user):
    if not is_head_member(group, user):
        raise GroupForbidden
    group.delete()


def leave(group, user):
    if group.head_id == user.pk:
        raise GroupRuleError(
            "The group head must transfer headship or disband.",
            html_message="The group head must transfer headship or disband the group.",
        )
    deleted, _ = GroupMembership.objects.filter(group=group, student=user).delete()
    if not deleted:
        raise GroupForbidden


def find_member(group, student_id):
    try:
        target = User.objects.get(pk=student_id, is_active=True)
    except User.DoesNotExist:
        raise GroupRuleError("That group member could not be found.", status=404)
    membership = GroupMembership.objects.filter(group=group, student=target).first()
    if membership is None:
        raise GroupRuleError("That student is not a member of this group.", status=404)
    return target, membership


def kick(group, target, membership):
    if target.pk == group.head_id:
        raise GroupRuleError("The group head cannot remove themselves.")
    with transaction.atomic():
        membership.delete()
        notify_group_member_kicked(group, target, membership)


def transfer_head(group, target):
    group.head = target
    group.save(update_fields=["head"])
