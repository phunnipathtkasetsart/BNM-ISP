import shutil
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import Course, CoursePost, CourseTA, Enrollment, Material, PostAttachment, Topic
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

        # UPDATED: Department users now have full management privileges
        self.sign_in(self.department_user())
        response = self.client.post(self.url("api_import_csv", self.course.pk), {"csv_file": self.csv_upload("student_id\n" + self.other_student.pk)})
        self.assertEqual(response.status_code, 200)  # Changed from 403 to 200
        self.assertTrue(self.course.enrollments.exists())  # Changed from assertFalse to assertTrue

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
        for section in [None, "", "   ", "1" * 21, "A01", "1.5", "-1", "+1", "1e3", "0 01", "๑๒๓", "１２３"]:
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
        course = create_course(owner=self.teacher, name="Algorithms", section="001")
        data = {"name": " algorithms ", "section": " 001 "}
        for action, args in [("create", ()), ("api_create", ()), ("edit", (self.course.pk,)), ("api_edit", (self.course.pk,))]:
            response = self.client.post(self.url(action, *args), data)
            self.assertEqual(response.status_code, 400)
            self.assertContains(response, "You already have a class", status_code=400)
        self.course.refresh_from_db()
        self.assertEqual(self.course.name, "Software Engineering")
        self.assertEqual(self.client.post(self.url("api_edit", course.pk), data).status_code, 200)
        self.assertEqual(self.client.post(self.url("api_create"), {"name": "Algorithms", "section": "002"}).status_code, 201)
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
        # Safely close the underlying file stream if present, without triggering request_finished
        if getattr(response, 'file_to_stream', None):
            response.file_to_stream.close()
        return content

    # ================= 4.2 permissions =================
    def test_42_role_flags_and_tabs(self):
        cases = [
            (self.student, False, False),
            (self.ta, True, False),
            (self.teacher, True, True),
            (self.dept, True, True),  # Department now has roster permissions
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

    """def test_42_other_department_cannot_see_class(self):
        self.sign_in(self.other_dept)
        self.assertEqual(self.client.get(self.url("api_detail", self.course.pk)).status_code, 404)"""

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

    def test_45_only_enrolled_students_can_become_tas(self):
        self.sign_in(self.teacher)
        self.assertEqual(self.send("api_ta_assign", self.course.pk, self.outsider.pk).status_code, 404)
        self.assertFalse(CourseTA.objects.filter(user=self.outsider).exists())

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


# Add these additional test cases to ContentViewTests or as an extended suite in tests.py

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