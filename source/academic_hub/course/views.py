import json
from datetime import timedelta


from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST
from django.utils import timezone

from .forms import (CourseForm, CoursePostForm, CsvRosterForm, JoinForm,
                    MaterialEditForm, MaterialUploadForm, RosterForm)
from .models import Course, CoursePost, CourseTA, Enrollment, Material, PostAttachment
from .permissions import can_teach, course_access, visible_courses
from .services import (add_materials, add_students, assign_ta, create_course,
                       get_or_create_topic, remove_ta, update_course)


def api_request(request):
    return request.path.startswith("/course/api/")


def payload(request):
    if request.content_type == "application/json":
        try:
            data = json.loads(request.body)
        except (ValueError, UnicodeDecodeError):
            raise ValidationError("Invalid JSON body.")
        if not isinstance(data, dict) or any(not isinstance(v, str) for v in data.values()):
            raise ValidationError("Provide an object with string values.")
        return data
    return request.POST


def bind_form(request, form_class):
    try:
        return form_class(payload(request))
    except ValidationError as error:
        form = form_class({})
        form.is_valid()
        form.add_error(None, error)
        return form


def course_data(course, user):
    data = {"id": course.pk, "name": course.name, "section": course.section, "owner_id": course.owner_id}
    if course.owner_id == user.pk and can_teach(user):
        data["class_code"] = course.class_code
    return data


def form_error(request, form, title, *, course=None, template="course/form.html"):
    if api_request(request):
        return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
    return render(request, template, {"form": form, "title": title, "course": course}, status=400)


@course_access()
@require_http_methods(["GET"])
def dashboard(request):
    courses = visible_courses(request.user).select_related("owner")

    ta_course_ids = set(
        CourseTA.objects.filter(user=request.user).values_list("course_id", flat=True)
    )

    # Fetch deadlines within the next 7 days across all classes
    now = timezone.now()
    upcoming_deadlines = (
        CoursePost.objects.filter(
            course__in=courses,
            deadline__isnull=False,
            deadline__gte=now,
            deadline__lte=now + timedelta(days=7)
        )
        .select_related("course")
        .order_by("deadline")
    )

    if api_request(request):
        return JsonResponse({"courses": [course_data(c, request.user) for c in courses]})

    return render(
        request,
        "course/dashboard.html",
        {
            "courses": courses,
            "can_create": can_teach(request.user),
            "can_join": not request.user.is_staff and not request.user.is_superuser,
            "ta_course_ids": ta_course_ids,
            "upcoming_deadlines": upcoming_deadlines,
            "create_form": CourseForm(),
            "join_form": JoinForm(),
        },
    )


@course_access(lecturer=True)
@require_http_methods(["GET", "POST"])
def create(request):
    form = bind_form(request, CourseForm) if request.method == "POST" else CourseForm()
    if request.method == "POST":
        if form.is_valid():
            try:
                course = create_course(owner=request.user, name=form.cleaned_data["name"],
                                       section=form.cleaned_data["section"])
            except ValidationError as error:
                form.add_error(None, error)
            else:
                if api_request(request):
                    return JsonResponse(course_data(course, request.user), status=201)
                messages.success(request, "Class created. Share the code with your students.")
                return redirect("course:detail", pk=course.pk)
        return form_error(request, form, "Create class")
    return render(request, "course/form.html", {"form": form, "title": "Create class"})


@course_access(student=True)
@require_http_methods(["GET", "POST"])
def join(request):
    form = bind_form(request, JoinForm) if request.method == "POST" else JoinForm()
    if request.method == "POST":
        if form.is_valid():
            course = Course.objects.filter(class_code=form.cleaned_data["code"]).first()
            if course is None:
                form.add_error("code", "Class not found. Check the code with your lecturer.")
            else:
                _, created = Enrollment.objects.get_or_create(course=course, student=request.user)
                if api_request(request):
                    return JsonResponse({"course": course_data(course, request.user), "joined": created}, status=201 if created else 200)
                messages.success(request, "You joined the class." if created else "You are already a member of this class.")
                return redirect("course:detail", pk=course.pk)
        return form_error(request, form, "Join class")
    return render(request, "course/form.html", {"form": form, "title": "Join class"})


