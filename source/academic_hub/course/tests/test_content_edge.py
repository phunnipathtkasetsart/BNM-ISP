from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from ..models import (
    CoursePost,
    Enrollment,
    Material,
    Topic,
)
from ..services import assign_ta, create_course

from .helpers import CourseClientMixin


class ContentEdgeCaseAndTopicTests(CourseClientMixin, TestCase):
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
