from datetime import date, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.middleware import GUEST_SESSION_KEY
from announcements.models import Announcement, Audience
from course.models import CoursePost, Enrollment
from course.services import create_course

from .events import events_by_day, find_event, next_deadline
from .models import DayNote
from .views import _shift, build_weeks


class MonthMathTests(SimpleTestCase):
    def test_shift_crosses_year_boundaries(self):
        self.assertEqual(_shift(2026, 12, 1), (2027, 1))
        self.assertEqual(_shift(2026, 1, -1), (2025, 12))
        self.assertEqual(_shift(2026, 6, 0), (2026, 6))

    def test_every_month_has_six_rows(self):
        # Feb 2026 starts on a Sunday and fits in 4 weeks; Aug 2026 needs 6.
        for year, month in ((2026, 2), (2026, 10), (2026, 8)):
            self.assertEqual(len(build_weeks(year, month, date(2026, 10, 22))), 6)

    def test_padding_weeks_continue_into_next_month(self):
        weeks = build_weeks(2026, 2, date(2026, 10, 22))
        days = [d["date"] for w in weeks for d in w]
        self.assertEqual(days[0], date(2026, 2, 1))
        self.assertEqual(days[-1], date(2026, 3, 14))
        self.assertEqual(len(set(days)), 42)

    def test_weeks_are_full_and_start_on_sunday(self):
        weeks = build_weeks(2026, 10, date(2026, 10, 22))
        self.assertTrue(all(len(week) == 7 for week in weeks))
        self.assertEqual(weeks[0][0]["date"].weekday(), 6)  # Sunday

    def test_only_today_is_marked(self):
        weeks = build_weeks(2026, 10, date(2026, 10, 22))
        marked = [d["date"] for w in weeks for d in w if d["is_today"]]
        self.assertEqual(marked, [date(2026, 10, 22)])

    def test_padding_days_are_out_of_month(self):
        weeks = build_weeks(2026, 10, date(2026, 10, 22))
        first = weeks[0][0]
        self.assertEqual(first["date"], date(2026, 9, 27))
        self.assertFalse(first["in_month"])


class CalendarViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.student = get_user_model().objects.create_user(
            nisit_id="6610540099", email="cal@ku.th", password="x",
            first_name="Cal", last_name="Test", department="ske")

    def test_anonymous_is_sent_to_login(self):
        response = self.client.get(reverse("calendar:month"))
        self.assertRedirects(response, reverse("accounts:login") + "?next=/calendar/",
                             fetch_redirect_response=False)

    def test_guest_is_sent_to_board(self):
        self.client.force_login(self.student)
        session = self.client.session
        session[GUEST_SESSION_KEY] = True
        session.save()
        response = self.client.get(reverse("calendar:month"))
        self.assertRedirects(response, reverse("announcements:public_board"),
                             fetch_redirect_response=False)

    @patch("calendar_app.views.timezone.localdate", return_value=date(2026, 10, 22))
    def test_defaults_to_current_month(self, _):
        self.client.force_login(self.student)
        response = self.client.get(reverse("calendar:month"))
        self.assertContains(response, "October 2026")
        self.assertContains(response, 'aria-current="date"')
        self.assertContains(response, "?year=2026&amp;month=9")
        self.assertContains(response, "?year=2026&amp;month=11")

    def test_requested_month(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("calendar:month"), {"year": 2027, "month": 1})
        self.assertContains(response, "January 2027")

    @patch("calendar_app.views.timezone.localdate", return_value=date(2026, 10, 22))
    def test_bad_params_fall_back_to_current_month(self, _):
        self.client.force_login(self.student)
        for params in ({"year": "abc"}, {"month": 13}, {"year": 99999, "month": 1}):
            response = self.client.get(reverse("calendar:month"), params)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "October 2026")

    def test_tab_is_active_on_calendar_page(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("calendar:month"))
        self.assertContains(
            response,
            'class="subnav__tab subnav__tab--on" href="/calendar/" aria-current="page">Calendar',
        )


