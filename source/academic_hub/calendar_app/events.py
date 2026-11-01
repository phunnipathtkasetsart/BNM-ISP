"""Deadlines shown on the calendar.

Two sources, each filtered by the same rules the rest of the site uses, so
the calendar never shows something its reader could not open elsewhere:

    course     CoursePost.deadline, for classes in course.permissions.visible_courses()
    announcement  Announcement.deadline, through Announcement.objects.visible_to()

Course deadlines are drawn as coloured bars (one colour per class, like
Google Classroom). Announcement deadlines are drawn as a dot, time and title.

The reader's own day notes (models.DayNote) are listed in the same cells,
first in each day. They are not deadlines, so "Next deadline" skips them.
"""

from collections import defaultdict
from datetime import datetime, time
from urllib.parse import urlencode

from django.urls import reverse
from django.utils import timezone

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
NOTE_COLOUR = "#B7791F"    # amber: personal, distinct from every class colour
NOTIFY_COLOUR = "#C53030"  # soft-red chip: a note you asked to be notified about


def course_colour(course_id):
    return COURSE_COLOURS[course_id % len(COURSE_COLOURS)]


def _bounds(first_day, last_day):
    """Aware datetimes covering first_day 00:00 to the end of last_day."""
    start = timezone.make_aware(datetime.combine(first_day, time.min))
    end = timezone.make_aware(datetime.combine(last_day, time.max))
    return start, end


def _event(*, uid, kind, title, body, deadline, colour, source, url, link_label):
    local = timezone.localtime(deadline)
    return {
        # Drawn in a grey tone of its colour once the due time has gone by.
        "passed": deadline < timezone.now(),
        "id": uid,
        "kind": kind,
        "title": title,
        "body": body,
        "date": local.date(),
        "deadline": local,
        # "11:59pm", as Google writes it. Built by hand because the
        # no-padding strftime flag differs between Linux and Windows.
        "time_label": f"{local.hour % 12 or 12}:{local:%M}{'am' if local.hour < 12 else 'pm'}",
        "date_label": f"{local:%A}, {local:%B} {local.day}",
        "colour": colour,
        "source": source,
        "url": url,
        "link_label": link_label,
    }


def _window(start, end):
    """Deadline filter for start..end; end=None means no upper limit."""
    window = {"deadline__gte": start}
    if end is not None:
        window["deadline__lte"] = end
    return window


def _visible_posts(user):
    return CoursePost.objects.filter(course__in=visible_courses(user)).select_related("course")


def _visible_announcements(user):
    # visible_to() hands the Department its drafts too; a calendar only
    # shows what has actually been published.
    return Announcement.objects.visible_to(user).filter(
        is_published=True, published_at__lte=timezone.now())


def _post_event(post):
    course = post.course
    source = course.name + (f" · Sec {course.section}" if course.section else "")
    return _event(
        uid=f"post-{post.pk}",
        kind="course",
        title=post.title,
        body=post.body,
        deadline=post.deadline,
        colour=course_colour(course.pk),
        source=source,
        url=reverse("course:detail", args=[course.pk]) + f"#post-{post.pk}",
        link_label="Open in class",
    )


def _announcement_event(item):
    board = reverse("announcements:public_board")
    return _event(
        uid=f"ann-{item.pk}",
        kind="announcement",
        title=item.title,
        body=item.body,
        deadline=item.deadline,
        colour=ANNOUNCEMENT_COLOUR,
        source=f"Announcement · {item.author_label}",
        url=f"{board}?{urlencode({'q': item.title})}",
        link_label="Open on board",
    )


def course_events(user, start, end):
    posts = _visible_posts(user).filter(**_window(start, end))
    for post in posts:
        yield _post_event(post)


def announcement_events(user, start, end):
    for item in _visible_announcements(user).filter(**_window(start, end)):
        yield _announcement_event(item)


def sort_key(event):
    """One total order for deadlines: by time, then bars before dots, then
    title, then id, so two deadlines at the same minute still take turns."""
    return (event["deadline"], KIND_ORDER[event["kind"]], event["title"].lower(), event["id"])


def events_by_day(user, first_day, last_day):
    """{date: [event, ...]} for every deadline between the two days, inclusive."""
    start, end = _bounds(first_day, last_day)
    days = defaultdict(list)
    for source in (course_events, announcement_events):
        for event in source(user, start, end):
            days[event["date"]].append(event)
    for items in days.values():
        items.sort(key=lambda e: (KIND_ORDER[e["kind"]], e["deadline"], e["title"].lower()))
    return days


# --- "Next deadline" button ------------------------------------------------

def find_event(user, uid):
    """The deadline with this id ("post-12" / "ann-5") if this user may see it."""
    kind, _, pk = (uid or "").partition("-")
    if not pk.isdigit():
        return None
    if kind == "post":
        post = _visible_posts(user).filter(pk=pk, deadline__isnull=False).first()
        return _post_event(post) if post else None
    if kind == "ann":
        item = _visible_announcements(user).filter(pk=pk, deadline__isnull=False).first()
        return _announcement_event(item) if item else None
    return None


def next_deadline(user, after=None):
    """The deadline straight after `after` (an event dict) in time, or,
    without one, the closest deadline from now. None when nothing is left.

    Stepping from a given deadline is purely chronological, so from a
    deadline that has passed you reach the next one, even if that has
    passed too.
    """
    start = after["deadline"] if after is not None else timezone.now()
    later = sorted(
        list(course_events(user, start, None)) + list(announcement_events(user, start, None)),
        key=sort_key,
    )
    if after is not None:
        later = [e for e in later if sort_key(e) > sort_key(after)]
    return later[0] if later else None


# --- personal notes -----------------------------------------------------

def _note_event(note):
    return {
        "id": f"note-{note.pk}",
        "pk": note.pk,
        "kind": "note",
        "title": note.title,
        "body": note.description,
        "date": note.date,
        "deadline": None,
        "time_label": "",
        "date_label": f"{note.date:%A}, {note.date:%B} {note.date.day}",
        "colour": NOTIFY_COLOUR if note.notify else NOTE_COLOUR,
        "source": "Your note",
        "notify": note.notify,
        "passed": False,  # notes are not deadlines; they keep their colour
        "url": "",
        "link_label": "",
    }


def add_notes(days, user, first_day, last_day):
    """Put the user's notes for first_day..last_day at the top of each day."""
    notes = DayNote.objects.filter(user=user, date__gte=first_day, date__lte=last_day)
    by_day = defaultdict(list)
    for note in notes:
        by_day[note.date].append(_note_event(note))
    for day, items in by_day.items():
        days[day] = items + days.get(day, [])
    return days


def popover_data(days):
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
            "passed": e.get("passed", False),
            "pk": e.get("pk"),
        }
        for items in days.values()
        for e in items
    ]
