from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

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
from .permissions import course_access
from .views import payload


def _can_manage_group_works(request):
    return request.is_owner or request.is_ta


def _is_api(request):
    return request.path.startswith("/course/api/")


def _forbidden(request):
    if _is_api(request):
        return JsonResponse({"error": "You do not have permission for this action."}, status=403)
    return render(request, "accounts/no_access.html", status=403)


def _is_student(request):
    return not (
        request.user.is_staff
        or request.user.is_superuser
        or request.course.ta_links.filter(user=request.user).exists()
    )


def _student_can_join(request):
    return (
        _is_student(request)
        and Enrollment.objects.filter(course=request.course, student=request.user).exists()
    )


def _work_url(course_id, work_id):
    return reverse("course:group_work", args=[course_id, work_id])


def _group_counts(group, work):
    member_count = len(group.memberships.all())
    pending_invites = [
        invite for invite in group.invitations.all()
        if invite.status == GroupInvitation.Status.PENDING
    ]
    pending_requests = [
        join_request for join_request in group.join_requests.all()
        if join_request.status == GroupJoinRequest.Status.PENDING
    ]
    pending_invite_count = len(pending_invites)
    pending_request_count = len(pending_requests)
    reserved_student_ids = {
        invite.invitee_id for invite in pending_invites
    } | {
        join_request.requester_id for join_request in pending_requests
    }
    remaining_slots = max(
        0, work.max_members - member_count - len(reserved_student_ids)
    )
    return {
        "member_count": member_count,
        "pending_invite_count": pending_invite_count,
        "pending_request_count": pending_request_count,
        "pending_reservation_count": len(reserved_student_ids),
        "remaining_slots": remaining_slots,
    }


def _cancel_competing_join_paths(work, student, *, now=None):
    now = now or timezone.now()
    invitations = list(GroupInvitation.objects.filter(
        group_work=work,
        invitee=student,
        status=GroupInvitation.Status.PENDING,
    ).values_list("pk", flat=True))
    requests = list(GroupJoinRequest.objects.filter(
        group_work=work,
        requester=student,
        status=GroupJoinRequest.Status.PENDING,
    ).values_list("pk", flat=True))

    if invitations:
        GroupInvitation.objects.filter(pk__in=invitations).update(
            status=GroupInvitation.Status.CANCELLED,
            responded_at=now,
        )
        Notification.objects.filter(
            recipient=student,
            event_key__in=[f"group-invitation:{invitation_id}" for invitation_id in invitations],
            read_at__isnull=True,
        ).update(read_at=now)
    if requests:
        GroupJoinRequest.objects.filter(pk__in=requests).update(
            status=GroupJoinRequest.Status.CANCELLED,
            responded_at=now,
        )
        Notification.objects.filter(
            event_key__in=[f"group-join-request:{request_id}" for request_id in requests],
            read_at__isnull=True,
        ).update(read_at=now)


def _invalid_payload_response(request, error, course, work=None):
    message = error.messages[0]
    if _is_api(request):
        return JsonResponse({"error": message}, status=400)
    messages.error(request, message)
    if work is not None:
        return redirect(_work_url(course.pk, work.pk))
    return redirect("course:group_work_index", pk=course.pk)