class DeadlineEventTests(TestCase):
    """Who sees which deadline, and where it lands on the grid."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.teacher = User.objects.create_user(
            nisit_id="6610540201", email="teach@ku.th", password="x",
            first_name="Tea", last_name="Cher", department="ske", is_staff=True)
        cls.student = User.objects.create_user(
            nisit_id="6610540202", email="stud@ku.th", password="x",
            first_name="Stu", last_name="Dent", department="ske")
        cls.mine = create_course(owner=cls.teacher, name="Databases", section="001")
        cls.other = create_course(owner=cls.teacher, name="Networks", section="001")
        Enrollment.objects.create(course=cls.mine, student=cls.student)

        due = timezone.make_aware(datetime(2026, 10, 27, 23, 59))
        cls.post = CoursePost.objects.create(
            course=cls.mine, author=cls.teacher, title="Week8: HW",
            body="Use SQL transactions.", deadline=due)
        CoursePost.objects.create(
            course=cls.other, author=cls.teacher, title="Not my class", deadline=due)
        CoursePost.objects.create(
            course=cls.mine, author=cls.teacher, title="Next year", deadline=due.replace(year=2027))
        CoursePost.objects.create(course=cls.mine, author=cls.teacher, title="No deadline")

        cls.public = Announcement.objects.create(
            title="Add/drop closes", body="Form needed after.",
            audience=Audience.PUBLIC, deadline=due.replace(hour=12))
        Announcement.objects.create(
            title="Staff only", body="x", audience=Audience.STAFF, deadline=due)
        Announcement.objects.create(
            title="Draft", body="x", audience=Audience.PUBLIC, deadline=due,
            is_published=False)

    def titles(self, user):
        days = events_by_day(user, date(2026, 9, 27), date(2026, 11, 7))
        return [e["title"] for e in days.get(date(2026, 10, 27), [])]

    def test_student_sees_enrolled_course_and_public_items_only(self):
        self.assertEqual(self.titles(self.student), ["Week8: HW", "Add/drop closes"])

    def test_owner_sees_all_their_classes_and_staff_items(self):
        self.assertEqual(self.titles(self.teacher),
                         ["Not my class", "Week8: HW", "Add/drop closes", "Staff only"])

    def test_course_bars_come_before_dot_items(self):
        kinds = [e["kind"] for e in events_by_day(
            self.student, date(2026, 10, 1), date(2026, 10, 31))[date(2026, 10, 27)]]
        self.assertEqual(kinds, ["course", "announcement"])

    def test_event_fields(self):
        event = events_by_day(self.student, date(2026, 10, 1), date(2026, 10, 31))[date(2026, 10, 27)][0]
        self.assertEqual(event["time_label"], "11:59pm")
        self.assertEqual(event["date_label"], "Tuesday, October 27")
        self.assertEqual(event["source"], "Databases · Sec 001")
        self.assertEqual(event["url"], f"/course/{self.mine.pk}/#post-{self.post.pk}")

    def test_page_renders_chips_more_button_and_popover_data(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("calendar:month"), {"year": 2026, "month": 10})
        self.assertContains(response, 'data-event="post-%d"' % self.post.pk)
        self.assertContains(response, 'data-event="ann-%d"' % self.public.pk)
        self.assertContains(response, "data-more hidden")
        self.assertContains(response, 'id="cal-events"')
        self.assertContains(response, "Use SQL transactions.")
        self.assertNotContains(response, "Not my class")
        self.assertNotContains(response, "Staff only")


class NextDeadlineTests(TestCase):
    """The "Next deadline" button steps through upcoming deadlines in order."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.teacher = User.objects.create_user(
            nisit_id="6610540301", email="nt@ku.th", password="x",
            first_name="Next", last_name="Teacher", department="ske", is_staff=True)
        cls.student = User.objects.create_user(
            nisit_id="6610540302", email="ns@ku.th", password="x",
            first_name="Next", last_name="Student", department="ske")
        course = create_course(owner=cls.teacher, name="Algorithms", section="001")
        hidden = create_course(owner=cls.teacher, name="Not enrolled", section="001")
        Enrollment.objects.create(course=course, student=cls.student)

        now = timezone.now().replace(second=0, microsecond=0)
        day = timedelta(days=1)
        post = lambda title, when, c=course: CoursePost.objects.create(
            course=c, author=cls.teacher, title=title, deadline=when)
        cls.past = post("Already due", now - day)
        cls.first = post("A first", now + day)
        cls.tie = post("B same minute", now + day)          # same time as first
        cls.other_class = post("Other class", now + day, hidden)
        cls.ann = Announcement.objects.create(
            title="Form due", body="", audience=Audience.PUBLIC, deadline=now + 2 * day)
        cls.far = post("Far away", now + 45 * day)          # a later month

    def ids_in_order(self):
        seen, event = [], next_deadline(self.student)
        while event:
            seen.append(event["id"])
            event = next_deadline(self.student, after=event)
        return seen

    def test_steps_through_every_upcoming_deadline_once(self):
        self.assertEqual(self.ids_in_order(), [
            f"post-{self.first.pk}", f"post-{self.tie.pk}",
            f"ann-{self.ann.pk}", f"post-{self.far.pk}",
        ])

    def test_steps_chronologically_from_a_past_deadline(self):
        older = CoursePost.objects.create(
            course=self.first.course, author=self.teacher, title="Long ago",
            deadline=self.past.deadline - timedelta(days=3))
        older_event = find_event(self.student, f"post-{older.pk}")
        # From an old deadline, the next one in time - even though it has passed too.
        self.assertEqual(next_deadline(self.student, after=older_event)["id"], f"post-{self.past.pk}")
        past_event = find_event(self.student, f"post-{self.past.pk}")
        self.assertEqual(next_deadline(self.student, after=past_event)["id"], f"post-{self.first.pk}")

    def test_find_event_respects_visibility(self):
        self.assertIsNone(find_event(self.student, f"post-{self.other_class.pk}"))
        self.assertIsNone(find_event(self.student, "post-abc"))
        self.assertIsNone(find_event(self.student, "nonsense"))
        self.assertIsNotNone(find_event(self.teacher, f"post-{self.other_class.pk}"))

    def test_button_links_to_closest_deadline(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("calendar:month"))
        self.assertContains(response, f'href="?focus=post-{self.first.pk}"')

    def test_focus_opens_that_month_and_links_onward(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("calendar:month"), {"focus": f"ann-{self.ann.pk}"})
        far_month = timezone.localtime(self.far.deadline)
        self.assertContains(response, f'data-focus="ann-{self.ann.pk}"')
        self.assertContains(response, "cal__ev--focus")
        self.assertContains(response, f'href="?focus=post-{self.far.pk}"')

        response = self.client.get(reverse("calendar:month"), {"focus": f"post-{self.far.pk}"})
        self.assertContains(response, f"{far_month:%B} {far_month.year}")
        self.assertContains(response, "This is the last deadline")

    def test_hidden_focus_is_ignored(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("calendar:month"),
                                   {"focus": f"post-{self.other_class.pk}"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-focus=""')
        self.assertNotContains(response, "Other class")

    def test_passed_deadlines_are_flagged(self):
        self.assertTrue(find_event(self.student, f"post-{self.past.pk}")["passed"])
        self.assertFalse(find_event(self.student, f"post-{self.first.pk}")["passed"])

    def test_passed_deadline_is_drawn_grey(self):
        self.client.force_login(self.student)
        page = self.client.get(reverse("calendar:month"), {"focus": f"post-{self.past.pk}"})
        self.assertContains(page, "cal__ev--passed", count=1)
        self.assertContains(page, "Already due (Algorithms · Sec 001) · passed")

    def test_after_redirects_to_the_following_deadline(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("calendar:month"), {"after": f"post-{self.first.pk}"})
        self.assertRedirects(response, f"/calendar/?focus=post-{self.tie.pk}",
                             fetch_redirect_response=False)

    def test_after_the_last_deadline_stays_on_it(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("calendar:month"), {"after": f"post-{self.far.pk}"})
        self.assertRedirects(response, f"/calendar/?focus=post-{self.far.pk}",
                             fetch_redirect_response=False)

    def test_after_a_hidden_deadline_counts_from_now(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("calendar:month"),
                                   {"after": f"post-{self.other_class.pk}"})
        self.assertRedirects(response, f"/calendar/?focus=post-{self.first.pk}",
                             fetch_redirect_response=False)

    def test_greyed_button_is_still_a_link_the_script_can_re_aim(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("calendar:month"), {"focus": f"post-{self.far.pk}"})
        self.assertContains(response, 'class="cal__btn cal__btn--next is-disabled" data-next')


class DayNoteTests(TestCase):
    """Personal notes: only the author sees, edits or deletes them."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.me = User.objects.create_user(
            nisit_id="6610540401", email="me@ku.th", password="x",
            first_name="Me", last_name="Self", department="ske")
        cls.other = User.objects.create_user(
            nisit_id="6610540402", email="other@ku.th", password="x",
            first_name="Some", last_name="One", department="ske")

    def setUp(self):
        self.client.force_login(self.me)

    def create(self, **data):
        payload = {"date": "2026-10-27", "title": "Study group", "description": "Room 204"}
        payload.update(data)
        return self.client.post(reverse("calendar:note_create"), payload)

    def test_create_saves_and_returns_to_that_month(self):
        response = self.create(notify="on")
        self.assertRedirects(response, "/calendar/?year=2026&month=10", fetch_redirect_response=False)
        note = DayNote.objects.get()
        self.assertEqual((note.user, note.date, note.title, note.description, note.notify),
                         (self.me, date(2026, 10, 27), "Study group", "Room 204", True))

    def test_notify_note_is_red_without_a_bell(self):
        self.create(notify="on")
        self.create(date="2026-10-28", title="Plain note")
        page = self.client.get(reverse("calendar:month"), {"year": 2026, "month": 10})
        self.assertContains(page, "cal__ev--note cal__ev--notify", count=1)
        self.assertNotContains(page, "cal__bell")
        self.assertNotContains(page, "&#128276;")

    def test_notes_on_past_days_keep_their_colour(self):
        DayNote.objects.create(user=self.me, date=date(2020, 1, 15), title="Old note")
        page = self.client.get(reverse("calendar:month"), {"year": 2020, "month": 1})
        self.assertContains(page, "Old note")
        self.assertNotContains(page, "cal__ev--passed")

    def test_notify_defaults_to_off(self):
        self.create()
        self.assertFalse(DayNote.objects.get().notify)

    def test_blank_title_is_rejected(self):
        response = self.create(title="   ")
        self.assertEqual(DayNote.objects.count(), 0)
        page = self.client.get(response.url)
        self.assertContains(page, "The note was not saved.")

    def test_note_shows_on_its_day_for_its_author_only(self):
        self.create(notify="on")
        page = self.client.get(reverse("calendar:month"), {"year": 2026, "month": 10})
        note = DayNote.objects.get()
        self.assertContains(page, f'data-event="note-{note.pk}"')
        self.assertContains(page, "cal__ev--note")
        self.assertContains(page, "Room 204")

        self.client.force_login(self.other)
        page = self.client.get(reverse("calendar:month"), {"year": 2026, "month": 10})
        self.assertNotContains(page, "Study group")

    def test_edit_own_note(self):
        self.create()
        note = DayNote.objects.get()
        self.client.post(reverse("calendar:note_edit", args=[note.pk]),
                         {"date": "2026-10-27", "title": "Renamed", "description": "", "notify": "on"})
        note.refresh_from_db()
        self.assertEqual((note.title, note.description, note.notify), ("Renamed", "", True))

    def test_cannot_edit_or_delete_someone_elses_note(self):
        theirs = DayNote.objects.create(user=self.other, date=date(2026, 10, 27), title="Theirs")
        edit = self.client.post(reverse("calendar:note_edit", args=[theirs.pk]),
                                {"date": "2026-10-27", "title": "Hacked"})
        delete = self.client.post(reverse("calendar:note_delete", args=[theirs.pk]))
        self.assertEqual((edit.status_code, delete.status_code), (404, 404))
        theirs.refresh_from_db()
        self.assertEqual(theirs.title, "Theirs")

    def test_delete_own_note(self):
        self.create()
        note = DayNote.objects.get()
        response = self.client.post(reverse("calendar:note_delete", args=[note.pk]))
        self.assertRedirects(response, "/calendar/?year=2026&month=10", fetch_redirect_response=False)
        self.assertFalse(DayNote.objects.exists())

    def test_saving_and_deleting_show_no_banner(self):
        page = self.client.get(self.create().url)
        self.assertNotContains(page, "cal__notice")
        note = DayNote.objects.get()
        page = self.client.get(self.client.post(reverse("calendar:note_delete", args=[note.pk])).url)
        self.assertNotContains(page, "cal__notice")

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(reverse("calendar:note_create")).status_code, 405)

    def test_anonymous_and_guest_cannot_save(self):
        self.client.logout()
        self.create()
        self.client.force_login(self.me)
        session = self.client.session
        session[GUEST_SESSION_KEY] = True
        session.save()
        self.create()
        self.assertFalse(DayNote.objects.exists())

    def test_notes_are_not_deadlines(self):
        DayNote.objects.create(user=self.me, date=timezone.localdate() + timedelta(days=1), title="Mine")
        self.assertIsNone(next_deadline(self.me))
        self.assertIsNone(find_event(self.me, "note-1"))
