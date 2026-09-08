from types import SimpleNamespace
from unittest.mock import patch

from django.shortcuts import render
from django.test import Client, RequestFactory, SimpleTestCase, override_settings
from django.urls import reverse

from .middleware import GUEST_SESSION_KEY, is_guest_request
from .models import User
from .views import dashboard_view


@override_settings(
    SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies",
)
class GuestAccessTests(SimpleTestCase):
    def setUp(self):
        self.client = Client()

    def sign_in_as_guest(self):
        return self.client.post(reverse("accounts:guest_login"))

    def test_guest_sign_in_is_post_only_and_opens_dashboard(self):
        response = self.client.get(reverse("accounts:guest_login"))
        self.assertEqual(response.status_code, 405)

        response = self.sign_in_as_guest()
        self.assertRedirects(
            response,
            reverse("accounts:dashboard"),
            fetch_redirect_response=False,
        )
        self.assertTrue(self.client.session[GUEST_SESSION_KEY])

        response = self.client.get(reverse("accounts:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/dashboard.html")
        self.assertContains(response, "Guest access")
        self.assertContains(response, '<span class="topbar__role">Guest</span>', html=True)
        self.assertNotContains(response, "Manage roles")

    def test_anonymous_visitor_must_choose_guest_or_normal_login(self):
        response = self.client.get(reverse("accounts:dashboard"))
        expected = f'{reverse("accounts:login")}?next={reverse("accounts:dashboard")}'
        self.assertRedirects(response, expected, fetch_redirect_response=False)

    def test_guest_is_redirected_from_every_other_application_page(self):
        self.sign_in_as_guest()

        blocked_views = (
            "accounts:login",
            "accounts:roles",
            "accounts:register",
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
                    reverse("accounts:dashboard"),
                    fetch_redirect_response=False,
                )

    def test_guest_is_redirected_from_admin(self):
        self.sign_in_as_guest()
        response = self.client.get("/admin/")
        self.assertRedirects(
            response,
            reverse("accounts:dashboard"),
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
        response = self.client.get(reverse("accounts:dashboard"))
        self.assertEqual(response.status_code, 302)

    def test_announcements_page_has_been_removed(self):
        self.assertEqual(self.client.get("/accounts/announcements/").status_code, 404)

    def test_authenticated_roles_use_the_same_dashboard(self):
        for role, is_staff, is_superuser in (
            ("Student", False, False),
            ("Lecturer", True, False),
            ("Department", False, True),
        ):
            with self.subTest(role=role):
                request = RequestFactory().get(reverse("accounts:dashboard"))
                request.user = User(
                    nisit_id="6810545956", first_name="Test", last_name="User",
                    is_staff=is_staff, is_superuser=is_superuser,
                )
                request.session = {GUEST_SESSION_KEY: True}
                with patch("accounts.views.render", wraps=render) as render_mock:
                    response = dashboard_view(request)
                self.assertEqual(render_mock.call_args.args[1], "accounts/dashboard.html")
                self.assertContains(response, "Test User")
                self.assertContains(response, f'<span class="topbar__role">{role}</span>', html=True)
                self.assertNotContains(response, "Guest access")
                self.assertNotContains(response, "Announcements")
                self.assertEqual(b"Manage roles" in response.content, is_superuser)

    def test_authenticated_user_is_never_treated_as_guest(self):
        request = SimpleNamespace(
            user=SimpleNamespace(is_authenticated=True),
            session={GUEST_SESSION_KEY: True},
        )
        self.assertFalse(is_guest_request(request))