@course_access()
@require_http_methods(["GET", "POST"])
def group_work_index(request, pk):
    course = request.course
    can_manage = _can_manage_group_works(request)
    title_value = ""
    max_members_value = ""

    if request.method == "POST":
        if not can_manage:
            return _forbidden(request)
        try:
            data = payload(request)
        except ValidationError as error:
            return _invalid_payload_response(request, error, course)
        title_value = data.get("title", "")
        max_members_value = data.get("max_members", "")
        title = title_value.strip()
        try:
            max_members = int(max_members_value)
        except (TypeError, ValueError):
            max_members = 0
        if not title or len(title) > 150:
            error = "Enter a group work title no longer than 150 characters."
            if _is_api(request):
                return JsonResponse({"error": error}, status=400)
            messages.error(request, error)
        elif max_members < 1:
            error = "The maximum group size must be at least one."
            if _is_api(request):
                return JsonResponse({"error": error}, status=400)
            messages.error(request, error)
        else:
            try:
                with transaction.atomic():
                    work = GroupWork.objects.create(
                        course=course,
                        title=title,
                        max_members=max_members,
                        created_by=request.user,
                    )
            except IntegrityError:
                error = "A group work with that title already exists."
                if _is_api(request):
                    return JsonResponse({"error": error}, status=400)
                messages.error(request, error)
            else:
                if _is_api(request):
                    return JsonResponse({
                        "id": work.pk,
                        "title": work.title,
                        "max_members": work.max_members,
                    }, status=201)
                messages.success(request, "Group work created.")
                return redirect(_work_url(course.pk, work.pk))

    works = list(course.group_works.all())
    memberships = {
        row.group_work_id: row.group
        for row in GroupMembership.objects.filter(
            group_work__course=course, student=request.user
        ).select_related("group")
    }
    for work in works:
        work.my_group = memberships.get(work.pk)
    if _is_api(request):
        return JsonResponse({
            "group_works": [
                {
                    "id": work.pk,
                    "title": work.title,
                    "max_members": work.max_members,
                    "my_group": (
                        {"id": work.my_group.pk, "name": work.my_group.name}
                        if work.my_group else None
                    ),
                }
                for work in works
            ],
            "can_manage": can_manage,
        })
    return render(request, "course/group_work_index.html", {
        "course": course,
        "group_works": works,
        "can_manage": can_manage,
        "can_join": _student_can_join(request),
        "title_value": title_value,
        "max_members_value": max_members_value,
    })


