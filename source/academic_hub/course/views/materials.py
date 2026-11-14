"""Course materials: upload, edit, archive, restore, delete and download."""
from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from ..forms import MaterialEditForm, MaterialUploadForm
from ..models import Material
from ..permissions import course_access
from ..responses import detail_url, file_download, form_errors_json, is_api, respond
from ..services import add_materials, archive_material, resolve_topic, restore_material
from .courses import detail_context


def course_material(request, material_id):
    return get_object_or_404(Material, pk=material_id, course=request.course)


@course_access(manage_content=True)
@require_POST
def upload_materials(request, pk):
    course = request.course
    form = MaterialUploadForm(request.POST, request.FILES, course=course, auto_id="material_%s")
    if not form.is_valid():
        if is_api(request):
            return form_errors_json(form)
        return render(request, "course/detail.html", detail_context(request, material_form=form), status=400)

    topic = resolve_topic(course, form.cleaned_data["topic"], form.cleaned_data["new_topic"])
    created = add_materials(course, topic, form.cleaned_data["files"], request.user)
    return respond(request, {"added": len(created), "topic": topic.title},
                   f"{len(created)} file(s) added to {topic.title}.", detail_url(pk), status=201)


@course_access(manage_content=True)
@require_POST
def edit_material(request, pk, material_id):
    material = course_material(request, material_id)
    form = MaterialEditForm(request.POST, course=request.course)
    if not form.is_valid():
        if is_api(request):
            return form_errors_json(form)
        messages.error(request, "Could not update the material. Check the name and topic.")
        return redirect(detail_url(pk))
    topic_id = form.cleaned_data["topic"]
    material.title = form.cleaned_data["title"].strip()
    material.topic = get_object_or_404(request.course.topics, pk=topic_id) if topic_id else None
    material.save(update_fields=["title", "topic", "updated_at"])
    return respond(request, {"id": material.pk, "title": material.title}, "Material updated.", detail_url(pk))


@course_access(manage_content=True)
@require_POST
def archive_material_view(request, pk, material_id):
    archive_material(course_material(request, material_id), request.user)
    return respond(request, {"archived": True}, "Material archived.", detail_url(pk))


@course_access(manage_content=True)
@require_POST
def restore_material_view(request, pk, material_id):
    restore_material(course_material(request, material_id))
    return respond(request, {"restored": True}, "Material restored.", detail_url(pk))


@course_access(manage_content=True)
@require_POST
def delete_material(request, pk, material_id):
    course_material(request, material_id).delete()  # signals.py removes the file after commit
    return respond(request, {"deleted": True}, "Material deleted.", detail_url(pk))


@course_access()
@require_http_methods(["GET"])
def download_material(request, pk, material_id):
    material = course_material(request, material_id)
    # Archived materials stay downloadable for course staff only.
    if material.archived_at is not None and not request.can_manage_content:
        raise Http404("Material is no longer available.")
    return file_download(material)
