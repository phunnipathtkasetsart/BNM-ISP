"""Deadlines shown on the calendar.

Two sources, each filtered by the same rules the rest of the site uses, so
the calendar never shows something its reader could not open elsewhere:

    course        CoursePost.deadline, in course.permissions.visible_courses()
    announcement  Announcement.deadline, from Announcement.objects.visible_to()

Course deadlines are drawn as coloured bars (one colour per class, like
Google Classroom). Announcement deadlines are drawn as a dot, time and title.

The reader's own day notes (models.DayNote) are listed in the same cells,
first in each day. They are not deadlines, so "Next deadline" skips them.
"""

from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping
from datetime import date, datetime, time
from typing import Any, NotRequired, TypedDict, cast
from urllib.parse import urlencode

from django.db.models import QuerySet
from django.urls import reverse
from django.utils import timezone

from accounts.models import User as Account
from announcements.models import Announcement
from course.models import CoursePost
from course.permissions import visible_courses

from .models import DayNote

# White text stays readable on every one of these.
COURSE_COLOURS = [
    "#C2620F",  # orange
    "#2B6CB0",  # blue
    "#2C7A7B",  # teal
    "#6B46C1",  # purple
    "#B83280",  # pink
    "#2F855A",  # green
    "#9C4221",  # brown
    "#4A5568",  # slate
]
ANNOUNCEMENT_COLOUR = "#2C5449"  # --ku-green-800

# Your notes, then bars, then dot items.
KIND_ORDER = {"note": -1, "course": 0, "announcement": 1}
NOTE_COLOUR = "#B7791F"  # amber: personal, distinct from every class colour
NOTIFY_COLOUR = "#C53030"  # soft red: a note you asked to be reminded of


class Event(TypedDict):
    """One item in a day cell: a deadline or one of your notes."""

    id: str  # "post-12", "ann-5" or "note-3"
    kind: str  # a key of KIND_ORDER
    title: str
    body: str
    date: date
    deadline: datetime | None  # None for notes
    time_label: str
    date_label: str
    colour: str
    source: str
    url: str
    link_label: str
    # Drawn in a grey tone of its colour once the due time has gone by.
    passed: bool
    pk: NotRequired[int]  # notes only: for the edit and delete forms
    notify: NotRequired[bool]  # notes only


Days = dict[date, list[Event]]


def course_colour(course_id: int) -> str:
    """The same colour for a class everywhere it appears."""
    return COURSE_COLOURS[course_id % len(COURSE_COLOURS)]


def day_label(day: date) -> str:
    """ "Tuesday, October 27", as the calendar writes a day."""
    return f"{day:%A}, {day:%B} {day.day}"


def time_label(moment: datetime) -> str:
    """ "11:59pm", as Google writes it.

    Built by hand because the no-padding strftime flag differs between
    Linux and Windows.
    """
    suffix = "am" if moment.hour < 12 else "pm"
    return f"{moment.hour % 12 or 12}:{moment:%M}{suffix}"


def _bounds(first_day: date, last_day: date) -> tuple[datetime, datetime]:
    """Aware datetimes covering first_day 00:00 to the end of last_day."""
    start = timezone.make_aware(datetime.combine(first_day, time.min))
    end = timezone.make_aware(datetime.combine(last_day, time.max))
    return start, end


# pylint: disable-next=too-many-arguments
def _event(
    *,
    uid: str,
    kind: str,
    title: str,
    body: str,
    deadline: datetime,
    colour: str,
    source: str,
    url: str,
    link_label: str,
) -> Event:
    local = timezone.localtime(deadline)
    return {
        "passed": deadline < timezone.now(),
        "id": uid,
        "kind": kind,
        "title": title,
        "body": body,
        "date": local.date(),
        "deadline": local,
        "time_label": time_label(local),
        "date_label": day_label(local.date()),
        "colour": colour,
        "source": source,
        "url": url,
        "link_label": link_label,
    }


def _window(start: datetime, end: datetime | None) -> dict[str, datetime]:
    """Deadline filter for start..end; end=None means no upper limit."""
    window = {"deadline__gte": start}
    if end is not None:
        window["deadline__lte"] = end
    return window


def _visible_posts(user: Account) -> "QuerySet[CoursePost]":
    """Live posts in live classes the user may open."""
    return CoursePost.objects.filter(
        archived_at__isnull=True,
        course__archived_at__isnull=True,
        course__in=visible_courses(user),
    ).select_related("course")


def _visible_announcements(user: Account) -> "QuerySet[Announcement]":
    """Published announcements the user may read."""
    # visible_to() hands the Department its drafts too; a calendar only
    # shows what has actually been published.
    rows: QuerySet[Announcement] = Announcement.objects.visible_to(user)
    return rows.filter(is_published=True, published_at__lte=timezone.now())


def _post_event(post: CoursePost) -> Event:
    course = post.course
    section = f" · Sec {course.section}" if course.section else ""
    return _event(
        uid=f"post-{post.pk}",
        kind="course",
        title=post.title,
        body=post.body,
        # Only posts with a deadline are ever drawn (see _window and
        # _first_event), so it is never None here.
        deadline=cast(datetime, post.deadline),
        colour=course_colour(course.pk),
        source=course.name + section,
        url=reverse("course:detail", args=[course.pk]) + f"#post-{post.pk}",
        link_label="Open in class",
    )


