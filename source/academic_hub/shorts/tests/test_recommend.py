"""The feed order: shorts that fit the viewer's courses and likes first."""

from collections import Counter

from django.test import SimpleTestCase
from django.urls import reverse

from accounts.models import User as Account
from course.models import Course, CourseTA, Enrollment, Topic
from shorts.models import Short, ShortLike
from shorts.recommend import (
    Interests,
    course_score,
    course_texts,
    feed_order,
    interests_for,
    like_score,
    liked,
    related_tags,
    weight,
    weighted_order,
    words_of,
)
from shorts.tests.helpers import ShortsTestCase, make_user
from shorts.views import FEED_BATCH, MAX_ROUND

ROUNDS = 2000


def first_place_share(weights: dict[int, int], pk: int) -> float:
    """How often `pk` comes first over ROUNDS seeded rounds."""
    wins = sum(
        weighted_order(weights, seed)[0] == pk for seed in range(ROUNDS)
    )
    return wins / ROUNDS


class CourseWordTests(SimpleTestCase):
    """Turning course names into tag-style words."""

    def test_words_and_phrases_without_filler(self) -> None:
        self.assertEqual(
            words_of(["Data Structures and Algorithms"]),
            {"data", "structures", "algorithms", "data-structures"},
        )
        self.assertEqual(
            words_of(["Introduction to Calculus I"]), {"calculus"}
        )

    def test_thai_name_is_one_word(self) -> None:
        self.assertEqual(words_of(["การเรียนรู้"]), {"การเรียนรู้"})

    def test_subjects_bring_in_related_tags(self) -> None:
        isp = related_tags(words_of(["Individual Software Process"]))
        self.assertTrue({"program", "code", "python"} <= isp)
        maths = related_tags(words_of(["Calculus"]))
        self.assertTrue({"geometry", "algebra"} <= maths)
        self.assertNotIn("calculus", maths)  # the course's own word
        self.assertEqual(related_tags(words_of(["Art History"])), set())


class ScoreTests(SimpleTestCase):
    """Points per tag, the caps, and the weight."""

    interests = Interests(
        course_words=frozenset({"recursion"}),
        related_tags=frozenset({"python", "code"}),
        liked_tags={"python": 2, "sorting": 1},
        liked_shorts=frozenset({7}),
    )

    def test_course_points(self) -> None:
        self.assertEqual(course_score(["recursion"], self.interests), 2)
        self.assertEqual(course_score(["python"], self.interests), 1)
        self.assertEqual(course_score(["art"], self.interests), 0)
        capped = course_score(["recursion", "python", "code"], self.interests)
        self.assertEqual(capped, 3)

    def test_like_points(self) -> None:
        self.assertEqual(like_score(1, ["python"], self.interests), 2)
        self.assertEqual(
            like_score(1, ["python", "sorting"], self.interests), 3
        )
        self.assertEqual(like_score(1, ["art"], self.interests), 0)
        # A liked short does not push itself up.
        self.assertEqual(like_score(7, ["python"], self.interests), 0)

    def test_weight_runs_from_1_to_13(self) -> None:
        self.assertEqual(weight(1, ["art"], self.interests), 1)
        both = weight(1, ["recursion", "python", "sorting"], self.interests)
        self.assertEqual(both, 13)
        self.assertTrue(Interests().is_empty)
        self.assertFalse(self.interests.is_empty)


class WeightedOrderTests(SimpleTestCase):
    """The seeded weighted draw behind each round."""

    def test_same_seed_same_order_and_every_short_once(self) -> None:
        weights = {pk: 1 + pk % 3 for pk in range(1, 21)}
        order = weighted_order(weights, 42)
        self.assertEqual(order, weighted_order(weights, 42))
        self.assertEqual(sorted(order), list(range(1, 21)))
        self.assertNotEqual(order, weighted_order(weights, 43))

    def test_heavier_shorts_come_first_in_proportion(self) -> None:
        # Weight 5 among nine weight-1 shorts: first 5 / 14 ≈ 36% of the
        # time, against 1 / 10 for an even shuffle.
        weights = {1: 5, **{pk: 1 for pk in range(2, 11)}}
        self.assertAlmostEqual(
            first_place_share(weights, 1), 5 / 14, delta=0.04
        )
        even = dict.fromkeys(range(1, 11), 1)
        self.assertAlmostEqual(first_place_share(even, 1), 1 / 10, delta=0.03)