@course_access()
@require_http_methods(["GET"])
def group_work_detail(request, pk, work_id):
    course = request.course
    work = get_object_or_404(GroupWork, pk=work_id, course=course)
    can_manage = _can_manage_group_works(request)
    current_membership = GroupMembership.objects.filter(
        group_work=work, student=request.user
    ).select_related("group", "group__head").first()
    pending_invites = list(GroupInvitation.objects.filter(
        group_work=work,
        invitee=request.user,
        status=GroupInvitation.Status.PENDING,
    ).select_related("group", "group__head"))
    can_join = _student_can_join(request) and current_membership is None

    groups = work.groups.select_related("head").prefetch_related(
        "memberships__student", "invitations", "join_requests__requester"
    )
    groups = list(groups)
    for group in groups:
        counts = _group_counts(group, work)
        group.member_count = counts["member_count"]
        group.pending_invite_count = counts["pending_invite_count"]
        group.pending_request_count = counts["pending_request_count"]
        group.pending_reservation_count = counts["pending_reservation_count"]
        group.remaining_slots = counts["remaining_slots"]
        group.is_full = group.member_count >= work.max_members
        group.is_reserved = not group.is_full and group.remaining_slots == 0
        group.is_current_group = bool(
            current_membership and current_membership.group_id == group.pk
        )
        group.can_view_members = bool(
            can_manage
            or request.is_dept_override
            or group.is_current_group
        )
        group.has_pending_request = any(
            join_request.requester_id == request.user.pk
            and join_request.status == GroupJoinRequest.Status.PENDING
            for join_request in group.join_requests.all()
        )
        group.has_pending_invitation = any(
            invite.invitee_id == request.user.pk
            and invite.status == GroupInvitation.Status.PENDING
            for invite in group.invitations.all()
        )
        group.pending_requests = [
            join_request
            for join_request in group.join_requests.all()
            if join_request.status == GroupJoinRequest.Status.PENDING
        ] if group.head_id == request.user.pk else []
        owns_reserved_slot = group.has_pending_invitation
        group.can_join_public = bool(
            can_join
            and group.is_public
            and not group.is_full
            and (group.remaining_slots > 0 or owns_reserved_slot)
        )
        group.can_request_private = bool(
            can_join
            and not group.is_public
            and not group.is_full
            and group.remaining_slots > 0
            and not group.has_pending_request
            and not group.has_pending_invitation
        )
    groups.sort(key=lambda group: (
        not group.is_current_group,
        group.member_count,
        group.created_at,
        group.pk,
    ))

    if _is_api(request):
        return JsonResponse({
            "id": work.pk,
            "title": work.title,
            "max_members": work.max_members,
            "my_group_id": current_membership.group_id if current_membership else None,
            "pending_invitations": [
                {
                    "id": invite.pk,
                    "group_id": invite.group_id,
                    "group_name": invite.group.name,
                    "head_name": invite.group.head.get_full_name(),
                }
                for invite in pending_invites
            ],
            "groups": [
                {
                    "id": group.pk,
                    "name": group.name,
                    "is_public": group.is_public,
                    "third_party_contact": (
                        group.third_party_contact
                        if group.is_current_group
                        else None
                    ),
                    "head": ({
                        "id": group.head_id,
                        "name": group.head.get_full_name(),
                        "student_id": group.head.nisit_id,
                    } if group.can_view_members else None),
                    "members": [
                        {
                            "id": member.student_id,
                            "name": member.student.get_full_name(),
                            "student_id": member.student.nisit_id,
                            "is_head": member.student_id == group.head_id,
                        }
                        for member in group.memberships.all()
                    ] if group.can_view_members else [],
                    "member_count": group.member_count,
                    "pending_invite_count": (
                        group.pending_invite_count if group.can_view_members else None
                    ),
                    "pending_request_count": (
                        group.pending_request_count if group.can_view_members else None
                    ),
                    "pending_reservation_count": (
                        group.pending_reservation_count if group.can_view_members else None
                    ),
                    "remaining_slots": group.remaining_slots,
                    "is_full": group.is_full,
                    "is_reserved": group.is_reserved,
                    "is_member": group.is_current_group,
                    "viewer_has_group": current_membership is not None,
                    "has_pending_request": group.has_pending_request,
                    "has_pending_invitation": group.has_pending_invitation,
                    "can_join": group.can_join_public,
                    "can_request": group.can_request_private,
                    "join_requests": [
                        {
                            "id": join_request.pk,
                            "requester_id": join_request.requester_id,
                            "requester_name": join_request.requester.get_full_name(),
                        }
                        for join_request in group.pending_requests
                    ],
                }
                for group in groups
            ],
        })

    existing_group_names = list(work.groups.values_list("name", flat=True))
    return render(request, "course/group_work_detail.html", {
        "course": course,
        "work": work,
        "groups": groups,
        "existing_group_names": existing_group_names,
        "current_group": current_membership.group if current_membership else None,
        "current_membership": current_membership,
        "pending_invites": pending_invites,
        "can_manage": can_manage,
        "can_join": can_join,
        "is_department": request.is_dept_override,
    })


