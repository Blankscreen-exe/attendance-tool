"""Attendance rules: punching, day status, weekly targets and requests.

Everything a calendar or report shows is derived here from four sources:
time entries, approved leave, holidays and the employee's schedule.
"""

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .dates import (  # noqa: F401  (re-exported for the views)
    BEGINNING,
    ONE_DAY,
    clock_text,
    day_start,
    duration_text,
    today,
    week_start,
    work_date,
)
from .models import (
    Correction,
    Employee,
    Holiday,
    LeaveAllowance,
    LeaveRequest,
    MissingTimeRequest,
    RequestStatus,
    ScheduleAssignment,
    ScheduleVersion,
    TimeEntry,
)

MAX_ENTRY_HOURS = 16
MAX_LEAVE_DAYS = 90
DEFAULT_WORKING_WEEKDAYS = frozenset(range(5))


# --- Schedules over time ----------------------------------------------------


@transaction.atomic
def set_schedule_terms(schedule, weekly_hours, weekdays, effective_from):
    """Give a schedule these hours and days from the week of `effective_from` onward.

    Weeks before that keep whatever applied to them. Anything that had been
    set for that week or later is replaced. `weekdays` maps each weekday
    field name to whether it is a working day.
    """
    monday = max(week_start(effective_from), BEGINNING)
    schedule.versions.filter(effective_from__gte=monday).delete()
    previous = schedule.versions.filter(effective_from__lt=monday).order_by("-effective_from").first()
    version = ScheduleVersion(schedule=schedule, effective_from=monday, weekly_hours=weekly_hours, **weekdays)
    if previous is None or previous.terms != version.terms:
        version.save()
    schedule.forget_history()


@transaction.atomic
def assign_schedule(employee, schedule, effective_from):
    """Put an employee on a schedule (or none) from the week of `effective_from` onward."""
    monday = max(week_start(effective_from), BEGINNING)
    employee.assignments.filter(effective_from__gte=monday).delete()
    previous = employee.assignments.filter(effective_from__lt=monday).order_by("-effective_from").first()
    previous_schedule = previous.schedule if previous else None
    if previous_schedule != schedule:
        ScheduleAssignment.objects.create(employee=employee, schedule=schedule, effective_from=monday)
    employee.forget_history()


# --- Punching ---------------------------------------------------------------


def close_stale_entries(employee=None, now=None):
    """Flag entries left open from an earlier day as missing their clock-out."""
    cutoff = day_start(today(now))
    stale = TimeEntry.objects.filter(clock_out__isnull=True, missing_out=False, clock_in__lt=cutoff)
    if employee is not None:
        stale = stale.filter(employee=employee)
    if not stale.exists():
        return 0
    return stale.update(missing_out=True)


def get_open_entry(employee):
    return TimeEntry.objects.filter(employee=employee, clock_out__isnull=True, missing_out=False).first()


@transaction.atomic
def punch(employee, action, now=None):
    """Clock `employee` in or out at the server's current time.

    `action` is what the button said when it was pressed ("in" or "out"). If
    the employee is already in that state, nothing changes, so a double tap
    can never clock someone in and straight back out.

    Returns (entry, changed).
    """
    if action not in ("in", "out"):
        raise ValueError(f"Unknown punch action: {action!r}")
    now = (now or timezone.now()).replace(microsecond=0)
    # Serialises concurrent punches by the same person (a no-op on SQLite,
    # where IMMEDIATE transactions already do).
    Employee.objects.select_for_update().get(pk=employee.pk)
    close_stale_entries(employee, now)
    entry = get_open_entry(employee)
    if action == "in":
        if entry:
            return entry, False
        return TimeEntry.objects.create(employee=employee, clock_in=now), True
    if not entry:
        return None, False
    entry.clock_out = now
    entry.save(update_fields=["clock_out"])
    return entry, True


# --- Holidays and leave -----------------------------------------------------


@dataclass(frozen=True)
class HolidayInfo:
    name: str
    tentative: bool


