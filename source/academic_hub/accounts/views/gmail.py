import secrets
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.views.decorators.http import require_GET

from ..gmail import (
    GMAIL_COMPOSE_SCOPE,
    GmailAuthorizationError,
    GmailIntegrationError,
    create_advisor_draft,
    decrypt_refresh_token,
    encrypt_refresh_token,
    exchange_authorization_code,
    get_google_account,
)
from ..models import AdvisorAssignment, GmailOAuthCredential


def _advisor_contact_assignment(user):
    if user.is_staff or user.is_superuser or not user.email.lower().endswith("@ku.th"):
        return None
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


@login_required
@require_GET
def gmail_oauth_start(request):
    assignment = _advisor_contact_assignment(request.user)
    if assignment is None:
        messages.error(request, "You do not have an assigned academic advisor.")
        return redirect("course:dashboard")
    credential = GmailOAuthCredential.objects.filter(user=request.user).first()
    if credential and credential.google_email.casefold() != request.user.email.casefold():
        credential.delete()
        credential = None

    if not settings.GOOGLE_OAUTH_CLIENT_ID or not settings.GOOGLE_OAUTH_CLIENT_SECRET:
        messages.error(request, "Gmail contact is not configured. Please contact your department.")
        return redirect("course:dashboard")
    if not settings.GMAIL_TOKEN_ENCRYPTION_KEY:
        messages.error(request, "Gmail contact is not configured. Please contact your department.")
        return redirect("course:dashboard")

    state = secrets.token_urlsafe(32)
    request.session["gmail_oauth_state"] = state
    request.session["gmail_oauth_user_id"] = request.user.pk
    request.session["gmail_oauth_advisor_id"] = assignment.advisor_id
    force_consent = request.session.pop("gmail_oauth_force_consent", False)
    query = urlencode({
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_GMAIL_OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": f"openid email profile {GMAIL_COMPOSE_SCOPE}",
        "state": state,
        "access_type": "offline",
        "prompt": "consent select_account" if not credential or force_consent else "none",
    })
    return redirect(f"https://accounts.google.com/o/oauth2/v2/auth?{query}")


@login_required
def gmail_oauth_callback(request):
    expected_state = request.session.pop("gmail_oauth_state", None)
    expected_user_id = request.session.pop("gmail_oauth_user_id", None)
    expected_advisor_id = request.session.pop("gmail_oauth_advisor_id", None)
    if (
        not expected_state
        or expected_user_id != request.user.pk
        or not secrets.compare_digest(expected_state, request.GET.get("state", ""))
    ):
        messages.error(request, "Gmail authorization could not be verified. Please try again.")
        return redirect("course:dashboard")
    if request.GET.get("error") in {
        "interaction_required",
        "login_required",
        "consent_required",
    }:
        request.session["gmail_oauth_force_consent"] = True
        return redirect("accounts:gmail_oauth_start")
    if request.GET.get("error") or not request.GET.get("code"):
        messages.error(request, "Gmail authorization was cancelled.")
        return redirect("course:dashboard")

    assignment = _advisor_contact_assignment(request.user)
    if assignment is None or assignment.advisor_id != expected_advisor_id:
        messages.error(request, "Your advisor assignment changed. Please try again.")
        return redirect("course:dashboard")

    try:
        token_data = exchange_authorization_code(request.GET["code"])
        access_token = token_data.get("access_token")
        granted_scopes = token_data.get("scope", "")
        if (
            not isinstance(access_token, str)
            or not access_token
            or not isinstance(granted_scopes, str)
            or GMAIL_COMPOSE_SCOPE not in granted_scopes.split()
        ):
            raise GmailAuthorizationError("Google did not grant Gmail draft access.")
        google_account = get_google_account(access_token)
        google_email = google_account.get("email", "")
        if (
            google_account.get("email_verified") is not True
            or not isinstance(google_email, str)
            or google_email.casefold() != request.user.email.casefold()
            or not google_email.casefold().endswith("@ku.th")
        ):
            messages.error(
                request,
                "Choose the verified KU Google account that matches your student email.",
            )
            return redirect("course:dashboard")

        credential = GmailOAuthCredential.objects.filter(user=request.user).first()
        refresh_token = token_data.get("refresh_token")
        if not refresh_token:
            if credential and credential.google_email.casefold() == google_email.casefold():
                refresh_token = decrypt_refresh_token(credential.encrypted_refresh_token)
            else:
                raise GmailAuthorizationError(
                    "Google did not provide offline access. Please authorize Gmail again."
                )
        if credential is None:
            credential = GmailOAuthCredential(user=request.user)
        credential.google_email = google_email
        credential.encrypted_refresh_token = encrypt_refresh_token(refresh_token)
        credential.save()
        draft_message_id = create_advisor_draft(
            request.user,
            assignment.advisor,
            access_token=access_token,
        )
    except GmailAuthorizationError:
        request.session["gmail_oauth_force_consent"] = True
        messages.error(request, "Gmail authorization expired or was incomplete. Please try again.")
        return redirect("accounts:gmail_oauth_start")
    except GmailIntegrationError:
        messages.error(request, "Gmail could not create your draft. Please try again later.")
        return redirect("course:dashboard")

    return redirect(
        "https://mail.google.com/mail/?"
        + urlencode({"authuser": request.user.email})
        + "#inbox?"
        + urlencode({"compose": draft_message_id})
    )
