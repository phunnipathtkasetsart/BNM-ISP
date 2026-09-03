from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .forms import ForgotPasswordForm, LoginForm, RegisterForm
from .models import User


def login_view(request):
    if request.user.is_authenticated:
        return redirect("accounts:dashboard")

    error = None
    form = LoginForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        user = authenticate(
            request,
            nisit_id=form.cleaned_data["nisit_id"],
            password=form.cleaned_data["password"],
        )
        if user is not None:
            login(request, user)
            return redirect("accounts:dashboard")
        error = "Invalid Nisit ID or password."

    return render(request, "accounts/login.html", {"form": form, "error": error})


def register_view(request):
    if request.user.is_authenticated:
        return redirect("accounts:dashboard")

    form = RegisterForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        return redirect("accounts:dashboard")

    return render(request, "accounts/register.html", {"form": form})


@login_required(login_url="accounts:login")
def dashboard_view(request):
    return render(request, "accounts/dashboard.html")


def logout_view(request):
    logout(request)
    return redirect("accounts:login")


def forgot_password_view(request):
    """The 'Forgot password?' page behind Having Problems?.

    The form validates the address, but nothing is emailed yet: that needs an
    email backend in settings.py (and, for real resets, Django's
    PasswordResetConfirmView on the other end). Until then this confirms the
    request without claiming a message was sent.
    """
    submitted = False
    form = ForgotPasswordForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        submitted = True
        form = ForgotPasswordForm()

    return render(
        request,
        "accounts/forgot_password.html",
        {"form": form, "submitted": submitted},
    )


def faq_view(request):
    return render(request, "accounts/faq.html")


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

    if request.method == "POST":
        target_id = request.POST.get("nisit_id", "")
        new_role = request.POST.get("role", "")

        if new_role not in ROLES:
            error = "That is not a role we recognise."
        elif target_id == request.user.nisit_id:
            # Without this a department admin can demote themselves and lock
            # everyone out of the only page that can put the role back.
            error = "You cannot change your own role. Ask another Department admin."
        else:
            target = User.objects.filter(nisit_id=target_id).first()
            if target is None:
                error = "That account no longer exists."
            else:
                flags = ROLES[new_role]
                target.is_staff = flags["is_staff"]
                target.is_superuser = flags["is_superuser"]
                target.save(update_fields=["is_staff", "is_superuser"])
                notice = f"{target.get_full_name()} is now {flags['label']}."

    people = []
    for u in User.objects.all().order_by("nisit_id"):
        key = role_of(u)
        people.append({
            "user": u,
            "role": key,
            "role_label": ROLES[key]["label"],
            "is_me": u.nisit_id == request.user.nisit_id,
        })

    return render(request, "accounts/roles.html", {
        "people": people,
        "roles": [{"key": k, "label": v["label"]} for k, v in ROLES.items()],
        "notice": notice,
        "error": error,
    })
