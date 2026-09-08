import json
import secrets
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.contrib import messages
from django.shortcuts import redirect, render
from django.db import IntegrityError, transaction
from django.views.decorators.http import require_POST

from .forms import ForgotPasswordForm, GoogleAccountForm, LoginForm, RegisterForm
from .middleware import (
    GUEST_ID_PREFIX,
    GUEST_LANDING_VIEW,
    GUEST_SESSION_KEY,
    GUEST_SESSION_SECONDS,
    is_guest_request,
)
from .models import User


def google_login(request):
    if request.user.is_authenticated:
        return redirect(GUEST_LANDING_VIEW)

    if not settings.GOOGLE_OAUTH_CLIENT_ID or not settings.GOOGLE_OAUTH_CLIENT_SECRET:
        messages.error(request, "Google sign-in is not configured yet.")
        return redirect("accounts:login")

    state = secrets.token_urlsafe(32)
    request.session["google_oauth_state"] = state
    query = urlencode(
        {
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "redirect_uri": settings.GOOGLE_OAUTH_REDIRECT_URI,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "online",
            "prompt": "select_account",
        }
    )
    return redirect(f"https://accounts.google.com/o/oauth2/v2/auth?{query}")


def google_callback(request):
    expected_state = request.session.pop("google_oauth_state", None)
    if not expected_state or not secrets.compare_digest(
        expected_state, request.GET.get("state", "")
    ):
        messages.error(request, "Google sign-in could not be verified. Please try again.")
        return redirect("accounts:login")

    if request.GET.get("error") or not request.GET.get("code"):
        messages.error(request, "Google sign-in was cancelled.")
        return redirect("accounts:login")

    try:
        token_request = Request(
            "https://oauth2.googleapis.com/token",
            data=urlencode(
                {
                    "code": request.GET["code"],
                    "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                    "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                    "redirect_uri": settings.GOOGLE_OAUTH_REDIRECT_URI,
                    "grant_type": "authorization_code",
                }
            ).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urlopen(token_request, timeout=10) as response:
            token_data = json.load(response)
        user_request = Request(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )
        with urlopen(user_request, timeout=10) as response:
            google_user = json.load(response)
    except (KeyError, HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        messages.error(request, "Google sign-in failed. Please try again.")
        return redirect("accounts:login")

    email = google_user.get("email", "").lower()
    if not google_user.get("email_verified") or not email.endswith("@ku.th"):
        messages.error(request, "Use a verified KU Google account to sign in.")
        return redirect("accounts:login")

    try:
        user = User.objects.get(email__iexact=email, is_active=True)
    except User.DoesNotExist:
        request.session["google_pending_account"] = {
            "email": email,
            "first_name": google_user.get("given_name", "").strip(),
            "last_name": google_user.get("family_name", "").strip(),
        }
        return redirect("accounts:google_register")
    except User.MultipleObjectsReturned:
        messages.error(request, "This KU email is linked to multiple accounts.")
        return redirect("accounts:login")

    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return redirect(GUEST_LANDING_VIEW)


def google_register(request):
    pending = request.session.get("google_pending_account")
    if request.user.is_authenticated:
        return redirect(GUEST_LANDING_VIEW)
    if not pending:
        messages.error(request, "Your Google sign-in session expired. Please try again.")
        return redirect("accounts:login")

    form = GoogleAccountForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    nisit_id=form.cleaned_data["nisit_id"],
                    email=pending["email"],
                    first_name=pending["first_name"],
                    last_name=pending["last_name"],
                    department=form.cleaned_data["department"],
                    password=None,
                )
        except IntegrityError:
            form.add_error("nisit_id", "This Nisit ID or email is already registered.")
        else:
            request.session.pop("google_pending_account", None)
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            return redirect(GUEST_LANDING_VIEW)

    return render(
        request,
        "accounts/google_register.html",
        {"form": form, "google_account": pending},
    )


def discard_guest_account(request):
    """Drop the throwaway row when a guest session ends.

    Returns the account so the caller can delete it after `login()` has
    replaced the session - reading request.user afterwards would give the
    new user, not the guest being left behind.
    """
    if not is_guest_request(request) or not request.user.is_authenticated:
        return None
    guest = request.user
    return guest if str(guest.nisit_id).startswith(GUEST_ID_PREFIX) else None


def login_view(request):
    # A guest is signed in as a generated account, so the usual
    # "already authenticated" bounce would trap them in guest mode with no
    # way to reach this form.
    if request.user.is_authenticated and not is_guest_request(request):
        return redirect(GUEST_LANDING_VIEW)

    error = None
    form = LoginForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        user = authenticate(
            request,
            nisit_id=form.cleaned_data["nisit_id"],
            password=form.cleaned_data["password"],
        )
        if user is not None:
            stale_guest = discard_guest_account(request)
            login(request, user)
            # Signing in ends guest mode. login() cycles the session key but
            # keeps its contents, so the flag would otherwise linger and
            # reappear as a restriction after the next logout.
            request.session.pop(GUEST_SESSION_KEY, None)
            if stale_guest is not None:
                stale_guest.delete()
            return redirect(GUEST_LANDING_VIEW)
        error = "Invalid Nisit ID or password."

    return render(request, "accounts/login.html", {"form": form, "error": error})


def _new_guest_id():
    """A free 10-character ID for a guest row.

    "G" plus nine digits fits `userID` exactly and cannot collide with a
    ten-digit nisit ID or a staff ID like A0001. Retried rather than assumed
    unique, because the primary key is the one thing that must not clash.
    """
    for _ in range(20):
        candidate = GUEST_ID_PREFIX + "".join(secrets.choice("0123456789") for _ in range(9))
        if not User.objects.filter(nisit_id=candidate).exists():
            return candidate
    raise RuntimeError("Could not allocate a guest ID after 20 attempts.")


def create_guest_account():
    """Generate a throwaway account inside the existing Users table.

    No schema change: every value fits the columns that are already there.
    The password is set unusable, so the row can never be signed into through
    the normal form - it is reachable only by pressing "Sign in as guest".
    """
    guest_id = _new_guest_id()
    user = User(
        nisit_id=guest_id,
        first_name="Guest",
        last_name=guest_id[1:],          # userLastName is varchar(20)
        email=f"{guest_id.lower()}@guest.local",   # unique, fits varchar(50)
        department="",
        is_staff=False,
        is_superuser=False,
        is_active=True,
    )
    user.set_unusable_password()
    user.save()
    return user


@require_POST
def guest_login_view(request):
    """Create a guest account and sign into it for a limited time."""
    if request.user.is_authenticated and not is_guest_request(request):
        return redirect(GUEST_LANDING_VIEW)

    request.session.pop("google_oauth_state", None)
    request.session.pop("google_pending_account", None)

    guest = create_guest_account()
    # No password was checked, so the backend has to be named explicitly.
    login(request, guest, backend="django.contrib.auth.backends.ModelBackend")

    request.session[GUEST_SESSION_KEY] = True
    # Per-session expiry: a real student's session is unaffected.
    request.session.set_expiry(GUEST_SESSION_SECONDS)
    return redirect(GUEST_LANDING_VIEW)


def register_view(request):
    # Same reason as login_view: a guest is authenticated, and must still be
    # able to create a real account.
    if request.user.is_authenticated and not is_guest_request(request):
        return redirect(GUEST_LANDING_VIEW)

    form = RegisterForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        stale_guest = discard_guest_account(request)
        user = form.save()
        login(request, user)
        request.session.pop(GUEST_SESSION_KEY, None)
        if stale_guest is not None:
            stale_guest.delete()
        return redirect(GUEST_LANDING_VIEW)

    return render(request, "accounts/register.html", {"form": form})


def logout_view(request):
    # Remove the generated row on the way out rather than leaving it for the
    # purge command - the session it belonged to is over either way.
    stale_guest = discard_guest_account(request)
    logout(request)
    if stale_guest is not None:
        stale_guest.delete()
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
