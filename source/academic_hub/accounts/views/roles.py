import re

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import render

from course.models import Course
from notifications.services import notify_role_changes

from ..models import AdvisorAssignment, User


# --- role management (US-05) ---------------------------------------------
# Roles are the two flags the Users table already carries. They are treated as
# mutually exclusive here, which is how the seeded rows already use them:
#
#     Student      isLecturer=f  isDepartment=f
#     Lecturer     isLecturer=t  isDepartment=f
#     Department   isLecturer=f  isDepartment=t
#
# Django's own admin is not used for this. It gates on is_staff, which maps to
# isLecturer, so it lets lecturers in and keeps the department admin out - the
# reverse of what US-05 asks for. It also needs the Users_groups and
# Users_user_permissions tables, which this database does not have.
ROLES = {
    "student": {"label": "Student", "is_staff": False, "is_superuser": False},
    "lecturer": {"label": "Lecturer", "is_staff": True, "is_superuser": False},
    "department": {"label": "Department", "is_staff": False, "is_superuser": True},
}


def role_of(user):
    """The role key for a user, from the flags on the row."""
    if user.is_superuser:
        return "department"
    if user.is_staff:
        return "lecturer"
    return "student"


def is_department_admin(user):
    """Department is the admin role for this app, not staff/lecturer."""
    return user.is_authenticated and user.is_superuser


