"""Announcements: create, edit, archive, restore, delete and attachment downloads."""
from django.contrib import messages
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods, require_POST

from notifications.services import notify_course_material, notify_course_post, notify_deadline

from ..forms import CoursePostForm
from ..models import CoursePost, PostAttachment
from ..permissions import can_modify_post, course_access
from ..responses import detail_url, fail, file_download, form_errors_json, is_api, respond
from ..services import archive_post, attach_uploads, resolve_topic, restore_post
from .courses import detail_context


def own_post_or_none(request, pk, post_id, verb):
    """Return the post, or a 403 response when the user may not ``verb`` it."""
    post = get_object_or_404(CoursePost, pk=post_id, course=request.course)
    if can_modify_post(request, post):
        return post, None
    return None, fail(request, f"You can only {verb} your own announcements.", detail_url(pk), status=403)


def material_topic(request, form):
    """The topic chosen for "add to Materials", or None when not requested."""
    data = form.cleaned_data
    if not data.get("add_to_materials"):
        return None
    return resolve_topic(request.course, data.get("topic"), data.get("new_topic"))


def invalid_post_form(request, form):
    if is_api(request):
        return form_errors_json(form)
    return render(request, "course/detail.html", detail_context(request, post_form=form), status=400)


def invalid_edit_form(request, form):
    """Report a rejected edit with its reasons, without filling the New announcement form."""
    if is_api(request):
        return form_errors_json(form)
    reasons = " ".join(error for errors in form.errors.values() for error in errors)
    messages.error(request, f"Failed to update announcement. {reasons}")
    return render(request, "course/detail.html", detail_context(request), status=400)


@course_access(manage_content=True)
@require_POST
def create_post(request, pk):
    form = CoursePostForm(request.POST, request.FILES, course=request.course)
    if not form.is_valid():
        return invalid_post_form(request, form)

    uploads = form.cleaned_data.get("attachment") or []
    with transaction.atomic():
        post = form.save(commit=False)
        post.course = request.course
        post.author = request.user
        post.save()
        topic = material_topic(request, form) if uploads else None
        materials = attach_uploads(post, uploads, topic, request.user)

    notify_course_post(post)
    if post.deadline:
        notify_deadline(post)
    for material in materials:
        notify_course_material(material)
    return respond(request, {"id": post.pk, "title": post.title}, "Announcement posted.",
                   detail_url(pk), status=201)


def save_post_edit(request, form, post):
    """Save an edited post: drop the files marked for removal, add the new uploads.

    Files the user did not remove are kept. Returns the Materials created.
    """
    with transaction.atomic():
        post = form.save()
        for attachment in form.removed_attachments():
            attachment.delete()  # signals.py removes the stored file after commit
        uploads = form.cleaned_data.get("attachment") or []
        topic = material_topic(request, form) if uploads else None
        return post, attach_uploads(post, uploads, topic, request.user)


def render_post_editor(request, post):
    if is_api(request):
        return JsonResponse({"id": post.pk, "title": post.title, "body": getattr(post, "body", "")})
    return render(request, "course/form.html", {
        "form": CoursePostForm(instance=post, course=request.course),
        "title": "Edit Announcement",
        "course": request.course,
        "post": post,
    })


@course_access(manage_content=True)
@require_http_methods(["GET", "POST"])
def edit_post(request, pk, post_id):
    post, denied = own_post_or_none(request, pk, post_id, "edit")
    if denied:
        return denied
    if request.method == "GET":
        return render_post_editor(request, post)

    # Read before validation: is_valid() copies the submitted deadline onto the post.
    previous_deadline = post.deadline
    form = CoursePostForm(request.POST, request.FILES, instance=post, course=request.course)
    if not form.is_valid():
        return invalid_edit_form(request, form)

    post, materials = save_post_edit(request, form, post)
    if post.deadline and post.deadline != previous_deadline:
        notify_deadline(post)
    for material in materials:
        notify_course_material(material)
    return respond(request, {"id": post.pk, "title": post.title},
                   "Announcement updated successfully.", detail_url(pk))


@course_access(manage_content=True)
@require_POST
def archive_post_view(request, pk, post_id):
    post, denied = own_post_or_none(request, pk, post_id, "archive")
    if denied:
        return denied
    archive_post(post, request.user)
    return respond(request, {"archived": True}, "Announcement archived.", detail_url(pk))


@course_access(manage_content=True)
@require_POST
def restore_post_view(request, pk, post_id):
    post, denied = own_post_or_none(request, pk, post_id, "restore")
    if denied:
        return denied
    restore_post(post)
    return respond(request, {"restored": True}, "Announcement restored.", detail_url(pk))


@course_access(manage_content=True)
@require_POST
def delete_post(request, pk, post_id):
    post, denied = own_post_or_none(request, pk, post_id, "delete")
    if denied:
        return denied
    post.delete()
    return respond(request, {"deleted": True}, "Announcement deleted.", detail_url(pk))


@course_access()
@require_http_methods(["GET"])
def download_attachment(request, pk, attachment_id):
    attachment = get_object_or_404(PostAttachment, pk=attachment_id, post__course=request.course)
    # Archived attachments stay downloadable for course staff only.
    if attachment.post.archived_at is not None and not request.can_manage_content:
        raise Http404("Attachment is no longer available.")
    return file_download(attachment)
