import struct
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from accounts.middleware import GUEST_SESSION_KEY
from announcements.models import Tag

from .forms import parse_tags
from .models import Short
from .validators import clip_duration


def _box(kind, payload):
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def mp4_bytes(seconds, timescale=1000):
    """The smallest MP4 that states a length: ftyp, then moov/mvhd.

    Enough for the length check, which reads only the header, so the tests
    need no real video and no ffmpeg.
    """
    ftyp = _box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2avc1mp41")
    mvhd = _box(
        b"mvhd",
        struct.pack(">B3xIIII", 0, 0, 0, timescale, int(round(seconds * timescale)))
        + struct.pack(">IH10x", 0x00010000, 0x0100)
        + b"\0" * 60
        + struct.pack(">I", 2),
    )
    return ftyp + _box(b"moov", mvhd) + _box(b"mdat", b"\0" * 64)


def clip(seconds=30, name="lesson.mp4", content_type="video/mp4"):
    return SimpleUploadedFile(name, mp4_bytes(seconds), content_type=content_type)


class Users:
    @classmethod
    def make_users(cls):
        User = get_user_model()
        cls.student = User.objects.create_user(
            nisit_id="6610540201", email="short.student@ku.th", password="x",
            first_name="Short", last_name="Student", department="ske")
        cls.lecturer = User.objects.create_user(
            nisit_id="6610540202", email="short.lecturer@ku.th", password="x",
            first_name="Short", last_name="Lecturer", department="ske",
            is_staff=True)
        cls.department = User.objects.create_user(
            nisit_id="6610540203", email="short.dept@ku.th", password="x",
            first_name="Short", last_name="Dept", department="ske",
            is_superuser=True)


