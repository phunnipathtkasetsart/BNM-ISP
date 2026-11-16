from datetime import timedelta
from django.urls import reverse
from django.utils import timezone
from django.utils.dateformat import format as date_format
from ..models import Faq, ForumCategory, ForumQuestion, ForumReply
from .forum import ForumTestCase


class ForumBoardTests(ForumTestCase):
    def test_student_sees_everyone_and_own_department_only(self):
        titles = self.question_titles(self.student)
        self.assertIn("Everyone question", titles)
        self.assertIn("SKE question", titles)
        self.assertNotIn("CPE question", titles)
        self.assertNotIn("Unpublished question", titles)

    def test_lecturer_sees_all_active_published_departments(self):
        titles = self.question_titles(self.lecturer)
        self.assertIn("Everyone question", titles)
        self.assertIn("SKE question", titles)
        self.assertIn("CPE question", titles)
        self.assertNotIn("Unpublished question", titles)
        self.assertNotIn("Archived question", titles)

    def test_department_sees_archived_and_unpublished_questions(self):
        titles = self.question_titles(self.department)
        self.assertIn("Unpublished question", titles)
        self.assertIn("Archived question", titles)

    def test_guest_opens_public_thread_but_not_department_thread(self):
        from accounts.middleware import GUEST_ALLOWED_VIEWS

        self.client.post(reverse("accounts:guest_login"))
        public_response = self.client.get(
            self.forum_url("faq_board"),
            {"q": self.everyone_question.pk},
        )
        self.assertEqual(public_response.status_code, 200)
        self.assertContains(public_response, "Everyone question")
        self.assertContains(public_response, "Visible to every reader.")
        self.assertNotContains(public_response, "CPE department content.")
        detail_response = self.client.get(
            self.forum_url("forum_question_detail", self.everyone_question.pk)
        )
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "Everyone question")

        department_response = self.client.get(
            self.forum_url("faq_board"),
            {"q": self.cpe_question.pk},
        )
        self.assertEqual(department_response.status_code, 404)
        private_detail = self.client.get(
            self.forum_url("forum_question_detail", self.cpe_question.pk)
        )
        self.assertEqual(private_detail.status_code, 404)
        self.assertIn("announcements:faq_board", GUEST_ALLOWED_VIEWS)
        self.assertIn("announcements:forum_question_detail", GUEST_ALLOWED_VIEWS)
        self.assertNotIn("announcements:forum_question_create", GUEST_ALLOWED_VIEWS)

    def test_question_list_links_to_separate_detail_page(self):
        self.sign_in(self.student)
        response = self.client.get(self.forum_url("faq_board"))
        self.assertContains(
            response,
            f'href="{self.forum_url("forum_question_detail", self.ske_question.pk)}"',
        )
        self.assertNotContains(response, "SKE question comment.")

        detail = self.client.get(
            self.forum_url("forum_question_detail", self.ske_question.pk)
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "SKE department content.")
        self.assertContains(detail, "Comments")
        self.assertNotContains(detail, "Course</a>")
        self.assertNotContains(detail, "Question thread")

    def test_question_edit_attribution_and_department_label_render(self):
        self.sign_in(self.department)
        response = self.client.post(
            self.forum_url("forum_question_edit", self.everyone_question.pk),
            {
                "title": self.everyone_question.title,
                "body": "Edited by the Department.",
                "visibility": ForumQuestion.Visibility.EVERYONE,
                "is_published": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.everyone_question.refresh_from_db()
        self.assertEqual(self.everyone_question.updated_by, self.department)
        self.assertEqual(
            self.everyone_question.updated_by_label,
            self.department.get_full_name(),
        )
        self.assertFalse(self.everyone_question.updated_by_is_author)

        response = self.client.get(
            self.forum_url("forum_question_detail", self.everyone_question.pk),
        )
        self.assertContains(response, "Updated")
        self.assertContains(response, "(Forum Department)")
        self.assertContains(response, "Comments")
        self.assertContains(
            response,
            '<span class="forum-audience-tag">Everyone</span>',
        )

    def test_new_question_hides_updated_timestamp_and_shows_department_tag(self):
        self.sign_in(self.student)
        response = self.client.get(
            self.forum_url("forum_question_detail", self.ske_question.pk),
        )
        self.assertContains(response, "Forum Student")
        self.assertContains(response, "SKE")
        self.assertContains(
            response,
            '<span class="forum-audience-tag">SKE</span>',
        )
        self.assertNotContains(response, "Updated")
        self.assertContains(response, "Comments")
        self.assertNotContains(response, "Department only")

    def test_department_published_checkbox_is_inline_with_label(self):
        self.sign_in(self.department)
        response = self.client.get(
            self.forum_url("forum_question_edit", self.everyone_question.pk)
        )
        self.assertContains(response, "forum-form__field--checkbox")
        self.assertContains(
            response,
            '<label for="id_is_published">Published:</label><input',
        )
        self.assertContains(response, "When checked, this question is visible")

    def test_author_names_and_bangkok_timestamps_render(self):
        reply = ForumReply.objects.create(
            question=self.everyone_question,
            body="Readable reply.",
            author=self.lecturer,
            author_label=self.lecturer.get_full_name(),
        )
        ForumQuestion.objects.filter(pk=self.everyone_question.pk).update(
            updated_at=self.everyone_question.created_at + timedelta(hours=1),
            updated_by=self.department,
            updated_by_label=self.department.get_full_name(),
            updated_by_is_author=False,
        )
        ForumReply.objects.filter(pk=reply.pk).update(
            updated_at=reply.created_at + timedelta(hours=1),
            has_been_edited=True,
        )
        self.sign_in(self.lecturer)
        with timezone.override("Asia/Bangkok"):
            response = self.client.get(
                self.forum_url("forum_question_detail", self.everyone_question.pk),
            )
            expected_question_time = date_format(
                timezone.localtime(self.everyone_question.created_at),
                "H:i",
            )
            expected_reply_time = date_format(
                timezone.localtime(reply.created_at),
                "H:i",
            )
        self.assertContains(response, "Forum Student")
        self.assertContains(response, "Forum Lecturer")
        self.assertContains(response, expected_question_time)
        self.assertContains(response, expected_reply_time)
        self.assertContains(response, "Updated")
        self.assertContains(response, "(Forum Department)")
        self.assertNotContains(response, "Student:")
        self.assertNotContains(response, "Lecturer:")

    def test_forum_timestamps_show_time_today_and_date_for_older_posts(self):
        reply = ForumReply.objects.create(
            question=self.everyone_question,
            body="Timestamp formatting reply.",
            author=self.lecturer,
            author_label=self.lecturer.get_full_name(),
            author_role="Lecturer",
        )
        old_timestamp = timezone.now() - timedelta(days=2)
        ForumQuestion.objects.filter(pk=self.everyone_question.pk).update(
            created_at=old_timestamp,
            updated_at=old_timestamp,
            updated_by=self.department,
            updated_by_label=self.department.get_full_name(),
            updated_by_is_author=False,
        )
        ForumReply.objects.filter(pk=reply.pk).update(
            created_at=old_timestamp,
            updated_at=old_timestamp,
            has_been_edited=True,
        )
        self.sign_in(self.lecturer)
        with timezone.override("Asia/Bangkok"):
            response = self.client.get(
                self.forum_url("forum_question_detail", self.everyone_question.pk),
            )
            old_date = date_format(timezone.localtime(old_timestamp), "j M Y")
            old_time = date_format(timezone.localtime(old_timestamp), "H:i")
        self.assertContains(response, old_date)
        self.assertNotContains(response, f"{old_date}, {old_time}")

        current_timestamp = timezone.now()
        ForumQuestion.objects.filter(pk=self.everyone_question.pk).update(
            created_at=current_timestamp,
            updated_at=current_timestamp,
        )
        ForumReply.objects.filter(pk=reply.pk).update(
            created_at=current_timestamp,
            updated_at=current_timestamp,
        )
        with timezone.override("Asia/Bangkok"):
            response = self.client.get(
                self.forum_url("forum_question_detail", self.everyone_question.pk),
            )
            current_time = date_format(
                timezone.localtime(current_timestamp),
                "H:i",
            )
        self.assertContains(response, current_time)
        self.assertNotContains(response, old_date)

    def test_question_list_is_paginated_and_preserves_selected_thread(self):
        for index in range(21):
            ForumQuestion.objects.create(
                title=f"Pagination question {index}",
                body="Page test.",
                visibility=ForumQuestion.Visibility.EVERYONE,
            )
        self.sign_in(self.lecturer)
        response = self.client.get(
            self.forum_url("faq_board"),
            {"q": self.everyone_question.pk, "page": 1},
        )
        self.assertEqual(len(response.context["threads"]), 20)
        self.assertContains(
            response,
            f"?page=2&amp;q={self.everyone_question.pk}",
        )
        second_page = self.client.get(
            self.forum_url("faq_board"),
            {"q": self.everyone_question.pk, "page": 2},
        )
        self.assertEqual(second_page.context["selected_question"], self.everyone_question)
        invalid_page = self.client.get(
            self.forum_url("faq_board"),
            {"page": "not-a-page"},
        )
        self.assertEqual(invalid_page.status_code, 200)
        self.assertEqual(invalid_page.context["page_obj"].number, 1)

    def test_long_homepage_faq_descriptions_have_expand_controls(self):
        long_answer = "Long FAQ answer. " * 80
        Faq.objects.create(
            question="Long FAQ question",
            answer=long_answer,
        )
        self.client.force_login(self.department)
        response = self.client.get(reverse("announcements:public_board"))
        self.assertContains(response, 'data-faq-description')
        self.assertContains(response, 'data-faq-description-toggle')
        self.assertContains(response, "Show more")

    def test_forum_filters_search_audience_category_dates_and_comment_sort(self):
        category = ForumCategory.objects.create(
            name="Registration",
            created_by=self.department,
        )
        self.everyone_question.category = category
        self.everyone_question.save(update_fields=["category"])
        self.ske_question.category = category
        self.ske_question.save(update_fields=["category"])
        lecturer_question = ForumQuestion.objects.create(
            title="Lecturer authored question",
            body="Official response.",
            visibility=ForumQuestion.Visibility.EVERYONE,
            author=self.lecturer,
            author_role="Lecturer",
        )
        department_question = ForumQuestion.objects.create(
            title="Department authored question",
            body="Department guidance.",
            visibility=ForumQuestion.Visibility.EVERYONE,
            author=self.department,
            author_role="Department",
        )

        old_date = timezone.now() - timedelta(days=400)
        ForumQuestion.objects.filter(pk=self.cpe_question.pk).update(
            created_at=old_date,
        )
        for index in range(2):
            ForumReply.objects.create(
                question=self.ske_question,
                body=f"SKE reply {index}",
                author=self.lecturer,
                author_label=self.lecturer.get_full_name(),
                author_role="Lecturer",
            )
        ForumReply.objects.create(
            question=self.cpe_question,
            body="CPE reply",
            author=self.other_lecturer,
            author_label=self.other_lecturer.get_full_name(),
            author_role="Lecturer",
        )

        self.sign_in(self.department)
        response = self.client.get(
            self.forum_url("faq_board"),
            {"search": "SKE", "audience": "ske"},
        )
        self.assertEqual(
            {question.title for question in response.context["threads"]},
            {"SKE question"},
        )

        response = self.client.get(
            self.forum_url("faq_board"),
            {"audience": "", "category": category.pk},
        )
        self.assertEqual(
            {question.title for question in response.context["threads"]},
            {"Everyone question", "SKE question"},
        )
        response = self.client.get(
            self.forum_url("faq_board"),
            {"author": "lecturer"},
        )
        self.assertEqual(
            {question.title for question in response.context["threads"]},
            {lecturer_question.title},
        )
        response = self.client.get(
            self.forum_url("faq_board"),
            {"author": "department"},
        )
        self.assertEqual(
            {question.title for question in response.context["threads"]},
            {department_question.title},
        )

        selected_date = timezone.localdate() - timedelta(days=200)
        response = self.client.get(
            self.forum_url("faq_board"),
            {
                "date_direction": "before",
                "date": selected_date.isoformat(),
            },
        )
        self.assertEqual(
            {question.title for question in response.context["threads"]},
            {"CPE question"},
        )
        response = self.client.get(
            self.forum_url("faq_board"),
            {
                "date_direction": "after",
                "date": selected_date.isoformat(),
            },
        )
        self.assertNotIn(
            "CPE question",
            {question.title for question in response.context["threads"]},
        )
        response = self.client.get(
            self.forum_url("faq_board"),
            {"date": selected_date.isoformat()},
        )
        self.assertTrue(response.context["filter_form"].is_valid())
        self.assertEqual(
            response.context["filter_form"].cleaned_data["date_direction"],
            "before",
        )

        response = self.client.get(
            self.forum_url("faq_board"),
            {"sort": "comments_desc"},
        )
        self.assertEqual(response.context["threads"][0], self.ske_question)
        response = self.client.get(
            self.forum_url("faq_board"),
            {"sort": "comments_asc"},
        )
        self.assertEqual(response.context["threads"][0].comment_count, 0)
        response = self.client.get(
            self.forum_url("faq_board"),
            {"sort": "oldest"},
        )
        self.assertEqual(response.context["threads"][0], self.cpe_question)

        self.assertNotIn("month", response.context["filter_form"].fields)
        self.assertNotIn("year", response.context["filter_form"].fields)

    def test_invalid_filters_preserve_reader_visibility(self):
        self.sign_in(self.student)
        response = self.client.get(
            self.forum_url("faq_board"),
            {"audience": "everyone", "search": "No matching question"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("audience", response.context["filter_form"].errors)
        self.assertEqual(
            {question.title for question in response.context["threads"]},
            {"Everyone question", "SKE question"},
        )

    def test_live_search_returns_filtered_question_results(self):
        self.sign_in(self.lecturer)
        board = self.client.get(self.forum_url("faq_board"))
        self.assertNotContains(board, "Apply filters")
        self.assertContains(board, "Clear filters")

        response = self.client.get(
            self.forum_url("faq_board"),
            {"search": "SKE"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SKE question")
        self.assertNotContains(response, "Everyone question")
        self.assertEqual(
            response.templates[0].name,
            "announcements/_forum_question_results.html",
        )

    def test_faq_board_ignores_stale_removed_date_parameters(self):
        self.sign_in(self.lecturer)
        default_response = self.client.get(self.forum_url("faq_board"))
        self.assertEqual(
            default_response.context["filter_form"]["date_direction"].value(),
            "before",
        )
        self.assertEqual(
            list(
                default_response.context["filter_form"]
                .fields["date_direction"]
                .choices
            ),
            [("before", "Before"), ("after", "After")],
        )

        response = self.client.get(
            self.forum_url("faq_board"),
            {
                "date_after": timezone.localdate().isoformat(),
                "date_before": timezone.localdate().isoformat(),
                "month": timezone.localdate().strftime("%Y-%m"),
                "year": timezone.localdate().year,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Everyone question", {
            question.title for question in response.context["threads"]
        })
