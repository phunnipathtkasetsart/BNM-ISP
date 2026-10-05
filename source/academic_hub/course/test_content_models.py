import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from .models import Course, CoursePost, CourseTA, Material, PostAttachment, Topic
from .services import create_course
from .validators import validate_upload_size

MEDIA = tempfile.mkdtemp(prefix="course-test-media-")


@override_settings(MEDIA_ROOT=MEDIA)
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
        shutil.rmtree(MEDIA, ignore_errors=True)

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