@login_required(login_url="accounts:login")
def roles_view(request):
    if not is_department_admin(request.user):
        return render(request, "accounts/no_access.html", status=403)

    notice = error = None
    role_fields = {}
    programme_fields = {}

    if request.method == "POST":
        action = request.POST.get("action", "role")
        if action in {"roles", "role"}:
            if action == "roles":
                role_fields = {
                    key.removeprefix("role_for_"): value
                    for key, value in request.POST.items()
                    if key.startswith("role_for_")
                }
                programme_fields = {
                    key.removeprefix("programme_for_"): value
                    for key, value in request.POST.items()
                    if key.startswith("programme_for_")
                }
            else:
                target_id = request.POST.get("nisit_id", "")
                role_fields = {target_id: request.POST.get("role", "")}
                if "department" in request.POST:
                    programme_fields[target_id] = request.POST["department"]
            changes = []
            for target_id, new_role in role_fields.items():
                target = User.objects.filter(nisit_id=target_id).first()
                if target is None:
                    error = f"Account {target_id} no longer exists."
                    break
                if target_id == request.user.nisit_id:
                    error = "You cannot change your own role. Ask another Department admin."
                    break
                if new_role not in ROLES:
                    error = f"That is not a role we recognise for {target.get_full_name()}."
                    break
                programme = target.department
                if new_role == "student":
                    programme = programme_fields.get(target_id, programme).strip().lower()
                # The page sends every row, with what each one showed when it
                # was loaded. A row this admin did not touch is skipped, so a
                # page left open cannot undo a change another admin made since.
                shown_role = request.POST.get(f"was_role_for_{target_id}")
                shown_programme = request.POST.get(f"was_programme_for_{target_id}", "")
                if shown_role == new_role and (
                    new_role != "student"
                    or programme_fields.get(target_id, shown_programme).strip().lower()
                    == shown_programme.strip().lower()
                ):
                    continue
                if role_of(target) == new_role and (
                    new_role != "student" or target.department.strip().lower() == programme
                ):
                    continue
                if new_role == "student" and programme not in User.Department.values:
                    error = f"Choose CPE or SKE for {target.get_full_name()}."
                    break
                if target.is_staff and new_role != "lecturer" and Course.objects.filter(owner=target).exists():
                    error = "This lecturer cannot change roles while they still own a class."
                    break
                changes.append((target, new_role, programme))

            if not error and changes:
                with transaction.atomic():
                    for target, new_role, programme in changes:
                        flags = ROLES[new_role]
                        if new_role != "student":
                            AdvisorAssignment.objects.filter(student=target).delete()
                        if new_role != "lecturer":
                            AdvisorAssignment.objects.filter(advisor=target).delete()
                        target.is_staff = flags["is_staff"]
                        target.is_superuser = flags["is_superuser"]
                        target.department = programme
                        target.save(update_fields=["is_staff", "is_superuser", "department"])
                notify_role_changes(
                    request.user,
                    [(target, ROLES[new_role]["label"]) for target, new_role, _ in changes],
                )
                notice = f"Role changes saved for {len(changes)} account{'s' if len(changes) != 1 else ''}."
            elif not error:
                notice = "There are no role changes to save."
        elif action == "advisor":
            target_id = request.POST.get("nisit_id", "")
            target = User.objects.filter(nisit_id=target_id).first()
            if target is None:
                error = "That account no longer exists."
            else:
                selected_ids = set(request.POST.getlist("student_ids"))
                typed_ids = set(re.split(r"[\s,;]+", request.POST.get("student_ids_text", "").strip()))
                typed_ids.discard("")
                selected_ids.update(typed_ids)
                if role_of(target) != "lecturer" or not target.is_active:
                    error = "Only active Lecturer accounts can have advised students."
                else:
                    students = list(User.objects.filter(
                        nisit_id__in=selected_ids,
                        is_staff=False,
                        is_superuser=False,
                        is_active=True,
                    ))
                    students_by_id = {student.nisit_id: student for student in students}
                    invalid_ids = sorted(selected_ids - students_by_id.keys())
                    if invalid_ids:
                        error = (
                            "These IDs are unknown, inactive, or not student accounts: "
                            + ", ".join(invalid_ids)
                        )
                    else:
                        with transaction.atomic():
                            AdvisorAssignment.objects.filter(advisor=target).exclude(
                                student_id__in=selected_ids
                            ).delete()
                            for student in students:
                                AdvisorAssignment.objects.update_or_create(
                                    student=student,
                                    defaults={"advisor": target},
                                )
                        count = len(selected_ids)
                        notice = (
                            f"{count} advised student{'s' if count != 1 else ''} saved for "
                            f"{target.get_full_name()}."
                        )
        else:
            error = "That action is not recognised."

    assignments = AdvisorAssignment.objects.filter(
        advisor__is_staff=True,
        advisor__is_superuser=False,
        advisor__is_active=True,
    ).select_related("advisor", "student")
    assignments_by_student = {
        assignment.student_id: assignment.advisor for assignment in assignments
    }
    advisee_counts = {}
    for assignment in assignments:
        advisee_counts[assignment.advisor_id] = advisee_counts.get(assignment.advisor_id, 0) + 1
    student_users = User.objects.filter(
        is_staff=False, is_superuser=False, is_active=True
    ).order_by("nisit_id")
    students = [
        {
            "user": student,
            "advisor": assignments_by_student.get(student.nisit_id),
        }
        for student in student_users
    ]
    # One query for everyone who owns a class, rather than one per account.
    class_owner_ids = set(Course.objects.values_list("owner_id", flat=True))
    people = []
    for u in User.objects.all().order_by("nisit_id"):
        key = role_of(u)
        people.append({
            "user": u,
            "role": key,
            "role_label": ROLES[key]["label"],
            "selected_role": role_fields.get(u.nisit_id, key) if error else key,
            "programme": (
                programme_fields.get(u.nisit_id, u.department) if error else u.department
            ).strip().lower(),
            "is_me": u.nisit_id == request.user.nisit_id,
            "has_owned_classes": u.is_staff and u.pk in class_owner_ids,
            "advisor": assignments_by_student.get(u.nisit_id),
            "advisee_count": advisee_counts.get(u.nisit_id, 0),
        })

    # Department first, then lecturers, then students; A to Z inside each role.
    role_rank = {"department": 0, "lecturer": 1, "student": 2}
    people.sort(key=lambda p: (
        role_rank[p["role"]],
        p["user"].get_full_name().casefold(),
        p["user"].nisit_id,
    ))

    return render(request, "accounts/roles.html", {
        "people": people,
        "roles": [{"key": k, "label": v["label"]} for k, v in ROLES.items()],
        "students": students,
        "programmes": User.Department.values,
        "notice": notice,
        "error": error,
    })
