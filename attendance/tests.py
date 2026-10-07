from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.staticfiles import finders
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import pk_holidays, services
from .models import (
    WEEKDAY_FIELDS,
    Correction,
    Employee,
    Holiday,
    LeaveAllowance,
    LeaveRequest,
    MissingTimeRequest,
    RequestStatus,
    Schedule,
    ScheduleAssignment,
    ScheduleVersion,
    TimeEntry,
)

ZONE = ZoneInfo("Asia/Karachi")
HOUR = timedelta(hours=1)

# The week used throughout: Monday 5 to Sunday 11 October 2026.
MONDAY = date(2026, 10, 5)
LATER = datetime(2026, 10, 20, 12, 0, tzinfo=ZONE)


def at(day, hour=0, minute=0):
    """A moment in October 2026, company time."""
    return datetime(2026, 10, day, hour, minute, tzinfo=ZONE)


@override_settings(
    TIME_ZONE="Asia/Karachi",
    DAY_ROLLOVER_HOUR=0,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],  # fast, tests only
)
class AttendanceTestCase(TestCase):
    def setUp(self):
        Holiday.objects.all().delete()  # the seeded Pakistan holidays would skew targets
        self.schedule = self.make_schedule("BD", 35)
        self.employee = Employee.objects.create_user(
            "sara", password="correct-horse-1", start_date=date(2026, 1, 1)
        )
        services.assign_schedule(self.employee, self.schedule, services.BEGINNING)
        self.admin = Employee.objects.create_user(
            "boss", password="correct-horse-2", is_staff=True, start_date=date(2026, 1, 1)
        )

    def make_schedule(self, name, hours, days=WEEKDAY_FIELDS[:5]):
        schedule = Schedule.objects.create(name=name)
        self.set_terms(schedule, hours, days=days)
        return schedule

    def set_terms(self, schedule, hours, days=WEEKDAY_FIELDS[:5], start=services.BEGINNING):
        weekdays = {day: day in days for day in WEEKDAY_FIELDS}
        services.set_schedule_terms(schedule, Decimal(str(hours)), weekdays, start)

    def work(self, day, hours=7, start_hour=9, employee=None):
        start = at(day, start_hour)
        return TimeEntry.objects.create(
            employee=employee or self.employee, clock_in=start, clock_out=start + hours * HOUR
        )

    def week(self, now=LATER, employee=None, monday=MONDAY):
        # Loaded fresh, the way a page request would, so nothing is remembered between calls.
        employee = Employee.objects.get(pk=(employee or self.employee).pk)
        return services.build_weeks(employee, monday, monday, now=now)[0]

    def leave_taken(self, year=2026):
        employee = Employee.objects.get(pk=self.employee.pk)
        return {b.label: b.taken for b in services.leave_balances(employee, year) if b.taken}

    def leave(self, first, last, status=RequestStatus.APPROVED, leave_type="sick"):
        return LeaveRequest.objects.create(
            employee=self.employee,
            leave_type=leave_type,
            start_date=date(2026, 10, first),
            end_date=date(2026, 10, last),
            reason="Unwell",
            status=status,
        )


class PunchTests(AttendanceTestCase):
    def test_clock_in_then_out_records_server_time(self):
        entry, changed = services.punch(self.employee, "in", now=at(5, 9))
        self.assertTrue(changed)
        self.assertTrue(entry.is_open)

        entry, changed = services.punch(self.employee, "out", now=at(5, 17, 30))
        self.assertTrue(changed)
        self.assertEqual(entry.clock_in, at(5, 9))
        self.assertEqual(entry.worked(), timedelta(hours=8, minutes=30))
        self.assertEqual(entry.source, TimeEntry.Source.PUNCH)

    def test_double_tap_on_clock_in_does_not_clock_out(self):
        services.punch(self.employee, "in", now=at(5, 9))
        entry, changed = services.punch(self.employee, "in", now=at(5, 9, 1))
        self.assertFalse(changed)
        self.assertTrue(entry.is_open)
        self.assertEqual(TimeEntry.objects.count(), 1)

    def test_double_tap_on_clock_out_does_not_clock_in(self):
        services.punch(self.employee, "in", now=at(5, 9))
        services.punch(self.employee, "out", now=at(5, 17))
        entry, changed = services.punch(self.employee, "out", now=at(5, 17, 1))
        self.assertFalse(changed)
        self.assertIsNone(entry)
        self.assertEqual(TimeEntry.objects.count(), 1)

    def test_database_refuses_a_second_open_entry(self):
        TimeEntry.objects.create(employee=self.employee, clock_in=at(5, 9))
        with self.assertRaises(IntegrityError), transaction.atomic():
            TimeEntry.objects.create(employee=self.employee, clock_in=at(5, 10))

    def test_several_sessions_in_a_day_add_up(self):
        self.work(5, hours=4, start_hour=9)
        self.work(5, hours=3, start_hour=14)
        monday = self.week().days[0]
        self.assertEqual(monday.worked, 7 * HOUR)
        self.assertEqual(len(monday.entries), 2)

    def test_forgotten_clock_out_is_flagged_next_day_and_counts_zero(self):
        services.punch(self.employee, "in", now=at(5, 9))
        self.assertEqual(services.close_stale_entries(now=at(5, 23, 59)), 0)
        self.assertEqual(services.close_stale_entries(now=at(6, 0, 1)), 1)

        entry = TimeEntry.objects.get()
        self.assertTrue(entry.missing_out)
        self.assertIsNone(entry.clock_out)
        self.assertEqual(entry.worked(now=at(6, 8)), timedelta(0))

        monday = self.week(now=at(6, 8)).days[0]
        self.assertEqual(monday.status, "missing")
        self.assertEqual(monday.worked, timedelta(0))

    def test_button_offers_clock_in_the_morning_after_a_forgotten_clock_out(self):
        services.punch(self.employee, "in", now=at(5, 9))
        entry, changed = services.punch(self.employee, "in", now=at(6, 9))
        self.assertTrue(changed)
        self.assertEqual(entry.clock_in, at(6, 9))
        self.assertEqual(TimeEntry.objects.filter(missing_out=True).count(), 1)

    @override_settings(DAY_ROLLOVER_HOUR=6)
    def test_rollover_hour_keeps_late_work_on_the_day_it_started(self):
        services.punch(self.employee, "in", now=at(5, 22))
        self.assertEqual(services.close_stale_entries(now=at(6, 2)), 0)
        entry, _ = services.punch(self.employee, "out", now=at(6, 2))
        self.assertEqual(services.work_date(entry.clock_in), MONDAY)
        self.assertEqual(self.week().days[0].worked, 4 * HOUR)

        services.punch(self.employee, "in", now=at(6, 22))
        self.assertEqual(services.close_stale_entries(now=at(7, 6, 30)), 1)