class CourseLookupTests(ShortsTestCase):
    """Which courses count for whom."""

    def test_enrolled_ta_and_owned_courses_with_topics(self) -> None:
        maths = Course.objects.create(name="Calculus", owner=self.lecturer)
        Topic.objects.create(course=maths, title="Limits")
        isp = Course.objects.create(
            name="Individual Software Process", owner=self.lecturer
        )
        Course.objects.create(
            name="Old Course",
            owner=self.lecturer,
            archived_at="2026-01-01T00:00:00Z",
        )
        Enrollment.objects.create(course=maths, student=self.student)
        CourseTA.objects.create(course=isp, user=self.student)
        self.assertEqual(
            sorted(course_texts(self.student)),
            ["Calculus", "Individual Software Process", "Limits"],
        )
        self.assertEqual(
            sorted(course_texts(self.lecturer)),
            ["Calculus", "Individual Software Process", "Limits"],
        )
        self.assertEqual(course_texts(self.department), [])

    def test_likes_count_tags_and_skip_archived_shorts(self) -> None:
        python = self.make_short("Py", tags=["python", "code"])
        loops = self.make_short("Loops", tags=["python"])
        gone = self.make_short("Gone", tags=["python"], archived=True)
        untagged = self.make_short("Plain")
        for short in (python, loops, gone, untagged):
            ShortLike.objects.create(short=short, user=self.student)
        counts, shorts = liked(self.student)
        self.assertEqual(counts, Counter({"python": 2, "code": 1}))
        self.assertEqual(shorts, {python.pk, loops.pk, untagged.pk})
        self.assertFalse(interests_for(self.student).is_empty)


class FeedOrderTests(ShortsTestCase):
    """The order the feed is actually served in."""

    def setUp(self) -> None:
        super().setUp()
        self.plain = [self.make_short(f"Plain {n}") for n in range(9)]

    def first_share(self, short: Short, viewer: Account) -> float:
        """How often `short` opens a round for `viewer` (400 rounds)."""
        wins = sum(
            feed_order(viewer, seed)[0] == short.pk for seed in range(400)
        )
        return wins / 400

    def test_no_courses_and_no_likes_keeps_newest_first(self) -> None:
        newest = self.make_short("Newest")
        order = feed_order(self.student, seed=1)
        self.assertEqual(order[0], newest.pk)
        self.assertEqual(order, feed_order(self.student, seed=2))

    def test_course_tags_come_first_more_often(self) -> None:
        course = Course.objects.create(name="Calculus", owner=self.lecturer)
        Enrollment.objects.create(course=course, student=self.student)
        geometry = self.make_short("Shapes", tags=["geometry"])  # +1 -> 3
        # Weight 3 among nine weight-1 shorts: 3 / 12 = 25% vs 10%.
        self.assertAlmostEqual(
            self.first_share(geometry, self.student), 3 / 12, delta=0.07
        )

    def test_likes_raise_shorts_with_the_same_tag(self) -> None:
        liked_one = self.make_short("Liked", tags=["recursion"])
        ShortLike.objects.create(short=liked_one, user=self.department)
        similar = self.make_short("Similar", tags=["recursion"])
        # Similar: like score 1 -> weight 3; the liked one stays at 1.
        share = self.first_share(similar, self.department)
        self.assertAlmostEqual(share, 3 / 13, delta=0.07)
        self.assertLess(self.first_share(liked_one, self.department), 0.15)


class FeedRoundTests(ShortsTestCase):
    """Paging through one round, and starting the next."""

    def setUp(self) -> None:
        super().setUp()
        self.shorts = [
            self.make_short(f"Clip {n}", tags=["python"] if n % 2 else [])
            for n in range(12)
        ]
        ShortLike.objects.create(short=self.shorts[1], user=self.student)
        self.client.force_login(self.student)

    def batch(
        self, offset: int, round_seed: int | str | None = None
    ) -> dict[str, object]:
        """One batch from the feed API."""
        query: dict[str, int | str] = {"offset": offset}
        if round_seed is not None:
            query["round"] = round_seed
        data: dict[str, object] = self.client.get(
            reverse("shorts:api_feed"), query
        ).json()
        return data

    def test_one_round_pages_through_every_short_once(self) -> None:
        page = self.client.get(reverse("shorts:feed")).context["feed"]
        first = [item["id"] for item in page["items"]]
        rest = self.batch(FEED_BATCH, page["round"])
        ids = first + [item["id"] for item in rest["items"]]  # type: ignore
        self.assertEqual(len(first), FEED_BATCH)
        self.assertEqual(sorted(ids), sorted(s.pk for s in self.shorts))
        self.assertEqual(rest["round"], page["round"])
        self.assertIsNone(rest["next"])

    def test_offset_zero_or_a_bad_round_starts_a_new_round(self) -> None:
        rounds = {self.batch(0)["round"] for _ in range(5)}
        self.assertGreater(len(rounds), 1)
        for bad in ("x", str(MAX_ROUND)):
            with self.subTest(round=bad):
                seed = self.batch(FEED_BATCH, bad)["round"]
                self.assertNotEqual(str(seed), bad)

    def test_new_user_without_likes_gets_newest_first(self) -> None:
        self.client.force_login(make_user(5, "Fresh"))
        items = self.batch(0)["items"]
        self.assertEqual(
            [item["title"] for item in items],  # type: ignore[attr-defined]
            [f"Clip {n}" for n in range(11, 3, -1)],
        )
