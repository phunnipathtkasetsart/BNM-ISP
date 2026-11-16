import secrets
from datetime import timedelta

from django.contrib.auth import authenticate, login, logout
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..forms import LoginForm, RegisterForm
from ..middleware import (
    GUEST_ID_PREFIX,
    GUEST_LANDING_VIEW,
    GUEST_SESSION_KEY,
    GUEST_SESSION_SECONDS,
    is_guest_request,
)
from ..models import User


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
    # Per-session expiry, so a real student's session is unaffected.
    #
    # An absolute datetime, not a number of seconds. Django measures an
    # integer expiry from the last time the session was written, so any
    # request that touched the session pushed the deadline forward and a
    # guest could stay indefinitely by reloading. A datetime is fixed at
    # sign-in: thirty minutes for the account, whatever it does.
    request.session.set_expiry(
        timezone.now() + timedelta(seconds=GUEST_SESSION_SECONDS)
    )
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


def faq_view(request):
    return render(request, "accounts/faq.html")
