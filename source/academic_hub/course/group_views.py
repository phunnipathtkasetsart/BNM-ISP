from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from accounts.models import User
from notifications.models import Notification
from notifications.services import create_notification, notify_group_invitation

from .models import (
    CourseTA,
    Enrollment,
    GroupInvitation,
    GroupMembership,
    GroupMessage,
    GroupMessageAttachment,
    GroupWork,
    StudentGroup,
)
from .permissions import course_access
from .validators import validate_upload_size
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


def _invalid_payload_response(request, error, course, work=None, group=None):
    message = error.messages[0]
    if _is_api(request):
        return JsonResponse({"error": message}, status=400)
    messages.error(request, message)
    if group is not None:
        return redirect("course:group_chat", pk=course.pk, group_id=group.pk)
    if work is not None:
        return redirect(_work_url(course.pk, work.pk))
    return redirect("course:group_work_index", pk=course.pk)


@course_access()
@require_http_methods(["GET", "POST"])
def group_work_index(request, pk):
    course = request.course
    can_manage = _can_manage_group_works(request)

    if request.method == "POST":
        if not can_manage:
            return _forbidden(request)
        try:
            data = payload(request)
        except ValidationError as error:
            return _invalid_payload_response(request, error, course)
        title = data.get("title", "").strip()
        try:
            max_members = int(data.get("max_members", ""))
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
    pending_invites = GroupInvitation.objects.filter(
        group_work=work,
        invitee=request.user,
        status=GroupInvitation.Status.PENDING,
    ).select_related("group", "group__head")
    has_pending_invites = pending_invites.exists()

    groups = work.groups.select_related("head").prefetch_related(
        "memberships__student", "invitations"
    )
    if not can_manage and not request.is_dept_override:
        groups = groups.filter(pk=current_membership.group_id) if current_membership else groups.none()
    groups = list(groups)
    for group in groups:
        group.member_count = len(group.memberships.all())
        group.pending_invite_count = sum(
            invitation.status == GroupInvitation.Status.PENDING
            for invitation in group.invitations.all()
        )
        group.remaining_slots = max(
            0, work.max_members - group.member_count - group.pending_invite_count
        )

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
                    "head": {
                        "id": group.head_id,
                        "name": group.head.get_full_name(),
                        "student_id": group.head.nisit_id,
                    },
                    "members": [
                        {
                            "id": member.student_id,
                            "name": member.student.get_full_name(),
                            "student_id": member.student.nisit_id,
                            "is_head": member.student_id == group.head_id,
                        }
                        for member in group.memberships.all()
                    ],
                    "member_count": group.member_count,
                    "pending_invite_count": group.pending_invite_count,
                    "remaining_slots": group.remaining_slots,
                }
                for group in groups
            ],
        })

    return render(request, "course/group_work_detail.html", {
        "course": course,
        "work": work,
        "groups": groups,
        "current_group": current_membership.group if current_membership else None,
        "current_membership": current_membership,
        "pending_invites": pending_invites,
        "can_manage": can_manage,
        "can_join": (
            _student_can_join(request)
            and current_membership is None
            and not has_pending_invites
        ),
        "is_department": request.is_dept_override,
        "can_chat": _is_student(request) and current_membership is not None,
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
            if GroupInvitation.objects.filter(
                group_work=work,
                invitee=request.user,
                status=GroupInvitation.Status.PENDING,
            ).exists():
                if _is_api(request):
                    return JsonResponse({"error": "Accept or decline your pending invitation first."}, status=409)
                messages.error(request, "Accept or decline your pending invitation before creating a group.")
                return redirect(_work_url(course.pk, work.pk))
            group = StudentGroup.objects.create(
                group_work=work, name=name, head=request.user
            )
            GroupMembership.objects.create(
                group_work=work, group=group, student=request.user
            )
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
            "member_count": 1,
            "max_members": work.max_members,
        }, status=201)
    messages.success(request, "Your group has been created.")
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
                group_work=work, invitee=invitee, status=GroupInvitation.Status.PENDING
            ).exists():
                if _is_api(request):
                    return JsonResponse({"error": "That student already has a pending invitation."}, status=409)
                messages.error(request, "That student already has a pending invitation.")
                return redirect(_work_url(course.pk, work.pk))
            pending_count = GroupInvitation.objects.filter(
                group=group, status=GroupInvitation.Status.PENDING
            ).count()
            if group.memberships.count() + pending_count >= work.max_members:
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
            create_notification(
                recipient=invitation.group.head,
                kind="group_invitation_response",
                event_key=f"group-invitation-response:{invitation.pk}:{invitation.status}",
                title="Group invitation response",
                message=f"{request.user.get_full_name()} {invitation.status} your invitation.",
                url=_work_url(course.pk, work.pk),
            )
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
            membership.delete()
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