def _announcement_event(item: Announcement) -> Event:
    board = reverse("announcements:public_board")
    return _event(
        uid=f"ann-{item.pk}",
        kind="announcement",
        title=item.title,
        body=item.body,
        deadline=cast(datetime, item.deadline),  # never None: see above
        colour=ANNOUNCEMENT_COLOUR,
        source=f"Announcement · {item.author_label}",
        url=f"{board}?{urlencode({'q': item.title})}",
        link_label="Open on board",
    )


def course_events(
    user: Account, start: datetime, end: datetime | None
) -> Iterator[Event]:
    """Deadlines of the user's classes between start and end."""
    for post in _visible_posts(user).filter(**_window(start, end)):
        yield _post_event(post)


def announcement_events(
    user: Account, start: datetime, end: datetime | None
) -> Iterator[Event]:
    """Announcement deadlines the user may see, between start and end."""
    for item in _visible_announcements(user).filter(**_window(start, end)):
        yield _announcement_event(item)


def _all_events(
    user: Account, start: datetime, end: datetime | None
) -> Iterator[Event]:
    yield from course_events(user, start, end)
    yield from announcement_events(user, start, end)


def sort_key(event: Event) -> tuple[Any, ...]:
    """One total order for deadlines: by time, then bars before dots, then
    title, then id, so two deadlines at the same minute still take turns."""
    return (
        event["deadline"],
        KIND_ORDER[event["kind"]],
        event["title"].lower(),
        event["id"],
    )


def _within_day(event: Event) -> tuple[Any, ...]:
    """Order inside one day cell: bars before dots, then by time."""
    return (
        KIND_ORDER[event["kind"]],
        event["deadline"],
        event["title"].lower(),
    )


def events_by_day(user: Account, first_day: date, last_day: date) -> Days:
    """{date: [event, ...]} for every deadline between the days, inclusive."""
    start, end = _bounds(first_day, last_day)
    days: Days = defaultdict(list)
    for event in _all_events(user, start, end):
        days[event["date"]].append(event)
    for items in days.values():
        items.sort(key=_within_day)
    return days


# --- "Next deadline" button ------------------------------------------------


def _first_event(
    rows: "QuerySet[Any]", pk: int, draw: Callable[[Any], Event]
) -> Event | None:
    row = rows.filter(pk=pk, deadline__isnull=False).first()
    return draw(row) if row is not None else None


def _find_post(user: Account, pk: int) -> Event | None:
    return _first_event(_visible_posts(user), pk, _post_event)


def _find_announcement(user: Account, pk: int) -> Event | None:
    return _first_event(_visible_announcements(user), pk, _announcement_event)


# The id prefix of each kind of deadline, and how to find one.
_FINDERS: Mapping[str, Callable[[Account, int], Event | None]] = {
    "post": _find_post,
    "ann": _find_announcement,
}


def find_event(user: Account, uid: str | None) -> Event | None:
    """The deadline with this id ("post-12" / "ann-5") if the user may
    see it, else None."""
    kind, _, pk = (uid or "").partition("-")
    finder = _FINDERS.get(kind)
    if finder is None or not pk.isdigit():
        return None
    return finder(user, int(pk))


def _looking_from(after: Event | None) -> datetime:
    """Where the search starts: the given deadline's time, or now."""
    if after is None or after["deadline"] is None:
        return timezone.now()
    return after["deadline"]


def next_deadline(user: Account, after: Event | None = None) -> Event | None:
    """The deadline straight after `after` in time or, without one, the
    closest deadline from now. None when nothing is left.

    Stepping from a given deadline is purely chronological, so from a
    deadline that has passed you reach the next one, even if that has
    passed too.
    """
    later = sorted(_all_events(user, _looking_from(after), None), key=sort_key)
    if after is not None:
        later = [e for e in later if sort_key(e) > sort_key(after)]
    return later[0] if later else None


# --- personal notes ------------------------------------------------------


def _note_event(note: DayNote) -> Event:
    return {
        "id": f"note-{note.pk}",
        "pk": note.pk,
        "kind": "note",
        "title": note.title,
        "body": note.description,
        "date": note.date,
        "deadline": None,
        "time_label": "",
        "date_label": day_label(note.date),
        "colour": NOTIFY_COLOUR if note.notify else NOTE_COLOUR,
        "source": "Your note",
        "notify": note.notify,
        "passed": False,  # notes are not deadlines; they keep their colour
        "url": "",
        "link_label": "",
    }


def add_notes(
    days: Days, user: Account, first_day: date, last_day: date
) -> Days:
    """Put the user's notes for first_day..last_day at the top of each day."""
    notes = DayNote.objects.filter(
        user=user, date__gte=first_day, date__lte=last_day
    )
    by_day: Days = defaultdict(list)
    for note in notes:
        by_day[note.date].append(_note_event(note))
    for day, items in by_day.items():
        days[day] = items + days.get(day, [])
    return days


def popover_data(days: Days) -> list[dict[str, Any]]:
    """The fields the browser needs to draw the detail popovers, JSON-safe."""
    return [
        {
            "id": e["id"],
            "kind": e["kind"],
            "title": e["title"],
            "body": e["body"],
            "date": e["date"].isoformat(),
            "date_label": e["date_label"],
            "time_label": e["time_label"],
            "colour": e["colour"],
            "source": e["source"],
            "url": e["url"],
            "link_label": e["link_label"],
            "notify": e.get("notify", False),
            "passed": e["passed"],
            "pk": e.get("pk"),
        }
        for items in days.values()
        for e in items
    ]
