"""Search from the pop-down bar: exact #tags, and titles closest first."""

from typing import Any
from unittest.mock import patch

from django.urls import reverse

from announcements.models import Tag
from shorts.search import (
    ALL_WORDS,
    CONTAINS,
    EXACT,
    NEAR,
    STARTS,
    WORD_STARTS,
    Query,
    parse,
    search,
    title_rank,
)
from shorts.tests.helpers import ShortsTestCase


class ParseTests(ShortsTestCase):
    """Reading what was typed into the box."""

    def test_hash_means_a_tag_search(self) -> None:
        self.assertEqual(
            parse("  #Recursion "), Query("tag", tags=("recursion",))
        )

    def test_several_tags_and_repeats(self) -> None:
        self.assertEqual(
            parse("#ske  big-o #ske"), Query("tag", tags=("ske", "big-o"))
        )

    def test_a_lone_hash_is_a_tag_search_with_no_tag(self) -> None:
        self.assertEqual(parse("#"), Query("tag"))

    def test_anything_else_searches_titles(self) -> None:
        self.assertEqual(
            parse("  Big   O\tNotation "),
            Query("title", text="big o notation"),
        )

    def test_blank_is_empty(self) -> None:
        self.assertEqual(parse("   "), Query("empty"))
        self.assertEqual(search(parse("")), [])

    def test_long_queries_are_cut(self) -> None:
        self.assertEqual(len(parse("a" * 500).text), 100)


def tier(query: str, title: str) -> int:
    """The tier of a title that is expected to match."""
    rank = title_rank(query, title)
    assert rank is not None
    return rank[0]


class TitleRankTests(ShortsTestCase):
    """How close a title is to the words typed."""

    def test_tiers(self) -> None:
        q = "recursion"
        self.assertEqual(title_rank(q, "Recursion"), (EXACT, 1.0))
        self.assertEqual(tier(q, "Recursion basics"), STARTS)
        self.assertEqual(tier(q, "Intro to recursion"), WORD_STARTS)
        self.assertEqual(tier(q, "Corecursion"), CONTAINS)
        self.assertEqual(tier("intro rec", "Intro to recursion"), ALL_WORDS)
        self.assertEqual(tier(q, "Recursoin made easy"), NEAR)
        self.assertIsNone(title_rank(q, "Big-O notation"))

    def test_shorter_titles_are_closer_within_a_tier(self) -> None:
        short = title_rank("loops", "Loops in C")
        long = title_rank("loops", "Loops in C, Java and Python")
        assert short is not None and long is not None
        self.assertGreater(short, long)

    def test_case_and_spacing_do_not_matter(self) -> None:
        self.assertEqual(title_rank("big o", "  BIG   O "), (EXACT, 1.0))

    def test_nothing_to_compare(self) -> None:
        self.assertIsNone(title_rank("", "Recursion"))
        self.assertIsNone(title_rank("recursion", "   "))


