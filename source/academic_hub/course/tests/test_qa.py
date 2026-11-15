import json
from datetime import datetime, timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from ..models import (
    CourseAnswer,
    CourseQuestion,
    CourseTA,
    Enrollment,
)
from ..services import create_course

from .helpers import CourseClientMixin


class CourseQATests(CourseClientMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.teacher = User.objects.create_user(
            nisit_id="6710540001", email="qa-teacher@ku.th", password="pass",
            first_name="Lecturer", last_name="One", department="ske", is_staff=True,
        )
        cls.other_teacher = User.objects.create_user(
            nisit_id="6710540002", email="qa-other-teacher@ku.th", password="pass",
            first_name="Lecturer", last_name="Two", department="ske", is_staff=True,
        )
        cls.student = User.objects.create_user(
            nisit_id="6710540003", email="qa-student@ku.th", password="pass",
            first_name="Student", last_name="One", department="ske",
        )
        cls.other_student = User.objects.create_user(
            nisit_id="6710540004", email="qa-other-student@ku.th", password="pass",
            first_name="Student", last_name="Two", department="ske",
        )
        cls.ta = User.objects.create_user(
            nisit_id="6710540005", email="qa-ta@ku.th", password="pass",
            first_name="Assistant", last_name="One", department="ske",
        )
        cls.department = User.objects.create_user(
            nisit_id="A0001", email="qa-department@ku.th", password="pass",
            first_name="Department", last_name="Admin", department="ske",
            is_superuser=True,
        )
        cls.course = create_course(owner=cls.teacher, name="Q&A Course")
        cls.other_course = create_course(owner=cls.other_teacher, name="Other Q&A Course")
        Enrollment.objects.create(course=cls.course, student=cls.student)
        Enrollment.objects.create(course=cls.course, student=cls.ta)
        Enrollment.objects.create(course=cls.other_course, student=cls.other_student)
        CourseTA.objects.create(course=cls.course, user=cls.ta)

    def make_question(self, *, course=None, author=None, title="Question", body="Question body"):
        course = course or self.course
        author = author or self.student
        if author.is_superuser:
            role = "Department"
        elif CourseTA.objects.filter(course=course, user=author).exists():
            role = "Teaching Assistant"
        elif author.is_staff:
            role = "Lecturer"
        else:
            role = "Student"
        return CourseQuestion.objects.create(
            course=course,
            author=author,
            author_label=author.get_full_name(),
            author_role_label=role,
            title=title,
            body=body,
        )

    def make_answer(self, question, *, author=None, body="Answer body"):
        author = author or self.teacher
        if author.is_superuser:
            role = "Department"
        elif CourseTA.objects.filter(course=question.course, user=author).exists():
            role = "Teaching Assistant"
        else:
            role = "Lecturer"
        return CourseAnswer.objects.create(
            question=question,
            author=author,
            author_label=author.get_full_name(),
            author_role_label=role,
            body=body,
        )

    def test_anonymous_and_guest_are_blocked_without_qa_data(self):
        question = self.make_question(title="Private Q&A text")
        list_url = self.url("api_qa_questions", self.course.pk)
        self.assertEqual(self.client.get(list_url).status_code, 401)

        self.sign_in(self.student)
        session = self.client.session
        session["is_guest"] = True
        session.save()
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("announcements:public_board"))
        self.assertNotIn(question.title, response.content.decode())

    def test_student_can_post_read_and_see_author_snapshots_and_timestamps(self):
        self.sign_in(self.student)
        response = self.client.post(
            self.url("api_qa_questions", self.course.pk),
            {"title": "  Where is the rubric?  ", "body": "  Please share it.  "},
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["title"], "Where is the rubric?")
        self.assertEqual(data["body"], "Please share it.")
        self.assertEqual(data["author_name"], "Student One")
        self.assertEqual(data["author_role_label"], "Student")
        self.assertTrue(timezone.is_aware(datetime.fromisoformat(data["created_at"])))
        self.assertTrue(data["is_mine"])
        self.assertTrue(data["can_edit"])
        self.assertFalse(data["can_answer"])
        self.assertIn("updated_at", data)
        self.assertIsNone(data["edited_at"])

        question_id = data["id"]
        listing = self.client.get(self.url("api_qa_questions", self.course.pk)).json()
        self.assertEqual(listing["count"], 1)
        self.assertEqual(listing["results"][0]["body_excerpt"], "Please share it.")
        detail = self.client.get(
            self.url("api_qa_question_detail", self.course.pk, question_id),
        ).json()
        self.assertEqual(detail["question"]["body"], "Please share it.")
        self.assertEqual(detail["answers"], [])

    def test_course_detail_exposes_role_aware_sidebar_forum_ui(self):
        self.sign_in(self.student)
        response = self.client.get(self.url("detail", self.course.pk))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('data-api-base="/course/api/', html)
        self.assertIn('data-qa-question-form', html)
        faq_header = html.split('class="course-tool__header"', 2)[2].split("</header>", 1)[0]
        self.assertIn('data-qa-open-ask', faq_header)
        self.assertIn('data-qa-search', html)
        self.assertIn('data-qa-thread', html)
        self.assertIn('course-qa.js', html)
        self.assertIn('course-qa.css', html)
        self.assertNotIn('id="tab-qa"', html)
        self.assertNotIn('Archived (Department)', html)
        self.assertNotIn('id="course-forum-status"', html)

        self.sign_in(self.ta)
        ta_page = self.client.get(self.url("detail", self.course.pk))
        self.assertEqual(ta_page.status_code, 200)
        ta_html = ta_page.content.decode()
        self.assertIn('id="course-forum-status"', ta_html)
        self.assertIn('value="answered"', ta_html)
        self.assertIn('value="unanswered"', ta_html)
        self.assertIn('value="archived"', ta_html)
        self.assertNotIn('value="mine"', ta_html)
        ta_faq_header = ta_html.split('class="course-tool__header"', 2)[2].split("</header>", 1)[0]
        self.assertNotIn('data-qa-open-ask', ta_faq_header)
        self.assertNotIn('data-qa-question-form', ta_html)
        self.assertIn('not course TAs can post a question', ta_html)

        self.sign_in(self.teacher)
        lecturer_page = self.client.get(self.url("detail", self.course.pk))
        self.assertEqual(lecturer_page.status_code, 200)
        lecturer_html = lecturer_page.content.decode()
        self.assertIn('id="course-forum-status"', lecturer_html)
        self.assertIn('value="answered"', lecturer_html)
        self.assertIn('value="unanswered"', lecturer_html)
        self.assertIn('value="archived"', lecturer_html)
        self.assertNotIn('value="mine"', lecturer_html)

        self.sign_in(self.department)
        department_page = self.client.get(self.url("detail", self.course.pk))
        self.assertEqual(department_page.status_code, 200)
        self.assertIn('value="archived"', department_page.content.decode())

    def test_ta_cannot_ask_question_from_ui_or_api(self):
        self.sign_in(self.ta)
        page = self.client.get(self.url("detail", self.course.pk))
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        faq_header = html.split('class="course-tool__header"', 2)[2].split("</header>", 1)[0]
        self.assertNotIn('data-qa-open-ask', faq_header)
        self.assertNotIn('data-qa-question-form', html)

        response = self.client.post(
            self.url("api_qa_questions", self.course.pk),
            {"title": "TA question", "body": "TA should not ask"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("not course TAs", response.json()["error"])
        self.assertFalse(CourseQuestion.objects.filter(title="TA question").exists())

    def test_course_staff_can_list_and_read_archived_questions(self):
        question = self.make_question(title="Archived course question")
        answer = self.make_answer(question, body="Archived course answer")
        archived_at = timezone.now()
        CourseQuestion.objects.filter(pk=question.pk).update(
            archived_at=archived_at,
            archived_by=self.ta,
        )
        CourseAnswer.objects.filter(pk=answer.pk).update(
            archived_at=archived_at,
            archived_by=self.ta,
        )
        list_url = self.url("api_qa_questions", self.course.pk)
        detail_url = self.url("api_qa_question_detail", self.course.pk, question.pk)

        self.sign_in(self.student)
        self.assertEqual(self.client.get(list_url, {"status": "archived"}).status_code, 400)
        self.assertEqual(self.client.get(detail_url).status_code, 404)

        for user in (self.teacher, self.ta):
            with self.subTest(user=user.pk):
                self.sign_in(user)
                listing = self.client.get(list_url, {"status": "archived"})
                self.assertEqual(listing.status_code, 200)
                self.assertEqual(listing.json()["count"], 1)
                listed_question = listing.json()["results"][0]
                self.assertEqual(listed_question["id"], question.pk)
                self.assertIsNotNone(listed_question["archived_at"])
                detail = self.client.get(detail_url)
                self.assertEqual(detail.status_code, 200)
                self.assertIsNotNone(detail.json()["question"]["archived_at"])
                self.assertEqual(len(detail.json()["answers"]), 1)
                archived_answer = detail.json()["answers"][0]
                self.assertEqual(archived_answer["body"], "Archived course answer")
                self.assertIsNotNone(archived_answer["archived_at"])
                can_restore = user == self.ta
                self.assertEqual(detail.json()["question"]["can_restore"], can_restore)
                self.assertEqual(archived_answer["can_restore"], can_restore)

        self.sign_in(self.ta)
        archived_by_ta = self.client.get(detail_url).json()
        self.assertTrue(archived_by_ta["question"]["can_restore"])
        self.assertTrue(archived_by_ta["answers"][0]["can_restore"])

    def test_course_scope_and_students_cannot_answer(self):
        question = self.make_question()
        foreign_question = self.make_question(
            course=self.other_course, author=self.other_student,
        )
        self.sign_in(self.other_student)
        inaccessible = self.client.get(self.url("api_qa_questions", self.course.pk))
        self.assertEqual(inaccessible.status_code, 404)
        self.assertEqual(inaccessible["Content-Type"], "application/json")
        self.assertEqual(
            self.client.get(
                self.url("api_qa_question_detail", self.course.pk, question.pk),
            ).status_code,
            404,
        )
        self.sign_in(self.other_teacher)
        self.assertEqual(
            self.client.get(self.url("api_qa_questions", self.course.pk)).status_code,
            404,
        )
        self.sign_in(self.teacher)
        cross_course = self.client.get(
            self.url("api_qa_question_detail", self.course.pk, foreign_question.pk),
        )
        self.assertEqual(cross_course.status_code, 404)
        self.assertEqual(cross_course["Content-Type"], "application/json")
        self.assertEqual(
            self.client.get(
                self.url("api_qa_question_detail", self.course.pk, 999999),
            )["Content-Type"],
            "application/json",
        )
        self.sign_in(self.student)
        response = self.client.post(
            self.url("api_qa_answer_create", self.course.pk, question.pk),
            {"body": "Student answer"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(question.answers.exists())

    def test_owner_can_answer_pin_and_verify_multiple_answers(self):
        question = self.make_question()
        self.sign_in(self.teacher)
        first = self.client.post(
            self.url("api_qa_answer_create", self.course.pk, question.pk),
            {"body": "Official answer one"},
        )
        second = self.client.post(
            self.url("api_qa_answer_create", self.course.pk, question.pk),
            {"body": "Official answer two"},
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        answer_id = first.json()["answer"]["id"]
        edited = self.client.post(
            self.url("api_qa_answer_edit", self.course.pk, question.pk, answer_id),
            {"body": "Edited official answer one"},
        )
        self.assertEqual(edited.status_code, 200)
        self.assertIsNotNone(edited.json()["answer"]["edited_at"])
        edited_at = CourseAnswer.objects.get(pk=answer_id).edited_at
        edited_at_response = edited.json()["answer"]["edited_at"]
        pin = self.client.post(
            self.url("api_qa_question_pin", self.course.pk, question.pk),
            {"is_pinned": "true"},
        )
        self.assertEqual(pin.status_code, 200)
        self.assertTrue(pin.json()["question"]["is_pinned"])

        for created in [first, second]:
            response = self.client.post(
                self.url(
                    "api_qa_answer_verify", self.course.pk, question.pk,
                    created.json()["answer"]["id"],
                ),
                {"is_verified": "true"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["answer"]["is_verified"])
            self.assertTrue(response.json()["answer"]["verified_at"])

        detail = self.client.get(
            self.url("api_qa_question_detail", self.course.pk, question.pk),
        ).json()
        self.assertTrue(detail["question"]["has_verified_answer"])
        self.assertEqual(detail["question"]["answer_count"], 2)
        self.assertEqual(len([a for a in detail["answers"] if a["is_verified"]]), 2)
        self.assertEqual(detail["answers"][0]["author_name"], "Lecturer One")
        self.assertEqual(detail["answers"][0]["author_role_label"], "Lecturer")
        self.assertIn("created_at", detail["answers"][0])
        self.assertEqual(detail["answers"][0]["edited_at"], edited_at_response)
        self.assertTrue(detail["answers"][0]["can_verify"])
        unverify = self.client.post(
            self.url("api_qa_answer_verify", self.course.pk, question.pk, answer_id),
            {"is_verified": "false"},
        )
        self.assertFalse(unverify.json()["answer"]["is_verified"])
        self.assertIsNone(unverify.json()["answer"]["verified_at"])
        detail = self.client.get(
            self.url("api_qa_question_detail", self.course.pk, question.pk),
        ).json()
        self.assertEqual(
            [answer["id"] for answer in detail["answers"]],
            [second.json()["answer"]["id"], answer_id],
        )
        question.refresh_from_db()
        self.assertIsNone(question.edited_at)
        self.assertEqual(CourseAnswer.objects.get(pk=answer_id).edited_at, edited_at)

    def test_ta_can_answer_pin_verify_archive_and_restore_own_archives(self):
        question = self.make_question()
        self.sign_in(self.ta)
        answer_response = self.client.post(
            self.url("api_qa_answer_create", self.course.pk, question.pk),
            {"body": "TA answer"},
        )
        self.assertEqual(answer_response.status_code, 201)
        answer = answer_response.json()["answer"]
        self.assertEqual(answer["author_role_label"], "Teaching Assistant")
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_pin", self.course.pk, question.pk),
                {"is_pinned": "true"},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                self.url("api_qa_answer_verify", self.course.pk, question.pk, answer["id"]),
                {"is_verified": "true"},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_archive", self.course.pk, question.pk),
            ).status_code,
            200,
        )
        self.sign_in(self.teacher)
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_restore", self.course.pk, question.pk),
            ).status_code,
            403,
        )
        self.sign_in(self.ta)
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_restore", self.course.pk, question.pk),
            ).status_code,
            200,
        )
        question.refresh_from_db()
        self.assertIsNone(question.edited_at)

        self.sign_in(self.teacher)
        self.client.post(
            self.url("api_qa_answer_archive", self.course.pk, question.pk, answer["id"]),
        )
        self.sign_in(self.ta)
        self.assertEqual(
            self.client.post(
                self.url("api_qa_answer_restore", self.course.pk, question.pk, answer["id"]),
            ).status_code,
            403,
        )
        self.assertIsNone(CourseAnswer.objects.get(pk=answer["id"]).edited_at)

    def test_department_can_moderate_across_courses(self):
        question = self.make_question(
            course=self.other_course, author=self.other_student, title="Department scope",
        )
        self.sign_in(self.department)
        self.assertEqual(
            self.client.get(
                self.url("api_qa_question_detail", self.other_course.pk, question.pk),
            ).status_code,
            200,
        )
        answer = self.client.post(
            self.url("api_qa_answer_create", self.other_course.pk, question.pk),
            {"body": "Department response"},
        )
        self.assertEqual(answer.status_code, 201)
        answer_id = answer.json()["answer"]["id"]
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_pin", self.other_course.pk, question.pk),
                {"is_pinned": "true"},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                self.url("api_qa_answer_verify", self.other_course.pk, question.pk, answer_id),
                {"is_verified": "true"},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                self.url("api_qa_answer_edit", self.other_course.pk, question.pk, answer_id),
                {"body": "Department edited response"},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_edit", self.other_course.pk, question.pk),
                {"title": "Department edited question", "body": "Updated"},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_archive", self.other_course.pk, question.pk),
            ).status_code,
            200,
        )
        archived = self.client.get(
            self.url("api_qa_questions", self.other_course.pk),
            {"status": "archived"},
        )
        self.assertEqual(archived.json()["count"], 1)
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_restore", self.other_course.pk, question.pk),
            ).status_code,
            200,
        )

    def test_author_edit_archive_and_department_restores_student_question(self):
        question = self.make_question()
        self.sign_in(self.student)
        edited = self.client.post(
            self.url("api_qa_question_edit", self.course.pk, question.pk),
            {"title": "Edited title", "body": "Edited body"},
        )
        self.assertEqual(edited.status_code, 200)
        question.refresh_from_db()
        self.assertIsNotNone(question.edited_at)
        self.assertEqual(question.title, "Edited title")

        self.client.post(self.url("api_qa_question_archive", self.course.pk, question.pk))
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_restore", self.course.pk, question.pk),
            ).status_code,
            403,
        )
        self.sign_in(self.department)
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_restore", self.course.pk, question.pk),
            ).status_code,
            200,
        )

    def test_student_cannot_edit_question_after_active_answer_but_can_archive(self):
        question = self.make_question()
        self.make_answer(question)
        self.sign_in(self.student)
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_edit", self.course.pk, question.pk),
                {"title": "Too late", "body": "Edited"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_archive", self.course.pk, question.pk),
            ).status_code,
            200,
        )

    def test_course_owner_can_archive_any_and_restore_only_what_they_archived(self):
        question = self.make_question()
        answer = self.make_answer(question)
        self.sign_in(self.teacher)
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_edit", self.course.pk, question.pk),
                {"title": "Owner cannot edit", "body": "No"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                self.url("api_qa_answer_archive", self.course.pk, question.pk, answer.pk),
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                self.url("api_qa_answer_restore", self.course.pk, question.pk, answer.pk),
            ).status_code,
            200,
        )

    def test_archived_items_require_restore_before_moderation(self):
        question = self.make_question()
        answer = self.make_answer(question)
        self.sign_in(self.teacher)
        self.client.post(self.url("api_qa_question_archive", self.course.pk, question.pk))

        self.sign_in(self.student)
        self.assertEqual(
            self.client.get(
                self.url("api_qa_question_detail", self.course.pk, question.pk),
            ).status_code,
            404,
        )
        visible = self.client.get(
            self.url("api_qa_questions", self.course.pk),
        ).json()
        self.assertNotIn(question.pk, [item["id"] for item in visible["results"]])
        self.assertEqual(
            self.client.get(
                self.url("api_qa_questions", self.course.pk),
                {"status": "archived"},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                self.url("api_qa_answer_create", self.course.pk, question.pk),
                {"body": "No answer on archived thread"},
            ).status_code,
            404,
        )

        self.sign_in(self.department)
        detail = self.client.get(
            self.url("api_qa_question_detail", self.course.pk, question.pk),
        )
        self.assertEqual(detail.status_code, 200)
        self.assertFalse(detail.json()["question"]["can_edit"])
        self.assertFalse(detail.json()["question"]["can_answer"])
        for action, args, body in [
            ("api_qa_question_edit", (question.pk,), {"title": "x", "body": "y"}),
            ("api_qa_question_pin", (question.pk,), {"is_pinned": "true"}),
            ("api_qa_answer_create", (question.pk,), {"body": "No"}),
            ("api_qa_answer_edit", (question.pk, answer.pk), {"body": "No"}),
            ("api_qa_answer_verify", (question.pk, answer.pk), {"is_verified": "true"}),
        ]:
            self.assertEqual(
                self.client.post(self.url(action, self.course.pk, *args), body).status_code,
                404,
            )
        self.assertEqual(
            self.client.post(
                self.url("api_qa_question_restore", self.course.pk, question.pk),
            ).status_code,
            200,
        )

        self.sign_in(self.teacher)
        self.client.post(
            self.url("api_qa_answer_archive", self.course.pk, question.pk, answer.pk),
        )
        self.sign_in(self.department)
        self.assertEqual(
            self.client.post(
                self.url("api_qa_answer_verify", self.course.pk, question.pk, answer.pk),
                {"is_verified": "true"},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                self.url("api_qa_answer_restore", self.course.pk, question.pk, answer.pk),
            ).status_code,
            200,
        )

    def test_validation_too_long_json_toggles_mutation_methods_and_csrf(self):
        self.sign_in(self.student)
        for title, body in [
            ("   ", "Body"),
            ("x" * 201, "Body"),
            ("Title", "   "),
            ("Title", "x" * 5001),
        ]:
            self.assertEqual(
                self.client.post(
                    self.url("api_qa_questions", self.course.pk),
                    {"title": title, "body": body},
                ).status_code,
                400,
            )

        question = self.make_question()
        self.sign_in(self.teacher)
        answer_url = self.url("api_qa_answer_create", self.course.pk, question.pk)
        self.assertEqual(
            self.client.post(answer_url, {"body": "x" * 5001}).status_code,
            400,
        )
        pin_url = self.url("api_qa_question_pin", self.course.pk, question.pk)
        self.assertEqual(
            self.client.post(
                pin_url, json.dumps({"is_pinned": "true"}),
                content_type="application/json",
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                pin_url, json.dumps({"is_pinned": True}),
                content_type="application/json",
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                pin_url,
                '{"title":',
                content_type="application/json",
            ).status_code,
            400,
        )

        answer = self.make_answer(question)
        mutation_urls = [
            self.url("api_qa_question_edit", self.course.pk, question.pk),
            self.url("api_qa_question_archive", self.course.pk, question.pk),
            self.url("api_qa_question_restore", self.course.pk, question.pk),
            pin_url,
            answer_url,
            self.url("api_qa_answer_edit", self.course.pk, question.pk, answer.pk),
            self.url("api_qa_answer_archive", self.course.pk, question.pk, answer.pk),
            self.url("api_qa_answer_restore", self.course.pk, question.pk, answer.pk),
            self.url("api_qa_answer_verify", self.course.pk, question.pk, answer.pk),
        ]
        for url in mutation_urls:
            self.assertEqual(self.client.get(url).status_code, 405)

        client = Client(enforce_csrf_checks=True)
        client.force_login(self.teacher)
        self.assertEqual(client.post(pin_url, {"is_pinned": "true"}).status_code, 403)

    def test_filters_pagination_search_pinned_and_verified_order(self):
        oldest = self.make_question(title="Old question", body="moon search term")
        answered = self.make_question(title="Answered", body="Has answer")
        newest = self.make_question(title="Newest", body="Other")
        pinned = self.make_question(title="Pinned", body="Other")
        latest_unverified = self.make_question(title="Latest unverified", body="Other")
        CourseQuestion.objects.filter(pk=oldest.pk).update(
            created_at=timezone.now() - timedelta(days=2),
        )
        self.make_answer(answered, body="Answer")
        verified = self.make_answer(newest, body="Verified")
        verified.is_verified = True
        verified.verified_by = self.teacher
        verified.verified_at = timezone.now()
        verified.save(update_fields=["is_verified", "verified_by", "verified_at"])
        pinned.is_pinned = True
        pinned.pinned_at = timezone.now()
        pinned.save(update_fields=["is_pinned", "pinned_at"])

        self.sign_in(self.student)
        listing_url = self.url("api_qa_questions", self.course.pk)
        page = self.client.get(listing_url, {"page": "1", "page_size": "2"}).json()
        self.assertEqual([q["id"] for q in page["results"]], [pinned.pk, newest.pk])
        page_two = self.client.get(listing_url, {"page": "2", "page_size": "2"}).json()
        self.assertEqual(
            [q["id"] for q in page_two["results"]],
            [latest_unverified.pk, answered.pk],
        )
        self.assertEqual(
            self.client.get(listing_url, {"page": "99"}).json()["results"],
            [],
        )
        self.assertTrue(page["results"][1]["has_verified_answer"])
        self.assertEqual(
            self.client.get(listing_url, {"status": "answered"}).json()["count"],
            2,
        )
        self.assertEqual(
            self.client.get(listing_url, {"status": "unanswered"}).json()["count"],
            3,
        )
        self.assertEqual(
            self.client.get(listing_url, {"status": "mine"}).json()["count"],
            5,
        )
        self.assertEqual(
            self.client.get(listing_url, {"q": "MOON"}).json()["results"][0]["id"],
            oldest.pk,
        )
        for params in [{"page": "0"}, {"page_size": "101"}, {"status": "unknown"}]:
            self.assertEqual(self.client.get(listing_url, params).status_code, 400)

        self.sign_in(self.department)
        oldest.archived_at = timezone.now()
        oldest.archived_by = self.department
        oldest.save(update_fields=["archived_at", "archived_by"])
        archived = self.client.get(listing_url, {"status": "archived"}).json()
        self.assertEqual(archived["count"], 1)
        self.assertEqual(archived["results"][0]["id"], oldest.pk)

    def test_question_from_removed_student_remains_visible(self):
        question = self.make_question(title="Question by removed student")
        self.sign_in(self.teacher)
        self.assertEqual(
            self.client.post(
                self.url("api_remove_member", self.course.pk, self.student.pk),
            ).status_code,
            200,
        )
        listing = self.client.get(self.url("api_qa_questions", self.course.pk)).json()
        self.assertEqual(listing["results"][0]["id"], question.pk)
        self.assertEqual(listing["results"][0]["author_name"], "Student One")

    def test_list_query_count_does_not_grow_with_question_count(self):
        self.make_question(title="One")
        self.sign_in(self.student)
        url = self.url("api_qa_questions", self.course.pk)
        with CaptureQueriesContext(connection) as one_question:
            self.client.get(url)
        for index in range(2, 7):
            self.make_question(title=f"Question {index}")
        with CaptureQueriesContext(connection) as several_questions:
            response = self.client.get(url)
        self.assertEqual(response.json()["count"], 6)
        self.assertEqual(len(one_question), len(several_questions))

    def test_notification_hook_uses_on_commit_and_not_for_invalid_write(self):
        self.sign_in(self.student)
        with patch("course.services.qa_notification_hook") as hook:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                response = self.client.post(
                    self.url("api_qa_questions", self.course.pk),
                    {"title": "Committed question", "body": "Body"},
                )
            self.assertEqual(response.status_code, 201)
            self.assertEqual(len(callbacks), 1)
            hook.assert_called_once()

        with patch("course.services.qa_notification_hook") as hook:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                response = self.client.post(
                    self.url("api_qa_questions", self.course.pk),
                    {"title": "   ", "body": "Invalid"},
                )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(callbacks, [])
            hook.assert_not_called()

        question = self.make_question()
        self.sign_in(self.teacher)
        with patch("course.services.qa_notification_hook") as hook:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                response = self.client.post(
                    self.url("api_qa_answer_create", self.course.pk, question.pk),
                    {"body": "Committed answer"},
                )
            self.assertEqual(response.status_code, 201)
            self.assertEqual(len(callbacks), 1)
            hook.assert_called_once()