class WeeklyTargetTests(AttendanceTestCase):
    def test_completed_week(self):
        for day in range(5, 10):
            self.work(day)
        week = self.week()
        self.assertEqual(week.target, 35 * HOUR)
        self.assertEqual(week.worked, 35 * HOUR)
        self.assertEqual(week.state, "met")
        self.assertEqual(week.percent, 100)

    def test_short_week(self):
        for day in range(5, 9):
            self.work(day)
        week = self.week()
        self.assertEqual(week.state, "short")
        self.assertEqual(week.remaining, 7 * HOUR)
        self.assertEqual(week.difference, -7 * HOUR)

    def test_week_still_running_is_in_progress_not_short(self):
        self.work(5)
        week = self.week(now=at(7, 12))
        self.assertEqual(week.state, "progress")
        self.assertEqual(week.target, 35 * HOUR)

    def test_open_entry_counts_time_so_far(self):
        TimeEntry.objects.create(employee=self.employee, clock_in=at(7, 9))
        week = self.week(now=at(7, 12))
        self.assertEqual(week.worked, 3 * HOUR)

    def test_holiday_on_a_working_day_lowers_the_target(self):
        Holiday.objects.create(date=date(2026, 10, 7), name="Founders Day")
        for day in (5, 6, 8, 9):
            self.work(day)
        week = self.week()
        self.assertEqual(week.target, 28 * HOUR)
        self.assertEqual(week.state, "met")
        self.assertEqual(week.days[2].status, "holiday")

    def test_holiday_on_a_day_off_changes_nothing(self):
        Holiday.objects.create(date=date(2026, 10, 10), name="Saturday Festival")
        self.assertEqual(self.week().target, 35 * HOUR)

    def test_yearly_holiday_applies_in_later_years_only(self):
        Holiday.objects.create(date=date(2020, 10, 7), name="Every Year Day", recurs_yearly=True)
        self.assertEqual(self.week().target, 28 * HOUR)
        self.assertEqual(services.holiday_map(date(2019, 1, 1), date(2019, 12, 31)), {})

    def test_approved_leave_lowers_the_target_and_pending_leave_does_not(self):
        pending = self.leave(8, 9, status=RequestStatus.PENDING)
        self.assertEqual(self.week().target, 35 * HOUR)

        services.decide(pending, self.admin, approve=True)
        week = self.week()
        self.assertEqual(week.target, 21 * HOUR)
        self.assertEqual(week.days[3].status, "leave")
        self.assertEqual(week.days[3].label, "Sick leave")

    def test_holiday_and_leave_on_the_same_day_count_once(self):
        Holiday.objects.create(date=date(2026, 10, 7), name="Founders Day")
        self.leave(7, 7)
        self.assertEqual(self.week().target, 28 * HOUR)

    def test_week_of_joining_is_pro_rated(self):
        self.employee.start_date = date(2026, 10, 7)
        self.employee.save()
        week = self.week()
        self.assertEqual(week.target, 21 * HOUR)
        self.assertEqual(week.days[0].status, "before")

    def test_weeks_before_joining_are_not_judged(self):
        self.employee.start_date = date(2026, 11, 1)
        self.employee.save()
        self.assertEqual(self.week().state, "before")

    def test_six_day_schedule_spreads_the_hours(self):
        self.set_terms(self.schedule, 42, days=WEEKDAY_FIELDS[:6])
        Holiday.objects.create(date=date(2026, 10, 10), name="Saturday Festival")
        self.assertEqual(self.week().target, 35 * HOUR)

    def test_no_schedule_means_hours_without_a_target(self):
        services.assign_schedule(self.employee, None, services.BEGINNING)
        self.work(5)
        week = self.week()
        self.assertIsNone(week.target)
        self.assertEqual(week.state, "untracked")
        self.assertEqual(week.worked, 7 * HOUR)
        # With nothing expected of them, an empty weekday is not an absence.
        self.assertEqual([day.status for day in week.days[:3]], ["present", "none", "none"])

    def test_day_statuses(self):
        self.work(5)
        week = self.week(now=at(7, 12))
        statuses = [day.status for day in week.days]
        self.assertEqual(statuses, ["present", "absent", "today", "future", "future", "off", "off"])

    def test_work_on_a_day_off_counts_and_shows_as_present(self):
        self.work(10, hours=5)
        week = self.week()
        self.assertEqual(week.days[5].status, "present")
        self.assertEqual(week.worked, 5 * HOUR)


class RequestTests(AttendanceTestCase):
    def missing_time(self, start, end, entry=None):
        return MissingTimeRequest.objects.create(
            employee=self.employee, clock_in=start, clock_out=end, entry=entry, reason="Forgot to clock in"
        )

    def test_approved_missing_time_becomes_a_marked_manual_entry(self):
        request = self.missing_time(at(5, 9), at(5, 17))
        self.assertEqual(TimeEntry.objects.count(), 0)

        services.decide(request, self.admin, approve=True, note="Confirmed with the team lead")
        entry = TimeEntry.objects.get()
        self.assertEqual(entry.source, TimeEntry.Source.MANUAL)
        self.assertEqual(entry.request, request)
        self.assertEqual(entry.worked(), 8 * HOUR)

        request.refresh_from_db()
        self.assertEqual(request.status, RequestStatus.APPROVED)
        self.assertEqual(request.decided_by, self.admin)
        self.assertEqual(request.decision_note, "Confirmed with the team lead")

    def test_rejected_request_leaves_the_record_alone(self):
        request = self.missing_time(at(5, 9), at(5, 17))
        services.decide(request, self.admin, approve=False)
        self.assertEqual(TimeEntry.objects.count(), 0)
        request.refresh_from_db()
        self.assertEqual(request.status, RequestStatus.REJECTED)

    def test_approved_fix_supplies_the_missing_clock_out(self):
        services.punch(self.employee, "in", now=at(5, 9))
        services.close_stale_entries(now=at(6, 8))
        entry = TimeEntry.objects.get()
        request = self.missing_time(entry.clock_in, at(5, 17), entry=entry)

        services.decide(request, self.admin, approve=True)
        entry.refresh_from_db()
        self.assertFalse(entry.missing_out)
        self.assertEqual(entry.clock_out, at(5, 17))
        self.assertEqual(entry.source, TimeEntry.Source.ADJUSTED)
        self.assertEqual(TimeEntry.objects.count(), 1)
        self.assertEqual(self.week().days[0].status, "present")

    def test_a_request_cannot_be_decided_twice(self):
        request = self.missing_time(at(5, 9), at(5, 17))
        services.decide(request, self.admin, approve=True)
        with self.assertRaises(ValidationError):
            services.decide(request, self.admin, approve=True)
        self.assertEqual(TimeEntry.objects.count(), 1)

    def test_missing_time_rules(self):
        self.work(5, hours=4, start_hour=9)
        check = services.validate_missing_time
        now = at(6, 12)
        cases = {
            "overlaps recorded time": (at(5, 12), at(5, 15)),
            "out before in": (at(5, 15), at(5, 14)),
            "in the future": (at(6, 11), at(6, 13)),
            "longer than the limit": (at(5, 13), at(6, 6)),
        }
        for label, (start, end) in cases.items():
            with self.subTest(label), self.assertRaises(ValidationError):
                check(self.employee, start, end, now=now)
        check(self.employee, at(5, 13), at(5, 17), now=now)  # touching the earlier entry is fine

    def test_missing_time_cannot_overlap_an_entry_that_is_still_open(self):
        TimeEntry.objects.create(employee=self.employee, clock_in=at(6, 9))
        with self.assertRaises(ValidationError):
            services.validate_missing_time(self.employee, at(6, 10), at(6, 11), now=at(6, 12))
        services.validate_missing_time(self.employee, at(6, 7), at(6, 9), now=at(6, 12))

    def test_duplicate_pending_requests_are_refused(self):
        self.missing_time(at(5, 9), at(5, 17))
        with self.assertRaises(ValidationError):
            services.validate_missing_time(self.employee, at(5, 10), at(5, 12), now=at(6, 12))

    def test_approval_fails_if_the_time_was_recorded_in_the_meantime(self):
        request = self.missing_time(at(5, 9), at(5, 17))
        self.work(5, hours=2, start_hour=10)
        with self.assertRaises(ValidationError):
            services.decide(request, self.admin, approve=True)
        request.refresh_from_db()
        self.assertTrue(request.is_pending)
        self.assertEqual(TimeEntry.objects.count(), 1)

    def test_leave_cannot_overlap_other_leave(self):
        self.leave(8, 9)
        with self.assertRaises(ValidationError):
            services.validate_leave(self.employee, date(2026, 10, 9), date(2026, 10, 12))
        services.validate_leave(self.employee, date(2026, 10, 12), date(2026, 10, 12))

    def test_cancelling(self):
        pending = self.leave(12, 12, status=RequestStatus.PENDING)
        services.cancel(pending, by=self.employee)
        pending.refresh_from_db()
        self.assertEqual(pending.status, RequestStatus.CANCELLED)

        approved = self.leave(8, 9)
        with self.assertRaises(ValidationError):
            services.cancel(approved, by=self.employee)
        services.cancel(approved, by=self.admin)
        self.assertEqual(self.week().target, 35 * HOUR)

    def test_leave_taken_counts_working_days_only(self):
        Holiday.objects.create(date=date(2026, 10, 7), name="Founders Day")
        self.leave(5, 11)  # Monday to Sunday, with a holiday on Wednesday
        self.leave(13, 13, leave_type="casual")
        self.assertEqual(self.leave_taken(), {"Casual": 1, "Sick": 4})


