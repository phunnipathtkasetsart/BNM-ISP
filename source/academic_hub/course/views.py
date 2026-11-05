import os
import json
from datetime import timedelta
from functools import wraps

from django.db.models import Count, Exists, OuterRef, Q
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse, request
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST
from django.utils import timezone

from django.contrib.auth import get_user_model


from accounts.models import AdvisorAssignment


from .forms import (CourseForm, CoursePostForm, CsvRosterForm, JoinForm,
                    MaterialEditForm, MaterialUploadForm, RosterForm)
from .models import (
    Course,
    CourseAnswer,
    CoursePost,
    CourseQuestion,
    CourseTA,
    Enrollment,
    Material,
    PostAttachment,
)
from .permissions import can_teach, course_access, visible_courses
from .services import (
    add_materials,
    add_students,
    assign_ta,
    create_course,
    create_course_answer,
    create_course_question,
    get_or_create_topic,
    remove_ta,
    update_course,
    archive_post,
    restore_post,
    archive_material,
    restore_material,
    archive_course,
    restore_course,
)
from notifications.services import notify_course_material, notify_course_post, notify_deadline

# Selects the response format; authorization is handled by course_access.
def api_request(request):
    return request.path.startswith("/course/api/")


def payload(request):
    if request.content_type == "application/json":
        try:
            data = json.loads(request.body)
        # Convert malformed JSON into a form validation error.
        except (ValueError, UnicodeDecodeError):
            raise ValidationError("Invalid JSON body.")
        # Require string values to preserve identifiers such as section "001".
        if not isinstance(data, dict) or any(not isinstance(v, str) for v in data.values()):
            raise ValidationError("Provide an object with string values.")
        # Uploaded files are supplied separately in request.FILES.
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
    # Expose class codes only to owners who are allowed to teach.
    if course.owner_id == user.pk and can_teach(user):
        data["class_code"] = course.class_code
    return data


def form_error(request, form, title, *, course=None, template="course/form.html"):
    if api_request(request):
        return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
    return render(request, template, {"form": form, "title": title, "course": course}, status=400)


# Iteration 5 , adding archive feature to dashboard view, filtering courses based on archived status and user permissions.
@course_access()
@require_http_methods(["GET"])
def dashboard(request):
    query = request.GET.get("q", "").strip()
    all_courses = visible_courses(request.user).select_related("owner")

    if request.user.is_superuser and query:
        all_courses = all_courses.filter(
            Q(name__icontains=query) |
            Q(section__icontains=query) |
            Q(owner__first_name__icontains=query) |
            Q(owner__last_name__icontains=query)
        ).distinct()

    # Separate active vs. archived courses
    courses = all_courses.filter(archived_at__isnull=True)
    archived_courses = all_courses.filter(archived_at__isnull=False) if can_teach(request.user) else []

    ta_course_ids = set(
        CourseTA.objects.filter(user=request.user).values_list("course_id", flat=True)
    )

    now = timezone.now()
    upcoming_deadlines = (
        CoursePost.objects.filter(
            course__in=courses,
            archived_at__isnull=True,
            deadline__isnull=False,
            deadline__gte=now,
            deadline__lte=now + timedelta(days=7)
        )
        .select_related("course")
        .order_by("deadline")
    )

    if api_request(request):
        return JsonResponse({
            "courses": [course_data(c, request.user) for c in courses],
            "archived_courses": [course_data(c, request.user) for c in archived_courses],
        })

    is_student = not request.user.is_staff and not request.user.is_superuser
    advisor_assignment = None
    if is_student:
        advisor_assignment = (
            AdvisorAssignment.objects.filter(
                student=request.user,
                advisor__is_staff=True,
                advisor__is_superuser=False,
                advisor__is_active=True,
            )
            .select_related("advisor")
            .first()
        )

    return render(
        request,
        "course/dashboard.html",
        {
            "courses": courses,
            "archived_courses": archived_courses,
            "query": query,
            "can_create": can_teach(request.user),
            "can_join": not request.user.is_staff and not request.user.is_superuser,
            "ta_course_ids": ta_course_ids,
            "upcoming_deadlines": upcoming_deadlines,
            "create_form": CourseForm(),
            "join_form": JoinForm(),
            "is_student": is_student,
            "advisor_assignment": advisor_assignment,
        },
    )