@course_access()
@require_POST
def group_create(request, pk, work_id):
    course = request.course
    work = get_object_or_404(GroupWork, pk=work_id, course=course)
    if not _student_can_join(request):
        return _forbidden(request)

    try:
        data = payload(request)
    except ValidationError as error:
        return _invalid_payload_response(request, error, course, work)
    name = data.get("name", "").strip()
    public_value = data.get("is_public", "false").strip().lower()
    if public_value not in {"true", "false"}:
        error = "Choose whether the group is public or private."
        if _is_api(request):
            return JsonResponse({"error": error}, status=400)
        messages.error(request, error)
        return redirect(_work_url(course.pk, work.pk))
    is_public = public_value == "true"
    if not name or len(name) > 120:
        if _is_api(request):
            return JsonResponse({"error": "Enter a group name no longer than 120 characters."}, status=400)
        messages.error(request, "Enter a group name no longer than 120 characters.")
        return redirect(_work_url(course.pk, work.pk))

    try:
        with transaction.atomic():
            work = GroupWork.objects.select_for_update().get(pk=work.pk)
            if GroupMembership.objects.filter(group_work=work, student=request.user).exists():
                if _is_api(request):
                    return JsonResponse({"error": "You already belong to a group for this work."}, status=409)
                messages.error(request, "You already belong to a group for this group work.")
                return redirect(_work_url(course.pk, work.pk))
            group = StudentGroup.objects.create(
                group_work=work,
                name=name,
                head=request.user,
                is_public=is_public,
            )
            GroupMembership.objects.create(
                group_work=work, group=group, student=request.user
            )
            _cancel_competing_join_paths(work, request.user)
    except IntegrityError:
        if _is_api(request):
            return JsonResponse({"error": "That group name is already in use for this group work."}, status=409)
        messages.error(request, "That group name is already in use for this group work.")
        return redirect(_work_url(course.pk, work.pk))

    if _is_api(request):
        return JsonResponse({
            "id": group.pk,
            "name": group.name,
            "head_id": group.head_id,
            "is_public": group.is_public,
            "member_count": 1,
            "max_members": work.max_members,
        }, status=201)
    messages.success(request, "Your group has been created.")
    return redirect(_work_url(course.pk, work.pk))


@course_access()
@require_POST
def group_public_join(request, pk, work_id, group_id):
    course = request.course
    work = get_object_or_404(GroupWork, pk=work_id, course=course)
    group = get_object_or_404(StudentGroup, pk=group_id, group_work=work)
    if not _student_can_join(request):
        return _forbidden(request)
    if not group.is_public:
        if _is_api(request):
            return JsonResponse({"error": "Only public groups can be joined directly."}, status=409)
        messages.error(request, "Private groups require a join request.")
        return redirect(_work_url(course.pk, work.pk))

    try:
        with transaction.atomic():
            work = GroupWork.objects.select_for_update().get(pk=work.pk)
            group = StudentGroup.objects.select_for_update().get(pk=group.pk)
            if GroupMembership.objects.filter(group_work=work, student=request.user).exists():
                if _is_api(request):
                    return JsonResponse({"error": "You already belong to a group for this work."}, status=409)
                messages.error(request, "You already belong to a group for this group work.")
                return redirect(_work_url(course.pk, work.pk))

            counts = _group_counts(group, work)
            owns_reserved_slot = GroupInvitation.objects.filter(
                group=group,
                invitee=request.user,
                status=GroupInvitation.Status.PENDING,
            ).exists()
            if counts["member_count"] >= work.max_members or (
                counts["remaining_slots"] == 0 and not owns_reserved_slot
            ):
                if _is_api(request):
                    return JsonResponse({"error": "That group is full or all remaining seats are reserved."}, status=409)
                messages.error(request, "That group is full or all remaining seats are reserved.")
                return redirect(_work_url(course.pk, work.pk))

            membership = GroupMembership.objects.create(
                group_work=work,
                group=group,
                student=request.user,
            )
            _cancel_competing_join_paths(work, request.user)
            notify_public_group_join(group, request.user, membership)
    except IntegrityError:
        if _is_api(request):
            return JsonResponse({"error": "The group changed while you joined. Please review it and try again."}, status=409)
        messages.error(request, "The group changed while you joined. Please review it and try again.")
        return redirect(_work_url(course.pk, work.pk))

    if _is_api(request):
        return JsonResponse({
            "group_id": group.pk,
            "membership_id": membership.pk,
            "member_count": counts["member_count"] + 1,
            "max_members": work.max_members,
        }, status=201)
    messages.success(request, f"You joined {group.name}.")
    return redirect(_work_url(course.pk, work.pk))


