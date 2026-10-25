import json
import shutil
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .models import (
    Course,
    CourseAnswer,
    CoursePost,
    CourseQuestion,
    CourseTA,
    Enrollment,
    Material,
    PostAttachment,
    Topic,
)
from .services import assign_ta, create_course
from .validators import validate_upload_size

# Separate temp directories to prevent tearDownClass conflicts between test suites
MEDIA_MODELS = tempfile.mkdtemp(prefix="course-test-media-models-")
MEDIA_VIEWS = tempfile.mkdtemp(prefix="course-test-media-views-")


@override_settings(MEDIA_ROOT=MEDIA_MODELS)
class ContentModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.teacher = User.objects.create_user(
            nisit_id="6610540001", email="t@ku.th", password="x", first_name="T",
            last_name="Test", department="ske", is_staff=True)
        cls.student = User.objects.create_user(
            nisit_id="6610540003", email="s@ku.th", password="x", first_name="S",
            last_name="Test", department="ske")
        cls.course = create_course(owner=cls.teacher, name="Software Engineering", section="001")
        cls.other = create_course(owner=cls.teacher, name="Databases", section="001")

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_MODELS, ignore_errors=True)

    def upload(self, name="Lecture 1.pdf", data=b"hello"):
        return SimpleUploadedFile(name, data, content_type="application/pdf")

    def test_material_keeps_original_name_and_size_but_stores_random_path(self):
        m = Material.objects.create(course=self.course, title="Slides", file=self.upload(),
                                    created_by=self.teacher)
        self.assertEqual(m.original_name, "Lecture 1.pdf")
        self.assertEqual(m.size, 5)
        self.assertTrue(m.file.name.startswith(f"course_files/{self.course.pk}/"))
        self.assertNotIn("Lecture", m.file.name)
        self.assertTrue(m.file.name.endswith(".pdf"))

    def test_attachment_path_uses_the_posts_course(self):
        post = CoursePost.objects.create(course=self.course, author=self.teacher, title="Hi")
        a = PostAttachment.objects.create(post=post, file=self.upload("a.txt"))
        self.assertTrue(a.file.name.startswith(f"course_files/{self.course.pk}/"))
        self.assertEqual(a.original_name, "a.txt")
        self.assertEqual(a.course_id, self.course.pk)

    def test_size_limit_is_enforced_by_validator(self):
        with override_settings(MAX_UPLOAD_BYTES=10):
            with self.assertRaises(ValidationError):
                validate_upload_size(self.upload(data=b"x" * 11))
            validate_upload_size(self.upload(data=b"x" * 10))

    def test_default_limit_is_50_mb(self):
        big = SimpleUploadedFile("big.bin", b"x")
        big.size = 50 * 1024 * 1024 + 1
        with self.assertRaises(ValidationError):
            validate_upload_size(big)
        big.size = 50 * 1024 * 1024
        validate_upload_size(big)

    def test_deleting_row_removes_file_from_storage(self):
        m = Material.objects.create(course=self.course, title="Slides", file=self.upload())
        storage, name = m.file.storage, m.file.name
        self.assertTrue(storage.exists(name))
        with self.captureOnCommitCallbacks(execute=True):
            m.delete()
        self.assertFalse(storage.exists(name))

    def test_deleting_course_removes_its_files_and_content(self):
        post = CoursePost.objects.create(course=self.course, title="Hi")
        a = PostAttachment.objects.create(post=post, file=self.upload("a.txt"))
        m = Material.objects.create(course=self.course, title="Slides", file=self.upload())
        names = [(a.file.storage, a.file.name), (m.file.storage, m.file.name)]
        with self.captureOnCommitCallbacks(execute=True):
            self.course.delete()
        self.assertFalse(CoursePost.objects.exists() or Material.objects.exists()
                         or PostAttachment.objects.exists())
        for storage, name in names:
            self.assertFalse(storage.exists(name))

    def test_deleting_topic_keeps_materials(self):
        topic = Topic.objects.create(course=self.course, title="Week 1")
        m = Material.objects.create(course=self.course, topic=topic, title="S", file=self.upload())
        topic.delete()
        m.refresh_from_db()
        self.assertIsNone(m.topic)

    def test_topic_title_unique_per_course_ignoring_case_and_spaces(self):
        Topic.objects.create(course=self.course, title="Week 1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Topic.objects.create(course=self.course, title="  week 1 ")
        Topic.objects.create(course=self.other, title="Week 1")

    def test_ta_link_unique_per_course_and_user(self):
        CourseTA.objects.create(course=self.course, user=self.student)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CourseTA.objects.create(course=self.course, user=self.student)
        CourseTA.objects.create(course=self.other, user=self.student)

    def test_deleting_author_keeps_post(self):
        post = CoursePost.objects.create(course=self.course, author=self.student, title="Hi")
        self.student.delete()
        post.refresh_from_db()
        self.assertIsNone(post.author)

    def test_posts_and_materials_list_newest_first(self):
        first = CoursePost.objects.create(course=self.course, title="One")
        second = CoursePost.objects.create(course=self.course, title="Two")
        self.assertEqual(list(self.course.posts.all()), [second, first])


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

    def csv_upload(self, text, name="students.csv"):
        return SimpleUploadedFile(name, text.encode("utf-8") if isinstance(text, str) else text, content_type="text/csv")

    def test_csv_import_bom_duplicates_existing_and_repeat(self):
        self.sign_in(self.teacher)
        Enrollment.objects.create(course=self.course, student=self.student)
        text = f'\ufeffstudent_id\r\n"{self.student.pk}"\r\n{self.other_student.pk}\r\n{self.other_student.pk}\r\n\r\n'
        response = self.client.post(self.url("api_import_csv", self.course.pk), {"csv_file": self.csv_upload(text)})
        self.assertEqual(response.json(), {"added": 1, "already_enrolled": 1, "duplicates_skipped": 1, "rejected": []})
        response = self.client.post(self.url("api_import_csv", self.course.pk), {"csv_file": self.csv_upload(text)})
        self.assertEqual(response.json(), {"added": 0, "already_enrolled": 2, "duplicates_skipped": 1, "rejected": []})
        self.assertEqual(self.course.enrollments.count(), 2)

    def test_csv_invalid_files_add_nobody(self):
        self.sign_in(self.teacher)
        for content in ["", "student_id\n", "wrong_header\n" + self.student.pk,
                        f"student_id\n{self.student.pk},extra",
                        'student_id\n"unterminated', b"student_id\n\xff",
                        "student_id\n" + (self.student.pk + "\n") * 501,
                        b"x" * (1024 * 1024 + 1)]:
            with self.subTest(content=str(content)[:60]):
                response = self.client.post(self.url("api_import_csv", self.course.pk), {"csv_file": self.csv_upload(content)})
                self.assertEqual(response.status_code, 400)
                self.assertFalse(self.course.enrollments.exists())
        self.assertEqual(self.client.post(self.url("api_import_csv", self.course.pk), {}).status_code, 400)
        self.assertEqual(self.client.post(self.url("api_import_csv", self.course.pk), {"csv_file": self.csv_upload("student_id\n" + self.student.pk, "students.xlsx")}).status_code, 400)

    def test_csv_department_owner_and_html_error_recovery(self):
        department = self.department_user()
        owned = create_course(owner=department, name="CSV class")
        self.sign_in(department)
        page = self.client.get(self.url("members_page", owned.pk))
        self.assertContains(page, "Import CSV")
        self.assertContains(page, 'enctype="multipart/form-data"')
        response = self.client.post(self.url("import_csv", owned.pk), {"csv_file": self.csv_upload("student_id\nbad")})
        self.assertEqual(response.status_code, 400)
        self.assertTemplateUsed(response, "course/members.html")
        self.assertContains(response, "Line 2", status_code=400)
        response = self.client.post(self.url("import_csv", owned.pk), {"csv_file": self.csv_upload("nisit_id\n" + self.student.pk)})
        self.assertRedirects(response, self.url("members_page", owned.pk))
        self.assertEqual(owned.enrollments.count(), 1)

    def test_csv_permissions_and_csrf(self):
        for user in [self.other_teacher, self.student]:
            self.sign_in(user)
            response = self.client.post(self.url("api_import_csv", self.course.pk), {"csv_file": self.csv_upload("student_id\n" + self.other_student.pk)})
            self.assertEqual(response.status_code, 404)

        self.sign_in(self.department_user())
        response = self.client.post(self.url("api_import_csv", self.course.pk), {"csv_file": self.csv_upload("student_id\n" + self.other_student.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.course.enrollments.exists())

        self.sign_in(self.teacher)
        self.assertEqual(self.client.get(self.url("import_csv", self.course.pk)).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.teacher)
        response = client.post(self.url("import_csv", self.course.pk), {"csv_file": self.csv_upload("student_id\n" + self.student.pk)})
        self.assertEqual(response.status_code, 403)

    def test_csv_preserves_leading_zero_and_rejects_inactive(self):
        student = get_user_model().objects.create_user(nisit_id="0012345678", email="zero@ku.th", first_name="Zero", last_name="Test", department="ske")
        self.sign_in(self.teacher)
        response = self.client.post(self.url("api_import_csv", self.course.pk), {"csv_file": self.csv_upload("student_id\n0012345678")})
        self.assertEqual(response.json()["added"], 1)
        self.assertTrue(self.course.enrollments.filter(student_id="0012345678").exists())
        self.third_student.is_active = False
        self.third_student.save(update_fields=["is_active"])
        response = self.client.post(self.url("api_import_csv", self.course.pk), {"csv_file": self.csv_upload("student_id\n" + self.third_student.pk)})
        self.assertEqual(response.status_code, 400)

    def test_csv_bulk_add_rolls_back_on_database_failure(self):
        self.sign_in(self.teacher)
        original = Enrollment.objects.get_or_create
        count = 0

        def fail_second(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise IntegrityError("simulated failure")
            return original(*args, **kwargs)

        with patch("course.services.Enrollment.objects.get_or_create", side_effect=fail_second):
            with self.assertRaises(IntegrityError):
                self.client.post(self.url("api_import_csv", self.course.pk), {"csv_file": self.csv_upload(f"student_id\n{self.student.pk}\n{self.other_student.pk}")})
        self.assertFalse(self.course.enrollments.exists())

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
            self.assertRedirects(self.client.get(self.url("members_page", self.course.pk)), self.url("dashboard"))
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
        
        # Department users can view detail and members
        for action in ["api_detail", "api_members"]:
            self.assertEqual(self.client.get(self.url(action, self.course.pk)).status_code, 200)
        
        # Department users have full management access over classes
        self.assertEqual(
            self.client.post(self.url("api_edit", self.course.pk), {"name": "Updated Name", "section": "001"}).status_code, 200
        )
        self.assertEqual(
            self.client.post(self.url("api_import_members", self.course.pk), {"student_ids": self.student.pk}).status_code, 200
        )
        self.assertEqual(
            self.client.post(self.url("api_remove_member", self.course.pk, self.student.pk)).status_code, 200
        )
        self.assertEqual(
            self.client.post(self.url("api_delete", self.course.pk)).status_code, 200
        )
        
        # Department users CANNOT join classes as students
        self.assertEqual(
            self.client.post(self.url("api_join"), {"code": self.course.class_code}).status_code, 403
        )

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
            for action in ["api_detail", "api_members"]:
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
            self.assertEqual(self.client.post(self.url(action, self.course.pk), {"section": "001", "name": "No"}).status_code, 403)
        self.assertEqual(self.client.post(self.url("api_remove_member", self.course.pk, self.student.pk)).status_code, 403)

    def test_other_lecturer_cannot_manage(self):
        self.sign_in(self.other_teacher)
        for action in ["api_edit", "api_delete", "api_import_members"]:
            self.assertEqual(self.client.post(self.url(action, self.course.pk), {}).status_code, 404)

    def test_bulk_import_deduplicates_and_reports_existing(self):
        self.sign_in(self.teacher)
        data = {"student_ids": f"{self.student.pk}, {self.other_student.pk}\n{self.student.pk}"}
        response = self.client.post(self.url("api_import_members", self.course.pk), data)
        self.assertEqual(response.json(), {"added": 2, "already_enrolled": 0, "duplicates_skipped": 1, "rejected": []})
        self.assertEqual(self.client.post(self.url("api_import_members", self.course.pk), data).json(), {"added": 0, "already_enrolled": 2, "duplicates_skipped": 1, "rejected": []})

    def test_bulk_import_adds_valid_ids_and_reports_invalid(self):
        self.sign_in(self.teacher)
        for invalid in ["9999999999", self.other_teacher.pk, "bad-id"]:
            response = self.client.post(self.url("api_import_members", self.course.pk), {"student_ids": f"{self.student.pk}, {invalid}"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["rejected"][0]["student_id"], invalid)
            self.assertEqual(self.course.enrollments.count(), 1)

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
        self.assertNotContains(response, "2 students")
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

    def test_unavailable_class_redirects_without_leaking_class_details(self):
        enrollment = Enrollment.objects.create(course=self.course, student=self.student)
        self.sign_in(self.student)
        enrollment.delete()
        for action in ["detail", "members_page", "edit"]:
            response = self.client.get(self.url(action, self.course.pk), follow=True)
            self.assertRedirects(response, self.url("dashboard"))
            self.assertContains(response, "This class is no longer available to you")
            self.assertNotContains(response, self.course.class_code)
        pk = self.course.pk
        self.course.delete()
        self.assertRedirects(self.client.get(self.url("detail", pk)), self.url("dashboard"))
        self.assertEqual(self.client.get(self.url("api_detail", pk)).status_code, 404)

    def test_csv_49_valid_one_invalid(self):
        students = [get_user_model().objects.create_user(
            nisit_id=f"670000{i:04d}", email=f"bulk{i}@ku.th", first_name="Bulk", last_name="Student", department="ske")
            for i in range(49)]
        self.sign_in(self.teacher)
        text = "student_id\n" + "\n".join(u.pk for u in students) + "\ninvalid-id"
        response = self.client.post(self.url("api_import_csv", self.course.pk), {"csv_file": self.csv_upload(text)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["added"], 49)
        self.assertEqual(response.json()["rejected"][0]["row"], 51)
        self.assertEqual(self.course.enrollments.count(), 49)

    def test_partial_import_report_csv_manual_and_retry(self):
        self.sign_in(self.teacher)
        for invalid in ["bad", "9999999999", self.teacher.pk]:
            response = self.client.post(self.url("import_csv", self.course.pk), {"csv_file": self.csv_upload(f"student_id\n{self.student.pk}\n{invalid}")})
            self.assertContains(response, "Import results")
            self.assertContains(response, invalid)
            self.assertEqual(len(response.context["import_report"]["rejected"]), 1)
        response = self.client.post(self.url("import_members", self.course.pk), {"student_ids": f"{self.other_student.pk},bad"})
        self.assertContains(response, "Import results")
        self.assertEqual(response.context["import_report"]["added"], 1)
        self.assertEqual(self.course.enrollments.count(), 2)

    def test_duplicate_create_edit_and_normalization(self):
        self.sign_in(self.teacher)
        course = create_course(owner=self.teacher, name="Algorithms", section="A01")
        data = {"name": " algorithms ", "section": " a01 "}
        for action, args in [("create", ()), ("api_create", ()), ("edit", (self.course.pk,)), ("api_edit", (self.course.pk,))]:
            response = self.client.post(self.url(action, *args), data)
            self.assertEqual(response.status_code, 400)
            self.assertContains(response, "You already have a class", status_code=400)
        self.course.refresh_from_db()
        self.assertEqual(self.course.name, "Software Engineering")
        self.assertEqual(self.client.post(self.url("api_edit", course.pk), data).status_code, 200)
        self.assertEqual(self.client.post(self.url("api_create"), {"name": "Algorithms", "section": "A02"}).status_code, 201)
        self.sign_in(self.other_teacher)
        self.assertEqual(self.client.post(self.url("api_create"), data).status_code, 201)

    def test_database_rejects_duplicate_class(self):
        create_course(owner=self.teacher, name="Databases", section="001")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Course.objects.create(owner=self.teacher, name=" databases ", section="001 ")

    def test_duplicate_race_error_is_a_validation_error(self):
        from django.core.exceptions import ValidationError
        from .services import update_course
        with patch("course.services.duplicate_class", side_effect=[False, True]), patch("course.services.Course.objects.create", side_effect=IntegrityError):
            with self.assertRaises(ValidationError):
                create_course(owner=self.teacher, name="Race", section="001")
        with patch("course.services.duplicate_class", side_effect=[False, True]), patch("django.db.models.query.QuerySet.update", side_effect=IntegrityError):
            with self.assertRaises(ValidationError):
                update_course(self.course, name="Race", section="001")


@override_settings(MEDIA_ROOT=MEDIA_VIEWS)
class ContentViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()

        def make(i, **kwargs):
            return User.objects.create_user(
                nisit_id=f"00000000{i:02d}", email=f"cv{i}@ku.th", password="x",
                first_name=f"User{i}", last_name="Test", **kwargs)

        cls.teacher = make(1, is_staff=True, department="ske")
        cls.dept = make(2, is_superuser=True, department="ske")
        cls.student = make(3, department="ske")
        cls.ta = make(4, department="ske")
        cls.outsider = make(5, department="ske")
        cls.other_teacher = make(6, is_staff=True, department="ske")
        cls.other_dept = make(7, is_superuser=True, department="eng")

        cls.course = create_course(owner=cls.teacher, name="Content Course", section="001")
        cls.other_course = create_course(owner=cls.teacher, name="Second Course", section="002")
        Enrollment.objects.create(course=cls.course, student=cls.student)
        Enrollment.objects.create(course=cls.course, student=cls.ta)
        assign_ta(cls.course, cls.ta.pk)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_VIEWS, ignore_errors=True)

    # ---- helpers ----
    def sign_in(self, user):
        self.client.force_login(user)

    def url(self, action, *args):
        return reverse(f"course:{action}", args=args)

    def send(self, action, *args, **data):
        return self.client.post(self.url(action, *args), data)

    def upload(self, name="file.pdf", data=b"data"):
        return SimpleUploadedFile(name, data, content_type="application/pdf")

    def make_post(self, course=None, author=None, title="Post", file=None):
        post = CoursePost.objects.create(
            course=course or self.course, author=author or self.teacher, title=title)
        if file:
            PostAttachment.objects.create(post=post, file=file)
        return post

    def make_material(self, course=None, topic=None, title="Slides", name="slides.pdf", data=b"data"):
        return Material.objects.create(
            course=course or self.course, topic=topic, title=title,
            file=self.upload(name, data), created_by=self.teacher)

    def body(self, response):
        content = b"".join(response.streaming_content)
        if getattr(response, 'file_to_stream', None):
            response.file_to_stream.close()
        return content

    # ================= 4.2 permissions =================
    def test_42_role_flags_and_tabs(self):
        cases = [
            (self.student, False, False),
            (self.ta, True, False),
            (self.teacher, True, True),
            (self.dept, True, True),
        ]
        for user, content, roster in cases:
            with self.subTest(user=user.pk):
                self.sign_in(user)
                data = self.client.get(self.url("api_detail", self.course.pk)).json()
                self.assertEqual((data["can_manage_content"], data["can_manage_roster"]),
                                 (content, roster))
                html = self.client.get(self.url("detail", self.course.pk))
                self.assertEqual(html.status_code, 200)
                text = html.content.decode()
                self.assertEqual('id="panel-manage"' in text, content)
                self.assertEqual('id="panel-settings"' in text, roster)

    def test_42_outsiders_get_404_on_content_api(self):
        post, material = self.make_post(), self.make_material()
        for user in [self.outsider, self.other_teacher]:
            self.sign_in(user)
            for action, args in [("api_create_post", ()), ("api_upload_materials", ()),
                                 ("api_delete_post", (post.pk,)),
                                 ("api_edit_material", (material.pk,)),
                                 ("api_delete_material", (material.pk,))]:
                with self.subTest(user=user.pk, action=action):
                    self.assertEqual(self.send(action, self.course.pk, *args).status_code, 404)
        self.assertTrue(CoursePost.objects.filter(pk=post.pk).exists())
        self.assertTrue(Material.objects.filter(pk=material.pk).exists())

    def test_42_outsiders_redirected_from_html_downloads(self):
        att = self.make_post(file=self.upload("a.pdf")).attachments.get()
        material = self.make_material()
        for user in [self.outsider, self.other_teacher]:
            self.sign_in(user)
            for response in [
                self.client.get(self.url("download_attachment", self.course.pk, att.pk)),
                self.client.get(self.url("download_material", self.course.pk, material.pk)),
            ]:
                with self.subTest(user=user.pk):
                    self.assertRedirects(response, self.url("dashboard"))

    def test_42_student_cannot_change_content(self):
        post, material = self.make_post(), self.make_material()
        self.sign_in(self.student)
        for action, args in [("api_create_post", ()), ("api_upload_materials", ()),
                             ("api_delete_post", (post.pk,)), ("edit_post", (post.pk,)),
                             ("api_edit_material", (material.pk,)),
                             ("api_delete_material", (material.pk,))]:
            with self.subTest(action=action):
                self.assertEqual(self.send(action, self.course.pk, *args).status_code, 403)
        self.assertEqual(CoursePost.objects.filter(course=self.course).count(), 1)
        self.assertTrue(Material.objects.filter(pk=material.pk).exists())

    def test_42_ta_cannot_manage_roster_class_or_tas(self):
        self.sign_in(self.ta)
        for action, args in [("api_edit", ()), ("api_delete", ()), ("api_import_members", ()),
                             ("api_remove_member", (self.student.pk,)),
                             ("api_ta_assign", (self.student.pk,)),
                             ("api_ta_remove", (self.ta.pk,))]:
            with self.subTest(action=action):
                self.assertEqual(self.send(action, self.course.pk, *args).status_code, 403)
        self.assertEqual(self.course.enrollments.count(), 2)
        self.assertTrue(CourseTA.objects.filter(course=self.course, user=self.ta).exists())

    def test_42_ta_edits_and_deletes_only_own_posts(self):
        theirs = self.make_post(author=self.teacher, title="Teacher post")
        mine = self.make_post(author=self.ta, title="Mine")
        self.sign_in(self.ta)
        self.assertEqual(self.send("api_delete_post", self.course.pk, theirs.pk).status_code, 403)
        self.send("edit_post", self.course.pk, theirs.pk, title="Hacked", body="x")
        theirs.refresh_from_db()
        self.assertEqual(theirs.title, "Teacher post")
        self.send("edit_post", self.course.pk, mine.pk, title="Renamed", body="x")
        mine.refresh_from_db()
        self.assertEqual(mine.title, "Renamed")
        self.assertEqual(self.send("api_delete_post", self.course.pk, mine.pk).status_code, 200)
        self.assertFalse(CoursePost.objects.filter(pk=mine.pk).exists())

    def test_42_owner_and_dept_can_edit_and_delete_any_post(self):
        for user in [self.teacher, self.dept]:
            with self.subTest(user=user.pk):
                post = self.make_post(author=self.ta, title="TA post")
                self.sign_in(user)
                self.send("edit_post", self.course.pk, post.pk, title="Edited", body="x")
                post.refresh_from_db()
                self.assertEqual(post.title, "Edited")
                self.assertEqual(self.send("api_delete_post", self.course.pk, post.pk).status_code, 200)
                self.assertFalse(CoursePost.objects.filter(pk=post.pk).exists())

    def test_42_ta_manages_any_material(self):
        material = self.make_material(title="Teacher file")
        self.sign_in(self.ta)
        resp = self.send("api_upload_materials", self.course.pk, topic="__new",
                         new_topic="TA Topic", files=self.upload("ta.pdf"))
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(Topic.objects.filter(course=self.course, title="TA Topic").exists())
        self.assertEqual(self.send("api_edit_material", self.course.pk, material.pk,
                                   title="By TA", topic="").status_code, 200)
        self.assertEqual(self.send("api_delete_material", self.course.pk, material.pk).status_code, 200)

    def test_42_dept_override_can_manage_roster(self):
        self.sign_in(self.dept)
        self.assertEqual(self.send("api_create_post", self.course.pk, title="Dept", body="x").status_code, 201)
        self.assertEqual(self.send("api_upload_materials", self.course.pk, topic="__new",
                                   new_topic="Dept", files=self.upload()).status_code, 201)
        material = Material.objects.get(course=self.course)
        self.assertEqual(self.send("api_delete_material", self.course.pk, material.pk).status_code, 200)
        for action, args, payload in [
            ("api_ta_assign", (self.student.pk,), {}),
            ("api_edit", (), {"name": "Updated Name", "section": "001"}),
            ("api_import_members", (), {"student_ids": self.student.pk}),
        ]:
            with self.subTest(action=action):
                resp = self.client.post(self.url(action, self.course.pk, *args), payload)
                self.assertIn(resp.status_code, [200, 201])

    def test_42_anonymous_get_and_csrf(self):
        self.assertEqual(self.send("api_create_post", self.course.pk, title="x").status_code, 401)
        self.assertEqual(self.send("create_post", self.course.pk, title="x").status_code, 302)
        self.sign_in(self.teacher)
        self.assertEqual(self.client.get(self.url("create_post", self.course.pk)).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.teacher)
        for action in ["create_post", "upload_materials"]:
            self.assertEqual(client.post(self.url(action, self.course.pk), {"title": "x"}).status_code, 403)
        self.assertFalse(CoursePost.objects.exists())

    # ================= 4.3 announcements =================
    def test_43_create_post_with_attachment_and_student_sees_it(self):
        self.sign_in(self.teacher)
        resp = self.send("api_create_post", self.course.pk, title="Welcome", body="Hello",
                         deadline="2030-01-02T10:30", attachment=self.upload("notes.pdf", b"notes"))
        self.assertEqual(resp.status_code, 201)
        post = CoursePost.objects.get(title="Welcome")
        att = post.attachments.get()
        self.assertEqual(post.author, self.teacher)
        self.assertIsNotNone(post.deadline)
        self.assertEqual((att.original_name, att.size), ("notes.pdf", 5))
        self.sign_in(self.student)
        page = self.client.get(self.url("detail", self.course.pk))
        self.assertContains(page, "Welcome")
        self.assertContains(page, self.url("download_attachment", self.course.pk, att.pk))

    def test_43_edit_post_replace_and_remove_attachment(self):
        """Test replacing and removing announcement attachments during edit."""
        post = self.make_post(file=self.upload("original.pdf", b"old content"))
        old_att = post.attachments.get()
        storage, old_name = old_att.file.storage, old_att.file.name

        self.sign_in(self.teacher)

        # Replace attachment with a new file
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.send("edit_post", self.course.pk, post.pk,
                             title="Post Updated", body="Body",
                             attachment=self.upload("replacement.pdf", b"new content"))
        self.assertEqual(resp.status_code, 302)
        post.refresh_from_db()
        self.assertEqual(post.title, "Post Updated")
        self.assertFalse(storage.exists(old_name))
        new_att = post.attachments.get()
        self.assertEqual(new_att.original_name, "replacement.pdf")

        # Remove attachment without uploading a new one
        new_storage, new_name = new_att.file.storage, new_att.file.name
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.send("edit_post", self.course.pk, post.pk,
                             title="Post No File", body="Body",
                             remove_attachment="true")
        self.assertEqual(resp.status_code, 302)
        post.refresh_from_db()
        self.assertFalse(post.attachments.exists())
        self.assertFalse(new_storage.exists(new_name))

    def test_43_edit_post_with_add_to_materials(self):
        """Test adding replaced attachment to materials during edit_post."""
        post = self.make_post(title="Edit with Material")
        self.sign_in(self.teacher)

        resp = self.send("edit_post", self.course.pk, post.pk,
                         title="Edit with Material", body="Body",
                         attachment=self.upload("edit_mat.pdf", b"data"),
                         add_to_materials="on", topic="__new", new_topic="Edit Topic")
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Material.objects.filter(course=self.course, title="edit_mat.pdf").exists())
        self.assertTrue(Topic.objects.filter(course=self.course, title="Edit Topic").exists())

    def test_43_upcoming_deadlines_7_day_filter(self):
        """Test that upcoming deadlines filter only captures posts within a 7-day future window."""
        now = timezone.now()
        in_3_days = now + timedelta(days=3)
        in_10_days = now + timedelta(days=10)
        past_deadline = now - timedelta(days=1)

        p_valid = CoursePost.objects.create(course=self.course, author=self.teacher, title="Valid", deadline=in_3_days)
        p_far = CoursePost.objects.create(course=self.course, author=self.teacher, title="Far", deadline=in_10_days)
        p_past = CoursePost.objects.create(course=self.course, author=self.teacher, title="Past", deadline=past_deadline)

        self.sign_in(self.student)
        resp = self.client.get(self.url("dashboard"))
        deadlines = list(resp.context["upcoming_deadlines"])
        self.assertIn(p_valid, deadlines)
        self.assertNotIn(p_far, deadlines)
        self.assertNotIn(p_past, deadlines)

    def test_43_post_without_attachment_and_invalid_input(self):
        self.sign_in(self.teacher)
        self.assertEqual(self.send("api_create_post", self.course.pk, title="Plain", body="x").status_code, 201)
        self.assertFalse(CoursePost.objects.get(title="Plain").attachments.exists())
        before = CoursePost.objects.count()
        self.assertEqual(self.send("api_create_post", self.course.pk, title="", body="x").status_code, 400)
        self.assertEqual(self.send("api_create_post", self.course.pk, title="x" * 151).status_code, 400)
        self.assertEqual(CoursePost.objects.count(), before)

    def test_43_oversize_attachment_rejected(self):
        self.sign_in(self.teacher)
        with override_settings(MAX_UPLOAD_BYTES=10):
            resp = self.send("api_create_post", self.course.pk, title="Big",
                             attachment=self.upload("big.bin", b"x" * 11))
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(CoursePost.objects.filter(title="Big").exists())

    def test_43_add_to_materials_shares_one_stored_file(self):
        self.sign_in(self.teacher)
        resp = self.send("api_create_post", self.course.pk, title="Shared", body="x",
                         attachment=self.upload("shared.pdf", b"shared"),
                         add_to_materials="on", topic="__new", new_topic="Week 1")
        self.assertEqual(resp.status_code, 201)
        att = CoursePost.objects.get(title="Shared").attachments.get()
        material = Material.objects.get(course=self.course)
        self.assertEqual(material.file.name, att.file.name)
        self.assertEqual((material.original_name, material.size, material.title),
                         ("shared.pdf", 6, "shared.pdf"))
        self.assertEqual(material.topic.title, "Week 1")

    def test_43_add_to_materials_into_existing_topic(self):
        topic = Topic.objects.create(course=self.course, title="Week 2")
        self.sign_in(self.teacher)
        self.send("api_create_post", self.course.pk, title="P", attachment=self.upload("a.pdf"),
                  add_to_materials="on", topic=topic.pk)
        self.assertEqual(Material.objects.get(course=self.course).topic, topic)

    def test_43_add_to_materials_needs_file_and_topic(self):
        self.sign_in(self.teacher)
        for data in [
            {"attachment": self.upload("a.pdf"), "add_to_materials": "on"},
            {"attachment": self.upload("a.pdf"), "add_to_materials": "on", "topic": "__new", "new_topic": ""},
            {"add_to_materials": "on", "topic": "__new", "new_topic": "X"},
        ]:
            with self.subTest(keys=sorted(data)):
                self.assertEqual(self.send("api_create_post", self.course.pk, title="Bad", **data).status_code, 400)
        self.assertFalse(CoursePost.objects.exists() or Material.objects.exists()
                         or PostAttachment.objects.exists())

    def test_43_deleting_post_removes_unshared_file(self):
        post = self.make_post(file=self.upload("gone.pdf"))
        att = post.attachments.get()
        storage, name = att.file.storage, att.file.name
        self.sign_in(self.teacher)
        with self.captureOnCommitCallbacks(execute=True):
            self.send("api_delete_post", self.course.pk, post.pk)
        self.assertFalse(storage.exists(name))

    def test_43_shared_file_survives_until_last_reference_is_gone(self):
        self.sign_in(self.teacher)
        self.send("api_create_post", self.course.pk, title="Shared", attachment=self.upload("s.pdf"),
                  add_to_materials="on", topic="__new", new_topic="T")
        post = CoursePost.objects.get(title="Shared")
        material = Material.objects.get(course=self.course)
        storage, name = material.file.storage, material.file.name
        with self.captureOnCommitCallbacks(execute=True):
            self.send("api_delete_post", self.course.pk, post.pk)
        self.assertTrue(storage.exists(name))
        with self.captureOnCommitCallbacks(execute=True):
            self.send("api_delete_material", self.course.pk, material.pk)
        self.assertFalse(storage.exists(name))

    def test_43_download_attachment_rules(self):
        att = self.make_post(file=self.upload("notes.pdf", b"NOTES")).attachments.get()
        for user in [self.student, self.ta, self.teacher, self.dept]:
            with self.subTest(user=user.pk):
                self.sign_in(user)
                resp = self.client.get(self.url("download_attachment", self.course.pk, att.pk))
                self.assertEqual(resp.status_code, 200)
                disposition = resp["Content-Disposition"]
                self.assertIn("attachment", disposition)
                self.assertIn("notes.pdf", disposition)
                self.assertNotIn(att.file.name, disposition)
                self.assertEqual(self.body(resp), b"NOTES")

    def test_43_attachment_from_another_class_is_404(self):
        other = self.make_post(course=self.other_course, file=self.upload("o.pdf")).attachments.get()
        self.sign_in(self.teacher)
        self.assertEqual(self.client.get(self.url("download_attachment", self.course.pk, other.pk)).status_code, 404)

    def test_43_missing_file_on_disk_is_404(self):
        att = self.make_post(file=self.upload("x.pdf")).attachments.get()
        att.file.storage.delete(att.file.name)
        self.sign_in(self.student)
        self.assertEqual(self.client.get(self.url("download_attachment", self.course.pk, att.pk)).status_code, 404)

    def test_43_post_from_another_class_cannot_be_deleted_or_edited(self):
        other = self.make_post(course=self.other_course, title="Other")
        self.sign_in(self.teacher)
        self.assertEqual(self.send("api_delete_post", self.course.pk, other.pk).status_code, 404)
        self.assertEqual(self.send("edit_post", self.course.pk, other.pk, title="x").status_code, 404)
        self.assertTrue(CoursePost.objects.filter(pk=other.pk).exists())

    # ================= 4.4 materials =================
    def test_44_upload_multiple_files_new_topic(self):
        self.sign_in(self.teacher)
        resp = self.client.post(self.url("api_upload_materials", self.course.pk), {
            "topic": "__new", "new_topic": "Lectures",
            "files": [self.upload("a.pdf"), self.upload("b.pdf")]})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json(), {"added": 2, "topic": "Lectures"})
        self.assertEqual(Material.objects.filter(course=self.course, topic__title="Lectures").count(), 2)

    def test_44_topic_reuse_ignores_case_and_spaces(self):
        self.sign_in(self.teacher)
        for name in ["Week 1", "  week   1 ", "WEEK 1"]:
            self.send("api_upload_materials", self.course.pk, topic="__new", new_topic=name,
                      files=self.upload())
        self.assertEqual(Topic.objects.filter(course=self.course).count(), 1)
        self.assertEqual(Material.objects.filter(course=self.course).count(), 3)

    def test_44_upload_into_existing_topic_and_foreign_topic_rejected(self):
        mine = Topic.objects.create(course=self.course, title="Mine")
        foreign = Topic.objects.create(course=self.other_course, title="Foreign")
        self.sign_in(self.teacher)
        self.assertEqual(self.send("api_upload_materials", self.course.pk, topic=mine.pk,
                                   files=self.upload()).status_code, 201)
        self.assertEqual(Material.objects.get(course=self.course).topic, mine)
        resp = self.send("api_upload_materials", self.course.pk, topic=foreign.pk, files=self.upload())
        self.assertIn(resp.status_code, (400, 404))
        self.assertEqual(Material.objects.filter(course=self.course).count(), 1)

    def test_44_upload_validation(self):
        self.sign_in(self.teacher)
        for data in [{"topic": "__new", "new_topic": "X"},
                     {"topic": "__new", "new_topic": "", "files": self.upload()},
                     {"files": self.upload()}]:
            with self.subTest(keys=sorted(data)):
                self.assertEqual(self.send("api_upload_materials", self.course.pk, **data).status_code, 400)
        with override_settings(MAX_UPLOAD_BYTES=10):
            resp = self.send("api_upload_materials", self.course.pk, topic="__new", new_topic="X",
                             files=self.upload("big.bin", b"x" * 11))
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Material.objects.exists())

    def test_44_hostile_file_name_never_reaches_the_path(self):
        self.sign_in(self.teacher)
        self.send("api_upload_materials", self.course.pk, topic="__new", new_topic="X",
                  files=self.upload("../../evil.pdf"))
        material = Material.objects.get(course=self.course)
        self.assertTrue(material.file.name.startswith(f"course_files/{self.course.pk}/"))
        self.assertNotIn("..", material.file.name)
        self.assertNotIn("evil", material.file.name)
        self.assertEqual(material.original_name, "evil.pdf")

    def test_44_edit_material(self):
        t1 = Topic.objects.create(course=self.course, title="A")
        t2 = Topic.objects.create(course=self.course, title="B")
        foreign = Topic.objects.create(course=self.other_course, title="F")
        material = self.make_material(topic=t1, title="Old")
        self.sign_in(self.teacher)
        resp = self.send("api_edit_material", self.course.pk, material.pk, title="  New name ", topic=t2.pk)
        self.assertEqual(resp.status_code, 200)
        material.refresh_from_db()
        self.assertEqual((material.title, material.topic), ("New name", t2))
        self.send("api_edit_material", self.course.pk, material.pk, title="New name", topic="")
        material.refresh_from_db()
        self.assertIsNone(material.topic)
        self.assertEqual(self.send("api_edit_material", self.course.pk, material.pk,
                                   title="", topic=t1.pk).status_code, 400)
        resp = self.send("api_edit_material", self.course.pk, material.pk, title="Hack", topic=foreign.pk)
        self.assertIn(resp.status_code, (400, 404))
        material.refresh_from_db()
        self.assertEqual(material.title, "New name")
        self.assertIsNone(material.topic)

    def test_44_delete_material_removes_file(self):
        material = self.make_material()
        storage, name = material.file.storage, material.file.name
        self.sign_in(self.teacher)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.send("api_delete_material", self.course.pk, material.pk).status_code, 200)
        self.assertFalse(Material.objects.filter(pk=material.pk).exists())
        self.assertFalse(storage.exists(name))

    def test_44_download_material_rules(self):
        material = self.make_material(name="slides.pdf", data=b"SLIDES")
        for user in [self.student, self.ta, self.teacher, self.dept]:
            with self.subTest(user=user.pk):
                self.sign_in(user)
                resp = self.client.get(self.url("download_material", self.course.pk, material.pk))
                self.assertEqual(resp.status_code, 200)
                self.assertIn("attachment", resp["Content-Disposition"])
                self.assertIn("slides.pdf", resp["Content-Disposition"])
                self.assertEqual(self.body(resp), b"SLIDES")

    def test_44_material_from_another_class_is_404(self):
        other = self.make_material(course=self.other_course)
        self.sign_in(self.teacher)
        self.assertEqual(self.client.get(self.url("download_material", self.course.pk, other.pk)).status_code, 404)
        self.assertEqual(self.send("api_edit_material", self.course.pk, other.pk, title="x", topic="").status_code, 404)
        self.assertEqual(self.send("api_delete_material", self.course.pk, other.pk).status_code, 404)
        self.assertTrue(Material.objects.filter(pk=other.pk).exists())

    def test_44_student_page_groups_by_topic_and_hides_manage_forms(self):
        topic = Topic.objects.create(course=self.course, title="Week 9")
        self.make_material(topic=topic, title="Grouped file")
        self.make_material(title="Loose file", name="loose.pdf")
        self.sign_in(self.student)
        page = self.client.get(self.url("detail", self.course.pk))
        for text in ["Week 9", "Grouped file", "No topic", "Loose file"]:
            self.assertContains(page, text)
        self.assertNotContains(page, "Upload New Material")
        topic.delete()
        self.assertContains(self.client.get(self.url("detail", self.course.pk)), "Grouped file")
        for user in [self.teacher, self.ta]:
            self.sign_in(user)
            self.assertContains(self.client.get(self.url("detail", self.course.pk)), "Upload New Material")

    # ================= 4.5 teaching assistants =================
    def test_45_assign_and_remove(self):
        self.sign_in(self.teacher)
        self.assertEqual(self.send("api_ta_assign", self.course.pk, self.student.pk).status_code, 201)
        self.assertEqual(self.send("api_ta_assign", self.course.pk, self.student.pk).status_code, 200)
        self.assertEqual(CourseTA.objects.filter(course=self.course, user=self.student).count(), 1)
        members = self.client.get(self.url("api_members", self.course.pk)).json()["students"]
        self.assertTrue(all(m["is_ta"] for m in members))
        self.assertEqual(self.send("api_ta_remove", self.course.pk, self.student.pk).json(), {"removed": True})
        self.assertEqual(self.send("api_ta_remove", self.course.pk, self.student.pk).json(), {"removed": False})

    def test_45_assign_ta_by_nisit_id_or_pk(self):
        """Test assign_ta searching globally by nisit_id or pk."""
        self.sign_in(self.teacher)
        
        # Search by nisit_id (passed as positional argument for URL reversing)
        resp = self.send("ta_assign", self.course.pk, self.outsider.nisit_id)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(CourseTA.objects.filter(course=self.course, user=self.outsider).exists())

        # Search by PK (passed as positional argument for URL reversing)
        resp = self.send("ta_assign", self.course.pk, self.student.pk)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(CourseTA.objects.filter(course=self.course, user=self.student).exists())

    def test_45_assign_unenrolled_student_auto_enrolls_and_becomes_ta(self):
        self.sign_in(self.teacher)
        # Outsider is not enrolled yet
        self.assertFalse(Enrollment.objects.filter(course=self.course, student=self.outsider).exists())
        # Assigning outsider by ID auto-enrolls and assigns TA
        resp = self.send("api_ta_assign", self.course.pk, self.outsider.pk)
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(CourseTA.objects.filter(course=self.course, user=self.outsider).exists())
        self.assertTrue(Enrollment.objects.filter(course=self.course, student=self.outsider).exists())
        # Invalid student ID or staff user cannot become TA
        self.assertEqual(self.send("api_ta_assign", self.course.pk, "invalid_id").status_code, 400)
        self.assertEqual(self.send("api_ta_assign", self.course.pk, self.other_teacher.pk).status_code, 400)

    def test_45_removed_student_loses_ta_and_rejoin_does_not_revive(self):
        self.sign_in(self.teacher)
        self.send("api_remove_member", self.course.pk, self.ta.pk)
        self.assertFalse(CourseTA.objects.filter(course=self.course, user=self.ta).exists())
        Enrollment.objects.create(course=self.course, student=self.ta)
        self.assertFalse(CourseTA.objects.filter(course=self.course, user=self.ta).exists())
        self.sign_in(self.ta)
        self.assertEqual(self.send("api_create_post", self.course.pk, title="x").status_code, 403)

    def test_45_ta_role_is_per_class(self):
        self.sign_in(self.ta)
        self.assertEqual(self.client.get(self.url("api_detail", self.other_course.pk)).status_code, 404)

    def test_admin_dashboard_search_filter(self):
        """Test superuser course search filter on dashboard."""
        self.sign_in(self.dept)
        resp = self.client.get(self.url("dashboard") + "?q=Content")
        self.assertEqual(resp.status_code, 200)
        courses = list(resp.context["courses"])
        self.assertIn(self.course, courses)
        self.assertNotIn(self.other_course, courses)


class ContentEdgeCaseAndTopicTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.teacher = User.objects.create_user(
            nisit_id="6610549901", email="teacher_edge@ku.th", password="x",
            first_name="Prof", last_name="Oak", department="ske", is_staff=True
        )
        cls.student = User.objects.create_user(
            nisit_id="6610549902", email="student_edge@ku.th", password="x",
            first_name="Ash", last_name="Ketchum", department="ske"
        )
        cls.ta_user = User.objects.create_user(
            nisit_id="6610549903", email="ta_edge@ku.th", password="x",
            first_name="Brock", last_name="Rock", department="ske"
        )
        cls.course = create_course(owner=cls.teacher, name="Advanced SE", section="001")
        cls.other_course = create_course(owner=cls.teacher, name="Databases", section="002")
        
        Enrollment.objects.create(course=cls.course, student=cls.student)
        Enrollment.objects.create(course=cls.course, student=cls.ta_user)
        assign_ta(cls.course, cls.ta_user.pk)

    def setUp(self):
        self.client.force_login(self.teacher)

    def url(self, action, *args):
        return reverse(f"course:{action}", args=args)

    def upload(self, name="file.pdf", data=b"data"):
        return SimpleUploadedFile(name, data, content_type="application/pdf")

    def test_unicode_and_thai_filenames_handled_safely(self):
        """Ensures non-ASCII/Thai filenames retain original names and store safely without path issues."""
        thai_filename = "การบ้าน_บทที่1_แคลคูลัส.pdf"
        resp = self.client.post(
            self.url("api_upload_materials", self.course.pk),
            {"topic": "__new", "new_topic": "บทเรียน", "files": self.upload(thai_filename, b"content")}
        )
        self.assertEqual(resp.status_code, 201)
        material = Material.objects.get(course=self.course, title=thai_filename)
        self.assertEqual(material.original_name, thai_filename)
        self.assertTrue(material.file.name.startswith(f"course_files/{self.course.pk}/"))
        
        # Test downloading non-ASCII file
        download_resp = self.client.get(self.url("download_material", self.course.pk, material.pk))
        self.assertEqual(download_resp.status_code, 200)
        self.assertIn("attachment", download_resp["Content-Disposition"])

    def test_edit_post_get_html_view_and_authorization(self):
        """Tests rendering the HTML edit post form and verifying permissions."""
        post = CoursePost.objects.create(course=self.course, author=self.teacher, title="Original Title", body="Body")
        
        # Teacher GET post edit form
        resp = self.client.get(self.url("edit_post", self.course.pk, post.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Original Title")

        # Student GET post edit form should be 403
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(self.url("edit_post", self.course.pk, post.pk)).status_code, 403)

    def test_file_storage_preserved_if_db_deletion_fails(self):
        """Ensures file on disk is not removed if transaction fails/rolls back during DB delete."""
        material = Material.objects.create(
            course=self.course, title="Rollback Test",
            file=self.upload("rollback.pdf", b"data"), created_by=self.teacher
        )
        storage, file_name = material.file.storage, material.file.name
        self.assertTrue(storage.exists(file_name))

        # Simulate DB failure during delete call
        with patch("course.models.Material.delete", side_effect=IntegrityError("DB Error")):
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    material.delete()

        # File must still exist on storage because transaction was aborted
        self.assertTrue(storage.exists(file_name))

    def test_reassigning_material_between_topics(self):
        """Tests moving existing material between topics and unlinking from topics."""
        topic_a = Topic.objects.create(course=self.course, title="Topic A")
        topic_b = Topic.objects.create(course=self.course, title="Topic B")
        material = Material.objects.create(
            course=self.course, topic=topic_a, title="Slide A",
            file=self.upload("slide.pdf"), created_by=self.teacher
        )

        # Reassign to Topic B
        resp = self.client.post(
            self.url("api_edit_material", self.course.pk, material.pk),
            {"title": "Slide A Updated", "topic": topic_b.pk}
        )
        self.assertEqual(resp.status_code, 200)
        material.refresh_from_db()
        self.assertEqual(material.topic, topic_b)

        # Unassign topic
        self.client.post(
            self.url("api_edit_material", self.course.pk, material.pk),
            {"title": "Slide A Loose", "topic": ""}
        )
        material.refresh_from_db()
        self.assertIsNone(material.topic)

    def test_ta_flag_in_members_api_and_context(self):
        """Verifies TA flags render accurately in members list API."""
        resp = self.client.get(self.url("api_members", self.course.pk))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        
        students_dict = {s["id"]: s["is_ta"] for s in data["students"]}
        self.assertTrue(students_dict[self.ta_user.pk])
        self.assertFalse(students_dict[self.student.pk])

    def test_cross_tenant_isolation_on_post_edit(self):
        """Prevents editing or deleting posts belonging to a different course."""
        other_post = CoursePost.objects.create(
            course=self.other_course, author=self.teacher, title="Other Course Post"
        )
        resp = self.client.post(
            self.url("edit_post", self.course.pk, other_post.pk),
            {"title": "Hacked Title", "body": "Hacked Body"}
        )
        self.assertEqual(resp.status_code, 404)
        other_post.refresh_from_db()
        self.assertEqual(other_post.title, "Other Course Post")


class CourseQATests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.teacher = User.objects.create_user(
            nisit_id="6710540001", email="qa-teacher@ku.th", password="pass",
            first_name="Lecturer", last_name="One", department="ske", is_staff=True,
        )
        cls.other_teacher = User.objects.create_user(
            nisit_id="6710540002", email="qa-other-teacher@ku.th", password="pass",
            first_name="Lecturer", last_name="Two", department="ske", is_staff=True,
        )
        cls.student = User.objects.create_user(
            nisit_id="6710540003", email="qa-student@ku.th", password="pass",
            first_name="Student", last_name="One", department="ske",
        )
        cls.other_student = User.objects.create_user(
            nisit_id="6710540004", email="qa-other-student@ku.th", password="pass",
            first_name="Student", last_name="Two", department="ske",
        )
        cls.ta = User.objects.create_user(
            nisit_id="6710540005", email="qa-ta@ku.th", password="pass",
            first_name="Assistant", last_name="One", department="ske",
        )
        cls.department = User.objects.create_user(
            nisit_id="A0001", email="qa-department@ku.th", password="pass",
            first_name="Department", last_name="Admin", department="ske",
            is_superuser=True,
        )
        cls.course = create_course(owner=cls.teacher, name="Q&A Course")
        cls.other_course = create_course(owner=cls.other_teacher, name="Other Q&A Course")
        Enrollment.objects.create(course=cls.course, student=cls.student)
        Enrollment.objects.create(course=cls.course, student=cls.ta)
        Enrollment.objects.create(course=cls.other_course, student=cls.other_student)
        CourseTA.objects.create(course=cls.course, user=cls.ta)

    def sign_in(self, user):
        self.client.force_login(user)

    def url(self, action, *args):
        return reverse(f"course:{action}", args=args)

    def make_question(self, *, course=None, author=None, title="Question", body="Question body"):
        course = course or self.course
        author = author or self.student
        if author.is_superuser:
            role = "Department"
        elif CourseTA.objects.filter(course=course, user=author).exists():
            role = "Teaching Assistant"
        elif author.is_staff:
            role = "Lecturer"
        else:
            role = "Student"
        return CourseQuestion.objects.create(
            course=course,
            author=author,
            author_label=author.get_full_name(),
            author_role_label=role,
            title=title,
            body=body,
        )

    def make_answer(self, question, *, author=None, body="Answer body"):
        author = author or self.teacher
        if author.is_superuser:
            role = "Department"
        elif CourseTA.objects.filter(course=question.course, user=author).exists():
            role = "Teaching Assistant"
        else:
            role = "Lecturer"
        return CourseAnswer.objects.create(
            question=question,
            author=author,
            author_label=author.get_full_name(),
            author_role_label=role,
            body=body,
        )

    def test_anonymous_and_guest_are_blocked_without_qa_data(self):
        question = self.make_question(title="Private Q&A text")
        list_url = self.url("api_qa_questions", self.course.pk)
        self.assertEqual(self.client.get(list_url).status_code, 401)

        self.sign_in(self.student)
        session = self.client.session
        session["is_guest"] = True
        session.save()
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("announcements:public_board"))
        self.assertNotIn(question.title, response.content.decode())

    def test_student_can_post_read_and_see_author_snapshots_and_timestamps(self):
        self.sign_in(self.student)
        response = self.client.post(
            self.url("api_qa_questions", self.course.pk),
            {"title": "  Where is the rubric?  ", "body": "  Please share it.  "},
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["title"], "Where is the rubric?")
        self.assertEqual(data["body"], "Please share it.")
        self.assertEqual(data["author_name"], "Student One")
        self.assertEqual(data["author_role_label"], "Student")
        self.assertTrue(timezone.is_aware(datetime.fromisoformat(data["created_at"])))
        self.assertTrue(data["is_mine"])
        self.assertTrue(data["can_edit"])
        self.assertFalse(data["can_answer"])
        self.assertIn("updated_at", data)
        self.assertIsNone(data["edited_at"])

        question_id = data["id"]
        listing = self.client.get(self.url("api_qa_questions", self.course.pk)).json()
        self.assertEqual(listing["count"], 1)
        self.assertEqual(listing["results"][0]["body_excerpt"], "Please share it.")
        detail = self.client.get(
            self.url("api_qa_question_detail", self.course.pk, question_id),
        ).json()
        self.assertEqual(detail["question"]["body"], "Please share it.")
        self.assertEqual(detail["answers"], [])

    def test_course_detail_exposes_role_aware_sidebar_forum_ui(self):
        self.sign_in(self.student)
        response = self.client.get(self.url("detail", self.course.pk))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('data-api-base="/course/api/', html)
        self.assertIn('data-qa-question-form', html)
        faq_header = html.split('class="course-tool__header"', 2)[2].split("</header>", 1)[0]
        self.assertIn('data-qa-open-ask', faq_header)
        self.assertIn('data-qa-search', html)
        self.assertIn('data-qa-thread', html)
        self.assertIn('course-qa.js', html)
        self.assertIn('course-qa.css', html)
        self.assertNotIn('id="tab-qa"', html)
        self.assertNotIn('Archived (Department)', html)
        self.assertNotIn('id="course-forum-status"', html)

        self.sign_in(self.ta)
        ta_page = self.client.get(self.url("detail", self.course.pk))
        self.assertEqual(ta_page.status_code, 200)
        ta_html = ta_page.content.decode()
        self.assertIn('id="course-forum-status"', ta_html)
        self.assertIn('value="answered"', ta_html)
        self.assertIn('value="unanswered"', ta_html)
        self.assertIn('value="archived"', ta_html)
        self.assertNotIn('value="mine"', ta_html)
        ta_faq_header = ta_html.split('class="course-tool__header"', 2)[2].split("</header>", 1)[0]
        self.assertNotIn('data-qa-open-ask', ta_faq_header)
        self.assertNotIn('data-qa-question-form', ta_html)
        self.assertIn('not course TAs can post a question', ta_html)

        self.sign_in(self.teacher)
        lecturer_page = self.client.get(self.url("detail", self.course.pk))
        self.assertEqual(lecturer_page.status_code, 200)
        lecturer_html = lecturer_page.content.decode()
        self.assertIn('id="course-forum-status"', lecturer_html)
        self.assertIn('value="answered"', lecturer_html)
        self.assertIn('value="unanswered"', lecturer_html)
        self.assertIn('value="archived"', lecturer_html)
        self.assertNotIn('value="mine"', lecturer_html)

        self.sign_in(self.department)
        department_page = self.client.get(self.url("detail", self.course.pk))
        self.assertEqual(department_page.status_code, 200)
        self.assertIn('value="archived"', department_page.content.decode())

    def test_ta_cannot_ask_question_from_ui_or_api(self):
        self.sign_in(self.ta)
        page = self.client.get(self.url("detail", self.course.pk))
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        faq_header = html.split('class="course-tool__header"', 2)[2].split("</header>", 1)[0]
        self.assertNotIn('data-qa-open-ask', faq_header)
        self.assertNotIn('data-qa-question-form', html)

        response = self.client.post(
            self.url("api_qa_questions", self.course.pk),
            {"title": "TA question", "body": "TA should not ask"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("not course TAs", response.json()["error"])
        self.assertFalse(CourseQuestion.objects.filter(title="TA question").exists())

    def test_course_staff_can_list_and_read_archived_questions(self):
        question = self.make_question(title="Archived course question")
        answer = self.make_answer(question, body="Archived course answer")
        archived_at = timezone.now()
        CourseQuestion.objects.filter(pk=question.pk).update(
            archived_at=archived_at,
            archived_by=self.ta,
        )
        CourseAnswer.objects.filter(pk=answer.pk).update(
            archived_at=archived_at,
            archived_by=self.ta,
        )
        list_url = self.url("api_qa_questions", self.course.pk)
        detail_url = self.url("api_qa_question_detail", self.course.pk, question.pk)

        self.sign_in(self.student)
        self.assertEqual(self.client.get(list_url, {"status": "archived"}).status_code, 400)
        self.assertEqual(self.client.get(detail_url).status_code, 404)

        for user in (self.teacher, self.ta):
            with self.subTest(user=user.pk):
                self.sign_in(user)
                listing = self.client.get(list_url, {"status": "archived"})
                self.assertEqual(listing.status_code, 200)
                self.assertEqual(listing.json()["count"], 1)
                listed_question = listing.json()["results"][0]
                self.assertEqual(listed_question["id"], question.pk)
                self.assertIsNotNone(listed_question["archived_at"])
                detail = self.client.get(detail_url)
                self.assertEqual(detail.status_code, 200)
                self.assertIsNotNone(detail.json()["question"]["archived_at"])
                self.assertEqual(len(detail.json()["answers"]), 1)
                archived_answer = detail.json()["answers"][0]
                self.assertEqual(archived_answer["body"], "Archived course answer")
                self.assertIsNotNone(archived_answer["archived_at"])
                can_restore = user == self.ta
                self.assertEqual(detail.json()["question"]["can_restore"], can_restore)
                self.assertEqual(archived_answer["can_restore"], can_restore)

        self.sign_in(self.ta)
        archived_by_ta = self.client.get(detail_url).json()
        self.assertTrue(archived_by_ta["question"]["can_restore"])
        self.assertTrue(archived_by_ta["answers"][0]["can_restore"])

    def test_course_scope_and_students_cannot_answer(self):
        question = self.make_question()
        foreign_question = self.make_question(
            course=self.other_course, author=self.other_student,
        )
        self.sign_in(self.other_student)
        inaccessible = self.client.get(self.url("api_qa_questions", self.course.pk))
        self.assertEqual(inaccessible.status_code, 404)
        self.assertEqual(inaccessible["Content-Type"], "application/json")
        self.assertEqual(
            self.client.get(
                self.url("api_qa_question_detail", self.course.pk, question.pk),
            ).status_code,
            404,
        )
        self.sign_in(self.other_teacher)
        self.assertEqual(
            self.client.get(self.url("api_qa_questions", self.course.pk)).status_code,
            404,
        )
        self.sign_in(self.teacher)
        cross_course = self.client.get(
            self.url("api_qa_question_detail", self.course.pk, foreign_question.pk),
        )
        self.assertEqual(cross_course.status_code, 404)
        self.assertEqual(cross_course["Content-Type"], "application/json")
        self.assertEqual(
            self.client.get(
                self.url("api_qa_question_detail", self.course.pk, 999999),
            )["Content-Type"],
            "application/json",
        )
        self.sign_in(self.student)
        response = self.client.post(
            self.url("api_qa_answer_create", self.course.pk, question.pk),
            {"body": "Student answer"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(question.answers.exists())

    def test_owner_can_answer_pin_and_verify_multiple_answers(self):
        question = self.make_question()
        self.sign_in(self.teacher)
        first = self.client.post(
            self.url("api_qa_answer_create", self.course.pk, question.pk),
            {"body": "Official answer one"},
        )
        second = self.client.post(
            self.url("api_qa_answer_create", self.course.pk, question.pk),
            {"body": "Official answer two"},
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        answer_id = first.json()["answer"]["id"]
        edited = self.client.post(
            self.url("api_qa_answer_edit", self.course.pk, question.pk, answer_id),
            {"body": "Edited official answer one"},
        )
        self.assertEqual(edited.status_code, 200)
        self.assertIsNotNone(edited.json()["answer"]["edited_at"])
        edited_at = CourseAnswer.objects.get(pk=answer_id).edited_at
        edited_at_response = edited.json()["answer"]["edited_at"]
        pin = self.client.post(
            self.url("api_qa_question_pin", self.course.pk, question.pk),
            {"is_pinned": "true"},
        )
        self.assertEqual(pin.status_code, 200)
        self.assertTrue(pin.json()["question"]["is_pinned"])

        for created in [first, second]:
            response = self.client.post(
                self.url(
                    "api_qa_answer_verify", self.course.pk, question.pk,
                    created.json()["answer"]["id"],
                ),
                {"is_verified": "true"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["answer"]["is_verified"])
            self.assertTrue(response.json()["answer"]["verified_at"])

        detail = self.client.get(
            self.url("api_qa_question_detail", self.course.pk, question.pk),
        ).json()
        self.assertTrue(detail["question"]["has_verified_answer"])
        self.assertEqual(detail["question"]["answer_count"], 2)
        self.assertEqual(len([a for a in detail["answers"] if a["is_verified"]]), 2)
        self.assertEqual(detail["answers"][0]["author_name"], "Lecturer One")
        self.assertEqual(detail["answers"][0]["author_role_label"], "Lecturer")
        self.assertIn("created_at", detail["answers"][0])
        self.assertEqual(detail["answers"][0]["edited_at"], edited_at_response)
        self.assertTrue(detail["answers"][0]["can_verify"])
        unverify = self.client.post(
            self.url("api_qa_answer_verify", self.course.pk, question.pk, answer_id),
            {"is_verified": "false"},
        )
        self.assertFalse(unverify.json()["answer"]["is_verified"])
        self.assertIsNone(unverify.json()["answer"]["verified_at"])
        detail = self.client.get(
            self.url("api_qa_question_detail", self.course.pk, question.pk),
        ).json()
        self.assertEqual(
            [answer["id"] for answer in detail["answers"]],
            [second.json()["answer"]["id"], answer_id],
        )
        question.refresh_from_db()
        self.assertIsNone(question.edited_at)
        self.assertEqual(CourseAnswer.objects.get(pk=answer_id).edited_at, edited_at)

    def test_ta_can_answer_pin_verify_archive_and_restore_own_archives(self):
        question = self.make_question()
        self.sign_in(self.ta)
        answer_response = self.client.post(
            self.url("api_qa_answer_create", self.course.pk, question.pk),
            {"body": "TA answer"},
        )
        self.assertEqual(answer_response.status_code, 201)
        answer = answer_response.json()["answer"]
        self.assertEqual(answer["author_role_label"], "Teaching Assistant")
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_pin", self.course.pk, question.pk),
                {"is_pinned": "true"},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                self.url("api_qa_answer_verify", self.course.pk, question.pk, answer["id"]),
                {"is_verified": "true"},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_archive", self.course.pk, question.pk),
            ).status_code,
            200,
        )
        self.sign_in(self.teacher)
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_restore", self.course.pk, question.pk),
            ).status_code,
            403,
        )
        self.sign_in(self.ta)
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_restore", self.course.pk, question.pk),
            ).status_code,
            200,
        )
        question.refresh_from_db()
        self.assertIsNone(question.edited_at)

        self.sign_in(self.teacher)
        self.client.post(
            self.url("api_qa_answer_archive", self.course.pk, question.pk, answer["id"]),
        )
        self.sign_in(self.ta)
        self.assertEqual(
            self.client.post(
                self.url("api_qa_answer_restore", self.course.pk, question.pk, answer["id"]),
            ).status_code,
            403,
        )
        self.assertIsNone(CourseAnswer.objects.get(pk=answer["id"]).edited_at)

    def test_department_can_moderate_across_courses(self):
        question = self.make_question(
            course=self.other_course, author=self.other_student, title="Department scope",
        )
        self.sign_in(self.department)
        self.assertEqual(
            self.client.get(
                self.url("api_qa_question_detail", self.other_course.pk, question.pk),
            ).status_code,
            200,
        )
        answer = self.client.post(
            self.url("api_qa_answer_create", self.other_course.pk, question.pk),
            {"body": "Department response"},
        )
        self.assertEqual(answer.status_code, 201)
        answer_id = answer.json()["answer"]["id"]
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_pin", self.other_course.pk, question.pk),
                {"is_pinned": "true"},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                self.url("api_qa_answer_verify", self.other_course.pk, question.pk, answer_id),
                {"is_verified": "true"},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                self.url("api_qa_answer_edit", self.other_course.pk, question.pk, answer_id),
                {"body": "Department edited response"},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_edit", self.other_course.pk, question.pk),
                {"title": "Department edited question", "body": "Updated"},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_archive", self.other_course.pk, question.pk),
            ).status_code,
            200,
        )
        archived = self.client.get(
            self.url("api_qa_questions", self.other_course.pk),
            {"status": "archived"},
        )
        self.assertEqual(archived.json()["count"], 1)
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_restore", self.other_course.pk, question.pk),
            ).status_code,
            200,
        )

    def test_author_edit_archive_and_department_restores_student_question(self):
        question = self.make_question()
        self.sign_in(self.student)
        edited = self.client.post(
            self.url("api_qa_question_edit", self.course.pk, question.pk),
            {"title": "Edited title", "body": "Edited body"},
        )
        self.assertEqual(edited.status_code, 200)
        question.refresh_from_db()
        self.assertIsNotNone(question.edited_at)
        self.assertEqual(question.title, "Edited title")

        self.client.post(self.url("api_qa_question_archive", self.course.pk, question.pk))
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_restore", self.course.pk, question.pk),
            ).status_code,
            403,
        )
        self.sign_in(self.department)
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_restore", self.course.pk, question.pk),
            ).status_code,
            200,
        )

    def test_student_cannot_edit_question_after_active_answer_but_can_archive(self):
        question = self.make_question()
        self.make_answer(question)
        self.sign_in(self.student)
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_edit", self.course.pk, question.pk),
                {"title": "Too late", "body": "Edited"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_archive", self.course.pk, question.pk),
            ).status_code,
            200,
        )

    def test_course_owner_can_archive_any_and_restore_only_what_they_archived(self):
        question = self.make_question()
        answer = self.make_answer(question)
        self.sign_in(self.teacher)
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_edit", self.course.pk, question.pk),
                {"title": "Owner cannot edit", "body": "No"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                self.url("api_qa_answer_archive", self.course.pk, question.pk, answer.pk),
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                self.url("api_qa_answer_restore", self.course.pk, question.pk, answer.pk),
            ).status_code,
            200,
        )

    def test_archived_items_require_restore_before_moderation(self):
        question = self.make_question()
        answer = self.make_answer(question)
        self.sign_in(self.teacher)
        self.client.post(self.url("api_qa_question_archive", self.course.pk, question.pk))

        self.sign_in(self.student)
        self.assertEqual(
            self.client.get(
                self.url("api_qa_question_detail", self.course.pk, question.pk),
            ).status_code,
            404,
        )
        visible = self.client.get(
            self.url("api_qa_questions", self.course.pk),
        ).json()
        self.assertNotIn(question.pk, [item["id"] for item in visible["results"]])
        self.assertEqual(
            self.client.get(
                self.url("api_qa_questions", self.course.pk),
                {"status": "archived"},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                self.url("api_qa_answer_create", self.course.pk, question.pk),
                {"body": "No answer on archived thread"},
            ).status_code,
            404,
        )

        self.sign_in(self.department)
        detail = self.client.get(
            self.url("api_qa_question_detail", self.course.pk, question.pk),
        )
        self.assertEqual(detail.status_code, 200)
        self.assertFalse(detail.json()["question"]["can_edit"])
        self.assertFalse(detail.json()["question"]["can_answer"])
        for action, args, body in [
            ("api_qa_question_edit", (question.pk,), {"title": "x", "body": "y"}),
            ("api_qa_question_pin", (question.pk,), {"is_pinned": "true"}),
            ("api_qa_answer_create", (question.pk,), {"body": "No"}),
            ("api_qa_answer_edit", (question.pk, answer.pk), {"body": "No"}),
            ("api_qa_answer_verify", (question.pk, answer.pk), {"is_verified": "true"}),
        ]:
            self.assertEqual(
                self.client.post(self.url(action, self.course.pk, *args), body).status_code,
                404,
            )
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_restore", self.course.pk, question.pk),
            ).status_code,
            200,
        )

        self.sign_in(self.teacher)
        self.client.post(
            self.url("api_qa_answer_archive", self.course.pk, question.pk, answer.pk),
        )
        self.sign_in(self.department)
        self.assertEqual(
            self.client.post(
                self.url("api_qa_answer_verify", self.course.pk, question.pk, answer.pk),
                {"is_verified": "true"},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                self.url("api_qa_answer_restore", self.course.pk, question.pk, answer.pk),
            ).status_code,
            200,
        )

    def test_validation_too_long_json_toggles_mutation_methods_and_csrf(self):
        self.sign_in(self.student)
        for title, body in [
            ("   ", "Body"),
            ("x" * 201, "Body"),
            ("Title", "   "),
            ("Title", "x" * 5001),
        ]:
            self.assertEqual(
                self.client.post(
                    self.url("api_qa_questions", self.course.pk),
                    {"title": title, "body": body},
                ).status_code,
                400,
            )

        question = self.make_question()
        self.sign_in(self.teacher)
        answer_url = self.url("api_qa_answer_create", self.course.pk, question.pk)
        self.assertEqual(
            self.client.post(answer_url, {"body": "x" * 5001}).status_code,
            400,
        )
        pin_url = self.url("api_qa_question_pin", self.course.pk, question.pk)
        self.assertEqual(
            self.client.post(
                pin_url, json.dumps({"is_pinned": "true"}),
                content_type="application/json",
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                pin_url, json.dumps({"is_pinned": True}),
                content_type="application/json",
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                pin_url,
                '{"title":',
                content_type="application/json",
            ).status_code,
            400,
        )

        answer = self.make_answer(question)
        mutation_urls = [
            self.url("api_qa_question_edit", self.course.pk, question.pk),
            self.url("api_qa_question_archive", self.course.pk, question.pk),
            self.url("api_qa_question_restore", self.course.pk, question.pk),
            pin_url,
            answer_url,
            self.url("api_qa_answer_edit", self.course.pk, question.pk, answer.pk),
            self.url("api_qa_answer_archive", self.course.pk, question.pk, answer.pk),
            self.url("api_qa_answer_restore", self.course.pk, question.pk, answer.pk),
            self.url("api_qa_answer_verify", self.course.pk, question.pk, answer.pk),
        ]
        for url in mutation_urls:
            self.assertEqual(self.client.get(url).status_code, 405)

        client = Client(enforce_csrf_checks=True)
        client.force_login(self.teacher)
        self.assertEqual(client.post(pin_url, {"is_pinned": "true"}).status_code, 403)

    def test_filters_pagination_search_pinned_and_verified_order(self):
        oldest = self.make_question(title="Old question", body="moon search term")
        answered = self.make_question(title="Answered", body="Has answer")
        newest = self.make_question(title="Newest", body="Other")
        pinned = self.make_question(title="Pinned", body="Other")
        latest_unverified = self.make_question(title="Latest unverified", body="Other")
        CourseQuestion.objects.filter(pk=oldest.pk).update(
            created_at=timezone.now() - timedelta(days=2),
        )
        self.make_answer(answered, body="Answer")
        verified = self.make_answer(newest, body="Verified")
        verified.is_verified = True
        verified.verified_by = self.teacher
        verified.verified_at = timezone.now()
        verified.save(update_fields=["is_verified", "verified_by", "verified_at"])
        pinned.is_pinned = True
        pinned.pinned_at = timezone.now()
        pinned.save(update_fields=["is_pinned", "pinned_at"])

        self.sign_in(self.student)
        listing_url = self.url("api_qa_questions", self.course.pk)
        page = self.client.get(listing_url, {"page": "1", "page_size": "2"}).json()
        self.assertEqual([q["id"] for q in page["results"]], [pinned.pk, newest.pk])
        page_two = self.client.get(listing_url, {"page": "2", "page_size": "2"}).json()
        self.assertEqual(
            [q["id"] for q in page_two["results"]],
            [latest_unverified.pk, answered.pk],
        )
        self.assertEqual(
            self.client.get(listing_url, {"page": "99"}).json()["results"],
            [],
        )
        self.assertTrue(page["results"][1]["has_verified_answer"])
        self.assertEqual(
            self.client.get(listing_url, {"status": "answered"}).json()["count"],
            2,
        )
        self.assertEqual(
            self.client.get(listing_url, {"status": "unanswered"}).json()["count"],
            3,
        )
        self.assertEqual(
            self.client.get(listing_url, {"status": "mine"}).json()["count"],
            5,
        )
        self.assertEqual(
            self.client.get(listing_url, {"q": "MOON"}).json()["results"][0]["id"],
            oldest.pk,
        )
        for params in [{"page": "0"}, {"page_size": "101"}, {"status": "unknown"}]:
            self.assertEqual(self.client.get(listing_url, params).status_code, 400)

        self.sign_in(self.department)
        oldest.archived_at = timezone.now()
        oldest.archived_by = self.department
        oldest.save(update_fields=["archived_at", "archived_by"])
        archived = self.client.get(listing_url, {"status": "archived"}).json()
        self.assertEqual(archived["count"], 1)
        self.assertEqual(archived["results"][0]["id"], oldest.pk)

    def test_question_from_removed_student_remains_visible(self):
        question = self.make_question(title="Question by removed student")
        self.sign_in(self.teacher)
        self.assertEqual(
            self.client.post(
                self.url("api_remove_member", self.course.pk, self.student.pk),
            ).status_code,
            200,
        )
        listing = self.client.get(self.url("api_qa_questions", self.course.pk)).json()
        self.assertEqual(listing["results"][0]["id"], question.pk)
        self.assertEqual(listing["results"][0]["author_name"], "Student One")

    def test_list_query_count_does_not_grow_with_question_count(self):
        self.make_question(title="One")
        self.sign_in(self.student)
        url = self.url("api_qa_questions", self.course.pk)
        with CaptureQueriesContext(connection) as one_question:
            self.client.get(url)
        for index in range(2, 7):
            self.make_question(title=f"Question {index}")
        with CaptureQueriesContext(connection) as several_questions:
            response = self.client.get(url)
        self.assertEqual(response.json()["count"], 6)
        self.assertEqual(len(one_question), len(several_questions))

    def test_notification_hook_uses_on_commit_and_not_for_invalid_write(self):
        self.sign_in(self.student)
        with patch("course.services.qa_notification_hook") as hook:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                response = self.client.post(
                    self.url("api_qa_questions", self.course.pk),
                    {"title": "Committed question", "body": "Body"},
                )
            self.assertEqual(response.status_code, 201)
            self.assertEqual(len(callbacks), 1)
            hook.assert_called_once()

        with patch("course.services.qa_notification_hook") as hook:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                response = self.client.post(
                    self.url("api_qa_questions", self.course.pk),
                    {"title": "   ", "body": "Invalid"},
                )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(callbacks, [])
            hook.assert_not_called()

        question = self.make_question()
        self.sign_in(self.teacher)
        with patch("course.services.qa_notification_hook") as hook:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                response = self.client.post(
                    self.url("api_qa_answer_create", self.course.pk, question.pk),
                    {"body": "Committed answer"},
                )
            self.assertEqual(response.status_code, 201)
            self.assertEqual(len(callbacks), 1)
            hook.assert_called_once()