"""Error paths and small rules not reached through the main flows."""

import io
import json
import tempfile
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.http import Http404
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import reverse

from accounts.models import User as Account
from announcements.models import Tag
from shorts.forms import MAX_TAG_LENGTH, ShortForm, parse_tags
from shorts.interactions import ApiError, read_body, signed_in
from shorts.models import Short, ShortComment, ShortLike
from shorts.seed import MANIFEST, default_seed_dir, load_seed
from shorts.services import resolve_tags, save_short
from shorts.signals import delete_clip_with_row
from shorts.streaming import UnsatisfiableRange, _read, parse_range
from shorts.tests.helpers import ShortsTestCase, clip, mp4_bytes
from shorts.validators import clip_duration
from shorts.views import _account


class ParseRangeTests(SimpleTestCase):
    """parse_range: which bytes a Range header asks for."""

    def test_ranges(self) -> None:
        cases: list[tuple[str, tuple[int, int] | None]] = [
            ("", None),
            ("bytes=-", None),
            ("bytes=0-9,20-29", None),  # several ranges: send it all
            ("bytes=10-19", (10, 19)),
            ("bytes=90-", (90, 99)),
            ("bytes=-5", (95, 99)),
            ("bytes=0-500", (0, 99)),
            ("bytes=-500", (0, 99)),
        ]
        for header, expected in cases:
            with self.subTest(header=header):
                self.assertEqual(parse_range(header, 100), expected)

    def test_outside_the_file(self) -> None:
        for header in ("bytes=100-", "bytes=50-40"):
            with self.subTest(header=header):
                with self.assertRaises(UnsatisfiableRange):
                    parse_range(header, 100)

    def test_read_stops_at_the_end_of_a_short_file(self) -> None:
        handle = io.BytesIO(b"abc")
        self.assertEqual(b"".join(_read(handle, 1, 10)), b"bc")
        self.assertTrue(handle.closed)


class FormRuleTests(SimpleTestCase):
    """ShortForm and parse_tags reject what they should."""

    def test_overlong_tag_is_refused(self) -> None:
        with self.assertRaisesMessage(ValidationError, "is too long"):
            parse_tags("a" * (MAX_TAG_LENGTH + 1))

    def test_blank_title_and_too_many_tags(self) -> None:
        form = ShortForm(
            {"title": "   ", "tags": "a, b, c, d, e, f"},
            {"clip": clip(10)},
        )
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors["title"], ["This field is required."])
        self.assertEqual(form.errors["tags"], ["Use at most 5 tags."])

    @override_settings(SHORTS_MAX_SECONDS=90)
    def test_mp4_header_that_cannot_be_parsed(self) -> None:
        broken = SimpleUploadedFile(
            "broken.mp4", mp4_bytes(30)[:12] + b"\xff" * 40
        )
        with self.assertRaisesMessage(
            ValidationError, "Could not read how long this clip is."
        ):
            clip_duration(broken)


class RequestBodyTests(SimpleTestCase):
    """read_body accepts JSON objects and form posts, and nothing else."""

    factory = RequestFactory()

    def post(self, body: str, content_type: str) -> Any:
        """A POST request with this raw body."""
        return self.factory.post("/", body, content_type=content_type)

    def test_json_object_and_form_fields(self) -> None:
        as_json = self.post(json.dumps({"liked": True}), "application/json")
        self.assertEqual(read_body(as_json), {"liked": True})
        as_form = self.factory.post("/", {"liked": "true"})
        self.assertEqual(read_body(as_form)["liked"], "true")

    def test_malformed_or_non_object_json(self) -> None:
        for body in ("{not json", "[1, 2]", '"text"'):
            with self.subTest(body=body):
                with self.assertRaisesMessage(ApiError, "Send a JSON"):
                    read_body(self.post(body, "application/json"))

    def test_anonymous_request_has_no_account(self) -> None:
        request = self.factory.get("/")
        request.user = Account()  # an unsaved account still counts
        self.assertIs(signed_in(request), request.user)
        request.user = _anonymous()
        with self.assertRaises(ApiError):
            signed_in(request)
        with self.assertRaises(PermissionDenied):
            _account(request)


def _anonymous() -> Any:
    # pylint: disable-next=import-outside-toplevel
    from django.contrib.auth.models import AnonymousUser

    return AnonymousUser()


