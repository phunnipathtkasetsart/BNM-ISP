"""ensure_shorts_bucket: creating the MinIO bucket at start-up."""

from io import StringIO
from typing import Any
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError, EndpointConnectionError
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code}}, "HeadBucket")


@override_settings(SHORTS_STORAGE="minio", SHORTS_BUCKET="shorts")
class EnsureBucketTests(SimpleTestCase):
    """The command creates a missing bucket and never blocks start-up."""

    sleeps = 0

    def run_command(self, client: MagicMock) -> tuple[str, str]:
        """Run against a fake S3 client; (stdout, stderr)."""
        storage: Any = MagicMock()
        storage.connection.meta.client = client
        out, err = StringIO(), StringIO()
        with patch.dict(
            "shorts.management.commands.ensure_shorts_bucket.storages."
            "_storages",
            {"shorts": storage},
        ), patch(
            "shorts.management.commands.ensure_shorts_bucket.time.sleep"
        ) as sleep:
            call_command(
                "ensure_shorts_bucket",
                "--attempts",
                "3",
                stdout=out,
                stderr=err,
            )
        self.sleeps = sleep.call_count
        return out.getvalue(), err.getvalue()

    @override_settings(SHORTS_STORAGE="local")
    def test_local_storage_needs_no_bucket(self) -> None:
        out = StringIO()
        call_command("ensure_shorts_bucket", stdout=out)
        self.assertIn("no bucket needed", out.getvalue())

    def test_existing_bucket(self) -> None:
        client = MagicMock()
        out, err = self.run_command(client)
        self.assertIn('bucket "shorts" is ready', out)
        self.assertEqual(err, "")
        client.create_bucket.assert_not_called()

    def test_missing_bucket_is_created(self) -> None:
        client = MagicMock()
        client.head_bucket.side_effect = _client_error("404")
        out, _ = self.run_command(client)
        self.assertIn('Created Shorts bucket "shorts"', out)
        client.create_bucket.assert_called_once_with(Bucket="shorts")

    def test_wrong_credentials_warn_without_retrying(self) -> None:
        client = MagicMock()
        client.head_bucket.side_effect = _client_error("403")
        _, err = self.run_command(client)
        self.assertIn("Could not reach the Shorts bucket", err)
        self.assertEqual(client.head_bucket.call_count, 1)

    def test_unreachable_minio_is_retried_then_warned(self) -> None:
        client = MagicMock()
        client.head_bucket.side_effect = EndpointConnectionError(
            endpoint_url="http://minio:9000"
        )
        _, err = self.run_command(client)
        self.assertIn("uploading a short will fail until MinIO is up", err)
        self.assertEqual(client.head_bucket.call_count, 3)
        self.assertEqual(self.sleeps, 2)  # not after the last attempt
