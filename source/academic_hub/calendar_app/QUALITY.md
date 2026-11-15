# Calendar — code quality

How the calendar app (`source/academic_hub/calendar_app/`) is checked,
where it stands, and what is still owed. Every tool reads its settings
from `calendar_app/pyproject.toml`, so the rules apply to this app only.
They are the same rules and tool versions as the Shorts app.

## Running the checks

From `source/academic_hub/`, with PostgreSQL up (`docker compose up db`)
and `DB_HOST=localhost`:

```bash
pip install -r requirements.txt -r calendar_app/requirements-dev.txt

black --config calendar_app/pyproject.toml calendar_app
isort --settings-path calendar_app/pyproject.toml calendar_app
pylint --rcfile calendar_app/pyproject.toml calendar_app
mypy --config-file calendar_app/pyproject.toml calendar_app
radon cc -s -a -e "calendar_app/migrations/*" calendar_app
radon mi -s -e "calendar_app/migrations/*" calendar_app
bandit -q -r calendar_app -x calendar_app/tests.py,calendar_app/migrations
coverage run --rcfile calendar_app/pyproject.toml manage.py test calendar_app
coverage report --rcfile calendar_app/pyproject.toml   # fails under 95%
git log --stat -- calendar_app
```

CI runs the same steps on every pull request that touches the calendar
(`.github/workflows/code-quality.yml`, the `calendar_app` job).

## Results (2026-11-14)

| Check | Before | After |
|---|---|---|
| PEP 8 / black, 79 columns | 4 files to reformat, 80 lines too long | clean, 0 lines too long |
| isort | 1 file out of order | clean |
| Type hints, `mypy --strict` | 266 errors | 0 errors in 9 files (tests included) |
| pylint | 7.77 / 10 | 10.00 / 10 |
| radon cc, worst function | B (9) `note_save` | A (5): every function is A |
| radon cc, average (source) | A (2.63) | A (2.07) |
| radon mi, lowest file | A (28.4) `tests.py` | A (24.5) `tests.py` |
| Tests | 42 | 45 |
| Coverage | 99 % | 100 % lines and branches |
| bandit | 0 issues | 0 issues |

`tests.py` scores a little lower than before because it gained type
hints, three tests and two helpers; it is still grade A.

## What the refactor changed

Behaviour is unchanged: the 42 original tests passed against the new code
before any test was added, and the month page, the next month, today's
month, a focused deadline and the "next deadline" redirect rendered
the same HTML before and after (compared character by character, apart
from the random CSRF token).

- **The month grid is built once per page**, not twice (it was built
  empty to find the first and last day, then again with the events).
  `month_dates()` now gives the dates and `build_weeks()` fills them.
- **One shared `CALENDAR`** instead of a `calendar.Calendar` per use.
- **One `day_label()`** for "Tuesday, October 27", written out in three
  places before (two in `events.py`, one in `views.py`), and one
  `time_label()` for "11:59pm".
- **Smaller functions.** `note_save` (B 9) became `_note_to_save`,
  `_refused` and `_posted_date`; `month_view` hands the month choice to
  `_shown_month` and the day contents to `_month_days`; `find_event`
  picks its source from a table (`_FINDERS`) instead of an if-chain.
- **A typed `Event`** (`TypedDict`) describes the dictionaries every day
  cell, popover and the "Next deadline" button use.
- **Tests:** one `make_user()` replaces seven copies of the same
  `create_user(...)` call, and the lambda in `NextDeadlineTests` became a
  function.

## Bugs found and fixed

| # | Bug | Fix |
|---|---|---|
| 1 | Saving a note dated an impossible day (e.g. `2026-02-30`) answered **500 Server Error**: Django's `parse_date` raises for well-formed impossible dates, in the code that picks the month to return to. | `_posted_date()` treats it as no date; the page shows the form's "Enter a valid date." Test: `test_impossible_date_is_refused_not_a_server_error`. |
| 2 | `DayNoteForm.clean_title` had a "Give the note a title." error that could never show, and `clean_description` only trimmed what the field already trims. | Both removed; Django's own "This field is required." covers a blank title. |

## Security basics

- **Input is validated:** `?year=` and `?month=` fall back to this month
  when bad or out of range; `?focus=` and `?after=` ids are only used
  if the reader may see that deadline; notes go through `DayNoteForm`.
- **SQL is only built by the Django ORM** (bandit: 0 issues).
- **Passwords** are never handled here (accounts hashes them with
  Django's `set_password`, PBKDF2-SHA256).
- **Access:** every page needs a signed-in user; guests are kept out by
  `GuestAccessMiddleware`; someone else's note answers 404; note changes
  are POST-only and need the CSRF token (tested).
- **Visibility:** the calendar uses the same rules as the rest of the
  site (`visible_courses()`, `Announcement.objects.visible_to()`), so it
  never shows a deadline its reader could not open elsewhere.

## Code churn (`git log --stat`, before this refactor)

7 commits from 2026-10-23 to 11-05 by 3 people; 1,751 lines added and 7
deleted.

| File | Commits | Added | Deleted |
|---|---|---|---|
| `events.py` | 3 | 255 | 2 |
| `static/calendar_app/calendar.css` | 3 | 246 | 1 |
| `templates/calendar_app/calendar.html` | 3 | 134 | 2 |
| `tests.py` | 2 | 404 | 0 |
| `static/calendar_app/calendar.js` | 2 | 395 | 2 |
| `views.py` | 1 | 180 | 0 |

## Technical debt

| # | Debt | Why it matters | Next step |
|---|---|---|---|
| 1 | A note's `notify` flag is stored but nothing sends reminders. | Users may expect a reminder. | Add a daily job that notifies through the notifications app. |
| 2 | "Next deadline" loads every future deadline and sorts them in Python. | Fine for a term's deadlines; slower with years of data. | Query only the next few by time if it gets slow. |
| 3 | `calendar.js` (393 lines) and the CSS have no linter or browser tests. | These tools check Python only. | Add ESLint and Playwright tests to CI. |
| 4 | `tests.py` is one 642-line file, the lowest maintainability score in the app (still A). | Harder to find a test as it grows. | Split it into a `tests/` package by feature, as Shorts does. |
| 5 | The week starts on Sunday for everyone. | Some people expect Monday. | Make it a user setting if asked. |

Track new bugs and debt as GitHub issues labelled `calendar` plus `bug`
or `tech-debt`, and link the fixing PR.

## Review and merging

- Keep pull requests small: one change, merged the same day or the next.
- At least one teammate reviews each PR before merging.
- In the repository settings, make the **Code quality / calendar_app**
  check required on `main`, so a PR can't merge while it fails.