def holiday_occurrences(start, end):
    """(date, Holiday) pairs between two dates, expanding yearly holidays."""
    found = [
        (holiday.date, holiday)
        for holiday in Holiday.objects.filter(recurs_yearly=False, date__range=(start, end))
    ]
    for holiday in Holiday.objects.filter(recurs_yearly=True, date__lte=end):
        for year in range(max(start.year, holiday.date.year), end.year + 1):
            try:
                day = holiday.date.replace(year=year)
            except ValueError:  # 29 February in a non-leap year
                continue
            if start <= day <= end:
                found.append((day, holiday))
    return sorted(found, key=lambda pair: (pair[0], pair[1].name))


def holiday_map(start, end):
    grouped = defaultdict(list)
    for day, holiday in holiday_occurrences(start, end):
        grouped[day].append(holiday)
    return {
        day: HolidayInfo(
            name=" / ".join(holiday.name for holiday in holidays),
            tentative=any(holiday.tentative for holiday in holidays),
        )
        for day, holidays in grouped.items()
    }


def leave_map(employee, start, end):
    """Each day between two dates that is covered by approved leave."""
    leaves = LeaveRequest.objects.filter(
        employee=employee, status=RequestStatus.APPROVED, start_date__lte=end, end_date__gte=start
    )
    days = {}
    for leave in leaves:
        day = max(leave.start_date, start)
        while day <= min(leave.end_date, end):
            days[day] = leave
            day += ONE_DAY
    return days


def working_weekdays(terms):
    """The weekdays worked under a schedule version; Monday to Friday when there is none."""
    return terms.working_weekdays if terms else DEFAULT_WORKING_WEEKDAYS


def working_leave_days(employee, start, end, holidays=None):
    """The days between two dates that leave would actually use: working days that are not holidays."""
    if holidays is None:
        holidays = holiday_map(start, end)
    days = []
    day = start
    while day <= end:
        if day not in holidays and day.weekday() in working_weekdays(employee.terms_on(day)):
            days.append(day)
        day += ONE_DAY
    return days


@dataclass
class LeaveBalance:
    leave_type: str
    label: str
    allowance: int | None  # None when the leave type has no yearly limit
    taken: int  # working days of approved leave
    pending: int  # working days in requests still waiting

    @property
    def remaining(self):
        return None if self.allowance is None else self.allowance - self.taken

    @property
    def over_by(self):
        """Days by which approved plus waiting leave exceeds the allowance."""
        if self.allowance is None:
            return 0
        return max(0, self.taken + self.pending - self.allowance)


def leave_balances(employee, year, allowances=None):
    """Where an employee stands on each leave type for a calendar year."""
    if allowances is None:
        allowances = dict(LeaveAllowance.objects.values_list("leave_type", "days"))
    start, end = date(year, 1, 1), date(year, 12, 31)
    holidays = holiday_map(start, end)
    counted = (RequestStatus.APPROVED, RequestStatus.PENDING)
    days = {status: Counter() for status in counted}
    for leave in LeaveRequest.objects.filter(
        employee=employee, status__in=counted, start_date__lte=end, end_date__gte=start
    ):
        used = working_leave_days(employee, max(leave.start_date, start), min(leave.end_date, end), holidays)
        days[leave.status][leave.leave_type] += len(used)

    balances = []
    for value, label in LeaveRequest.LeaveType.choices:
        taken = days[RequestStatus.APPROVED][value]
        pending = days[RequestStatus.PENDING][value]
        if value in allowances or taken or pending:
            balances.append(LeaveBalance(value, label, allowances.get(value), taken, pending))
    return balances


def leave_balance_for(request, allowances=None):
    """The balance a leave request draws on: its leave type, in the year it starts."""
    balances = leave_balances(request.employee, request.start_date.year, allowances)
    return next((balance for balance in balances if balance.leave_type == request.leave_type), None)


# --- Day status and weekly targets ------------------------------------------


MIN_BREAK = timedelta(minutes=1)  # shorter gaps are a double tap, not a break


@dataclass(frozen=True)
class Break:
    """The gap between clocking out and clocking back in on the same day."""

    start: datetime
    end: datetime
    is_break = True

    @property
    def duration(self):
        return self.end - self.start


