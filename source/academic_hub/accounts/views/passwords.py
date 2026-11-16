import hashlib
import logging
import secrets

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import transaction
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from ..forms import ForgotPasswordForm, ResetPasswordForm
from ..models import PasswordResetToken, User


logger = logging.getLogger(__name__)


def forgot_password_view(request):
    """Request a reset without revealing whether an account exists."""
    submitted = False
    form = ForgotPasswordForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        submitted = True
        email = form.cleaned_data["email"]
        throttle_key = "password-reset:" + hashlib.sha256(
            f"{request.META.get('REMOTE_ADDR', '')}:{email}".encode()
        ).hexdigest()
        if cache.get(throttle_key, 0) < settings.PASSWORD_RESET_REQUEST_LIMIT:
            cache.set(throttle_key, cache.get(throttle_key, 0) + 1, settings.PASSWORD_RESET_REQUEST_WINDOW)
            user = User.objects.filter(email__iexact=email, is_active=True).first()
            if user is not None:
                raw_token = secrets.token_urlsafe(32)
                PasswordResetToken.objects.filter(user=user, used_at__isnull=True).delete()
                reset_token = PasswordResetToken.objects.create(
                    user=user,
                    token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
                    expires_at=timezone.now() + settings.PASSWORD_RESET_TIMEOUT,
                )
                reset_path = reverse("accounts:reset_password") + f"?token={raw_token}"
                reset_url = (
                    f"{settings.PASSWORD_RESET_BASE_URL}{reset_path}"
                    if settings.PASSWORD_RESET_BASE_URL
                    else request.build_absolute_uri(reset_path)
                )
                try:
                    send_mail(
                        "Reset Your Password",
                        "We received a request to reset the password for your account.\n"
                        "Please click the link below to create a new password:\n\n"
                        f"{reset_url}\n\n"
                        f"This password reset link will expire in {settings.PASSWORD_RESET_TIMEOUT.total_seconds() // 60:.0f} minutes for security purposes.\n"
                        "If you did not request a password reset, you can safely ignore this email. Your account will remain secure.\n\n"
                        "BNM Support Team\n"
                        "Kasetsart University",
                        settings.DEFAULT_FROM_EMAIL,
                        [user.email],
                        fail_silently=False,
                    )
                except Exception:
                    logger.exception("Password reset email could not be sent")
                    reset_token.delete()
        form = ForgotPasswordForm()

    return render(
        request,
        "accounts/forgot_password.html",
        {"form": form, "submitted": submitted},
    )


def reset_password_view(request):
    raw_token = request.POST.get("token", "") if request.method == "POST" else request.GET.get("token", "")
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest() if raw_token else ""
    reset_token = PasswordResetToken.objects.select_related("user").filter(token_hash=token_hash).first()
    valid = reset_token is not None and reset_token.is_valid()
    form = ResetPasswordForm(request.POST or None, user=reset_token.user if valid else None)

    if request.method == "POST" and valid and form.is_valid():
        with transaction.atomic():
            reset_token = PasswordResetToken.objects.select_for_update().select_related("user").get(pk=reset_token.pk)
            if not reset_token.is_valid():
                valid = False
            else:
                reset_token.user.set_password(form.cleaned_data["password1"])
                reset_token.user.save(update_fields=["password"])
                reset_token.used_at = timezone.now()
                reset_token.save(update_fields=["used_at"])
                return render(request, "accounts/reset_password.html", {"success": True})

    return render(
        request,
        "accounts/reset_password.html",
        {"form": form, "valid": valid, "token": raw_token},
    )
