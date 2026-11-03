from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from announcements.models import Announcement, Audience
from calendar_app.models import DayNote
from course.models import CoursePost, Enrollment
from course.services import add_students, create_course

from .models import Notification
from .services import notify_announcement, notify_course_post, notify_deadline


class NotificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.lecturer = User.objects.create_user(
            nisit_id="6610502001", email="notice-teacher@ku.th", password="test-pass-123",
            first_name="Notice", last_name="Teacher", department="ske", is_staff=True,
        )
        cls.student = User.objects.create_user(
            nisit_id="6610502002", email="notice-student@ku.th", password="test-pass-123",
            first_name="Notice", last_name="Student", department="ske",
        )
        cls.other_student = User.objects.create_user(
            nisit_id="6610502003", email="notice-other@ku.th", password="test-pass-123",
            first_name="Other", last_name="Student", department="cpe",
        )
        cls.course = create_course(owner=cls.lecturer, name="Notice Course", section="001")
        Enrollment.objects.create(course=cls.course, student=cls.student)

    def test_api_lists_and_marks_only_the_recipient_notifications_read(self):
        notice = Notification.objects.create(
            recipient=self.student,
            kind="test",
            title="A notice",
            message="Details",
            url="/course/",
            event_key="test:one",
        )
        self.client.force_login(self.student)
        response = self.client.get(reverse("notifications:list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["unread_count"], 1)

        response = self.client.post(reverse("notifications:read", args=[notice.pk]))
        self.assertEqual(response.status_code, 200)
        notice.refresh_from_db()
        self.assertIsNotNone(notice.read_at)

        private_notice = Notification.objects.create(
            recipient=self.other_student,
            kind="test",
            title="Private",
            url="/",
            event_key="test:private",
        )
        self.assertEqual(
            self.client.post(reverse("notifications:read", args=[private_notice.pk])).status_code,
            404,
        )

    def test_personal_calendar_reminder_is_private_and_created_once(self):
        note = DayNote.objects.create(
            user=self.student,
            date=timezone.localdate(),
            title="Bring the prototype",
            notify=True,
        )
        self.client.force_login(self.student)
        for _ in range(2):
            response = self.client.get(reverse("notifications:list"))
            self.assertEqual(response.status_code, 200)
        reminder = Notification.objects.get(
            recipient=self.student,
            event_key=f"calendar-note:{note.pk}:{note.date.isoformat()}",
        )
        self.assertEqual(reminder.kind, "calendar_reminder")
        self.assertEqual(reminder.url, reverse("calendar:month"))

        self.client.force_login(self.other_student)
        self.client.get(reverse("notifications:list"))
        self.assertFalse(Notification.objects.filter(
            recipient=self.other_student,
            event_key=f"calendar-note:{note.pk}:{note.date.isoformat()}",
        ).exists())

    def test_roster_addition_and_course_post_create_notifications(self):
        self.assertEqual(add_students(self.course, [self.other_student]), 1)
        enrollment = Enrollment.objects.get(course=self.course, student=self.other_student)
        self.assertTrue(Notification.objects.filter(
            recipient=self.other_student,
            event_key=f"course-enrollment:{enrollment.pk}",
        ).exists())

        post = CoursePost.objects.create(
            course=self.course, author=self.lecturer, title="Project brief"
        )
        notify_course_post(post)
        self.assertTrue(Notification.objects.filter(
            recipient=self.student, kind="course_post", message="Project brief"
        ).exists())

    def test_announcement_notification_respects_published_visibility(self):
        announcement = Announcement.objects.create(
            title="Visible notice",
            body="Details",
            audience=Audience.STUDENTS,
            author=self.lecturer,
            is_published=True,
            published_at=timezone.now(),
        )
        notify_announcement(announcement)
        self.assertTrue(Notification.objects.filter(
            recipient=self.student,
            event_key__startswith=f"announcement:{announcement.pk}:",
        ).exists())
        self.assertFalse(Notification.objects.filter(
            recipient=self.lecturer,
            event_key__startswith=f"announcement:{announcement.pk}:",
        ).exists())

        draft = Announcement.objects.create(
            title="Draft notice",
            body="Hidden",
            audience=Audience.STUDENTS,
            author=self.lecturer,
            is_published=False,
        )
        notify_announcement(draft)
        self.assertFalse(Notification.objects.filter(
            event_key__startswith=f"announcement:{draft.pk}:"
        ).exists())

    def test_published_announcement_deadline_notifies_visible_readers(self):
        deadline = timezone.now() + timedelta(days=4)
        announcement = Announcement.objects.create(
            title="Submission deadline",
            body="Submit the report",
            audience=Audience.STUDENTS,
            author=self.lecturer,
            deadline=deadline,
            is_published=True,
        )
        notify_announcement(announcement)
        self.assertTrue(Notification.objects.filter(
            recipient=self.student,
            event_key=f"announcement-deadline:{announcement.pk}:{deadline.isoformat()}",
        ).exists())

        updated_at = timezone.now() - timedelta(days=2)
        upcoming = timezone.now() + timedelta(hours=12)
        Announcement.objects.filter(pk=announcement.pk).update(
            deadline=upcoming, updated_at=updated_at
        )
        self.client.force_login(self.student)
        self.client.get(reverse("notifications:list"))
        self.assertTrue(Notification.objects.filter(
            recipient=self.student,
            event_key=f"announcement-deadline-24h:{announcement.pk}:{upcoming.isoformat()}",
        ).exists())

    def test_deadline_reminder_is_created_once_when_within_24_hours(self):
        post = CoursePost.objects.create(
            course=self.course,
            author=self.lecturer,
            title="Due soon",
            deadline=timezone.now() + timedelta(hours=12),
        )
        CoursePost.objects.filter(pk=post.pk).update(
            created_at=timezone.now() - timedelta(days=2),
            updated_at=timezone.now() - timedelta(days=2),
        )
        self.client.force_login(self.student)
        for _ in range(2):
            self.client.get(reverse("notifications:list"))
        self.assertEqual(Notification.objects.filter(
            recipient=self.student,
            event_key=f"course-deadline-24h:{post.pk}:{post.deadline.isoformat()}",
        ).count(), 1)

    def test_deadline_is_announced_when_created_or_changed(self):
        post = CoursePost.objects.create(
            course=self.course,
            author=self.lecturer,
            title="New deadline",
            deadline=timezone.now() + timedelta(days=5),
        )
        notify_deadline(post)
        notice = Notification.objects.get(
            recipient=self.student,
            event_key=f"course-deadline:{post.pk}:{post.deadline.isoformat()}",
        )
        self.assertIn("New deadline", notice.message)
