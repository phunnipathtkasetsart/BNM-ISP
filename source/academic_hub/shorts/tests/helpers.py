"""Test fixtures shared by the Shorts tests: users, clips, storage."""

import struct
from collections.abc import Iterable
from unittest.mock import patch

from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from accounts.models import User as Account
from announcements.models import Tag
from shorts.models import Short


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def mp4_bytes(seconds: float, timescale: int = 1000) -> bytes:
    """The smallest MP4 that states a length: ftyp, then moov/mvhd.

    Enough for the length check, which reads only the header, so the tests
    need no real video and no ffmpeg.
    """
    ftyp = _box(
        b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2avc1mp41"
    )
    mvhd = _box(
        b"mvhd",
        struct.pack(
            ">B3xIIII", 0, 0, 0, timescale, int(round(seconds * timescale))
        )
        + struct.pack(">IH10x", 0x00010000, 0x0100)
        + b"\0" * 60
        + struct.pack(">I", 2),
    )
    return ftyp + _box(b"moov", mvhd) + _box(b"mdat", b"\0" * 64)


def clip(
    seconds: float = 30,
    name: str = "lesson.mp4",
    content_type: str = "video/mp4",
) -> SimpleUploadedFile:
    """An upload holding a valid MP4 header of the given length."""
    return SimpleUploadedFile(
        name, mp4_bytes(seconds), content_type=content_type
    )


def make_user(
    number: int, name: str, department: str = "ske", **roles: bool
) -> Account:
    """A test account; roles are is_staff / is_superuser."""
    user: Account = Account.objects.create_user(
        nisit_id=f"661054020{number}",
        email=f"short.{name.lower()}@ku.th",
        password="x",
        first_name="Short",
        last_name=name,
        department=department,
        **roles,
    )
    return user


class ShortsTestCase(TestCase):
    """A student, a lecturer and the Department, with clips in memory.

    The clip field is pointed at an InMemoryStorage for every test, so no
    test ever writes to MinIO or to disk.
    """

    student: Account
    lecturer: Account
    department: Account
    storage: InMemoryStorage

    @classmethod
    def setUpTestData(cls) -> None:
        cls.student = make_user(1, "Student")
        cls.lecturer = make_user(2, "Lecturer", is_staff=True)
        cls.department = make_user(3, "Dept", is_superuser=True)

    def setUp(self) -> None:
        super().setUp()
        self.use_fresh_storage()

    def use_fresh_storage(self) -> None:
        """Point the clip field at a new, empty in-memory storage."""
        self.storage = InMemoryStorage()
        patcher = patch.object(
            Short._meta.get_field("clip"), "storage", self.storage
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_short(
        self,
        title: str = "Clip",
        tags: Iterable[str] = (),
        archived: bool = False,
        body: bytes | None = None,
    ) -> Short:
        """A saved short by the lecturer, stored in memory."""
        short = Short(
            title=title,
            description=f"About {title}",
            duration_seconds=30,
            author=self.lecturer,
            author_label="Lecturer",
        )
        short.clip = SimpleUploadedFile(
            "c.mp4", body or mp4_bytes(30), content_type="video/mp4"
        )
        short.save()
        short.tags.set(
            [
                Tag.objects.get_or_create(slug=t, defaults={"label": t})[0]
                for t in tags
            ]
        )
        if archived:
            Short.objects.filter(pk=short.pk).update(
                archived_at="2026-01-01T00:00:00Z"
            )
        return short
