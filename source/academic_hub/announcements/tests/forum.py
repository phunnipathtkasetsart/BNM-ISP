from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from ..models import ForumQuestion, Tag


class ForumTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        from accounts.models import User

        cls.ske, _ = Tag.objects.update_or_create(
            slug="ske",
            defaults={"label": "SKE", "kind": Tag.Kind.PROGRAMME},
        )
        cls.cpe, _ = Tag.objects.update_or_create(
            slug="cpe",
            defaults={"label": "CPE", "kind": Tag.Kind.PROGRAMME},
        )

        cls.student = User.objects.create_user(
            nisit_id="7600000001",
            email="forum-student@ku.th",
            password="test-password",
            first_name="Forum",
            last_name="Student",
            department="SKE",
        )
        cls.other_student = User.objects.create_user(
            nisit_id="7600000002",
            email="forum-cpe@ku.th",
            password="test-password",
            first_name="Cpe",
            last_name="Student",
            department="cpe",
        )
        cls.lecturer = User.objects.create_user(
            nisit_id="7600000003",
            email="forum-lecturer@ku.th",
            password="test-password",
            first_name="Forum",
            last_name="Lecturer",
            department="ske",
            is_staff=True,
        )
        cls.other_lecturer = User.objects.create_user(
            nisit_id="7600000004",
            email="forum-lecturer2@ku.th",
            password="test-password",
            first_name="Other",
            last_name="Lecturer",
            department="cpe",
            is_staff=True,
        )
        cls.department = User.objects.create_user(
            nisit_id="7600000005",
            email="forum-department@ku.th",
            password="test-password",
            first_name="Forum",
            last_name="Department",
            department="ske",
            is_superuser=True,
        )

        cls.everyone_question = ForumQuestion.objects.create(
            title="Everyone question",
            body="Visible to every reader.",
            visibility=ForumQuestion.Visibility.EVERYONE,
            author=cls.student,
            author_label=cls.student.get_full_name(),
        )
        cls.ske_question = ForumQuestion.objects.create(
            title="SKE question",
            body="SKE department content.",
            visibility=ForumQuestion.Visibility.DEPARTMENT,
            author=cls.student,
            author_label=cls.student.get_full_name(),
        )
        cls.ske_question.departments.add(cls.ske)
        cls.cpe_question = ForumQuestion.objects.create(
            title="CPE question",
            body="CPE department content.",
            visibility=ForumQuestion.Visibility.DEPARTMENT,
            author=cls.other_student,
            author_label=cls.other_student.get_full_name(),
        )
        cls.cpe_question.departments.add(cls.cpe)
        cls.unpublished_question = ForumQuestion.objects.create(
            title="Unpublished question",
            body="Not yet visible.",
            visibility=ForumQuestion.Visibility.EVERYONE,
            is_published=False,
            author=cls.student,
            author_label=cls.student.get_full_name(),
        )
        cls.archived_question = ForumQuestion.objects.create(
            title="Archived question",
            body="Retained for Department.",
            visibility=ForumQuestion.Visibility.DEPARTMENT,
            archived_at=timezone.now(),
            author=cls.other_student,
            author_label=cls.other_student.get_full_name(),
        )
        cls.archived_question.departments.add(cls.cpe)

    def sign_in(self, user):
        self.client.force_login(user)

    def forum_url(self, name, *args):
        return reverse(f"announcements:{name}", args=args)

    def question_titles(self, user):
        self.client.force_login(user)
        response = self.client.get(self.forum_url("faq_board"))
        self.assertEqual(response.status_code, 200)
        return {question.title for question in response.context["threads"]}
