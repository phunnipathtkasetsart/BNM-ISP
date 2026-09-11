"""Database-backed tests for the public layer.

These are the tests that could not exist before: building a test database
failed because `Users` lived in a schema only db_backup.sql created. The
accounts 0002 migration now builds that schema, so the visibility rules the
whole public layer depends on can finally be asserted rather than eyeballed.
"""

from django.test import TestCase
from django.urls import reverse

from .forms import AnnouncementForm
from .models import Announcement, Audience, Faq, Tag


class GuestVisibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.dept_tag = Tag.objects.create(
            slug="department", label="Department", kind=Tag.Kind.DEPARTMENT
        )
        cls.course_tag = Tag.objects.create(
            slug="cs101", label="CS101", kind=Tag.Kind.COURSE
        )

        cls.public = Announcement.objects.create(
            title="Registration closes Friday",
            body="Add/drop closes at 23:59 on Friday.",
            audience=Audience.PUBLIC,
        )
        cls.public.tags.set([cls.dept_tag])

        cls.internal = Announcement.objects.create(
            title="CS101 Lab 3 extended",
            body="Lab 3 has been extended by one week.",
            audience=Audience.STUDENTS,
        )
        cls.internal.tags.set([cls.course_tag])

        cls.faq = Faq.objects.create(
            question="What is my Nisit ID?",
            answer="Your ten-digit student number.",
            audience=Audience.PUBLIC,
        )
        Faq.objects.create(
            question="Where is the staff room?",
            answer="Second floor.",
            audience=Audience.STAFF,
        )

    def sign_in_as_guest(self):
        self.client.post(reverse("accounts:guest_login"))

    def test_anonymous_cannot_reach_the_board(self):
        """The board is behind sign-in or the guest button, by design."""
        response = self.client.get(reverse("announcements:public_board"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_guest_sees_only_public_announcements(self):
        self.sign_in_as_guest()
        response = self.client.get(reverse("announcements:public_board"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registration closes Friday")
        self.assertNotContains(response, "CS101 Lab 3 extended")

    def test_guest_sees_only_public_faqs(self):
        self.sign_in_as_guest()
        response = self.client.get(reverse("announcements:public_board"))
        self.assertContains(response, "What is my Nisit ID?")
        self.assertNotContains(response, "Where is the staff room?")

    def test_tag_filter_narrows_the_list(self):
        self.sign_in_as_guest()
        response = self.client.get(
            reverse("announcements:public_board"), {"tag": "department"}
        )
        self.assertContains(response, "Registration closes Friday")

    def test_search_never_leaks_a_non_public_announcement(self):
        """The rule most likely to break: search is a second read path."""
        self.sign_in_as_guest()
        response = self.client.get(
            reverse("announcements:public_board"), {"q": "CS101"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "CS101 Lab 3 extended")

    def test_unpublished_announcement_is_hidden(self):
        self.sign_in_as_guest()
        Announcement.objects.create(
            title="Draft notice", body="Not ready.",
            audience=Audience.PUBLIC, is_published=False,
        )
        response = self.client.get(reverse("announcements:public_board"))
        self.assertNotContains(response, "Draft notice")

    def test_guest_cannot_open_the_create_form(self):
        response = self.client.get(reverse("announcements:announcement_create"))
        self.assertEqual(response.status_code, 302)


class SignedInBoardTests(TestCase):
    """The board replaced the dashboard, so it now carries identity and role.

    These assertions used to live in accounts/tests.py against dashboard_view.
    They belong here now: the board reads the database, so they need a real
    test database rather than a SimpleTestCase.
    """

    @classmethod
    def setUpTestData(cls):
        from accounts.models import User
        cls.roles = {}
        for key, staff, superuser in (
            ("Student", False, False),
            ("Lecturer", True, False),
            ("Department", False, True),
        ):
            user = User.objects.create_user(
                nisit_id=f"999000{len(cls.roles)}999",
                email=f"{key.lower()}@ku.th",
                password="test-pass-1234",
                first_name=key, last_name="Tester", department="ske",
            )
            user.is_staff, user.is_superuser = staff, superuser
            user.save(update_fields=["is_staff", "is_superuser"])
            cls.roles[key] = user

    def test_each_role_sees_its_own_label_on_the_board(self):
        for role, user in self.roles.items():
            with self.subTest(role=role):
                self.client.force_login(user)
                response = self.client.get(reverse("announcements:public_board"))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'<span class="topbar__role">{role}</span>', html=True)
                self.assertContains(response, "Tester")

    def test_only_department_is_offered_manage_roles(self):
        for role, user in self.roles.items():
            with self.subTest(role=role):
                self.client.force_login(user)
                response = self.client.get(reverse("announcements:public_board"))
                self.assertEqual(b"Manage roles" in response.content, role == "Department")

    def test_only_staff_are_offered_the_create_control(self):
        for role, user in self.roles.items():
            with self.subTest(role=role):
                self.client.force_login(user)
                response = self.client.get(reverse("announcements:public_board"))
                expected = role in {"Lecturer", "Department"}
                self.assertEqual(b"Create Announcement" in response.content, expected)


class RoleVisibilityTests(TestCase):
    """Who sees which audience. The bug this covers: signed-in students and
    lecturers only ever saw public items, so anything addressed to them was
    invisible to them."""

    @classmethod
    def setUpTestData(cls):
        from accounts.models import User
        cls.people = {}
        for key, nid, staff, sup in (
            ("student", "7000000101", False, False),
            ("lecturer", "7000000102", True, False),
            ("department", "7000000103", False, True),
        ):
            u = User.objects.create_user(
                nisit_id=nid, email=f"{key}@ku.th", password="vis-1234",
                first_name=key.title(), last_name="V", department="ske",
            )
            u.is_staff, u.is_superuser = staff, sup
            u.save(update_fields=["is_staff", "is_superuser"])
            cls.people[key] = u

        for title, audience in (
            ("Probe public", Audience.PUBLIC),
            ("Probe students", Audience.STUDENTS),
            ("Probe staff", Audience.STAFF),
        ):
            Announcement.objects.create(title=title, body="probe", audience=audience)

    def sign_in_as_guest(self):
        self.client.post(reverse("accounts:guest_login"))

    def board(self):
        return self.client.get(reverse("announcements:public_board"))

    def test_visibility_matrix(self):
        expected = {
            # role        public students staff
            # Anonymous cannot reach the board at all now; "guest" is the
            # deliberate way in, and sees exactly what anonymous used to.
            "guest": (True, False, False),
            "student": (True, True, False),
            "lecturer": (True, True, True),
            "department": (True, True, True),
        }
        for role, (pub, stu, staff) in expected.items():
            with self.subTest(role=role):
                if role == "guest":
                    self.client.logout()
                    self.sign_in_as_guest()
                else:
                    self.client.force_login(self.people[role])
                page = self.board().content
                self.assertEqual(b"Probe public" in page, pub)
                self.assertEqual(b"Probe students" in page, stu)
                self.assertEqual(b"Probe staff" in page, staff)

    def test_student_cannot_reach_a_staff_item_through_search(self):
        """Search is the second read path, so it gets its own assertion."""
        self.client.force_login(self.people["student"])
        response = self.client.get(
            reverse("announcements:public_board"), {"q": "Probe"}
        )
        self.assertNotContains(response, "Probe staff")


class LabTagTests(TestCase):
    """Lab notices are department business and never public.

    US-02 says internal lab posts stay hidden from unauthenticated readers,
    and the team's rule is that only the Department may post one.
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

    def test_a_lecturer_cannot_post_a_lab_tag(self):
        form = AnnouncementForm(
            data={"title": "x", "body": "y", "audience": Audience.STAFF,
                  "tags": [self.lab.pk]},
            author=self.lecturer,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("Department", form.errors["tags"][0])

    def test_the_department_can_post_a_lab_tag(self):
        form = AnnouncementForm(
            data={"title": "x", "body": "y", "audience": Audience.STAFF,
                  "tags": [self.lab.pk]},
            author=self.department,
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_a_lecturer_may_still_use_an_ordinary_tag(self):
        form = AnnouncementForm(
            data={"title": "x", "body": "y", "audience": Audience.STUDENTS,
                  "tags": [self.dept_tag.pk]},
            author=self.lecturer,
        )
        self.assertTrue(form.is_valid(), form.errors)

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


class DateFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from datetime import datetime, timezone as tz
        cls.old = Announcement.objects.create(
            title="Older notice", body="b", audience=Audience.PUBLIC,
            published_at=datetime(2026, 1, 5, 9, 0, tzinfo=tz.utc),
        )
        cls.recent = Announcement.objects.create(
            title="Recent notice", body="b", audience=Audience.PUBLIC,
            published_at=datetime(2026, 6, 20, 9, 0, tzinfo=tz.utc),
        )

    def board(self, **params):
        self.client.post(reverse("accounts:guest_login"))
        return self.client.get(reverse("announcements:public_board"), params)

    def test_from_date_excludes_earlier_items(self):
        response = self.board(**{"from": "2026-03-01"})
        self.assertContains(response, "Recent notice")
        self.assertNotContains(response, "Older notice")

    def test_to_date_excludes_later_items(self):
        response = self.board(**{"to": "2026-03-01"})
        self.assertContains(response, "Older notice")
        self.assertNotContains(response, "Recent notice")

    def test_a_single_day_includes_that_whole_day(self):
        """Posted at 09:00, so comparing against midnight would drop it."""
        response = self.board(**{"from": "2026-06-20", "to": "2026-06-20"})
        self.assertContains(response, "Recent notice")

    def test_an_unparseable_date_narrows_nothing(self):
        response = self.board(**{"from": "notadate"})
        self.assertContains(response, "Older notice")
        self.assertContains(response, "Recent notice")


class LabEditGuardTests(TestCase):
    """A lecturer cannot edit a post carrying a lab tag.

    The form cannot offer a lab tag to a lecturer, so letting the edit through
    would drop the tag on save without saying so.
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

    def test_lecturer_cannot_edit_their_own_lab_post(self):
        self.client.force_login(self.lecturer)
        self.assertEqual(self.edit(self.lab_post.pk).status_code, 403)

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


class ProgrammeTagTests(TestCase):
    """SKE and CPE posts reach their own programme only.

    The department column holds both "SKE" and "ske", so the match has to be
    case-insensitive or half the students silently lose their own notices.
    """

    @classmethod
    def setUpTestData(cls):
        from accounts.models import User

        # Migration 0004 already created these, so fetch rather than
        # create: the slug is unique.
        cls.ske, _ = Tag.objects.update_or_create(
            slug="ske",
            defaults={"label": "SKE", "kind": Tag.Kind.PROGRAMME},
        )
        cls.cpe, _ = Tag.objects.update_or_create(
            slug="cpe",
            defaults={"label": "CPE", "kind": Tag.Kind.PROGRAMME},
        )

        cls.ske_student = User.objects.create_user(
            nisit_id="7400000001", email="ske@ku.th", password="prog-1234",
            first_name="Ske", last_name="S", department="SKE",
        )
        cls.cpe_student = User.objects.create_user(
            nisit_id="7400000002", email="cpe@ku.th", password="prog-1234",
            first_name="Cpe", last_name="S", department="cpe",
        )
        cls.department = User.objects.create_user(
            nisit_id="7400000003", email="dep2@ku.th", password="prog-1234",
            first_name="Dep", last_name="S", department="ske",
        )
        cls.department.is_superuser = True
        cls.department.save(update_fields=["is_superuser"])

        cls.ske_post = Announcement.objects.create(
            title="SKE curriculum briefing", body="Third year.",
            audience=Audience.PUBLIC, is_published=True,
        )
        cls.ske_post.tags.add(cls.ske)

        cls.cpe_post = Announcement.objects.create(
            title="CPE internship placement", body="Now open.",
            audience=Audience.PUBLIC, is_published=True,
        )
        cls.cpe_post.tags.add(cls.cpe)

        cls.untagged = Announcement.objects.create(
            title="Registration closes Friday", body="Add/drop.",
            audience=Audience.PUBLIC, is_published=True,
        )

    def titles_for(self, user, is_guest=False):
        return set(
            Announcement.objects.visible_to(user, is_guest)
            .values_list("title", flat=True)
        )

    def test_ske_student_sees_only_ske(self):
        titles = self.titles_for(self.ske_student)
        self.assertIn("SKE curriculum briefing", titles)
        self.assertNotIn("CPE internship placement", titles)

    def test_cpe_student_sees_only_cpe(self):
        titles = self.titles_for(self.cpe_student)
        self.assertIn("CPE internship placement", titles)
        self.assertNotIn("SKE curriculum briefing", titles)

    def test_untagged_post_reaches_both(self):
        for student in (self.ske_student, self.cpe_student):
            self.assertIn("Registration closes Friday", self.titles_for(student))

    def test_department_sees_every_programme(self):
        titles = self.titles_for(self.department)
        self.assertIn("SKE curriculum briefing", titles)
        self.assertIn("CPE internship placement", titles)

    def test_guest_sees_no_programme_post(self):
        titles = self.titles_for(self.ske_student, is_guest=True)
        self.assertNotIn("SKE curriculum briefing", titles)
        self.assertNotIn("CPE internship placement", titles)
        self.assertIn("Registration closes Friday", titles)


class DepartmentChipTests(TestCase):
    """SKE and CPE are departments, so the generic #Department filter is
    redundant for a reader who has one. The posts stay; only the chip goes."""

    @classmethod
    def setUpTestData(cls):
        from accounts.models import User

        cls.dept_tag, _ = Tag.objects.update_or_create(
            slug="department",
            defaults={"label": "Department", "kind": Tag.Kind.DEPARTMENT},
        )
        Tag.objects.update_or_create(
            slug="ske",
            defaults={"label": "SKE", "kind": Tag.Kind.PROGRAMME},
        )
        cls.notice = Announcement.objects.create(
            title="Registration closes Friday", body="Add/drop.",
            audience=Audience.PUBLIC, is_published=True,
        )
        cls.notice.tags.add(cls.dept_tag)

        cls.student = User.objects.create_user(
            nisit_id="7500000001", email="chip@ku.th", password="chip-1234",
            first_name="Chip", last_name="S", department="SKE",
        )
        cls.boss = User.objects.create_user(
            nisit_id="7500000002", email="chip2@ku.th", password="chip-1234",
            first_name="Boss", last_name="S", department="ske",
        )
        cls.boss.is_superuser = True
        cls.boss.save(update_fields=["is_superuser"])

    def chips_for(self, user):
        self.client.force_login(user)
        response = self.client.get(reverse("announcements:public_board"))
        return [t.slug for t in response.context["chips"]]

    def test_programme_student_loses_the_department_chip(self):
        self.assertNotIn("department", self.chips_for(self.student))

    def test_but_keeps_the_posts(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("announcements:public_board"))
        titles = [a.title for a in response.context["announcements"]]
        self.assertIn("Registration closes Friday", titles)

    def test_department_role_keeps_the_chip(self):
        self.assertIn("department", self.chips_for(self.boss))
