"""Load the team's shared shorts from shorts/seed/ into this machine.

    docker compose exec web python manage.py seed_shorts

Uploads each clip to this machine's storage (MinIO under Docker) and
creates its short. Safe to run again: shorts already there are skipped.
"""

from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from shorts.seed import default_seed_dir, load_seed


class Command(BaseCommand):
    """manage.py seed_shorts [--dir PATH]"""

    help = "Create the shorts in shorts/seed/ (skips ones already here)."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--dir",
            default=None,
            help="Where the seed is (default: shorts/seed).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            added, skipped = load_seed(
                options["dir"] or default_seed_dir(), log=self.stdout.write
            )
        except FileNotFoundError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded shorts: {added} added, {skipped} already there."
            )
        )
