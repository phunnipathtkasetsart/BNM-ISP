"""Month calendar with deadlines, laid out like Google Calendar.

Deadlines come from calendar_app/events.py. Each day cell lists them; when a
cell is too short for all of them, static/calendar_app/calendar.js hides the
ones that do not fit and shows "+N more", which opens the full list.

"Next deadline": ?focus=<id> (e.g. post-12) opens the month holding that
deadline, highlights it and opens its details. The button on that page links
to the deadline after it, so each press steps one deadline forward. With no
focus, the button goes to the closest deadline from now.

When the reader opens a deadline's details by clicking it, calendar.js points
the button at ?after=<that id>; the view finds the deadline after it (in any
month) and redirects to ?focus=<that one>.

Day notes: clicking empty space in a day opens a form (built by calendar.js
from the <template>s in calendar.html) that posts to note_save. Notes are
private to their author; see models.DayNote.

Guests never reach this view: GuestAccessMiddleware only lets them onto the
public board and FAQ, so it is not listed in GUEST_ALLOWED_VIEWS.
"""

import calendar
from datetime import date, timedelta
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from accounts.models import User as Account

from .events import (
    Days,
    Event,
    add_notes,
    day_label,
    events_by_day,
    find_event,
    next_deadline,
    popover_data,
)
from .forms import DayNoteForm
from .models import DayNote

# Sunday first, matching the way Thai university calendars are printed.
CALENDAR = calendar.Calendar(firstweekday=calendar.SUNDAY)

MIN_YEAR, MAX_YEAR = 1900, 2999

# Every month is drawn with the same number of week rows, so the grid keeps
# one height as you page through months. Six is the most any month needs.
WEEK_ROWS = 6

signed_in_page = login_required(login_url="accounts:login")

YearMonth = tuple[int, int]
Week = list[dict[str, Any]]


def _account(request: HttpRequest) -> Account:
    """The signed-in account; signed_in_page has already checked."""
    user = request.user
    if not isinstance(user, Account):
        raise PermissionDenied
    return user


def _requested_month(request: HttpRequest, today: date) -> YearMonth:
    """Year and month from ?year=&month=, falling back to the current month.

    Bad or out-of-range values are ignored rather than raising, so a mangled
    link still shows a calendar instead of an error page.
    """
    try:
        year = int(request.GET.get("year", today.year))
        month = int(request.GET.get("month", today.month))
    except (TypeError, ValueError):
        return today.year, today.month
    if not (MIN_YEAR <= year <= MAX_YEAR and 1 <= month <= 12):
        return today.year, today.month
    return year, month


def _shift(year: int, month: int, delta: int) -> YearMonth:
    """The (year, month) `delta` months away."""
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def month_dates(year: int, month: int) -> list[list[date]]:
    """WEEK_ROWS weeks of dates, padded with days from the next month."""
    dates = CALENDAR.monthdatescalendar(year, month)
    while len(dates) < WEEK_ROWS:
        last = dates[-1][-1]
        dates.append([last + timedelta(days=i) for i in range(1, 8)])
    return dates


def build_weeks(
    year: int, month: int, today: date, events: Days | None = None
) -> list[Week]:
    """The month grid: one dict per day, with that day's events.

    `events` maps a date to its list of deadlines (see events.events_by_day).
    """
    events = events or {}
    return [
        [
            {
                "date": day,
                "in_month": day.month == month,
                "is_today": day == today,
                "is_weekend": day.weekday() >= 5,
                "label": day_label(day),
                "events": events.get(day, []),
            }
            for day in week
        ]
        for week in month_dates(year, month)
    ]


def _step_after(request: HttpRequest) -> HttpResponse:
    """Redirect to the deadline after ?after=<id>.

    At the last deadline, stay on it (its page then shows the button greyed
    out). An id this user cannot see is treated as "from now".
    """
    user = _account(request)
    base = reverse("calendar:month")
    start = find_event(user, request.GET.get("after"))
    target = next_deadline(user, after=start) or start
    return redirect(f"{base}?focus={target['id']}" if target else base)


