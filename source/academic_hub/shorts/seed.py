"""Sharing a set of shorts through git, so the whole team sees the same clips.

Clips live in each person's own MinIO, which git never sees. The seed is a
folder in the repo holding copies of the video files and a shorts.json
describing them (title, description, tags, who posted them):

    python manage.py export_shorts_seed   # you: your shorts -> shorts/seed/
    git add shorts/seed && git commit && git push
    python manage.py seed_shorts          # teammates: shorts/seed/ -> their MinIO

Likes and comments are not part of it: they belong to accounts that may not
exist on another machine.
"""

import json
import os
from pathlib import Path

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files import File
from django.db import transaction
from django.utils.text import slugify

from .forms import parse_tags
from .models import Short
from .services import attach_clip, resolve_tags
from .validators import clip_duration

MANIFEST = "shorts.json"
CLIP_DIR = "clips"
FORMAT_VERSION = 1


def default_seed_dir():
    return Path(apps.get_app_config("shorts").path) / "seed"


def export_seed(seed_dir):
    """Write every live short into seed_dir. Returns (count, total bytes).

    Oldest first, so seeding recreates them in the same order and the feed
    (newest first) looks the same on every machine. Clip files from an
    earlier export that are no longer needed are removed.
    """
    seed_dir = Path(seed_dir)
    clip_dir = seed_dir / CLIP_DIR
    clip_dir.mkdir(parents=True, exist_ok=True)

    entries, written, total = [], set(), 0
    shorts = (Short.objects.filter(archived_at__isnull=True)
              .select_related("author").prefetch_related("tags")
              .order_by("created_at", "pk"))
    for number, short in enumerate(shorts, start=1):
        extension = os.path.splitext(short.clip.name)[1].lower() or ".mp4"
        name = f"{number:02d}-{slugify(short.title)[:40] or 'short'}{extension}"
        with short.clip.storage.open(short.clip.name, "rb") as source, open(clip_dir / name, "wb") as target:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                target.write(chunk)
        total += (clip_dir / name).stat().st_size
        written.add(name)
        entries.append({
            "file": f"{CLIP_DIR}/{name}",
            "title": short.title,
            "description": short.description,
            "tags": [tag.label for tag in short.tags.all()],
            "author": short.author_id,
            "author_label": short.author_label,
            "original_name": short.original_name,
        })

    for stale in clip_dir.iterdir():
        if stale.is_file() and stale.name not in written:
            stale.unlink()

    manifest = {"version": FORMAT_VERSION, "shorts": entries}
    (seed_dir / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")
    return len(entries), total


def load_seed(seed_dir, log=lambda message: None):
    """Create the shorts described in seed_dir. Returns (added, skipped).

    Safe to run again: a short already there (same title, file name and
    size) is skipped, so re-running after a pull only adds what is new.
    """
    seed_dir = Path(seed_dir)
    manifest_path = seed_dir / MANIFEST
    if not manifest_path.exists():
        raise FileNotFoundError(f"No {MANIFEST} in {seed_dir}. Run export_shorts_seed first.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    User = get_user_model()

    added = skipped = 0
    for entry in manifest.get("shorts", []):
        path = seed_dir / entry["file"]
        if not path.exists():
            raise FileNotFoundError(f"{entry['file']} is listed in {MANIFEST} but missing.")
        size = path.stat().st_size
        original_name = entry.get("original_name") or path.name
        if Short.objects.filter(title=entry["title"], original_name=original_name, size=size).exists():
            skipped += 1
            log(f"  already there: {entry['title']}")
            continue

        # The person who posted it, if that account exists here too.
        author = User.objects.filter(pk=entry.get("author")).first() if entry.get("author") else None
        tags = parse_tags(", ".join(entry.get("tags") or [])) or [("shorts", "shorts")]
        with open(path, "rb") as handle:
            upload = File(handle, name=original_name)
            duration = clip_duration(upload)  # the same checks an upload gets
            short = Short(
                title=entry["title"],
                description=entry.get("description", ""),
                author=author,
                author_label=entry.get("author_label") or "Department",
            )
            try:
                with transaction.atomic():
                    tag_rows = resolve_tags(tags)
                    attach_clip(short, upload, duration)
                    short.save()
                    short.tags.set(tag_rows)
            except Exception:
                if short.clip.name:
                    short.clip.storage.delete(short.clip.name)
                raise
        added += 1
        log(f"  added: {entry['title']}")
    return added, skipped
