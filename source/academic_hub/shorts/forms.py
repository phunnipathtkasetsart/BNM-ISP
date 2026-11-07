import re
import unicodedata

from django import forms

from .validators import ALLOWED_EXTENSIONS, clip_duration

MAX_TAGS = 5
MAX_TAG_LENGTH = 40


def tag_slug(label):
    """"Data Structures" -> "data-structures", keeping any script's letters.

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


def parse_tags(raw):
    """"#Recursion, data structures, ske" -> [("recursion", "Recursion"), ...].

    Commas separate tags, so a tag may contain spaces. A leading # is
    optional. Duplicates (by slug) are dropped, keeping the first spelling.
    """
    seen, tags = set(), []
    for part in raw.split(","):
        label = re.sub(r"\s+", " ", part.strip().lstrip("#").strip())
        if not label:
            continue
        if len(label) > MAX_TAG_LENGTH:
            raise forms.ValidationError(
                f'"{label[:20]}…" is too long. Keep each tag under {MAX_TAG_LENGTH} characters.'
            )
        slug = tag_slug(label)
        if not slug:
            raise forms.ValidationError(f'"{label}" needs at least one letter or number.')
        if slug not in seen:
            seen.add(slug)
            tags.append((slug, label))
    return tags


class ShortForm(forms.Form):
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
        widget=forms.TextInput(attrs={"placeholder": "e.g. recursion, python, ske"}),
        help_text=f"Separate tags with commas. Up to {MAX_TAGS}.",
    )
    clip = forms.FileField(
        label="Clip",
        widget=forms.FileInput(attrs={
            "accept": ",".join(["video/mp4", "video/quicktime"]
                               + [f".{ext}" for ext in sorted(ALLOWED_EXTENSIONS)]),
        }),
        help_text="MP4 or MOV, up to 1:30 long.",
    )

    def __init__(self, *args, editing=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.clip_duration = None
        if editing:
            # Editing keeps the current clip unless a new one is chosen.
            self.fields["clip"].required = False
            self.fields["clip"].label = "Replace clip"
            self.fields["clip"].help_text = (
                "Optional. MP4 or MOV, up to 1:30 long. Leave empty to keep the current clip."
            )

    def clean_title(self):
        title = self.cleaned_data["title"].strip()
        if not title:
            raise forms.ValidationError("Give the short a title.")
        return title

    def clean_description(self):
        return self.cleaned_data["description"].strip()

    def clean_tags(self):
        tags = parse_tags(self.cleaned_data["tags"])
        if not tags:
            raise forms.ValidationError("Add at least one tag.")
        if len(tags) > MAX_TAGS:
            raise forms.ValidationError(f"Use at most {MAX_TAGS} tags.")
        return tags

    def clean_clip(self):
        clip = self.cleaned_data["clip"]
        if not clip:  # only possible when editing: keep the clip there is
            return None
        # Kept on the form so the view does not have to read the file twice.
        self.clip_duration = clip_duration(clip)
        return clip
