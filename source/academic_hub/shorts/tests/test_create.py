"""Create Short: the form, the clip rules and what gets stored."""

from typing import Any
from unittest.mock import patch

from django import forms
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from accounts.models import User as Account
from announcements.models import Tag
from shorts.forms import parse_tags
from shorts.models import Short
from shorts.tests.helpers import (
    ShortsTestCase,
    clip,
    mp4_bytes,
)
from shorts.validators import clip_duration


class CreateShortTests(ShortsTestCase):
    """Uploading through the Create Short form."""

    ske: Tag

    @classmethod
    def setUpTestData(cls) -> None:
        super().setUpTestData()
        # Created by announcements migration 0004 as a programme tag.
        cls.ske = Tag.objects.get(slug="ske")

    def post(self, user: Account | None = None, **overrides: Any) -> Any:
        """Submit Create Short as `user` (the lecturer by default)."""
        self.client.force_login(user or self.lecturer)
        data = {
            "title": "Big-O in 60 seconds",
            "description": "Why loops nest.",
            "tags": "recursion, #SKE",
            "clip": clip(60),
        }
        data.update(overrides)
        return self.client.post(reverse("shorts:create"), data)

    def test_lecturer_upload_is_stored_with_its_details(self) -> None:
        response = self.post()
        self.assertRedirects(
            response, reverse("shorts:feed"), fetch_redirect_response=False
        )

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

    def test_department_upload_is_labelled_department(self) -> None:
        self.post(self.department)
        self.assertEqual(Short.objects.get().author_label, "Department")

    def test_tags_reuse_existing_and_create_topics(self) -> None:
        self.post()
        tags = {t.slug: t for t in Short.objects.get().tags.all()}
        self.assertEqual(set(tags), {"recursion", "ske"})
        self.assertEqual(
            tags["ske"].pk, self.ske.pk
        )  # matched, not duplicated
        self.assertEqual(tags["recursion"].kind, Tag.Kind.TOPIC)
        self.assertEqual(tags["recursion"].label, "recursion")

    def test_clip_of_exactly_ninety_seconds_is_accepted(self) -> None:
        self.post(clip=clip(90))
        self.assertEqual(Short.objects.count(), 1)

    def test_clip_over_ninety_seconds_is_rejected(self) -> None:
        response = self.post(clip=clip(95))
        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            "The clip is 1:35 long. Shorts can be at most 1:30.",
            status_code=400,
        )
        self.assertFalse(Short.objects.exists())
        self.assertEqual(self.storage.listdir("")[1], [])  # nothing uploaded

    def test_renamed_non_video_is_rejected(self) -> None:
        fake = SimpleUploadedFile(
            "lesson.mp4",
            b"%PDF-1.7 not a video" * 20,
            content_type="video/mp4",
        )
        response = self.post(clip=fake)
        self.assertContains(
            response, "not a readable MP4 or MOV video", status_code=400
        )
        self.assertFalse(Short.objects.exists())

    def test_other_formats_are_rejected(self) -> None:
        response = self.post(
            clip=SimpleUploadedFile("lesson.webm", b"\x1aE\xdf\xa3" * 50)
        )
        self.assertContains(
            response, "Upload an MP4 or MOV video.", status_code=400
        )

    def test_mov_from_a_phone_is_accepted_and_typed_by_extension(self) -> None:
        sent: list[str] = []
        real_save = self.storage.save

        def spy(name: str, content: Any, **kwargs: Any) -> str:
            sent.append(content.content_type)
            return real_save(name, content, **kwargs)

        with patch.object(self.storage, "save", side_effect=spy):
            # Browsers often label .mov as octet-stream; storage must not
            # keep that.
            self.post(
                clip=clip(
                    20,
                    name="IMG_0042.MOV",
                    content_type="application/octet-stream",
                )
            )
        self.assertTrue(Short.objects.get().clip.name.endswith(".mov"))
        self.assertEqual(sent, ["video/quicktime"])

    @override_settings(SHORTS_MAX_UPLOAD_BYTES=100)
    def test_oversized_clip_is_rejected(self) -> None:
        response = self.post()
        self.assertContains(
            response, "The clip is too large.", status_code=400
        )

    def test_title_tags_and_clip_are_required(self) -> None:
        response = self.post(title="  ", tags=" , #, ", clip="")
        self.assertEqual(response.status_code, 400)
        form = response.context["form"]
        self.assertEqual(set(form.errors), {"title", "tags", "clip"})
        self.assertFalse(Short.objects.exists())

    def test_description_is_optional(self) -> None:
        self.post(description="")
        self.assertEqual(Short.objects.get().description, "")

    def test_student_cannot_upload(self) -> None:
        response = self.post(self.student)
        self.assertRedirects(
            response, reverse("shorts:feed"), fetch_redirect_response=False
        )
        self.assertFalse(Short.objects.exists())

    def test_deleting_a_short_deletes_its_clip(self) -> None:
        self.post()
        short = Short.objects.get()
        name = short.clip.name
        with self.captureOnCommitCallbacks(execute=True):
            short.delete()
        self.assertFalse(self.storage.exists(name))

    def test_failure_after_upload_leaves_no_clip_behind(self) -> None:
        uploaded: list[str] = []
        real_save = self.storage.save

        def spy(name: str, content: Any, **kwargs: Any) -> str:
            uploaded.append(real_save(name, content, **kwargs))
            return uploaded[-1]

        # An unsaved Tag makes tags.set() fail, which happens after the clip
        # has already been sent to storage.
        with patch.object(self.storage, "save", side_effect=spy), patch(
            "shorts.services.resolve_tags",
            return_value=[Tag(slug="x", label="x")],
        ):
            with self.assertRaises(ValueError):
                self.post()
        self.assertEqual(len(uploaded), 1)
        self.assertFalse(self.storage.exists(uploaded[0]))
        self.assertFalse(Short.objects.exists())


class ClipRulesTests(SimpleTestCase):
    """The length rule and tag parsing, without the database."""

    def test_tenth_of_a_second_over_the_limit_is_tolerated(self) -> None:
        self.assertAlmostEqual(clip_duration(clip(90.04)), 90.04)

    def test_ninety_point_one_is_too_long(self) -> None:
        with self.assertRaises(ValidationError):
            clip_duration(clip(90.1))

    def test_check_leaves_the_file_where_it_was(self) -> None:
        upload = clip(10)
        upload.seek(7)
        clip_duration(upload)
        self.assertEqual(upload.tell(), 7)

    def test_parse_tags(self) -> None:
        self.assertEqual(
            parse_tags(" #Data  Structures, ske, SKE ,, การเรียน "),
            [
                ("data-structures", "Data Structures"),
                ("ske", "ske"),
                ("การเรียน", "การเรียน"),
            ],
        )

    def test_parse_tags_rejects_symbol_only_tags(self) -> None:

        with self.assertRaises(forms.ValidationError):
            parse_tags("!!!")
