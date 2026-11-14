"""The like and comment JSON API."""

from typing import Any

from django.test import Client
from django.urls import reverse

from accounts.models import User as Account
from shorts.models import Short, ShortComment, ShortLike
from shorts.tests.helpers import (
    ShortsTestCase,
)


class LikeAndCommentTests(ShortsTestCase):
    """The like and comment endpoints reels.js calls."""

    def setUp(self) -> None:
        super().setUp()
        self.short = self.make_short("Recursion")
        self.like_url = reverse("shorts:api_like", args=[self.short.pk])
        self.comments_url = reverse(
            "shorts:api_comments", args=[self.short.pk]
        )

    def post_json(
        self, url: str, data: dict[str, Any], user: Account | None = None
    ) -> Any:
        """POST `data` as JSON, signed in as `user` (a student by default)."""
        self.client.force_login(user or self.student)
        return self.client.post(url, data, content_type="application/json")

    # --- likes ---
    def test_like_is_set_not_toggled(self) -> None:
        for _ in range(2):  # a double tap or a retry still counts once
            response = self.post_json(self.like_url, {"liked": True})
            self.assertEqual(response.json(), {"liked": True, "like_count": 1})
        self.post_json(self.like_url, {"liked": True}, self.lecturer)
        response = self.post_json(self.like_url, {"liked": False})
        self.assertEqual(response.json(), {"liked": False, "like_count": 1})
        response = self.post_json(self.like_url, {"liked": False})
        self.assertEqual(response.json()["like_count"], 1)

    def test_like_needs_a_true_or_false(self) -> None:
        for bad in ({}, {"liked": "yes"}, {"liked": 1}):
            self.assertEqual(
                self.post_json(self.like_url, bad).status_code, 400
            )

    def test_feed_reports_likes_and_comments_per_viewer(self) -> None:
        self.post_json(self.like_url, {"liked": True}, self.lecturer)
        self.post_json(self.comments_url, {"body": "Nice"}, self.lecturer)
        self.client.force_login(self.student)
        item = self.client.get(reverse("shorts:api_feed")).json()["items"][0]
        self.assertEqual(
            (item["like_count"], item["comment_count"], item["liked"]),
            (1, 1, False),
        )
        self.assertEqual(item["like_url"], self.like_url)
        self.assertEqual(item["comments_url"], self.comments_url)
        self.client.force_login(self.lecturer)
        self.assertTrue(
            self.client.get(reverse("shorts:api_feed")).json()["items"][0][
                "liked"
            ]
        )

    def test_page_carries_the_csrf_token_reels_js_sends(self) -> None:
        self.client.force_login(self.student)
        self.assertContains(
            self.client.get(reverse("shorts:feed")),
            'name="csrfmiddlewaretoken"',
        )

    def test_posts_without_the_csrf_token_are_refused(self) -> None:

        client = Client(enforce_csrf_checks=True)
        client.force_login(self.student)
        response = client.post(
            self.like_url, {"liked": True}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 403)

    # --- comments ---
    def test_comment_is_stored_and_returned(self) -> None:
        response = self.post_json(
            self.comments_url, {"body": "  Why is the base case first?  "}
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["comment_count"], 1)
        comment = data["comment"]
        self.assertEqual(comment["body"], "Why is the base case first?")
        self.assertEqual(comment["author"], "Short Student")
        self.assertEqual(comment["role"], "Student")
        self.assertTrue(comment["is_mine"])
        self.assertIsNotNone(comment["delete_url"])

    def test_roles_are_labelled(self) -> None:
        for user, role in (
            (self.lecturer, "Lecturer"),
            (self.department, "Department"),
        ):
            response = self.post_json(self.comments_url, {"body": "hi"}, user)
            self.assertEqual(response.json()["comment"]["role"], role)

    def test_empty_or_too_long_comments_are_refused(self) -> None:
        for bad in ({"body": "   "}, {}, {"body": "x" * 501}, {"body": 5}):
            self.assertEqual(
                self.post_json(self.comments_url, bad).status_code, 400
            )
        self.assertEqual(
            self.post_json(self.comments_url, {"body": "x" * 500}).status_code,
            201,
        )

    def test_comments_list_newest_first_in_pages(self) -> None:
        for n in range(25):
            self.post_json(self.comments_url, {"body": f"comment {n}"})
        first = self.client.get(self.comments_url).json()
        self.assertEqual(len(first["items"]), 20)
        self.assertEqual(first["items"][0]["body"], "comment 24")
        self.assertEqual(first["comment_count"], 25)
        rest = self.client.get(
            self.comments_url, {"before": first["next_before"]}
        ).json()
        self.assertEqual(
            [c["body"] for c in rest["items"]],
            [f"comment {n}" for n in range(4, -1, -1)],
        )
        self.assertIsNone(rest["next_before"])

    def test_only_the_author_or_department_can_delete(self) -> None:
        made = self.post_json(self.comments_url, {"body": "mine"}).json()[
            "comment"
        ]
        delete_url = made["delete_url"]
        # Someone else sees no delete link and is refused if they try anyway.
        self.client.force_login(self.lecturer)
        listed = self.client.get(self.comments_url).json()["items"][0]
        self.assertIsNone(listed["delete_url"])
        self.assertEqual(self.client.post(delete_url).status_code, 403)
        # The Department may moderate.
        self.client.force_login(self.department)
        self.assertIsNotNone(
            self.client.get(self.comments_url).json()["items"][0]["delete_url"]
        )
        response = self.client.post(delete_url)
        self.assertEqual(
            response.json(), {"deleted": True, "comment_count": 0}
        )

    def test_author_can_delete_own_comment(self) -> None:
        made = self.post_json(self.comments_url, {"body": "oops"}).json()[
            "comment"
        ]
        self.assertEqual(self.client.post(made["delete_url"]).status_code, 200)
        self.assertEqual(self.client.post(made["delete_url"]).status_code, 404)

    def test_signed_out_gets_json_401(self) -> None:
        for method, url in (
            ("post", self.like_url),
            ("get", self.comments_url),
            ("post", self.comments_url),
        ):
            response = getattr(self.client, method)(url)
            self.assertEqual(response.status_code, 401)
            self.assertIn("error", response.json())

    def test_archived_short_cannot_be_liked_or_commented(self) -> None:
        Short.objects.filter(pk=self.short.pk).update(
            archived_at="2026-01-01T00:00:00Z"
        )
        self.assertEqual(
            self.post_json(self.like_url, {"liked": True}).status_code, 404
        )
        self.assertEqual(
            self.post_json(self.comments_url, {"body": "hi"}).status_code, 404
        )

    def test_deleting_a_short_takes_its_likes_and_comments(self) -> None:
        self.post_json(self.like_url, {"liked": True})
        self.post_json(self.comments_url, {"body": "hi"})
        with self.captureOnCommitCallbacks(execute=True):
            self.short.delete()
        self.assertFalse(ShortLike.objects.exists())
        self.assertFalse(ShortComment.objects.exists())
