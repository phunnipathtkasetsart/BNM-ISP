"""A Short is one study clip for the DoomScroll feed (US-14, URS-9, SRS-8).

Only the clip's bytes go to object storage. Everything the feed will need to
pick and order clips (title, tags, length, author) stays here in PostgreSQL,
so building the feed never has to touch MinIO.
"""

import os
import uuid

from django.conf import settings
from django.db import models

from .storage import shorts_storage


def clip_upload_path(instance, filename):
    """A random object name, so nothing the uploader typed reaches storage."""
    extension = os.path.splitext(filename)[1].lower()[:10]
    return f"clips/{uuid.uuid4().hex}{extension}"


class Short(models.Model):
    title = models.CharField(max_length=150)
    description = models.TextField(blank=True, default="", max_length=2000)
    # The same tags announcements use. A typed tag that matches an existing
    # one (e.g. "ske") reuses it; anything new is created as a Topic tag.
    tags = models.ManyToManyField("announcements.Tag", blank=True, related_name="shorts")

    clip = models.FileField(upload_to=clip_upload_path, storage=shorts_storage, max_length=255)
    original_name = models.CharField(max_length=255, blank=True, default="")
    size = models.PositiveBigIntegerField(default=0)
    duration_seconds = models.FloatField()

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_constraint=False,
        related_name="shorts",
    )
    author_label = models.CharField(max_length=80, default="Department")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Not used yet. SRS-9 has staff archive videos; the field is here so
    # that later step needs no change to existing rows.
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return self.title


class ShortLike(models.Model):
    """One user liking one short. A second like is the same row, not a new one."""

    short = models.ForeignKey(Short, on_delete=models.CASCADE, related_name="likes")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        db_constraint=False,
        related_name="short_likes",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["short", "user"], name="unique_short_like"),
        ]


class ShortComment(models.Model):
    short = models.ForeignKey(Short, on_delete=models.CASCADE, related_name="comments")
    # Set on replies: the comment the thread hangs from. Threads are one level
    # deep, as on Reels: replying to a reply joins the same thread, so parent
    # is always a top-level comment. Deleting it takes its replies with it.
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="replies",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_constraint=False,
        related_name="short_comments",
    )
    # Kept with the comment, as course Q&A does, so it still reads right if
    # the account is later renamed or removed.
    author_label = models.CharField(max_length=301)
    author_role_label = models.CharField(max_length=30)
    body = models.TextField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(fields=["short", "-created_at"], name="short_comment_list_idx"),
            models.Index(fields=["parent", "created_at"], name="short_reply_list_idx"),
        ]

    def __str__(self):
        return f"Comment on {self.short_id}"
