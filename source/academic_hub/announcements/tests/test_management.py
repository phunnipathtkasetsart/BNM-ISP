from unittest.mock import patch

from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from ..forms import AnnouncementForm
from ..models import Announcement, Audience, Tag
from ..views import can_manage_announcements


class AnnouncementPermissionTests(SimpleTestCase):
    def test_management_requires_staff_and_ownership_or_department_role(self):
        from accounts.models import User
        from django.contrib.auth.models import AnonymousUser

        announcement = Announcement(author_id="owner")
        for user, can_create, can_edit in [
            (AnonymousUser(), False, False),
            (User(nisit_id="owner"), False, False),
            (User(nisit_id="owner", is_staff=True), True, True),
            (User(nisit_id="other", is_staff=True), True, False),
            (User(nisit_id="department", is_superuser=True), True, True),
        ]:
            with self.subTest(user=user):
                self.assertEqual(can_manage_announcements(user), can_create)
                self.assertEqual(can_manage_announcements(user, announcement), can_edit)
                self.assertEqual(
                    can_manage_announcements(user, Announcement()),
                    user.is_superuser,
                )

    def test_url_names_are_unique(self):
        from ..urls import urlpatterns

        names = [pattern.name for pattern in urlpatterns]
        self.assertEqual(len(names), len(set(names)))


