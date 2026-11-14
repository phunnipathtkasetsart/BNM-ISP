"""Copy your current shorts into the repo so they can be pushed.

    docker compose exec web python manage.py export_shorts_seed

Writes shorts/seed/shorts.json and shorts/seed/clips/. Commit that folder.
"""

from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand

from shorts.seed import CLIP_DIR, default_seed_dir, export_seed

# GitHub refuses any single file over 100 MB and warns from 50 MB.
GITHUB_FILE_LIMIT = 100 * 1024 * 1024
WARN_TOTAL = 200 * 1024 * 1024


class Command(BaseCommand):
    """manage.py export_shorts_seed [--dir PATH]"""

    help = "Export every live short (clip + details) into shorts/seed/."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--dir",
            default=None,
            help="Where to write the seed (default: shorts/seed).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        seed_dir = Path(options["dir"]) if options["dir"] else None
        seed_dir = seed_dir or default_seed_dir()
        count, total = export_seed(seed_dir)
        self.stdout.write(
            self.style.SUCCESS(
                f"Exported {count} short(s), {total / 1048576:.1f} MB, "
                f"to {seed_dir}"
            )
        )
        self._warn_about_size(seed_dir / CLIP_DIR, total)
        self.stdout.write(
            "Next, from the repo root: git add "
            "source/academic_hub/shorts/seed, then commit and push."
        )

    def _warn_about_size(self, clip_dir: Path, total: int) -> None:
        big = [
            path.name
            for path in clip_dir.iterdir()
            if path.stat().st_size > GITHUB_FILE_LIMIT
        ]
        if big:
            self.stderr.write(
                self.style.ERROR(
                    "GitHub will reject these clips (over 100 MB each): "
                    + ", ".join(big)
                )
            )
        elif total > WARN_TOTAL:
            self.stderr.write(
                self.style.WARNING(
                    "The seed is large; every clone of the repo will "
                    "download it. Consider exporting fewer or shorter clips."
                )
            )
