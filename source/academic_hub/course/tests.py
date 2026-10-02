from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse

from .models import Course, Enrollment
from .services import create_course


class CourseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = []
        for i in range(1, 6):
            users.append(get_user_model().objects.create_user(
                nisit_id=f"661054000{i}", email=f"course{i}@ku.th", password="test-password",
                first_name=f"Person{i}", last_name="Test", department="ske", is_staff=i <= 2))
        cls.teacher, cls.other_teacher, cls.student, cls.other_student, cls.third_student = users
        cls.course = create_course(owner=cls.teacher, name="Software Engineering")
        cls.other_course = create_course(owner=cls.other_teacher, name="Private class")

    def sign_in(self, user):
        self.client.force_login(user)

    def url(self, action, *args):
        return reverse(f"course:{action}", args=args)

    def test_create_code_and_owner_ignore_forged_fields(self):
        self.sign_in(self.teacher)
        response = self.client.post(self.url("api_create"), {"section": "001", "name": " New class ", "owner": self.other_teacher.pk, "class_code": "AAAAAA"})
        self.assertEqual(response.status_code, 201)
        course = Course.objects.get(pk=response.json()["id"])
        self.assertEqual(course.owner, self.teacher)
        self.assertEqual(course.name, "New class")
        self.assertRegex(course.class_code, r"^[A-Z2-9]{6}$")

    def test_student_cannot_create(self):
        self.sign_in(self.student)
        self.assertEqual(self.client.post(self.url("api_create"), {"section": "001", "name": "No"}).status_code, 403)

    def test_separate_members_page_and_return_after_mutations(self):
        self.sign_in(self.teacher)
        detail = self.client.get(self.url("detail", self.course.pk))
        self.assertContains(detail, self.url("members_page", self.course.pk))
        self.assertNotContains(detail, "Student IDs:")
        self.assertNotContains(detail, "Delete class")
        self.assertContains(self.client.get(self.url("edit", self.course.pk)), "Delete class")
        page = self.client.get(self.url("members_page", self.course.pk))
        self.assertContains(page, "Manage members")
        self.assertContains(page, "Student IDs:")
        self.assertNotContains(page, "Delete class")
        response = self.client.post(self.url("import_members", self.course.pk), {"student_ids": self.student.pk})
        self.assertRedirects(response, self.url("members_page", self.course.pk))
        response = self.client.post(self.url("remove_member", self.course.pk, self.student.pk))
        self.assertRedirects(response, self.url("members_page", self.course.pk))

    def test_members_page_preserves_import_errors_and_input(self):
        self.sign_in(self.teacher)
        response = self.client.post(self.url("import_members", self.course.pk), {"student_ids": "invalid-id"})
        self.assertEqual(response.status_code, 400)
        self.assertTemplateUsed(response, "course/members.html")
        self.assertEqual(response.context["roster_form"]["student_ids"].value(), "invalid-id")
        self.assertContains(response, "Invalid 10-digit IDs", status_code=400)

    def test_members_page_access_for_students_outsiders_and_department(self):
        Enrollment.objects.create(course=self.course, student=self.student)
        self.sign_in(self.student)
        self.assertContains(self.client.get(self.url("detail", self.course.pk)), "View members")
        page = self.client.get(self.url("members_page", self.course.pk))
        self.assertContains(page, self.student.get_full_name())
        self.assertNotContains(page, "Add students")
        self.assertNotContains(page, "Remove")
        for user in [self.other_teacher, self.other_student]:
            self.sign_in(user)
            self.assertEqual(self.client.get(self.url("members_page", self.course.pk)).status_code, 404)
        department = self.department_user()
        owned = create_course(owner=department, name="Department class", section="001")
        self.sign_in(department)
        self.assertContains(self.client.get(self.url("members_page", owned.pk)), "Add students")
        self.client.logout()
        self.assertEqual(self.client.get(self.url("members_page", owned.pk)).status_code, 302)

    def test_section_saved_displayed_and_editable(self):
        self.sign_in(self.teacher)
        response = self.client.post(self.url("create"), {"name": "Section class", "section": " 001 "})
        self.assertEqual(response.status_code, 302)
        course = Course.objects.get(name="Section class")
        self.assertEqual(course.section, "001")
        self.assertContains(self.client.get(self.url("dashboard")), "Sec 001")
        self.assertContains(self.client.get(self.url("detail", course.pk)), "Sec 001")
        edit = self.client.get(self.url("edit", course.pk))
        self.assertEqual(edit.context["form"].initial["section"], "001")
        response = self.client.post(self.url("api_edit", course.pk), {"name": course.name, "section": "002"})
        self.assertEqual(response.json()["section"], "002")
        course.refresh_from_db()
        self.assertEqual(course.section, "002")
        self.sign_in(self.student)
        joined = self.client.post(self.url("api_join"), {"code": course.class_code})
        self.assertEqual(joined.json()["course"]["section"], "002")
        self.assertContains(self.client.get(self.url("dashboard")), "Sec 002")

    def test_section_is_required_and_limited_on_create_and_edit(self):
        self.sign_in(self.teacher)
        for section in [None, "", "   ", "x" * 21]:
            data = {"name": "Valid name"}
            if section is not None:
                data["section"] = section
            for action, args in [("api_create", ()), ("api_edit", (self.course.pk,))]:
                response = self.client.post(self.url(action, *args), data)
                self.assertEqual(response.status_code, 400)
                self.assertIn("section", response.json()["errors"])
        self.course.refresh_from_db()
        self.assertEqual(self.course.name, "Software Engineering")

    def test_existing_class_with_no_section_still_renders(self):
        self.sign_in(self.teacher)
        self.assertEqual(self.course.section, "")
        self.assertEqual(self.client.get(self.url("detail", self.course.pk)).status_code, 200)
        self.assertEqual(self.client.get(self.url("dashboard")).status_code, 200)

    def department_user(self):
        return get_user_model().objects.create_user(
            nisit_id="A0001", email="department-test@ku.th", password="test-password",
            first_name="Department", last_name="Test", department="ske",
            is_superuser=True, is_staff=False)

    def test_department_can_create_and_manage_own_class(self):
        department = self.department_user()
        self.sign_in(department)
        dashboard = self.client.get(self.url("dashboard"))
        self.assertContains(dashboard, "Create class")
        self.assertContains(dashboard, '<span class="topbar__role">Department</span>', html=True)
        self.assertContains(dashboard, "Manage roles")
        self.assertNotContains(dashboard, "Join class")
        response = self.client.post(self.url("api_create"), {"section": "001", "name": "Department class"})
        self.assertEqual(response.status_code, 201)
        course = Course.objects.get(pk=response.json()["id"])
        self.assertEqual(course.owner, department)
        self.assertEqual(response.json()["class_code"], course.class_code)
        detail = self.client.get(self.url("detail", course.pk))
        self.assertContains(detail, "Manage members")
        self.assertContains(detail, course.class_code)
        self.assertEqual(self.client.post(self.url("api_edit", course.pk), {"section": "001", "name": "Renamed"}).json()["name"], "Renamed")
        added = self.client.post(self.url("api_import_members", course.pk), {"student_ids": self.student.pk})
        self.assertEqual(added.json()["added"], 1)
        self.assertEqual(self.client.get(self.url("api_members", course.pk)).status_code, 200)
        self.assertEqual(self.client.post(self.url("api_remove_member", course.pk, self.student.pk)).status_code, 200)
        self.assertEqual(self.client.post(self.url("api_delete", course.pk)).status_code, 200)
        self.assertFalse(Course.objects.filter(pk=course.pk).exists())

    def test_department_cannot_manage_other_owners_or_join_as_student(self):
        self.sign_in(self.department_user())
        for action in ["api_detail", "api_members"]:
            self.assertEqual(self.client.get(self.url(action, self.course.pk)).status_code, 404)
        for action in ["api_edit", "api_delete", "api_import_members"]:
            self.assertEqual(self.client.post(self.url(action, self.course.pk), {}).status_code, 404)
        self.assertEqual(self.client.post(self.url("api_remove_member", self.course.pk, self.student.pk)).status_code, 404)
        self.assertEqual(self.client.post(self.url("api_join"), {"code": self.course.class_code}).status_code, 403)

    def test_blank_and_overlong_names_rejected(self):
        self.sign_in(self.teacher)
        for name in ["   ", "x" * 121]:
            self.assertEqual(self.client.post(self.url("api_create"), {"section": "001", "name": name}).status_code, 400)

    def test_code_collision_retries(self):
        with patch("course.services.generate_class_code", side_effect=[self.course.class_code, "XYZ234"]):
            created = create_course(owner=self.teacher, name="Retry")
        self.assertEqual(created.class_code, "XYZ234")

    def test_join_normalizes_code_and_is_idempotent(self):
        self.sign_in(self.student)
        for status in [201, 200]:
            response = self.client.post(self.url("api_join"), {"code": self.course.class_code.lower()})
            self.assertEqual(response.status_code, status)
        self.assertEqual(self.course.enrollments.count(), 1)
        self.assertNotIn("class_code", response.json()["course"])

    def test_invalid_and_unknown_codes(self):
        self.sign_in(self.student)
        for code in ["", "ABC", "abcdefg", "@@@@@@", "000000"]:
            self.assertEqual(self.client.post(self.url("api_join"), {"code": code}).status_code, 400)
        self.assertFalse(self.course.enrollments.exists())

    def test_lecturer_cannot_join_as_student(self):
        self.sign_in(self.other_teacher)
        self.assertEqual(self.client.post(self.url("api_join"), {"code": self.course.class_code}).status_code, 403)

    def test_outsiders_cannot_read_class_or_roster(self):
        for user in [self.student, self.other_teacher]:
            self.sign_in(user)
            for action in ["api_detail", "api_members", "detail"]:
                self.assertEqual(self.client.get(self.url(action, self.course.pk)).status_code, 404)

    def test_student_reads_roster_but_cannot_manage(self):
        Enrollment.objects.create(course=self.course, student=self.student)
        self.sign_in(self.student)
        response = self.client.get(self.url("api_members", self.course.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["students"][0]["id"], self.student.pk)
        html = self.client.get(self.url("detail", self.course.pk))
        self.assertNotContains(html, "Class members")
        self.assertNotContains(html, self.course.class_code)
        self.assertNotContains(html, "Add students")
        for action in ["api_edit", "api_delete", "api_import_members"]:
            self.assertEqual(self.client.post(self.url(action, self.course.pk), {"section": "001", "name": "No"}).status_code, 404)
        self.assertEqual(self.client.post(self.url("api_remove_member", self.course.pk, self.student.pk)).status_code, 404)

    def test_other_lecturer_cannot_manage(self):
        self.sign_in(self.other_teacher)
        for action in ["api_edit", "api_delete", "api_import_members"]:
            self.assertEqual(self.client.post(self.url(action, self.course.pk), {}).status_code, 404)

    def test_bulk_import_deduplicates_and_reports_existing(self):
        self.sign_in(self.teacher)
        data = {"student_ids": f"{self.student.pk}, {self.other_student.pk}\n{self.student.pk}"}
        response = self.client.post(self.url("api_import_members", self.course.pk), data)
        self.assertEqual(response.json(), {"added": 2, "already_enrolled": 0})
        self.assertEqual(self.client.post(self.url("api_import_members", self.course.pk), data).json(), {"added": 0, "already_enrolled": 2})

    def test_bulk_import_all_or_nothing(self):
        self.sign_in(self.teacher)
        for invalid in ["9999999999", self.other_teacher.pk, "bad-id"]:
            response = self.client.post(self.url("api_import_members", self.course.pk), {"student_ids": f"{self.student.pk}, {invalid}"})
            self.assertEqual(response.status_code, 400)
            self.assertFalse(self.course.enrollments.exists())

    def test_inactive_student_rejected(self):
        self.third_student.is_active = False
        self.third_student.save(update_fields=["is_active"])
        self.sign_in(self.teacher)
        self.assertEqual(self.client.post(self.url("api_import_members", self.course.pk), {"student_ids": self.third_student.pk}).status_code, 400)

    def test_remove_revokes_access_and_keeps_account(self):
        Enrollment.objects.create(course=self.course, student=self.student)
        self.sign_in(self.teacher)
        self.assertEqual(self.client.post(self.url("api_remove_member", self.course.pk, self.student.pk)).status_code, 200)
        self.assertTrue(get_user_model().objects.filter(pk=self.student.pk).exists())
        self.sign_in(self.student)
        self.assertEqual(self.client.get(self.url("api_detail", self.course.pk)).status_code, 404)

    def test_remove_is_scoped_to_class(self):
        Enrollment.objects.create(course=self.other_course, student=self.student)
        self.sign_in(self.teacher)
        self.assertEqual(self.client.post(self.url("api_remove_member", self.course.pk, self.student.pk)).status_code, 404)
        self.assertTrue(self.other_course.enrollments.exists())

    def test_edit_delete_and_cascade(self):
        Enrollment.objects.create(course=self.course, student=self.student)
        self.sign_in(self.teacher)
        self.assertEqual(self.client.post(self.url("api_edit", self.course.pk), {"section": "001", "name": "Updated"}).json()["name"], "Updated")
        self.assertEqual(self.client.post(self.url("api_delete", self.course.pk)).status_code, 200)
        self.assertFalse(Enrollment.objects.filter(course_id=self.course.pk).exists())
        self.assertTrue(Course.objects.filter(pk=self.other_course.pk).exists())

    def test_dashboard_only_shows_enrolled_and_owned_classes(self):
        Enrollment.objects.create(course=self.course, student=self.student)
        Enrollment.objects.create(course=self.course, student=self.other_student)
        self.sign_in(self.student)
        response = self.client.get(self.url("dashboard"))
        self.assertContains(response, "Software Engineering")
        self.assertContains(response, "2 students")
        self.assertNotContains(response, "Private class")
        self.assertContains(response, "Join class")
        self.sign_in(self.teacher)
        self.assertContains(self.client.get(self.url("dashboard")), "Create class")

    def test_anonymous_and_guest_blocked(self):
        self.assertEqual(self.client.get(self.url("api_list")).status_code, 401)
        self.assertEqual(self.client.get(self.url("dashboard")).status_code, 302)
        self.sign_in(self.student)
        session = self.client.session
        session["is_guest"] = True
        session.save()
        self.assertRedirects(self.client.get(self.url("dashboard")), reverse("announcements:public_board"), fetch_redirect_response=False)

    def test_mutations_require_post_and_csrf(self):
        self.sign_in(self.teacher)
        for action in ["delete", "import_members"]:
            self.assertEqual(self.client.get(self.url(action, self.course.pk)).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.teacher)
        self.assertEqual(client.post(self.url("api_create"), {"section": "001", "name": "No CSRF"}).status_code, 403)

    def test_json_validation(self):
        self.sign_in(self.teacher)
        self.assertEqual(self.client.post(self.url("api_create"), {"section": "001", "name": "JSON course"}, content_type="application/json").status_code, 201)
        for body in ['{"name":', '[]', '{"section": "001", "name": ["x"]}']:
            self.assertEqual(self.client.post(self.url("api_create"), body, content_type="application/json").status_code, 400)

    def test_database_rejects_duplicate_membership(self):
        Enrollment.objects.create(course=self.course, student=self.student)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Enrollment.objects.create(course=self.course, student=self.student)

    def test_html_forms_and_error_recovery(self):
        self.sign_in(self.teacher)
        for action, args in [("create", ()), ("edit", (self.course.pk,))]:
            self.assertEqual(self.client.get(self.url(action, *args)).status_code, 200)
        response = self.client.post(self.url("import_members", self.course.pk), {"student_ids": "bad"})
        self.assertContains(response, "Invalid 10-digit IDs", status_code=400)
        self.assertContains(self.client.get(self.url("detail", self.course.pk)), self.course.class_code)
        self.sign_in(self.student)
        self.assertEqual(self.client.get(self.url("join")).status_code, 200)