@course_access()
@require_POST
def group_join_request_create(request, pk, work_id, group_id):
    course = request.course
    work = get_object_or_404(GroupWork, pk=work_id, course=course)
    group = get_object_or_404(StudentGroup, pk=group_id, group_work=work)
    if not _student_can_join(request):
        return _forbidden(request)
    if group.is_public:
        if _is_api(request):
            return JsonResponse({"error": "Public groups can be joined directly."}, status=409)
        messages.error(request, "Public groups can be joined directly.")
        return redirect(_work_url(course.pk, work.pk))

    try:
        with transaction.atomic():
            work = GroupWork.objects.select_for_update().get(pk=work.pk)
            group = StudentGroup.objects.select_for_update().get(pk=group.pk)
            if GroupMembership.objects.filter(group_work=work, student=request.user).exists():
                if _is_api(request):
                    return JsonResponse({"error": "You already belong to a group for this work."}, status=409)
                messages.error(request, "You already belong to a group for this group work.")
                return redirect(_work_url(course.pk, work.pk))
            if GroupInvitation.objects.filter(
                group=group,
                invitee=request.user,
                status=GroupInvitation.Status.PENDING,
            ).exists():
                if _is_api(request):
                    return JsonResponse({"error": "You already have an invitation to this group."}, status=409)
                messages.error(request, "You already have an invitation to this group.")
                return redirect(_work_url(course.pk, work.pk))
            if GroupJoinRequest.objects.filter(
                group=group,
                requester=request.user,
                status=GroupJoinRequest.Status.PENDING,
            ).exists():
                if _is_api(request):
                    return JsonResponse({"error": "You already requested to join this group."}, status=409)
                messages.error(request, "You already requested to join this group.")
                return redirect(_work_url(course.pk, work.pk))
            counts = _group_counts(group, work)
            if counts["member_count"] >= work.max_members or counts["remaining_slots"] == 0:
                if _is_api(request):
                    return JsonResponse({"error": "That group is full or all remaining seats are reserved."}, status=409)
                messages.error(request, "That group is full or all remaining seats are reserved.")
                return redirect(_work_url(course.pk, work.pk))
            join_request = GroupJoinRequest.objects.create(
                group_work=work,
                group=group,
                requester=request.user,
            )
            notify_group_join_request(join_request)
    except IntegrityError:
        if _is_api(request):
            return JsonResponse({"error": "That request could not be created. Please review the group and try again."}, status=409)
        messages.error(request, "That request could not be created. Please review the group and try again.")
        return redirect(_work_url(course.pk, work.pk))

    if _is_api(request):
        return JsonResponse({
            "id": join_request.pk,
            "group_id": group.pk,
            "status": join_request.status,
        }, status=201)
    messages.success(request, f"Your request to join {group.name} was sent.")
    return redirect(_work_url(course.pk, work.pk))


