import json
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import (
    CourseTA,
    Enrollment,
    GroupInvitation,
    GroupMembership,
    GroupMessage,
    GroupMessageAttachment,
    GroupWork,
    StudentGroup,
)
from .services import create_course


GROUP_MEDIA = tempfile.mkdtemp(prefix="group-test-media-")


@override_settings(MEDIA_ROOT=GROUP_MEDIA)
class GroupWorkTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.lecturer = User.objects.create_user(
            nisit_id="6610501001", email="group-teacher@ku.th", password="test-pass-123",
            first_name="Group", last_name="Teacher", department="ske", is_staff=True,
        )
        cls.ta = User.objects.create_user(
            nisit_id="6610501002", email="group-ta@ku.th", password="test-pass-123",
            first_name="Group", last_name="Assistant", department="ske",
        )
        cls.student = User.objects.create_user(
            nisit_id="6610501003", email="group-student@ku.th", password="test-pass-123",
            first_name="First", last_name="Student", department="ske",
        )
        cls.invitee = User.objects.create_user(
            nisit_id="6610501004", email="group-invitee@ku.th", password="test-pass-123",
            first_name="Second", last_name="Student", department="ske",
        )
        cls.other_student = User.objects.create_user(
            nisit_id="6610501005", email="group-other@ku.th", password="test-pass-123",
            first_name="Third", last_name="Student", department="ske",
        )
        cls.department = User.objects.create_user(
            nisit_id="D0001", email="group-dept@ku.th", password="test-pass-123",
            first_name="Department", last_name="Admin", department="ske",
            is_staff=True, is_superuser=True,
        )
        cls.course = create_course(owner=cls.lecturer, name="Group Work Course", section="001")
        for student in (cls.ta, cls.student, cls.invitee, cls.other_student):
            Enrollment.objects.create(course=cls.course, student=student)
        CourseTA.objects.create(course=cls.course, user=cls.ta)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(GROUP_MEDIA, ignore_errors=True)

    def url(self, name, *args):
        return reverse(f"course:{name}", args=args)

    def make_work(self, *, title="Project", max_members=2):
        return GroupWork.objects.create(
            course=self.course, title=title, max_members=max_members, created_by=self.lecturer
        )

    def make_group(self, work=None, head=None, name="Team"):
        work = work or self.make_work()
        head = head or self.student
        group = StudentGroup.objects.create(group_work=work, name=name, head=head)
        GroupMembership.objects.create(group_work=work, group=group, student=head)
        return group

    def test_lecturer_and_ta_can_create_group_work_but_department_and_students_cannot(self):
        url = self.url("group_work_index", self.course.pk)
        self.client.force_login(self.lecturer)
        response = self.client.post(url, {"title": "Design", "max_members": "4"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(GroupWork.objects.filter(course=self.course, title="Design").exists())

        self.client.force_login(self.ta)
        response = self.client.post(url, {"title": "Review", "max_members": "3"})
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.department)
        self.assertEqual(
            self.client.post(url, {"title": "Forbidden", "max_members": "4"}).status_code,
            403,
        )
        self.client.force_login(self.student)
        self.assertEqual(
            self.client.post(url, {"title": "Forbidden", "max_members": "4"}).status_code,
            403,
        )

    def test_student_creates_one_group_per_work_and_can_join_another_work(self):
        first = self.make_work(title="First work")
        second = self.make_work(title="Second work")
        self.client.force_login(self.student)

        response = self.client.post(
            self.url("group_create", self.course.pk, first.pk), {"name": "First team"}
        )
        self.assertEqual(response.status_code, 302)
        group = StudentGroup.objects.get(group_work=first)
        self.assertTrue(GroupMembership.objects.filter(
            group_work=first, group=group, student=self.student
        ).exists())

        self.client.post(
            self.url("group_create", self.course.pk, first.pk), {"name": "Another team"}
        )
        self.assertEqual(StudentGroup.objects.filter(group_work=first).count(), 1)

        self.client.post(
            self.url("group_create", self.course.pk, second.pk), {"name": "Second team"}
        )
        self.assertEqual(GroupMembership.objects.filter(student=self.student).count(), 2)

    def test_invite_acceptance_checks_membership_and_capacity(self):
        work = self.make_work(max_members=2)
        group = self.make_group(work=work)
        self.client.force_login(self.student)
        response = self.client.post(
            self.url("group_invite", self.course.pk, work.pk, group.pk),
            {"nisit_id": self.invitee.nisit_id},
        )
        self.assertEqual(response.status_code, 302)
        invitation = GroupInvitation.objects.get(invitee=self.invitee, group=group)

        from notifications.models import Notification

        self.assertTrue(Notification.objects.filter(
            recipient=self.invitee, event_key=f"group-invitation:{invitation.pk}"
        ).exists())

        self.client.force_login(self.invitee)
        response = self.client.post(self.url(
            "group_invitation_action", self.course.pk, work.pk, invitation.pk, "accept"
        ))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(GroupMembership.objects.filter(
            group_work=work, group=group, student=self.invitee
        ).exists())

        self.client.force_login(self.student)
        response = self.client.post(
            self.url("group_invite", self.course.pk, work.pk, group.pk),
            {"nisit_id": self.other_student.nisit_id},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(GroupInvitation.objects.filter(
            group=group, invitee=self.other_student
        ).exists())

    def test_group_api_returns_live_slots_and_enforces_capacity(self):
        work = self.make_work(max_members=1)
        self.client.force_login(self.student)
        response = self.client.get(self.url("api_group_work_index", self.course.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["group_works"][0]["max_members"], 1)

        response = self.client.post(
            self.url("api_group_create", self.course.pk, work.pk),
            {"name": "Full team"},
        )
        self.assertEqual(response.status_code, 201)
        group = StudentGroup.objects.get(group_work=work)

        response = self.client.get(self.url("api_group_work", self.course.pk, work.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["groups"][0]["remaining_slots"], 0)
        self.assertEqual(response.json()["groups"][0]["members"][0]["student_id"], self.student.nisit_id)

        response = self.client.post(
            self.url("api_group_invite", self.course.pk, work.pk, group.pk),
            {"nisit_id": self.invitee.nisit_id},
        )
        self.assertEqual(response.status_code, 409)

    def test_group_api_accepts_json_mutations(self):
        self.client.force_login(self.lecturer)
        response = self.client.post(
            self.url("api_group_work_index", self.course.pk),
            data=json.dumps({"title": "JSON project", "max_members": "4"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        work = GroupWork.objects.get(course=self.course, title="JSON project")

        self.client.force_login(self.student)
        response = self.client.post(
            self.url("api_group_create", self.course.pk, work.pk),
            data=json.dumps({"name": "JSON team"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["name"], "JSON team")

    def test_group_gathering_is_available_in_course_sidebar(self):
        work = self.make_work(title="Design project")
        self.client.force_login(self.student)
        response = self.client.get(self.url("detail", self.course.pk))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("Group gathering", html)
        self.assertIn("Design project", html)
        self.assertIn(
            self.url("group_work", self.course.pk, work.pk),
            html,
        )
        self.assertNotIn(
            f'href="{self.url("group_work_index", self.course.pk)}">Group work</a>',
            html,
        )

    def test_head_may_transfer_and_kick_but_cannot_leave(self):
        work = self.make_work()
        group = self.make_group(work=work)
        GroupMembership.objects.create(
            group_work=work, group=group, student=self.invitee
        )
        self.client.force_login(self.student)
        self.client.post(self.url(
            "group_member_action", self.course.pk, work.pk, group.pk, "leave"
        ))
        group.refresh_from_db()
        self.assertEqual(group.head_id, self.student.pk)

        self.client.post(
            self.url("group_member_action", self.course.pk, work.pk, group.pk, "transfer"),
            {"student_id": self.invitee.pk},
        )
        group.refresh_from_db()
        self.assertEqual(group.head_id, self.invitee.pk)

        self.client.force_login(self.invitee)
        self.client.post(
            self.url("group_member_action", self.course.pk, work.pk, group.pk, "kick"),
            {"student_id": self.student.pk},
        )
        self.assertFalse(GroupMembership.objects.filter(group=group, student=self.student).exists())

    def test_department_and_lecturer_can_view_members_but_never_chat(self):
        work = self.make_work()
        group = self.make_group(work=work)
        details_url = self.url("group_work", self.course.pk, work.pk)
        chat_url = self.url("group_chat", self.course.pk, group.pk)

        for staff_user in (self.lecturer, self.ta, self.department):
            self.client.force_login(staff_user)
            response = self.client.get(details_url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, self.student.nisit_id)
            self.assertEqual(self.client.get(chat_url).status_code, 403)

    def test_members_can_post_text_and_inline_image_with_50_mb_limit(self):
        work = self.make_work()
        group = self.make_group(work=work)
        chat_url = self.url("group_chat", self.course.pk, group.pk)
        self.client.force_login(self.student)

        response = self.client.post(chat_url, {
            "body": "Hello team",
            "files": SimpleUploadedFile(
                "diagram.png", b"\x89PNG\r\n\x1a\n", content_type="image/png"
            ),
        })
        self.assertEqual(response.status_code, 302)
        message = GroupMessage.objects.get(group=group)
        attachment = GroupMessageAttachment.objects.get(message=message)
        self.assertEqual(message.body, "Hello team")
        self.assertTrue(attachment.is_inline_image)
        response = self.client.get(chat_url)
        self.assertContains(response, "Hello team")
        html = response.content.decode()
        input_row = html.split('class="group-chat__input-row"', 1)[1]
        self.assertLess(
            input_row.index('class="group-file-picker"'),
            input_row.index('id="chat-body"'),
        )
        self.assertIn("group-chat.js", html)
        self.assertIn("Press Shift and Enter for a new line", html)
        self.assertNotIn(">Send message</button>", html)
        self.assertIn('data-file-preview', html)
        self.assertIn('data-chat-file-input', html)

        with override_settings(MAX_UPLOAD_BYTES=10):
            response = self.client.post(chat_url, {
                "body": "",
                "files": SimpleUploadedFile("large.bin", b"x" * 11),
            })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(GroupMessage.objects.filter(group=group).count(), 1)

    def test_non_members_cannot_read_group_chat_or_attachments(self):
        work = self.make_work()
        group = self.make_group(work=work)
        message = GroupMessage.objects.create(group=group, sender=self.student, body="Private")
        attachment = GroupMessageAttachment.objects.create(
            message=message, file=SimpleUploadedFile("image.png", b"image")
        )
        self.client.force_login(self.other_student)
        self.assertEqual(self.client.get(
            self.url("group_chat", self.course.pk, group.pk)
        ).status_code, 403)
        self.assertEqual(self.client.get(
            self.url("group_attachment", self.course.pk, attachment.pk)
        ).status_code, 403)
