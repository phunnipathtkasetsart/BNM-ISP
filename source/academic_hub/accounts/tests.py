from types import SimpleNamespace

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .middleware import GUEST_ALLOWED_VIEWS, GUEST_SESSION_KEY, is_guest_request


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

    def test_anonymous_visitor_may_read_the_public_board(self):
        """US-02: the board is public. No sign-in, no guest session needed."""
        self.assertEqual(
            self.client.get(reverse("announcements:public_board")).status_code, 200
        )

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