class LabTagTests(TestCase):
    """Who may attach which tag, and what a guest may still not see.

    A lecturer may tag Lab, SKE and CPE. The Department may also tag
    Department and Scholarship. Nobody posts Syllabus from this form.
    US-02 still holds separately: a lab post never reaches a guest,
    whoever wrote it.
    """

    @classmethod
    def setUpTestData(cls):
        from accounts.models import User
        cls.lab = Tag.objects.create(slug="lab", label="Lab", kind=Tag.Kind.LAB)
        cls.dept_tag = Tag.objects.create(
            slug="department", label="Department", kind=Tag.Kind.DEPARTMENT
        )
        cls.lecturer = User.objects.create_user(
            nisit_id="7300000002", email="lec@ku.th", password="lab-1234",
            first_name="Lec", last_name="T", department="ske",
        )
        cls.lecturer.is_staff = True
        cls.lecturer.save(update_fields=["is_staff"])

        cls.department = User.objects.create_user(
            nisit_id="7300000003", email="dep@ku.th", password="lab-1234",
            first_name="Dep", last_name="T", department="ske",
        )
        cls.department.is_superuser = True
        cls.department.save(update_fields=["is_superuser"])

        cls.lab_post = Announcement.objects.create(
            title="Lab access hours changed", body="Closing early.",
            audience=Audience.STAFF,
        )
        cls.lab_post.tags.set([cls.lab])

    def test_a_lecturer_can_post_a_lab_tag(self):
        form = AnnouncementForm(
            data={"title": "x", "body": "y", "audience": Audience.STAFF,
                  "tags": [self.lab.pk]},
            author=self.lecturer,
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_the_department_can_post_a_lab_tag(self):
        form = AnnouncementForm(
            data={"title": "x", "body": "y", "audience": Audience.STAFF,
                  "tags": [self.lab.pk]},
            author=self.department,
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_a_lecturer_cannot_post_a_department_tag(self):
        form = AnnouncementForm(
            data={"title": "x", "body": "y", "audience": Audience.STUDENTS,
                  "tags": [self.dept_tag.pk]},
            author=self.lecturer,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("Lab, SKE and CPE", form.errors["tags"][0])

    def test_only_the_department_is_offered_scholarship(self):
        money = Tag.objects.create(
            slug="scholarship-5a", label="5A", kind=Tag.Kind.SCHOLARSHIP
        )
        offered = AnnouncementForm(author=self.lecturer).fields["tags"].queryset
        self.assertNotIn(money, offered)
        offered = AnnouncementForm(author=self.department).fields["tags"].queryset
        self.assertIn(money, offered)

    def test_syllabus_is_offered_to_nobody(self):
        syllabus = Tag.objects.create(
            slug="syllabus", label="Syllabus", kind=Tag.Kind.TOPIC
        )
        for who in (self.lecturer, self.department):
            offered = AnnouncementForm(author=who).fields["tags"].queryset
            self.assertNotIn(syllabus, offered)

    def test_a_public_item_tagged_lab_is_still_hidden_from_guests(self):
        """Belt and braces: mistagging must not leak an internal notice."""
        slip = Announcement.objects.create(
            title="Mistagged lab notice", body="Should stay hidden.",
            audience=Audience.PUBLIC,
        )
        slip.tags.set([self.lab])
        self.client.post(reverse("accounts:guest_login"))
        response = self.client.get(reverse("announcements:public_board"))
        self.assertNotContains(response, "Mistagged lab notice")


class LabEditGuardTests(TestCase):
    """Who may edit a post that carries a lab tag.

    Lecturers can post with #Lab, so they must also be able to edit those
    posts. They used to get a 403 on their own Lab post.
    """

    @classmethod
    def setUpTestData(cls):
        from accounts.models import User
        cls.lab = Tag.objects.create(slug="lab", label="Lab", kind=Tag.Kind.LAB)
        cls.plain = Tag.objects.create(
            slug="department", label="Department", kind=Tag.Kind.DEPARTMENT
        )
        cls.lecturer = User.objects.create_user(
            nisit_id="7600000002", email="l6@ku.th", password="lab-1234",
            first_name="Lec", last_name="G", department="ske",
        )
        cls.lecturer.is_staff = True
        cls.lecturer.save(update_fields=["is_staff"])
        cls.department = User.objects.create_user(
            nisit_id="7600000003", email="d6@ku.th", password="lab-1234",
            first_name="Dep", last_name="G", department="ske",
        )
        cls.department.is_superuser = True
        cls.department.save(update_fields=["is_superuser"])

        cls.lab_post = Announcement.objects.create(
            title="Own lab post", body="b",
            audience=Audience.STUDENTS, author=cls.lecturer,
        )
        cls.lab_post.tags.set([cls.lab])

        cls.plain_post = Announcement.objects.create(
            title="Own plain post", body="b",
            audience=Audience.STUDENTS, author=cls.lecturer,
        )
        cls.plain_post.tags.set([cls.plain])

    def edit(self, pk):
        return self.client.get(reverse("announcements:announcement_edit", args=[pk]))

    def test_lecturer_can_edit_their_own_lab_post(self):
        self.client.force_login(self.lecturer)
        self.assertEqual(self.edit(self.lab_post.pk).status_code, 200)

    def test_saving_a_lecturers_lab_post_keeps_the_lab_tag(self):
        self.client.force_login(self.lecturer)
        response = self.client.post(
            reverse("announcements:announcement_edit", args=[self.lab_post.pk]),
            {"title": "Own lab post, edited", "body": "b", "audience": Audience.STUDENTS,
             "tags": [self.lab.pk], "is_published": "on"},
        )
        self.assertEqual(response.status_code, 302)
        self.lab_post.refresh_from_db()
        self.assertEqual(self.lab_post.title, "Own lab post, edited")
        self.assertEqual(list(self.lab_post.tags.all()), [self.lab])

    def test_lecturer_still_cannot_edit_someone_elses_lab_post(self):
        other = Announcement.objects.create(
            title="Department lab post", body="b",
            audience=Audience.STUDENTS, author=self.department,
        )
        other.tags.set([self.lab])
        self.client.force_login(self.lecturer)
        self.assertEqual(self.edit(other.pk).status_code, 403)

    def test_lecturer_can_still_edit_their_ordinary_post(self):
        self.client.force_login(self.lecturer)
        self.assertEqual(self.edit(self.plain_post.pk).status_code, 200)

    def test_department_can_edit_a_lab_post(self):
        self.client.force_login(self.department)
        self.assertEqual(self.edit(self.lab_post.pk).status_code, 200)

    def test_a_lab_tagged_post_never_reaches_a_guest(self):
        self.client.post(reverse("accounts:guest_login"))
        response = self.client.get(reverse("announcements:public_board"))
        self.assertNotContains(response, "Own lab post")

    def test_management_actions_require_post(self):
        self.client.force_login(self.lecturer)
        for action in ("archive", "restore", "delete"):
            with self.subTest(action=action):
                response = self.client.get(reverse(
                    f"announcements:announcement_{action}", args=[self.lab_post.pk]
                ))
                self.assertEqual(response.status_code, 405)
        self.lab_post.refresh_from_db()
        self.assertIsNone(self.lab_post.archived_at)

    def test_create_preserves_author_tags_and_search(self):
        for user, label in ((self.lecturer, "Lecturer"), (self.department, "Department")):
            with self.subTest(user=user):
                self.client.force_login(user)
                response = self.client.post(reverse("announcements:announcement_create"), {
                    "title": "Fresh lab bulletin", "body": "New lab hours",
                    "audience": Audience.STUDENTS, "tags": [self.lab.pk],
                    "is_published": "on",
                })
                self.assertRedirects(response, reverse("announcements:public_board"))
                post = Announcement.objects.get(title="Fresh lab bulletin", author=user)
                self.assertEqual(post.author_label, label)
                self.assertEqual(list(post.tags.all()), [self.lab])
                results = self.client.get(reverse("announcements:public_board"), {"q": "bulletin"})
                self.assertIn(post, results.context["announcements"])

    def test_empty_post_returns_bound_form_errors_without_creating_announcement(self):
        self.client.force_login(self.lecturer)
        count = Announcement.objects.count()
        response = self.client.post(reverse("announcements:announcement_create"), {})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].is_bound)
        self.assertIn("title", response.context["form"].errors)
        self.assertEqual(Announcement.objects.count(), count)

    def test_failed_create_rolls_back_the_announcement(self):
        self.client.force_login(self.lecturer)
        count = Announcement.objects.count()
        for target in (
            "announcements.forms.AnnouncementForm._save_m2m",
            "announcements.views.announcements._refresh_search_vector",
        ):
            with self.subTest(target=target):
                with patch(target, side_effect=DatabaseError("Save failed")):
                    with self.assertRaisesMessage(DatabaseError, "Save failed"):
                        self.client.post(reverse("announcements:announcement_create"), {
                            "title": "Failed announcement", "body": "b",
                            "audience": Audience.STUDENTS, "tags": [self.lab.pk],
                            "is_published": "on",
                        })
                self.assertEqual(Announcement.objects.count(), count)
                self.assertFalse(Announcement.objects.filter(title="Failed announcement").exists())

    def test_failed_edit_restores_content_and_tags(self):
        self.client.force_login(self.department)
        updated_at = self.lab_post.updated_at
        for target in (
            "announcements.forms.AnnouncementForm._save_m2m",
            "announcements.views.announcements._refresh_search_vector",
        ):
            with self.subTest(target=target):
                with patch(target, side_effect=DatabaseError("Save failed")):
                    with self.assertRaisesMessage(DatabaseError, "Save failed"):
                        self.client.post(reverse(
                            "announcements:announcement_edit", args=[self.lab_post.pk]
                        ), {
                            "title": "Failed edit", "body": "Changed body",
                            "audience": Audience.PUBLIC, "tags": [self.plain.pk],
                            "is_published": "on",
                        })
                self.lab_post.refresh_from_db()
                self.assertEqual(self.lab_post.title, "Own lab post")
                self.assertEqual(self.lab_post.body, "b")
                self.assertEqual(self.lab_post.audience, Audience.STUDENTS)
                self.assertEqual(self.lab_post.updated_at, updated_at)
                self.assertEqual(list(self.lab_post.tags.all()), [self.lab])

    def test_lecturer_cannot_modify_another_authors_post(self):
        self.lab_post.author = self.department
        self.lab_post.save(update_fields=["author"])
        self.client.force_login(self.lecturer)
        for action in ("edit", "archive", "restore", "delete"):
            with self.subTest(action=action):
                response = self.client.post(reverse(
                    f"announcements:announcement_{action}", args=[self.lab_post.pk]
                ), {"title": "Forbidden edit", "body": "b", "audience": Audience.STUDENTS})
                self.assertEqual(response.status_code, 403)
                self.lab_post.refresh_from_db()
                self.assertEqual(self.lab_post.title, "Own lab post")
                self.assertIsNone(self.lab_post.archived_at)

    def test_owner_and_department_can_archive_restore_and_delete(self):
        for user in (self.lecturer, self.department):
            with self.subTest(user=user):
                self.client.force_login(user)
                post = Announcement.objects.create(
                    title="Managed post", body="b", author=self.lecturer,
                )
                response = self.client.post(reverse(
                    "announcements:announcement_archive", args=[post.pk]
                ))
                self.assertRedirects(response, reverse("announcements:public_board"))
                post.refresh_from_db()
                self.assertIsNotNone(post.archived_at)
                self.assertEqual(post.archived_by_id, user.pk)
                board = self.client.get(reverse("announcements:public_board"))
                self.assertNotIn(post, board.context["announcements"])
                self.assertIn(post, board.context["archived_announcements"])

                response = self.client.post(reverse(
                    "announcements:announcement_restore", args=[post.pk]
                ))
                self.assertRedirects(response, reverse("announcements:public_board"))
                post.refresh_from_db()
                self.assertIsNone(post.archived_at)
                self.assertIsNone(post.archived_by_id)
                board = self.client.get(reverse("announcements:public_board"))
                self.assertIn(post, board.context["announcements"])
                self.assertNotIn(post, board.context["archived_announcements"])

                response = self.client.post(reverse(
                    "announcements:announcement_delete", args=[post.pk]
                ))
                self.assertRedirects(response, reverse("announcements:public_board"))
                self.assertFalse(Announcement.objects.filter(pk=post.pk).exists())

    def test_lecturer_board_includes_own_drafts_and_archives_only(self):
        self.lab_post.is_published = False
        self.lab_post.save(update_fields=["is_published"])
        self.plain_post.archived_at = timezone.now()
        self.plain_post.save(update_fields=["archived_at"])
        other_draft = Announcement.objects.create(
            title="Department draft", body="b", author=self.department,
            is_published=False,
        )
        other_archive = Announcement.objects.create(
            title="Department archive", body="b", author=self.department,
            archived_at=timezone.now(),
        )
        self.client.force_login(self.lecturer)
        board = self.client.get(reverse("announcements:public_board"))
        self.assertIn(self.lab_post, board.context["announcements"])
        self.assertNotIn(self.plain_post, board.context["announcements"])
        self.assertNotIn(other_draft, board.context["announcements"])
        self.assertNotIn(other_archive, board.context["announcements"])
        self.assertEqual(list(board.context["archived_announcements"]), [self.plain_post])
