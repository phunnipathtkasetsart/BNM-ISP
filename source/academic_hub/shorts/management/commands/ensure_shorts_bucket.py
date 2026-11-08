"""Create the MinIO bucket Shorts clips go into, if it is not there yet.

docker-compose runs this before the dev server starts, so a fresh clone
needs no manual step in the MinIO console. It never stops the app from
starting: if MinIO is not reachable it prints a warning and carries on,
because every page except Create Short works without it.
"""

import time

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.files.storage import storages
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create the Shorts bucket in MinIO if it does not exist."

    def add_arguments(self, parser):
        parser.add_argument("--attempts", type=int, default=15,
                            help="How many times to try reaching MinIO (2 seconds apart).")

    def handle(self, *args, attempts, **options):
        if settings.SHORTS_STORAGE != "minio":
            self.stdout.write("Shorts storage is local disk; no bucket needed.")
            return

        client = storages["shorts"].connection.meta.client
        bucket = settings.SHORTS_BUCKET
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                client.head_bucket(Bucket=bucket)
                self.stdout.write(f'Shorts bucket "{bucket}" is ready.')
                return
            except ClientError as error:
                code = str(error.response.get("Error", {}).get("Code", ""))
                if code in {"404", "NoSuchBucket", "NotFound"}:
                    client.create_bucket(Bucket=bucket)
                    self.stdout.write(self.style.SUCCESS(f'Created Shorts bucket "{bucket}".'))
                    return
                last_error = error  # e.g. wrong credentials: retrying will not help
                break
            except BotoCoreError as error:  # MinIO still starting, or not running
                last_error = error
                if attempt < attempts:
                    time.sleep(2)

        self.stderr.write(self.style.WARNING(
            f"Could not reach the Shorts bucket in MinIO ({last_error}). "
            "The app will start, but uploading a short will fail until MinIO is up."
        ))
