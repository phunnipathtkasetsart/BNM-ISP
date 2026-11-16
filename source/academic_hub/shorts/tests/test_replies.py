"""Replies to comments: one-level threads."""

from typing import Any

from django.urls import reverse

from accounts.models import User as Account
from shorts.models import ShortComment
from shorts.tests.helpers import (
    ShortsTestCase,
)


class ReplyTests(ShortsTestCase):
    """Replies: one level deep, as on Reels."""

    def setUp(self) -> None:
        super().setUp()
        self.short = self.make_short("Recursion")
        self.comments_url = reverse(
            "shorts:api_comments", args=[self.short.pk]
        )

    def say(
        self,
        body: str,
        user: Account | None = None,
        reply_to: object = None,
    ) -> Any:
        """Post a comment, or a reply when `reply_to` is set."""
        self.client.force_login(user or self.student)
        data: dict[str, Any] = {"body": body}
        if reply_to is not None:
            data["reply_to"] = reply_to
        return self.client.post(
            self.comments_url, data, content_type="application/json"
        )

    def test_reply_joins_the_thread_and_counts(self) -> None:
        top = self.say("Why base case first?").json()["comment"]
        self.assertEqual(top["reply_count"], 0)
        self.assertIsNone(top["parent_id"])
        response = self.say(
            "Otherwise it never stops.", self.lecturer, reply_to=top["id"]
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["comment"]["parent_id"], top["id"])
        self.assertNotIn(
            "replies_url", data["comment"]
        )  # replies have no thread of their own
        self.assertEqual(data["reply_count"], 1)
        self.assertEqual(
            data["comment_count"], 2
        )  # the clip's count includes replies

    def test_list_shows_top_level_comments_with_reply_counts(self) -> None:
        top = self.say("Top").json()["comment"]
        self.say("Reply 1", self.lecturer, reply_to=top["id"])
        self.say("Reply 2", reply_to=top["id"])
        self.say("Another top")
        data = self.client.get(self.comments_url).json()
        self.assertEqual(
            [c["body"] for c in data["items"]], ["Another top", "Top"]
        )
        self.assertEqual([c["reply_count"] for c in data["items"]], [0, 2])
        self.assertEqual(data["comment_count"], 4)

    def test_replies_come_oldest_first_in_pages(self) -> None:
        top = self.say("Top").json()["comment"]
        for n in range(23):
            self.say(f"reply {n}", reply_to=top["id"])
        first = self.client.get(top["replies_url"]).json()
        self.assertEqual(len(first["items"]), 20)
        self.assertEqual(first["items"][0]["body"], "reply 0")
        self.assertEqual(first["reply_count"], 23)
        rest = self.client.get(
            top["replies_url"], {"after": first["next_after"]}
        ).json()
        self.assertEqual(
            [c["body"] for c in rest["items"]],
            ["reply 20", "reply 21", "reply 22"],
        )
        self.assertIsNone(rest["next_after"])

    def test_replying_to_a_reply_stays_in_the_same_thread(self) -> None:
        top = self.say("Top").json()["comment"]
        reply = self.say(
            "First reply", self.lecturer, reply_to=top["id"]
        ).json()["comment"]
        nested = self.say(
            "@Short Lecturer agreed", reply_to=reply["id"]
        ).json()
        self.assertEqual(nested["comment"]["parent_id"], top["id"])
        self.assertEqual(nested["reply_count"], 2)

    def test_reply_to_a_missing_or_foreign_comment_is_refused(self) -> None:
        other = self.make_short("Other clip")
        self.client.force_login(self.student)
        foreign = self.client.post(
            reverse("shorts:api_comments", args=[other.pk]),
            {"body": "elsewhere"},
            content_type="application/json",
        ).json()["comment"]
        self.assertEqual(
            self.say("hi", reply_to=foreign["id"]).status_code, 404
        )
        self.assertEqual(self.say("hi", reply_to=999999).status_code, 404)
        for bad in ("abc", True, 1.5):
            self.assertEqual(self.say("hi", reply_to=bad).status_code, 400)

    def test_replies_of_a_reply_cannot_be_listed(self) -> None:
        top = self.say("Top").json()["comment"]
        reply = self.say("r", reply_to=top["id"]).json()["comment"]
        url = reverse(
            "shorts:api_comment_replies", args=[self.short.pk, reply["id"]]
        )
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_deleting_a_comment_deletes_its_replies(self) -> None:
        top = self.say("Top").json()["comment"]
        self.say("r1", self.lecturer, reply_to=top["id"])
        self.say("r2", self.lecturer, reply_to=top["id"])
        self.client.force_login(self.student)
        data = self.client.post(top["delete_url"]).json()
        self.assertEqual(data["comment_count"], 0)
        self.assertFalse(ShortComment.objects.exists())

    def test_deleting_a_reply_updates_the_thread_count(self) -> None:
        top = self.say("Top").json()["comment"]
        mine = self.say("mine", self.lecturer, reply_to=top["id"]).json()[
            "comment"
        ]
        self.say("theirs", reply_to=top["id"])
        self.client.force_login(self.lecturer)
        data = self.client.post(mine["delete_url"]).json()
        self.assertEqual((data["reply_count"], data["comment_count"]), (1, 2))

    def test_only_the_author_or_department_can_delete_a_reply(self) -> None:
        top = self.say("Top", self.lecturer).json()["comment"]
        reply = self.say("student reply", reply_to=top["id"]).json()["comment"]
        self.client.force_login(
            self.lecturer
        )  # wrote the comment, not the reply
        listed = self.client.get(top["replies_url"]).json()["items"][0]
        self.assertIsNone(listed["delete_url"])
        self.assertEqual(
            self.client.post(reply["delete_url"]).status_code, 403
        )
        self.client.force_login(self.department)
        self.assertEqual(
            self.client.post(reply["delete_url"]).status_code, 200
        )
