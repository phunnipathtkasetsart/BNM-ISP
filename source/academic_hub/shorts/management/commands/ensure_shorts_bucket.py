"""Create the MinIO bucket Shorts clips go into, if it is not there yet.

docker-compose runs this before the dev server starts, so a fresh clone
needs no manual step in the MinIO console. It never stops the app from
starting: if MinIO is not reachable it prints a warning and carries on,
because every page except Create Short works without it.
"""

import time
from argparse import ArgumentParser
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.files.storage import storages
from django.core.management.base import BaseCommand

MISSING_BUCKET_CODES = {"404", "NoSuchBucket", "NotFound"}
RETRY_SECONDS = 2


class Command(BaseCommand):
    """manage.py ensure_shorts_bucket [--attempts N]"""

    help = "Create the Shorts bucket in MinIO if it does not exist."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--attempts",
            type=int,
            default=15,
            help="How many times to try reaching MinIO (2 seconds apart).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if settings.SHORTS_STORAGE != "minio":
            self.stdout.write(
                "Shorts storage is local disk; no bucket needed."
            )
            return
        error = self._ensure_bucket(int(options["attempts"]))
        if error is not None:
            self.stderr.write(
                self.style.WARNING(
                    f"Could not reach the Shorts bucket in MinIO ({error}). "
                    "The app will start, but uploading a short will fail "
                    "until MinIO is up."
                )
            )

    def _ensure_bucket(self, attempts: int) -> Exception | None:
        """Find or create the bucket; the last error if that failed."""
        client = storages["shorts"].connection.meta.client  # type: ignore
        bucket = settings.SHORTS_BUCKET
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                client.head_bucket(Bucket=bucket)
                self.stdout.write(f'Shorts bucket "{bucket}" is ready.')
                return None
            except ClientError as error:
                code = str(error.response.get("Error", {}).get("Code", ""))
                if code not in MISSING_BUCKET_CODES:
                    # e.g. wrong credentials: retrying will not help.
                    return error
                client.create_bucket(Bucket=bucket)
                self.stdout.write(
                    self.style.SUCCESS(f'Created Shorts bucket "{bucket}".')
                )
                return None
            except BotoCoreError as error:  # MinIO starting or not running
                last_error = error
                if attempt < attempts:
                    time.sleep(RETRY_SECONDS)
        return last_error
