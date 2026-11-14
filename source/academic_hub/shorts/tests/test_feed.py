"""The reels feed page, its JSON batches and ?start=."""

from typing import Any

from django.urls import reverse

from shorts.models import Short
from shorts.tests.helpers import ShortsTestCase, make_user


class FeedTests(ShortsTestCase):
    """The reels page and its JSON batches."""

    def test_empty_feed_shows_the_empty_panel(self) -> None:
        self.client.force_login(self.student)
        response = self.client.get(reverse("shorts:feed"))
        self.assertContains(response, "No shorts yet.")
        self.assertNotContains(response, "reels-data")

    def test_feed_hands_the_player_titles_descriptions_and_tags(self) -> None:
        old = self.make_short("Older", tags=["recursion"])
        new = self.make_short("Newer", tags=["big-o", "ske"])
        self.make_short("Hidden", archived=True)
        # A CPE student: no short fits them, so the feed is newest first.
        self.client.force_login(make_user(6, "Cpe", department="cpe"))
        response = self.client.get(reverse("shorts:feed"))
        self.assertContains(response, 'id="reel-template"')
        items = response.context["feed"]["items"]
        self.assertEqual(
            [i["title"] for i in items], ["Newer", "Older"]
        )  # newest first
        self.assertEqual(items[0]["description"], "About Newer")
        self.assertEqual(
            sorted(items[0]["tags"]), ["SKE", "big-o"]
        )  # labels, not slugs
        self.assertEqual(
            items[1]["src"], reverse("shorts:clip", args=[old.pk])
        )
        self.assertIsNone(response.context["feed"]["next"])
        # The data reaches the page as JSON for reels.js.
        self.assertContains(
            response, '<script id="reels-data" type="application/json">'
        )
        self.assertNotContains(response, "Hidden")
        self.assertNotIn(
            new.clip.name, response.content.decode()
        )  # no storage paths leak

    def test_feed_api_pages_through_and_signals_the_end(self) -> None:
        for n in range(10):
            self.make_short(f"Clip {n}")
        self.client.force_login(self.student)
        first = self.client.get(
            reverse("shorts:api_feed"), {"offset": 0}
        ).json()
        self.assertEqual(len(first["items"]), 8)
        self.assertEqual(first["next"], 8)
        second = self.client.get(
            reverse("shorts:api_feed"), {"offset": 8}
        ).json()
        self.assertEqual(
            [i["title"] for i in second["items"]], ["Clip 1", "Clip 0"]
        )
        self.assertIsNone(second["next"])  # the page starts over from offset 0

    def test_feed_api_rejects_a_bad_offset(self) -> None:
        self.client.force_login(self.student)
        for bad in ("-1", "abc"):
            response = self.client.get(
                reverse("shorts:api_feed"), {"offset": bad}
            )
            self.assertEqual(response.status_code, 400)

    def test_signed_out_visitors_get_nothing(self) -> None:
        short = self.make_short()
        for url in (
            reverse("shorts:api_feed"),
            reverse("shorts:clip", args=[short.pk]),
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse("accounts:login"), response["Location"])


class StartAtShortTests(ShortsTestCase):
    """Edit Short's View opens the feed on that short: /shorts/?start=<id>."""

    def setUp(self) -> None:
        super().setUp()
        self.shorts = [
            self.make_short(f"Clip {n}") for n in range(10)
        ]  # Clip 9 is newest
        self.client.force_login(self.student)

    def titles(self, response: Any) -> list[str]:
        """The titles of the first batch, in order."""
        return [item["title"] for item in response.context["feed"]["items"]]

    def test_start_puts_that_short_first_without_repeating_it(self) -> None:
        target = self.shorts[5]  # inside the first batch of 8
        titles = self.titles(
            self.client.get(reverse("shorts:feed"), {"start": target.pk})
        )
        self.assertEqual(titles[0], "Clip 5")
        self.assertEqual(titles.count("Clip 5"), 1)
        self.assertEqual(
            titles[1:],
            [
                "Clip 9",
                "Clip 8",
                "Clip 7",
                "Clip 6",
                "Clip 4",
                "Clip 3",
                "Clip 2",
            ],
        )

    def test_start_on_an_older_short_keeps_the_usual_batch_after_it(
        self,
    ) -> None:
        target = self.shorts[0]  # not in the first batch
        response = self.client.get(
            reverse("shorts:feed"), {"start": target.pk}
        )
        titles = self.titles(response)
        self.assertEqual(titles[0], "Clip 0")
        self.assertEqual(len(titles), 9)
        self.assertEqual(
            response.context["feed"]["next"], 8
        )  # paging carries on unchanged

    def test_unknown_or_archived_start_is_ignored(self) -> None:
        Short.objects.filter(pk=self.shorts[3].pk).update(
            archived_at="2026-01-01T00:00:00Z"
        )
        for start in ("999999", str(self.shorts[3].pk), "abc"):
            titles = self.titles(
                self.client.get(reverse("shorts:feed"), {"start": start})
            )
            self.assertEqual(titles[0], "Clip 9")

    def test_edit_short_links_to_the_feed(self) -> None:
        self.shorts[2].author = self.lecturer
        self.client.force_login(self.lecturer)
        link = f'{reverse("shorts:feed")}?start={self.shorts[2].pk}'
        self.assertContains(self.client.get(reverse("shorts:manage")), link)
        self.assertContains(
            self.client.get(reverse("shorts:edit", args=[self.shorts[2].pk])),
            "View in feed",
        )
