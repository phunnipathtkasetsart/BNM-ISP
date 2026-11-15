"""Group work pages and API. The rules live in ``course.group_services``."""
from functools import wraps

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods, require_POST

from .. import group_services as rules
from ..group_services import GroupForbidden, GroupRuleError, work_url
from ..models import GroupInvitation, GroupJoinRequest, GroupMembership, GroupWork, StudentGroup
from ..permissions import course_access
from ..responses import fail, forbidden, is_api, payload, respond


def rule_errors(view):
    """Translate rule failures from group_services into responses.

    Failures are reported against the group work page the request came from.
    """
    @wraps(view)
    def wrapped(request, pk, work_id, *args, **kwargs):
        try:
            return view(request, pk, work_id, *args, **kwargs)
        except GroupForbidden:
            return forbidden(request)
        except GroupRuleError as error:
            return fail(request, error.message, work_url(pk, work_id),
                        status=error.status, html_message=error.html_message)
        except ValidationError as error:  # malformed request body
            return fail(request, error.messages[0], work_url(pk, work_id))
    return wrapped


def can_manage_group_works(request):
    return request.is_owner or request.is_ta


def get_work(request, work_id):
    return get_object_or_404(GroupWork, pk=work_id, course=request.course)


def get_group(request, work_id, group_id):
    work = get_work(request, work_id)
    return work, get_object_or_404(StudentGroup, pk=group_id, group_work=work)


def require_joinable(request):
    if not rules.student_can_join(request.course, request.user):
        raise GroupForbidden


# --- serializers ---------------------------------------------------------------------

def work_summary(work):
    return {"id": work.pk, "title": work.title, "max_members": work.max_members}


def invitation_data(invite):
    return {
        "id": invite.pk,
        "group_id": invite.group_id,
        "group_name": invite.group.name,
        "head_name": invite.group.head.get_full_name(),
    }


def member_data(group, member):
    return {
        "id": member.student_id,
        "name": member.student.get_full_name(),
        "student_id": member.student.nisit_id,
        "is_head": member.student_id == group.head_id,
    }


def _private_fields(group):
    """Fields only shown to members of the group, course staff and Department."""
    visible = group.can_view_members
    head = group.head
    return {
        "head": {"id": group.head_id, "name": head.get_full_name(), "student_id": head.nisit_id}
        if visible else None,
        "members": [member_data(group, m) for m in group.memberships.all()] if visible else [],
        "pending_invite_count": group.pending_invite_count if visible else None,
        "pending_request_count": group.pending_request_count if visible else None,
        "pending_reservation_count": group.pending_reservation_count if visible else None,
    }


def group_data(group, viewer_has_group):
    return {
        "id": group.pk,
        "name": group.name,
        "is_public": group.is_public,
        # Contact details are private to the group's own members.
        "third_party_contact": group.third_party_contact if group.is_current_group else None,
        **_private_fields(group),
        "member_count": group.member_count,
        "remaining_slots": group.remaining_slots,
        "is_full": group.is_full,
        "is_reserved": group.is_reserved,
        "is_member": group.is_current_group,
        "viewer_has_group": viewer_has_group,
        "has_pending_request": group.has_pending_request,
        "has_pending_invitation": group.has_pending_invitation,
        "can_join": group.can_join_public,
        "can_request": group.can_request_private,
        "join_requests": [
            {"id": r.pk, "requester_id": r.requester_id, "requester_name": r.requester.get_full_name()}
            for r in group.pending_requests
        ],
    }


# --- group work list -----------------------------------------------------------------

def works_with_my_group(request):
    course = request.course
    works = list(course.group_works.all())
    memberships = {
        row.group_work_id: row.group
        for row in GroupMembership.objects.filter(
            group_work__course=course, student=request.user
        ).select_related("group")
    }
    for work in works:
        work.my_group = memberships.get(work.pk)
    return works


def render_work_index(request, *, title="", max_members=""):
    works = works_with_my_group(request)
    can_manage = can_manage_group_works(request)
    if is_api(request):
        return JsonResponse({
            "group_works": [
                {**work_summary(work),
                 "my_group": {"id": work.my_group.pk, "name": work.my_group.name} if work.my_group else None}
                for work in works
            ],
            "can_manage": can_manage,
        })
    return render(request, "course/group_work_index.html", {
        "course": request.course,
        "group_works": works,
        "can_manage": can_manage,
        "can_join": rules.student_can_join(request.course, request.user),
        "title_value": title,
        "max_members_value": max_members,
    })