def topic_groups(course):
    """Materials grouped by topic, newest topic first. Files with no topic last."""
    by_topic = {}
    for material in course.materials.order_by("-created_at", "-pk"):
        by_topic.setdefault(material.topic_id, []).append(material)
    groups = [{"title": t.title, "materials": by_topic[t.pk]}
              for t in course.topics.order_by("-pk") if t.pk in by_topic]
    if None in by_topic:
        groups.append({"title": "No topic", "materials": by_topic[None]})
    return groups



def detail_context(request, post_form=None, material_form=None):
    course = request.course
    manage = request.can_manage_content
    if manage and post_form is None:
        post_form = CoursePostForm(course=course)
    if manage and material_form is None:
        material_form = MaterialUploadForm(course=course, auto_id="material_%s")
    groups = topic_groups(course)
    
    # Query deadlines within the next 7 days
    now = timezone.now()
    upcoming_deadlines = course.posts.filter(
        deadline__isnull=False, 
        deadline__gte=now,
        deadline__lte=now + timedelta(days=7)
    ).order_by("deadline")

    return {
        "course": course,
        "posts": course.posts.select_related("author").prefetch_related("attachments"),
        "upcoming_deadlines": upcoming_deadlines,
        "post_form": post_form,
        "material_form": material_form,
        "topic_groups": groups,
        "material_count": sum(len(g["materials"]) for g in groups),
        "topics": list(course.topics.order_by("-pk")) if manage else [],
        "can_manage": request.can_manage_roster,
        "can_manage_content": manage,
        "can_delete_any": request.is_owner or request.is_dept_override,

        "enrollments": course.enrollments.select_related("student") if request.can_manage_roster else [],
        "ta_ids": set(course.ta_links.values_list("user_id", flat=True)),
    }


@course_access()
@require_http_methods(["GET"])
def detail(request, pk):
    if api_request(request):
        data = course_data(request.course, request.user)
        data["can_manage_content"] = request.can_manage_content
        data["can_manage_roster"] = request.can_manage_roster
        return JsonResponse(data)
    return render(request, "course/detail.html", detail_context(request))


def render_members(request, *, form=None, csv_form=None, import_report=None, status=200):
    course = request.course
    return render(request, "course/members.html", {
        "course": course,
        "members": course.enrollments.select_related("student"),
        "ta_ids": set(course.ta_links.values_list("user_id", flat=True)),
        "can_manage": request.can_manage_roster,
        "roster_form": form if form is not None else RosterForm(),
        "csv_form": csv_form if csv_form is not None else CsvRosterForm(),
        "import_report": import_report,
    }, status=status)


@course_access()
@require_http_methods(["GET"])
def members_page(request, pk):
    return render_members(request)


@course_access(owner=True)
@require_http_methods(["GET", "POST"])
def edit(request, pk):
    course = request.course
    form = bind_form(request, CourseForm) if request.method == "POST" else CourseForm(initial={"name": course.name, "section": course.section})
    if request.method == "POST":
        if form.is_valid():
            try:
                update_course(course, name=form.cleaned_data["name"], section=form.cleaned_data["section"])
            except ValidationError as error:
                form.add_error(None, error)
            else:
                if api_request(request):
                    return JsonResponse(course_data(course, request.user))
                messages.success(request, "Class updated.")
                return redirect("course:detail", pk=pk)
        return form_error(request, form, "Edit class", course=course, template="course/edit.html")
    return render(request, "course/edit.html", {"form": form, "title": "Edit class", "course": course})


@course_access(owner=True)
@require_POST
def delete(request, pk):
    request.course.delete()
    if api_request(request):
        return JsonResponse({"deleted": True})
    messages.success(request, "Class deleted.")
    return redirect("course:dashboard")


@course_access()
@require_http_methods(["GET"])
def members(request, pk):
    roster = request.course.enrollments.select_related("student")
    tas = set(request.course.ta_links.values_list("user_id", flat=True))
    return JsonResponse({"lecturer": {"id": request.course.owner_id, "name": request.course.owner.get_full_name()},
                         "students": [{"id": e.student_id, "name": e.student.get_full_name(),
                                       "is_ta": e.student_id in tas} for e in roster]})


@course_access(owner=True)
@require_POST
def import_members(request, pk):
    form = bind_form(request, RosterForm)
    if not form.is_valid():
        if not api_request(request):
            return render_members(request, form=form, status=400)
        return form_error(request, form, "Add students", course=request.course)
    return finish_import(request, form, csv=False)