class LeaveAllowanceTests(AttendanceTestCase):
    def setUp(self):
        super().setUp()
        LeaveAllowance.objects.all().delete()
        LeaveAllowance.objects.create(leave_type="annual", days=14)
        LeaveAllowance.objects.create(leave_type="sick", days=2)

    def balance(self, leave_type, year=2026):
        employee = Employee.objects.get(pk=self.employee.pk)
        return next(b for b in services.leave_balances(employee, year) if b.leave_type == leave_type)

    def test_approved_leave_uses_the_allowance_and_waiting_leave_does_not_yet(self):
        self.leave(5, 7, leave_type="annual")
        self.leave(12, 13, status=RequestStatus.PENDING, leave_type="annual")
        self.leave(19, 19, status=RequestStatus.REJECTED, leave_type="annual")
        balance = self.balance("annual")
        self.assertEqual(
            (balance.allowance, balance.taken, balance.pending, balance.remaining, balance.over_by),
            (14, 3, 2, 11, 0),
        )

    def test_weekends_and_holidays_inside_leave_use_nothing(self):
        Holiday.objects.create(date=date(2026, 10, 7), name="Founders Day")
        self.leave(5, 11, leave_type="annual")  # Monday to Sunday
        self.assertEqual(self.balance("annual").taken, 4)

    def test_going_over_the_allowance_is_flagged_not_blocked(self):
        self.leave(5, 6)  # both sick days
        waiting = self.leave(8, 8, status=RequestStatus.PENDING)
        balance = services.leave_balance_for(waiting)
        self.assertEqual((balance.remaining, balance.over_by), (0, 1))

        services.decide(waiting, self.admin, approve=True)
        self.assertEqual((self.balance("sick").taken, self.balance("sick").remaining), (3, -1))

    def test_leave_types_without_an_allowance_have_no_limit(self):
        employee = Employee.objects.get(pk=self.employee.pk)
        self.assertEqual([b.leave_type for b in services.leave_balances(employee, 2026)], ["annual", "sick"])
        self.leave(5, 5, leave_type="unpaid")
        unpaid = self.balance("unpaid")
        self.assertEqual((unpaid.allowance, unpaid.taken, unpaid.remaining, unpaid.over_by), (None, 1, None, 0))

    def test_each_calendar_year_has_its_own_allowance(self):
        LeaveRequest.objects.create(
            employee=self.employee,
            leave_type="annual",
            start_date=date(2026, 12, 30),  # Wednesday
            end_date=date(2027, 1, 5),  # Tuesday
            reason="Winter break",
            status=RequestStatus.APPROVED,
        )
        self.assertEqual(self.balance("annual", 2026).taken, 2)
        self.assertEqual(self.balance("annual", 2027).taken, 3)


class StartingDataTests(TestCase):
    def test_new_database_starts_with_common_leave_allowances(self):
        allowances = dict(LeaveAllowance.objects.values_list("leave_type", "days"))
        self.assertEqual(allowances, {"annual": 14, "casual": 10, "sick": 8})


class InstallOnPhoneTests(TestCase):
    def test_manifest_describes_the_app_and_points_at_real_icons(self):
        response = self.client.get(reverse("manifest"))
        self.assertEqual(response["Content-Type"], "application/manifest+json")
        manifest = response.json()
        self.assertEqual((manifest["start_url"], manifest["scope"], manifest["display"]), ("/", "/", "standalone"))
        self.assertEqual({icon["sizes"] for icon in manifest["icons"]}, {"192x192", "512x512"})
        self.assertIn("maskable", {icon["purpose"] for icon in manifest["icons"]})
        for icon in manifest["icons"]:
            with self.subTest(icon["src"]):
                self.assertIsNotNone(finders.find(icon["src"].removeprefix("/static/")))

    def test_service_worker_is_served_from_the_top_of_the_site_without_signing_in(self):
        response = self.client.get("/sw.js")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/javascript"))
        self.assertIn(b'addEventListener("fetch"', response.content)

    def test_pages_link_the_manifest_and_icons(self):
        page = self.client.get(reverse("login"))
        for text in ('rel="manifest" href="/manifest.webmanifest"', "apple-touch-icon.png", "/sw.js"):
            with self.subTest(text):
                self.assertContains(page, text)


class PakistanHolidayTests(TestCase):
    def test_new_database_starts_with_the_fixed_holidays(self):
        names = set(Holiday.objects.filter(recurs_yearly=True).values_list("name", flat=True))
        self.assertEqual(names, {name for _, _, name in pk_holidays.FIXED_HOLIDAYS})
        self.assertTrue(Holiday.objects.filter(tentative=True, name="Eid ul-Fitr").exists())

    def test_estimated_islamic_dates_match_a_known_year(self):
        found = pk_holidays.lunar_holidays(2025)
        self.assertIn((date(2025, 3, 31), "Eid ul-Fitr"), found)
        self.assertIn((date(2025, 6, 7), "Eid ul-Adha"), found)
        self.assertIn((date(2025, 7, 6), "Ashura"), found)

    def test_adding_estimates_twice_does_not_duplicate_or_undo_edits(self):
        Holiday.objects.all().delete()
        self.assertEqual(pk_holidays.add_lunar_holidays(Holiday, 2030), 9)
        Holiday.objects.filter(date__year=2030, name="Ashura").update(tentative=False)
        self.assertEqual(pk_holidays.add_lunar_holidays(Holiday, 2030), 0)
        self.assertEqual(Holiday.objects.filter(date__year=2030).count(), 9)


class ScheduleHistoryTests(AttendanceTestCase):
    NEXT_MONDAY = date(2026, 10, 12)
    SIX_DAYS = WEEKDAY_FIELDS[:6]

    def target(self, monday, employee=None):
        return self.week(monday=monday, employee=employee).target

    def test_new_hours_apply_from_the_chosen_week_only(self):
        self.set_terms(self.schedule, 40, start=self.NEXT_MONDAY)
        self.assertEqual(self.target(MONDAY), 35 * HOUR)
        self.assertEqual(self.target(self.NEXT_MONDAY), 40 * HOUR)
        self.assertEqual(self.target(date(2026, 11, 2)), 40 * HOUR)

    def test_a_week_already_judged_keeps_its_result(self):
        for day in range(5, 10):
            self.work(day)
        self.assertEqual(self.week().state, "met")
        self.set_terms(self.schedule, 40, start=self.NEXT_MONDAY)
        self.assertEqual(self.week().state, "met")
        # Dating the change from that week itself is how a mistake is corrected.
        self.set_terms(self.schedule, 40, start=MONDAY)
        self.assertEqual(self.week().state, "short")

    def test_a_midweek_date_starts_on_that_weeks_monday(self):
        self.set_terms(self.schedule, 40, start=date(2026, 10, 14))
        self.assertEqual(self.target(self.NEXT_MONDAY), 40 * HOUR)
        self.assertEqual(self.schedule.latest.effective_from, self.NEXT_MONDAY)

    def test_new_working_days_apply_from_the_chosen_week_only(self):
        self.set_terms(self.schedule, 35, days=self.SIX_DAYS, start=self.NEXT_MONDAY)
        self.assertEqual(self.week().days[5].status, "off")
        self.assertEqual(self.week(monday=self.NEXT_MONDAY).days[5].status, "absent")

    def test_setting_terms_from_an_earlier_week_replaces_later_changes(self):
        self.set_terms(self.schedule, 40, start=self.NEXT_MONDAY)
        self.set_terms(self.schedule, 38, start=MONDAY)
        self.assertEqual(self.target(date(2026, 9, 28)), 35 * HOUR)
        self.assertEqual(self.target(MONDAY), 38 * HOUR)
        self.assertEqual(self.target(self.NEXT_MONDAY), 38 * HOUR)
        self.assertEqual(self.schedule.versions.count(), 2)

    def test_saving_the_same_terms_adds_no_history(self):
        self.set_terms(self.schedule, 35, start=self.NEXT_MONDAY)
        self.assertEqual(self.schedule.versions.count(), 1)
        self.set_terms(self.schedule, 40, start=self.NEXT_MONDAY)
        self.set_terms(self.schedule, 35, start=self.NEXT_MONDAY)  # changed back
        self.assertEqual(self.schedule.versions.count(), 1)

    def test_moving_to_another_schedule_keeps_earlier_weeks(self):
        support = self.make_schedule("Support", 42, days=self.SIX_DAYS)
        services.assign_schedule(self.employee, support, date(2026, 10, 15))
        self.assertEqual(self.target(MONDAY), 35 * HOUR)
        self.assertEqual(self.target(self.NEXT_MONDAY), 42 * HOUR)
        self.assertEqual(self.week(monday=self.NEXT_MONDAY).terms.name, "Support")

    def test_a_schedule_given_later_leaves_earlier_weeks_without_a_target(self):
        omar = Employee.objects.create_user("omar", password="correct-horse-3", start_date=date(2026, 1, 1))
        services.assign_schedule(omar, self.schedule, self.NEXT_MONDAY)
        self.assertEqual(self.week(employee=omar).state, "untracked")
        self.assertEqual(self.target(self.NEXT_MONDAY, employee=omar), 35 * HOUR)

    def test_taking_a_schedule_away_from_a_week_onward(self):
        services.assign_schedule(self.employee, None, self.NEXT_MONDAY)
        self.assertEqual(self.target(MONDAY), 35 * HOUR)
        self.assertIsNone(self.target(self.NEXT_MONDAY))
        services.assign_schedule(self.employee, self.schedule, self.NEXT_MONDAY)  # and back again
        self.assertEqual(ScheduleAssignment.objects.filter(employee=self.employee).count(), 1)

    def test_leave_days_follow_the_schedule_of_their_week(self):
        self.set_terms(self.schedule, 42, days=self.SIX_DAYS, start=self.NEXT_MONDAY)
        self.leave(10, 10)  # a Saturday, still a day off that week
        self.leave(17, 17)  # a Saturday, now a working day
        self.assertEqual(self.leave_taken(), {"Sick": 1})


