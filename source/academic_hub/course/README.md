# Course / class management

The app uses the existing KU accounts and session authentication. Open `/course/`
or choose Course from Home / FAQ. Lecturers (`User.is_staff`) and Department users
(`User.is_superuser`) can create classes and manage their own classes, including
codes and rosters. Neither role can manage another owner's class. Students can
join and read the roster of their classes. Guests and unrelated users cannot
access course data.

## Setup

The project registers `course` in settings and includes `course.urls`.
Run `python manage.py migrate` against the project's PostgreSQL database, then
start the project normally. With Docker: `docker compose exec web python manage.py migrate`.
Run regression tests with `python manage.py test course` (or the same command
inside the web container). Tests require a PostgreSQL test database; the existing
accounts migrations create the legacy Users schema.

## Behavior

Section (Sec) is required when creating or editing a class, up to 20 characters.
It is stored as text to preserve leading zeros (e.g. `001`) and appears on class
cards, class detail pages and API responses. Existing classes keep an empty
section until an owner edits them; no section is guessed during migration.

- Create, rename and delete classes; class codes contain six uppercase letters/digits.
- Unique database constraints protect codes and prevent duplicate memberships.
- Joining with a lowercase code is accepted. Joining twice is harmless.
- The owner can copy the code and add/remove students. Codes are hidden from students.
- Bulk import accepts existing, active students' 10-digit IDs, separated by spaces,
  newlines, commas or semicolons. At most 500 entries per request. Valid students are added even if other IDs
  are invalid; rejected entries and reasons are reported. Duplicate/existing entries are skipped.
- Removing a member immediately revokes course access, but does not delete their
  account. They may join again if they still have the code. Removal is not a ban.
- Deleting a class removes its enrollments, not user accounts.
- Course Q&A rendering, groups, notifications, and account creation are outside
  this backend API task. The Figma sketches guide the separate Q&A UI iteration.

## JSON endpoints

### CSV roster import

Owners (Lecturer or Department) can upload a CSV under Manage members → Import
students from CSV. Download the header-only template and fill one ID per row.
The file must be UTF-8 (BOM supported), at most 1 MB and 500 nonblank data rows,
with exactly one column headed `student_id` or `nisit_id`. IDs must be 10 digits;
keep them as text in spreadsheet software to preserve leading zeros.
Valid IDs for active student accounts are imported; invalid IDs are rejected
individually. Unreadable CSV, wrong headers, encoding errors and oversized files
are rejected before any students are added. Existing memberships and duplicate rows are skipped and reported.
Uploaded files are parsed in memory and are not saved by this feature.

The owner-only `POST /course/api/<id>/members/import-csv/` endpoint accepts a
multipart form upload named `csv_file` with the usual session and CSRF token.
Both import endpoints return `added`, `already_enrolled`, `duplicates_skipped`,
and a `rejected` list with `row`, `student_id`, and `reason`. A partial success
returns 200; no valid student rows returns 400. Database failures roll back the
valid batch, so a retry is safe.

### Course Q&A

Open a course and use the **FAQ** card in the right sidebar. Search results
appear beneath the search field; selecting a question opens its answer thread.
The **Ask question** button opens the question form. Enrolled students can post
questions; course owners, TAs, and Department can reply and use their permitted
moderation actions. Verified answers appear first and carry a verified badge.
Questions with verified answers are surfaced after manually pinned questions
with a pinned verified badge.

Each course has its own Q&A. Enrolled students and the course owner can read
active questions and answers. Course TAs can read and perform official answer,
pin, and verify actions. Department users can manage Q&A in any course using the
existing Department override; this API does not add access to other course
features. Guests continue to be redirected to the public board and receive no
Q&A data. Users outside the course receive 404.

Students can post questions, edit their own questions until the first active
answer exists, and archive their own questions at any time. The course owner and
course TAs can answer, pin/unpin, verify/unverify, archive any question or
answer, and restore only items they archived. They may edit their own answers;
they cannot edit another user's question or answer. Department can edit,
archive, and restore any Q&A item. Only Department may restore a
student-archived question.

Archived questions and answers are visible to course owners, course TAs, and
Department; students cannot see them. Course owners and TAs can list archived
questions only in their course with `status=archived`, while Department can do
so in any course. Other roles receive 400 for that filter. A mutation on an
archived question, answer, or thread returns 404 until the archived item (and
any archived parent question) is restored. Course owners and TAs can restore
only items they archived; Department may restore any item. There is no hard
delete.

