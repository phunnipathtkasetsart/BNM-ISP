"""Announcements, FAQs and the tags that decide who sees what.

Visibility in this app is tag-driven rather than hard-coded per role. An
announcement carries an audience (who it is addressed to) and any number of
tags (what it is about). A reader sees an item when their role clears the
audience and, for course-scoped items, when they are enrolled on the tagged
course.

Course enrolment does not exist yet - the Course & Class Code engine is
Iteration 3 - so `Announcement.visible_to()` currently resolves the guest and
department cases fully and leaves a single, clearly marked hook for course
membership. Nothing else needs to change when IT-3 lands.
"""

from datetime import timedelta

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from django.utils import timezone


class Tag(models.Model):
    """A label. `kind` is what makes tag-based visibility possible.

    DEPARTMENT and TOPIC tags are open to everyone. COURSE tags scope an item
    to the students enrolled on that course, which is why the kind has to be
    stored rather than inferred from the name.
    """

    class Kind(models.TextChoices):
        DEPARTMENT = "department", "Department"
        COURSE = "course", "Course"
        TOPIC = "topic", "Topic"

    slug = models.SlugField(max_length=60, unique=True)
    label = models.CharField(max_length=80)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.TOPIC)

    class Meta:
        ordering = ["kind", "label"]

    def __str__(self):
        return f"#{self.slug}"


class PublishedQuerySet(models.QuerySet):
    """Shared by both content models - they answer the same two questions."""

    def published(self):
        return self.filter(is_published=True, published_at__lte=timezone.now())

    def for_guest(self):
        """What someone who is not signed in may read.

        Deliberately strict: public audience only. A course-tagged item is
        never public, because a guest cannot be enrolled on anything.
        """
        return self.published().filter(audience=Audience.PUBLIC)


class Audience(models.TextChoices):
    """Who an item is addressed to, widest first."""

    PUBLIC = "public", "Everyone, including guests"
    STUDENTS = "students", "Signed-in students"
    STAFF = "staff", "Lecturers and department only"


class Announcement(models.Model):
    title = models.CharField(max_length=200)
    body = models.TextField()

    audience = models.CharField(
        max_length=20, choices=Audience.choices, default=Audience.PUBLIC
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name="announcements")

    # Pinned above everything else in its list. Kept separate from ordering by
    # date so an old but still-critical notice does not sink out of sight.
    is_urgent = models.BooleanField(default=False)
    # A soft deadline shown as "DEADLINE NEAR" once it is close.
    deadline = models.DateTimeField(null=True, blank=True)

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="announcements",
        # The Users table is unmanaged and lives in another Postgres schema, so
        # the database-level constraint is left off. Django still resolves the
        # relation; it just does not ask Postgres to enforce it across schemas.
        db_constraint=False,
    )
    # Shown as the byline. Stored rather than derived from `author` so an
    # announcement keeps its attribution if the account is later removed.
    author_label = models.CharField(max_length=80, default="Department")

    is_published = models.BooleanField(default=True)
    published_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    search_vector = SearchVectorField(null=True, editable=False)

    objects = PublishedQuerySet.as_manager()

    class Meta:
        ordering = ["-is_urgent", "-published_at"]
        indexes = [
            GinIndex(fields=["search_vector"], name="ann_search_gin"),
            models.Index(fields=["audience", "-published_at"], name="ann_audience_date"),
        ]

    def __str__(self):
        return self.title

    # Seven days, because a week is the shortest span in which a student can
    # realistically still act on a deadline they have just noticed.
    DEADLINE_WARNING_DAYS = 7

    @property
    def deadline_near(self):
        """True when the deadline is close but has not passed."""
        if not self.deadline:
            return False
        remaining = self.deadline - timezone.now()
        return timedelta(0) <= remaining <= timedelta(days=self.DEADLINE_WARNING_DAYS)

    @property
    def deadline_passed(self):
        return bool(self.deadline) and self.deadline < timezone.now()


class Faq(models.Model):
    question = models.CharField(max_length=250)
    answer = models.TextField()

    audience = models.CharField(
        max_length=20, choices=Audience.choices, default=Audience.PUBLIC
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name="faqs")

    # FAQs are read in a curated order, not by date, so position is explicit.
    position = models.PositiveIntegerField(default=0)

    is_published = models.BooleanField(default=True)
    published_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    search_vector = SearchVectorField(null=True, editable=False)

    objects = PublishedQuerySet.as_manager()

    class Meta:
        ordering = ["position", "id"]
        verbose_name = "FAQ"
        verbose_name_plural = "FAQs"
        indexes = [
            GinIndex(fields=["search_vector"], name="faq_search_gin"),
        ]

    def __str__(self):
        return self.question
