"""Edit Short: listing, editing and deleting shorts."""

from unittest.mock import patch

from django.urls import reverse

from accounts.models import User as Account
from announcements.models import Tag
from shorts.models import Short
from shorts.services import update_short
from shorts.tests.helpers import (
    ShortsTestCase,
    clip,
)


class EditShortTests(ShortsTestCase):
    """Edit Short: lecturers manage their own shorts; the Department
    manages all of them."""

    other_lecturer: Account

    @classmethod
    def setUpTestData(cls) -> None:
        super().setUpTestData()
        cls.other_lecturer = Account.objects.create_user(
            nisit_id="6610540204",
            email="short.other@ku.th",
            password="x",
            first_name="Other",
            last_name="Lecturer",
            department="cpe",
            is_staff=True,
        )

    def setUp(self) -> None:
        super().setUp()
        self.mine = self.make_short("Mine", tags=["recursion"])
        self.theirs = self.make_short("Theirs")
        Short.objects.filter(pk=self.theirs.pk).update(
            author=self.other_lecturer
        )

    # --- the two buttons ---
    def test_staff_see_create_then_edit_in_the_tab_bar(self) -> None:
        self.client.force_login(self.lecturer)
        html = self.client.get(reverse("shorts:feed")).content.decode()
        create_at = html.index(f'href="{reverse("shorts:create")}"')
        edit_at = html.index(f'href="{reverse("shorts:manage")}"')
        self.assertLess(create_at, edit_at)  # Create Short sits to the left
        self.assertIn("Edit Short", html)

    def test_students_see_neither_button_and_cannot_manage(self) -> None:
        self.client.force_login(self.student)
        self.assertNotContains(
            self.client.get(reverse("shorts:feed")), "Edit Short"
        )
        for url in (
            reverse("shorts:manage"),
            reverse("shorts:edit", args=[self.mine.pk]),
            reverse("shorts:delete", args=[self.mine.pk]),
        ):
            response = self.client.get(url)
            self.assertRedirects(
                response, reverse("shorts:feed"), fetch_redirect_response=False
            )
        self.client.post(reverse("shorts:delete", args=[self.mine.pk]))
        self.assertTrue(Short.objects.filter(pk=self.mine.pk).exists())

    # --- the list ---
    def test_lecturer_lists_only_their_own_shorts(self) -> None:
        self.client.force_login(self.lecturer)
        response = self.client.get(reverse("shorts:manage"))
        self.assertContains(response, "My shorts")
        titles = [s.title for s in response.context["page_obj"]]
        self.assertEqual(titles, ["Mine"])

    def test_department_lists_every_short_with_its_author(self) -> None:
        self.client.force_login(self.department)
        response = self.client.get(reverse("shorts:manage"))
        self.assertContains(response, "All shorts")
        self.assertEqual(
            {s.title for s in response.context["page_obj"]}, {"Mine", "Theirs"}
        )
        self.assertContains(response, "Other Lecturer")

    def test_empty_list_points_to_create(self) -> None:
        self.client.force_login(self.other_lecturer)
        Short.objects.filter(author=self.other_lecturer).delete()
        response = self.client.get(reverse("shorts:manage"))
        self.assertContains(response, "You have not uploaded any shorts yet.")

    # --- editing ---
    def test_edit_form_is_filled_in_and_clip_is_optional(self) -> None:
        self.client.force_login(self.lecturer)
        response = self.client.get(reverse("shorts:edit", args=[self.mine.pk]))
        form = response.context["form"]
        self.assertEqual(form.initial["title"], "Mine")
        self.assertEqual(form.initial["tags"], "recursion")
        self.assertFalse(form.fields["clip"].required)
        self.assertContains(response, "Current clip")

    def test_edit_details_and_keep_the_clip(self) -> None:
        old_name = self.mine.clip.name
        self.client.force_login(self.lecturer)
        response = self.client.post(
            reverse("shorts:edit", args=[self.mine.pk]),
            {
                "title": "Mine, renamed",
                "description": "New words.",
                "tags": "big-o, ske",
            },
        )
        self.assertRedirects(
            response, reverse("shorts:manage"), fetch_redirect_response=False
        )
        self.mine.refresh_from_db()
        self.assertEqual(
            (self.mine.title, self.mine.description),
            ("Mine, renamed", "New words."),
        )
        self.assertEqual(
            {t.slug for t in self.mine.tags.all()}, {"big-o", "ske"}
        )
        self.assertEqual(self.mine.clip.name, old_name)
        self.assertTrue(self.storage.exists(old_name))

    def test_replacing_the_clip_stores_the_new_one_and_removes_the_old(
        self,
    ) -> None:
        old_name = self.mine.clip.name
        self.client.force_login(self.lecturer)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse("shorts:edit", args=[self.mine.pk]),
                {
                    "title": "Mine",
                    "description": "",
                    "tags": "recursion",
                    "clip": clip(
                        45, name="take2.mov", content_type="video/quicktime"
                    ),
                },
            )
        self.mine.refresh_from_db()
        self.assertNotEqual(self.mine.clip.name, old_name)
        self.assertTrue(self.mine.clip.name.endswith(".mov"))
        self.assertEqual(self.mine.duration_seconds, 45)
        self.assertEqual(self.mine.original_name, "take2.mov")
        self.assertTrue(self.storage.exists(self.mine.clip.name))
        self.assertFalse(self.storage.exists(old_name))

    def test_a_too_long_replacement_changes_nothing(self) -> None:
        old_name = self.mine.clip.name
        self.client.force_login(self.lecturer)
        response = self.client.post(
            reverse("shorts:edit", args=[self.mine.pk]),
            {
                "title": "Changed?",
                "description": "",
                "tags": "recursion",
                "clip": clip(95),
            },
        )
        self.assertContains(
            response, "Shorts can be at most 1:30.", status_code=400
        )
        self.mine.refresh_from_db()
        self.assertEqual(
            (self.mine.title, self.mine.clip.name), ("Mine", old_name)
        )

    def test_failed_save_keeps_the_old_clip_and_drops_the_new(self) -> None:
        old_name = self.mine.clip.name
        with patch(
            "shorts.services.resolve_tags",
            return_value=[Tag(slug="x", label="x")],
        ):
            with self.assertRaises(ValueError):
                update_short(
                    self.mine,
                    title="t",
                    description="",
                    tags=[("x", "x")],
                    clip=clip(20, name="new.mp4"),
                    duration_seconds=20,
                )
        self.assertTrue(self.storage.exists(old_name))
        self.assertEqual(
            len(self.storage.listdir("clips")[1]), 2
        )  # mine + theirs, no orphan
        self.assertEqual(
            Short.objects.get(pk=self.mine.pk).clip.name, old_name
        )

    def test_lecturer_cannot_edit_or_delete_someone_elses_short(self) -> None:
        self.client.force_login(self.lecturer)
        for url in (
            reverse("shorts:edit", args=[self.theirs.pk]),
            reverse("shorts:delete", args=[self.theirs.pk]),
        ):
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(
                self.client.post(url, {"title": "x", "tags": "x"}).status_code,
                404,
            )
        self.theirs.refresh_from_db()
        self.assertEqual(self.theirs.title, "Theirs")

    def test_department_can_edit_anyones_short(self) -> None:
        self.client.force_login(self.department)
        self.client.post(
            reverse("shorts:edit", args=[self.theirs.pk]),
            {"title": "Moderated", "description": "", "tags": "ske"},
        )
        self.theirs.refresh_from_db()
        self.assertEqual(self.theirs.title, "Moderated")
        self.assertEqual(
            self.theirs.author, self.other_lecturer
        )  # still theirs

    # --- deleting ---
    def test_delete_asks_first_then_removes_short_clip_likes_and_comments(
        self,
    ) -> None:
        self.client.force_login(self.student)
        self.client.post(
            reverse("shorts:api_like", args=[self.mine.pk]),
            {"liked": True},
            content_type="application/json",
        )
        self.client.post(
            reverse("shorts:api_comments", args=[self.mine.pk]),
            {"body": "hi"},
            content_type="application/json",
        )
        self.client.force_login(self.lecturer)
        confirm = self.client.get(
            reverse("shorts:delete", args=[self.mine.pk])
        )
        self.assertContains(confirm, "Delete this short?")
        self.assertContains(
            confirm, "1 like and 1 comment will be deleted too."
        )
        self.assertTrue(
            Short.objects.filter(pk=self.mine.pk).exists()
        )  # GET changes nothing

        name = self.mine.clip.name
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("shorts:delete", args=[self.mine.pk]), follow=True
            )
        self.assertRedirects(response, reverse("shorts:manage"))
        self.assertContains(response, "“Mine” was deleted.")
        self.assertFalse(Short.objects.filter(pk=self.mine.pk).exists())
        self.assertFalse(self.storage.exists(name))

    def test_department_can_delete_anyones_short(self) -> None:
        self.client.force_login(self.department)
        self.client.post(reverse("shorts:delete", args=[self.theirs.pk]))
        self.assertFalse(Short.objects.filter(pk=self.theirs.pk).exists())