@dataclass
class Day:
    date: date
    working: bool
    in_scope: bool  # on or after the employee's start date
    is_today: bool
    is_future: bool
    terms: ScheduleVersion | None = None  # the schedule that applied that week
    holiday: HolidayInfo | None = None
    leave: LeaveRequest | None = None
    entries: list = field(default_factory=list)
    worked: timedelta = timedelta(0)
    muted: bool = False  # set by views for days outside the month being shown

    @property
    def expected(self):
        """With a schedule, a working day without work is an absence."""
        return self.terms is not None

    @property
    def has_missing_out(self):
        return any(entry.missing_out for entry in self.entries)

    @property
    def sequence(self):
        """The day's entries in order, with a Break wherever someone clocked out and back in."""
        items = []
        previous = None
        for entry in self.entries:
            if previous is not None and previous.clock_out and entry.clock_in - previous.clock_out >= MIN_BREAK:
                items.append(Break(previous.clock_out, entry.clock_in))
            items.append(entry)
            previous = entry
        return items

    @property
    def break_total(self):
        return sum((item.duration for item in self.sequence if item.is_break), timedelta(0))

    @property
    def is_credited(self):
        """A working day that does not count towards the weekly target."""
        return self.working and (not self.in_scope or bool(self.holiday) or bool(self.leave))

    @property
    def status(self):
        if not self.in_scope:
            return "before"
        if self.has_missing_out:
            return "missing"
        if self.holiday:
            return "holiday"
        if self.leave:
            return "leave"
        if self.entries:
            return "present"
        if not self.working:
            return "off"
        if self.is_future:
            return "future"
        if not self.expected:
            return "none"
        if self.is_today:
            return "today"
        return "absent"

    @property
    def label(self):
        status = self.status
        if status == "holiday":
            return self.holiday.name
        if status == "leave":
            return f"{self.leave.get_leave_type_display()} leave"
        return {
            "missing": "No clock-out",
            "present": "Present",
            "off": "Day off",
            "absent": "Absent",
        }.get(status, "")


@dataclass
class Week:
    start: date
    days: list
    worked: timedelta
    target: timedelta | None  # None when the employee has no schedule
    state: str  # met | short | progress | future | untracked | before
    terms: ScheduleVersion | None = None  # the schedule that applied that week

    @property
    def end(self):
        return self.start + timedelta(days=6)

    @property
    def difference(self):
        return None if self.target is None else self.worked - self.target

    @property
    def remaining(self):
        return None if self.target is None else max(self.target - self.worked, timedelta(0))

    @property
    def percent(self):
        if not self.target:
            return 100 if self.target is not None else 0
        return min(100, round(self.worked / self.target * 100))


def build_days(employee, start, end, now=None, holidays=None):
    now = now or timezone.now()
    current = today(now)
    if holidays is None:
        holidays = holiday_map(start, end)
    leaves = leave_map(employee, start, end)
    terms_by_week = {}

    def terms_for(day):
        monday = week_start(day)
        if monday not in terms_by_week:
            terms_by_week[monday] = employee.terms_on(monday)
        return terms_by_week[monday]

    entries_by_day = defaultdict(list)
    entries = TimeEntry.objects.filter(
        employee=employee, clock_in__gte=day_start(start), clock_in__lt=day_start(end + ONE_DAY)
    ).select_related("request")
    for entry in entries:
        entries_by_day[work_date(entry.clock_in)].append(entry)

    days = []
    day = start
    while day <= end:
        day_entries = entries_by_day.get(day, [])
        terms = terms_for(day)
        days.append(
            Day(
                date=day,
                working=day.weekday() in working_weekdays(terms),
                in_scope=day >= employee.start_date,
                is_today=day == current,
                is_future=day > current,
                terms=terms,
                holiday=holidays.get(day),
                leave=leaves.get(day),
                entries=day_entries,
                worked=sum((entry.worked(now) for entry in day_entries), timedelta(0)),
            )
        )
        day += ONE_DAY
    return days


def _week_target(terms, days):
    """The weekly hours, less a day's share for each holiday or leave day."""
    if terms is None:
        return None
    working = [day for day in days if day.working]
    if not working:
        return timedelta(0)
    counted = sum(1 for day in working if not day.is_credited)
    weekly_seconds = float(terms.weekly_hours) * 3600
    return timedelta(seconds=round(weekly_seconds * counted / len(working)))


