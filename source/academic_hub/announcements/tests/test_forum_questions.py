from django.test import Client
from django.urls import reverse
from ..models import Faq, ForumCategory, ForumQuestion
from .forum import ForumTestCase


class ForumQuestionTests(ForumTestCase):
    def test_guest_cannot_post_a_question(self):
        self.client.post(reverse("accounts:guest_login"))
        response = self.client.post(
            self.forum_url("forum_question_create"),
            {
                "title": "Guest attempt",
                "body": "This must not be saved.",
                "visibility": ForumQuestion.Visibility.EVERYONE,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ForumQuestion.objects.filter(title="Guest attempt").exists())

    def test_student_creates_own_department_and_everyone_questions(self):
        self.sign_in(self.student)
        response = self.client.post(
            self.forum_url("forum_question_create"),
            {
                "title": "Student department question",
                "body": "Only SKE students should see this.",
                "visibility": ForumQuestion.Visibility.DEPARTMENT,
                "is_published": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        question = ForumQuestion.objects.get(title="Student department question")
        self.assertEqual(list(question.departments.all()), [self.ske])
        self.assertTrue(question.is_published)
        self.assertEqual(question.author_label, "Forum Student")
        self.assertEqual(question.author_role, "Student")

        response = self.client.post(
            self.forum_url("forum_question_create"),
            {
                "title": "Student everyone question",
                "body": "This is for everyone.",
                "visibility": ForumQuestion.Visibility.EVERYONE,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            ForumQuestion.objects.get(title="Student everyone question").is_published
        )

    def test_student_forged_department_tag_is_rejected(self):
        self.sign_in(self.student)
        response = self.client.post(
            self.forum_url("forum_question_create"),
            {
                "title": "Forged tag question",
                "body": "Trying to select CPE.",
                "visibility": ForumQuestion.Visibility.DEPARTMENT,
                "departments": [self.cpe.pk],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ForumQuestion.objects.filter(title="Forged tag question").exists())
        self.assertContains(response, "Students may only use their own department tag.")

    def test_question_form_rejects_blank_and_oversized_content(self):
        self.sign_in(self.student)
        for title, body in (
            (" ", "Question body."),
            ("Long body question", "x" * 5001),
            ("x" * 201, "Question body."),
        ):
            with self.subTest(title_length=len(title), body_length=len(body)):
                response = self.client.post(
                    self.forum_url("forum_question_create"),
                    {
                        "title": title,
                        "body": body,
                        "visibility": ForumQuestion.Visibility.EVERYONE,
                    },
                )
                self.assertEqual(response.status_code, 200)
        self.assertFalse(
            ForumQuestion.objects.filter(title="Long body question").exists()
        )

    def test_question_creation_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.student)
        response = csrf_client.post(
            self.forum_url("forum_question_create"),
            {
                "title": "CSRF question",
                "body": "Must not be saved without a token.",
                "visibility": ForumQuestion.Visibility.EVERYONE,
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ForumQuestion.objects.filter(title="CSRF question").exists())

    def test_unknown_student_department_fails_closed(self):
        from accounts.models import User

        unknown_student = User.objects.create_user(
            nisit_id="7600000006",
            email="forum-unknown@ku.th",
            password="test-password",
            first_name="Unknown",
            last_name="Student",
            department="unknown",
        )
        self.client.force_login(unknown_student)
        response = self.client.get(self.forum_url("faq_board"))
        titles = {question.title for question in response.context["threads"]}
        self.assertIn("Everyone question", titles)
        self.assertNotIn("SKE question", titles)
        self.assertNotIn("CPE question", titles)
        self.assertContains(response, "department is not configured")

        response = self.client.post(
            self.forum_url("forum_question_create"),
            {
                "title": "Unknown department question",
                "body": "Cannot be department-scoped.",
                "visibility": ForumQuestion.Visibility.DEPARTMENT,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not configured for FAQ questions")
        self.assertFalse(
            ForumQuestion.objects.filter(title="Unknown department question").exists()
        )

    def test_lecturer_creates_everyone_and_department_questions(self):
        self.sign_in(self.lecturer)
        response = self.client.post(
            self.forum_url("forum_question_create"),
            {
                "title": "Lecturer public question",
                "body": "Visible to everyone.",
                "visibility": ForumQuestion.Visibility.EVERYONE,
                "is_published": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        public = ForumQuestion.objects.get(title="Lecturer public question")
        self.assertEqual(public.visibility, ForumQuestion.Visibility.EVERYONE)
        self.assertEqual(public.author_label, "Forum Lecturer")

        response = self.client.post(
            self.forum_url("forum_question_create"),
            {
                "title": "Lecturer CPE question",
                "body": "Department-specific guidance.",
                "visibility": "cpe",
                "is_published": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        department_question = ForumQuestion.objects.get(
            title="Lecturer CPE question"
        )
        self.assertEqual(list(department_question.departments.all()), [self.cpe])
        self.assertTrue(department_question.is_published)

    def test_lecturer_can_edit_any_visible_question_except_pinning(self):
        self.sign_in(self.lecturer)
        response = self.client.post(
            self.forum_url("forum_question_edit", self.cpe_question.pk),
            {
                "title": "Updated SKE question",
                "body": "Lecturer updated this content.",
                "visibility": "ske",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.cpe_question.refresh_from_db()
        self.assertEqual(self.cpe_question.title, "Updated SKE question")
        self.assertEqual(
            self.cpe_question.visibility,
            ForumQuestion.Visibility.DEPARTMENT,
        )
        self.assertEqual(list(self.cpe_question.departments.all()), [self.ske])

    def test_department_can_update_archive_and_restore_questions_and_replies(self):
        self.sign_in(self.department)
        response = self.client.post(
            self.forum_url("forum_question_create"),
            {
                "title": "Department managed question",
                "body": "Department owns publication state.",
                "visibility": "cpe",
                "is_published": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        question = ForumQuestion.objects.get(title="Department managed question")
        self.assertFalse(question.is_published)

        response = self.client.post(
            self.forum_url("forum_question_edit", question.pk),
            {
                "title": "Department managed question",
                "body": "Department changed the programme tag.",
                "visibility": "ske",
                "is_published": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        question.refresh_from_db()
        self.assertEqual(list(question.departments.all()), [self.ske])
        self.assertFalse(question.is_published)

        response = self.client.post(
            self.forum_url("forum_question_edit", question.pk),
            {
                "title": "Department updated question",
                "body": "Updated and published.",
                "visibility": ForumQuestion.Visibility.EVERYONE,
                "is_published": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        question.refresh_from_db()
        self.assertEqual(question.visibility, ForumQuestion.Visibility.EVERYONE)
        self.assertTrue(question.is_published)

        response = self.client.post(
            self.forum_url("forum_reply_create", question.pk),
            {"body": "Department answer."},
        )
        self.assertEqual(response.status_code, 302)
        reply = question.replies.get()
        self.assertEqual(reply.author_label, "Forum Department")
        self.assertEqual(reply.author_role, "Department")

        response = self.client.post(
            self.forum_url("forum_reply_edit", reply.pk),
            {"body": "Updated Department answer."},
        )
        self.assertEqual(response.status_code, 302)
        reply.refresh_from_db()
        self.assertEqual(reply.body, "Updated Department answer.")

        self.client.post(self.forum_url("forum_question_archive", question.pk))
        self.client.post(self.forum_url("forum_reply_archive", reply.pk))
        question.refresh_from_db()
        reply.refresh_from_db()
        self.assertIsNotNone(question.archived_at)
        self.assertIsNotNone(reply.archived_at)
        self.assertTrue(ForumQuestion.objects.filter(pk=question.pk).exists())

        self.client.post(self.forum_url("forum_question_restore", question.pk))
        self.client.post(self.forum_url("forum_reply_restore", reply.pk))
        question.refresh_from_db()
        reply.refresh_from_db()
        self.assertIsNone(question.archived_at)
        self.assertIsNone(reply.archived_at)

    def test_archive_endpoint_is_post_only(self):
        self.sign_in(self.department)
        response = self.client.get(
            self.forum_url("forum_question_archive", self.everyone_question.pk)
        )
        self.assertEqual(response.status_code, 405)

    def test_only_department_receives_publication_control(self):
        for user in (self.student, self.lecturer):
            self.sign_in(user)
            response = self.client.get(self.forum_url("forum_question_create"))
            self.assertNotIn("is_published", response.context["question_form"].fields)
            self.assertNotIn("is_pinned", response.context["question_form"].fields)

        self.sign_in(self.department)
        response = self.client.get(self.forum_url("forum_question_create"))
        self.assertIn("is_published", response.context["question_form"].fields)
        self.assertIn("is_pinned", response.context["question_form"].fields)

    def test_staff_can_create_shared_categories_but_students_cannot(self):
        self.sign_in(self.lecturer)
        create_page = self.client.get(
            self.forum_url("forum_question_create")
        )
        self.assertContains(create_page, 'id="forum-question-form"')
        self.assertContains(create_page, "data-forum-category-link")
        self.assertContains(create_page, "announcements/js/faq-board.js")
        response = self.client.post(
            self.forum_url("forum_category_create"),
            {"name": "Registration"},
        )
        self.assertEqual(response.status_code, 302)
        category = ForumCategory.objects.get(name="Registration")
        self.assertEqual(category.created_by, self.lecturer)
        edit_url = self.forum_url(
            "forum_question_edit",
            self.everyone_question.pk,
        )
        response = self.client.post(
            self.forum_url("forum_category_create"),
            {"name": "Editing", "next": edit_url},
        )
        self.assertRedirects(response, edit_url)
        edit_page = self.client.get(edit_url)
        self.assertContains(edit_page, "Create a category")
        self.assertIn(
            category,
            edit_page.context["question_form"].fields["category"].queryset,
        )

        response = self.client.post(
            self.forum_url("forum_question_create"),
            {
                "title": "Categorized question",
                "body": "Category selection persists.",
                "visibility": ForumQuestion.Visibility.EVERYONE,
                "category": category.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        question = ForumQuestion.objects.get(title="Categorized question")
        self.assertEqual(question.category, category)

        self.sign_in(self.student)
        response = self.client.post(
            self.forum_url("forum_category_create"),
            {"name": "Student category"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ForumCategory.objects.filter(name="Student category").exists())

    def test_department_only_can_pin_and_pinned_question_appears_with_legacy_faqs(self):
        question = self.everyone_question
        self.sign_in(self.lecturer)
        denied = self.client.post(
            self.forum_url("forum_question_pin", question.pk)
        )
        self.assertEqual(denied.status_code, 403)
        question.refresh_from_db()
        self.assertFalse(question.is_pinned)

        self.sign_in(self.department)
        accepted = self.client.post(
            self.forum_url("forum_question_pin", question.pk)
        )
        self.assertEqual(accepted.status_code, 302)
        question.refresh_from_db()
        self.assertTrue(question.is_pinned)

        Faq.objects.create(
            question="Legacy FAQ entry",
            answer="Legacy answer.",
        )
        self.client.post(reverse("accounts:guest_login"))
        homepage = self.client.get(reverse("announcements:public_board"))
        self.assertContains(homepage, "Everyone question")
        self.assertContains(homepage, "Legacy FAQ entry")
        self.assertContains(homepage, "Open question and comments")

        self.client.force_login(self.department)
        self.client.post(self.forum_url("forum_question_pin", question.pk))
        question.refresh_from_db()
        self.assertFalse(question.is_pinned)

    def test_department_and_lecturer_choose_programme_as_audience(self):
        from ..forms import ForumQuestionForm

        form = ForumQuestionForm(
            data={
                "title": "CPE audience question",
                "body": "The programme tag is selected automatically.",
                "visibility": "cpe",
            },
            author=self.lecturer,
        )
        self.assertNotIn("departments", form.fields)
        self.assertEqual(
            list(form.fields["visibility"].choices),
            [
                (ForumQuestion.Visibility.EVERYONE, "Everyone"),
                ("ske", "SKE"),
                ("cpe", "CPE"),
            ],
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["visibility"],
            ForumQuestion.Visibility.DEPARTMENT,
        )
        self.assertEqual(form.selected_departments(), [self.cpe])