@course_access()
@require_http_methods(["GET", "POST"])
def group_chat(request, pk, group_id):
    course = request.course
    group = get_object_or_404(
        StudentGroup.objects.select_related("group_work"),
        pk=group_id,
        group_work__course=course,
    )
    is_member = GroupMembership.objects.filter(
        group=group, student=request.user
    ).exists()
    if not _is_student(request) or not is_member:
        return _forbidden(request)

    if request.method == "POST":
        try:
            data = payload(request)
        except ValidationError as error:
            return _invalid_payload_response(
                request, error, course, group.group_work, group
            )
        body = data.get("body", "").strip()
        uploads = request.FILES.getlist("files")
        if len(body) > 5000:
            if _is_api(request):
                return JsonResponse({"error": "Messages must be 5,000 characters or fewer."}, status=400)
            messages.error(request, "Messages must be 5,000 characters or fewer.")
            return redirect("course:group_chat", pk=course.pk, group_id=group.pk)
        if not body and not uploads:
            if _is_api(request):
                return JsonResponse({"error": "Write a message or attach at least one file."}, status=400)
            messages.error(request, "Write a message or attach at least one file.")
            return redirect("course:group_chat", pk=course.pk, group_id=group.pk)
        try:
            for upload in uploads:
                validate_upload_size(upload)
        except ValidationError as error:
            if _is_api(request):
                return JsonResponse({"error": error.messages[0]}, status=400)
            messages.error(request, error.messages[0])
            return redirect("course:group_chat", pk=course.pk, group_id=group.pk)

        with transaction.atomic():
            message = GroupMessage.objects.create(
                group=group, sender=request.user, body=body
            )
            for upload in uploads:
                GroupMessageAttachment.objects.create(message=message, file=upload)
        if _is_api(request):
            return JsonResponse({
                "id": message.pk,
                "body": message.body,
                "sender_name": request.user.get_full_name(),
                "created_at": message.created_at.isoformat(),
            }, status=201)
        return redirect("course:group_chat", pk=course.pk, group_id=group.pk)

    chat_messages = group.messages.select_related("sender").prefetch_related("attachments")
    if _is_api(request):
        return JsonResponse({
            "group_id": group.pk,
            "messages": [
                {
                    "id": message.pk,
                    "body": message.body,
                    "sender_name": message.sender.get_full_name() if message.sender else "Former member",
                    "created_at": message.created_at.isoformat(),
                    "attachments": [
                        {
                            "id": attachment.pk,
                            "name": attachment.original_name,
                            "size": attachment.size,
                            "is_image": attachment.is_inline_image,
                            "url": reverse(
                                "course:group_attachment",
                                args=[course.pk, attachment.pk],
                            ),
                        }
                        for attachment in message.attachments.all()
                    ],
                }
                for message in chat_messages
            ],
        })
    return render(request, "course/group_chat.html", {
        "course": course,
        "group": group,
        "chat_messages": chat_messages,
    })


@course_access()
@require_http_methods(["GET"])
def group_attachment(request, pk, attachment_id):
    attachment = get_object_or_404(
        GroupMessageAttachment.objects.select_related(
            "message__group__group_work"
        ),
        pk=attachment_id,
        message__group__group_work__course=request.course,
    )
    if not _is_student(request) or not GroupMembership.objects.filter(
        group=attachment.message.group, student=request.user
    ).exists():
        return _forbidden(request)
    try:
        response = FileResponse(
            attachment.file.open("rb"),
            as_attachment=not attachment.is_inline_image,
            filename=attachment.original_name,
            content_type=(
                attachment.mime_type
                if attachment.is_inline_image
                else "application/octet-stream"
            ),
        )
    except OSError as error:
        raise Http404("The attachment is unavailable.") from error
    return response
