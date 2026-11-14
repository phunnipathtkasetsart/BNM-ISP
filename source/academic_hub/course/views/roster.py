"""Class roster: members, manual and CSV import, removal and TA roles."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from ..forms import CsvRosterForm, RosterForm
from ..models import Enrollment
from ..permissions import course_access
from ..responses import bind_form, detail_url, fail, form_error, form_errors_json, is_api, respond
from ..services import add_students, assign_ta, remove_ta


def members_url(course_pk):
    return reverse("course:members_page", args=[course_pk])


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


@course_access()
@require_http_methods(["GET"])
def members(request, pk):
    course = request.course
    tas = set(course.ta_links.values_list("user_id", flat=True))
    return JsonResponse({
        "lecturer": {"id": course.owner_id, "name": course.owner.get_full_name()},
        "students": [
            {"id": e.student_id, "name": e.student.get_full_name(), "is_ta": e.student_id in tas}
            for e in course.enrollments.select_related("student")
        ],
    })


@course_access(owner=True)
@require_POST
def import_members(request, pk):
    form = bind_form(request, RosterForm)
    if form.is_valid():
        return finish_import(request, form, csv=False)
    if is_api(request):
        return form_error(request, form, "Add students", course=request.course)
    return render_members(request, form=form, status=400)


# CSV uploads are in request.FILES, separate from request.POST.
@course_access(owner=True)
@require_POST
def import_csv(request, pk):
    form = CsvRosterForm(request.POST, request.FILES)
    if form.is_valid():
        return finish_import(request, form, csv=True)
    if is_api(request):
        return form_errors_json(form)
    return render_members(request, csv_form=form, status=400)


def finish_import(request, form, *, csv):
    added = add_students(request.course, form.students)
    result = {"added": added, "already_enrolled": len(form.students) - added,
              "duplicates_skipped": form.duplicate_count, "rejected": form.rejected}
    status = 200 if form.students else 400
    if is_api(request):
        return JsonResponse(result, status=status)
    if form.rejected:
        form_arg = {"csv_form": form} if csv else {"form": form}
        return render_members(request, import_report=result, status=status, **form_arg)
    messages.success(request, f"Added {added} student(s); {result['already_enrolled']} already enrolled; "
                     f"{result['duplicates_skipped']} duplicate row(s) skipped.")
    return redirect(members_url(request.course.pk))


@course_access(owner=True)
@require_POST
def remove_member(request, pk, student_id):
    get_object_or_404(Enrollment, course=request.course, student_id=student_id).delete()
    return respond(request, {"removed": True}, "Student removed from the class.", members_url(pk))


@course_access(owner=True)
@require_POST
def ta_assign(request, pk, student_id=None):
    # The Nisit ID comes from the form body or, for row buttons, the URL.
    target_id = request.POST.get("student_id", "").strip() or student_id
    if not target_id:
        messages.error(request, "Please enter a Nisit ID.")
        return redirect(detail_url(pk))
    try:
        created = assign_ta(request.course, target_id)
    except ValidationError as error:
        return fail(request, error.messages[0], detail_url(pk))
    return respond(
        request,
        {"assigned": True, "created": created},
        "Student is now a TA." if created else "Student is already a TA.",
        detail_url(pk),
        status=201 if created else 200,
    )


@course_access(owner=True)
@require_POST
def ta_remove(request, pk, student_id):
    removed = remove_ta(request.course, student_id)
    return respond(
        request,
        {"removed": removed},
        "TA role removed." if removed else "That student was not a TA.",
        detail_url(pk),
    )
