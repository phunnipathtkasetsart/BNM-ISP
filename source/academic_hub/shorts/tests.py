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


class FeedFixtures(InMemoryClips, Users):
    @classmethod
    def setUpTestData(cls):
        cls.make_users()

    def make_short(self, title="Clip", tags=(), archived=False, body=None):
        short = Short(title=title, description=f"About {title}", duration_seconds=30,
                      author=self.lecturer, author_label="Lecturer")
        short.clip = SimpleUploadedFile("c.mp4", body or mp4_bytes(30), content_type="video/mp4")
        short.save()
        if tags:
            short.tags.set([Tag.objects.get_or_create(slug=t, defaults={"label": t})[0] for t in tags])
        if archived:
            Short.objects.filter(pk=short.pk).update(archived_at="2026-01-01T00:00:00Z")
        return short


class FeedTests(FeedFixtures, TestCase):
    def test_empty_feed_shows_the_empty_panel(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("shorts:feed"))
        self.assertContains(response, "No shorts yet.")
        self.assertNotContains(response, "reels-data")

    def test_feed_hands_the_player_titles_descriptions_and_tags(self):
        old = self.make_short("Older", tags=["recursion"])
        new = self.make_short("Newer", tags=["big-o", "ske"])
        self.make_short("Hidden", archived=True)
        self.client.force_login(self.student)
        response = self.client.get(reverse("shorts:feed"))
        self.assertContains(response, 'id="reel-template"')
        items = response.context["feed"]["items"]
        self.assertEqual([i["title"] for i in items], ["Newer", "Older"])  # newest first
        self.assertEqual(items[0]["description"], "About Newer")
        self.assertEqual(sorted(items[0]["tags"]), ["SKE", "big-o"])  # labels, not slugs
        self.assertEqual(items[1]["src"], reverse("shorts:clip", args=[old.pk]))
        self.assertIsNone(response.context["feed"]["next"])
        # The data reaches the page as JSON for reels.js.
        self.assertContains(response, '<script id="reels-data" type="application/json">')
        self.assertNotContains(response, "Hidden")
        self.assertNotIn(new.clip.name, response.content.decode())  # no storage paths leak

    def test_feed_api_pages_through_and_signals_the_end(self):
        for n in range(10):
            self.make_short(f"Clip {n}")
        self.client.force_login(self.student)
        first = self.client.get(reverse("shorts:api_feed"), {"offset": 0}).json()
        self.assertEqual(len(first["items"]), 8)
        self.assertEqual(first["next"], 8)
        second = self.client.get(reverse("shorts:api_feed"), {"offset": 8}).json()
        self.assertEqual([i["title"] for i in second["items"]], ["Clip 1", "Clip 0"])
        self.assertIsNone(second["next"])  # the page starts over from offset 0

    def test_feed_api_rejects_a_bad_offset(self):
        self.client.force_login(self.student)
        for bad in ("-1", "abc"):
            response = self.client.get(reverse("shorts:api_feed"), {"offset": bad})
            self.assertEqual(response.status_code, 400)

    def test_signed_out_visitors_get_nothing(self):
        short = self.make_short()
        for url in (reverse("shorts:api_feed"), reverse("shorts:clip", args=[short.pk])):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse("accounts:login"), response["Location"])


@override_settings(SHORTS_STORAGE="local")
class LocalClipStreamingTests(FeedFixtures, TestCase):
    """Without MinIO the app serves the bytes, and must honour Range requests."""

    def setUp(self):
        super().setUp()
        self.body = mp4_bytes(30) + bytes(range(256)) * 4
        self.short = self.make_short(body=self.body)
        self.url = reverse("shorts:clip", args=[self.short.pk])
        self.client.force_login(self.student)

    def get(self, range_header=None):
        extra = {"HTTP_RANGE": range_header} if range_header else {}
        response = self.client.get(self.url, **extra)
        return response, b"".join(response.streaming_content) if response.streaming else b""

    def test_whole_file(self):
        response, body = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body, self.body)
        self.assertEqual(response["Content-Type"], "video/mp4")
        self.assertEqual(response["Accept-Ranges"], "bytes")
        self.assertEqual(response["Content-Length"], str(len(self.body)))

    def test_byte_range(self):
        response, body = self.get("bytes=10-19")
        self.assertEqual(response.status_code, 206)
        self.assertEqual(body, self.body[10:20])
        self.assertEqual(response["Content-Range"], f"bytes 10-19/{len(self.body)}")
        self.assertEqual(response["Content-Length"], "10")

    def test_open_ended_and_suffix_ranges(self):
        _, tail = self.get("bytes=100-")
        self.assertEqual(tail, self.body[100:])
        response, last = self.get("bytes=-5")
        self.assertEqual(last, self.body[-5:])
        self.assertEqual(response["Content-Range"],
                         f"bytes {len(self.body) - 5}-{len(self.body) - 1}/{len(self.body)}")

    def test_range_past_the_end(self):
        response, _ = self.get(f"bytes={len(self.body) + 10}-")
        self.assertEqual(response.status_code, 416)
        self.assertEqual(response["Content-Range"], f"bytes */{len(self.body)}")

    def test_archived_clip_is_not_served(self):
        Short.objects.filter(pk=self.short.pk).update(archived_at="2026-01-01T00:00:00Z")
        response, _ = self.get()
        self.assertEqual(response.status_code, 404)


