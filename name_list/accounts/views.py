from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .forms import ForgotPasswordForm, LoginForm, RegisterForm


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
