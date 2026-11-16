"""Checks on an uploaded clip, done before anything is sent to storage."""

from __future__ import annotations

import os

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files import File
from mutagen import MutagenError
from mutagen.mp4 import MP4

# MP4 and the QuickTime .mov phones record share one container format, which
# is what lets the length be read without ffmpeg. Browsers play MP4 (H.264)
# everywhere, so that is the format to ask people for.
ALLOWED_EXTENSIONS = {"mp4", "m4v", "mov"}


def format_duration(seconds: float) -> str:
    """83.4 -> "1:23"."""
    whole = int(round(seconds))
    return f"{whole // 60}:{whole % 60:02d}"


def _check_extension(name: str) -> None:
    extension = os.path.splitext(name)[1].lstrip(".").lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            "Upload an MP4 or MOV video.", code="clip_format"
        )


def _check_size(size: int) -> None:
    limit = settings.SHORTS_MAX_UPLOAD_BYTES
    if size > limit:
        raise ValidationError(
            "The clip is too large. The maximum size is %(mb)d MB.",
            code="clip_too_large",
            params={"mb": limit // (1024 * 1024)},
        )


def _read_seconds(upload: File[bytes]) -> float:
    """The length in the MP4 header; 0 if the header can't be parsed."""
    position = upload.tell()
    try:
        upload.seek(0)
        # Every MP4/MOV file names its brand in an "ftyp" box at byte 4.
        # Checking it first turns away renamed files cheaply.
        if upload.read(12)[4:8] != b"ftyp":
            raise ValidationError(
                "This file is not a readable MP4 or MOV video.",
                code="clip_unreadable",
            )
        upload.seek(0)
        try:
            info = MP4(upload).info
            return float(info.length) if info is not None else 0.0
        except (MutagenError, ValueError, OSError):
            return 0.0
    finally:
        upload.seek(position)


def _check_length(seconds: float) -> None:
    if seconds <= 0:
        raise ValidationError(
            "Could not read how long this clip is. "
            "Export it again as MP4 and retry.",
            code="clip_unreadable",
        )
    # Compare to the tenth of a second: editors that trim to exactly 1:30
    # often write 90.02s, and nobody watching can tell the difference.
    if round(seconds, 1) > settings.SHORTS_MAX_SECONDS:
        raise ValidationError(
            "The clip is %(length)s long. Shorts can be at most %(limit)s.",
            code="clip_too_long",
            params={
                "length": format_duration(seconds),
                "limit": format_duration(settings.SHORTS_MAX_SECONDS),
            },
        )


def clip_duration(upload: File[bytes]) -> float:
    """Validate an uploaded clip and return its length in seconds."""
    _check_extension(upload.name or "")
    _check_size(upload.size or 0)
    seconds = _read_seconds(upload)
    _check_length(seconds)
    return seconds
