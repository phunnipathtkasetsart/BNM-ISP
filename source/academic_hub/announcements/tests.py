"""Database-backed tests for the public layer.

These are the tests that could not exist before: building a test database
failed because `Users` lived in a schema only db_backup.sql created. The
accounts 0002 migration now builds that schema, so the visibility rules the
whole public layer depends on can finally be asserted rather than eyeballed.
"""

from django.test import TestCase
from django.urls import reverse

from .models import Announcement, Audience, Faq, Tag


class GuestVisibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.dept_tag = Tag.objects.create(
            slug="department", label="Department", kind=Tag.Kind.DEPARTMENT
        )
        cls.course_tag = Tag.objects.create(
            slug="cs101", label="CS101", kind=Tag.Kind.COURSE
        )

        cls.public = Announcement.objects.create(
            title="Registration closes Friday",
            body="Add/drop closes at 23:59 on Friday.",
            audience=Audience.PUBLIC,
        )
        cls.public.tags.set([cls.dept_tag])

        cls.internal = Announcement.objects.create(
            title="CS101 Lab 3 extended",
            body="Lab 3 has been extended by one week.",
            audience=Audience.STUDENTS,
        )
        cls.internal.tags.set([cls.course_tag])

        cls.faq = Faq.objects.create(
            question="What is my Nisit ID?",
            answer="Your ten-digit student number.",
            audience=Audience.PUBLIC,
        )
        Faq.objects.create(
            question="Where is the staff room?",
            answer="Second floor.",
            audience=Audience.STAFF,
        )

    def test_guest_sees_only_public_announcements(self):
        response = self.client.get(reverse("announcements:public_board"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registration closes Friday")
        self.assertNotContains(response, "CS101 Lab 3 extended")

    def test_guest_sees_only_public_faqs(self):
        response = self.client.get(reverse("announcements:public_board"))
        self.assertContains(response, "What is my Nisit ID?")
        self.assertNotContains(response, "Where is the staff room?")

    def test_tag_filter_narrows_the_list(self):
        response = self.client.get(
            reverse("announcements:public_board"), {"tag": "department"}
        )
        self.assertContains(response, "Registration closes Friday")

    def test_search_never_leaks_a_non_public_announcement(self):
        """The rule most likely to break: search is a second read path."""
        response = self.client.get(
            reverse("announcements:public_board"), {"q": "CS101"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "CS101 Lab 3 extended")

    def test_unpublished_announcement_is_hidden(self):
        Announcement.objects.create(
            title="Draft notice", body="Not ready.",
            audience=Audience.PUBLIC, is_published=False,
        )
        response = self.client.get(reverse("announcements:public_board"))
        self.assertNotContains(response, "Draft notice")

    def test_guest_cannot_open_the_create_form(self):
        response = self.client.get(reverse("announcements:announcement_create"))
        self.assertEqual(response.status_code, 302)