class MonthSummaryTests(AttendanceTestCase):
    def summary(self, first, last, employee=None):
        employee = Employee.objects.get(pk=(employee or self.employee).pk)
        return services.month_summary(employee, date(2026, 10, first), date(2026, 10, last), now=LATER)

    def test_days_and_hours_for_part_of_a_month(self):
        Holiday.objects.create(date=date(2026, 10, 1), name="Founders Day")  # Thursday
        self.work(2)
        self.work(5)
        self.work(6, hours=5)
        TimeEntry.objects.create(employee=self.employee, clock_in=at(7, 9), missing_out=True)
        self.leave(8, 8)
        self.work(10, hours=2)  # some work on the Saturday; Friday the 9th is an absence

        summary = self.summary(1, 11)
        self.assertEqual(
            (summary.expected_days, summary.present, summary.missing, summary.absent, summary.leave, summary.holidays),
            (5, 4, 1, 1, 1, 1),
        )
        self.assertEqual(summary.worked, 21 * HOUR)
        self.assertEqual(summary.target, 35 * HOUR)
        self.assertEqual(summary.difference, -14 * HOUR)
        self.assertEqual(summary.terms.name, "BD")

    def test_leave_over_a_weekend_counts_working_days_only(self):
        self.leave(8, 11)  # Thursday to Sunday
        summary = self.summary(1, 11)
        self.assertEqual((summary.leave, summary.expected_days), (2, 5))

    def test_target_follows_a_schedule_change_inside_the_month(self):
        self.set_terms(self.schedule, 40, start=date(2026, 10, 12))
        summary = self.summary(1, 16)  # 7 days at 7h, then 5 days at 8h
        self.assertEqual((summary.expected_days, summary.target), (12, 89 * HOUR))

    def test_no_schedule_means_hours_without_a_target(self):
        services.assign_schedule(self.employee, None, services.BEGINNING)
        self.work(5)
        summary = self.summary(1, 9)
        self.assertEqual((summary.present, summary.absent, summary.expected_days), (1, 0, 0))
        self.assertEqual(summary.worked, 7 * HOUR)
        self.assertIsNone(summary.target)
        self.assertIsNone(summary.difference)

    def test_days_before_joining_are_left_out(self):
        self.employee.start_date = date(2026, 10, 7)
        self.employee.save()
        summary = self.summary(1, 9)
        self.assertEqual((summary.expected_days, summary.absent, summary.target), (3, 3, 21 * HOUR))


class CorrectionTests(AttendanceTestCase):
    def test_admin_adds_time_with_a_reason_and_a_trail(self):
        correction = services.correct_entry(self.admin, self.employee, at(5, 9), at(5, 16), "Reader was broken")
        entry = TimeEntry.objects.get()
        self.assertEqual((entry.source, entry.note), (TimeEntry.Source.ADMIN, "Reader was broken"))
        self.assertEqual(self.week().days[0].worked, 7 * HOUR)
        self.assertEqual(
            (correction.action, correction.entry, correction.admin, correction.old_clock_in),
            (Correction.Action.ADDED, entry, self.admin, None),
        )

    def test_admin_changes_times_and_the_old_ones_are_kept(self):
        entry = self.work(5)
        correction = services.correct_entry(
            self.admin, self.employee, at(5, 9), at(5, 13), "Left at lunch", entry=entry
        )
        entry.refresh_from_db()
        self.assertEqual((entry.clock_out, entry.source), (at(5, 13), TimeEntry.Source.ADMIN))
        self.assertEqual(correction.action, Correction.Action.CHANGED)
        self.assertEqual((correction.old_clock_out, correction.new_clock_out), (at(5, 16), at(5, 13)))
        self.assertEqual(TimeEntry.objects.count(), 1)

    def test_admin_supplies_a_missing_clock_out(self):
        entry = TimeEntry.objects.create(employee=self.employee, clock_in=at(5, 9), missing_out=True)
        services.correct_entry(self.admin, self.employee, entry.clock_in, at(5, 17), "Confirmed", entry=entry)
        monday = self.week().days[0]
        self.assertEqual((monday.status, monday.worked), ("present", 8 * HOUR))

    def test_corrections_follow_the_same_rules_as_any_entry(self):
        self.work(5, hours=4)
        far_ahead = datetime(2099, 1, 1, 9, tzinfo=ZONE)
        for label, start, end in (
            ("overlap", at(5, 12), at(5, 14)),
            ("future", far_ahead, far_ahead + HOUR),
            ("backwards", at(6, 12), at(6, 11)),
        ):
            with self.subTest(label), self.assertRaises(ValidationError):
                services.correct_entry(self.admin, self.employee, start, end, "Not allowed")
        self.assertEqual(Correction.objects.count(), 0)
        self.assertEqual(TimeEntry.objects.count(), 1)

    def test_admin_removes_an_entry_and_the_trail_keeps_its_times(self):
        entry = self.work(5)
        correction = services.remove_entry(self.admin, entry, "Clocked in by mistake")
        self.assertEqual(TimeEntry.objects.count(), 0)
        correction.refresh_from_db()
        self.assertIsNone(correction.entry)
        self.assertEqual(
            (correction.action, correction.old_clock_in, correction.old_clock_out),
            (Correction.Action.REMOVED, at(5, 9), at(5, 16)),
        )


class ScheduleHistoryMigrationTests(TransactionTestCase):
    def test_existing_schedules_and_assignments_become_the_start_of_the_history(self):
        before = ("attendance", "0003_login_throttle")
        executor = MigrationExecutor(connection)
        executor.migrate([before])
        old = executor.loader.project_state([before]).apps
        bd = old.get_model("attendance", "Schedule").objects.create(
            name="BD", weekly_hours=Decimal("42"), saturday=True
        )
        OldEmployee = old.get_model("attendance", "Employee")
        OldEmployee.objects.create(username="sara", schedule=bd, start_date=date(2026, 1, 1))
        OldEmployee.objects.create(username="free", start_date=date(2026, 1, 1))

        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

        terms = Employee.objects.get(username="sara").terms_on(date(2026, 10, 5))
        self.assertEqual((terms.name, terms.weekly_hours, terms.days_label), ("BD", 42, "Mon–Sat"))
        self.assertEqual(terms.effective_from, services.BEGINNING)
        self.assertIsNone(Employee.objects.get(username="free").terms_on(date(2026, 10, 5)))
        self.assertEqual(ScheduleVersion.objects.count(), 1)