def build_weeks(employee, start, end, now=None, holidays=None):
    """Whole Monday-to-Sunday weeks covering the dates from `start` to `end`."""
    now = now or timezone.now()
    current = today(now)
    first = week_start(start)
    last = week_start(end) + timedelta(days=6)
    days = build_days(employee, first, last, now=now, holidays=holidays)

    weeks = []
    for index in range(0, len(days), 7):
        week_days = days[index : index + 7]
        worked = sum((day.worked for day in week_days), timedelta(0))
        terms = week_days[0].terms
        target = _week_target(terms, week_days)
        if not any(day.in_scope for day in week_days):
            state = "before"
        elif week_days[0].date > current:
            state = "future"
        elif target is None:
            state = "untracked"
        elif worked >= target:
            state = "met"
        elif week_days[-1].date >= current:
            state = "progress"
        else:
            state = "short"
        weeks.append(
            Week(start=week_days[0].date, days=week_days, worked=worked, target=target, state=state, terms=terms)
        )
    return weeks


# --- Timeline ---------------------------------------------------------------

TIMELINE_DEFAULT_HOURS = (8, 18)  # the axis always covers at least this part of the day
TIMELINE_MAX_HOUR = 36  # room for work that runs past midnight


@dataclass
class Segment:
    """One mark on a timeline: a stretch of work, a break, or a clock-in with no clock-out."""

    kind: str  # work | live | break | missing
    start: datetime
    end: datetime | None  # None when there was no clock-out
    from_hour: float = 0.0  # hours since the attendance day began
    to_hour: float = 0.0
    left: str = "0"  # position and width along the axis, in percent
    width: str = "0"

    @property
    def value(self):
        """The headline of the tooltip."""
        return "No clock-out" if self.end is None else duration_text(self.end - self.start)

    @property
    def label(self):
        if self.kind == "missing":
            return f"Clocked in at {clock_text(self.start)}"
        if self.kind == "live":
            return f"Working now · since {clock_text(self.start)}"
        title = "Break" if self.kind == "break" else "Worked"
        return f"{title} · {clock_text(self.start)} – {clock_text(self.end)}"


@dataclass
class TimelineRow:
    label: str
    url: str | None
    day: Day
    segments: list

    def _in_words(self, separator):
        if not self.segments:
            return "Nothing recorded"
        return separator.join(f"{segment.label}: {segment.value}" for segment in self.segments)

    @property
    def summary(self):
        """The whole row in one sentence, for screen readers."""
        return self._in_words("; ")

    @property
    def summary_lines(self):
        """The same, one stretch per line, for the tooltip shown on keyboard focus."""
        return self._in_words("\n")


@dataclass
class Timeline:
    rows: list
    ticks: list  # one per hour: {"left", "label", "major"}
    now_left: str | None  # where the present moment falls, if today is shown
    kinds: set  # which kinds of segment appear, so the legend lists only those


def _hour_label(hour):
    hour %= 24
    if hour == 0:
        return "12 AM"
    if hour == 12:
        return "12 PM"
    return f"{hour} AM" if hour < 12 else f"{hour - 12} PM"


def build_timeline(rows, now=None):
    """Lays days out along one shared time-of-day axis.

    `rows` is a list of (label, url, Day): one bar each, so the same chart
    serves "every employee on one day" and "one employee across many days".
    """
    now = now or timezone.now()
    current = today(now)
    low, high = TIMELINE_DEFAULT_HOURS
    built = []
    for label, url, day in rows:
        origin = day_start(day.date)
        segments = []
        for item in day.sequence:
            if item.is_break:
                segment = Segment("break", item.start, item.end)
            elif item.clock_out:
                segment = Segment("work", item.clock_in, item.clock_out)
            elif item.missing_out:
                segment = Segment("missing", item.clock_in, None)
            else:
                segment = Segment("live", item.clock_in, max(now, item.clock_in))
            segment.from_hour = (segment.start - origin).total_seconds() / 3600
            segment.to_hour = ((segment.end or segment.start) - origin).total_seconds() / 3600
            low = min(low, math.floor(segment.from_hour))
            high = max(high, math.ceil(segment.to_hour))
            segments.append(segment)
        built.append(TimelineRow(label, url, day, segments))

    low = max(low, 0)
    high = min(high, TIMELINE_MAX_HOUR)
    span = high - low

    def position(hour):
        return (min(max(hour, low), high) - low) / span * 100

    for row in built:
        for segment in row.segments:
            segment.left = f"{position(segment.from_hour):.3f}"
            segment.width = f"{position(segment.to_hour) - position(segment.from_hour):.3f}"

    step = 1 if span <= 6 else 2 if span <= 14 else 3 if span <= 21 else 4
    ticks = [
        {
            "left": f"{position(hour):.3f}",
            "label": _hour_label(hour) if (hour - low) % step == 0 else "",
            "major": (hour - low) % (step * 2) == 0,  # the only labels shown on narrow screens
        }
        for hour in range(low, high + 1)
    ]
    now_left = None
    if any(row.day.date == current for row in built):
        now_hour = (now - day_start(current)).total_seconds() / 3600
        if low <= now_hour <= high:
            now_left = f"{position(now_hour):.3f}"
    kinds = {segment.kind for row in built for segment in row.segments}
    return Timeline(rows=built, ticks=ticks, now_left=now_left, kinds=kinds)


