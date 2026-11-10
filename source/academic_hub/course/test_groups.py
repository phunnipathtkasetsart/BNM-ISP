import json
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import (
    CourseTA,
    Enrollment,
    GroupInvitation,
    GroupMembership,
    GroupWork,
    StudentGroup,
)
from .services import create_course


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

    def test_duplicate_group_work_title_keeps_values_and_shows_error(self):
        self.make_work(title="Design")
        self.client.force_login(self.lecturer)

        response = self.client.post(
            self.url("group_work_index", self.course.pk),
            {"title": " design ", "max_members": "4"},
        )

        self.assertContains(response, "A group work with that title already exists.")
        self.assertContains(response, 'value=" design "')
        self.assertContains(response, 'value="4"')
        self.assertEqual(GroupWork.objects.filter(course=self.course).count(), 1)

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

    def test_group_head_can_set_contact_for_members_and_api(self):
        work = self.make_work(max_members=3)
        group = self.make_group(work=work)
        GroupMembership.objects.create(
            group_work=work, group=group, student=self.invitee
        )
        self.client.force_login(self.student)
        response = self.client.post(
            self.url("group_contact_update", self.course.pk, work.pk, group.pk),
            {
                "third_party_contact": (
                    "Project coordinator\n"
                    "Details: https://example.com/contact\n"
                    "Phone: 012-345-6789"
                )
            },
        )
        self.assertEqual(response.status_code, 302)
        group.refresh_from_db()
        self.assertEqual(
            group.third_party_contact,
            "Project coordinator\nDetails: https://example.com/contact\nPhone: 012-345-6789",
        )

        head_page = self.client.get(self.url("group_work", self.course.pk, work.pk))
        self.assertContains(head_page, "Add third-party contact")
        self.assertContains(head_page, "Project coordinator")
        self.assertContains(head_page, "name=\"third_party_contact\"")

        self.client.force_login(self.invitee)
        member_page = self.client.get(self.url("group_work", self.course.pk, work.pk))
        self.assertContains(member_page, "Project coordinator")
        self.assertContains(
            member_page,
            '<a target="_blank" href="https://example.com/contact"',
            html=False,
        )
        self.assertContains(member_page, 'rel="noopener noreferrer"', html=False)
        self.assertNotContains(member_page, "name=\"third_party_contact\"")

        response = self.client.get(self.url("api_group_work", self.course.pk, work.pk))
        self.assertEqual(
            response.json()["groups"][0]["third_party_contact"],
            "Project coordinator\nDetails: https://example.com/contact\nPhone: 012-345-6789",
        )

        self.client.force_login(self.lecturer)
        staff_response = self.client.get(
            self.url("api_group_work", self.course.pk, work.pk)
        )
        self.assertIsNone(
            staff_response.json()["groups"][0]["third_party_contact"]
        )

    def test_only_group_head_can_update_contact(self):
        work = self.make_work()
        group = self.make_group(work=work)
        self.client.force_login(self.other_student)
        response = self.client.post(
            self.url("group_contact_update", self.course.pk, work.pk, group.pk),
            {"third_party_contact": "Unauthorized"},
        )
        self.assertEqual(response.status_code, 403)
        group.refresh_from_db()
        self.assertEqual(group.third_party_contact, "")

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
        group_id = response.json()["id"]

        response = self.client.post(
            self.url("api_group_contact_update", self.course.pk, work.pk, group_id),
            data=json.dumps({"third_party_contact": "Contact via email@example.test"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["third_party_contact"],
            "Contact via email@example.test",
        )

        response = self.client.post(
            self.url("api_group_contact_update", self.course.pk, work.pk, group_id),
            data=json.dumps({"third_party_contact": "x" * 501}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

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

    def test_department_and_lecturer_can_view_all_group_members(self):
        work = self.make_work()
        self.make_group(work=work)
        details_url = self.url("group_work", self.course.pk, work.pk)

        for staff_user in (self.lecturer, self.ta, self.department):
            self.client.force_login(staff_user)
            response = self.client.get(details_url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, self.student.nisit_id)

    def test_chat_routes_and_group_chat_controls_are_removed(self):
        work = self.make_work()
        group = self.make_group(work=work)
        self.client.force_login(self.student)

        response = self.client.get(self.url("group_work", self.course.pk, work.pk))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Open chat")

        self.assertEqual(
            self.client.get(f"/course/{self.course.pk}/groups/{group.pk}/chat/").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                f"/course/api/{self.course.pk}/groups/{group.pk}/chat/"
            ).status_code,
            404,
        )
