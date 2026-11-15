"""Saving shorts: the one place a clip is uploaded, replaced or cleaned up.

Create Short, Edit Short and the seed loader all save through save_short(),
so the rule "never leave a stored clip that no row points at, and never
lose the clip a row does point at" is written once.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from functools import partial

from django.core.files import File
from django.db import IntegrityError, transaction

from accounts.models import User as Account
from announcements.models import Tag

from .models import CLIP_PREFIX, Short

# (slug, label) pairs, as parse_tags() returns them.
TagPair = tuple[str, str]

# Stored with each object, so a browser can later play the clip from a
# signed link. Browsers often send application/octet-stream for .mov, so the
# type comes from the extension (already checked by the form), not the
# upload.
CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
}


def content_type_for(name: str) -> str:
    """The video MIME type for a file name, by its extension."""
    extension = os.path.splitext(name)[1].lower()
    return CONTENT_TYPES.get(extension, "video/mp4")


def resolve_tags(parsed: Iterable[TagPair]) -> list[Tag]:
    """Turn (slug, label) pairs into Tag rows, creating Topic tags."""
    tags = []
    for slug, label in parsed:
        tag = Tag.objects.filter(slug__iexact=slug).first()
        if tag is None:
            try:
                # A savepoint, so a lost race on the unique slug does not
                # break the surrounding transaction.
                with transaction.atomic():
                    tag = Tag.objects.create(
                        slug=slug, label=label, kind=Tag.Kind.TOPIC
                    )
            except IntegrityError:
                tag = Tag.objects.get(slug__iexact=slug)
        tags.append(tag)
    return tags


def attach_clip(
    short: Short, clip: File[bytes], duration_seconds: float
) -> None:
    """Point the short at a new upload; saving the short then stores it."""
    # django-storages reads content_type off the file it uploads.
    setattr(clip, "content_type", content_type_for(clip.name or ""))
    short.clip = clip
    short.original_name = os.path.basename(clip.name or "")[:255]
    short.size = clip.size or 0
    short.duration_seconds = duration_seconds


def save_short(
    short: Short,
    *,
    tags: Iterable[TagPair],
    clip: File[bytes] | None = None,
    duration_seconds: float = 0.0,
) -> Short:
    """Save a short and its tags, uploading `clip` if one is given.

    Saving the row is what uploads the clip. If anything fails, the
    transaction rolls the row back and the newly uploaded object is
    deleted, so storage never holds a clip no row refers to. A replaced
    clip is deleted only once the change is safely committed, so a failed
    edit never loses the video the short already had.
    """
    storage = short.clip.storage
    old_name = short.clip.name or ""
    try:
        with transaction.atomic():
            tag_rows = resolve_tags(tags)
            if clip is not None:
                attach_clip(short, clip, duration_seconds)
            short.save()
            short.tags.set(tag_rows)
            if clip is not None and old_name:
                transaction.on_commit(partial(storage.delete, old_name))
    except Exception:
        new_name = short.clip.name or ""
        # Before the upload the name is still the uploader's file name, so
        # only an uploaded object (under CLIP_PREFIX) is removed.
        if new_name != old_name and new_name.startswith(CLIP_PREFIX):
            storage.delete(new_name)
        raise
    return short


def author_label_for(user: Account) -> str:
    """How a new short's poster is shown: "Department" or "Lecturer"."""
    return "Department" if user.is_superuser else "Lecturer"


# pylint: disable-next=too-many-arguments
def create_short(
    *,
    author: Account,
    title: str,
    description: str,
    tags: Iterable[TagPair],
    clip: File[bytes],
    duration_seconds: float,
) -> Short:
    """Upload a new short."""
    short = Short(
        title=title,
        description=description,
        author=author,
        author_label=author_label_for(author),
    )
    return save_short(
        short, tags=tags, clip=clip, duration_seconds=duration_seconds
    )


# pylint: disable-next=too-many-arguments
def update_short(
    short: Short,
    *,
    title: str,
    description: str,
    tags: Iterable[TagPair],
    clip: File[bytes] | None = None,
    duration_seconds: float | None = None,
) -> Short:
    """Change a short's details and, if a new clip is given, swap it."""
    short.title = title
    short.description = description
    return save_short(
        short,
        tags=tags,
        clip=clip,
        duration_seconds=duration_seconds or 0.0,
    )
