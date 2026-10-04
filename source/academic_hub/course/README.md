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
  newlines, commas or semicolons. At most 500 unique IDs per request. All IDs are
  validated before any membership is saved; duplicate/existing entries are skipped.
- Removing a member immediately revokes course access, but does not delete their
  account. They may join again if they still have the code. Removal is not a ban.
- Deleting a class removes its enrollments, not user accounts.
- Assignment/recent-work content, co-teacher roles and account creation are outside
  this scope. The Figma sketches guide the teal cards, dialogs and members table.

## JSON endpoints

### CSV roster import

Owners (Lecturer or Department) can upload a CSV under Manage members → Import
students from CSV. Download the header-only template and fill one ID per row.
The file must be UTF-8 (BOM supported), at most 1 MB and 500 nonblank data rows,
with exactly one column headed `student_id` or `nisit_id`. IDs must be 10 digits;
keep them as text in spreadsheet software to preserve leading zeros.
Every ID must belong to an existing active student account. Invalid files add
nobody. Existing memberships and duplicate rows are skipped and reported.
Uploaded files are parsed in memory and are not saved by this feature.

The owner-only `POST /course/api/<id>/members/import-csv/` endpoint accepts a
multipart form upload named `csv_file` with the usual session and CSRF token.
It returns `added`, `already_enrolled`, and `duplicates_skipped` counts.

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
