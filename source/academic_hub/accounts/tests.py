from types import SimpleNamespace
import base64
import json
from email.parser import BytesParser
from email.policy import SMTP
from urllib.parse import parse_qs, urlencode, urlparse
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.core import mail
from django.db import IntegrityError, transaction
from django.utils import timezone
from datetime import timedelta
import hashlib
from cryptography.fernet import Fernet

from .middleware import GUEST_ALLOWED_VIEWS, GUEST_SESSION_KEY, is_guest_request
from .forms import LoginForm, RegisterForm, ResetPasswordForm
from .gmail import _create_draft, decrypt_refresh_token, encrypt_refresh_token
from .models import AdvisorAssignment, GmailOAuthCredential, PasswordResetToken, User


@override_settings(
    SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies",
)
class GuestAccessTests(TestCase):
    def setUp(self):
        self.client = Client()

    def sign_in_as_guest(self):
        return self.client.post(reverse("accounts:guest_login"))

    def test_guest_sign_in_is_post_only_and_opens_the_public_board(self):
        response = self.client.get(reverse("accounts:guest_login"))
        self.assertEqual(response.status_code, 405)

        response = self.sign_in_as_guest()
        self.assertRedirects(
            response,
            reverse("announcements:public_board"),
            fetch_redirect_response=False,
        )
        self.assertTrue(self.client.session[GUEST_SESSION_KEY])

        # The board itself is not fetched here: it queries announcements and
        # FAQs, and these tests run without a database on purpose - `Users` is
        # managed=False in a separate schema, so Django cannot build a test
        # database for it. What matters for this change is the destination,
        # and that the middleware now treats the board as allowed.
        self.assertIn("announcements:public_board", GUEST_ALLOWED_VIEWS)

    def test_dashboard_no_longer_exists(self):
        """The dashboard was an empty page behind the same bar as the board.

        Everyone now lands on the board instead, so the route is gone rather
        than left as a redirect nobody would notice was dead.
        """
        self.assertEqual(self.client.get("/accounts/dashboard/").status_code, 404)

    def test_anonymous_visitor_is_sent_to_sign_in(self):
        """The board sits behind sign-in or the guest button.

        US-02 is still met: a visitor without an account reads the board by
        pressing "Sign in as guest". What is blocked is arriving there without
        having chosen either, which used to happen from the KU logo.
        """
        response = self.client.get(reverse("announcements:public_board"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_guest_is_redirected_from_every_other_application_page(self):
        self.sign_in_as_guest()

        blocked_views = (
            "accounts:roles",
            "accounts:forgot_password",
            "accounts:faq",
            "accounts:google_login",
            "accounts:google_register",
            "accounts:google_callback",
        )
        for view_name in blocked_views:
            with self.subTest(view_name=view_name):
                response = self.client.get(reverse(view_name))
                self.assertRedirects(
                    response,
                    reverse("announcements:public_board"),
                    fetch_redirect_response=False,
                )

    def test_guest_can_reach_sign_in_and_register(self):
        """A guest must have a way out of guest mode.

        Blocking these made the "Sign in" control in the bar bounce back to
        the board, stranding a guest with no route to a real account.
        """
        self.sign_in_as_guest()
        for view_name in ("accounts:login", "accounts:register"):
            with self.subTest(view_name=view_name):
                response = self.client.get(reverse(view_name))
                self.assertEqual(response.status_code, 200)

    def test_guest_is_redirected_from_admin(self):
        self.sign_in_as_guest()
        response = self.client.get("/admin/")
        self.assertRedirects(
            response,
            reverse("announcements:public_board"),
            fetch_redirect_response=False,
        )

    def test_logout_clears_guest_session(self):
        self.sign_in_as_guest()
        response = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(
            response,
            reverse("accounts:login"),
            fetch_redirect_response=False,
        )
        self.assertNotIn(GUEST_SESSION_KEY, self.client.session)
        self.assertEqual(self.client.get(reverse("accounts:login")).status_code, 200)

    def test_announcements_page_has_been_removed(self):
        self.assertEqual(self.client.get("/accounts/announcements/").status_code, 404)

    def test_guest_flag_is_what_marks_a_guest_not_authentication(self):
        """A guest is now signed in as a generated account.

        Before, guests were anonymous and `is_authenticated` could tell them
        apart. It cannot any more, so the session flag is the only marker.
        """
        self.assertTrue(is_guest_request(
            SimpleNamespace(session={GUEST_SESSION_KEY: True})
        ))
        self.assertFalse(is_guest_request(SimpleNamespace(session={})))

    def test_guest_account_is_generated_and_removed_on_logout(self):
        from .middleware import GUEST_ID_PREFIX
        from .models import User

        self.sign_in_as_guest()
        guests = User.objects.filter(nisit_id__startswith=GUEST_ID_PREFIX)
        self.assertEqual(guests.count(), 1)

        guest = guests.first()
        # Everything fits the existing columns - no schema change was needed.
        self.assertEqual(len(guest.nisit_id), 10)
        self.assertEqual(guest.first_name, "Guest")
        self.assertFalse(guest.is_staff)
        self.assertFalse(guest.is_superuser)
        self.assertFalse(guest.has_usable_password())

        self.client.post(reverse("accounts:logout"))
        self.assertEqual(
            User.objects.filter(nisit_id__startswith=GUEST_ID_PREFIX).count(), 0
        )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PasswordResetTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            nisit_id="6810545956", email="student@ku.th", password="old-pass-1234",
            first_name="Student", last_name="Tester", department="ske",
        )
        self.forgot_url = reverse("accounts:forgot_password")
        self.reset_url = reverse("accounts:reset_password")

    def request_reset(self, email="student@ku.th"):
        return self.client.post(self.forgot_url, {"email": email})

    def test_unknown_email_gets_same_confirmation_without_sending(self):
        known = self.request_reset()
        self.assertContains(known, "If an account exists for that email")
        self.assertEqual(len(mail.outbox), 1)
        mail.outbox.clear()

        unknown = self.request_reset("nobody@ku.th")
        self.assertContains(unknown, "If an account exists for that email")
        self.assertEqual(len(mail.outbox), 0)

    def test_invalid_email_is_rejected(self):
        response = self.request_reset("not-an-email")
        self.assertContains(response, "Use your KU address")
        self.assertEqual(len(mail.outbox), 0)

    def test_token_is_hashed_and_expires(self):
        self.request_reset()
        token = PasswordResetToken.objects.get(user=self.user)
        raw_token = mail.outbox[0].body.split("token=")[1].split()[0]
        self.assertNotEqual(token.token_hash, raw_token)
        self.assertEqual(token.token_hash, hashlib.sha256(raw_token.encode()).hexdigest())

        token.expires_at = timezone.now() - timedelta(minutes=1)
        token.save(update_fields=["expires_at"])
        response = self.client.get(self.reset_url, {"token": raw_token})
        self.assertContains(response, "invalid or has expired")

    def test_successful_reset_hashes_password_and_consumes_token(self):
        self.request_reset()
        raw_token = mail.outbox[0].body.split("token=")[1].split()[0]
        response = self.client.post(self.reset_url, {
            "token": raw_token, "password1": "new-secure-pass-123", "password2": "new-secure-pass-123",
        })
        self.assertContains(response, "reset successfully")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("new-secure-pass-123"))
        self.assertNotEqual(self.user.password, "new-secure-pass-123")
        reset_token = PasswordResetToken.objects.get(user=self.user)
        self.assertIsNotNone(reset_token.used_at)

        reused = self.client.post(self.reset_url, {
            "token": raw_token, "password1": "another-pass-123", "password2": "another-pass-123",
        })
        self.assertContains(reused, "invalid or has expired")

    def test_weak_and_mismatched_passwords_are_rejected(self):
        self.request_reset()
        raw_token = mail.outbox[0].body.split("token=")[1].split()[0]
        response = self.client.post(self.reset_url, {
            "token": raw_token, "password1": "short", "password2": "different",
        })
        self.assertContains(response, "at least 8 characters")
        self.assertEqual(PasswordResetToken.objects.get(user=self.user).used_at, None)