# --- Course Archive & Restore Views ---

@course_access(owner=True)
@require_POST
def archive_course_view(request, pk):
    archive_course(request.course, request.user)
    if api_request(request):
        return JsonResponse({"archived": True})
    messages.success(request, "Class archived successfully.")
    return redirect("course:dashboard")

@course_access(owner=True)
@require_POST
def restore_course_view(request, pk):
    restore_course(request.course)
    if api_request(request):
        return JsonResponse({"restored": True})
    messages.success(request, "Class restored successfully.")
    return redirect("course:detail", pk=pk)

@course_access(lecturer=True)
@require_http_methods(["GET", "POST"])
def create(request):
    form = bind_form(request, CourseForm) if request.method == "POST" else CourseForm()
    if request.method == "POST":
        if form.is_valid():
            # Ownership always comes from the authenticated user, never the submitted data.
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
                # joined=False means the user was already enrolled; it does not mean they are not a member.
                if api_request(request):
                    return JsonResponse({"course": course_data(course, request.user), "joined": created}, status=201 if created else 200)
                messages.success(request, "You joined the class." if created else "You are already a member of this class.")
                return redirect("course:detail", pk=course.pk)
        return form_error(request, form, "Join class")
    return render(request, "course/form.html", {"form": form, "title": "Join class"})


