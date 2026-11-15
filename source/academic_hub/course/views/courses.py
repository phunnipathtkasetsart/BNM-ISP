"""Class lifecycle: dashboard, create, join, detail, edit, archive and delete."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from accounts.models import AdvisorAssignment

from ..forms import ALLOWED_ACCEPT_ATTR, CourseForm, CoursePostForm, JoinForm, MaterialUploadForm
from ..models import Course, CoursePost, CourseTA, Enrollment
from ..permissions import can_teach, course_access, is_student_user, visible_courses
from ..responses import bind_form, detail_url, form_error, is_api, respond
from ..services import archive_course, create_course, restore_course, update_course

DEADLINE_WINDOW = timedelta(days=7)


def course_data(course, user):
    data = {"id": course.pk, "name": course.name, "section": course.section, "owner_id": course.owner_id}
    # Expose class codes only to owners who are allowed to teach.
    if course.owner_id == user.pk and can_teach(user):
        data["class_code"] = course.class_code
    return data


def upcoming(posts):
    """Active posts whose deadline falls within the next week, soonest first."""
    now = timezone.now()
    return posts.filter(
        archived_at__isnull=True,
        deadline__isnull=False,
        deadline__gte=now,
        deadline__lte=now + DEADLINE_WINDOW,
    ).order_by("deadline")


def search_courses(courses, query):
    return courses.filter(
        Q(name__icontains=query)
        | Q(section__icontains=query)
        | Q(owner__first_name__icontains=query)
        | Q(owner__last_name__icontains=query)
    ).distinct()


def advisor_for(user):
    return (
        AdvisorAssignment.objects.filter(
            student=user,
            advisor__is_staff=True,
            advisor__is_superuser=False,
            advisor__is_active=True,
        )
        .select_related("advisor")
        .first()
    )


@course_access()
@require_http_methods(["GET"])
def dashboard(request):
    user = request.user
    query = request.GET.get("q", "").strip()
    all_courses = visible_courses(user).select_related("owner")
    if user.is_superuser and query:
        all_courses = search_courses(all_courses, query)

    courses = all_courses.filter(archived_at__isnull=True)
    archived_courses = all_courses.filter(archived_at__isnull=False) if can_teach(user) else []

    if is_api(request):
        return JsonResponse({
            "courses": [course_data(c, user) for c in courses],
            "archived_courses": [course_data(c, user) for c in archived_courses],
        })

    is_student = is_student_user(user)
    return render(request, "course/dashboard.html", {
        "courses": courses,
        "archived_courses": archived_courses,
        "query": query,
        "can_create": can_teach(user),
        "can_join": is_student,
        "ta_course_ids": set(CourseTA.objects.filter(user=user).values_list("course_id", flat=True)),
        "upcoming_deadlines": upcoming(CoursePost.objects.filter(course__in=courses)).select_related("course"),
        "create_form": CourseForm(),
        "join_form": JoinForm(),
        "is_student": is_student,
        "advisor_assignment": advisor_for(user) if is_student else None,
    })


@course_access(owner=True)
@require_POST
def archive_course_view(request, pk):
    archive_course(request.course, request.user)
    return respond(request, {"archived": True}, "Class archived successfully.", reverse("course:dashboard"))


@course_access(owner=True)
@require_POST
def restore_course_view(request, pk):
    restore_course(request.course)
    return respond(request, {"restored": True}, "Class restored successfully.", detail_url(pk))


@course_access(lecturer=True)
@require_http_methods(["GET", "POST"])
def create(request):
    if request.method == "GET":
        return render(request, "course/form.html", {"form": CourseForm(), "title": "Create class"})
    form = bind_form(request, CourseForm)
    if form.is_valid():
        # Ownership always comes from the authenticated user, never the submitted data.
        try:
            course = create_course(owner=request.user, name=form.cleaned_data["name"],
                                   section=form.cleaned_data["section"])
        except ValidationError as error:
            form.add_error(None, error)
        else:
            return respond(request, course_data(course, request.user),
                           "Class created. Share the code with your students.",
                           detail_url(course.pk), status=201)
    return form_error(request, form, "Create class")


@course_access(student=True)
@require_http_methods(["GET", "POST"])
def join(request):
    if request.method == "GET":
        return render(request, "course/form.html", {"form": JoinForm(), "title": "Join class"})
    form = bind_form(request, JoinForm)
    if form.is_valid():
        course = Course.objects.filter(class_code=form.cleaned_data["code"]).first()
        if course is None:
            form.add_error("code", "Class not found. Check the code with your lecturer.")
        else:
            # joined=False means the user was already enrolled, not that they are not a member.
            _, created = Enrollment.objects.get_or_create(course=course, student=request.user)
            return respond(
                request,
                {"course": course_data(course, request.user), "joined": created},
                "You joined the class." if created else "You are already a member of this class.",
                detail_url(course.pk),
                status=201 if created else 200,
            )
    return form_error(request, form, "Join class")


def topic_groups(course):
    """Active materials grouped by topic, newest topic first. Files with no topic last."""
    by_topic = {}
    for material in course.materials.filter(archived_at__isnull=True).order_by("-created_at", "-pk"):
        by_topic.setdefault(material.topic_id, []).append(material)
    groups = [{"title": t.title, "materials": by_topic[t.pk]}
              for t in course.topics.order_by("-pk") if t.pk in by_topic]
    if None in by_topic:
        groups.append({"title": "No topic", "materials": by_topic[None]})
    return groups


def can_ask_question(request):
    user, course = request.user, request.course
    return (
        is_student_user(user)
        and course.enrollments.filter(student=user).exists()
        and not course.ta_links.filter(user=user).exists()
    )


def _staff_only_context(course, manage):
    """Archived content and topic choices, shown to course staff only."""
    if not manage:
        return {"archived_posts": [], "archived_materials": [], "topics": []}
    return {
        "archived_posts": course.posts.filter(archived_at__isnull=False)
        .select_related("author").prefetch_related("attachments"),
        "archived_materials": course.materials.filter(archived_at__isnull=False),
        "topics": list(course.topics.order_by("-pk")),
    }


def detail_context(request, post_form=None, material_form=None):
    course = request.course
    manage = request.can_manage_content
    if manage and post_form is None:
        post_form = CoursePostForm(course=course)
    if manage and material_form is None:
        material_form = MaterialUploadForm(course=course, auto_id="material_%s")

    groups = topic_groups(course)
    ta_user_ids = set(course.ta_links.values_list("user_id", flat=True))
    available_students = get_user_model().objects.filter(
        is_active=True, is_staff=False, is_superuser=False
    ).exclude(pk__in=ta_user_ids)

    return {
        "course": course,
        "group_works": course.group_works.all(),
        "posts": course.posts.filter(archived_at__isnull=True)
        .select_related("author").prefetch_related("attachments"),
        **_staff_only_context(course, manage),
        "upcoming_deadlines": upcoming(course.posts),
        "post_form": post_form,
        "material_form": material_form,
        "topic_groups": groups,
        "material_count": sum(len(g["materials"]) for g in groups),
        "can_manage": request.can_manage_roster,
        "can_manage_content": manage,
        "can_delete_any": request.is_owner or request.is_dept_override,
        "can_filter_questions": manage,
        "can_ask_question": can_ask_question(request),
        "file_accept": ALLOWED_ACCEPT_ATTR,  # file picker filter on the post edit forms
        "is_department": request.is_dept_override,
        "enrollments": course.enrollments.select_related("student") if request.can_manage_roster else [],
        "ta_ids": ta_user_ids,
        "available_students": available_students,
    }


@course_access()
@require_http_methods(["GET"])
def detail(request, pk):
    if is_api(request):
        data = course_data(request.course, request.user)
        data["can_manage_content"] = request.can_manage_content
        data["can_manage_roster"] = request.can_manage_roster
        return JsonResponse(data)
    return render(request, "course/detail.html", detail_context(request))


@course_access(owner=True)
@require_http_methods(["GET", "POST"])
def edit(request, pk):
    course = request.course
    if request.method == "GET":
        form = CourseForm(initial={"name": course.name, "section": course.section})
        return render(request, "course/edit.html", {"form": form, "title": "Edit class", "course": course})
    form = bind_form(request, CourseForm)
    if form.is_valid():
        try:
            update_course(course, name=form.cleaned_data["name"], section=form.cleaned_data["section"])
        except ValidationError as error:
            form.add_error(None, error)
        else:
            return respond(request, course_data(course, request.user), "Class updated.", detail_url(pk))
    return form_error(request, form, "Edit class", course=course, template="course/edit.html")


# Deleting a course cascades to its records but keeps student accounts.
@course_access(owner=True)
@require_POST
def delete(request, pk):
    request.course.delete()
    return respond(request, {"deleted": True}, "Class deleted.", reverse("course:dashboard"))