@dataclass
class MonthSummary:
    """One employee's attendance over a stretch of days, counted in days and hours."""

    employee: Employee
    terms: ScheduleVersion | None  # the schedule at the end of the period
    expected_days: int = 0  # working days that were neither a holiday nor leave
    present: int = 0
    missing: int = 0  # came in but never clocked out
    absent: int = 0
    leave: int = 0
    holidays: int = 0
    worked: timedelta = timedelta(0)
    target: timedelta | None = None  # None when there was no schedule at all

    @property
    def difference(self):
        return None if self.target is None else self.worked - self.target


def month_summary(employee, first, last, now=None, holidays=None):
    """Totals for the days from `first` to `last`. The target is each expected day's share of its week."""
    summary = MonthSummary(employee=employee, terms=employee.terms_on(last))
    for day in build_days(employee, first, last, now=now, holidays=holidays):
        if not day.in_scope:
            continue
        summary.worked += day.worked
        if day.terms and day.working and not day.is_credited:
            summary.expected_days += 1
            summary.target = (summary.target or timedelta(0)) + day.terms.daily_target
        elif day.terms and summary.target is None:
            summary.target = timedelta(0)
        status = day.status
        if status == "present":
            summary.present += 1
        elif status == "missing":
            summary.missing += 1
        elif status == "absent":
            summary.absent += 1
        elif status == "leave" and day.working:
            summary.leave += 1
        elif status == "holiday" and day.working:
            summary.holidays += 1
    if summary.target is not None:
        summary.target = timedelta(seconds=round(summary.target.total_seconds()))
    return summary


# --- Requests ---------------------------------------------------------------


def _overlapping_entries(employee, start, end, exclude_entry=None):
    """Entries that share any time with the span from `start` to `end`."""
    entries = TimeEntry.objects.filter(employee=employee, clock_in__lt=end).filter(
        Q(clock_out__gt=start)
        | Q(clock_out__isnull=True, missing_out=False)
        | Q(clock_out__isnull=True, missing_out=True, clock_in__gte=start)
    )
    if exclude_entry is not None:
        entries = entries.exclude(pk=exclude_entry.pk)
    return entries


def validate_entry_times(employee, clock_in, clock_out, exclude_entry=None, now=None):
    """The rules any stretch of recorded time must meet, however it gets entered."""
    now = now or timezone.now()
    if clock_out <= clock_in:
        raise ValidationError("The clock-out time must be after the clock-in time.")
    if clock_out > now:
        raise ValidationError("You can only log time that has already passed.")
    if clock_out - clock_in > timedelta(hours=MAX_ENTRY_HOURS):
        raise ValidationError(f"That is more than {MAX_ENTRY_HOURS} hours. Check the times.")
    if _overlapping_entries(employee, clock_in, clock_out, exclude_entry=exclude_entry).exists():
        raise ValidationError("This overlaps time that is already recorded.")


def validate_missing_time(employee, clock_in, clock_out, entry=None, exclude_request=None, now=None):
    if entry is not None and (entry.employee_id != employee.pk or not entry.missing_out):
        raise ValidationError("That entry is no longer missing its clock-out.")
    validate_entry_times(employee, clock_in, clock_out, exclude_entry=entry, now=now)
    pending = MissingTimeRequest.objects.filter(
        employee=employee, status=RequestStatus.PENDING, clock_in__lt=clock_out, clock_out__gt=clock_in
    )
    if exclude_request is not None:
        pending = pending.exclude(pk=exclude_request.pk)
    if pending.exists():
        raise ValidationError("There is already a pending request covering this time.")


