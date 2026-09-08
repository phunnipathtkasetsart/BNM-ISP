from types import SimpleNamespace

from django.test import Client, SimpleTestCase, override_settings
from django.urls import reverse

from .middleware import GUEST_SESSION_KEY, is_guest_request


@override_settings(
    SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies",
)
class GuestAccessTests(SimpleTestCase):
    def setUp(self):
        self.client = Client()

    def sign_in_as_guest(self):
        return self.client.post(reverse("accounts:guest_login"))

    def test_guest_sign_in_is_post_only_and_opens_announcements(self):
        response = self.client.get(reverse("accounts:guest_login"))
        self.assertEqual(response.status_code, 405)

        response = self.sign_in_as_guest()
        self.assertRedirects(
            response,
            reverse("accounts:announcements"),
            fetch_redirect_response=False,
        )
        self.assertTrue(self.client.session[GUEST_SESSION_KEY])

        response = self.client.get(reverse("accounts:announcements"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Guest access")

    def test_anonymous_visitor_must_choose_guest_or_normal_login(self):
        response = self.client.get(reverse("accounts:announcements"))
        expected = f'{reverse("accounts:login")}?next={reverse("accounts:announcements")}'
        self.assertRedirects(response, expected, fetch_redirect_response=False)

    def test_guest_is_redirected_from_every_other_application_page(self):
        self.sign_in_as_guest()

        blocked_views = (
            "accounts:dashboard",
            "accounts:roles",
            "accounts:register",
            "accounts:forgot_password",
            "accounts:faq",
            "accounts:google_login",
            "accounts:google_register",
        )
        for view_name in blocked_views:
            with self.subTest(view_name=view_name):
                response = self.client.get(reverse(view_name))
                self.assertRedirects(
                    response,
                    reverse("accounts:announcements"),
                    fetch_redirect_response=False,
                )

    def test_guest_is_redirected_from_admin(self):
        self.sign_in_as_guest()
        response = self.client.get("/admin/")
        self.assertRedirects(
            response,
            reverse("accounts:announcements"),
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

    def test_authenticated_user_is_never_treated_as_guest(self):
        request = SimpleNamespace(
            user=SimpleNamespace(is_authenticated=True),
            session={GUEST_SESSION_KEY: True},
        )
        self.assertFalse(is_guest_request(request))
