import json

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from .forms import CourseForm, CsvRosterForm, JoinForm, RosterForm
from .models import Course, Enrollment
from .permissions import can_teach, course_access, visible_courses
from .services import add_students, create_course


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
    if api_request(request):
        return JsonResponse({"courses": [course_data(c, request.user) for c in courses]})
    return render(request, "course/dashboard.html", {"courses": courses, "can_create": can_teach(request.user),
                  "can_join": not request.user.is_staff and not request.user.is_superuser,
                  "create_form": CourseForm(), "join_form": JoinForm()})


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


@course_access()
@require_http_methods(["GET"])
def detail(request, pk):
    course = request.course
    if api_request(request):
        return JsonResponse(course_data(course, request.user))
    return render(request, "course/detail.html", {"course": course,
                  "can_manage": course.owner_id == request.user.pk and can_teach(request.user)})


def render_members(request, *, form=None, csv_form=None, status=200):
    course = request.course
    return render(request, "course/members.html", {
        "course": course,
        "members": course.enrollments.select_related("student"),
        "can_manage": course.owner_id == request.user.pk and can_teach(request.user),
        "roster_form": form if form is not None else RosterForm(),
        "csv_form": csv_form if csv_form is not None else CsvRosterForm(),
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
            course.name = form.cleaned_data["name"]
            course.section = form.cleaned_data["section"]
            course.save(update_fields=["name", "section"])
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
    return JsonResponse({"lecturer": {"id": request.course.owner_id, "name": request.course.owner.get_full_name()},
                         "students": [{"id": e.student_id, "name": e.student.get_full_name()} for e in roster]})


@course_access(owner=True)
@require_POST
def import_members(request, pk):
    form = bind_form(request, RosterForm)
    if not form.is_valid():
        if not api_request(request):
            return render_members(request, form=form, status=400)
        return form_error(request, form, "Add students", course=request.course)
    students = form.cleaned_data["student_ids"]
    added = add_students(request.course, students)
    if api_request(request):
        return JsonResponse({"added": added, "already_enrolled": len(students) - added})
    messages.success(request, f"Added {added} student(s); {len(students) - added} already enrolled.")
    return redirect("course:members_page", pk=pk)


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
    added = add_students(request.course, form.students)
    result = {"added": added, "already_enrolled": len(form.students) - added,
              "duplicates_skipped": form.duplicate_count}
    if api_request(request):
        return JsonResponse(result)
    messages.success(request, f"CSV imported: {added} student(s) added; "
                     f"{result['already_enrolled']} already enrolled; "
                     f"{result['duplicates_skipped']} duplicate row(s) skipped.")
    return redirect("course:members_page", pk=pk)