class RegistrationPasswordValidationTests(TestCase):
    def test_common_password_is_rejected_during_registration(self):
        form = RegisterForm(data={
            "first_name": "New",
            "last_name": "Student",
            "nisit_id": "6810545957",
            "department": "ske",
            "email": "newstudent@ku.th",
            "password1": "password",
        })

        self.assertFalse(form.is_valid())
        self.assertIn("This password is too common.", form.errors["password1"])


class PasswordWhitespaceTests(TestCase):
    """A password is whatever the user typed, spaces included.

    forms.CharField trims by default, so every password field silently
    rewrote what was entered. A password chosen as "  open sesame  " was
    stored as "open sesame", and nothing told the user.
    """

    PADDED = "  open sesame  "

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            nisit_id="7600000001", email="pad@ku.th", password=cls.PADDED,
            first_name="Pad", last_name="S", department="ske",
        )

    def test_login_field_keeps_the_spaces(self):
        form = LoginForm(data={"nisit_id": "7600000001", "password": self.PADDED})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["password"], self.PADDED)

    def test_register_field_keeps_the_spaces(self):
        self.assertFalse(RegisterForm().fields["password1"].strip)

    def test_reset_fields_keep_the_spaces(self):
        form = ResetPasswordForm()
        self.assertFalse(form.fields["password1"].strip)
        self.assertFalse(form.fields["password2"].strip)

    def test_signing_in_with_the_padded_password_works(self):
        client = Client()
        response = client.post(
            reverse("accounts:login"),
            {"nisit_id": "7600000001", "password": self.PADDED},
        )
        self.assertEqual(response.status_code, 302)

    def test_signing_in_with_the_trimmed_password_is_refused(self):
        client = Client()
        response = client.post(
            reverse("accounts:login"),
            {"nisit_id": "7600000001", "password": self.PADDED.strip()},
        )
        self.assertEqual(response.status_code, 200)


