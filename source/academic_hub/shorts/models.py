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