@course_access()
@require_POST
def group_invite(request, pk, work_id, group_id):
    course = request.course
    work = get_object_or_404(GroupWork, pk=work_id, course=course)
    group = get_object_or_404(StudentGroup, pk=group_id, group_work=work)
    if (
        group.head_id != request.user.pk
        or not _student_can_join(request)
        or not GroupMembership.objects.filter(group=group, student=request.user).exists()
    ):
        return _forbidden(request)

    try:
        data = payload(request)
    except ValidationError as error:
        return _invalid_payload_response(request, error, course, work)
    nisit_id = data.get("nisit_id", "").strip()
    invitee = User.objects.filter(nisit_id=nisit_id, is_active=True).first()
    if invitee is None or invitee.is_staff or invitee.is_superuser:
        if _is_api(request):
            return JsonResponse({"error": "Enter the Nisit ID of an active student in this course."}, status=400)
        messages.error(request, "Enter the Nisit ID of an active student in this course.")
        return redirect(_work_url(course.pk, work.pk))
    if not Enrollment.objects.filter(course=course, student=invitee).exists():
        if _is_api(request):
            return JsonResponse({"error": "That student is not enrolled in this course."}, status=400)
        messages.error(request, "That student is not enrolled in this course.")
        return redirect(_work_url(course.pk, work.pk))
    if CourseTA.objects.filter(course=course, user=invitee).exists():
        if _is_api(request):
            return JsonResponse({"error": "Teaching assistants cannot join student groups."}, status=400)
        messages.error(request, "Teaching assistants cannot join student groups.")
        return redirect(_work_url(course.pk, work.pk))

    try:
        with transaction.atomic():
            work = GroupWork.objects.select_for_update().get(pk=work.pk)
            group = StudentGroup.objects.select_for_update().get(pk=group.pk)
            if GroupMembership.objects.filter(group_work=work, student=invitee).exists():
                if _is_api(request):
                    return JsonResponse({"error": "That student already belongs to a group for this work."}, status=409)
                messages.error(request, "That student already belongs to a group for this work.")
                return redirect(_work_url(course.pk, work.pk))
            if GroupInvitation.objects.filter(
                group=group, invitee=invitee, status=GroupInvitation.Status.PENDING
            ).exists():
                if _is_api(request):
                    return JsonResponse({"error": "That student already has a pending invitation."}, status=409)
                messages.error(request, "That student already has a pending invitation.")
                return redirect(_work_url(course.pk, work.pk))
            if GroupJoinRequest.objects.filter(
                group=group,
                requester=invitee,
                status=GroupJoinRequest.Status.PENDING,
            ).exists():
                if _is_api(request):
                    return JsonResponse({"error": "That student has already requested to join this group."}, status=409)
                messages.error(request, "That student has already requested to join this group.")
                return redirect(_work_url(course.pk, work.pk))
            counts = _group_counts(group, work)
            if counts["member_count"] >= work.max_members or counts["remaining_slots"] == 0:
                if _is_api(request):
                    return JsonResponse({"error": "Your group has reached its maximum size."}, status=409)
                messages.error(request, "Your group has reached its maximum size.")
                return redirect(_work_url(course.pk, work.pk))
            invitation = GroupInvitation.objects.create(
                group_work=work,
                group=group,
                invitee=invitee,
                invited_by=request.user,
            )
            notify_group_invitation(invitation)
    except IntegrityError:
        if _is_api(request):
            return JsonResponse({"error": "That student already has a pending invitation."}, status=409)
        messages.error(request, "That student already has a pending invitation.")
        return redirect(_work_url(course.pk, work.pk))

    if _is_api(request):
        return JsonResponse({
            "id": invitation.pk,
            "status": invitation.status,
            "invitee_id": invitation.invitee_id,
            "group_id": invitation.group_id,
        }, status=201)
    messages.success(request, f"Invitation sent to {invitee.get_full_name()}.")
    return redirect(_work_url(course.pk, work.pk))


@course_access()
@require_POST
def group_join_request_action(request, pk, work_id, request_id, action):
    course = request.course
    work = get_object_or_404(GroupWork, pk=work_id, course=course)
    if action not in {"accept", "ignore"}:
        raise Http404

    try:
        with transaction.atomic():
            work = GroupWork.objects.select_for_update().get(pk=work.pk)
            join_request = get_object_or_404(
                GroupJoinRequest.objects.select_for_update().select_related(
                    "group", "group__head", "requester"
                ),
                pk=request_id,
                group_work=work,
                group__head=request.user,
                status=GroupJoinRequest.Status.PENDING,
            )
            group = StudentGroup.objects.select_for_update().get(pk=join_request.group_id)
            if action == "accept":
                if GroupMembership.objects.filter(
                    group_work=work, student=join_request.requester
                ).exists():
                    if _is_api(request):
                        return JsonResponse({"error": "That student already belongs to a group for this work."}, status=409)
                    messages.error(request, "That student already belongs to a group for this group work.")
                    return redirect(_work_url(course.pk, work.pk))
                if group.memberships.count() >= work.max_members:
                    if _is_api(request):
                        return JsonResponse({"error": "That group has reached its maximum size."}, status=409)
                    messages.error(request, "That group has reached its maximum size.")
                    return redirect(_work_url(course.pk, work.pk))
                GroupMembership.objects.create(
                    group_work=work,
                    group=group,
                    student=join_request.requester,
                )
                join_request.status = GroupJoinRequest.Status.ACCEPTED
                messages.success(request, f"{join_request.requester.get_full_name()} joined {group.name}.")
            else:
                join_request.status = GroupJoinRequest.Status.IGNORED
                messages.success(request, "Join request ignored.")
            join_request.responded_at = timezone.now()
            join_request.save(update_fields=["status", "responded_at"])
            Notification.objects.filter(
                recipient=group.head,
                event_key=f"group-join-request:{join_request.pk}",
                read_at__isnull=True,
            ).update(read_at=timezone.now())
            notify_group_join_request_response(join_request)
            if action == "accept":
                _cancel_competing_join_paths(work, join_request.requester)
    except IntegrityError:
        if _is_api(request):
            return JsonResponse({"error": "The group changed while you responded. Please review it and try again."}, status=409)
        messages.error(request, "The group changed while you responded. Please review it and try again.")

    if _is_api(request):
        return JsonResponse({
            "id": join_request.pk,
            "status": join_request.status,
            "group_id": join_request.group_id,
        })
    return redirect(_work_url(course.pk, work.pk))


