"""The Create Short / Edit Short form, and how typed tags are read."""

import re
import unicodedata
from typing import Any

from django import forms
from django.core.files.uploadedfile import UploadedFile

from .services import TagPair
from .validators import ALLOWED_EXTENSIONS, clip_duration

MAX_TAGS = 5
MAX_TAG_LENGTH = 40


def tag_slug(label: str) -> str:
    """ "Data Structures" -> "data-structures", keeping any script's letters.

    Not django's slugify: it drops combining marks, which are part of Thai
    words, so "การเรียน" would come out as "การเรยน".
    """
    kept = []
    for char in unicodedata.normalize("NFC", label.lower()):
        if char.isalnum() or unicodedata.category(char).startswith("M"):
            kept.append(char)
        elif char in " -_":
            kept.append("-")
    return re.sub(r"-{2,}", "-", "".join(kept)).strip("-")


def _clean_label(part: str) -> str:
    """One comma-separated piece: no leading #, single spaces, checked."""
    label = re.sub(r"\s+", " ", part.strip().lstrip("#").strip())
    if len(label) > MAX_TAG_LENGTH:
        raise forms.ValidationError(
            f'"{label[:20]}…" is too long. '
            f"Keep each tag under {MAX_TAG_LENGTH} characters."
        )
    return label


def parse_tags(raw: str) -> list[TagPair]:
    """ "#Recursion, data structures" -> [("recursion", "Recursion"), ...].

    Commas separate tags, so a tag may contain spaces. A leading # is
    optional. Duplicates (by slug) are dropped, keeping the first spelling.
    """
    seen: set[str] = set()
    tags: list[TagPair] = []
    for part in raw.split(","):
        label = _clean_label(part)
        if not label:
            continue
        slug = tag_slug(label)
        if not slug:
            raise forms.ValidationError(
                f'"{label}" needs at least one letter or number.'
            )
        if slug not in seen:
            seen.add(slug)
            tags.append((slug, label))
    return tags


class ShortForm(forms.Form):
    """Title, description, tags and clip for a new or edited short.

    CharField trims surrounding spaces itself, so a title of only spaces
    is "required" and the description needs no cleaning of its own.
    """

    title = forms.CharField(max_length=150)
    description = forms.CharField(
        required=False,
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Optional. What students will learn from this clip.",
    )
    tags = forms.CharField(
        label="Tags",
        max_length=300,
        widget=forms.TextInput(
            attrs={"placeholder": "e.g. recursion, python, ske"}
        ),
        help_text=f"Separate tags with commas. Up to {MAX_TAGS}.",
    )
    clip = forms.FileField(
        label="Clip",
        widget=forms.FileInput(
            attrs={
                "accept": ",".join(
                    ["video/mp4", "video/quicktime"]
                    + [f".{ext}" for ext in sorted(ALLOWED_EXTENSIONS)]
                ),
            }
        ),
        help_text="MP4 or MOV, up to 1:30 long.",
    )

    def __init__(
        self, *args: Any, editing: bool = False, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self.clip_duration: float | None = None
        if editing:
            # Editing keeps the current clip unless a new one is chosen.
            field = self.fields["clip"]
            field.required = False
            field.label = "Replace clip"
            field.help_text = (
                "Optional. MP4 or MOV, up to 1:30 long. "
                "Leave empty to keep the current clip."
            )

    def clean_tags(self) -> list[TagPair]:
        """1 to MAX_TAGS (slug, label) pairs."""
        tags = parse_tags(self.cleaned_data["tags"])
        if not tags:
            raise forms.ValidationError("Add at least one tag.")
        if len(tags) > MAX_TAGS:
            raise forms.ValidationError(f"Use at most {MAX_TAGS} tags.")
        return tags

    def clean_clip(self) -> UploadedFile | None:
        """The checked upload, or None when editing without a new clip."""
        clip: UploadedFile | None = self.cleaned_data["clip"]
        if not clip:  # only possible when editing: keep the clip there is
            return None
        # Kept on the form so the view does not have to read the file twice.
        self.clip_duration = clip_duration(clip)
        return clip
