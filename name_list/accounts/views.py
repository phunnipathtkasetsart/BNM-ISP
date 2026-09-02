import json
import secrets
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import redirect, render
from django.db import IntegrityError, transaction

from .forms import ForgotPasswordForm, GoogleAccountForm, LoginForm, RegisterForm
from .models import User


def google_login(request):
    if request.user.is_authenticated:
        return redirect("accounts:dashboard")

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
    return redirect("accounts:dashboard")


def google_register(request):
    pending = request.session.get("google_pending_account")
    if request.user.is_authenticated:
        return redirect("accounts:dashboard")
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
            return redirect("accounts:dashboard")

    return render(
        request,
        "accounts/google_register.html",
        {"form": form, "google_account": pending},
    )


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
