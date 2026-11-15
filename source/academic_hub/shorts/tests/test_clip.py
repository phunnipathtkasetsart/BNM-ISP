"""Playing a clip: local Range streaming and MinIO signed links."""

from typing import Any

from django.test import override_settings
from django.urls import reverse

from shorts.models import Short
from shorts.tests.helpers import (
    ShortsTestCase,
    mp4_bytes,
)


@override_settings(SHORTS_STORAGE="local")
class LocalClipStreamingTests(ShortsTestCase):
    """Without MinIO the app serves the bytes and honours Range requests."""

    def setUp(self) -> None:
        super().setUp()
        self.body = mp4_bytes(30) + bytes(range(256)) * 4
        self.short = self.make_short(body=self.body)
        self.url = reverse("shorts:clip", args=[self.short.pk])
        self.client.force_login(self.student)

    def get(self, range_header: str | None = None) -> tuple[Any, bytes]:
        """GET the clip; the response and the streamed body."""
        headers = {"Range": range_header} if range_header else {}
        response: Any = self.client.get(self.url, headers=headers)
        body = (
            b"".join(response.streaming_content) if response.streaming else b""
        )
        return response, body

    def test_whole_file(self) -> None:
        response, body = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body, self.body)
        self.assertEqual(response["Content-Type"], "video/mp4")
        self.assertEqual(response["Accept-Ranges"], "bytes")
        self.assertEqual(response["Content-Length"], str(len(self.body)))

    def test_byte_range(self) -> None:
        response, body = self.get("bytes=10-19")
        self.assertEqual(response.status_code, 206)
        self.assertEqual(body, self.body[10:20])
        self.assertEqual(
            response["Content-Range"], f"bytes 10-19/{len(self.body)}"
        )
        self.assertEqual(response["Content-Length"], "10")

    def test_open_ended_and_suffix_ranges(self) -> None:
        _, tail = self.get("bytes=100-")
        self.assertEqual(tail, self.body[100:])
        response, last = self.get("bytes=-5")
        self.assertEqual(last, self.body[-5:])
        size = len(self.body)
        self.assertEqual(
            response["Content-Range"],
            f"bytes {size - 5}-{size - 1}/{size}",
        )

    def test_range_past_the_end(self) -> None:
        response, _ = self.get(f"bytes={len(self.body) + 10}-")
        self.assertEqual(response.status_code, 416)
        self.assertEqual(
            response["Content-Range"], f"bytes */{len(self.body)}"
        )

    def test_archived_clip_is_not_served(self) -> None:
        Short.objects.filter(pk=self.short.pk).update(
            archived_at="2026-01-01T00:00:00Z"
        )
        response, _ = self.get()
        self.assertEqual(response.status_code, 404)


@override_settings(
    SHORTS_STORAGE="minio",
    SHORTS_BUCKET="shorts",
    MINIO_PUBLIC_URL="",
    MINIO_PUBLIC_PORT=9000,
    MINIO_ACCESS_KEY="key",
    MINIO_SECRET_KEY="secret",
    SHORTS_URL_SECONDS=3600,
)
class MinioClipLinkTests(ShortsTestCase):
    """With MinIO the app only signs a link; the browser streams from MinIO."""

    # A phone on the same Wi-Fi opens the site at the laptop's address, which
    # has to be in ALLOWED_HOSTS for the site to load at all.
    @override_settings(ALLOWED_HOSTS=["localhost", "192.168.1.20"])
    def test_redirects_to_a_signed_link_on_the_host_the_site_was_opened_on(
        self,
    ) -> None:
        short = self.make_short()
        self.client.force_login(self.student)
        for host in ("localhost:8000", "192.168.1.20:8000"):
            with self.subTest(host=host):
                response = self.client.get(
                    reverse("shorts:clip", args=[short.pk]), HTTP_HOST=host
                )
                self.assertEqual(response.status_code, 302)
                location = response["Location"]
                name = host.split(":", maxsplit=1)[0]
                self.assertTrue(
                    location.startswith(
                        f"http://{name}:9000/shorts/{short.clip.name}?"
                    ),
                    location,
                )
                self.assertIn("X-Amz-Signature=", location)
                self.assertIn("X-Amz-Expires=3600", location)
                self.assertEqual(
                    response["Cache-Control"], "private, no-store"
                )

    @override_settings(MINIO_PUBLIC_URL="https://media.example.ku.th")
    def test_public_url_setting_wins(self) -> None:
        short = self.make_short()
        self.client.force_login(self.student)
        response = self.client.get(reverse("shorts:clip", args=[short.pk]))
        self.assertTrue(
            response["Location"].startswith(
                "https://media.example.ku.th/shorts/clips/"
            )
        )
