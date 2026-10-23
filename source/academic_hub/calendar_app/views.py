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

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from .events import add_notes, events_by_day, find_event, next_deadline, popover_data
from .forms import DayNoteForm
from .models import DayNote

# Sunday first, matching the way Thai university calendars are printed.
FIRST_WEEKDAY = calendar.SUNDAY

MIN_YEAR, MAX_YEAR = 1900, 2999

# Every month is drawn with the same number of week rows, so the grid keeps
# one height as you page through months. Six is the most any month needs.
WEEK_ROWS = 6


def _requested_month(request, today):
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


def _shift(year, month, delta):
    """The (year, month) `delta` months away."""
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def build_weeks(year, month, today, events=None):
    """WEEK_ROWS weeks of day dicts, padded with days from the next month.

    `events` maps a date to its list of deadlines (see events.events_by_day).
    """
    events = events or {}
    cal = calendar.Calendar(firstweekday=FIRST_WEEKDAY)
    dates = cal.monthdatescalendar(year, month)
    while len(dates) < WEEK_ROWS:
        last = dates[-1][-1]
        dates.append([last + timedelta(days=i) for i in range(1, 8)])

    weeks = []
    for week in dates:
        weeks.append([
            {
                "date": day,
                "in_month": day.month == month,
                "is_today": day == today,
                "is_weekend": day.weekday() >= 5,
                "label": f"{day:%A}, {day:%B} {day.day}",
                "events": events.get(day, []),
            }
            for day in week
        ])
    return weeks


def _step_after(request):
    """Redirect to the deadline after ?after=<id>.

    At the last deadline, stay on it (its page then shows the button greyed
    out). An id this user cannot see is treated as "from now".
    """
    base = reverse("calendar:month")
    start = find_event(request.user, request.GET.get("after"))
    target = next_deadline(request.user, after=start) or start
    return redirect(f"{base}?focus={target['id']}" if target else base)


@login_required(login_url="accounts:login")
def month_view(request):
    if "after" in request.GET:
        return _step_after(request)

    today = timezone.localdate()
    year, month = _requested_month(request, today)

    # A focused deadline decides which month is shown. An id this user may
    # not see (or that no longer exists) is ignored, never an error.
    focus = find_event(request.user, request.GET.get("focus"))
    if focus:
        year, month = focus["date"].year, focus["date"].month
    upcoming = next_deadline(request.user, after=focus)
    prev_year, prev_month = _shift(year, month, -1)
    next_year, next_month = _shift(year, month, 1)

    cal = calendar.Calendar(firstweekday=FIRST_WEEKDAY)
    weekday_names = [calendar.day_abbr[d] for d in cal.iterweekdays()]

    grid = build_weeks(year, month, today)
    first_day, last_day = grid[0][0]["date"], grid[-1][-1]["date"]
    days = events_by_day(request.user, first_day, last_day)
    add_notes(days, request.user, first_day, last_day)

    return render(request, "calendar_app/calendar.html", {
        "month_start": date(year, month, 1),
        "weeks": build_weeks(year, month, today, days),
        "popover_events": popover_data(days),
        "weekday_names": weekday_names,
        "prev": {"year": prev_year, "month": prev_month},
        "next": {"year": next_year, "month": next_month},
        "is_current_month": (year, month) == (today.year, today.month) and not focus,
        "today": today,
        "focus_id": focus["id"] if focus else "",
        "next_deadline": upcoming,
    })


# --- day notes -------------------------------------------------------------

def _month_of(day):
    return f"{reverse('calendar:month')}?year={day.year}&month={day.month}"


@login_required(login_url="accounts:login")
@require_POST
def note_save(request, pk=None):
    """Create a note, or edit one of your own (pk). Always returns to the
    month the note is on."""
    note = get_object_or_404(DayNote, pk=pk, user=request.user) if pk else DayNote(user=request.user)
    form = DayNoteForm(request.POST, instance=note)
    if form.is_valid():
        note = form.save()
        # No "Saved" banner: the note appearing in its day is the confirmation.
        return redirect(_month_of(note.date))

    problems = " ".join(err for errs in form.errors.values() for err in errs)
    messages.error(request, f"The note was not saved. {problems}")
    day = parse_date(request.POST.get("date", "") or "") or (note.date if pk else None)
    return redirect(_month_of(day) if day else reverse("calendar:month"))


@login_required(login_url="accounts:login")
@require_POST
def note_delete(request, pk):
    note = get_object_or_404(DayNote, pk=pk, user=request.user)
    day = note.date
    note.delete()
    return redirect(_month_of(day))