@course_access()
@require_POST
def group_contact_update(request, pk, work_id, group_id):
    course = request.course
    work = get_object_or_404(GroupWork, pk=work_id, course=course)
    group = get_object_or_404(StudentGroup, pk=group_id, group_work=work)
    if (
        not _is_student(request)
        or group.head_id != request.user.pk
        or not GroupMembership.objects.filter(
            group=group, group_work=work, student=request.user
        ).exists()
    ):
        return _forbidden(request)

    try:
        data = payload(request)
    except ValidationError as error:
        return _invalid_payload_response(request, error, course, work)
    contact = data.get("third_party_contact", "").strip()
    if len(contact) > 500:
        error = "Third-party contact must be 500 characters or fewer."
        if _is_api(request):
            return JsonResponse({"error": error}, status=400)
        messages.error(request, error)
        return redirect(_work_url(course.pk, work.pk))

    group.third_party_contact = contact
    group.save(update_fields=["third_party_contact"])
    if _is_api(request):
        return JsonResponse({
            "group_id": group.pk,
            "third_party_contact": group.third_party_contact,
        })
    messages.success(request, "Third-party contact updated.")
    return redirect(_work_url(course.pk, work.pk))


@course_access()
@require_POST
def group_invitation_action(request, pk, work_id, invitation_id, action):
    course = request.course
    work = get_object_or_404(GroupWork, pk=work_id, course=course)
    if action not in {"accept", "decline"}:
        raise Http404

    try:
        with transaction.atomic():
            work = GroupWork.objects.select_for_update().get(pk=work.pk)
            invitation = get_object_or_404(
                GroupInvitation.objects.select_for_update().select_related(
                    "group", "group__head", "invitee"
                ),
                pk=invitation_id,
                group_work=work,
                invitee=request.user,
                status=GroupInvitation.Status.PENDING,
            )
            if not _student_can_join(request):
                return _forbidden(request)
            if action == "accept":
                if GroupMembership.objects.filter(
                    group_work=work, student=request.user
                ).exists():
                    if _is_api(request):
                        return JsonResponse({"error": "You already belong to a group for this work."}, status=409)
                    messages.error(request, "You already belong to a group for this work.")
                    return redirect(_work_url(course.pk, work.pk))
                if invitation.group.memberships.count() >= work.max_members:
                    if _is_api(request):
                        return JsonResponse({"error": "That group has reached its maximum size."}, status=409)
                    messages.error(request, "That group has reached its maximum size.")
                    return redirect(_work_url(course.pk, work.pk))
                GroupMembership.objects.create(
                    group_work=work, group=invitation.group, student=request.user
                )
                invitation.status = GroupInvitation.Status.ACCEPTED
                messages.success(request, f"You joined {invitation.group.name}.")
            else:
                invitation.status = GroupInvitation.Status.DECLINED
                messages.success(request, "Invitation declined.")
            invitation.responded_at = timezone.now()
            invitation.save(update_fields=["status", "responded_at"])
            Notification.objects.filter(
                recipient=request.user,
                event_key=f"group-invitation:{invitation.pk}",
                read_at__isnull=True,
            ).update(read_at=timezone.now())
            notify_group_invitation_response(invitation)
            if action == "accept":
                _cancel_competing_join_paths(work, request.user)
    except IntegrityError:
        if _is_api(request):
            return JsonResponse({
                "error": "The group changed while you responded. Please review it and try again."
            }, status=409)
        messages.error(request, "The group changed while you responded. Please review it and try again.")

    if _is_api(request):
        return JsonResponse({
            "id": invitation.pk,
            "status": invitation.status,
            "group_id": invitation.group_id,
        })
    return redirect(_work_url(course.pk, work.pk))


