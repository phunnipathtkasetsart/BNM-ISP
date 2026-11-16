from django.urls import reverse
from notifications.models import Notification
from ..models import ForumQuestion, ForumReply
from .forum import ForumTestCase


class ForumReplyTests(ForumTestCase):
    def test_question_and_reply_text_is_autoescaped(self):
        question = ForumQuestion.objects.create(
            title="<script>alert(1)</script>",
            body="<img src=x onerror=alert(1)>",
            visibility=ForumQuestion.Visibility.EVERYONE,
        )
        ForumReply.objects.create(
            question=question,
            body="<script>reply</script>",
            author_label="Lecturer",
        )
        self.sign_in(self.lecturer)
        response = self.client.get(
            self.forum_url("faq_board"),
            {"q": question.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertNotContains(response, "<img src=x")
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;")

    def test_student_cannot_reply_but_lecturer_can(self):
        reply_url = self.forum_url("forum_reply_create", self.ske_question.pk)
        self.sign_in(self.student)
        denied = self.client.post(reply_url, {"body": "A student reply."})
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(self.ske_question.replies.exists())

        self.sign_in(self.lecturer)
        accepted = self.client.post(reply_url, {"body": "Official guidance."})
        self.assertEqual(accepted.status_code, 302)
        self.assertEqual(
            accepted["Location"],
            self.forum_url("forum_question_detail", self.ske_question.pk),
        )
        notification = Notification.objects.get(
            recipient=self.student,
            event_key=f"forum-reply:{self.ske_question.replies.get().pk}",
        )
        self.assertEqual(notification.kind, "forum_reply")
        self.assertEqual(notification.title, "New comment on your FAQ question")
        self.assertEqual(
            notification.url,
            self.forum_url("forum_question_detail", self.ske_question.pk),
        )
        reply = self.ske_question.replies.get()
        self.assertEqual(reply.author_label, "Forum Lecturer")
        self.assertEqual(reply.author_role, "Lecturer")
        thread = self.client.get(accepted["Location"])
        self.assertContains(
            thread,
            "forum-detail-comments",
        )
        self.assertContains(thread, "Official guidance.")
        self.assertContains(thread, "Lecturer</span> Forum Lecturer")
        self.assertNotContains(thread, "Question thread")

    def test_comments_appear_in_main_feed_only_for_questions_reader_can_see(self):
        ForumReply.objects.create(
            question=self.everyone_question,
            body="Public question comment.",
            author=self.lecturer,
            author_label=self.lecturer.get_full_name(),
            author_role="Lecturer",
        )
        ForumReply.objects.create(
            question=self.ske_question,
            body="SKE question comment.",
            author=self.lecturer,
            author_label=self.lecturer.get_full_name(),
            author_role="Lecturer",
        )
        ForumReply.objects.create(
            question=self.cpe_question,
            body="CPE question comment.",
            author=self.other_lecturer,
            author_label=self.other_lecturer.get_full_name(),
            author_role="Lecturer",
        )

        self.sign_in(self.student)
        student_feed = self.client.get(self.forum_url("faq_board"))
        self.assertNotContains(student_feed, "Public question comment.")
        self.assertNotContains(student_feed, "SKE question comment.")
        self.assertNotContains(student_feed, "CPE question comment.")
        student_public_detail = self.client.get(
            self.forum_url("forum_question_detail", self.everyone_question.pk)
        )
        self.assertContains(student_public_detail, "Public question comment.")
        student_ske_detail = self.client.get(
            self.forum_url("forum_question_detail", self.ske_question.pk)
        )
        self.assertContains(student_ske_detail, "SKE question comment.")
        self.assertNotContains(student_ske_detail, "CPE question comment.")

        self.client.logout()
        self.client.post(reverse("accounts:guest_login"))
        guest_feed = self.client.get(self.forum_url("faq_board"))
        self.assertNotContains(guest_feed, "Public question comment.")
        self.assertNotContains(guest_feed, "SKE question comment.")
        self.assertNotContains(guest_feed, "CPE question comment.")
        guest_detail = self.client.get(
            self.forum_url("forum_question_detail", self.everyone_question.pk)
        )
        self.assertContains(guest_detail, "Public question comment.")
        self.assertNotContains(guest_detail, "SKE question comment.")

        self.sign_in(self.lecturer)
        lecturer_feed = self.client.get(self.forum_url("faq_board"))
        self.assertNotContains(lecturer_feed, "Public question comment.")
        lecturer_detail = self.client.get(
            self.forum_url("forum_question_detail", self.everyone_question.pk)
        )
        self.assertContains(lecturer_detail, "Public question comment.")
        self.assertContains(
            self.client.get(
                self.forum_url("forum_question_detail", self.ske_question.pk)
            ),
            "SKE question comment.",
        )
        self.assertContains(
            self.client.get(
                self.forum_url("forum_question_detail", self.cpe_question.pk)
            ),
            "CPE question comment.",
        )

    def test_lecturer_can_only_edit_and_archive_own_replies(self):
        own_reply = ForumReply.objects.create(
            question=self.ske_question,
            body="My response.",
            author=self.lecturer,
            author_label=self.lecturer.get_full_name(),
        )
        other_reply = ForumReply.objects.create(
            question=self.ske_question,
            body="Another response.",
            author=self.other_lecturer,
            author_label=self.other_lecturer.get_full_name(),
        )
        self.sign_in(self.lecturer)
        denied = self.client.post(
            self.forum_url("forum_reply_edit", other_reply.pk),
            {"body": "Forged edit."},
        )
        self.assertEqual(denied.status_code, 403)
        accepted = self.client.post(
            self.forum_url("forum_reply_edit", own_reply.pk),
            {"body": "Updated guidance."},
        )
        self.assertEqual(accepted.status_code, 302)
        own_reply.refresh_from_db()
        self.assertEqual(own_reply.body, "Updated guidance.")
        self.assertTrue(own_reply.has_been_edited)

        archived = self.client.post(
            self.forum_url("forum_reply_archive", own_reply.pk)
        )
        self.assertEqual(archived.status_code, 302)
        own_reply.refresh_from_db()
        self.assertIsNotNone(own_reply.archived_at)
        self.assertIsNone(other_reply.archived_at)

    def test_new_reply_only_displays_posted_timestamp(self):
        reply = ForumReply.objects.create(
            question=self.everyone_question,
            body="A new, unedited reply.",
            author=self.lecturer,
            author_label=self.lecturer.get_full_name(),
            author_role="Lecturer",
        )
        self.sign_in(self.lecturer)
        response = self.client.get(
            self.forum_url("faq_board"),
            {"q": self.everyone_question.pk},
        )
        self.assertContains(response, "Posted")
        self.assertNotContains(response, "Updated")
        self.assertFalse(reply.has_been_edited)
