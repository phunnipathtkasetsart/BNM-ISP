"""Checks on an uploaded clip, done before anything is sent to storage."""

import os

from django.conf import settings
from django.core.exceptions import ValidationError
from mutagen import MutagenError
from mutagen.mp4 import MP4

# MP4 and the QuickTime .mov phones record share one container format, which
# is what lets the length be read without ffmpeg. Browsers play MP4 (H.264)
# everywhere, so that is the format to ask people for.
ALLOWED_EXTENSIONS = {"mp4", "m4v", "mov"}


def format_duration(seconds):
    whole = int(round(seconds))
    return f"{whole // 60}:{whole % 60:02d}"


def clip_duration(upload):
    """Validate an uploaded clip and return its length in seconds."""
    extension = os.path.splitext(upload.name or "")[1].lstrip(".").lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            "Upload an MP4 or MOV video.", code="clip_format",
        )

    limit = settings.SHORTS_MAX_UPLOAD_BYTES
    if upload.size > limit:
        raise ValidationError(
            "The clip is too large. The maximum size is %(mb)d MB.",
            code="clip_too_large", params={"mb": limit // (1024 * 1024)},
        )

    position = upload.tell()
    try:
        upload.seek(0)
        # Every MP4/MOV file names its brand in an "ftyp" box at byte 4.
        # Checking it first turns away renamed files cheaply.
        if upload.read(12)[4:8] != b"ftyp":
            raise ValidationError(
                "This file is not a readable MP4 or MOV video.", code="clip_unreadable",
            )
        upload.seek(0)
        try:
            seconds = MP4(upload).info.length
        except (MutagenError, ValueError, OSError):
            seconds = 0
    finally:
        upload.seek(position)

    if not seconds or seconds <= 0:
        raise ValidationError(
            "Could not read how long this clip is. Export it again as MP4 and retry.",
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
    return seconds