class GuestTimerTests(TestCase):
    """Thirty minutes belongs to the guest account, not to the last request.

    set_expiry() with an integer is measured from the last time the session
    was written, so reloading moved the deadline and a guest could stay for
    as long as they kept clicking.
    """

    def start_guest(self):
        client = Client()
        client.post(reverse("accounts:guest_login"))
        return client

    def test_expiry_is_about_thirty_minutes_from_sign_in(self):
        client = self.start_guest()
        left = client.session.get_expiry_date() - timezone.now()
        self.assertLess(abs(left - timedelta(minutes=30)), timedelta(seconds=30))

    def test_reloading_the_board_does_not_move_the_deadline(self):
        client = self.start_guest()
        before = client.session.get_expiry_date()
        for _ in range(3):
            client.get(reverse("announcements:public_board"))
        self.assertEqual(client.session.get_expiry_date(), before)

    def test_moving_between_pages_does_not_move_the_deadline(self):
        client = self.start_guest()
        before = client.session.get_expiry_date()
        client.get(reverse("announcements:public_board"))
        client.get(reverse("announcements:faq_board"))
        client.get(reverse("announcements:public_board"))
        self.assertEqual(client.session.get_expiry_date(), before)


class AdvisorAssignmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.department = User.objects.create_user(
            nisit_id="7700000001",
            email="advisor-department@ku.th",
            password="test-password",
            first_name="Department",
            last_name="Admin",
            department="ske",
            is_superuser=True,
        )
        cls.student = User.objects.create_user(
            nisit_id="7700000002",
            email="advisor-student@ku.th",
            password="test-password",
            first_name="Test",
            last_name="Student",
            department="ske",
        )
        cls.other_student = User.objects.create_user(
            nisit_id="7700000003",
            email="advisor-student2@ku.th",
            password="test-password",
            first_name="Other",
            last_name="Student",
            department="ske",
        )
        cls.lecturer = User.objects.create_user(
            nisit_id="7700000004",
            email="advisor-lecturer@ku.th",
            password="test-password",
            first_name="Lecturer",
            last_name="One",
            department="ske",
            is_staff=True,
        )
        cls.other_lecturer = User.objects.create_user(
            nisit_id="7700000005",
            email="advisor-lecturer2@ku.th",
            password="test-password",
            first_name="Lecturer",
            last_name="Two",
            department="ske",
            is_staff=True,
        )

    def setUp(self):
        self.client.force_login(self.department)
        self.roles_url = reverse("accounts:roles")

    def assign(self, advisor_id, student_ids=(), student_ids_text=""):
        return self.client.post(self.roles_url, {
            "action": "advisor",
            "nisit_id": advisor_id,
            "student_ids": student_ids,
            "student_ids_text": student_ids_text,
        })

    def test_department_can_assign_students_by_selection_and_student_ids(self):
        page = self.client.get(self.roles_url)
        self.assertEqual(
            [row["user"] for row in page.context["students"]],
            [self.student, self.other_student],
        )
        self.assertContains(page, "Assign advised students")

        self.assign(
            self.lecturer.nisit_id,
            student_ids=[self.student.nisit_id],
            student_ids_text=self.other_student.nisit_id,
        )
        assignment = AdvisorAssignment.objects.get(student=self.student)
        self.assertEqual(assignment.advisor, self.lecturer)
        self.assertEqual(
            AdvisorAssignment.objects.get(student=self.other_student).advisor,
            self.lecturer,
        )

        self.assign(self.lecturer.nisit_id, student_ids=[self.student.nisit_id])
        self.assertEqual(AdvisorAssignment.objects.filter(advisor=self.lecturer).count(), 1)
        self.assertFalse(AdvisorAssignment.objects.filter(student=self.other_student).exists())

        self.assign(self.other_lecturer.nisit_id, student_ids_text=self.student.nisit_id)
        self.assertEqual(
            AdvisorAssignment.objects.get(student=self.student).advisor,
            self.other_lecturer,
        )

        response = self.assign(self.other_lecturer.nisit_id)
        self.assertContains(response, "0 advised students saved")
        self.assertFalse(AdvisorAssignment.objects.filter(student=self.student).exists())

    def test_non_department_users_cannot_assign_and_non_students_are_rejected(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(self.roles_url).status_code, 403)

        self.client.force_login(self.department)
        response = self.assign(
            self.lecturer.nisit_id,
            student_ids=[self.other_lecturer.nisit_id],
        )
        self.assertContains(response, "unknown, inactive, or not student accounts")
        self.assertFalse(AdvisorAssignment.objects.filter(student=self.student).exists())

    def test_invalid_student_id_does_not_change_existing_assignments(self):
        AdvisorAssignment.objects.create(student=self.student, advisor=self.lecturer)
        response = self.assign(
            self.lecturer.nisit_id,
            student_ids=[self.other_student.nisit_id],
            student_ids_text="9999999999",
        )
        self.assertContains(response, "9999999999")
        self.assertEqual(
            list(AdvisorAssignment.objects.filter(advisor=self.lecturer).values_list("student_id", flat=True)),
            [self.student.nisit_id],
        )

    def test_role_changes_are_saved_together_with_one_submit(self):
        response = self.client.post(self.roles_url, {
            "action": "roles",
            f"role_for_{self.student.nisit_id}": "lecturer",
            f"role_for_{self.other_student.nisit_id}": "department",
        })
        self.assertContains(response, "Role changes saved for 2 accounts.")
        self.student.refresh_from_db()
        self.other_student.refresh_from_db()
        self.assertTrue(self.student.is_staff)
        self.assertTrue(self.other_student.is_superuser)

    def test_bulk_role_changes_reject_invalid_submissions_without_partial_updates(self):
        response = self.client.post(self.roles_url, {
            "action": "roles",
            f"role_for_{self.student.nisit_id}": "lecturer",
            f"role_for_{self.other_student.nisit_id}": "not-a-role",
        })
        self.assertContains(response, "not a role we recognise")
        self.student.refresh_from_db()
        self.assertFalse(self.student.is_staff)

    def test_roles_page_has_one_save_button_for_role_changes(self):
        response = self.client.get(self.roles_url)
        self.assertContains(response, "Save changes")
        self.assertNotContains(response, ">Save<", html=False)
        self.assertContains(response, "Assign students")

    def test_assignments_are_cleared_when_a_student_or_advisor_changes_role(self):
        self.assign(self.lecturer.nisit_id, student_ids=[self.student.nisit_id])
        self.client.post(self.roles_url, {
            "action": "role",
            "nisit_id": self.student.nisit_id,
            "role": "lecturer",
        })
        self.assertFalse(AdvisorAssignment.objects.filter(student=self.student).exists())

        self.assign(self.lecturer.nisit_id, student_ids=[self.other_student.nisit_id])
        self.client.post(self.roles_url, {
            "action": "role",
            "nisit_id": self.lecturer.nisit_id,
            "role": "student",
        })
        self.assertFalse(AdvisorAssignment.objects.filter(advisor=self.lecturer).exists())

    def test_student_can_have_only_one_advisor_assignment(self):
        AdvisorAssignment.objects.create(student=self.student, advisor=self.lecturer)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AdvisorAssignment.objects.create(
                student=self.student,
                advisor=self.other_lecturer,
            )


