"""Sharing a set of shorts through git, so the team sees the same clips.

Clips live in each person's own MinIO, which git never sees. The seed is a
folder in the repo holding copies of the video files and a shorts.json
describing them (title, description, tags, who posted them):

    python manage.py export_shorts_seed   # you: shorts -> shorts/seed/
    git add shorts/seed && git commit && git push
    python manage.py seed_shorts          # teammates: shorts/seed/ -> MinIO

Likes and comments are not part of it: they belong to accounts that may not
exist on another machine.
"""

import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from django.apps import apps
from django.core.files import File
from django.utils.text import slugify

from accounts.models import User as Account

from .forms import parse_tags
from .models import Short
from .services import save_short
from .validators import clip_duration

MANIFEST = "shorts.json"
CLIP_DIR = "clips"
FORMAT_VERSION = 1

Entry = dict[str, Any]
Log = Callable[[str], None]


def default_seed_dir() -> Path:
    """shorts/seed/ inside the app."""
    return Path(str(apps.get_app_config("shorts").path)) / "seed"


def _seed_file_name(number: int, short: Short) -> str:
    """ "03-recursion-basics.mp4": the order, then a readable title."""
    extension = os.path.splitext(short.clip.name)[1].lower() or ".mp4"
    return f"{number:02d}-{slugify(short.title)[:40] or 'short'}{extension}"


def _copy_clip(short: Short, target: Path) -> int:
    """Copy the stored clip to `target`; returns its size in bytes."""
    with short.clip.storage.open(short.clip.name, "rb") as source:
        with open(target, "wb") as copy:
            shutil.copyfileobj(source, copy, 1024 * 1024)
    return target.stat().st_size


def _entry_for(short: Short, file_name: str) -> Entry:
    return {
        "file": f"{CLIP_DIR}/{file_name}",
        "title": short.title,
        "description": short.description,
        "tags": [tag.label for tag in short.tags.all()],
        "author": short.author_id,
        "author_label": short.author_label,
        "original_name": short.original_name,
    }


def _remove_stale(clip_dir: Path, keep: set[str]) -> None:
    """Delete clip files from an earlier export that are not needed now."""
    for stale in clip_dir.iterdir():
        if stale.is_file() and stale.name not in keep:
            stale.unlink()


def export_seed(seed_dir: Path | str) -> tuple[int, int]:
    """Write every live short into seed_dir. Returns (count, total bytes).

    Oldest first, so seeding recreates them in the same order and the feed
    (newest first) looks the same on every machine.
    """
    seed_dir = Path(seed_dir)
    clip_dir = seed_dir / CLIP_DIR
    clip_dir.mkdir(parents=True, exist_ok=True)
    shorts = (
        Short.objects.filter(archived_at__isnull=True)
        .select_related("author")
        .prefetch_related("tags")
        .order_by("created_at", "pk")
    )
    entries, written, total = [], set(), 0
    for number, short in enumerate(shorts, start=1):
        file_name = _seed_file_name(number, short)
        total += _copy_clip(short, clip_dir / file_name)
        written.add(file_name)
        entries.append(_entry_for(short, file_name))
    _remove_stale(clip_dir, written)
    manifest = {"version": FORMAT_VERSION, "shorts": entries}
    (seed_dir / MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return len(entries), total


def _read_manifest(seed_dir: Path) -> list[Entry]:
    path = seed_dir / MANIFEST
    if not path.exists():
        raise FileNotFoundError(
            f"No {MANIFEST} in {seed_dir}. Run export_shorts_seed first."
        )
    shorts: list[Entry] = json.loads(path.read_text(encoding="utf-8")).get(
        "shorts", []
    )
    return shorts


def _clip_path(seed_dir: Path, entry: Entry) -> Path:
    path = seed_dir / str(entry["file"])
    if not path.exists():
        raise FileNotFoundError(
            f"{entry['file']} is listed in {MANIFEST} but missing."
        )
    return path


def _already_seeded(entry: Entry, original_name: str, size: int) -> bool:
    """Same title, file name and size: this short is already here."""
    return Short.objects.filter(
        title=entry["title"], original_name=original_name, size=size
    ).exists()


def _author(entry: Entry) -> Account | None:
    """The person who posted it, if that account exists here too."""
    author_id = entry.get("author")
    return Account.objects.filter(pk=author_id).first() if author_id else None


def _create_from_entry(entry: Entry, path: Path, original_name: str) -> None:
    """Upload one seed clip and create its short, as an upload would."""
    tags = parse_tags(", ".join(entry.get("tags") or [])) or [
        ("shorts", "shorts")
    ]
    short = Short(
        title=entry["title"],
        description=entry.get("description", ""),
        author=_author(entry),
        author_label=entry.get("author_label") or "Department",
    )
    with open(path, "rb") as handle:
        upload = File(handle, name=original_name)
        duration = clip_duration(upload)  # the same checks an upload gets
        save_short(short, tags=tags, clip=upload, duration_seconds=duration)


def load_seed(
    seed_dir: Path | str, log: Log = lambda message: None
) -> tuple[int, int]:
    """Create the shorts described in seed_dir. Returns (added, skipped).

    Safe to run again: a short already there (same title, file name and
    size) is skipped, so re-running after a pull only adds what is new.
    """
    seed_dir = Path(seed_dir)
    added = skipped = 0
    for entry in _read_manifest(seed_dir):
        path = _clip_path(seed_dir, entry)
        original_name = entry.get("original_name") or path.name
        if _already_seeded(entry, original_name, path.stat().st_size):
            skipped += 1
            log(f"  already there: {entry['title']}")
            continue
        _create_from_entry(entry, path, original_name)
        added += 1
        log(f"  added: {entry['title']}")
    return added, skipped
