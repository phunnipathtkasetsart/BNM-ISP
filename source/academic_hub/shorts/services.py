import os

from django.db import IntegrityError, transaction

from announcements.models import Tag

from .models import Short

# Stored with each object, so a browser can later play the clip from a signed
# link. Browsers often send application/octet-stream for .mov, so the type
# comes from the extension (already checked by the form), not the upload.
CONTENT_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime"}


def resolve_tags(parsed):
    """Turn (slug, label) pairs into Tag rows, creating Topic tags as needed."""
    tags = []
    for slug, label in parsed:
        tag = Tag.objects.filter(slug__iexact=slug).first()
        if tag is None:
            try:
                # A savepoint, so a lost race on the unique slug does not
                # break the surrounding transaction.
                with transaction.atomic():
                    tag = Tag.objects.create(slug=slug, label=label, kind=Tag.Kind.TOPIC)
            except IntegrityError:
                tag = Tag.objects.get(slug__iexact=slug)
        tags.append(tag)
    return tags


def create_short(*, author, title, description, tags, clip, duration_seconds):
    """Store the clip, then the row that points at it.

    Saving the row is what uploads the clip to storage. If anything after
    that fails, the transaction rolls the row back and the uploaded object is
    deleted, so storage is never left holding a clip no row refers to.
    """
    short = None
    try:
        with transaction.atomic():
            tag_rows = resolve_tags(tags)
            short = Short(
                title=title,
                description=description,
                author=author,
                author_label="Department" if author.is_superuser else "Lecturer",
            )
            attach_clip(short, clip, duration_seconds)
            short.save()
            short.tags.set(tag_rows)
    except Exception:
        if short is not None and short.clip.name:
            short.clip.storage.delete(short.clip.name)
        raise
    return short


def attach_clip(short, clip, duration_seconds):
    """Point the short at a new upload; saving the short then stores it."""
    clip.content_type = CONTENT_TYPES.get(os.path.splitext(clip.name)[1].lower(), "video/mp4")
    short.clip = clip
    short.original_name = os.path.basename(clip.name)[:255]
    short.size = clip.size
    short.duration_seconds = duration_seconds


def update_short(short, *, title, description, tags, clip=None, duration_seconds=None):
    """Change a short's details and, if a new clip is given, swap the video.

    The old clip is deleted from storage only once the change is safely
    saved. If saving fails, the new upload is deleted instead and the short
    keeps its old clip, so a failed edit never loses a video.
    """
    old_name = short.clip.name
    storage = short.clip.storage
    try:
        with transaction.atomic():
            tag_rows = resolve_tags(tags)
            short.title = title
            short.description = description
            if clip is not None:
                attach_clip(short, clip, duration_seconds)
            short.save()
            short.tags.set(tag_rows)
            if clip is not None and old_name:
                transaction.on_commit(lambda: storage.delete(old_name))
    except Exception:
        if clip is not None and short.clip.name and short.clip.name != old_name:
            storage.delete(short.clip.name)
        raise
    return short