def validate_leave(employee, start, end, exclude_request=None):
    if end < start:
        raise ValidationError("The last day cannot be before the first day.")
    if (end - start).days >= MAX_LEAVE_DAYS:
        raise ValidationError(f"A single request can cover at most {MAX_LEAVE_DAYS} days.")
    clashes = LeaveRequest.objects.filter(
        employee=employee,
        status__in=[RequestStatus.PENDING, RequestStatus.APPROVED],
        start_date__lte=end,
        end_date__gte=start,
    )
    if exclude_request is not None:
        clashes = clashes.exclude(pk=exclude_request.pk)
    if clashes.exists():
        raise ValidationError("These dates overlap another leave request.")


def _apply_missing_time(request):
    """Write an approved missing-time request into the attendance record."""
    validate_missing_time(
        request.employee, request.clock_in, request.clock_out, entry=request.entry, exclude_request=request
    )
    if request.entry is not None:
        entry = request.entry
        entry.clock_out = request.clock_out
        entry.missing_out = False
        entry.source = TimeEntry.Source.ADJUSTED
        entry.request = request
        entry.save()
    else:
        TimeEntry.objects.create(
            employee=request.employee,
            clock_in=request.clock_in,
            clock_out=request.clock_out,
            source=TimeEntry.Source.MANUAL,
            request=request,
        )


@transaction.atomic
def decide(request, admin, approve, note=""):
    """Approve or reject a pending request. Raises ValidationError if it can't be applied."""
    request = type(request).objects.select_for_update().get(pk=request.pk)
    if not request.is_pending:
        raise ValidationError("This request has already been dealt with.")
    if approve:
        if isinstance(request, MissingTimeRequest):
            _apply_missing_time(request)
        else:
            validate_leave(request.employee, request.start_date, request.end_date, exclude_request=request)
    request.status = RequestStatus.APPROVED if approve else RequestStatus.REJECTED
    request.decided_by = admin
    request.decided_at = timezone.now()
    request.decision_note = note.strip()
    request.save()
    return request


@transaction.atomic
def correct_entry(admin, employee, clock_in, clock_out, reason, entry=None):
    """An admin adds a time entry, or sets the times of an existing one. Always leaves a trail."""
    validate_entry_times(employee, clock_in, clock_out, exclude_entry=entry)
    correction = Correction(
        employee=employee, admin=admin, reason=reason, new_clock_in=clock_in, new_clock_out=clock_out
    )
    if entry is None:
        entry = TimeEntry(employee=employee)
        correction.action = Correction.Action.ADDED
    else:
        correction.action = Correction.Action.CHANGED
        correction.old_clock_in = entry.clock_in
        correction.old_clock_out = entry.clock_out
    entry.clock_in = clock_in
    entry.clock_out = clock_out
    entry.missing_out = False
    entry.source = TimeEntry.Source.ADMIN
    entry.note = reason
    entry.save()
    correction.entry = entry
    correction.save()
    return correction


@transaction.atomic
def remove_entry(admin, entry, reason):
    """An admin deletes a time entry. The trail keeps what it said."""
    correction = Correction.objects.create(
        employee=entry.employee,
        admin=admin,
        action=Correction.Action.REMOVED,
        old_clock_in=entry.clock_in,
        old_clock_out=entry.clock_out,
        reason=reason,
    )
    entry.delete()
    return correction


@transaction.atomic
def cancel(request, by):
    """Withdraw a request. Employees can cancel while pending; admins can also cancel approved leave."""
    request = type(request).objects.select_for_update().get(pk=request.pk)
    cancellable = request.is_pending or (
        by.is_staff and isinstance(request, LeaveRequest) and request.status == RequestStatus.APPROVED
    )
    if not cancellable:
        raise ValidationError("This request can no longer be cancelled.")
    if request.status == RequestStatus.APPROVED:
        request.decided_by = by
        request.decided_at = timezone.now()
    request.status = RequestStatus.CANCELLED
    request.save()
    return request