class LockoutTests(AttendanceTestCase):
    def attempt(self, password, username="sara"):
        return self.client.post(reverse("login"), {"username": username, "password": password})

    def signed_in(self):
        return "_auth_user_id" in self.client.session

    def test_five_wrong_passwords_lock_sign_in_even_for_the_right_one(self):
        for _ in range(4):
            self.assertContains(self.attempt("wrong"), "Please enter a correct username and password")
        self.assertContains(self.attempt("wrong"), "Too many wrong passwords")
        self.assertContains(self.attempt("correct-horse-1"), "Too many wrong passwords")
        self.assertFalse(self.signed_in())

    def test_lock_ends_after_fifteen_minutes(self):
        for _ in range(5):
            self.attempt("wrong")
        later = timezone.now() + timedelta(minutes=16)
        with mock.patch("django.utils.timezone.now", return_value=later):
            self.assertRedirects(self.attempt("correct-horse-1"), reverse("home"))

    def test_old_mistakes_are_forgotten(self):
        for _ in range(4):
            self.attempt("wrong")
        later = timezone.now() + timedelta(minutes=16)
        with mock.patch("django.utils.timezone.now", return_value=later):
            self.attempt("wrong")
            self.assertRedirects(self.attempt("correct-horse-1"), reverse("home"))

    def test_signing_in_resets_the_count(self):
        for _ in range(4):
            self.attempt("wrong")
        self.attempt("correct-horse-1")
        self.client.logout()
        for _ in range(4):
            self.attempt("wrong")
        self.assertRedirects(self.attempt("correct-horse-1"), reverse("home"))

    def test_changing_the_case_of_the_username_does_not_dodge_the_lock(self):
        for name in ("sara", "SARA", "Sara", " sara ", "sARA"):
            self.attempt("wrong", username=name)
        self.assertContains(self.attempt("correct-horse-1"), "Too many wrong passwords")

    def test_usernames_that_do_not_exist_lock_the_same_way(self):
        for _ in range(5):
            response = self.attempt("wrong", username="nobody")
        self.assertContains(response, "Too many wrong passwords")

    def test_one_persons_lock_does_not_affect_anyone_else(self):
        for _ in range(5):
            self.attempt("wrong")
        self.attempt("correct-horse-2", username="boss")
        self.assertTrue(self.signed_in())

    def test_admin_setting_a_new_password_unlocks_the_account(self):
        for _ in range(5):
            self.attempt("wrong")
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("manage_employees")), "Locked until")
        self.client.post(
            reverse("manage_employee_password", args=[self.employee.pk]),
            {"new_password1": "a-brand-new-pass", "new_password2": "a-brand-new-pass"},
        )
        self.assertNotContains(self.client.get(reverse("manage_employees")), "Locked until")
        self.client.logout()
        self.assertRedirects(self.attempt("a-brand-new-pass"), reverse("home"))

    def test_the_raw_admin_login_is_locked_too(self):
        for _ in range(5):
            self.attempt("wrong", username="boss")
        self.client.post("/admin/login/", {"username": "boss", "password": "correct-horse-2"})
        self.assertFalse(self.signed_in())