class SearchTests(ShortsTestCase):
    """What a search finds, and in what order."""

    def titles(self, raw: str) -> list[str]:
        """Titles found for what was typed, in the order shown."""
        return [short.title for short in search(parse(raw))]

    def test_titles_closest_first(self) -> None:
        for title in [
            "Big-O notation",
            "Recursoin made easy",
            "Corecursion",
            "Intro to recursion",
            "Recursion basics",
            "Recursion",
        ]:
            self.make_short(title)
        self.assertEqual(
            self.titles("recursion"),
            [
                "Recursion",
                "Recursion basics",
                "Intro to recursion",
                "Corecursion",
                "Recursoin made easy",
            ],
        )

    def test_equally_close_titles_newest_first(self) -> None:
        self.make_short("Graphs one")
        self.make_short("Graphs two")
        self.assertEqual(self.titles("graphs"), ["Graphs two", "Graphs one"])

    def test_tag_search_is_exact(self) -> None:
        self.make_short("Exact", tags=["recursion"])
        self.make_short("Longer tag", tags=["recursion-2"])
        self.make_short("Shorter tag", tags=["rec"])
        self.make_short("Title only: recursion")
        self.assertEqual(self.titles("#recursion"), ["Exact"])
        self.assertEqual(self.titles("#RECURSION"), ["Exact"])
        self.assertEqual(self.titles("#recur"), [])

    def test_tag_search_matches_the_label_too(self) -> None:
        tag = Tag.objects.create(slug="big-o-notation", label="Big O")
        short = self.make_short("Labelled")
        short.tags.add(tag)
        self.assertEqual(self.titles("#big o"), [])  # two tags: big, o
        self.assertEqual(self.titles("#big-o-notation"), ["Labelled"])

    def test_several_tags_need_all_of_them(self) -> None:
        self.make_short("Both", tags=["ske", "big-o"])
        self.make_short("One", tags=["ske"])
        self.assertEqual(self.titles("#ske #big-o"), ["Both"])
        self.assertEqual(self.titles("#ske"), ["One", "Both"])

    def test_a_lone_hash_finds_nothing(self) -> None:
        self.make_short("Clip", tags=["ske"])
        self.assertEqual(self.titles("#"), [])

    def test_archived_shorts_are_not_found(self) -> None:
        self.make_short("Recursion", tags=["ske"], archived=True)
        self.assertEqual(self.titles("recursion"), [])
        self.assertEqual(self.titles("#ske"), [])


class SearchApiTests(ShortsTestCase):
    """GET /shorts/api/search/?q= as search.js uses it."""

    def ask(self, q: str) -> dict[str, Any]:
        """The API's answer for q, signed in as a student."""
        self.client.force_login(self.student)
        response = self.client.get(reverse("shorts:api_search"), {"q": q})
        self.assertEqual(response.status_code, 200)
        data: dict[str, Any] = response.json()
        return data

    def test_needs_sign_in(self) -> None:
        response = self.client.get(reverse("shorts:api_search"), {"q": "x"})
        self.assertEqual(response.status_code, 302)

    def test_title_results(self) -> None:
        # "ske" already exists as the SKE programme tag; use a new one.
        short = self.make_short("Recursion", tags=["recursion"])
        data = self.ask("recur")
        self.assertEqual(data["kind"], "title")
        self.assertEqual(data["total"], 1)
        self.assertEqual(
            data["items"],
            [
                {
                    "id": short.pk,
                    "title": "Recursion",
                    "tags": ["recursion"],
                    "length": "0:30",
                    "src": reverse("shorts:clip", args=[short.pk]),
                    "url": reverse("shorts:feed") + f"?start={short.pk}",
                }
            ],
        )

    def test_tag_results(self) -> None:
        self.make_short("Tagged", tags=["ske"])
        data = self.ask("#SKE")
        self.assertEqual(data["kind"], "tag")
        self.assertEqual(data["tags"], ["ske"])
        self.assertEqual([i["title"] for i in data["items"]], ["Tagged"])

    def test_blank_query(self) -> None:
        self.make_short("Clip")
        data = self.ask("")
        self.assertEqual(
            data, {"kind": "empty", "tags": [], "items": [], "total": 0}
        )

    def test_results_are_capped_but_the_total_is_not(self) -> None:
        for n in range(3):
            self.make_short(f"Clip {n}")
        with patch("shorts.views.MAX_RESULTS", 2):
            data = self.ask("clip")
        self.assertEqual(len(data["items"]), 2)
        self.assertEqual(data["total"], 3)

    def test_feed_page_has_the_results_area(self) -> None:
        self.make_short("Clip")
        self.client.force_login(self.student)
        response = self.client.get(reverse("shorts:feed"))
        self.assertContains(response, "data-shorts-results")
        self.assertContains(response, 'id="result-template"')
        self.assertContains(response, reverse("shorts:api_search"))