@course_access(owner=True)
@require_POST
def remove_member(request, pk, student_id):
    enrollment = get_object_or_404(Enrollment, course=request.course, student_id=student_id)
    enrollment.delete()
    if api_request(request):
        return JsonResponse({"removed": True})
    messages.success(request, "Student removed from the class.")
    return redirect("course:members_page", pk=pk)


@course_access(owner=True)
@require_POST
def import_csv(request, pk):
    form = CsvRosterForm(request.POST, request.FILES)
    if not form.is_valid():
        if api_request(request):
            return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
        return render_members(request, csv_form=form, status=400)
    return finish_import(request, form, csv=True)


def finish_import(request, form, *, csv):
    added = add_students(request.course, form.students)
    result = {"added": added, "already_enrolled": len(form.students) - added,
              "duplicates_skipped": form.duplicate_count, "rejected": form.rejected}
    status = 200 if form.students else 400
    if api_request(request):
        return JsonResponse(result, status=status)
    if form.rejected:
        return render_members(request, import_report=result, status=status,
                              **({"csv_form": form} if csv else {"form": form}))
    messages.success(request, f"Added {added} student(s); {result['already_enrolled']} already enrolled; "
                     f"{result['duplicates_skipped']} duplicate row(s) skipped.")
    return redirect("course:members_page", pk=request.course.pk)


# --- Task 4.3: announcements and attachments ---------------------------------

@course_access(manage_content=True)
@require_POST
def create_post(request, pk):
    form = CoursePostForm(request.POST, request.FILES, course=request.course)
    if not form.is_valid():
        if api_request(request):
            return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
        return render(request, "course/detail.html", detail_context(request, form), status=400)

    topic = None
    if form.cleaned_data.get("attachment") and form.cleaned_data.get("add_to_materials"):
        choice = form.cleaned_data["topic"]
        if choice == MaterialUploadForm.NEW_TOPIC:
            topic = get_or_create_topic(request.course, form.cleaned_data["new_topic"])
        else:
            topic = get_object_or_404(request.course.topics, pk=choice)

            
    # Topic check outside atomic block to prevent orphan files on 404
    existing_topic = None
    if form.cleaned_data.get("attachment") and form.cleaned_data.get("add_to_materials"):
        choice = form.cleaned_data["topic"]
        if choice != MaterialUploadForm.NEW_TOPIC:
            existing_topic = get_object_or_404(request.course.topics, pk=choice)

    with transaction.atomic():
        post = form.save(commit=False)
        post.course = request.course
        post.author = request.user
        post.save()

        upload = form.cleaned_data.get("attachment")
        if upload:
            attachment = PostAttachment.objects.create(post=post, file=upload)
            if form.cleaned_data.get("add_to_materials"):
                topic = existing_topic or get_or_create_topic(request.course, form.cleaned_data["new_topic"])
                Material.objects.create(
                    course=request.course, topic=topic, created_by=request.user,
                    title=attachment.original_name[:150], file=attachment.file.name,
                    original_name=attachment.original_name, size=attachment.size
                )

    if api_request(request):
        return JsonResponse({"id": post.pk, "title": post.title}, status=201)
    messages.success(request, "Announcement posted.")
    return redirect("course:detail", pk=pk)

@course_access(manage_content=True)
@require_POST
def delete_post(request, pk, post_id):
    post = get_object_or_404(CoursePost, pk=post_id, course=request.course)
    if not (request.is_owner or request.is_dept_override) and post.author_id != request.user.pk:
        if api_request(request):
            return JsonResponse({"error": "You can only delete your own announcements."}, status=403)
        messages.error(request, "You can only delete your own announcements.")
        return redirect("course:detail", pk=pk)
    post.delete()
    if api_request(request):
        return JsonResponse({"deleted": True})
    messages.success(request, "Announcement deleted.")
    return redirect("course:detail", pk=pk)


def file_download(attachment):
    """
    Helper function to safely stream an attachment file.
    """
    try:
        # Obtain original filename safely
        filename = getattr(attachment, 'original_name', None) or getattr(attachment, 'name', None) or 'attachment'
        
        return FileResponse(
            attachment.file.open('rb'),
            as_attachment=True,
            filename=filename
        )
    except (FileNotFoundError, ValueError, AttributeError):
        raise Http404("Attachment file unavailable")



