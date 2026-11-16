"""Who can see the Shorts tab and open Create Short."""

from django.urls import reverse

from accounts.middleware import GUEST_SESSION_KEY
from shorts.tests.helpers import (
    ShortsTestCase,
)


class ShortsTabTests(ShortsTestCase):
    """Who sees the Shorts tab and the Create Short button."""

    def test_anonymous_user_is_sent_to_login(self) -> None:
        response = self.client.get(reverse("shorts:feed"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_guest_is_sent_to_public_board(self) -> None:
        self.client.force_login(self.student)
        session = self.client.session
        session[GUEST_SESSION_KEY] = True
        session.save()
        response = self.client.get(reverse("shorts:feed"))
        self.assertRedirects(
            response,
            reverse("announcements:public_board"),
            fetch_redirect_response=False,
        )

    def test_student_sees_tab_but_no_create_button(self) -> None:
        self.client.force_login(self.student)
        response = self.client.get(reverse("shorts:feed"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, 'aria-current="page">Shorts</a>', html=False
        )
        self.assertNotContains(response, "Create Short")
        self.assertNotContains(response, reverse("shorts:create"))

    def test_lecturer_and_department_see_create_button(self) -> None:
        for user in (self.lecturer, self.department):
            with self.subTest(user=user.nisit_id):
                self.client.force_login(user)
                response = self.client.get(reverse("shorts:feed"))
                self.assertContains(response, "Create Short")
                self.assertContains(response, reverse("shorts:create"))

    def test_student_cannot_open_create_page(self) -> None:
        self.client.force_login(self.student)
        response = self.client.get(reverse("shorts:create"), follow=True)
        self.assertRedirects(response, reverse("shorts:feed"))
        self.assertContains(
            response, "Only lecturers and the Department can create shorts."
        )

    def test_lecturer_sees_the_form(self) -> None:
        self.client.force_login(self.lecturer)
        response = self.client.get(reverse("shorts:create"))
        self.assertEqual(response.status_code, 200)
        for name in ("title", "description", "tags", "clip"):
            self.assertContains(response, f'name="{name}"')
        self.assertContains(response, 'enctype="multipart/form-data"')

    def test_shorts_tab_is_on_the_other_tab_bars(self) -> None:
        self.client.force_login(self.student)
        for name in (
            "announcements:public_board",
            "course:dashboard",
            "calendar:month",
        ):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertContains(
                    response, f'href="{reverse("shorts:feed")}"'
                )
