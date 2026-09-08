"""Delete guest accounts whose session has expired.

Guest rows are generated on "Sign in as guest" and never signed into again,
so they accumulate. Nothing in the project runs on a schedule yet, so this is
a command to run by hand or from cron:

    docker compose exec web python manage.py purge_guest_accounts

Deleting a user only works because accounts/0002 created Users_groups and
Users_user_permissions - the cascade goes through them, and before those
tables existed any delete raised ProgrammingError.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.middleware import GUEST_ID_PREFIX, GUEST_SESSION_SECONDS
from accounts.models import User


class Command(BaseCommand):
    help = "Delete expired guest accounts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--older-than", type=int, default=GUEST_SESSION_SECONDS,
            help="Age in seconds before a guest row is removed "
                 f"(default {GUEST_SESSION_SECONDS}, matching the session).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would go, delete nothing.",
        )

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(seconds=options["older_than"])

        # date_joined maps to createDate. Guests are matched on the ID prefix,
        # which is the only thing that marks a row as generated - there is no
        # is_guest column, and adding one would be a schema change.
        expired = User.objects.filter(
            nisit_id__startswith=GUEST_ID_PREFIX,
            date_joined__lt=cutoff,
        )
        count = expired.count()

        if options["dry_run"]:
            self.stdout.write(f"Would delete {count} expired guest account(s).")
            return

        expired.delete()
        self.stdout.write(self.style.SUCCESS(
            f"Deleted {count} expired guest account(s)."
        ))