@course_access()
@require_http_methods(["GET"])
def download_attachment(request, pk, attachment_id):
    attachment = get_object_or_404(PostAttachment, pk=attachment_id, post__course=request.course)
    return file_download(attachment)


# --- Task 4.4: materials ------------------------------------------------------

@course_access(manage_content=True)
@require_POST
def upload_materials(request, pk):
    course = request.course
    form = MaterialUploadForm(request.POST, request.FILES, course=course, auto_id="material_%s")
    if not form.is_valid():
        if api_request(request):
            return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
        return render(request, "course/detail.html",
                      detail_context(request, material_form=form), status=400)

    choice = form.cleaned_data["topic"]
    if choice == MaterialUploadForm.NEW_TOPIC:
        topic = get_or_create_topic(course, form.cleaned_data["new_topic"])
    else:
        topic = get_object_or_404(course.topics, pk=choice)
    created = add_materials(course, topic, form.cleaned_data["files"], request.user)

    if api_request(request):
        return JsonResponse({"added": len(created), "topic": topic.title}, status=201)
    messages.success(request, f"{len(created)} file(s) added to {topic.title}.")
    return redirect("course:detail", pk=pk)


@course_access(manage_content=True)
@require_POST
def edit_material(request, pk, material_id):
    material = get_object_or_404(Material, pk=material_id, course=request.course)
    form = MaterialEditForm(request.POST, course=request.course)
    if not form.is_valid():
        if api_request(request):
            return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
        messages.error(request, "Could not update the material. Check the name and topic.")
        return redirect("course:detail", pk=pk)
    topic_id = form.cleaned_data["topic"]
    material.title = form.cleaned_data["title"].strip()
    material.topic = get_object_or_404(request.course.topics, pk=topic_id) if topic_id else None
    material.save(update_fields=["title", "topic", "updated_at"])
    if api_request(request):
        return JsonResponse({"id": material.pk, "title": material.title})
    messages.success(request, "Material updated.")
    return redirect("course:detail", pk=pk)


@course_access(manage_content=True)
@require_POST
def delete_material(request, pk, material_id):
    material = get_object_or_404(Material, pk=material_id, course=request.course)
    material.delete()  # signals.py removes the file after commit
    if api_request(request):
        return JsonResponse({"deleted": True})
    messages.success(request, "Material deleted.")
    return redirect("course:detail", pk=pk)


@course_access()
@require_http_methods(["GET"])
def download_material(request, pk, material_id):
    material = get_object_or_404(Material, pk=material_id, course=request.course)
    return file_download(material)




@course_access(manage_content=True)
@require_POST
def edit_post(request, pk, post_id):
    post = get_object_or_404(CoursePost, pk=post_id, course=request.course)
    
    # Enforce same author check as delete_post
    if not (request.is_owner or request.is_dept_override) and post.author_id != request.user.pk:
        if api_request(request):
            return JsonResponse({"error": "You can only edit your own announcements."}, status=403)
        messages.error(request, "You can only edit your own announcements.")
        return redirect("course:detail", pk=pk)
        
    form = CoursePostForm(request.POST, instance=post, course=request.course)
    if form.is_valid():
        form.save()
        if api_request(request):
            return JsonResponse({"id": post.pk, "title": post.title})
        messages.success(request, "Announcement updated.")
    else:
        if api_request(request):
            return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
        messages.error(request, "Failed to update announcement. Check form fields.")
        
    return redirect("course:detail", pk=pk)


# --- Task 4.5: teaching assistants (owner only) -------------------------------

@course_access(owner=True)
@require_POST
def ta_assign(request, pk, student_id):
    try:
        created = assign_ta(request.course, student_id)
    except ValidationError as error:
        if api_request(request):
            return JsonResponse({"error": error.messages[0]}, status=404)
        messages.error(request, error.messages[0])
        return redirect("course:detail", pk=pk)
    if api_request(request):
        return JsonResponse({"assigned": True, "created": created}, status=201 if created else 200)
    messages.success(request, "Student is now a TA." if created else "Student is already a TA.")
    return redirect("course:detail", pk=pk)


@course_access(owner=True)
@require_POST
def ta_remove(request, pk, student_id):
    removed = remove_ta(request.course, student_id)
    if api_request(request):
        return JsonResponse({"removed": removed})
    messages.success(request, "TA role removed." if removed else "That student was not a TA.")
    return redirect("course:detail", pk=pk)