class ViewTests(AttendanceTestCase):
    """Drives the pages the way a browser would, with the clock fixed at Wednesday 7 October."""

    NOW = at(7, 10, 30)

    def setUp(self):
        super().setUp()
        patcher = mock.patch("django.utils.timezone.now", return_value=self.NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sign_in(self, employee):
        self.client.force_login(employee)

    def test_pages_require_signing_in(self):
        for name in ("home", "calendar", "my_requests", "manage_dashboard"):
            response = self.client.get(reverse(name))
            self.assertRedirects(response, f"{reverse('login')}?next={reverse(name)}")

    def test_sign_in_with_password(self):
        response = self.client.post(reverse("login"), {"username": "sara", "password": "correct-horse-1"})
        self.assertRedirects(response, reverse("home"))

    def test_employee_pages_render(self):
        self.work(5)
        TimeEntry.objects.create(employee=self.employee, clock_in=at(6, 9), missing_out=True)
        self.leave(8, 8)
        MissingTimeRequest.objects.create(
            employee=self.employee, clock_in=at(2, 9), clock_out=at(2, 17), reason="Reader was down"
        )
        self.sign_in(self.employee)
        for name in ("home", "calendar", "my_requests", "missing_time_new", "leave_new", "password_change"):
            with self.subTest(name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        self.assertContains(self.client.get(reverse("home")), "No clock-out on")
        self.assertContains(self.client.get(reverse("calendar")), "Sick leave")

    def test_admin_pages_render(self):
        self.work(5)
        self.work(7, hours=1)
        TimeEntry.objects.create(employee=self.employee, clock_in=at(6, 9), missing_out=True)
        approved = self.leave(8, 8)
        services.decide(self.leave(12, 12, status=RequestStatus.PENDING), self.admin, approve=False)
        MissingTimeRequest.objects.create(
            employee=self.employee, clock_in=at(2, 9), clock_out=at(2, 17), reason="Reader was down"
        )
        Holiday.objects.create(date=date(2026, 10, 9), name="Founders Day", tentative=True)
        self.sign_in(self.admin)
        pages = [
            reverse("manage_dashboard"),
            reverse("manage_review"),
            reverse("manage_review") + f"?start=2026-09-01&end=2026-10-31&employee={self.employee.pk}",
            reverse("manage_requests"),
            reverse("manage_employees"),
            reverse("manage_employee_new"),
            reverse("manage_employee_detail", args=[self.employee.pk]),
            reverse("manage_employee_edit", args=[self.employee.pk]),
            reverse("manage_employee_password", args=[self.employee.pk]),
            reverse("manage_schedules"),
            reverse("manage_schedule_new"),
            reverse("manage_schedule_edit", args=[self.schedule.pk]),
            reverse("manage_holidays"),
            reverse("manage_holiday_new"),
            reverse("manage_holiday_edit", args=[Holiday.objects.get().pk]),
        ]
        for url in pages:
            with self.subTest(url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.assertContains(self.client.get(reverse("manage_dashboard")), "Founders Day")
        self.assertContains(self.client.get(reverse("manage_requests")), "Reader was down")
        self.assertEqual(approved.status, RequestStatus.APPROVED)

    def test_employees_cannot_open_admin_pages_or_actions(self):
        request = MissingTimeRequest.objects.create(
            employee=self.employee, clock_in=at(2, 9), clock_out=at(2, 17), reason="Reader was down"
        )
        self.sign_in(self.employee)
        for name in ("manage_dashboard", "manage_review", "manage_export_daily", "manage_requests",
                     "manage_employees", "manage_schedules", "manage_holidays"):
            with self.subTest(name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403)
        response = self.client.post(
            reverse("manage_request_decide", args=["missing-time", request.pk]), {"decision": "approve"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(TimeEntry.objects.count(), 0)

    def test_clock_button_round_trip(self):
        self.sign_in(self.employee)
        self.assertContains(self.client.get(reverse("home")), "Clock in")

        self.client.post(reverse("punch"), {"action": "in"})
        self.client.post(reverse("punch"), {"action": "in"})  # double tap
        entry = TimeEntry.objects.get()
        self.assertEqual(entry.clock_in, self.NOW)
        self.assertContains(self.client.get(reverse("home")), "Clock out")

        self.client.post(reverse("punch"), {"action": "out"})
        entry.refresh_from_db()
        self.assertEqual(entry.clock_out, self.NOW)
        self.assertEqual(self.client.post(reverse("punch"), {"action": "sideways"}).status_code, 400)
        self.assertEqual(self.client.get(reverse("punch")).status_code, 405)

    def test_time_sent_by_the_browser_is_ignored(self):
        self.sign_in(self.employee)
        self.client.post(reverse("punch"), {"action": "in", "clock_in": "2026-10-07 06:00", "time": "06:00"})
        self.assertEqual(TimeEntry.objects.get().clock_in, self.NOW)

    def test_missing_time_request_from_form_to_record(self):
        self.sign_in(self.employee)
        form = {"date": "2026-10-06", "time_in": "09:00", "time_out": "17:15", "reason": "Forgot my phone"}
        self.assertRedirects(self.client.post(reverse("missing_time_new"), form), reverse("my_requests"))
        self.assertEqual(TimeEntry.objects.count(), 0)

        duplicate = self.client.post(reverse("missing_time_new"), form)
        self.assertContains(duplicate, "already a pending request")

        request = MissingTimeRequest.objects.get()
        self.sign_in(self.admin)
        self.client.post(
            reverse("manage_request_decide", args=["missing-time", request.pk]),
            {"decision": "approve", "note": "ok"},
        )
        entry = TimeEntry.objects.get()
        self.assertEqual((entry.clock_in, entry.clock_out), (at(6, 9), at(6, 17, 15)))
        self.assertEqual(entry.source, TimeEntry.Source.MANUAL)

    def test_missing_clock_out_form_asks_only_for_the_time(self):
        entry = TimeEntry.objects.create(employee=self.employee, clock_in=at(6, 9), missing_out=True)
        self.sign_in(self.employee)
        url = reverse("missing_time_new") + f"?entry={entry.pk}"
        self.assertNotContains(self.client.get(url), 'name="time_in"')
        self.client.post(url, {"time_out": "18:00", "reason": "Left in a hurry"})
        request = MissingTimeRequest.objects.get()
        self.assertEqual((request.entry, request.clock_in, request.clock_out), (entry, at(6, 9), at(6, 18)))

    def test_missing_time_in_the_future_is_refused(self):
        self.sign_in(self.employee)
        form = {"date": "2026-10-07", "time_in": "09:00", "time_out": "17:00", "reason": "Planning ahead"}
        self.assertContains(self.client.post(reverse("missing_time_new"), form), "already passed")
        self.assertEqual(MissingTimeRequest.objects.count(), 0)

    def test_leave_request_from_form_to_calendar(self):
        self.sign_in(self.employee)
        backwards = {"leave_type": "sick", "start_date": "2026-10-09", "end_date": "2026-10-08", "reason": "Flu"}
        self.assertContains(self.client.post(reverse("leave_new"), backwards), "cannot be before the first day")

        form = {"leave_type": "sick", "start_date": "2026-10-08", "end_date": "2026-10-09", "reason": "Flu"}
        self.assertRedirects(self.client.post(reverse("leave_new"), form), reverse("my_requests"))
        leave = LeaveRequest.objects.get()
        self.assertEqual(leave.employee, self.employee)
        self.assertTrue(leave.is_pending)

        self.sign_in(self.admin)
        self.client.post(reverse("manage_request_decide", args=["leave", leave.pk]), {"decision": "approve"})
        self.assertEqual(self.week(now=self.NOW).target, 21 * HOUR)

    def test_employees_can_only_cancel_their_own_requests(self):
        other = Employee.objects.create_user("omar", password="correct-horse-3")
        leave = self.leave(12, 12, status=RequestStatus.PENDING)
        self.sign_in(other)
        response = self.client.post(reverse("request_cancel", args=["leave", leave.pk]))
        self.assertEqual(response.status_code, 404)
        leave.refresh_from_db()
        self.assertTrue(leave.is_pending)

    def test_exports(self):
        self.work(5)
        MissingTimeRequest.objects.create(
            employee=self.employee, clock_in=at(6, 9), clock_out=at(6, 17), reason="=HYPERLINK(1)"
        )
        services.decide(MissingTimeRequest.objects.get(), self.admin, approve=True)
        self.sign_in(self.admin)
        query = "?start=2026-10-05&end=2026-10-07"

        daily = self.client.get(reverse("manage_export_daily") + query)
        self.assertEqual(daily["Content-Type"], "text/csv; charset=utf-8")
        text = daily.content.decode("utf-8-sig")
        self.assertIn("sara,2026-10-05,Mon,Present,7.00,09:00-16:00", text)
        self.assertIn("'=HYPERLINK(1)", text)

        weekly = self.client.get(reverse("manage_export_weekly") + query).content.decode("utf-8-sig")
        self.assertIn("sara,2026-10-05,2026-10-11,15.00,35.00,-20.00,In progress", weekly)

    def test_admin_adds_an_employee_who_can_then_sign_in(self):
        self.sign_in(self.admin)
        response = self.client.post(
            reverse("manage_employee_new"),
            {
                "username": "bilal",
                "password": "a-long-enough-pass",
                "first_name": "Bilal",
                "last_name": "Khan",
                "email": "",
                "schedule": self.schedule.pk,
                "start_date": "2026-10-07",
                "is_active": "on",
            },
        )
        self.assertRedirects(response, reverse("manage_employees"))
        bilal = Employee.objects.get(username="bilal")
        self.assertFalse(bilal.is_staff)
        self.assertEqual(bilal.terms.name, "BD")
        self.assertEqual(bilal.assignments.get().effective_from, services.BEGINNING)  # on it from the start
        self.assertTrue(self.client.login(username="bilal", password="a-long-enough-pass"))

    def test_admin_cannot_lock_themselves_out(self):
        self.sign_in(self.admin)
        response = self.client.post(
            reverse("manage_employee_edit", args=[self.admin.pk]),
            {"username": "boss", "start_date": "2026-01-01", "is_active": "on"},
        )
        self.assertContains(response, "cannot remove your own admin access")
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_staff)

    def test_schedule_and_holiday_setup(self):
        self.sign_in(self.admin)
        self.client.post(
            reverse("manage_schedule_new"),
            {"name": "Support", "weekly_hours": "40", "monday": "on", "tuesday": "on", "saturday": "on"},
        )
        support = Schedule.objects.get(name="Support")
        self.assertEqual(support.current.days_label, "Mon, Tue, Sat")
        self.assertEqual(support.current.weekly_hours, 40)

        same_name = self.client.post(
            reverse("manage_schedule_new"), {"name": "support", "weekly_hours": "30", "monday": "on"}
        )
        self.assertContains(same_name, "already a schedule with this name")

        no_days = self.client.post(reverse("manage_schedule_new"), {"name": "Empty", "weekly_hours": "10"})
        self.assertContains(no_days, "at least one working day")

        self.client.post(reverse("manage_schedule_delete", args=[self.schedule.pk]))
        self.assertTrue(Schedule.objects.filter(pk=self.schedule.pk).exists())  # still in use
        self.client.post(reverse("manage_schedule_delete", args=[support.pk]))
        self.assertFalse(Schedule.objects.filter(pk=support.pk).exists())

        self.client.post(reverse("manage_holiday_new"), {"date": "2026-10-09", "name": "Founders Day"})
        self.assertEqual(self.week(now=self.NOW).target, 28 * HOUR)
        self.client.post(reverse("manage_holiday_preset"), {"year": "2031"})
        self.assertEqual(Holiday.objects.filter(date__year=2031, tentative=True).count(), 9)
        self.client.post(reverse("manage_holiday_delete", args=[Holiday.objects.get(name="Founders Day").pk]))
        self.assertEqual(self.week(now=self.NOW).target, 35 * HOUR)

    def test_admins_keep_no_attendance_of_their_own(self):
        self.sign_in(self.admin)
        for name in ("home", "calendar", "my_requests", "missing_time_new", "leave_new"):
            with self.subTest(name):
                self.assertRedirects(self.client.get(reverse(name)), reverse("manage_dashboard"))
        self.client.post(reverse("punch"), {"action": "in"})
        self.assertEqual(TimeEntry.objects.count(), 0)

        dashboard = self.client.get(reverse("manage_dashboard"))
        self.assertNotContains(dashboard, reverse("calendar"))  # no personal menu
        for name in ("manage_dashboard", "manage_review", "manage_schedules"):
            with self.subTest(name):
                rows = self.client.get(reverse(name)).context["rows"]
                self.assertEqual([row["employee"] for row in rows], [self.employee])
        weekly = self.client.get(reverse("manage_export_weekly")).content.decode("utf-8-sig")
        self.assertIn("sara", weekly)
        self.assertNotIn("boss", weekly)

    def test_admin_lands_on_the_admin_area_after_signing_in(self):
        response = self.client.post(
            reverse("login"), {"username": "boss", "password": "correct-horse-2"}, follow=True
        )
        self.assertEqual(response.redirect_chain[-1][0], reverse("manage_dashboard"))

    def test_making_someone_an_admin_clears_their_schedule(self):
        self.sign_in(self.admin)
        self.client.post(
            reverse("manage_employee_edit", args=[self.employee.pk]),
            {
                "username": "sara",
                "schedule": self.schedule.pk,
                "start_date": "2026-01-01",
                "is_staff": "on",
                "is_active": "on",
            },
        )
        employee = Employee.objects.get(pk=self.employee.pk)
        self.assertTrue(employee.is_staff)
        self.assertIsNone(employee.terms)
        self.assertEqual(employee.terms_on(date(2026, 9, 28)).name, "BD")  # earlier weeks keep their history

    def test_schedule_overview_shows_each_employees_week(self):
        Holiday.objects.create(date=date(2026, 10, 9), name="Founders Day")
        self.leave(8, 8)
        self.sign_in(self.admin)

        this_week = self.client.get(reverse("manage_schedules"))
        self.assertEqual(this_week.context["monday"], MONDAY)
        self.assertEqual(this_week.context["rows"][0]["week"].target, 21 * HOUR)
        for text in ("Founders Day", "Sick leave", "7h 00m", "21h 00m", "BD · 35h a week"):
            with self.subTest(text):
                self.assertContains(this_week, text)

        next_week = self.client.get(reverse("manage_schedules") + "?week=2026-10-14")
        self.assertEqual(next_week.context["monday"], date(2026, 10, 12))
        self.assertEqual(next_week.context["rows"][0]["week"].target, 35 * HOUR)
        self.assertNotContains(next_week, "Sick leave")

        nonsense = self.client.get(reverse("manage_schedules") + "?week=nonsense")
        self.assertEqual(nonsense.context["monday"], MONDAY)

    def test_editing_a_schedule_from_next_week_keeps_this_week(self):
        self.sign_in(self.admin)
        url = reverse("manage_schedule_edit", args=[self.schedule.pk])
        self.assertEqual(self.client.get(url).context["form"].initial["effective_from"], MONDAY)
        days = {day: "on" for day in WEEKDAY_FIELDS[:5]}
        self.client.post(url, {"name": "BD", "weekly_hours": "40", "effective_from": "2026-10-12", **days})
        self.assertEqual(self.week(now=self.NOW).target, 35 * HOUR)
        self.assertEqual(self.week(now=self.NOW, monday=date(2026, 10, 12)).target, 40 * HOUR)
        page = self.client.get(reverse("manage_schedules"))
        self.assertContains(page, "Changes to 40h, Mon–Fri from 12 Oct 2026")

    def test_renaming_a_schedule_adds_no_history(self):
        self.sign_in(self.admin)
        days = {day: "on" for day in WEEKDAY_FIELDS[:5]}
        self.client.post(
            reverse("manage_schedule_edit", args=[self.schedule.pk]),
            {"name": "Sales", "weekly_hours": "35.00", "effective_from": "2026-10-05", **days},
        )
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.name, "Sales")
        self.assertEqual(self.schedule.versions.count(), 1)

    def test_changing_an_employees_schedule_from_a_chosen_week(self):
        support = self.make_schedule("Support", 42, days=WEEKDAY_FIELDS[:6])
        self.sign_in(self.admin)
        url = reverse("manage_employee_edit", args=[self.employee.pk])
        details = {"username": "sara", "start_date": "2026-01-01", "is_active": "on", "schedule_from": "2026-10-14"}

        self.client.post(url, {**details, "schedule": self.schedule.pk})  # same schedule: nothing to record
        self.assertEqual(self.employee.assignments.count(), 1)

        self.client.post(url, {**details, "schedule": support.pk})
        employee = Employee.objects.get(pk=self.employee.pk)
        self.assertEqual(employee.terms.name, "BD")
        self.assertEqual(employee.terms_on(date(2026, 10, 12)).name, "Support")
        detail = self.client.get(reverse("manage_employee_detail", args=[employee.pk]))
        self.assertContains(detail, "Schedule history")
        self.assertContains(detail, "Support from 12 Oct 2026")

    def test_admin_corrects_the_record_from_the_employee_page(self):
        self.sign_in(self.admin)
        page = reverse("manage_employee_detail", args=[self.employee.pk])
        add = reverse("manage_entry_new", args=[self.employee.pk])
        form = {"date": "2026-10-06", "time_in": "09:00", "time_out": "17:00", "reason": "Reader was down"}
        self.assertRedirects(self.client.post(add, form), page + "?month=2026-10")
        entry = TimeEntry.objects.get()
        self.assertEqual((entry.source, entry.clock_in), (TimeEntry.Source.ADMIN, at(6, 9)))
        self.assertContains(self.client.post(add, form), "overlaps time that is already recorded")

        edit = reverse("manage_entry_edit", args=[entry.pk])
        self.assertContains(self.client.get(edit), 'value="09:00"')
        self.client.post(edit, {**form, "time_out": "15:30", "reason": "Left early"})
        entry.refresh_from_db()
        self.assertEqual((entry.clock_out, entry.note), (at(6, 15, 30), "Left early"))
        for text in ("Admin corrections", "Reader was down", "Left early", reverse("manage_entry_remove", args=[entry.pk])):
            with self.subTest(text):
                self.assertContains(self.client.get(page), text)

        remove = reverse("manage_entry_remove", args=[entry.pk])
        self.assertContains(self.client.get(remove), "Tuesday 06 October 2026")
        self.client.post(remove, {"reason": "Entered for the wrong person"})
        self.assertEqual(TimeEntry.objects.count(), 0)
        self.assertEqual(Correction.objects.count(), 3)

        # The employee can see what was changed and why, but cannot do it themselves.
        self.sign_in(self.employee)
        self.assertContains(self.client.get(reverse("calendar")), "Entered for the wrong person")
        self.assertEqual(self.client.post(add, form).status_code, 403)
        self.assertEqual(TimeEntry.objects.count(), 0)

    def test_correcting_only_the_clock_out_leaves_the_punched_clock_in_exact(self):
        punched = at(6, 9, 2) + timedelta(seconds=37)
        entry = TimeEntry.objects.create(employee=self.employee, clock_in=punched, missing_out=True)
        self.sign_in(self.admin)
        self.client.post(
            reverse("manage_entry_edit", args=[entry.pk]),
            {"date": "2026-10-06", "time_in": "09:02", "time_out": "17:00", "reason": "Forgot to clock out"},
        )
        entry.refresh_from_db()
        self.assertEqual((entry.clock_in, entry.clock_out, entry.missing_out), (punched, at(6, 17), False))

    def test_monthly_summary_page_and_export(self):
        self.work(5)
        self.work(6, hours=5)
        self.work(7, hours=1)  # today: left out, the month is counted up to yesterday
        gone = Employee.objects.create_user("omar", password="correct-horse-3", start_date=date(2026, 1, 1))
        gone.is_active = False
        gone.save()
        TimeEntry.objects.create(
            employee=gone, clock_in=datetime(2026, 9, 15, 9, tzinfo=ZONE), clock_out=datetime(2026, 9, 15, 17, tzinfo=ZONE)
        )
        self.sign_in(self.admin)

        october = self.client.get(reverse("manage_monthly"))
        self.assertContains(october, "counted up to Tuesday 6 October")
        (row,) = october.context["rows"]
        self.assertEqual((row.employee, row.present, row.absent, row.worked), (self.employee, 2, 2, 12 * HOUR))
        self.assertEqual((row.target, row.difference), (28 * HOUR, -16 * HOUR))

        september = self.client.get(reverse("manage_monthly") + "?month=2026-09")
        self.assertNotContains(september, "still running")
        self.assertEqual({row.employee.username for row in september.context["rows"]}, {"sara", "omar"})
        self.assertContains(september, "inactive")

        november = self.client.get(reverse("manage_monthly") + "?month=2026-11")
        self.assertContains(november, "Nothing to report for November 2026 yet")

        export = self.client.get(reverse("manage_export_monthly") + "?month=2026-10")
        self.assertEqual(export["Content-Disposition"], 'attachment; filename="attendance-2026-10.csv"')
        self.assertIn("sara,sara,BD,4,2,0,2,0,0,12.00,28.00,-16.00", export.content.decode("utf-8-sig"))

        self.sign_in(self.employee)
        for name in ("manage_monthly", "manage_export_monthly"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def give_everyone_an_address(self):
        Employee.objects.filter(pk=self.admin.pk).update(email="boss@example.com")
        Employee.objects.filter(pk=self.employee.pk).update(email="sara@example.com")

    def test_email_notices_follow_a_leave_request_from_start_to_finish(self):
        self.give_everyone_an_address()
        self.sign_in(self.employee)
        form = {"leave_type": "sick", "start_date": "2026-10-08", "end_date": "2026-10-09", "reason": "Flu"}
        self.client.post(reverse("leave_new"), form)
        (to_admin,) = mail.outbox
        self.assertEqual((to_admin.to, to_admin.subject), (["boss@example.com"], "[Attendance] New request from sara"))
        for text in (
            "Sick leave, Thu 08 Oct 2026 to Fri 09 Oct 2026 (2 days)",
            "Reason: Flu",
            "http://testserver/manage/requests/",
        ):
            with self.subTest(text):
                self.assertIn(text, to_admin.body)

        self.sign_in(self.admin)
        leave = LeaveRequest.objects.get()
        self.client.post(
            reverse("manage_request_decide", args=["leave", leave.pk]),
            {"decision": "approve", "note": "Get well soon"},
        )
        to_employee = mail.outbox[1]
        self.assertEqual(
            (to_employee.to, to_employee.subject), (["sara@example.com"], "[Attendance] Your request was approved")
        )
        self.assertIn("Note from boss: Get well soon", to_employee.body)
        self.assertIn("http://testserver/requests/", to_employee.body)

        self.client.post(reverse("manage_leave_cancel", args=[leave.pk]))
        self.assertEqual(mail.outbox[2].subject, "[Attendance] Your request was cancelled")
        self.assertEqual(len(mail.outbox), 3)

    def test_email_notices_for_missing_time_and_corrections(self):
        self.give_everyone_an_address()
        self.sign_in(self.employee)
        self.client.post(
            reverse("missing_time_new"),
            {"date": "2026-10-06", "time_in": "09:00", "time_out": "17:15", "reason": "Forgot my phone"},
        )
        self.assertIn("Missing time: Tue 06 Oct 2026, 9:00 AM to 5:15 PM (8h 15m)", mail.outbox[0].body)

        self.sign_in(self.admin)
        request = MissingTimeRequest.objects.get()
        self.client.post(reverse("manage_request_decide", args=["missing-time", request.pk]), {"decision": "reject"})
        self.assertEqual(mail.outbox[1].subject, "[Attendance] Your request was rejected")

        entry_form = {"date": "2026-10-05", "time_in": "09:00", "time_out": "17:00", "reason": "Reader was down"}
        self.client.post(reverse("manage_entry_new", args=[self.employee.pk]), entry_form)
        added = mail.outbox[2]
        self.assertEqual((added.to, added.subject), (["sara@example.com"], "[Attendance] Your attendance record was corrected"))
        self.assertIn("Added: Mon 05 Oct 2026, 9:00 AM to 5:00 PM", added.body)
        self.assertIn("Reason: Reader was down", added.body)

        entry = TimeEntry.objects.get()
        self.client.post(reverse("manage_entry_edit", args=[entry.pk]), {**entry_form, "time_out": "15:00"})
        self.assertIn("Was: Mon 05 Oct 2026, 9:00 AM to 5:00 PM", mail.outbox[3].body)
        self.assertIn("Now: Mon 05 Oct 2026, 9:00 AM to 3:00 PM", mail.outbox[3].body)

        self.client.post(reverse("manage_entry_remove", args=[entry.pk]), {"reason": "Wrong person"})
        self.assertIn("Removed: Mon 05 Oct 2026, 9:00 AM to 3:00 PM", mail.outbox[4].body)

    def test_nothing_is_sent_to_people_without_an_address(self):
        self.sign_in(self.employee)
        form = {"leave_type": "sick", "start_date": "2026-10-08", "end_date": "2026-10-08", "reason": "Flu"}
        self.client.post(reverse("leave_new"), form)
        self.sign_in(self.admin)
        self.client.post(
            reverse("manage_request_decide", args=["leave", LeaveRequest.objects.get().pk]), {"decision": "approve"}
        )
        self.assertEqual(mail.outbox, [])

    def test_a_failing_mail_server_does_not_stop_the_work(self):
        self.give_everyone_an_address()
        self.sign_in(self.employee)
        form = {"leave_type": "sick", "start_date": "2026-10-08", "end_date": "2026-10-08", "reason": "Flu"}
        with (
            mock.patch("attendance.notifications.send_mail", side_effect=OSError("mail server is down")),
            self.assertLogs("attendance.notifications", level="ERROR"),
        ):
            response = self.client.post(reverse("leave_new"), form)
        self.assertRedirects(response, reverse("my_requests"))
        self.assertEqual(LeaveRequest.objects.count(), 1)

    def test_leave_allowance_from_request_to_the_admins_inbox(self):
        LeaveAllowance.objects.update_or_create(leave_type="sick", defaults={"days": 2})
        self.leave(5, 6)  # both sick days already used
        self.sign_in(self.employee)
        self.assertContains(self.client.get(reverse("my_requests")), "2 of 2 used")
        self.assertContains(self.client.get(reverse("leave_new")), "Sick 0")
        form = {"leave_type": "sick", "start_date": "2026-10-08", "end_date": "2026-10-09", "reason": "Flu"}
        sent = self.client.post(reverse("leave_new"), form, follow=True)
        self.assertContains(sent, "goes 2 days over your sick leave for 2026")
        self.assertContains(sent, "2 waiting for approval")
        self.assertEqual(LeaveRequest.objects.filter(status=RequestStatus.PENDING).count(), 1)  # not blocked

        self.sign_in(self.admin)
        inbox = self.client.get(reverse("manage_requests"))
        self.assertContains(inbox, "Uses 2 working days")
        self.assertContains(inbox, "2 of 2 already used")
        self.assertContains(inbox, "2 days over the allowance if approved")
        detail = self.client.get(reverse("manage_employee_detail", args=[self.employee.pk]))
        self.assertContains(detail, "2 of 2 used")

    def test_admin_sets_allowances_and_sees_everyones_balance(self):
        self.leave(5, 6)
        self.sign_in(self.admin)
        (row,) = self.client.get(reverse("manage_leave")).context["rows"]
        sick = next(cell for cell in row["cells"] if cell and cell.leave_type == "sick")
        self.assertEqual((row["employee"], sick.taken, sick.allowance), (self.employee, 2, 8))

        saved = self.client.post(
            reverse("manage_leave"), {"annual": "20", "casual": "", "sick": "1", "unpaid": "", "other": ""}
        )
        self.assertRedirects(saved, reverse("manage_leave") + "?year=2026")
        self.assertEqual(dict(LeaveAllowance.objects.values_list("leave_type", "days")), {"annual": 20, "sick": 1})
        self.assertContains(self.client.get(reverse("manage_leave")), "1 over")
        self.assertContains(self.client.post(reverse("manage_leave"), {"annual": "-3"}), "greater than or equal to 0")

        self.sign_in(self.employee)
        self.assertEqual(self.client.get(reverse("manage_leave")).status_code, 403)
        self.assertEqual(self.client.post(reverse("manage_leave"), {"annual": "99"}).status_code, 403)
        self.assertEqual(LeaveAllowance.objects.get(leave_type="annual").days, 20)
