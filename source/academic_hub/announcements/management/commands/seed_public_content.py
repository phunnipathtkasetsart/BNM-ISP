"""Demo content for the public board, plus search-vector population.

Run after `migrate` so the board and the search index have something to work
with:

    docker compose exec web python manage.py seed_public_content

Idempotent - re-running updates the same rows rather than duplicating them.
"""

from datetime import timedelta

from django.contrib.postgres.search import SearchVector
from django.core.management.base import BaseCommand
from django.utils import timezone

from announcements.models import Announcement, Audience, Faq, Tag

TAGS = [
    ("department", "Department", Tag.Kind.DEPARTMENT),
    ("syllabus", "Syllabus", Tag.Kind.TOPIC),
    ("scholarship", "Scholarship", Tag.Kind.TOPIC),
    ("lab", "Lab", Tag.Kind.LAB),
]

ANNOUNCEMENTS = [
    {
        "title": "Registration for Semester 2 closes Friday",
        "body": (
            "Add/drop for Semester 2 closes at 23:59 on Friday. Changes after "
            "that need a signed form from your advisor and take up to five "
            "working days to process."
        ),
        "author_label": "Department", "audience": Audience.PUBLIC,
        "is_urgent": True, "deadline_in_days": 3,
        "tags": ["department"],
    },
    {
        "title": "New Academic Policy: Fall Term 2026",
        "body": (
            "The department has published the academic policy for Fall Term "
            "2026, covering attendance, resubmission and the revised grading "
            "appeal window. Read it before the term begins."
        ),
        "author_label": "Software Engineering Dept.", "audience": Audience.PUBLIC,
        "is_urgent": False, "deadline_in_days": None,
        "tags": ["department", "syllabus"],
    },
    {
        "title": "Scholarship applications open for 2026 intake",
        "body": (
            "Applications for the department scholarship are open to all "
            "enrolled students. Submit the form and one academic reference "
            "through the student office."
        ),
        "author_label": "Department", "audience": Audience.PUBLIC,
        "is_urgent": False, "deadline_in_days": 6,
        "tags": ["department", "scholarship"],
    },
    {
        "title": "Course Update: CS101 Lab 3 extended",
        "body": (
            "Lab 3 has been extended by one week following the lab closure. "
            "Submission reopens on Monday."
        ),
        # Course-scoped, so a guest must NOT see this. It is here on purpose,
        # as the negative case that proves the guest filter works.
        "author_label": "Lecturer", "audience": Audience.STUDENTS,
        "is_urgent": False, "deadline_in_days": 4,
        "tags": ["lab"],
    },
    {
        "title": "Lab access hours changed for the SKE lab",
        "body": (
            "The SKE lab now closes at 18:00 on weekdays while the card "
            "readers are replaced. Out-of-hours access is suspended."
        ),
        # Lab notices are internal and department-posted. Kept here as the
        # second negative case: a guest must not see this either.
        "author_label": "Department", "audience": Audience.STAFF,
        "is_urgent": False, "deadline_in_days": None,
        "tags": ["lab"],
    },
]

FAQS = [
    ("What is my Nisit ID?",
     "Your ten-digit student number, printed on your student card. Staff "
     "accounts use a different format, such as A0001.", ["department"]),
    ("Which email should I register with?",
     "Your KU address, ending in @ku.th. Personal addresses are not accepted.",
     ["department"]),
    ("I forgot my password.",
     "Use the 'Having Problems?' link on the sign-in page. Password reset by "
     "email is not available yet, so contact the student office meanwhile.",
     ["department"]),
    ("Why can I not see my course announcements?",
     "Course announcements are only visible once you sign in, because they "
     "are limited to students enrolled on that course.", ["syllabus"]),
    ("How do I contact the department office?",
     "The student office is on the second floor and answers email at "
     "office@ku.th. Opening hours are 09:00 to 16:00 on weekdays.", ["department"]),
    ("Can I sign in with Google?",
     "Google sign-in is being added. For now, use your Nisit ID and password.",
     ["department"]),
]


class Command(BaseCommand):
    help = "Seed demo announcements, FAQs and tags, then rebuild search vectors."

    def handle(self, *args, **options):
        now = timezone.now()

        tags = {}
        for slug, label, kind in TAGS:
            tag, _ = Tag.objects.update_or_create(
                slug=slug, defaults={"label": label, "kind": kind}
            )
            tags[slug] = tag

        for i, spec in enumerate(ANNOUNCEMENTS):
            days = spec.pop("deadline_in_days")
            tag_slugs = spec.pop("tags")
            ann, _ = Announcement.objects.update_or_create(
                title=spec["title"],
                defaults={
                    **spec,
                    "deadline": now + timedelta(days=days) if days else None,
                    # Spread the dates so ordering is visible on the board.
                    "published_at": now - timedelta(days=i * 2),
                },
            )
            ann.tags.set([tags[s] for s in tag_slugs])

        for position, (question, answer, tag_slugs) in enumerate(FAQS):
            faq, _ = Faq.objects.update_or_create(
                question=question,
                defaults={"answer": answer, "position": position,
                          "audience": Audience.PUBLIC},
            )
            faq.tags.set([tags[s] for s in tag_slugs])

        self.rebuild_search_vectors()

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {Announcement.objects.count()} announcements, "
            f"{Faq.objects.count()} FAQs, {Tag.objects.count()} tags."
        ))

    def rebuild_search_vectors(self):
        """Populate the tsvector columns the GIN indexes cover.

        Weighted so a title match outranks a body match: 'A' is the highest
        weight Postgres assigns, 'B' the next.
        """
        Announcement.objects.update(
            search_vector=SearchVector("title", weight="A")
            + SearchVector("body", weight="B")
        )
        Faq.objects.update(
            search_vector=SearchVector("question", weight="A")
            + SearchVector("answer", weight="B")
        )
        self.stdout.write("Search vectors rebuilt.")