@override_settings(SHORTS_STORAGE="minio", SHORTS_BUCKET="shorts", MINIO_PUBLIC_URL="",
                   MINIO_PUBLIC_PORT=9000, MINIO_ACCESS_KEY="key", MINIO_SECRET_KEY="secret",
                   SHORTS_URL_SECONDS=3600)
class MinioClipLinkTests(FeedFixtures, TestCase):
    """With MinIO the app only signs a link; the browser streams from MinIO."""

    # A phone on the same Wi-Fi opens the site at the laptop's address, which
    # has to be in ALLOWED_HOSTS for the site to load at all.
    @override_settings(ALLOWED_HOSTS=["localhost", "192.168.1.20"])
    def test_redirects_to_a_signed_link_on_the_host_the_site_was_opened_on(self):
        short = self.make_short()
        self.client.force_login(self.student)
        for host in ("localhost:8000", "192.168.1.20:8000"):
            with self.subTest(host=host):
                response = self.client.get(reverse("shorts:clip", args=[short.pk]), HTTP_HOST=host)
                self.assertEqual(response.status_code, 302)
                location = response["Location"]
                name = host.split(":")[0]
                self.assertTrue(location.startswith(f"http://{name}:9000/shorts/{short.clip.name}?"),
                                location)
                self.assertIn("X-Amz-Signature=", location)
                self.assertIn("X-Amz-Expires=3600", location)
                self.assertEqual(response["Cache-Control"], "private, no-store")

    @override_settings(MINIO_PUBLIC_URL="https://media.example.ku.th")
    def test_public_url_setting_wins(self):
        short = self.make_short()
        self.client.force_login(self.student)
        response = self.client.get(reverse("shorts:clip", args=[short.pk]))
        self.assertTrue(response["Location"].startswith("https://media.example.ku.th/shorts/clips/"))