def _shown_month(
    request: HttpRequest, user: Account, today: date
) -> tuple[YearMonth, Event | None]:
    """The month to draw, and the deadline in focus if there is one.

    A focused deadline decides the month. An id this user may not see (or
    that no longer exists) is ignored, never an error.
    """
    focus = find_event(user, request.GET.get("focus"))
    if focus is not None:
        return (focus["date"].year, focus["date"].month), focus
    return _requested_month(request, today), None


def _month_days(user: Account, year: int, month: int) -> Days:
    """Deadlines and the user's notes for every day on the month's grid."""
    dates = month_dates(year, month)
    first_day, last_day = dates[0][0], dates[-1][-1]
    days = events_by_day(user, first_day, last_day)
    return add_notes(days, user, first_day, last_day)


@signed_in_page
def month_view(request: HttpRequest) -> HttpResponse:
    """One month of deadlines and notes, or a step to the next deadline."""
    if "after" in request.GET:
        return _step_after(request)
    user = _account(request)
    today = timezone.localdate()
    (year, month), focus = _shown_month(request, user, today)
    days = _month_days(user, year, month)
    prev_year, prev_month = _shift(year, month, -1)
    next_year, next_month = _shift(year, month, 1)
    is_this_month = (year, month) == (today.year, today.month)
    return render(
        request,
        "calendar_app/calendar.html",
        {
            "month_start": date(year, month, 1),
            "weeks": build_weeks(year, month, today, days),
            "popover_events": popover_data(days),
            "weekday_names": [
                calendar.day_abbr[d] for d in CALENDAR.iterweekdays()
            ],
            "prev": {"year": prev_year, "month": prev_month},
            "next": {"year": next_year, "month": next_month},
            "is_current_month": is_this_month and not focus,
            "today": today,
            "focus_id": focus["id"] if focus else "",
            "next_deadline": next_deadline(user, after=focus),
        },
    )


# --- day notes -------------------------------------------------------------


def _month_of(day: date | None) -> str:
    """The calendar page for the month holding `day` (this month if None)."""
    base = reverse("calendar:month")
    return f"{base}?year={day.year}&month={day.month}" if day else base


def _note_to_save(request: HttpRequest, pk: int | None) -> DayNote:
    """The user's own note `pk`, or a new one (404 for anyone else's)."""
    user = _account(request)
    if pk is None:
        return DayNote(user=user)
    return get_object_or_404(DayNote, pk=pk, user=user)


def _posted_date(request: HttpRequest) -> date | None:
    """The posted date, or None if it is missing or not a real date.

    parse_date raises for well-formed impossible dates ("2026-02-30"), and
    the form has already said what is wrong, so that is not an error here.
    """
    try:
        return parse_date(request.POST.get("date") or "")
    except ValueError:
        return None


def _refused(request: HttpRequest, form: DayNoteForm, note: DayNote) -> str:
    """Report why the note was not saved; the month to go back to."""
    problems = " ".join(
        str(err) for errs in form.errors.values() for err in errs
    )
    messages.error(request, f"The note was not saved. {problems}")
    day = _posted_date(request)
    return _month_of(day or (note.date if note.pk else None))


@signed_in_page
@require_POST
def note_save(request: HttpRequest, pk: int | None = None) -> HttpResponse:
    """Create a note, or edit one of your own (pk). Always returns to the
    month the note is on."""
    note = _note_to_save(request, pk)
    form = DayNoteForm(request.POST, instance=note)
    if not form.is_valid():
        return redirect(_refused(request, form, note))
    note = form.save()
    # No "Saved" banner: the note appearing in its day is the confirmation.
    return redirect(_month_of(note.date))


@signed_in_page
@require_POST
def note_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Delete one of your own notes and return to its month."""
    note = get_object_or_404(DayNote, pk=pk, user=_account(request))
    day = note.date
    note.delete()
    return redirect(_month_of(day))
