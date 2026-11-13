import json
import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from notifications.models import Notification

from .models import (
    CourseTA,
    Enrollment,
    GroupInvitation,
    GroupJoinRequest,
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

    def make_group(self, work=None, head=None, name="Team", is_public=False):
        work = work or self.make_work()
        head = head or self.student
        group = StudentGroup.objects.create(
            group_work=work, name=name, head=head, is_public=is_public
        )
        GroupMembership.objects.create(group_work=work, group=group, student=head)
        return group

    def make_extra_student(self, index):
        return get_user_model().objects.create_user(
            nisit_id=f"661059{index:04d}",
            email=f"group-extra-{index}@ku.th",
            password="test-pass-123",
            first_name="Extra",
            last_name=f"Student{index}",
            department="ske",
        )

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
        self.assertTrue(Notification.objects.filter(
            recipient=self.student,
            event_key=f"group-invitation-response:{invitation.pk}:accepted",
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
            data=json.dumps({"name": "JSON team", "is_public": "true"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["name"], "JSON team")
        self.assertTrue(response.json()["is_public"])
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
        self.assertTrue(Notification.objects.filter(
            recipient=self.student,
            kind="group_member_removed",
            message__contains=group.name,
        ).exists())
        self.assertFalse(Notification.objects.filter(
            recipient=self.invitee, kind="group_member_removed"
        ).exists())

    def test_group_creation_visibility_and_name_uniqueness_across_types(self):
        work = self.make_work()
        self.client.force_login(self.student)
        response = self.client.post(
            self.url("group_create", self.course.pk, work.pk),
            {"name": "Open team", "is_public": "true"},
        )
        self.assertEqual(response.status_code, 302)
        group = StudentGroup.objects.get(group_work=work)
        self.assertTrue(group.is_public)

        self.client.force_login(self.invitee)
        response = self.client.post(
            self.url("group_create", self.course.pk, work.pk),
            {"name": " open team ", "is_public": "false"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(StudentGroup.objects.filter(group_work=work).count(), 1)

    def test_all_students_see_group_summaries_and_staff_never_see_contact_details(self):
        work = self.make_work(max_members=3)
        private_group = self.make_group(work=work, name="Private team")
        private_group.third_party_contact = "private contact detail"
        private_group.save(update_fields=["third_party_contact"])
        public_group = self.make_group(
            work=work, head=self.invitee, name="Public team", is_public=True
        )
        GroupMembership.objects.create(
            group_work=work, group=public_group, student=self.other_student
        )
        outsider = self.make_extra_student(1)
        Enrollment.objects.create(course=self.course, student=outsider)
        details_url = self.url("group_work", self.course.pk, work.pk)

        self.client.force_login(outsider)
        response = self.client.get(details_url)
        self.assertContains(response, "PRIVATE GROUP")
        self.assertContains(response, "PUBLIC GROUP")
        self.assertContains(response, "Private team")
        self.assertContains(response, "Public team")
        self.assertContains(response, "1/3")
        self.assertNotContains(response, self.student.nisit_id)
        self.assertNotContains(response, "private contact detail")
        api_response = self.client.get(self.url(
            "api_group_work", self.course.pk, work.pk
        ))
        private_data = next(
            group for group in api_response.json()["groups"]
            if group["id"] == private_group.pk
        )
        self.assertEqual(private_data["members"], [])
        self.assertIsNone(private_data["third_party_contact"])
        self.assertIsNone(private_data["head"])
        self.assertIsNone(private_data["pending_request_count"])

        for staff_user in (self.lecturer, self.ta, self.department):
            self.client.force_login(staff_user)
            response = self.client.get(details_url)
            self.assertContains(response, self.student.nisit_id)
            self.assertNotContains(response, "private contact detail")
            api_response = self.client.get(self.url(
                "api_group_work", self.course.pk, work.pk
            ))
            private_data = next(
                group for group in api_response.json()["groups"]
                if group["id"] == private_group.pk
            )
            self.assertTrue(private_data["members"])
            self.assertIsNone(private_data["third_party_contact"])

        self.client.force_login(self.student)
        response = self.client.get(details_url)
        self.assertContains(response, "private contact detail")
        self.assertContains(response, self.student.nisit_id)

    def test_public_group_join_notifies_only_the_head_and_full_group_cannot_be_joined(self):
        work = self.make_work(max_members=2)
        group = self.make_group(work=work, is_public=True)

        self.client.force_login(self.invitee)
        response = self.client.post(
            self.url("group_public_join", self.course.pk, work.pk, group.pk)
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(GroupMembership.objects.filter(
            group_work=work, group=group, student=self.invitee
        ).exists())
        self.assertTrue(Notification.objects.filter(
            recipient=self.student,
            kind="group_member_joined",
            message__contains=group.name,
        ).exists())
        self.assertFalse(Notification.objects.filter(
            recipient=self.invitee, kind="group_member_joined"
        ).exists())

        self.client.force_login(self.other_student)
        page = self.client.get(self.url("group_work", self.course.pk, work.pk))
        self.assertContains(page, "FULL")
        response = self.client.post(
            self.url("api_group_public_join", self.course.pk, work.pk, group.pk)
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(GroupMembership.objects.filter(group=group).count(), 2)

    def test_private_requests_reserve_capacity_and_ignore_notifies_only_the_requester(self):
        work = self.make_work(max_members=2)
        group = self.make_group(work=work)

        self.client.force_login(self.invitee)
        response = self.client.post(
            self.url("group_join_request_create", self.course.pk, work.pk, group.pk)
        )
        self.assertEqual(response.status_code, 302)
        join_request = GroupJoinRequest.objects.get(group=group, requester=self.invitee)
        self.assertTrue(Notification.objects.filter(
            recipient=self.student,
            event_key=f"group-join-request:{join_request.pk}",
        ).exists())
        self.assertFalse(Notification.objects.filter(
            recipient=self.invitee,
            event_key=f"group-join-request:{join_request.pk}",
        ).exists())

        page = self.client.get(self.url("group_work", self.course.pk, work.pk))
        self.assertContains(page, "Requested")
        self.client.force_login(self.other_student)
        page = self.client.get(self.url("group_work", self.course.pk, work.pk))
        self.assertContains(page, "Reserved")
        api_response = self.client.get(self.url(
            "api_group_work", self.course.pk, work.pk
        ))
        group_data = api_response.json()["groups"][0]
        self.assertEqual(group_data["member_count"], 1)
        self.assertEqual(group_data["remaining_slots"], 0)
        self.assertTrue(group_data["is_reserved"])

        response = self.client.post(
            self.url("api_group_join_request_create", self.course.pk, work.pk, group.pk)
        )
        self.assertEqual(response.status_code, 409)

        self.client.force_login(self.student)
        response = self.client.post(self.url(
            "group_join_request_action", self.course.pk, work.pk,
            join_request.pk, "ignore",
        ))
        self.assertEqual(response.status_code, 302)
        join_request.refresh_from_db()
        self.assertEqual(join_request.status, GroupJoinRequest.Status.IGNORED)
        self.assertTrue(Notification.objects.filter(
            recipient=self.invitee,
            event_key=f"group-join-request-response:{join_request.pk}:ignored",
        ).exists())
        self.assertFalse(Notification.objects.filter(
            recipient=self.other_student,
            event_key=f"group-join-request-response:{join_request.pk}:ignored",
        ).exists())

    def test_joining_one_group_cancels_other_pending_requests_and_invitations(self):
        work = self.make_work(max_members=3)
        requested_group = self.make_group(work=work, name="Requested")
        other_request_group = self.make_group(
            work=work, head=self.other_student, name="Other request"
        )
        invite_head = self.make_extra_student(1)
        second_invite_head = self.make_extra_student(2)
        Enrollment.objects.create(course=self.course, student=invite_head)
        Enrollment.objects.create(course=self.course, student=second_invite_head)
        invite_group = self.make_group(
            work=work, head=invite_head, name="Invitation one"
        )
        second_invite_group = self.make_group(
            work=work, head=second_invite_head, name="Invitation two"
        )

        self.client.force_login(self.invitee)
        requests = []
        for group in (requested_group, other_request_group):
            response = self.client.post(
                self.url("group_join_request_create", self.course.pk, work.pk, group.pk)
            )
            self.assertEqual(response.status_code, 302)
            requests.append(GroupJoinRequest.objects.get(group=group, requester=self.invitee))

        invitations = []
        for head, group in (
            (invite_head, invite_group),
            (second_invite_head, second_invite_group),
        ):
            self.client.force_login(head)
            response = self.client.post(
                self.url("group_invite", self.course.pk, work.pk, group.pk),
                {"nisit_id": self.invitee.nisit_id},
            )
            self.assertEqual(response.status_code, 302)
            invitations.append(GroupInvitation.objects.get(
                group=group, invitee=self.invitee
            ))

        self.client.force_login(self.student)
        response = self.client.post(self.url(
            "group_join_request_action", self.course.pk, work.pk,
            requests[0].pk, "accept",
        ))
        self.assertEqual(response.status_code, 302)
        requests[0].refresh_from_db()
        requests[1].refresh_from_db()
        self.assertEqual(requests[0].status, GroupJoinRequest.Status.ACCEPTED)
        self.assertEqual(requests[1].status, GroupJoinRequest.Status.CANCELLED)
        self.assertTrue(GroupMembership.objects.filter(
            group=requested_group, student=self.invitee
        ).exists())

        for invitation in invitations:
            invitation.refresh_from_db()
            self.assertEqual(invitation.status, GroupInvitation.Status.CANCELLED)
            self.assertIsNotNone(Notification.objects.get(
                recipient=self.invitee,
                event_key=f"group-invitation:{invitation.pk}",
            ).read_at)
        self.assertIsNotNone(Notification.objects.get(
            recipient=self.other_student,
            event_key=f"group-join-request:{requests[1].pk}",
        ).read_at)
        self.assertTrue(Notification.objects.filter(
            recipient=self.invitee,
            event_key=f"group-join-request-response:{requests[0].pk}:accepted",
        ).exists())

    def test_groups_are_ordered_by_member_count_and_fifo_with_current_group_first(self):
        work = self.make_work(max_members=5)
        own_group = self.make_group(work=work, name="My group")
        oldest_one = self.make_group(
            work=work, head=self.invitee, name="Oldest one", is_public=True
        )
        newer_one = self.make_group(work=work, head=self.other_student, name="Newer one")
        three_members = self.make_group(
            work=work, head=self.make_extra_student(1), name="Three members"
        )
        full_oldest = self.make_group(
            work=work, head=self.make_extra_student(4), name="Full oldest"
        )
        full_newer = self.make_group(
            work=work, head=self.make_extra_student(9), name="Full newer"
        )
        extras = [self.make_extra_student(index) for index in (2, 3, 5, 6, 7, 8, 10, 11, 12, 13)]
        for student in extras[:2]:
            GroupMembership.objects.create(
                group_work=work, group=three_members, student=student
            )
        for student in extras[2:6]:
            GroupMembership.objects.create(
                group_work=work, group=full_oldest, student=student
            )
        for student in extras[6:]:
            GroupMembership.objects.create(
                group_work=work, group=full_newer, student=student
            )
        now = timezone.now()
        StudentGroup.objects.filter(pk=oldest_one.pk).update(
            created_at=now - timedelta(days=4)
        )
        StudentGroup.objects.filter(pk=newer_one.pk).update(
            created_at=now - timedelta(days=3)
        )
        StudentGroup.objects.filter(pk=full_oldest.pk).update(
            created_at=now - timedelta(days=2)
        )
        StudentGroup.objects.filter(pk=full_newer.pk).update(
            created_at=now - timedelta(days=1)
        )

        self.client.force_login(self.student)
        response = self.client.get(self.url("group_work", self.course.pk, work.pk))
        group_ids = [
            int(group_id)
            for group_id in re.findall(
                r'data-group-card="(\d+)"', response.content.decode()
            )
        ]
        self.assertEqual(group_ids, [
            own_group.pk,
            oldest_one.pk,
            newer_one.pk,
            three_members.pk,
            full_oldest.pk,
            full_newer.pk,
        ])

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
