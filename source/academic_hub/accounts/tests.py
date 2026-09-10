from types import SimpleNamespace

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.core import mail
from django.utils import timezone
from datetime import timedelta
import hashlib

from .middleware import GUEST_ALLOWED_VIEWS, GUEST_SESSION_KEY, is_guest_request
from .forms import RegisterForm
from .models import PasswordResetToken, User


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