class LikeAndCommentTests(FeedFixtures, TestCase):
    def setUp(self):
        super().setUp()
        self.short = self.make_short("Recursion")
        self.like_url = reverse("shorts:api_like", args=[self.short.pk])
        self.comments_url = reverse("shorts:api_comments", args=[self.short.pk])

    def post_json(self, url, data, user=None):
        self.client.force_login(user or self.student)
        return self.client.post(url, data, content_type="application/json")

    # --- likes ---
    def test_like_is_set_not_toggled(self):
        for _ in range(2):  # a double tap or a retry still counts once
            response = self.post_json(self.like_url, {"liked": True})
            self.assertEqual(response.json(), {"liked": True, "like_count": 1})
        self.post_json(self.like_url, {"liked": True}, self.lecturer)
        response = self.post_json(self.like_url, {"liked": False})
        self.assertEqual(response.json(), {"liked": False, "like_count": 1})
        response = self.post_json(self.like_url, {"liked": False})
        self.assertEqual(response.json()["like_count"], 1)

    def test_like_needs_a_true_or_false(self):
        for bad in ({}, {"liked": "yes"}, {"liked": 1}):
            self.assertEqual(self.post_json(self.like_url, bad).status_code, 400)

    def test_feed_reports_likes_and_comments_per_viewer(self):
        self.post_json(self.like_url, {"liked": True}, self.lecturer)
        self.post_json(self.comments_url, {"body": "Nice"}, self.lecturer)
        self.client.force_login(self.student)
        item = self.client.get(reverse("shorts:api_feed")).json()["items"][0]
        self.assertEqual((item["like_count"], item["comment_count"], item["liked"]), (1, 1, False))
        self.assertEqual(item["like_url"], self.like_url)
        self.assertEqual(item["comments_url"], self.comments_url)
        self.client.force_login(self.lecturer)
        self.assertTrue(self.client.get(reverse("shorts:api_feed")).json()["items"][0]["liked"])

    def test_page_carries_the_csrf_token_reels_js_sends(self):
        self.client.force_login(self.student)
        self.assertContains(self.client.get(reverse("shorts:feed")), 'name="csrfmiddlewaretoken"')

    def test_posts_without_the_csrf_token_are_refused(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.student)
        response = client.post(self.like_url, {"liked": True}, content_type="application/json")
        self.assertEqual(response.status_code, 403)

    # --- comments ---
    def test_comment_is_stored_and_returned(self):
        response = self.post_json(self.comments_url, {"body": "  Why is the base case first?  "})
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["comment_count"], 1)
        comment = data["comment"]
        self.assertEqual(comment["body"], "Why is the base case first?")
        self.assertEqual(comment["author"], "Short Student")
        self.assertEqual(comment["role"], "Student")
        self.assertTrue(comment["is_mine"])
        self.assertIsNotNone(comment["delete_url"])

    def test_roles_are_labelled(self):
        for user, role in ((self.lecturer, "Lecturer"), (self.department, "Department")):
            response = self.post_json(self.comments_url, {"body": "hi"}, user)
            self.assertEqual(response.json()["comment"]["role"], role)

    def test_empty_or_too_long_comments_are_refused(self):
        for bad in ({"body": "   "}, {}, {"body": "x" * 501}, {"body": 5}):
            self.assertEqual(self.post_json(self.comments_url, bad).status_code, 400)
        self.assertEqual(self.post_json(self.comments_url, {"body": "x" * 500}).status_code, 201)

    def test_comments_list_newest_first_in_pages(self):
        for n in range(25):
            self.post_json(self.comments_url, {"body": f"comment {n}"})
        first = self.client.get(self.comments_url).json()
        self.assertEqual(len(first["items"]), 20)
        self.assertEqual(first["items"][0]["body"], "comment 24")
        self.assertEqual(first["comment_count"], 25)
        rest = self.client.get(self.comments_url, {"before": first["next_before"]}).json()
        self.assertEqual([c["body"] for c in rest["items"]], [f"comment {n}" for n in range(4, -1, -1)])
        self.assertIsNone(rest["next_before"])

    def test_only_the_author_or_department_can_delete(self):
        made = self.post_json(self.comments_url, {"body": "mine"}).json()["comment"]
        delete_url = made["delete_url"]
        # Someone else sees no delete link and is refused if they try anyway.
        self.client.force_login(self.lecturer)
        listed = self.client.get(self.comments_url).json()["items"][0]
        self.assertIsNone(listed["delete_url"])
        self.assertEqual(self.client.post(delete_url).status_code, 403)
        # The Department may moderate.
        self.client.force_login(self.department)
        self.assertIsNotNone(self.client.get(self.comments_url).json()["items"][0]["delete_url"])
        response = self.client.post(delete_url)
        self.assertEqual(response.json(), {"deleted": True, "comment_count": 0})

    def test_author_can_delete_own_comment(self):
        made = self.post_json(self.comments_url, {"body": "oops"}).json()["comment"]
        self.assertEqual(self.client.post(made["delete_url"]).status_code, 200)
        self.assertEqual(self.client.post(made["delete_url"]).status_code, 404)

    def test_signed_out_gets_json_401(self):
        for method, url in (("post", self.like_url), ("get", self.comments_url), ("post", self.comments_url)):
            response = getattr(self.client, method)(url)
            self.assertEqual(response.status_code, 401)
            self.assertIn("error", response.json())

    def test_archived_short_cannot_be_liked_or_commented(self):
        Short.objects.filter(pk=self.short.pk).update(archived_at="2026-01-01T00:00:00Z")
        self.assertEqual(self.post_json(self.like_url, {"liked": True}).status_code, 404)
        self.assertEqual(self.post_json(self.comments_url, {"body": "hi"}).status_code, 404)

    def test_deleting_a_short_takes_its_likes_and_comments(self):
        self.post_json(self.like_url, {"liked": True})
        self.post_json(self.comments_url, {"body": "hi"})
        with self.captureOnCommitCallbacks(execute=True):
            self.short.delete()
        from .models import ShortComment, ShortLike
        self.assertFalse(ShortLike.objects.exists())
        self.assertFalse(ShortComment.objects.exists())


