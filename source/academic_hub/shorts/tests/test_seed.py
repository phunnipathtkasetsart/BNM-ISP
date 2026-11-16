"""Exporting and loading the shared seed of shorts."""

import json
import pathlib
import tempfile
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError

from shorts.models import Short
from shorts.tests.helpers import (
    ShortsTestCase,
    mp4_bytes,
)


class SeedTests(ShortsTestCase):
    """export_shorts_seed / seed_shorts: sharing clips through git."""

    def setUp(self) -> None:
        super().setUp()

        # enterContext closes it after the test; pylint cannot see that.
        # pylint: disable-next=consider-using-with
        self.seed_dir = self.enterContext(tempfile.TemporaryDirectory())

    def run_command(self, *args: str) -> str:
        """Run a seed command on the temporary seed folder; its output."""
        out = StringIO()
        call_command(
            *args, "--dir", self.seed_dir, stdout=out, stderr=StringIO()
        )
        return out.getvalue()

    def test_round_trip_recreates_the_same_shorts(self) -> None:

        older = self.make_short(
            "Older", tags=["recursion"], body=mp4_bytes(20)
        )
        self.make_short("Newer", tags=["ske"], body=mp4_bytes(40))
        self.make_short("Hidden", archived=True)

        out = self.run_command("export_shorts_seed")
        self.assertIn("Exported 2 short(s)", out)
        manifest = json.loads(
            (pathlib.Path(self.seed_dir) / "shorts.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            [s["title"] for s in manifest["shorts"]], ["Older", "Newer"]
        )  # oldest first
        self.assertEqual(manifest["shorts"][0]["tags"], ["recursion"])
        self.assertEqual(manifest["shorts"][0]["author"], self.lecturer.pk)
        self.assertEqual(
            sorted(
                p.name
                for p in (pathlib.Path(self.seed_dir) / "clips").iterdir()
            ),
            ["01-older.mp4", "02-newer.mp4"],
        )

        # A teammate's machine: no shorts yet, fresh storage.
        Short.objects.all().delete()
        self.use_fresh_storage()

        out = self.run_command("seed_shorts")
        self.assertIn("2 added, 0 already there", out)
        seeded = list(Short.objects.order_by("created_at", "pk"))
        self.assertEqual([s.title for s in seeded], ["Older", "Newer"])
        self.assertEqual(
            [s.duration_seconds for s in seeded], [20, 40]
        )  # measured again on seeding
        self.assertEqual(seeded[0].author, self.lecturer)
        self.assertEqual([t.slug for t in seeded[0].tags.all()], ["recursion"])
        for short in seeded:
            self.assertTrue(self.storage.exists(short.clip.name))
        with self.storage.open(seeded[0].clip.name) as stored:
            self.assertEqual(stored.read(), mp4_bytes(20))
        self.assertNotEqual(seeded[0].pk, older.pk)

    def test_seeding_again_adds_nothing(self) -> None:
        self.make_short("Only")
        self.run_command("export_shorts_seed")
        Short.objects.all().delete()
        self.assertIn("1 added", self.run_command("seed_shorts"))
        self.assertIn(
            "0 added, 1 already there", self.run_command("seed_shorts")
        )
        self.assertEqual(Short.objects.count(), 1)

    def test_missing_author_keeps_the_label(self) -> None:

        self.make_short("Orphan")
        self.run_command("export_shorts_seed")
        path = pathlib.Path(self.seed_dir) / "shorts.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["shorts"][0][
            "author"
        ] = "L9999"  # no such account on this machine
        path.write_text(json.dumps(manifest), encoding="utf-8")
        Short.objects.all().delete()
        self.run_command("seed_shorts")
        short = Short.objects.get()
        self.assertIsNone(short.author)
        self.assertEqual(short.author_label, "Lecturer")

    def test_re_export_drops_clips_no_longer_needed(self) -> None:

        first = self.make_short("First")
        self.make_short("Second")
        self.run_command("export_shorts_seed")
        first.delete()
        self.run_command("export_shorts_seed")
        self.assertEqual(
            [
                p.name
                for p in (pathlib.Path(self.seed_dir) / "clips").iterdir()
            ],
            ["01-second.mp4"],
        )

    def test_seed_without_an_export_says_what_to_do(self) -> None:

        with self.assertRaisesMessage(
            CommandError, "Run export_shorts_seed first"
        ):
            self.run_command("seed_shorts")