class ApiEdgeTests(ShortsTestCase):
    """The JSON API's less common answers."""

    def setUp(self) -> None:
        super().setUp()
        self.short = self.make_short()
        self.client.force_login(self.student)

    def test_like_from_a_form_post(self) -> None:
        url = reverse("shorts:api_like", args=[self.short.pk])
        self.assertTrue(
            self.client.post(url, {"liked": "true"}).json()["liked"]
        )
        self.assertEqual(ShortLike.objects.count(), 1)

    def test_bad_cursor_is_refused(self) -> None:
        url = reverse("shorts:api_comments", args=[self.short.pk])
        response = self.client.get(url, {"before": "x"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("before", response.json()["error"])

    def test_reply_to_from_a_form_post(self) -> None:
        url = reverse("shorts:api_comments", args=[self.short.pk])
        top = self.client.post(url, {"body": "top"}).json()["comment"]
        reply = self.client.post(
            url, {"body": "re", "reply_to": str(top["id"])}
        ).json()
        self.assertEqual(reply["comment"]["parent_id"], top["id"])
        self.assertEqual(reply["reply_count"], 1)

    def test_bad_feed_offset(self) -> None:
        for offset in ("-1", "x"):
            with self.subTest(offset=offset):
                response = self.client.get(
                    reverse("shorts:api_feed"), {"offset": offset}
                )
                self.assertEqual(response.status_code, 400)

    @override_settings(SHORTS_STORAGE="local")
    def test_missing_clip_file_is_a_404(self) -> None:
        self.storage.delete(self.short.clip.name)
        response = self.client.get(
            reverse("shorts:clip", args=[self.short.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_text_shown_for_each_row(self) -> None:
        like = ShortLike.objects.create(short=self.short, user=self.student)
        comment = ShortComment.objects.create(
            short=self.short,
            author=self.student,
            author_label="S",
            author_role_label="Student",
            body="hi",
        )
        self.assertEqual(str(self.short), "Clip")
        self.assertEqual(str(like), f"{self.student.pk} likes {self.short.pk}")
        self.assertEqual(str(comment), f"Comment on {self.short.pk}")


class SavingEdgeTests(ShortsTestCase):
    """save_short and the delete signal in their rarer cases."""

    def test_tag_created_meanwhile_is_reused(self) -> None:
        # Another upload creates the tag between this one's lookup and its
        # insert: the lookup misses, the insert hits the unique slug.
        existing = Tag.objects.create(slug="race", label="race")
        missed = MagicMock()
        missed.first.return_value = None
        with patch.object(Tag.objects, "filter", return_value=missed):
            self.assertEqual(resolve_tags([("race", "race")]), [existing])

    def test_failure_before_upload_deletes_nothing(self) -> None:
        short = Short(title="t", author=self.lecturer)
        with patch("shorts.services.resolve_tags", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                save_short(short, tags=[], clip=clip(10), duration_seconds=10)
        self.assertEqual(self.storage.listdir("")[1], [])

    def test_deleting_a_short_without_a_clip(self) -> None:
        short = Short(title="no clip", duration_seconds=1)
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            delete_clip_with_row(Short, short)
        self.assertEqual(callbacks, [])

    def test_unknown_short_is_a_404(self) -> None:
        # pylint: disable-next=import-outside-toplevel
        from shorts.interactions import live_short

        with self.assertRaises(Http404):
            live_short(999999)


class SeedEdgeTests(ShortsTestCase):
    """Seed export warnings and a broken seed folder."""

    def setUp(self) -> None:
        super().setUp()
        # pylint: disable-next=consider-using-with
        self.seed_dir = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def export(self, limit: str, value: int) -> str:
        """Run export_shorts_seed with one size limit lowered; its stderr."""
        err = StringIO()
        module = "shorts.management.commands.export_shorts_seed"
        with patch(f"{module}.{limit}", value):
            call_command(
                "export_shorts_seed",
                "--dir",
                str(self.seed_dir),
                stdout=StringIO(),
                stderr=err,
            )
        return err.getvalue()

    def test_default_folder_is_inside_the_app(self) -> None:
        self.assertEqual(default_seed_dir().parts[-2:], ("shorts", "seed"))

    def test_clip_too_big_for_github(self) -> None:
        self.make_short()
        err = self.export("GITHUB_FILE_LIMIT", 10)
        self.assertIn("GitHub will reject these clips", err)

    def test_large_seed_warning(self) -> None:
        self.make_short()
        err = self.export("WARN_TOTAL", 10)
        self.assertIn("The seed is large", err)

    def test_listed_clip_missing(self) -> None:
        manifest = {"shorts": [{"file": "clips/gone.mp4", "title": "Gone"}]}
        (self.seed_dir / MANIFEST).write_text(json.dumps(manifest))
        with self.assertRaisesMessage(FileNotFoundError, "but missing"):
            load_seed(self.seed_dir)
