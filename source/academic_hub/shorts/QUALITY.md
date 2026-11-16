# Shorts — code quality

How the Shorts app (`source/academic_hub/shorts/`) is checked, where it
stands, and what is still owed. Every tool reads its settings from
`shorts/pyproject.toml`, so the rules apply to this app only.

## Running the checks

From `source/academic_hub/`, with PostgreSQL up (`docker compose up db`)
and `DB_HOST=localhost`:

```bash
pip install -r requirements.txt -r shorts/requirements-dev.txt

black --config shorts/pyproject.toml shorts            # format
isort --settings-path shorts/pyproject.toml shorts     # sort imports
pylint --rcfile shorts/pyproject.toml shorts           # lint
mypy --config-file shorts/pyproject.toml shorts        # types (strict)
radon cc -s -a -e "shorts/migrations/*" shorts         # complexity
radon mi -s -e "shorts/migrations/*" shorts            # maintainability
bandit -q -r shorts -x shorts/tests,shorts/migrations  # security
coverage run --rcfile shorts/pyproject.toml manage.py test shorts
coverage report --rcfile shorts/pyproject.toml         # fails under 95%
git log --stat -- shorts                               # churn
```

CI runs the same steps on every pull request that touches `shorts/`
(`.github/workflows/code-quality.yml`, the `shorts` job).

## Results (2026-11-14)

| Check | Before | After |
|---|---|---|
| PEP 8 / black, 79 columns | 14 files to reformat, 211 lines too long | clean, 0 lines too long |
| Type hints, `mypy --strict` | 269 errors | 0 errors in 34 files (tests included) |
| pylint | 8.42 / 10 | 10.00 / 10 |
| radon cc, worst function | C (14) `create_comment`, C (12) `load_seed` | B (9) `save_short` |
| radon cc, average (source) | A (3.42) | A (2.72) |
| radon mi, lowest file | B (10.3) `tests.py` | A (43.9) `tests/test_edge_cases.py` |
| Tests | 84 | 131 (112 after the refactor, 19 for feed ranking) |
| Coverage | 90 % lines | 100 % lines and branches |
| bandit | not run | 0 issues |

## What the refactor changed

Behaviour is unchanged: all 84 original tests passed against the new code
before any test was added or moved.

- **One save path (DRY).** `services.save_short()` is the only code that
  uploads, replaces or cleans up a clip. Create Short, Edit Short and the
  seed loader used to carry three copies of that logic.
- **Smaller functions.** `create_comment` was split into `_comment_body`
  and `_reply_parent`; `load_seed` into `_read_manifest`, `_clip_path`,
  `_already_seeded`, `_author` and `_create_from_entry`; Range parsing
  moved out of `ranged_clip_response` into `parse_range`; `clip_duration`
  into one function per rule.
- **One error path for the JSON API.** `ApiError` and the `json_api`
  decorator replace the repeated `return error(...)` checks; the two
  comment lists share `_cursor` and `_page` for paging.
- **Shared access rules.** `access.py` holds the sign-in decorator (was
  repeated on 7 views), the signed-in account, and who may post and
  manage shorts.
- **Tests split by feature.** `tests.py` (938 lines, 11 test classes) became
  the `tests/` package; the three fixture mixins became one typed
  `ShortsTestCase`. New: `test_bucket.py` (the bucket command had 0 %
  coverage) and `test_edge_cases.py` (error paths).

## Bugs found and fixed

| # | Bug | Fix |
|---|---|---|
| 1 | If saving a short failed **before** its clip was uploaded, the clean-up deleted the object named after the uploader's file (e.g. `lesson.mp4`) instead of nothing. | `save_short` deletes only uploaded objects (names under `clips/`). Test: `test_failure_before_upload_deletes_nothing`. |
| 2 | `ShortForm.clean_title` had a "Give the short a title." error that could never show: `CharField` already trims and rejects blank titles. | Dead code removed with `clean_description`, which only repeated the trimming. |

## Security basics

- **Input is validated** before anything is stored: clip extension, size,
  MP4 header and length; title, description and tag lengths; comment
  length; `liked`, `reply_to`, `before`, `after` and `offset` types.
- **SQL is only built by the Django ORM** — no raw SQL, `extra()` or
  cursors (bandit: 0 issues).
- **Passwords** are never handled here. Accounts hashes them with
  Django's `set_password` (PBKDF2-SHA256).
- **Access:** every page and API call needs a signed-in user; other
  people's shorts answer 404, other people's comments 403; every POST
  needs the CSRF token (tested).
