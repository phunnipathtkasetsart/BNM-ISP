import base64
import json
from email.message import EmailMessage
from email.policy import SMTP
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

from .models import GmailOAuthCredential

GMAIL_COMPOSE_SCOPE = "https://www.googleapis.com/auth/gmail.compose"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GMAIL_DRAFTS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"


class GmailIntegrationError(Exception):
    pass


class GmailAuthorizationError(GmailIntegrationError):
    pass


def _fernet():
    key = settings.GMAIL_TOKEN_ENCRYPTION_KEY
    if not key:
        raise GmailIntegrationError("Gmail token encryption is not configured.")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise GmailIntegrationError("Gmail token encryption key is invalid.") from exc


def encrypt_refresh_token(token):
    if not isinstance(token, str) or not token:
        raise GmailIntegrationError("Google did not provide a valid refresh token.")
    return _fernet().encrypt(token.encode("utf-8")).decode("ascii")


def decrypt_refresh_token(encrypted_token):
    try:
        if not isinstance(encrypted_token, str):
            raise InvalidToken
        return _fernet().decrypt(encrypted_token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeEncodeError, UnicodeDecodeError) as exc:
        raise GmailAuthorizationError(
            "The saved Gmail authorization cannot be decrypted. Reconnect Gmail."
        ) from exc


def _request_json(request, *, authorization_error_statuses=()):
    try:
        with urlopen(request, timeout=15) as response:
            data = json.load(response)
    except HTTPError as exc:
        if exc.code in authorization_error_statuses:
            raise GmailAuthorizationError(
                "Google authorization expired or was revoked. Reconnect Gmail."
            ) from exc
        raise GmailIntegrationError(
            "Google could not complete the Gmail request. Please try again."
        ) from exc
    except (URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GmailIntegrationError(
            "Google could not be reached. Please try again."
        ) from exc
    if not isinstance(data, dict):
        raise GmailIntegrationError("Google returned an invalid response.")
    return data


def exchange_authorization_code(code):
    body = urlencode({
        "code": code,
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
        "redirect_uri": settings.GOOGLE_GMAIL_OAUTH_REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode()
    return _request_json(Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    ), authorization_error_statuses=(400, 401))


def get_google_account(access_token):
    return _request_json(Request(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    ), authorization_error_statuses=(401, 403))


def refresh_access_token(refresh_token):
    body = urlencode({
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    token_data = _request_json(Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    ), authorization_error_statuses=(400, 401))
    access_token = token_data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise GmailIntegrationError("Google did not return an access token.")
    return access_token


def build_advisor_message(student, advisor):
    message = EmailMessage(policy=SMTP)
    message["From"] = student.email
    message["To"] = advisor.email
    message["Subject"] = f"Academic advising request - {student.get_full_name()}"
    message.set_content(
        f"Dear {advisor.get_full_name()},\n\n"
        "I would like to discuss an academic matter. Please let me know when "
        "you are available.\n\n"
        f"Thank you,\n{student.get_full_name()}\n"
        f"Nisit ID: {student.nisit_id}\n"
        f"Email: {student.email}"
    )
    return message


def _create_draft(access_token, student, advisor):
    raw_message = base64.urlsafe_b64encode(
        build_advisor_message(student, advisor).as_bytes()
    ).decode("ascii").rstrip("=")
    body = json.dumps({"message": {"raw": raw_message}}).encode()
    response = _request_json(Request(
        GMAIL_DRAFTS_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    ), authorization_error_statuses=(401, 403))
    message = response.get("message")
    message_id = message.get("id") if isinstance(message, dict) else None
    if not isinstance(message_id, str) or not message_id:
        raise GmailIntegrationError("Gmail did not return the created draft message ID.")
    return message_id


def create_advisor_draft(student, advisor, *, access_token=None):
    credential = GmailOAuthCredential.objects.filter(user=student).first()
    if access_token is None:
        if credential is None:
            raise GmailAuthorizationError("Connect your KU Gmail account to continue.")
        if credential.google_email.casefold() != student.email.casefold():
            raise GmailAuthorizationError(
                "The connected Google account does not match your student email."
            )
        refresh_token = decrypt_refresh_token(credential.encrypted_refresh_token)
        access_token = refresh_access_token(refresh_token)
    return _create_draft(access_token, student, advisor)