| Method | Path | Body / purpose |
|---|---|---|
| GET | `/course/api/<id>/questions/` | Optional `page`, `page_size`, `status=answered\|unanswered\|mine`, `q` |
| GET | `/course/api/<id>/questions/?status=archived` | Course owner, TA, or Department archived question list |
| POST | `/course/api/<id>/questions/` | `{"title":"...", "body":"..."}`; enrolled student |
| GET | `/course/api/<id>/questions/<question_id>/` | Question detail and visible answers |
| POST | `/course/api/<id>/questions/<question_id>/edit/` | `{"title":"...", "body":"..."}` |
| POST | `/course/api/<id>/questions/<question_id>/archive/` | Soft archive a question |
| POST | `/course/api/<id>/questions/<question_id>/restore/` | Restore permitted archived question |
| POST | `/course/api/<id>/questions/<question_id>/pin/` | `{"is_pinned":true}` or `false` |
| POST | `/course/api/<id>/questions/<question_id>/answers/` | `{"body":"..."}`; owner Lecturer, TA, or Department |
| POST | `/course/api/<id>/questions/<question_id>/answers/<answer_id>/edit/` | `{"body":"..."}` |
| POST | `/course/api/<id>/questions/<question_id>/answers/<answer_id>/archive/` | Soft archive an answer |
| POST | `/course/api/<id>/questions/<question_id>/answers/<answer_id>/restore/` | Restore permitted archived answer |
| POST | `/course/api/<id>/questions/<question_id>/answers/<answer_id>/verify/` | `{"is_verified":true}` or `false` |

Question titles are trimmed, required, and limited to 200 characters. Question
and answer bodies are trimmed, required, and limited to 5,000 characters.
Validation errors return 400. JSON moderation toggles require booleans; form
submissions use `"true"` or `"false"`. Every mutation is POST-only and retains
normal session and CSRF protection. Creates return 201; successful other
mutations return 200; unsupported methods return 405.

The list response is `{"count":n,"page":n,"page_size":n,"results":[...]}`.
Each item includes `id`, `title`, plain-text `body_excerpt` (up to 240
characters), `author_name`, `author_role_label`, `created_at`, `updated_at`,
`edited_at`, `is_pinned`, active `answer_count`, `has_verified_answer`,
`is_mine`, `can_edit`, `can_archive`, `can_answer`, `can_pin`, and `can_verify`.
Detail returns the full plain-text question body plus answers with author name
and role label, timestamps, `is_verified`, `is_mine`, `can_edit`,
`can_archive`, and `can_verify`. Question detail also contains the six question
capability fields above. Timestamps are timezone-aware ISO-8601 values; dates
are not formatted for Bangkok in the API.

Pagination defaults to page 1 and 20 items; `page_size` must be 1–100. Invalid
pagination or status values return 400. Search `q` is case-insensitive over
question title and body. Questions sort manually pinned first, then questions
with verified answers, then newest-first. Answers sort verified-first, then
chronologically. Multiple verified answers are allowed. `edited_at` is set only
when question title/body or answer body changes;
pin, verify, archive, and restore do not set it.

All paths are under `/course/api/`, use existing login sessions, and retain Django
CSRF protection. Send the CSRF token in `X-CSRFToken` for JSON mutations. POST
bodies may be JSON objects with string values or normal form submissions.

| Method | Path | Body / purpose |
|---|---|---|
| GET | `/` | List own/enrolled classes |
| POST | `create/` | `{"name":"Software Engineering","section":"001"}` |
| POST | `join/` | `{"code":"ABC234"}` |
| GET | `<id>/` | Class detail (code only for owner) |
| POST | `<id>/edit/` | `{"name":"New name","section":"002"}` |
| POST | `<id>/delete/` | Delete class |
| GET | `<id>/members/` | Lecturer and student names/IDs |
| POST | `<id>/members/add/` | `{"student_ids":"6610540001,6610540002"}` |
| POST | `<id>/members/<student_id>/remove/` | Remove enrollment |

Validation errors return 400; anonymous API requests return 401; prohibited roles
return 403; inaccessible classes return 404. The project's guest middleware sends
guest sessions to the public board. Created classes/new joins return 201. Repeated
joins return 200. Bulk additions return `added` and `already_enrolled` counts.

## Availability and duplicate classes

Browser visits to deleted or inaccessible classes return to My classes with a
neutral explanatory message. API requests still return 404 without revealing
class details.

An owner cannot create or rename a class to an existing name/section combination,
ignoring case and surrounding spaces. Different owners and different sections
are allowed. The database enforces this rule, including concurrent requests.
Migration 0003 checks for existing duplicates and stops without changing any
classes if duplicates need to be resolved first.