class ShortsTabTests(Users, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.make_users()

    def test_anonymous_user_is_sent_to_login(self):
        response = self.client.get(reverse("shorts:feed"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_guest_is_sent_to_public_board(self):
        self.client.force_login(self.student)
        session = self.client.session
        session[GUEST_SESSION_KEY] = True
        session.save()
        response = self.client.get(reverse("shorts:feed"))
        self.assertRedirects(response, reverse("announcements:public_board"),
                             fetch_redirect_response=False)

    def test_student_sees_tab_but_no_create_button(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("shorts:feed"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-current="page">Shorts</a>', html=False)
        self.assertNotContains(response, "Create Short")
        self.assertNotContains(response, reverse("shorts:create"))

    def test_lecturer_and_department_see_create_button(self):
        for user in (self.lecturer, self.department):
            with self.subTest(user=user.nisit_id):
                self.client.force_login(user)
                response = self.client.get(reverse("shorts:feed"))
                self.assertContains(response, "Create Short")
                self.assertContains(response, reverse("shorts:create"))

    def test_student_cannot_open_create_page(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("shorts:create"), follow=True)
        self.assertRedirects(response, reverse("shorts:feed"))
        self.assertContains(response, "Only lecturers and the Department can create shorts.")

    def test_lecturer_sees_the_form(self):
        self.client.force_login(self.lecturer)
        response = self.client.get(reverse("shorts:create"))
        self.assertEqual(response.status_code, 200)
        for name in ("title", "description", "tags", "clip"):
            self.assertContains(response, f'name="{name}"')
        self.assertContains(response, 'enctype="multipart/form-data"')

    def test_shorts_tab_is_on_the_other_tab_bars(self):
        self.client.force_login(self.student)
        for name in ("announcements:public_board", "course:dashboard", "calendar:month"):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertContains(response, f'href="{reverse("shorts:feed")}"')


class InMemoryClips:
    """Point the clip field at memory, so tests never write to MinIO or disk."""

    def setUp(self):
        super().setUp()
        self.storage = InMemoryStorage()
        patcher = patch.object(Short._meta.get_field("clip"), "storage", self.storage)
        patcher.start()
        self.addCleanup(patcher.stop)


class CreateShortTests(InMemoryClips, Users, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.make_users()
        # Created by announcements migration 0004 as a programme tag.
        cls.ske = Tag.objects.get(slug="ske")

    def post(self, user=None, **overrides):
        self.client.force_login(user or self.lecturer)
        data = {"title": "Big-O in 60 seconds", "description": "Why loops nest.",
                "tags": "recursion, #SKE", "clip": clip(60)}
        data.update(overrides)
        return self.client.post(reverse("shorts:create"), data)

    def test_lecturer_upload_is_stored_with_its_details(self):
        response = self.post()
        self.assertRedirects(response, reverse("shorts:feed"), fetch_redirect_response=False)

        short = Short.objects.get()
        self.assertEqual(short.title, "Big-O in 60 seconds")
        self.assertEqual(short.description, "Why loops nest.")
        self.assertEqual(short.duration_seconds, 60)
        self.assertEqual(short.author, self.lecturer)
        self.assertEqual(short.author_label, "Lecturer")
        self.assertEqual(short.original_name, "lesson.mp4")
        self.assertEqual(short.size, len(mp4_bytes(60)))
        # The bytes reached storage, under a random name, not the uploader's.
        self.assertTrue(self.storage.exists(short.clip.name))
        self.assertRegex(short.clip.name, r"^clips/[0-9a-f]{32}\.mp4$")
        with self.storage.open(short.clip.name) as stored:
            self.assertEqual(stored.read(), mp4_bytes(60))

    def test_department_upload_is_labelled_department(self):
        self.post(self.department)
        self.assertEqual(Short.objects.get().author_label, "Department")

    def test_tags_reuse_existing_and_create_topics(self):
        self.post()
        tags = {t.slug: t for t in Short.objects.get().tags.all()}
        self.assertEqual(set(tags), {"recursion", "ske"})
        self.assertEqual(tags["ske"].pk, self.ske.pk)  # matched, not duplicated
        self.assertEqual(tags["recursion"].kind, Tag.Kind.TOPIC)
        self.assertEqual(tags["recursion"].label, "recursion")

    def test_clip_of_exactly_ninety_seconds_is_accepted(self):
        self.post(clip=clip(90))
        self.assertEqual(Short.objects.count(), 1)

    def test_clip_over_ninety_seconds_is_rejected(self):
        response = self.post(clip=clip(95))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "The clip is 1:35 long. Shorts can be at most 1:30.",
                            status_code=400)
        self.assertFalse(Short.objects.exists())
        self.assertEqual(self.storage.listdir("")[1], [])  # nothing uploaded

    def test_renamed_non_video_is_rejected(self):
        fake = SimpleUploadedFile("lesson.mp4", b"%PDF-1.7 not a video" * 20,
                                  content_type="video/mp4")
        response = self.post(clip=fake)
        self.assertContains(response, "not a readable MP4 or MOV video", status_code=400)
        self.assertFalse(Short.objects.exists())

    def test_other_formats_are_rejected(self):
        response = self.post(clip=SimpleUploadedFile("lesson.webm", b"\x1aE\xdf\xa3" * 50))
        self.assertContains(response, "Upload an MP4 or MOV video.", status_code=400)

    def test_mov_from_a_phone_is_accepted_and_typed_by_extension(self):
        sent = []
        real_save = self.storage.save
        with patch.object(self.storage, "save",
                          side_effect=lambda n, c, **kw: sent.append(c.content_type) or real_save(n, c, **kw)):
            # Browsers often label .mov as octet-stream; storage must not keep that.
            self.post(clip=clip(20, name="IMG_0042.MOV", content_type="application/octet-stream"))
        self.assertTrue(Short.objects.get().clip.name.endswith(".mov"))
        self.assertEqual(sent, ["video/quicktime"])

    @override_settings(SHORTS_MAX_UPLOAD_BYTES=100)
    def test_oversized_clip_is_rejected(self):
        response = self.post()
        self.assertContains(response, "The clip is too large.", status_code=400)

    def test_title_tags_and_clip_are_required(self):
        response = self.post(title="  ", tags=" , #, ", clip="")
        self.assertEqual(response.status_code, 400)
        form = response.context["form"]
        self.assertEqual(set(form.errors), {"title", "tags", "clip"})
        self.assertFalse(Short.objects.exists())

    def test_description_is_optional(self):
        self.post(description="")
        self.assertEqual(Short.objects.get().description, "")

    def test_student_cannot_upload(self):
        response = self.post(self.student)
        self.assertRedirects(response, reverse("shorts:feed"), fetch_redirect_response=False)
        self.assertFalse(Short.objects.exists())

    def test_deleting_a_short_deletes_its_clip(self):
        self.post()
        short = Short.objects.get()
        name = short.clip.name
        with self.captureOnCommitCallbacks(execute=True):
            short.delete()
        self.assertFalse(self.storage.exists(name))

    def test_failure_after_upload_leaves_no_clip_behind(self):
        uploaded = []
        real_save = self.storage.save

        def spy(name, content, **kwargs):
            uploaded.append(real_save(name, content, **kwargs))
            return uploaded[-1]

        # An unsaved Tag makes tags.set() fail, which happens after the clip
        # has already been sent to storage.
        with patch.object(self.storage, "save", side_effect=spy), \
             patch("shorts.services.resolve_tags", return_value=[Tag(slug="x", label="x")]):
            with self.assertRaises(ValueError):
                self.post()
        self.assertEqual(len(uploaded), 1)
        self.assertFalse(self.storage.exists(uploaded[0]))
        self.assertFalse(Short.objects.exists())


class ClipRulesTests(SimpleTestCase):
    def test_tenth_of_a_second_over_the_limit_is_tolerated(self):
        self.assertAlmostEqual(clip_duration(clip(90.04)), 90.04)

    def test_ninety_point_one_is_too_long(self):
        with self.assertRaises(ValidationError):
            clip_duration(clip(90.1))

    def test_check_leaves_the_file_where_it_was(self):
        upload = clip(10)
        upload.seek(7)
        clip_duration(upload)
        self.assertEqual(upload.tell(), 7)

    def test_parse_tags(self):
        self.assertEqual(
            parse_tags(" #Data  Structures, ske, SKE ,, การเรียน "),
            [("data-structures", "Data Structures"), ("ske", "ske"), ("การเรียน", "การเรียน")],
        )

    def test_parse_tags_rejects_symbol_only_tags(self):
        from django import forms
        with self.assertRaises(forms.ValidationError):
            parse_tags("!!!")