@override_settings(
    GOOGLE_OAUTH_CLIENT_ID="test-client",
    GOOGLE_OAUTH_CLIENT_SECRET="test-secret",
    GMAIL_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode("ascii"),
)
class GmailDraftFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.student = User.objects.create_user(
            nisit_id="7700000011",
            email="gmail-student@ku.th",
            password="test-password",
            first_name="Gmail",
            last_name="Student",
            department="ske",
        )
        cls.advisor = User.objects.create_user(
            nisit_id="7700000012",
            email="gmail-advisor@ku.th",
            password="test-password",
            first_name="Gmail",
            last_name="Advisor",
            department="ske",
            is_staff=True,
        )

    def setUp(self):
        self.client.force_login(self.student)
        self.assignment = AdvisorAssignment.objects.create(
            student=self.student,
            advisor=self.advisor,
        )

    def test_refresh_token_is_encrypted_at_rest(self):
        token = "google-refresh-token"
        encrypted = encrypt_refresh_token(token)

        self.assertNotIn(token, encrypted)
        self.assertEqual(decrypt_refresh_token(encrypted), token)

    def test_contact_with_existing_authorization_uses_silent_oauth(self):
        GmailOAuthCredential.objects.create(
            user=self.student,
            google_email=self.student.email,
            encrypted_refresh_token=encrypt_refresh_token("refresh-token"),
        )
        response = self.client.get(
            reverse("accounts:gmail_oauth_start"),
            HTTP_ORIGIN="null",
        )
        query = parse_qs(urlparse(response["Location"]).query)

        self.assertEqual(urlparse(response["Location"]).netloc, "accounts.google.com")
        self.assertEqual(query["prompt"], ["none"])

    def test_first_contact_requests_gmail_compose_scope(self):
        response = self.client.get(reverse("accounts:gmail_oauth_start"))
        query = parse_qs(urlparse(response["Location"]).query)

        self.assertEqual(urlparse(response["Location"]).netloc, "accounts.google.com")
        self.assertIn("https://www.googleapis.com/auth/gmail.compose", query["scope"][0])
        self.assertEqual(query["access_type"], ["offline"])
        self.assertIn("consent", query["prompt"][0])

    def test_gmail_oauth_start_is_a_get_navigation(self):
        response = self.client.post(reverse("accounts:gmail_oauth_start"))
        self.assertEqual(response.status_code, 405)

    def test_silent_oauth_falls_back_to_consent_when_google_requires_interaction(self):
        GmailOAuthCredential.objects.create(
            user=self.student,
            google_email=self.student.email,
            encrypted_refresh_token=encrypt_refresh_token("refresh-token"),
        )
        self.client.get(reverse("accounts:gmail_oauth_start"))
        state = self.client.session["gmail_oauth_state"]

        response = self.client.get(
            reverse("accounts:gmail_oauth_callback"),
            {"state": state, "error": "interaction_required"},
        )
        self.assertRedirects(
            response,
            reverse("accounts:gmail_oauth_start"),
            fetch_redirect_response=False,
        )

        response = self.client.get(reverse("accounts:gmail_oauth_start"))
        query = parse_qs(urlparse(response["Location"]).query)
        self.assertIn("consent", query["prompt"][0])

    def test_callback_matches_google_identity_and_stores_encrypted_token(self):
        state = "oauth-state"
        session = self.client.session
        session["gmail_oauth_state"] = state
        session["gmail_oauth_user_id"] = self.student.pk
        session["gmail_oauth_advisor_id"] = self.advisor.pk
        session.save()

        with (
            patch("accounts.views.exchange_authorization_code", return_value={
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "scope": "openid email profile https://www.googleapis.com/auth/gmail.compose",
            }),
            patch("accounts.views.get_google_account", return_value={
                "email": self.student.email,
                "email_verified": True,
            }),
            patch("accounts.views.create_advisor_draft") as create_draft,
        ):
            response = self.client.get(
                reverse("accounts:gmail_oauth_callback"),
                {"state": state, "code": "authorization-code"},
            )

        self.assertEqual(
            response["Location"],
            "https://mail.google.com/mail/?authuser="
            + urlencode({"authuser": self.student.email}).split("=", 1)[1]
            + "#drafts",
        )
        credential = GmailOAuthCredential.objects.get(user=self.student)
        self.assertEqual(credential.google_email, self.student.email)
        self.assertNotIn("refresh-token", credential.encrypted_refresh_token)
        self.assertEqual(
            decrypt_refresh_token(credential.encrypted_refresh_token),
            "refresh-token",
        )
        create_draft.assert_called_once_with(
            self.student,
            self.advisor,
            access_token="access-token",
        )

    def test_callback_rejects_a_different_google_account(self):
        state = "oauth-state"
        session = self.client.session
        session["gmail_oauth_state"] = state
        session["gmail_oauth_user_id"] = self.student.pk
        session["gmail_oauth_advisor_id"] = self.advisor.pk
        session.save()

        with (
            patch("accounts.views.exchange_authorization_code", return_value={
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "scope": "https://www.googleapis.com/auth/gmail.compose",
            }),
            patch("accounts.views.get_google_account", return_value={
                "email": "someone-else@ku.th",
                "email_verified": True,
            }),
        ):
            response = self.client.get(
                reverse("accounts:gmail_oauth_callback"),
                {"state": state, "code": "authorization-code"},
            )

        self.assertRedirects(response, reverse("course:dashboard"), fetch_redirect_response=False)
        self.assertFalse(GmailOAuthCredential.objects.filter(user=self.student).exists())

    def test_draft_api_payload_uses_student_and_advisor_addresses(self):
        with patch("accounts.gmail._request_json", return_value={"id": "draft-id"}) as request:
            draft_id = _create_draft("access-token", self.student, self.advisor)

        self.assertEqual(draft_id, "draft-id")
        request_data = request.call_args.args[0]
        payload = json.loads(request_data.data)
        raw = payload["message"]["raw"]
        raw_bytes = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        message = BytesParser(policy=SMTP).parsebytes(raw_bytes)
        self.assertEqual(message["From"], self.student.email)
        self.assertEqual(message["To"], self.advisor.email)
        self.assertIn(self.student.get_full_name(), message["Subject"])
        self.assertIn(self.student.nisit_id, message.get_content())