class EditShortTests(FeedFixtures, TestCase):
    """Edit Short: lecturers manage their own shorts, the Department all of them."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.other_lecturer = get_user_model().objects.create_user(
            nisit_id="6610540204", email="short.other@ku.th", password="x",
            first_name="Other", last_name="Lecturer", department="cpe", is_staff=True)

    def setUp(self):
        super().setUp()
        self.mine = self.make_short("Mine", tags=["recursion"])
        self.theirs = self.make_short("Theirs")
        Short.objects.filter(pk=self.theirs.pk).update(author=self.other_lecturer)

    # --- the two buttons ---
    def test_staff_see_create_then_edit_in_the_tab_bar(self):
        self.client.force_login(self.lecturer)
        html = self.client.get(reverse("shorts:feed")).content.decode()
        create_at = html.index(f'href="{reverse("shorts:create")}"')
        edit_at = html.index(f'href="{reverse("shorts:manage")}"')
        self.assertLess(create_at, edit_at)  # Create Short sits to the left
        self.assertIn("Edit Short", html)

    def test_students_see_neither_button_and_cannot_manage(self):
        self.client.force_login(self.student)
        self.assertNotContains(self.client.get(reverse("shorts:feed")), "Edit Short")
        for url in (reverse("shorts:manage"), reverse("shorts:edit", args=[self.mine.pk]),
                    reverse("shorts:delete", args=[self.mine.pk])):
            response = self.client.get(url)
            self.assertRedirects(response, reverse("shorts:feed"), fetch_redirect_response=False)
        self.client.post(reverse("shorts:delete", args=[self.mine.pk]))
        self.assertTrue(Short.objects.filter(pk=self.mine.pk).exists())

    # --- the list ---
    def test_lecturer_lists_only_their_own_shorts(self):
        self.client.force_login(self.lecturer)
        response = self.client.get(reverse("shorts:manage"))
        self.assertContains(response, "My shorts")
        titles = [s.title for s in response.context["page_obj"]]
        self.assertEqual(titles, ["Mine"])

    def test_department_lists_every_short_with_its_author(self):
        self.client.force_login(self.department)
        response = self.client.get(reverse("shorts:manage"))
        self.assertContains(response, "All shorts")
        self.assertEqual({s.title for s in response.context["page_obj"]}, {"Mine", "Theirs"})
        self.assertContains(response, "Other Lecturer")

    def test_empty_list_points_to_create(self):
        self.client.force_login(self.other_lecturer)
        Short.objects.filter(author=self.other_lecturer).delete()
        response = self.client.get(reverse("shorts:manage"))
        self.assertContains(response, "You have not uploaded any shorts yet.")

    # --- editing ---
    def test_edit_form_is_filled_in_and_clip_is_optional(self):
        self.client.force_login(self.lecturer)
        response = self.client.get(reverse("shorts:edit", args=[self.mine.pk]))
        form = response.context["form"]
        self.assertEqual(form.initial["title"], "Mine")
        self.assertEqual(form.initial["tags"], "recursion")
        self.assertFalse(form.fields["clip"].required)
        self.assertContains(response, "Current clip")

    def test_edit_details_and_keep_the_clip(self):
        old_name = self.mine.clip.name
        self.client.force_login(self.lecturer)
        response = self.client.post(reverse("shorts:edit", args=[self.mine.pk]), {
            "title": "Mine, renamed", "description": "New words.", "tags": "big-o, ske",
        })
        self.assertRedirects(response, reverse("shorts:manage"), fetch_redirect_response=False)
        self.mine.refresh_from_db()
        self.assertEqual((self.mine.title, self.mine.description), ("Mine, renamed", "New words."))
        self.assertEqual({t.slug for t in self.mine.tags.all()}, {"big-o", "ske"})
        self.assertEqual(self.mine.clip.name, old_name)
        self.assertTrue(self.storage.exists(old_name))

    def test_replacing_the_clip_stores_the_new_one_and_removes_the_old(self):
        old_name = self.mine.clip.name
        self.client.force_login(self.lecturer)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("shorts:edit", args=[self.mine.pk]), {
                "title": "Mine", "description": "", "tags": "recursion",
                "clip": clip(45, name="take2.mov", content_type="video/quicktime"),
            })
        self.mine.refresh_from_db()
        self.assertNotEqual(self.mine.clip.name, old_name)
        self.assertTrue(self.mine.clip.name.endswith(".mov"))
        self.assertEqual(self.mine.duration_seconds, 45)
        self.assertEqual(self.mine.original_name, "take2.mov")
        self.assertTrue(self.storage.exists(self.mine.clip.name))
        self.assertFalse(self.storage.exists(old_name))

    def test_a_too_long_replacement_changes_nothing(self):
        old_name = self.mine.clip.name
        self.client.force_login(self.lecturer)
        response = self.client.post(reverse("shorts:edit", args=[self.mine.pk]), {
            "title": "Changed?", "description": "", "tags": "recursion", "clip": clip(95),
        })
        self.assertContains(response, "Shorts can be at most 1:30.", status_code=400)
        self.mine.refresh_from_db()
        self.assertEqual((self.mine.title, self.mine.clip.name), ("Mine", old_name))

    def test_failed_save_keeps_the_old_clip_and_drops_the_new(self):
        old_name = self.mine.clip.name
        with patch("shorts.services.resolve_tags", return_value=[Tag(slug="x", label="x")]):
            from .services import update_short
            with self.assertRaises(ValueError):
                update_short(self.mine, title="t", description="", tags=[("x", "x")],
                             clip=clip(20, name="new.mp4"), duration_seconds=20)
        self.assertTrue(self.storage.exists(old_name))
        self.assertEqual(len(self.storage.listdir("clips")[1]), 2)  # mine + theirs, no orphan
        self.assertEqual(Short.objects.get(pk=self.mine.pk).clip.name, old_name)

    def test_lecturer_cannot_edit_or_delete_someone_elses_short(self):
        self.client.force_login(self.lecturer)
        for url in (reverse("shorts:edit", args=[self.theirs.pk]),
                    reverse("shorts:delete", args=[self.theirs.pk])):
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.post(url, {"title": "x", "tags": "x"}).status_code, 404)
        self.theirs.refresh_from_db()
        self.assertEqual(self.theirs.title, "Theirs")

    def test_department_can_edit_anyones_short(self):
        self.client.force_login(self.department)
        self.client.post(reverse("shorts:edit", args=[self.theirs.pk]),
                         {"title": "Moderated", "description": "", "tags": "ske"})
        self.theirs.refresh_from_db()
        self.assertEqual(self.theirs.title, "Moderated")
        self.assertEqual(self.theirs.author, self.other_lecturer)  # still theirs

    # --- deleting ---
    def test_delete_asks_first_then_removes_short_clip_likes_and_comments(self):
        self.client.force_login(self.student)
        self.client.post(reverse("shorts:api_like", args=[self.mine.pk]), {"liked": True},
                         content_type="application/json")
        self.client.post(reverse("shorts:api_comments", args=[self.mine.pk]), {"body": "hi"},
                         content_type="application/json")
        self.client.force_login(self.lecturer)
        confirm = self.client.get(reverse("shorts:delete", args=[self.mine.pk]))
        self.assertContains(confirm, "Delete this short?")
        self.assertContains(confirm, "1 like and 1 comment will be deleted too.")
        self.assertTrue(Short.objects.filter(pk=self.mine.pk).exists())  # GET changes nothing

        name = self.mine.clip.name
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("shorts:delete", args=[self.mine.pk]), follow=True)
        self.assertRedirects(response, reverse("shorts:manage"))
        self.assertContains(response, "“Mine” was deleted.")
        self.assertFalse(Short.objects.filter(pk=self.mine.pk).exists())
        self.assertFalse(self.storage.exists(name))

    def test_department_can_delete_anyones_short(self):
        self.client.force_login(self.department)
        self.client.post(reverse("shorts:delete", args=[self.theirs.pk]))
        self.assertFalse(Short.objects.filter(pk=self.theirs.pk).exists())


class ReplyTests(FeedFixtures, TestCase):
    """Replies: one level deep, as on Reels."""

    def setUp(self):
        super().setUp()
        self.short = self.make_short("Recursion")
        self.comments_url = reverse("shorts:api_comments", args=[self.short.pk])

    def say(self, body, user=None, reply_to=None):
        self.client.force_login(user or self.student)
        data = {"body": body}
        if reply_to is not None:
            data["reply_to"] = reply_to
        return self.client.post(self.comments_url, data, content_type="application/json")

    def test_reply_joins_the_thread_and_counts(self):
        top = self.say("Why base case first?").json()["comment"]
        self.assertEqual(top["reply_count"], 0)
        self.assertIsNone(top["parent_id"])
        response = self.say("Otherwise it never stops.", self.lecturer, reply_to=top["id"])
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["comment"]["parent_id"], top["id"])
        self.assertNotIn("replies_url", data["comment"])  # replies have no thread of their own
        self.assertEqual(data["reply_count"], 1)
        self.assertEqual(data["comment_count"], 2)  # the clip's count includes replies

    def test_list_shows_top_level_comments_with_reply_counts(self):
        top = self.say("Top").json()["comment"]
        self.say("Reply 1", self.lecturer, reply_to=top["id"])
        self.say("Reply 2", reply_to=top["id"])
        self.say("Another top")
        data = self.client.get(self.comments_url).json()
        self.assertEqual([c["body"] for c in data["items"]], ["Another top", "Top"])
        self.assertEqual([c["reply_count"] for c in data["items"]], [0, 2])
        self.assertEqual(data["comment_count"], 4)

    def test_replies_come_oldest_first_in_pages(self):
        top = self.say("Top").json()["comment"]
        for n in range(23):
            self.say(f"reply {n}", reply_to=top["id"])
        first = self.client.get(top["replies_url"]).json()
        self.assertEqual(len(first["items"]), 20)
        self.assertEqual(first["items"][0]["body"], "reply 0")
        self.assertEqual(first["reply_count"], 23)
        rest = self.client.get(top["replies_url"], {"after": first["next_after"]}).json()
        self.assertEqual([c["body"] for c in rest["items"]], ["reply 20", "reply 21", "reply 22"])
        self.assertIsNone(rest["next_after"])

    def test_replying_to_a_reply_stays_in_the_same_thread(self):
        top = self.say("Top").json()["comment"]
        reply = self.say("First reply", self.lecturer, reply_to=top["id"]).json()["comment"]
        nested = self.say("@Short Lecturer agreed", reply_to=reply["id"]).json()
        self.assertEqual(nested["comment"]["parent_id"], top["id"])
        self.assertEqual(nested["reply_count"], 2)

    def test_reply_to_a_missing_or_foreign_comment_is_refused(self):
        other = self.make_short("Other clip")
        self.client.force_login(self.student)
        foreign = self.client.post(reverse("shorts:api_comments", args=[other.pk]),
                                   {"body": "elsewhere"}, content_type="application/json").json()["comment"]
        self.assertEqual(self.say("hi", reply_to=foreign["id"]).status_code, 404)
        self.assertEqual(self.say("hi", reply_to=999999).status_code, 404)
        for bad in ("abc", True, 1.5):
            self.assertEqual(self.say("hi", reply_to=bad).status_code, 400)

    def test_replies_of_a_reply_cannot_be_listed(self):
        top = self.say("Top").json()["comment"]
        reply = self.say("r", reply_to=top["id"]).json()["comment"]
        url = reverse("shorts:api_comment_replies", args=[self.short.pk, reply["id"]])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_deleting_a_comment_deletes_its_replies(self):
        top = self.say("Top").json()["comment"]
        self.say("r1", self.lecturer, reply_to=top["id"])
        self.say("r2", self.lecturer, reply_to=top["id"])
        self.client.force_login(self.student)
        data = self.client.post(top["delete_url"]).json()
        self.assertEqual(data["comment_count"], 0)
        from .models import ShortComment
        self.assertFalse(ShortComment.objects.exists())

    def test_deleting_a_reply_updates_the_thread_count(self):
        top = self.say("Top").json()["comment"]
        mine = self.say("mine", self.lecturer, reply_to=top["id"]).json()["comment"]
        self.say("theirs", reply_to=top["id"])
        self.client.force_login(self.lecturer)
        data = self.client.post(mine["delete_url"]).json()
        self.assertEqual((data["reply_count"], data["comment_count"]), (1, 2))

    def test_only_the_author_or_department_can_delete_a_reply(self):
        top = self.say("Top", self.lecturer).json()["comment"]
        reply = self.say("student reply", reply_to=top["id"]).json()["comment"]
        self.client.force_login(self.lecturer)  # wrote the comment, not the reply
        listed = self.client.get(top["replies_url"]).json()["items"][0]
        self.assertIsNone(listed["delete_url"])
        self.assertEqual(self.client.post(reply["delete_url"]).status_code, 403)
        self.client.force_login(self.department)
        self.assertEqual(self.client.post(reply["delete_url"]).status_code, 200)
