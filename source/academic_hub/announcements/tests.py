"""Database-backed tests for the public layer.

These are the tests that could not exist before: building a test database
failed because `Users` lived in a schema only db_backup.sql created. The
accounts 0002 migration now builds that schema, so the visibility rules the
whole public layer depends on can finally be asserted rather than eyeballed.
"""

from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.dateformat import format as date_format

from .forms import AnnouncementForm
from .models import (
    Announcement,
    Audience,
    Faq,
    ForumQuestion,
    ForumReply,
    Tag,
)


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
        self.client.logout()
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

        # Addressed to both programmes at once. The first version of
        # for_programme() asked whether a post carried somebody
        # else's tag, so this reached nobody at all.
        cls.both = Announcement.objects.create(
            title="Joint SKE and CPE briefing", body="Both programmes.",
            audience=Audience.PUBLIC, is_published=True,
        )
        cls.both.tags.add(cls.ske, cls.cpe)

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

    def test_a_post_tagged_for_both_programmes_reaches_both(self):
        for student in (self.ske_student, self.cpe_student):
            self.assertIn(
                "Joint SKE and CPE briefing", self.titles_for(student)
            )

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


class ForumQuestionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from accounts.models import User

        cls.ske, _ = Tag.objects.update_or_create(
            slug="ske",
            defaults={"label": "SKE", "kind": Tag.Kind.PROGRAMME},
        )
        cls.cpe, _ = Tag.objects.update_or_create(
            slug="cpe",
            defaults={"label": "CPE", "kind": Tag.Kind.PROGRAMME},
        )

        cls.student = User.objects.create_user(
            nisit_id="7600000001",
            email="forum-student@ku.th",
            password="test-password",
            first_name="Forum",
            last_name="Student",
            department="SKE",
        )
        cls.other_student = User.objects.create_user(
            nisit_id="7600000002",
            email="forum-cpe@ku.th",
            password="test-password",
            first_name="Cpe",
            last_name="Student",
            department="cpe",
        )
        cls.lecturer = User.objects.create_user(
            nisit_id="7600000003",
            email="forum-lecturer@ku.th",
            password="test-password",
            first_name="Forum",
            last_name="Lecturer",
            department="ske",
            is_staff=True,
        )
        cls.other_lecturer = User.objects.create_user(
            nisit_id="7600000004",
            email="forum-lecturer2@ku.th",
            password="test-password",
            first_name="Other",
            last_name="Lecturer",
            department="cpe",
            is_staff=True,
        )
        cls.department = User.objects.create_user(
            nisit_id="7600000005",
            email="forum-department@ku.th",
            password="test-password",
            first_name="Forum",
            last_name="Department",
            department="ske",
            is_superuser=True,
        )

        cls.everyone_question = ForumQuestion.objects.create(
            title="Everyone question",
            body="Visible to every reader.",
            visibility=ForumQuestion.Visibility.EVERYONE,
            author=cls.student,
            author_label=cls.student.get_full_name(),
        )
        cls.ske_question = ForumQuestion.objects.create(
            title="SKE question",
            body="SKE department content.",
            visibility=ForumQuestion.Visibility.DEPARTMENT,
            author=cls.student,
            author_label=cls.student.get_full_name(),
        )
        cls.ske_question.departments.add(cls.ske)
        cls.cpe_question = ForumQuestion.objects.create(
            title="CPE question",
            body="CPE department content.",
            visibility=ForumQuestion.Visibility.DEPARTMENT,
            author=cls.other_student,
            author_label=cls.other_student.get_full_name(),
        )
        cls.cpe_question.departments.add(cls.cpe)
        cls.unpublished_question = ForumQuestion.objects.create(
            title="Unpublished question",
            body="Not yet visible.",
            visibility=ForumQuestion.Visibility.EVERYONE,
            is_published=False,
            author=cls.student,
            author_label=cls.student.get_full_name(),
        )
        cls.archived_question = ForumQuestion.objects.create(
            title="Archived question",
            body="Retained for Department.",
            visibility=ForumQuestion.Visibility.DEPARTMENT,
            archived_at=timezone.now(),
            author=cls.other_student,
            author_label=cls.other_student.get_full_name(),
        )
        cls.archived_question.departments.add(cls.cpe)

    def sign_in(self, user):
        self.client.force_login(user)

    def forum_url(self, name, *args):
        return reverse(f"announcements:{name}", args=args)

    def question_titles(self, user):
        self.client.force_login(user)
        response = self.client.get(self.forum_url("faq_board"))
        self.assertEqual(response.status_code, 200)
        return {question.title for question in response.context["threads"]}

    def test_student_sees_everyone_and_own_department_only(self):
        titles = self.question_titles(self.student)
        self.assertIn("Everyone question", titles)
        self.assertIn("SKE question", titles)
        self.assertNotIn("CPE question", titles)
        self.assertNotIn("Unpublished question", titles)

    def test_lecturer_sees_all_active_published_departments(self):
        titles = self.question_titles(self.lecturer)
        self.assertIn("Everyone question", titles)
        self.assertIn("SKE question", titles)
        self.assertIn("CPE question", titles)
        self.assertNotIn("Unpublished question", titles)
        self.assertNotIn("Archived question", titles)

    def test_department_sees_archived_and_unpublished_questions(self):
        titles = self.question_titles(self.department)
        self.assertIn("Unpublished question", titles)
        self.assertIn("Archived question", titles)

    def test_guest_opens_public_thread_but_not_department_thread(self):
        from accounts.middleware import GUEST_ALLOWED_VIEWS

        self.client.post(reverse("accounts:guest_login"))
        public_response = self.client.get(
            self.forum_url("faq_board"),
            {"q": self.everyone_question.pk},
        )
        self.assertEqual(public_response.status_code, 200)
        self.assertContains(public_response, "Everyone question")
        self.assertContains(public_response, "Visible to every reader.")
        self.assertNotContains(public_response, "CPE department content.")

        department_response = self.client.get(
            self.forum_url("faq_board"),
            {"q": self.cpe_question.pk},
        )
        self.assertEqual(department_response.status_code, 404)
        self.assertIn("announcements:faq_board", GUEST_ALLOWED_VIEWS)
        self.assertNotIn("announcements:forum_question_create", GUEST_ALLOWED_VIEWS)

    def test_guest_cannot_post_a_question(self):
        self.client.post(reverse("accounts:guest_login"))
        response = self.client.post(
            self.forum_url("forum_question_create"),
            {
                "title": "Guest attempt",
                "body": "This must not be saved.",
                "visibility": ForumQuestion.Visibility.EVERYONE,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ForumQuestion.objects.filter(title="Guest attempt").exists())

    def test_student_creates_own_department_and_everyone_questions(self):
        self.sign_in(self.student)
        response = self.client.post(
            self.forum_url("forum_question_create"),
            {
                "title": "Student department question",
                "body": "Only SKE students should see this.",
                "visibility": ForumQuestion.Visibility.DEPARTMENT,
                "is_published": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        question = ForumQuestion.objects.get(title="Student department question")
        self.assertEqual(list(question.departments.all()), [self.ske])
        self.assertTrue(question.is_published)
        self.assertEqual(question.author_label, "Forum Student")
        self.assertEqual(question.author_role, "Student")

        response = self.client.post(
            self.forum_url("forum_question_create"),
            {
                "title": "Student everyone question",
                "body": "This is for everyone.",
                "visibility": ForumQuestion.Visibility.EVERYONE,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            ForumQuestion.objects.get(title="Student everyone question").is_published
        )

    def test_student_forged_department_tag_is_rejected(self):
        self.sign_in(self.student)
        response = self.client.post(
            self.forum_url("forum_question_create"),
            {
                "title": "Forged tag question",
                "body": "Trying to select CPE.",
                "visibility": ForumQuestion.Visibility.DEPARTMENT,
                "departments": [self.cpe.pk],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ForumQuestion.objects.filter(title="Forged tag question").exists())
        self.assertContains(response, "Students may only use their own department tag.")

    def test_question_form_rejects_blank_and_oversized_content(self):
        self.sign_in(self.student)
        for title, body in (
            (" ", "Question body."),
            ("Long body question", "x" * 5001),
            ("x" * 201, "Question body."),
        ):
            with self.subTest(title_length=len(title), body_length=len(body)):
                response = self.client.post(
                    self.forum_url("forum_question_create"),
                    {
                        "title": title,
                        "body": body,
                        "visibility": ForumQuestion.Visibility.EVERYONE,
                    },
                )
                self.assertEqual(response.status_code, 200)
        self.assertFalse(
            ForumQuestion.objects.filter(title="Long body question").exists()
        )

    def test_question_creation_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.student)
        response = csrf_client.post(
            self.forum_url("forum_question_create"),
            {
                "title": "CSRF question",
                "body": "Must not be saved without a token.",
                "visibility": ForumQuestion.Visibility.EVERYONE,
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ForumQuestion.objects.filter(title="CSRF question").exists())

    def test_question_and_reply_text_is_autoescaped(self):
        question = ForumQuestion.objects.create(
            title="<script>alert(1)</script>",
            body="<img src=x onerror=alert(1)>",
            visibility=ForumQuestion.Visibility.EVERYONE,
        )
        ForumReply.objects.create(
            question=question,
            body="<script>reply</script>",
            author_label="Lecturer",
        )
        self.sign_in(self.lecturer)
        response = self.client.get(
            self.forum_url("faq_board"),
            {"q": question.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertNotContains(response, "<img src=x")
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;")

    def test_unknown_student_department_fails_closed(self):
        from accounts.models import User

        unknown_student = User.objects.create_user(
            nisit_id="7600000006",
            email="forum-unknown@ku.th",
            password="test-password",
            first_name="Unknown",
            last_name="Student",
            department="unknown",
        )
        self.client.force_login(unknown_student)
        response = self.client.get(self.forum_url("faq_board"))
        titles = {question.title for question in response.context["threads"]}
        self.assertIn("Everyone question", titles)
        self.assertNotIn("SKE question", titles)
        self.assertNotIn("CPE question", titles)
        self.assertContains(response, "department is not configured")

        response = self.client.post(
            self.forum_url("forum_question_create"),
            {
                "title": "Unknown department question",
                "body": "Cannot be department-scoped.",
                "visibility": ForumQuestion.Visibility.DEPARTMENT,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not configured for FAQ questions")
        self.assertFalse(
            ForumQuestion.objects.filter(title="Unknown department question").exists()
        )

    def test_lecturer_creates_everyone_and_department_questions(self):
        self.sign_in(self.lecturer)
        response = self.client.post(
            self.forum_url("forum_question_create"),
            {
                "title": "Lecturer public question",
                "body": "Visible to everyone.",
                "visibility": ForumQuestion.Visibility.EVERYONE,
                "is_published": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        public = ForumQuestion.objects.get(title="Lecturer public question")
        self.assertEqual(public.visibility, ForumQuestion.Visibility.EVERYONE)
        self.assertEqual(public.author_label, "Forum Lecturer")

        response = self.client.post(
            self.forum_url("forum_question_create"),
            {
                "title": "Lecturer CPE question",
                "body": "Department-specific guidance.",
                "visibility": ForumQuestion.Visibility.DEPARTMENT,
                "departments": [self.cpe.pk],
                "is_published": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        department_question = ForumQuestion.objects.get(
            title="Lecturer CPE question"
        )
        self.assertEqual(list(department_question.departments.all()), [self.cpe])
        self.assertTrue(department_question.is_published)

    def test_lecturer_can_retag_any_visible_question(self):
        self.sign_in(self.lecturer)
        response = self.client.post(
            self.forum_url("forum_question_edit", self.cpe_question.pk),
            {
                "departments": [self.ske.pk],
                "visibility": ForumQuestion.Visibility.EVERYONE,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.cpe_question.refresh_from_db()
        self.assertEqual(self.cpe_question.visibility, ForumQuestion.Visibility.DEPARTMENT)
        self.assertEqual(list(self.cpe_question.departments.all()), [self.ske])

    def test_student_cannot_reply_but_lecturer_can(self):
        reply_url = self.forum_url("forum_reply_create", self.ske_question.pk)
        self.sign_in(self.student)
        denied = self.client.post(reply_url, {"body": "A student reply."})
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(self.ske_question.replies.exists())

        self.sign_in(self.lecturer)
        accepted = self.client.post(reply_url, {"body": "Official guidance."})
        self.assertEqual(accepted.status_code, 302)
        self.assertIn("comments=1", accepted["Location"])
        reply = self.ske_question.replies.get()
        self.assertEqual(reply.author_label, "Forum Lecturer")
        self.assertEqual(reply.author_role, "Lecturer")
        thread = self.client.get(accepted["Location"])
        self.assertContains(
            thread,
            f'<details id="forum-comments-{self.ske_question.pk}" class="forum-comments" open>',
        )
        self.assertContains(thread, "Official guidance.")
        self.assertContains(thread, "Lecturer</span>: Forum Lecturer")
        self.assertNotContains(thread, "Question thread")

    def test_comments_appear_in_main_feed_only_for_questions_reader_can_see(self):
        ForumReply.objects.create(
            question=self.everyone_question,
            body="Public question comment.",
            author=self.lecturer,
            author_label=self.lecturer.get_full_name(),
            author_role="Lecturer",
        )
        ForumReply.objects.create(
            question=self.ske_question,
            body="SKE question comment.",
            author=self.lecturer,
            author_label=self.lecturer.get_full_name(),
            author_role="Lecturer",
        )
        ForumReply.objects.create(
            question=self.cpe_question,
            body="CPE question comment.",
            author=self.other_lecturer,
            author_label=self.other_lecturer.get_full_name(),
            author_role="Lecturer",
        )

        self.sign_in(self.student)
        student_feed = self.client.get(self.forum_url("faq_board"))
        self.assertContains(student_feed, "Public question comment.")
        self.assertContains(student_feed, "SKE question comment.")
        self.assertNotContains(student_feed, "CPE question comment.")
        self.assertContains(student_feed, "Student</span>: Forum Student")
        self.assertContains(student_feed, "Lecturer</span>: Forum Lecturer")

        self.client.logout()
        self.client.post(reverse("accounts:guest_login"))
        guest_feed = self.client.get(self.forum_url("faq_board"))
        self.assertContains(guest_feed, "Public question comment.")
        self.assertNotContains(guest_feed, "SKE question comment.")
        self.assertNotContains(guest_feed, "CPE question comment.")

        self.sign_in(self.lecturer)
        lecturer_feed = self.client.get(self.forum_url("faq_board"))
        self.assertContains(lecturer_feed, "Public question comment.")
        self.assertContains(lecturer_feed, "SKE question comment.")
        self.assertContains(lecturer_feed, "CPE question comment.")

    def test_lecturer_can_only_edit_and_archive_own_replies(self):
        own_reply = ForumReply.objects.create(
            question=self.ske_question,
            body="My response.",
            author=self.lecturer,
            author_label=self.lecturer.get_full_name(),
        )
        other_reply = ForumReply.objects.create(
            question=self.ske_question,
            body="Another response.",
            author=self.other_lecturer,
            author_label=self.other_lecturer.get_full_name(),
        )
        self.sign_in(self.lecturer)
        denied = self.client.post(
            self.forum_url("forum_reply_edit", other_reply.pk),
            {"body": "Forged edit."},
        )
        self.assertEqual(denied.status_code, 403)
        accepted = self.client.post(
            self.forum_url("forum_reply_edit", own_reply.pk),
            {"body": "Updated guidance."},
        )
        self.assertEqual(accepted.status_code, 302)
        own_reply.refresh_from_db()
        self.assertEqual(own_reply.body, "Updated guidance.")
        self.assertTrue(own_reply.has_been_edited)

        archived = self.client.post(
            self.forum_url("forum_reply_archive", own_reply.pk)
        )
        self.assertEqual(archived.status_code, 302)
        own_reply.refresh_from_db()
        self.assertIsNotNone(own_reply.archived_at)
        self.assertIsNone(other_reply.archived_at)

    def test_department_can_update_archive_and_restore_questions_and_replies(self):
        self.sign_in(self.department)
        response = self.client.post(
            self.forum_url("forum_question_create"),
            {
                "title": "Department managed question",
                "body": "Department owns publication state.",
                "visibility": ForumQuestion.Visibility.DEPARTMENT,
                "departments": [self.cpe.pk],
                "is_published": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        question = ForumQuestion.objects.get(title="Department managed question")
        self.assertFalse(question.is_published)

        response = self.client.post(
            self.forum_url("forum_question_edit", question.pk),
            {
                "title": "Department managed question",
                "body": "Department changed the programme tag.",
                "visibility": ForumQuestion.Visibility.DEPARTMENT,
                "departments": [self.ske.pk],
                "is_published": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        question.refresh_from_db()
        self.assertEqual(list(question.departments.all()), [self.ske])
        self.assertFalse(question.is_published)

        response = self.client.post(
            self.forum_url("forum_question_edit", question.pk),
            {
                "title": "Department updated question",
                "body": "Updated and published.",
                "visibility": ForumQuestion.Visibility.EVERYONE,
                "departments": [],
                "is_published": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        question.refresh_from_db()
        self.assertEqual(question.visibility, ForumQuestion.Visibility.EVERYONE)
        self.assertTrue(question.is_published)

        response = self.client.post(
            self.forum_url("forum_reply_create", question.pk),
            {"body": "Department answer."},
        )
        self.assertEqual(response.status_code, 302)
        reply = question.replies.get()
        self.assertEqual(reply.author_label, "Forum Department")
        self.assertEqual(reply.author_role, "Department")

        response = self.client.post(
            self.forum_url("forum_reply_edit", reply.pk),
            {"body": "Updated Department answer."},
        )
        self.assertEqual(response.status_code, 302)
        reply.refresh_from_db()
        self.assertEqual(reply.body, "Updated Department answer.")

        self.client.post(self.forum_url("forum_question_archive", question.pk))
        self.client.post(self.forum_url("forum_reply_archive", reply.pk))
        question.refresh_from_db()
        reply.refresh_from_db()
        self.assertIsNotNone(question.archived_at)
        self.assertIsNotNone(reply.archived_at)
        self.assertTrue(ForumQuestion.objects.filter(pk=question.pk).exists())

        self.client.post(self.forum_url("forum_question_restore", question.pk))
        self.client.post(self.forum_url("forum_reply_restore", reply.pk))
        question.refresh_from_db()
        reply.refresh_from_db()
        self.assertIsNone(question.archived_at)
        self.assertIsNone(reply.archived_at)

    def test_question_edit_attribution_and_department_label_render(self):
        self.sign_in(self.department)
        response = self.client.post(
            self.forum_url("forum_question_edit", self.everyone_question.pk),
            {
                "title": self.everyone_question.title,
                "body": "Edited by the Department.",
                "visibility": ForumQuestion.Visibility.EVERYONE,
                "departments": [],
                "is_published": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.everyone_question.refresh_from_db()
        self.assertEqual(self.everyone_question.updated_by, self.department)
        self.assertEqual(
            self.everyone_question.updated_by_label,
            self.department.get_full_name(),
        )
        self.assertFalse(self.everyone_question.updated_by_is_author)

        response = self.client.get(
            self.forum_url("faq_board"),
            {"q": self.everyone_question.pk},
        )
        self.assertContains(response, "Updated")
        self.assertContains(response, "(Forum Department)")
        self.assertContains(response, "Comments")
        self.assertContains(response, "Everyone")

    def test_new_question_hides_updated_timestamp_and_shows_department_tag(self):
        self.sign_in(self.student)
        response = self.client.get(
            self.forum_url("faq_board"),
            {"q": self.ske_question.pk},
        )
        self.assertContains(response, "Forum Student")
        self.assertContains(response, "SKE")
        self.assertNotContains(response, "Updated")
        self.assertContains(response, "Comments")

    def test_department_published_checkbox_is_inline_with_label(self):
        self.sign_in(self.department)
        response = self.client.get(
            self.forum_url("forum_question_edit", self.everyone_question.pk)
        )
        self.assertContains(response, "forum-form__field--checkbox")
        self.assertContains(
            response,
            '<label for="id_is_published">Published:</label><input',
        )
        self.assertContains(response, "When checked, this question is visible")

    def test_author_names_and_bangkok_timestamps_render(self):
        reply = ForumReply.objects.create(
            question=self.everyone_question,
            body="Readable reply.",
            author=self.lecturer,
            author_label=self.lecturer.get_full_name(),
        )
        ForumQuestion.objects.filter(pk=self.everyone_question.pk).update(
            updated_at=self.everyone_question.created_at + timedelta(hours=1),
            updated_by=self.department,
            updated_by_label=self.department.get_full_name(),
            updated_by_is_author=False,
        )
        ForumReply.objects.filter(pk=reply.pk).update(
            updated_at=reply.created_at + timedelta(hours=1),
            has_been_edited=True,
        )
        self.sign_in(self.lecturer)
        with timezone.override("Asia/Bangkok"):
            response = self.client.get(
                self.forum_url("faq_board"),
                {"q": self.everyone_question.pk},
            )
            expected_question_time = date_format(
                timezone.localtime(self.everyone_question.created_at),
                "j M Y, H:i",
            )
            expected_reply_time = date_format(
                timezone.localtime(reply.created_at),
                "j M Y, H:i",
            )
        self.assertContains(response, "Forum Student")
        self.assertContains(response, "Forum Lecturer")
        self.assertContains(response, expected_question_time)
        self.assertContains(response, expected_reply_time)
        self.assertContains(response, "Updated")
        self.assertContains(response, "(Forum Department)")

    def test_new_reply_only_displays_posted_timestamp(self):
        reply = ForumReply.objects.create(
            question=self.everyone_question,
            body="A new, unedited reply.",
            author=self.lecturer,
            author_label=self.lecturer.get_full_name(),
            author_role="Lecturer",
        )
        self.sign_in(self.lecturer)
        response = self.client.get(
            self.forum_url("faq_board"),
            {"q": self.everyone_question.pk},
        )
        self.assertContains(response, "Posted")
        self.assertNotContains(response, "Updated")
        self.assertFalse(reply.has_been_edited)

    def test_question_list_is_paginated_and_preserves_selected_thread(self):
        for index in range(21):
            ForumQuestion.objects.create(
                title=f"Pagination question {index}",
                body="Page test.",
                visibility=ForumQuestion.Visibility.EVERYONE,
            )
        self.sign_in(self.lecturer)
        response = self.client.get(
            self.forum_url("faq_board"),
            {"q": self.everyone_question.pk, "page": 1},
        )
        self.assertEqual(len(response.context["threads"]), 20)
        self.assertContains(
            response,
            f"?page=2&amp;q={self.everyone_question.pk}",
        )
        second_page = self.client.get(
            self.forum_url("faq_board"),
            {"q": self.everyone_question.pk, "page": 2},
        )
        self.assertEqual(second_page.context["selected_question"], self.everyone_question)
        invalid_page = self.client.get(
            self.forum_url("faq_board"),
            {"page": "not-a-page"},
        )
        self.assertEqual(invalid_page.status_code, 200)
        self.assertEqual(invalid_page.context["page_obj"].number, 1)

    def test_archive_endpoint_is_post_only(self):
        self.sign_in(self.department)
        response = self.client.get(
            self.forum_url("forum_question_archive", self.everyone_question.pk)
        )
        self.assertEqual(response.status_code, 405)

    def test_only_department_receives_publication_control(self):
        for user in (self.student, self.lecturer):
            self.sign_in(user)
            response = self.client.get(self.forum_url("forum_question_create"))
            self.assertNotIn("is_published", response.context["question_form"].fields)

        self.sign_in(self.department)
        response = self.client.get(self.forum_url("forum_question_create"))
        self.assertIn("is_published", response.context["question_form"].fields)
