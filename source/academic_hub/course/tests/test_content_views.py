import shutil
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from ..models import (
    CoursePost,
    CourseTA,
    Enrollment,
    Material,
    PostAttachment,
    Topic,
)
from ..services import assign_ta, create_course

from .helpers import CourseClientMixin

MEDIA_VIEWS = tempfile.mkdtemp(prefix="course-test-media-views-")


@override_settings(MEDIA_ROOT=MEDIA_VIEWS)
class ContentViewTests(CourseClientMixin, TestCase):
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
    def send(self, action, *args, **data):
        return self.client.post(self.url(action, *args), data)

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

    def attachment_names(self, post):
        return sorted(post.attachments.values_list("original_name", flat=True))

    def test_43_edit_post_adds_replaces_and_removes_attachments(self):
        """Uploads are added; a file is replaced by removing it and uploading another."""
        post = self.make_post(file=self.upload("original.pdf", b"old content"))
        old_att = post.attachments.get()
        storage, old_name = old_att.file.storage, old_att.file.name
        self.sign_in(self.teacher)

        # Uploading keeps the existing file and adds the new one.
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.send("edit_post", self.course.pk, post.pk, title="Post Updated", body="Body",
                             attachment=self.upload("added.pdf", b"new content"))
        self.assertEqual(resp.status_code, 302)
        post.refresh_from_db()
        self.assertEqual(post.title, "Post Updated")
        self.assertEqual(self.attachment_names(post), ["added.pdf", "original.pdf"])
        self.assertTrue(storage.exists(old_name))

        # Replace: remove the old file by id and upload its replacement in one save.
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.send("edit_post", self.course.pk, post.pk, title="Post Updated", body="Body",
                             remove_attachment_ids=[old_att.pk],
                             attachment=self.upload("replacement.pdf", b"newer content"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.attachment_names(post), ["added.pdf", "replacement.pdf"])
        self.assertFalse(storage.exists(old_name))

        # remove_attachment=true still removes every file.
        names = [(a.file.storage, a.file.name) for a in post.attachments.all()]
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.send("edit_post", self.course.pk, post.pk, title="Post No File", body="Body",
                             remove_attachment="true")
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(post.attachments.exists())
        for file_storage, name in names:
            self.assertFalse(file_storage.exists(name))

    def test_43_edit_post_removes_only_selected_files_and_adds_many(self):
        post = self.make_post()
        keep, drop_one, drop_two = (
            PostAttachment.objects.create(post=post, file=self.upload(f"{name}.pdf"))
            for name in ("keep", "drop1", "drop2")
        )
        self.sign_in(self.teacher)
        resp = self.send("edit_post", self.course.pk, post.pk, title="Post", body="Body",
                         remove_attachment_ids=[drop_one.pk, drop_two.pk],
                         attachment=[self.upload("new1.pdf"), self.upload("new2.pdf")])
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.attachment_names(post), ["keep.pdf", "new1.pdf", "new2.pdf"])

    def test_43_edit_post_rejects_other_posts_attachment_ids(self):
        other = self.make_post(title="Other", file=self.upload("other.pdf"))
        post = self.make_post(file=self.upload("mine.pdf"))
        self.sign_in(self.teacher)
        resp = self.send("edit_post", self.course.pk, post.pk, title="Post", body="Body",
                         remove_attachment_ids=[other.attachments.get().pk])
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(other.attachments.exists())
        self.assertTrue(post.attachments.exists())

    def test_43_edit_post_limit_counts_kept_and_new_files(self):
        # One existing file + two new ones = 3, over a limit of 2.
        post = self.make_post(file=self.upload("one.pdf"))
        self.sign_in(self.teacher)
        with patch("course.forms.MAX_POST_FILES", 2):
            resp = self.send("edit_post", self.course.pk, post.pk, title="Post", body="Body",
                             attachment=[self.upload("two.pdf"), self.upload("three.pdf")])
            self.assertEqual(resp.status_code, 400)
            self.assertEqual(self.attachment_names(post), ["one.pdf"])
            # Removing the old file makes room for both new ones.
            resp = self.send("edit_post", self.course.pk, post.pk, title="Post", body="Body",
                             remove_attachment_ids=[post.attachments.get().pk],
                             attachment=[self.upload("two.pdf"), self.upload("three.pdf")])
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.attachment_names(post), ["three.pdf", "two.pdf"])

    def test_43_edit_post_blocks_dangerous_files_like_create(self):
        post = self.make_post(title="Guarded")
        self.sign_in(self.teacher)
        for name, data in [("evil.exe", b"MZ data"), ("run.bat", b"echo"), ("fake.pdf", b"MZ data")]:
            with self.subTest(name=name):
                resp = self.send("edit_post", self.course.pk, post.pk, title="Changed", body="Body",
                                 attachment=self.upload(name, data))
                self.assertEqual(resp.status_code, 400)
                post.refresh_from_db()
                self.assertEqual(post.title, "Guarded")
                self.assertFalse(post.attachments.exists())
                # The reason is shown, and the New announcement form is not filled with the edit.
                self.assertContains(resp, "Failed to update announcement.", status_code=400)
                self.assertFalse(resp.context["post_form"].is_bound)

    def test_43_edit_form_has_the_same_file_guardrails_as_create(self):
        self.make_post(title="Has editor")
        self.sign_in(self.teacher)
        html = self.client.get(self.url("detail", self.course.pk)).content.decode()
        edit_form = html[html.index("data-post-editform"):]
        edit_form = edit_form[:edit_form.index("</form>")]
        self.assertIn("data-cc-compose", edit_form)
        self.assertIn("data-file-error", edit_form)
        self.assertIn('accept=".pdf,', edit_form)
        self.assertIn("multiple", edit_form)

    def test_43_edit_post_notifies_only_when_deadline_changes(self):
        post = self.make_post(title="Deadline post")
        self.sign_in(self.teacher)
        cases = [("2026-12-01T10:00", 1), ("2026-12-01T10:00", 0), ("2026-12-02T09:30", 1), ("", 0)]
        for deadline, expected_calls in cases:
            with self.subTest(deadline=deadline), patch("course.views.posts.notify_deadline") as notify:
                resp = self.send("edit_post", self.course.pk, post.pk,
                                 title="Deadline post", body="Body", deadline=deadline)
                self.assertEqual(resp.status_code, 302)
                self.assertEqual(notify.call_count, expected_calls)

    def test_43_edit_post_announces_every_new_material(self):
        post = self.make_post(title="Edit with Materials")
        self.sign_in(self.teacher)
        with patch("course.views.posts.notify_course_material") as notify:
            resp = self.send("edit_post", self.course.pk, post.pk, title="Edit with Materials", body="Body",
                             attachment=[self.upload("a.pdf"), self.upload("b.pdf")],
                             add_to_materials="on", topic="__new", new_topic="Week 1")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(notify.call_count, 2)

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