def topic_groups(course):
    """Materials grouped by topic, newest topic first. Files with no topic last."""
    by_topic = {}
    for material in course.materials.filter(archived_at__isnull=True).order_by("-created_at", "-pk"):
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
        
    groups = topic_groups(course)  # Active materials only
    now = timezone.now()

    # Active posts for main board
    posts = course.posts.filter(archived_at__isnull=True).select_related("author").prefetch_related("attachments")

    # Manager view: Include archived items (Lecturer / TA / Admin only)
    archived_posts = course.posts.filter(archived_at__isnull=False).select_related("author").prefetch_related("attachments") if manage else []
    archived_materials = course.materials.filter(archived_at__isnull=False) if manage else []

    upcoming_deadlines = course.posts.filter(
        archived_at__isnull=True,
        deadline__isnull=False, 
        deadline__gte=now,
        deadline__lte=now + timedelta(days=7)
    ).order_by("deadline")

    # Fetch all active DB students not yet assigned as TA for this course
    User = get_user_model()
    ta_user_ids = set(course.ta_links.values_list("user_id", flat=True))
    available_students = User.objects.filter(
        is_active=True, is_staff=False, is_superuser=False
    ).exclude(pk__in=ta_user_ids)

    return {
        "course": course,
        "group_works": course.group_works.all(),
        "posts": posts,  # FIX: Use the filtered 'posts' variable instead of course.posts.all()
        "archived_posts": archived_posts,
        "archived_materials": archived_materials,
        "upcoming_deadlines": upcoming_deadlines,
        "post_form": post_form,
        "material_form": material_form,
        "topic_groups": groups,
        "material_count": sum(len(g["materials"]) for g in groups),
        "topics": list(course.topics.order_by("-pk")) if manage else [],
        "can_manage": request.can_manage_roster,
        "can_manage_content": manage,
        "can_delete_any": request.is_owner or request.is_dept_override,
        "can_filter_questions": request.can_manage_content,
        "can_ask_question": (
            not request.user.is_staff
            and not request.user.is_superuser
            and course.enrollments.filter(student=request.user).exists()
            and not course.ta_links.filter(user=request.user).exists()
        ),
        "is_department": request.is_dept_override,
        "enrollments": course.enrollments.select_related("student") if request.can_manage_roster else [],
        "ta_ids": ta_user_ids,
        "available_students": available_students,
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


# Members may view the roster; only the owner and Department may manage it.
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


# Deleting a course cascades to its records but keeps student accounts.
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


# CSV uploads are in request.FILES, separate from request.POST.
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
        return render(request, "course/detail.html", detail_context(request, post_form=form), status=400)

    uploads = form.cleaned_data.get("attachment") or []

    # Validate topic outside atomic transaction
    existing_topic = None
    if uploads and form.cleaned_data.get("add_to_materials"):
        choice = form.cleaned_data["topic"]
        if choice != MaterialUploadForm.NEW_TOPIC:
            existing_topic = get_object_or_404(request.course.topics, pk=choice)

    with transaction.atomic():
        post = form.save(commit=False)
        post.course = request.course
        post.author = request.user
        post.save()

        created_materials = []
        if uploads:
            topic = None
            if form.cleaned_data.get("add_to_materials"):
                topic = existing_topic or get_or_create_topic(request.course, form.cleaned_data["new_topic"])

            for upload in uploads:
                attachment = PostAttachment.objects.create(post=post, file=upload)
                if topic:
                    mat = Material.objects.create(
                        course=request.course,
                        topic=topic,
                        created_by=request.user,
                        title=attachment.original_name[:150],
                        file=attachment.file.name,
                        original_name=attachment.original_name,
                        size=attachment.size,
                    )
                    created_materials.append(mat)

    notify_course_post(post)
    if post.deadline:
        notify_deadline(post)
    for mat in created_materials:
        notify_course_material(mat)

    if api_request(request):
        return JsonResponse({"id": post.pk, "title": post.title}, status=201)
    messages.success(request, "Announcement posted.")
    return redirect("course:detail", pk=pk)

# --- Announcement Archive & Restore Views ---

@course_access(manage_content=True)
@require_POST
def archive_post_view(request, pk, post_id):
    post = get_object_or_404(CoursePost, pk=post_id, course=request.course)
    if not (request.is_owner or request.is_dept_override) and post.author_id != request.user.pk:
        if api_request(request):
            return JsonResponse({"error": "You can only archive your own announcements."}, status=403)
        messages.error(request, "You can only archive your own announcements.")
        return redirect("course:detail", pk=pk)
    archive_post(post, request.user)
    if api_request(request):
        return JsonResponse({"archived": True})
    messages.success(request, "Announcement archived.")
    return redirect("course:detail", pk=pk)

@course_access(manage_content=True)
@require_POST
def restore_post_view(request, pk, post_id):
    post = get_object_or_404(CoursePost, pk=post_id, course=request.course)
    if not (request.is_owner or request.is_dept_override) and post.author_id != request.user.pk:
        if api_request(request):
            return JsonResponse({"error": "You can only restore your own announcements."}, status=403)
        messages.error(request, "You can only restore your own announcements.")
        return redirect("course:detail", pk=pk)
    restore_post(post)
    if api_request(request):
        return JsonResponse({"restored": True})
    messages.success(request, "Announcement restored.")
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
    """Safely stream an attachment or material file with correct filename & extension."""
    try:
        filename = (
            getattr(attachment, 'display_name', '').strip()
            or getattr(attachment, 'original_name', '').strip()
            or getattr(attachment, 'title', '').strip()
            or (os.path.basename(attachment.file.name) if attachment.file and attachment.file.name else '')
            or 'attachment'
        )
        
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
    # Restrict archived attachment downloads to course managers only
    if attachment.post.archived_at is not None and not request.can_manage_content:
        raise Http404("Attachment is no longer available.")
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


# --- Material Features ---

@course_access(manage_content=True)
@require_POST
def archive_material_view(request, pk, material_id):
    material = get_object_or_404(Material, pk=material_id, course=request.course)
    archive_material(material, request.user)
    if api_request(request):
        return JsonResponse({"archived": True})
    messages.success(request, "Material archived.")
    return redirect("course:detail", pk=pk)

@course_access(manage_content=True)
@require_POST
def restore_material_view(request, pk, material_id):
    material = get_object_or_404(Material, pk=material_id, course=request.course)
    restore_material(material)
    if api_request(request):
        return JsonResponse({"restored": True})
    messages.success(request, "Material restored.")
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
    # Restrict archived material downloads to course managers only
    if material.archived_at is not None and not request.can_manage_content:
        raise Http404("Material is no longer available.")
    return file_download(material)



@course_access(manage_content=True)
@require_http_methods(["GET", "POST"])
def edit_post(request, pk, post_id):
    post = get_object_or_404(CoursePost, pk=post_id, course=request.course)

    if not (request.is_owner or request.is_dept_override) and post.author_id != request.user.pk:
        if api_request(request):
            return JsonResponse({"error": "You can only edit your own announcements."}, status=403)
        messages.error(request, "You can only edit your own announcements.")
        return redirect("course:detail", pk=pk)

    if request.method == "POST":
        form = CoursePostForm(request.POST, request.FILES, instance=post, course=request.course)
        if form.is_valid():
            previous_deadline = post.deadline
            created_material = None
            with transaction.atomic():
                post = form.save()

                remove_att = form.cleaned_data.get("remove_attachment") or request.POST.get("remove_attachment") == "true"
                new_uploads = form.cleaned_data.get("attachment") or []

                # If removing attachments or uploading replacements, delete existing attachment(s)
                if remove_att or new_uploads:
                    for att in post.attachments.all():
                        att.delete()

                # If new file(s) were uploaded, attach all of them
                if new_uploads:
                    choice = form.cleaned_data.get("topic")
                    topic = None
                    if form.cleaned_data.get("add_to_materials"):
                        if choice == MaterialUploadForm.NEW_TOPIC:
                            topic = get_or_create_topic(request.course, form.cleaned_data.get("new_topic"))
                        elif choice:
                            topic = get_object_or_404(request.course.topics, pk=choice)

                    for upload in new_uploads:
                        attachment = PostAttachment.objects.create(post=post, file=upload)
                        if topic:
                            created_material = Material.objects.create(
                                course=request.course, topic=topic, created_by=request.user,
                                title=attachment.original_name[:150], file=attachment.file.name,
                                original_name=attachment.original_name, size=attachment.size
                            )

            if post.deadline != previous_deadline and post.deadline:
                notify_deadline(post)
            if created_material:
                notify_course_material(created_material)

            if api_request(request):
                return JsonResponse({"id": post.pk, "title": post.title})
            messages.success(request, "Announcement updated successfully.")
            return redirect("course:detail", pk=pk)
        else:
            if api_request(request):
                return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
            messages.error(request, "Failed to update announcement. Please check your input.")
            return render(request, "course/detail.html", detail_context(request, post_form=form), status=400)

    # Fallback GET handler
    form = CoursePostForm(instance=post, course=request.course)
    if api_request(request):
        return JsonResponse({"id": post.pk, "title": post.title, "body": getattr(post, 'body', '')})

    return render(request, "course/form.html", {
        "form": form,
        "title": "Edit Announcement",
        "course": request.course,
        "post": post,
    })

# --- Task 4.5: teaching assistants (owner only) -------------------------------

@course_access(owner=True)
@require_POST
def ta_assign(request, pk, student_id=None):
    # Retrieve Nisit ID from input form POST body or URL parameter
    target_id = request.POST.get("student_id", "").strip() or student_id
    if not target_id:
        messages.error(request, "Please enter a Nisit ID.")
        return redirect("course:detail", pk=pk)

    try:
        created = assign_ta(request.course, target_id)
    except ValidationError as error:
        if api_request(request):
            return JsonResponse({"error": error.messages[0]}, status=400)
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









# --- Iteration 5.2: course Q&A JSON API --------------------------------------

def qa_error(message, *, status=400, field=None):
    if field:
        return JsonResponse(
            {"errors": {field: [{"message": message, "code": "invalid"}]}},
            status=status,
        )
    return JsonResponse({"error": message}, status=status)


def qa_json_404(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except Http404:
            return JsonResponse({"error": "Not found."}, status=404)
    return wrapped


def qa_payload(request, *, boolean_fields=()):
    if request.content_type != "application/json":
        return request.POST, None
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return None, qa_error("Invalid JSON body.", field="__all__")
    if not isinstance(data, dict):
        return None, qa_error("Provide a JSON object.", field="__all__")
    for field, value in data.items():
        if isinstance(value, str) or (field in boolean_fields and type(value) is bool):
            continue
        return None, qa_error("Provide a string value.", field=field)
    return data, None


def qa_text_value(data, field, max_length):
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        return None, qa_error("This field is required.", field=field)
    value = value.strip()
    if len(value) > max_length:
        return None, qa_error(f"Ensure this value has at most {max_length} characters.", field=field)
    return value, None


def qa_boolean_value(request, data, field):
    value = data.get(field)
    if request.content_type == "application/json":
        if type(value) is not bool:
            return None, qa_error("Provide a JSON boolean.", field=field)
        return value, None
    if value not in {"true", "false"}:
        return None, qa_error('Use "true" or "false".', field=field)
    return value == "true", None


def qa_question_queryset(course):
    active_verified_answers = CourseAnswer.objects.filter(
        question_id=OuterRef("pk"),
        archived_at__isnull=True,
        is_verified=True,
    )
    return (
        CourseQuestion.objects.filter(course=course)
        .select_related("author", "course")
        .annotate(
            answer_count=Count(
                "answers",
                filter=Q(answers__archived_at__isnull=True),
                distinct=True,
            ),
            has_verified_answer=Exists(active_verified_answers),
        )
    )


def qa_get_question(request, question_id, *, lock=False, allow_archived=False):
    questions = CourseQuestion.objects.filter(course=request.course)
    if lock:
        questions = questions.select_for_update()
    question = get_object_or_404(questions, pk=question_id)
    if (question.archived_at is not None and not request.is_dept_override
            and not allow_archived):
        raise Http404
    return question


def qa_question_capabilities(request, question, answer_count):
    user = request.user
    is_mine = question.author_id == user.pk
    is_student_author = is_mine and not user.is_staff and not user.is_superuser
    active = question.archived_at is None
    can_manage_content = request.can_manage_content
    is_department = request.is_dept_override
    can_edit = active and (
        is_department or (is_student_author and answer_count == 0)
    )
    can_archive = active and (
        is_department or request.is_owner or request.is_ta or is_student_author
    )
    return {
        "is_mine": is_mine,
        "can_edit": can_edit,
        "can_archive": can_archive,
        "can_answer": active and can_manage_content,
        "can_pin": active and can_manage_content,
        "can_verify": active and can_manage_content,
    }


def qa_question_data(request, question, *, include_body=False):
    answer_count = question.answer_count
    data = {
        "id": question.pk,
        "title": question.title,
        "author_name": question.author_label,
        "author_role_label": question.author_role_label,
        "created_at": question.created_at,
        "updated_at": question.updated_at,
        "edited_at": question.edited_at,
        "is_pinned": question.is_pinned,
        "pinned_at": question.pinned_at,
        "answer_count": answer_count,
        "has_verified_answer": question.has_verified_answer,
        **qa_question_capabilities(request, question, answer_count),
    }
    if include_body:
        data["body"] = question.body
    else:
        data["body_excerpt"] = question.body[:240]
    if request.can_manage_content:
        data["archived_at"] = question.archived_at
        data["can_restore"] = question.archived_at is not None and (
            request.is_dept_override
            or question.archived_by_id == request.user.pk
        )
    return data


def qa_answer_data(request, question, answer):
    is_mine = answer.author_id == request.user.pk
    active = question.archived_at is None and answer.archived_at is None
    is_department = request.is_dept_override
    can_manage_content = request.can_manage_content
    return {
        "id": answer.pk,
        "body": answer.body,
        "author_name": answer.author_label,
        "author_role_label": answer.author_role_label,
        "created_at": answer.created_at,
        "updated_at": answer.updated_at,
        "edited_at": answer.edited_at,
        "is_verified": answer.is_verified,
        "verified_at": answer.verified_at,
        "is_mine": is_mine,
        "can_edit": active and (
            is_department or (can_manage_content and is_mine)
        ),
        "can_archive": active and (
            is_department or request.is_owner or request.is_ta
            or (can_manage_content and is_mine)
        ),
        "can_verify": active and can_manage_content,
        **({
            "archived_at": answer.archived_at,
            "can_restore": answer.archived_at is not None and (
                is_department or answer.archived_by_id == request.user.pk
            ),
        } if can_manage_content else {}),
    }


def qa_refreshed_question(request, question):
    return qa_question_queryset(request.course).get(pk=question.pk)


def qa_write_response(question, request, *, status=200):
    return JsonResponse(
        {"question": qa_question_data(
            request, qa_refreshed_question(request, question), include_body=True,
        )},
        status=status,
    )


@qa_json_404
@course_access()
@require_http_methods(["GET", "POST"])
def qa_question_list(request, pk):
    if request.method == "POST":
        return qa_question_create_response(request, pk)
    status_filter = request.GET.get("status", "")
    if status_filter not in {"", "answered", "unanswered", "mine", "archived"}:
        return qa_error("Unknown status filter.", field="status")
    if status_filter == "archived" and not request.can_manage_content:
        return qa_error("Only course staff may request archived questions.", field="status")

    def positive_integer(name, default):
        raw = request.GET.get(name)
        if raw is None:
            return default, None
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None, qa_error("Provide a positive integer.", field=name)
        if value < 1:
            return None, qa_error("Provide a positive integer.", field=name)
        return value, None

    page, error = positive_integer("page", 1)
    if error:
        return error
    page_size, error = positive_integer("page_size", 20)
    if error:
        return error
    if page_size > 100:
        return qa_error("Page size cannot exceed 100.", field="page_size")

    questions = qa_question_queryset(request.course)
    if status_filter == "archived":
        questions = questions.filter(archived_at__isnull=False)
    else:
        questions = questions.filter(archived_at__isnull=True)
    if status_filter == "answered":
        questions = questions.filter(answer_count__gt=0)
    elif status_filter == "unanswered":
        questions = questions.filter(answer_count=0)
    elif status_filter == "mine":
        questions = questions.filter(author=request.user)

    search = request.GET.get("q", "").strip()
    if search:
        questions = questions.filter(Q(title__icontains=search) | Q(body__icontains=search))
    questions = questions.order_by(
        "-is_pinned", "-has_verified_answer", "-created_at", "-pk",
    )

    count = questions.count()
    start = (page - 1) * page_size
    results = questions[start:start + page_size] if start < count else []
    return JsonResponse({
        "count": count,
        "page": page,
        "page_size": page_size,
        "results": [qa_question_data(request, question) for question in results],
    })


@qa_json_404
@course_access()
@require_http_methods(["GET"])
def qa_question_detail(request, pk, question_id):
    question = get_object_or_404(
        qa_question_queryset(request.course),
        pk=question_id,
    )
    if question.archived_at is not None and not request.can_manage_content:
        raise Http404
    answers = CourseAnswer.objects.filter(question=question).select_related("author")
    if not request.can_manage_content:
        answers = answers.filter(archived_at__isnull=True)
    answers = answers.order_by("-is_verified", "created_at", "pk")
    return JsonResponse({
        "question": qa_question_data(request, question, include_body=True),
        "answers": [qa_answer_data(request, question, answer) for answer in answers],
    })


def qa_question_create_response(request, pk):
    if (request.user.is_staff or request.user.is_superuser
            or CourseTA.objects.filter(course=request.course, user=request.user).exists()
            or not Enrollment.objects.filter(course=request.course, student=request.user).exists()):
        return qa_error(
            "Only enrolled students who are not course TAs may post questions.",
            status=403,
        )
    data, error = qa_payload(request)
    if error:
        return error
    title, error = qa_text_value(data, "title", 200)
    if error:
        return error
    body, error = qa_text_value(data, "body", 5000)
    if error:
        return error
    question = create_course_question(
        course=request.course,
        author=request.user,
        title=title,
        body=body,
    )
    return JsonResponse(
        qa_question_data(
            request, qa_refreshed_question(request, question), include_body=True,
        ),
        status=201,
    )


@qa_json_404
@course_access()
@require_POST
def qa_question_edit(request, pk, question_id):
    with transaction.atomic():
        question = qa_get_question(request, question_id, lock=True)
        if question.archived_at is not None:
            raise Http404
        answer_count = CourseAnswer.objects.filter(
            question=question, archived_at__isnull=True,
        ).count()
        is_student_author = (
            question.author_id == request.user.pk
            and not request.user.is_staff
            and not request.user.is_superuser
        )
        if not request.is_dept_override and not (is_student_author and answer_count == 0):
            return qa_error("You cannot edit this question.", status=403)
        data, error = qa_payload(request)
        if error:
            return error
        title, error = qa_text_value(data, "title", 200)
        if error:
            return error
        body, error = qa_text_value(data, "body", 5000)
        if error:
            return error
        if title != question.title or body != question.body:
            question.title = title
            question.body = body
            question.edited_at = timezone.now()
            question.save(update_fields=["title", "body", "edited_at", "updated_at"])
    return qa_write_response(question, request)


@qa_json_404
@course_access()
@require_POST
def qa_question_archive(request, pk, question_id):
    with transaction.atomic():
        question = qa_get_question(request, question_id, lock=True)
        if question.archived_at is not None:
            raise Http404
        is_student_author = (
            question.author_id == request.user.pk
            and not request.user.is_staff
            and not request.user.is_superuser
        )
        if not (request.is_dept_override or request.is_owner or request.is_ta or is_student_author):
            return qa_error("You cannot archive this question.", status=403)
        question.archived_at = timezone.now()
        question.archived_by = request.user
        question.save(update_fields=["archived_at", "archived_by", "updated_at"])
    return qa_write_response(question, request)


@qa_json_404
@course_access()
@require_POST
def qa_question_restore(request, pk, question_id):
    with transaction.atomic():
        question = qa_get_question(
            request, question_id, lock=True, allow_archived=True,
        )
        if question.archived_at is None:
            raise Http404
        can_restore = request.is_dept_override or (
            (request.is_owner or request.is_ta)
            and question.archived_by_id == request.user.pk
        )
        if not can_restore:
            return qa_error("You cannot restore this question.", status=403)
        question.archived_at = None
        question.archived_by = None
        question.save(update_fields=["archived_at", "archived_by", "updated_at"])
    return qa_write_response(question, request)


@qa_json_404
@course_access()
@require_POST
def qa_question_pin(request, pk, question_id):
    with transaction.atomic():
        question = qa_get_question(request, question_id, lock=True)
        if question.archived_at is not None:
            raise Http404
        if not request.can_manage_content:
            return qa_error("You cannot pin this question.", status=403)
        data, error = qa_payload(request, boolean_fields={"is_pinned"})
        if error:
            return error
        is_pinned, error = qa_boolean_value(request, data, "is_pinned")
        if error:
            return error
        question.is_pinned = is_pinned
        question.pinned_at = timezone.now() if is_pinned else None
        question.save(update_fields=["is_pinned", "pinned_at", "updated_at"])
    return qa_write_response(question, request)


@qa_json_404
@course_access()
@require_POST
def qa_answer_create(request, pk, question_id):
    with transaction.atomic():
        question = qa_get_question(request, question_id, lock=True)
        if question.archived_at is not None:
            raise Http404
        if not request.can_manage_content:
            return qa_error("Only the course owner, a TA, or Department may answer.", status=403)
        data, error = qa_payload(request)
        if error:
            return error
        body, error = qa_text_value(data, "body", 5000)
        if error:
            return error
        answer = create_course_answer(question=question, author=request.user, body=body)
    question = qa_refreshed_question(request, question)
    return JsonResponse({
        "answer": qa_answer_data(request, question, answer),
        "question": qa_question_data(request, question, include_body=True),
    }, status=201)


@qa_json_404
@course_access()
@require_POST
def qa_answer_edit(request, pk, question_id, answer_id):
    with transaction.atomic():
        question = qa_get_question(request, question_id, lock=True)
        if question.archived_at is not None:
            raise Http404
        answer = get_object_or_404(
            CourseAnswer.objects.select_for_update().filter(question=question),
            pk=answer_id,
        )
        if answer.archived_at is not None:
            raise Http404
        if not request.is_dept_override and not (
            request.can_manage_content and answer.author_id == request.user.pk
        ):
            return qa_error("You cannot edit this answer.", status=403)
        data, error = qa_payload(request)
        if error:
            return error
        body, error = qa_text_value(data, "body", 5000)
        if error:
            return error
        if body != answer.body:
            answer.body = body
            answer.edited_at = timezone.now()
            answer.save(update_fields=["body", "edited_at", "updated_at"])
    question = qa_refreshed_question(request, question)
    answer.refresh_from_db()
    return JsonResponse({
        "answer": qa_answer_data(request, question, answer),
        "question": qa_question_data(request, question, include_body=True),
    })


@qa_json_404
@course_access()
@require_POST
def qa_answer_archive(request, pk, question_id, answer_id):
    with transaction.atomic():
        question = qa_get_question(request, question_id, lock=True)
        if question.archived_at is not None:
            raise Http404
        answer = get_object_or_404(
            CourseAnswer.objects.select_for_update().filter(question=question),
            pk=answer_id,
        )
        if answer.archived_at is not None:
            raise Http404
        can_archive = (
            request.is_dept_override or request.is_owner or request.is_ta
            or (request.can_manage_content and answer.author_id == request.user.pk)
        )
        if not can_archive:
            return qa_error("You cannot archive this answer.", status=403)
        answer.archived_at = timezone.now()
        answer.archived_by = request.user
        answer.save(update_fields=["archived_at", "archived_by", "updated_at"])
    question = qa_refreshed_question(request, question)
    answer.refresh_from_db()
    return JsonResponse({
        "answer": qa_answer_data(request, question, answer),
        "question": qa_question_data(request, question, include_body=True),
    })


@qa_json_404
@course_access()
@require_POST
def qa_answer_restore(request, pk, question_id, answer_id):
    with transaction.atomic():
        question = qa_get_question(request, question_id, lock=True)
        if question.archived_at is not None:
            raise Http404
        answer = get_object_or_404(
            CourseAnswer.objects.select_for_update().filter(question=question),
            pk=answer_id,
        )
        if answer.archived_at is None:
            raise Http404
        can_restore = request.is_dept_override or (
            (request.is_owner or request.is_ta)
            and answer.archived_by_id == request.user.pk
        )
        if not can_restore:
            return qa_error("You cannot restore this answer.", status=403)
        answer.archived_at = None
        answer.archived_by = None
        answer.save(update_fields=["archived_at", "archived_by", "updated_at"])
    question = qa_refreshed_question(request, question)
    answer.refresh_from_db()
    return JsonResponse({
        "answer": qa_answer_data(request, question, answer),
        "question": qa_question_data(request, question, include_body=True),
    })


@qa_json_404
@course_access()
@require_POST
def qa_answer_verify(request, pk, question_id, answer_id):
    with transaction.atomic():
        question = qa_get_question(request, question_id, lock=True)
        if question.archived_at is not None:
            raise Http404
        answer = get_object_or_404(
            CourseAnswer.objects.select_for_update().filter(question=question),
            pk=answer_id,
        )
        if answer.archived_at is not None:
            raise Http404
        if not request.can_manage_content:
            return qa_error("You cannot verify this answer.", status=403)
        data, error = qa_payload(request, boolean_fields={"is_verified"})
        if error:
            return error
        is_verified, error = qa_boolean_value(request, data, "is_verified")
        if error:
            return error
        answer.is_verified = is_verified
        answer.verified_by = request.user if is_verified else None
        answer.verified_at = timezone.now() if is_verified else None
        answer.save(update_fields=[
            "is_verified", "verified_by", "verified_at", "updated_at",
        ])
    question = qa_refreshed_question(request, question)
    answer.refresh_from_db()
    return JsonResponse({
        "answer": qa_answer_data(request, question, answer),
        "question": qa_question_data(request, question, include_body=True),
    })
