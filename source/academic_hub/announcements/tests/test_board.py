from datetime import timedelta
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from ..models import Announcement, Audience


class DateFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from datetime import datetime, timezone as tz
        cls.old = Announcement.objects.create(
            title="Older notice", body="b", audience=Audience.PUBLIC,
            published_at=datetime(2026, 1, 5, 9, 0, tzinfo=tz.utc),
        )
        cls.recent = Announcement.objects.create(
            title="Recent notice", body="b", audience=Audience.PUBLIC,
            published_at=datetime(2026, 6, 20, 9, 0, tzinfo=tz.utc),
        )

    def board(self, **params):
        self.client.post(reverse("accounts:guest_login"))
        return self.client.get(reverse("announcements:public_board"), params)

    def test_from_date_excludes_earlier_items(self):
        response = self.board(**{"from": "2026-03-01"})
        self.assertContains(response, "Recent notice")
        self.assertNotContains(response, "Older notice")

    def test_to_date_excludes_later_items(self):
        response = self.board(**{"to": "2026-03-01"})
        self.assertContains(response, "Older notice")
        self.assertNotContains(response, "Recent notice")

    def test_a_single_day_includes_that_whole_day(self):
        """Posted at 09:00, so comparing against midnight would drop it."""
        response = self.board(**{"from": "2026-06-20", "to": "2026-06-20"})
        self.assertContains(response, "Recent notice")

    def test_an_unparseable_date_narrows_nothing(self):
        response = self.board(**{"from": "notadate"})
        self.assertContains(response, "Older notice")
        self.assertContains(response, "Recent notice")

    def test_impossible_dates_are_ignored(self):
        for parameter in ("from", "to"):
            for value in ("2026-02-30", "2026-13-01", "0000-01-01"):
                with self.subTest(parameter=parameter, value=value):
                    response = self.board(**{parameter: value})
                    self.assertContains(response, "Older notice")
                    self.assertContains(response, "Recent notice")
                    self.assertFalse(response.context["date_filtered"])

    def test_invalid_date_keeps_the_other_valid_boundary(self):
        for params, included, excluded in (
            ({"from": "2026-02-30", "to": "2026-03-01"}, "Older notice", "Recent notice"),
            ({"from": "2026-03-01", "to": "2026-02-30"}, "Recent notice", "Older notice"),
        ):
            with self.subTest(params=params):
                response = self.board(**params)
                self.assertContains(response, included)
                self.assertNotContains(response, excluded)
                self.assertTrue(response.context["date_filtered"])


class UrgentPinTests(TestCase):
    """Urgent posts sit on top only until their deadline passes."""

    @classmethod
    def setUpTestData(cls):
        from accounts.models import User
        cls.student = User.objects.create_user(
            nisit_id="7700000001", email="u7@ku.th", password="pin-12345",
            first_name="Stu", last_name="P",
        )
        now = timezone.now()
        cls.expired = Announcement.objects.create(
            title="Old urgent notice", body="b", audience=Audience.PUBLIC,
            is_urgent=True, deadline=now - timedelta(days=2),
        )
        cls.live = Announcement.objects.create(
            title="Live urgent notice", body="b", audience=Audience.PUBLIC,
            is_urgent=True, deadline=now + timedelta(days=2),
        )
        cls.open_ended = Announcement.objects.create(
            title="Urgent with no deadline", body="b", audience=Audience.PUBLIC,
            is_urgent=True,
        )
        cls.newest = Announcement.objects.create(
            title="Fresh ordinary news", body="b", audience=Audience.PUBLIC,
        )
        # published_at controls the order among unpinned posts
        Announcement.objects.filter(pk=cls.expired.pk).update(published_at=now - timedelta(days=5))
        Announcement.objects.filter(pk=cls.newest.pk).update(published_at=now)

    def board_titles(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("announcements:public_board"))
        return [a.title for a in response.context["announcements"]], response

    def test_an_expired_urgent_post_drops_below_newer_posts(self):
        titles, _ = self.board_titles()
        self.assertLess(titles.index("Fresh ordinary news"), titles.index("Old urgent notice"))

    def test_live_urgent_posts_stay_on_top(self):
        titles, _ = self.board_titles()
        self.assertEqual(set(titles[:2]), {"Live urgent notice", "Urgent with no deadline"})

    def test_expired_urgent_post_loses_the_urgent_badge(self):
        self.assertFalse(Announcement.objects.get(pk=self.expired.pk).is_pinned)
        self.assertTrue(Announcement.objects.get(pk=self.live.pk).is_pinned)
        self.assertTrue(Announcement.objects.get(pk=self.open_ended.pk).is_pinned)