def create_work(request):
    course = request.course
    try:
        data = payload(request)
    except ValidationError as error:
        return fail(request, error.messages[0], request.path)
    title, max_members = data.get("title", ""), data.get("max_members", "")
    try:
        work = rules.create_group_work(course, request.user, title, max_members)
    except GroupRuleError as error:
        if is_api(request):
            return JsonResponse({"error": error.message}, status=error.status)
        # The browser form is shown again with what the user typed.
        messages.error(request, error.message)
        return render_work_index(request, title=title, max_members=max_members)
    return respond(request, work_summary(work), "Group work created.", work_url(course.pk, work.pk), status=201)


@course_access()
@require_http_methods(["GET", "POST"])
def group_work_index(request, pk):
    if request.method == "GET":
        return render_work_index(request)
    if not can_manage_group_works(request):
        return forbidden(request)
    return create_work(request)


# --- one group work ------------------------------------------------------------------

def _viewer_status(group, user):
    """Whether the viewer has a pending request to / invitation from this group."""
    group.has_pending_request = any(
        r.requester_id == user.pk and r.status == GroupJoinRequest.Status.PENDING
        for r in group.join_requests.all()
    )
    group.has_pending_invitation = any(
        i.invitee_id == user.pk and i.status == GroupInvitation.Status.PENDING
        for i in group.invitations.all()
    )


def _join_options(group, can_join):
    """Which join button the viewer gets: direct join (public) or request (private)."""
    open_group = can_join and not group.is_full
    # A pending invitation holds a seat for the viewer, so it counts as free for them.
    group.can_join_public = bool(
        open_group and group.is_public
        and (group.remaining_slots > 0 or group.has_pending_invitation)
    )
    group.can_request_private = bool(
        open_group and not group.is_public and group.remaining_slots > 0
        and not group.has_pending_request and not group.has_pending_invitation
    )


def describe_group(request, group, work, my_group_id, can_join):
    """Attach the counts and viewer-specific flags the page and API need."""
    for name, value in rules.group_counts(group, work).items():
        setattr(group, name, value)
    group.is_full = group.member_count >= work.max_members
    group.is_reserved = not group.is_full and group.remaining_slots == 0
    group.is_current_group = my_group_id == group.pk
    group.can_view_members = bool(
        can_manage_group_works(request) or request.is_dept_override or group.is_current_group
    )
    _viewer_status(group, request.user)
    group.pending_requests = [
        r for r in group.join_requests.all() if r.status == GroupJoinRequest.Status.PENDING
    ] if group.head_id == request.user.pk else []
    _join_options(group, can_join)


def described_groups(request, work, my_group_id, can_join):
    groups = list(work.groups.select_related("head").prefetch_related(
        "memberships__student", "invitations", "join_requests__requester"
    ))
    for group in groups:
        describe_group(request, group, work, my_group_id, can_join)
    # The viewer's own group first, then the emptiest groups.
    groups.sort(key=lambda g: (not g.is_current_group, g.member_count, g.created_at, g.pk))
    return groups


@course_access()
@require_http_methods(["GET"])
def group_work_detail(request, pk, work_id):
    work = get_work(request, work_id)
    membership = GroupMembership.objects.filter(
        group_work=work, student=request.user
    ).select_related("group", "group__head").first()
    my_group_id = membership.group_id if membership else None
    pending_invites = list(GroupInvitation.objects.filter(
        group_work=work, invitee=request.user, status=GroupInvitation.Status.PENDING,
    ).select_related("group", "group__head"))
    can_join = rules.student_can_join(request.course, request.user) and membership is None
    groups = described_groups(request, work, my_group_id, can_join)

    if is_api(request):
        return JsonResponse({
            **work_summary(work),
            "my_group_id": my_group_id,
            "pending_invitations": [invitation_data(invite) for invite in pending_invites],
            "groups": [group_data(group, membership is not None) for group in groups],
        })
    return render(request, "course/group_work_detail.html", {
        "course": request.course,
        "work": work,
        "groups": groups,
        "existing_group_names": list(work.groups.values_list("name", flat=True)),
        "current_group": membership.group if membership else None,
        "current_membership": membership,
        "pending_invites": pending_invites,
        "can_manage": can_manage_group_works(request),
        "can_join": can_join,
        "is_department": request.is_dept_override,
    })


# --- creating and joining groups -------------------------------------------------------

@course_access()
@require_POST
@rule_errors
def group_create(request, pk, work_id):
    work = get_work(request, work_id)
    require_joinable(request)
    data = payload(request)
    group = rules.create_group(work, request.user, data.get("name", ""), data.get("is_public", "false"))
    body = {"id": group.pk, "name": group.name, "head_id": group.head_id, "is_public": group.is_public,
            "member_count": 1, "max_members": work.max_members}
    return respond(request, body, "Your group has been created.", work_url(pk, work_id), status=201)