@course_access()
@require_POST
def group_member_action(request, pk, work_id, group_id, action):
    course = request.course
    work = get_object_or_404(GroupWork, pk=work_id, course=course)
    group = get_object_or_404(StudentGroup, pk=group_id, group_work=work)

    if action == "disband":
        if (
            group.head_id != request.user.pk
            or not GroupMembership.objects.filter(group=group, student=request.user).exists()
        ):
            return _forbidden(request)
        group.delete()
        if _is_api(request):
            return JsonResponse({"disbanded": True})
        messages.success(request, "Your group has been disbanded.")
        return redirect(_work_url(course.pk, work.pk))

    if action == "leave":
        if group.head_id == request.user.pk:
            if _is_api(request):
                return JsonResponse({
                    "error": "The group head must transfer headship or disband."
                }, status=409)
            messages.error(request, "The group head must transfer headship or disband the group.")
            return redirect(_work_url(course.pk, work.pk))
        deleted, _ = GroupMembership.objects.filter(
            group=group, student=request.user
        ).delete()
        if not deleted:
            return _forbidden(request)
        if _is_api(request):
            return JsonResponse({"left": True})
        messages.success(request, "You left the group.")
        return redirect(_work_url(course.pk, work.pk))

    if (
        group.head_id != request.user.pk
        or not GroupMembership.objects.filter(group=group, student=request.user).exists()
    ):
        return _forbidden(request)
    try:
        data = payload(request)
    except ValidationError as error:
        return _invalid_payload_response(request, error, course, work)
    try:
        target_id = data.get("student_id", "")
        target = User.objects.get(pk=target_id, is_active=True)
    except User.DoesNotExist:
        if _is_api(request):
            return JsonResponse({"error": "That group member could not be found."}, status=404)
        messages.error(request, "That group member could not be found.")
        return redirect(_work_url(course.pk, work.pk))

    membership = GroupMembership.objects.filter(group=group, student=target).first()
    if membership is None:
        if _is_api(request):
            return JsonResponse({"error": "That student is not a member of this group."}, status=404)
        messages.error(request, "That student is not a member of this group.")
    elif action == "kick":
        if target.pk == group.head_id:
            if _is_api(request):
                return JsonResponse({"error": "The group head cannot remove themselves."}, status=409)
            messages.error(request, "The group head cannot remove themselves.")
        else:
            with transaction.atomic():
                membership.delete()
                notify_group_member_kicked(group, target, membership)
            if _is_api(request):
                return JsonResponse({"removed": True, "student_id": target.pk})
            messages.success(request, "Group member removed.")
    elif action == "transfer":
        group.head = target
        group.save(update_fields=["head"])
        if _is_api(request):
            return JsonResponse({"head_id": target.pk, "head_name": target.get_full_name()})
        messages.success(request, f"Group headship transferred to {target.get_full_name()}.")
    else:
        raise Http404
    return redirect(_work_url(course.pk, work.pk))