- **Storage:** object names are random (`clips/<uuid>.mp4`), the bucket is
  private, and clips play only through 1-hour signed links.
- **Output:** feed data reaches the page through `json_script`, and
  `reels.js` sets text with `textContent`, never `innerHTML`.

## Feed ranking (`recommend.py`)

Clips that fit the viewer come up earlier; nothing is hidden.

- **Course score.** The words of the viewer's live courses (name and
  topic titles; students' enrolled, TAs' assisted, lecturers' own) give
  +2 per tag they name and +1 per tag a related subject in `SUBJECTS`
  brings in (ISP -> #python, #code; Calculus -> #geometry). At most 3.
  The Department oversees every course, so only its likes count.
- **Like score.** Each tag adds how many of the viewer's liked shorts
  carry it. A liked short is not boosted by its own like. At most 3.
- **Programme score.** +2 when the short is tagged with the viewer's
  programme: #ske or #cpe (from their account's department), or the
  programme's full name, e.g. #software-knowledge-engineering.
- **Order.** Weight = 1 + 2 x (course + like + programme), so 1 to 17.
  Each round is a seeded weighted draw without replacement: every short
  appears once, heavier ones tend to come first. The seed travels with
  each batch (`?round=`), so paging never repeats or skips; offset 0
  starts a new round. When no short fits the viewer at all, the feed
  stays newest first.
- **Checked:** with one weight-5 short among nine weight-1 shorts, it
  opened 34% of 2,000 rounds (5 / 14 ≈ 36% expected). In a browser, 2 #python
  shorts among 20 averaged 1.5 of the first 8 places for an ISP student
  (0.8 for an even shuffle).

## Code churn (`git log --stat`, before this refactor)

9 commits from 2026-11-05 to 11-07; 4,355 lines added and 117 deleted,
not counting the seed videos.

| File | Commits | Added | Deleted |
|---|---|---|---|
| `tests.py` | 8 | 942 | 4 |
| `views.py` | 6 | 276 | 16 |
| `templates/shorts/feed.html` | 5 | 185 | 16 |
| `static/shorts/reels.js` | 4 | 713 | 9 |
| `static/shorts/reels.css` | 4 | 340 | 22 |
| `interactions.py` | 2 | 232 | 8 |

The files that changed most (`tests.py`, `views.py`) are the ones this
refactor split up.

## Technical debt

| # | Debt | Why it matters | Next step |
|---|---|---|---|
| 1 | Ranking reads course names and topics through `SUBJECTS`, a list in code. | A new kind of course needs a line added there to rank well. | Move it to an admin table if courses change often. |
| 2 | Thai course names are one word (no spaces), so they only match a tag spelled the same. | Thai-named courses get little course boost. | Add Thai words to `SUBJECTS`, or let lecturers tag courses. |
| 3 | Every feed request scores every live short in Python. | Fine for hundreds or a few thousand shorts. | Cache each round's order if the feed slows. |
| 4 | `Short.archived_at` is never set. | SRS-9 archiving isn't built. | Add archive and restore to Edit Short. |
| 5 | The 100 MB limit is checked after Django has received the whole upload. | A huge upload still uses disk and time. | Set a request-size limit in the web server for production. |
| 6 | Like and comment counts are counted on every feed load. | Fine now; slower with many likes. | Cache the counts on `Short` if it gets slow. |
| 7 | The seed puts videos in git (35 MB, 6 clips today). | Every clone downloads them. | Keep the seed small, or move it to Git LFS. |
| 8 | `reels.js` (709 lines) and the CSS have no linter or automated browser tests. | These tools check Python only. | Add ESLint and Playwright tests to CI. |
| 9 | MinIO runs from the community image `pgsty/silo`. | The official image is no longer published. | Re-check before deploying. |

Track new bugs and debt as GitHub issues labelled `shorts` plus `bug` or
`tech-debt`, and link the fixing PR.

## Review and merging

- Keep pull requests small: one change, merged the same day or the next.
- At least one teammate reviews each PR before merging.
- In the repository settings, make the **Code quality / shorts** check required on
  `main`, so a PR can't merge while it fails.

Two tests in **other** apps already fail on `main` and are not caused by
Shorts: `announcements.tests.ForumQuestionTests.test_forum_filters_search_audience_category_dates_and_comment_sort`
and `course.tests.test_courses.CourseTests.test_duplicate_create_edit_and_normalization`.