@course_access()
@require_POST
@rule_errors
def group_public_join(request, pk, work_id, group_id):
    work, group = get_group(request, work_id, group_id)
    require_joinable(request)
    group, membership, members_before = rules.join_public_group(work, group, request.user)
    body = {"group_id": group.pk, "membership_id": membership.pk,
            "member_count": members_before + 1, "max_members": work.max_members}
    return respond(request, body, f"You joined {group.name}.", work_url(pk, work_id), status=201)


@course_access()
@require_POST
@rule_errors
def group_join_request_create(request, pk, work_id, group_id):
    work, group = get_group(request, work_id, group_id)
    require_joinable(request)
    group, join_request = rules.request_to_join(work, group, request.user)
    body = {"id": join_request.pk, "group_id": group.pk, "status": join_request.status}
    return respond(request, body, f"Your request to join {group.name} was sent.",
                   work_url(pk, work_id), status=201)


@course_access()
@require_POST
@rule_errors
def group_join_request_action(request, pk, work_id, request_id, action):
    work = get_work(request, work_id)
    if action not in {"accept", "ignore"}:
        raise Http404
    accept = action == "accept"
    join_request, group = rules.respond_to_join_request(work, request_id, request.user, accept)
    message = (f"{join_request.requester.get_full_name()} joined {group.name}."
               if accept else "Join request ignored.")
    body = {"id": join_request.pk, "status": join_request.status, "group_id": join_request.group_id}
    return respond(request, body, message, work_url(pk, work_id))


# --- inside a group --------------------------------------------------------------------

@course_access()
@require_POST
@rule_errors
def group_invite(request, pk, work_id, group_id):
    work, group = get_group(request, work_id, group_id)
    if not (rules.is_head_member(group, request.user)
            and rules.student_can_join(request.course, request.user)):
        raise GroupForbidden
    nisit_id = payload(request).get("nisit_id", "")
    invitation = rules.invite_member(request.course, work, group, request.user, nisit_id)
    body = {"id": invitation.pk, "status": invitation.status,
            "invitee_id": invitation.invitee_id, "group_id": invitation.group_id}
    message = f"Invitation sent to {invitation.invitee.get_full_name()}."
    return respond(request, body, message, work_url(pk, work_id), status=201)


@course_access()
@require_POST
@rule_errors
def group_contact_update(request, pk, work_id, group_id):
    work, group = get_group(request, work_id, group_id)
    if not (rules.is_student(request.course, request.user)
            and rules.is_head_member(group, request.user)):
        raise GroupForbidden
    rules.update_contact(group, payload(request).get("third_party_contact", ""))
    body = {"group_id": group.pk, "third_party_contact": group.third_party_contact}
    return respond(request, body, "Third-party contact updated.", work_url(pk, work_id))


@course_access()
@require_POST
@rule_errors
def group_invitation_action(request, pk, work_id, invitation_id, action):
    work = get_work(request, work_id)
    if action not in {"accept", "decline"}:
        raise Http404
    invitation = rules.respond_to_invitation(
        request.course, work, invitation_id, request.user, accept=action == "accept",
    )
    message = f"You joined {invitation.group.name}." if action == "accept" else "Invitation declined."
    body = {"id": invitation.pk, "status": invitation.status, "group_id": invitation.group_id}
    return respond(request, body, message, work_url(pk, work_id))


def _disband(request, group):
    rules.disband(group, request.user)
    return {"disbanded": True}, "Your group has been disbanded."


def _leave(request, group):
    rules.leave(group, request.user)
    return {"left": True}, "You left the group."


def _kick(request, group, target, membership):
    rules.kick(group, target, membership)
    return {"removed": True, "student_id": target.pk}, "Group member removed."


def _transfer(request, group, target, membership):
    rules.transfer_head(group, target)
    name = target.get_full_name()
    return {"head_id": target.pk, "head_name": name}, f"Group headship transferred to {name}."


SELF_ACTIONS = {"disband": _disband, "leave": _leave}
HEAD_ACTIONS = {"kick": _kick, "transfer": _transfer}


def head_action(request, group, action):
    """Actions the group head performs on another member (kick, transfer)."""
    if not rules.is_head_member(group, request.user):
        raise GroupForbidden
    target, membership = rules.find_member(group, payload(request).get("student_id", ""))
    if action not in HEAD_ACTIONS:
        raise Http404
    return HEAD_ACTIONS[action](request, group, target, membership)


@course_access()
@require_POST
@rule_errors
def group_member_action(request, pk, work_id, group_id, action):
    _, group = get_group(request, work_id, group_id)
    if action in SELF_ACTIONS:
        body, message = SELF_ACTIONS[action](request, group)
    else:
        body, message = head_action(request, group, action)
    return respond(request, body, message, work_url(pk, work_id))
