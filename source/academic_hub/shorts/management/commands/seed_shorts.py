"""Load the team's shared shorts from shorts/seed/ into this machine.

    docker compose exec web python manage.py seed_shorts

Uploads each clip to this machine's storage (MinIO under Docker) and creates
its short. Safe to run again: shorts already there are skipped.
"""

from django.core.management.base import BaseCommand, CommandError

from shorts.seed import default_seed_dir, load_seed


class Command(BaseCommand):
    help = "Create the shorts in shorts/seed/ (skips ones already here)."

    def add_arguments(self, parser):
        parser.add_argument("--dir", default=None, help="Where the seed is (default: shorts/seed).")

    def handle(self, *args, dir=None, **options):
        try:
            added, skipped = load_seed(dir or default_seed_dir(), log=self.stdout.write)
        except FileNotFoundError as error:
            raise CommandError(str(error))
        self.stdout.write(self.style.SUCCESS(f"Seeded shorts: {added} added, {skipped} already there."))
