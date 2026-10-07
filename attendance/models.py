from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from . import dates

WEEKDAY_FIELDS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
WEEKDAY_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def in_force(items, day, fall_back_to_first=False):
    """From items sorted by `effective_from`, the one that applies on `day`."""
    chosen = None
    for item in items:
        if item.effective_from > day:
            break
        chosen = item
    if chosen is None and fall_back_to_first and items:
        return items[0]
    return chosen


class Schedule(models.Model):
    """A named working pattern, e.g. "BD".

    Its hours and days are kept as dated versions, so changing them from one
    week onward leaves earlier weeks judged by what applied at the time.
    """

    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def version_list(self):
        if getattr(self, "_version_list", None) is None:
            self._version_list = sorted(self.versions.all(), key=lambda version: version.effective_from)
        return self._version_list

    def forget_history(self):
        self._version_list = None
        getattr(self, "_prefetched_objects_cache", {}).pop("versions", None)

    def version_on(self, day):
        return in_force(self.version_list(), day, fall_back_to_first=True)

    @property
    def current(self):
        return self.version_on(dates.today())

    @property
    def latest(self):
        versions = self.version_list()
        return versions[-1] if versions else None

    @property
    def upcoming(self):
        """Versions that start after this week."""
        current = self.current
        return [v for v in self.version_list() if current and v.effective_from > current.effective_from]

    @property
    def past(self):
        """Versions that have been replaced, most recent first."""
        current = self.current
        versions = self.version_list()
        earlier = [v for v in versions if current and v.effective_from < current.effective_from]
        # Each one applied until the day before the next version began.
        for version in earlier:
            version.until = versions[versions.index(version) + 1].effective_from - timedelta(days=1)
        return earlier[::-1]


class ScheduleVersion(models.Model):
    """A schedule's weekly hours and working days from one Monday onward."""

    schedule = models.ForeignKey(Schedule, on_delete=models.CASCADE, related_name="versions")
    effective_from = models.DateField()  # always a Monday
    weekly_hours = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.5")), MaxValueValidator(Decimal("112"))],
        help_text="Hours to complete each week. Holidays and approved leave reduce it pro rata.",
    )
    monday = models.BooleanField(default=True)
    tuesday = models.BooleanField(default=True)
    wednesday = models.BooleanField(default=True)
    thursday = models.BooleanField(default=True)
    friday = models.BooleanField(default=True)
    saturday = models.BooleanField(default=False)
    sunday = models.BooleanField(default=False)

    class Meta:
        ordering = ["effective_from"]
        constraints = [
            models.UniqueConstraint(fields=["schedule", "effective_from"], name="one_version_per_schedule_week")
        ]

    def __str__(self):
        return f"{self.schedule.name}: {self.hours_label}, {self.days_label} from {self.effective_from}"

    @property
    def name(self):
        return self.schedule.name

    @property
    def terms(self):
        """What the version says, for telling whether two versions differ."""
        return (self.weekly_hours, self.working_weekdays)

    @property
    def working_weekdays(self):
        return frozenset(i for i, name in enumerate(WEEKDAY_FIELDS) if getattr(self, name))

    @property
    def hours_label(self):
        return f"{self.weekly_hours.normalize():f}h"

    @property
    def daily_target(self):
        """The share of the weekly hours that falls on one working day."""
        days = len(self.working_weekdays)
        return timedelta(hours=float(self.weekly_hours) / days) if days else timedelta(0)

    @property
    def days_label(self):
        days = sorted(self.working_weekdays)
        if not days:
            return "No days"
        if len(days) > 2 and days == list(range(days[0], days[-1] + 1)):
            return f"{WEEKDAY_SHORT[days[0]]}–{WEEKDAY_SHORT[days[-1]]}"
        return ", ".join(WEEKDAY_SHORT[day] for day in days)


class Employee(AbstractUser):
    """A login. Admins have `is_staff` set and keep no attendance of their own."""

    start_date = models.DateField(
        default=timezone.localdate, help_text="Attendance is tracked from this date."
    )

    class Meta(AbstractUser.Meta):
        verbose_name = "employee"
        verbose_name_plural = "employees"
        ordering = ["first_name", "last_name", "username"]

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        return self.get_full_name() or self.username

    def assignment_list(self):
        if getattr(self, "_assignment_list", None) is None:
            prefetched = "assignments" in getattr(self, "_prefetched_objects_cache", {})
            found = self.assignments.all() if prefetched else self.assignments.select_related("schedule")
            self._assignment_list = sorted(found, key=lambda assignment: assignment.effective_from)
        return self._assignment_list

    def forget_history(self):
        self._assignment_list = None
        getattr(self, "_prefetched_objects_cache", {}).pop("assignments", None)

    def terms_on(self, day):
        """The schedule version that applies to this employee on a day, or None."""
        assignment = in_force(self.assignment_list(), day)
        if assignment is None or assignment.schedule is None:
            return None
        return assignment.schedule.version_on(day)

    @property
    def terms(self):
        """The schedule version in force this week, or None."""
        return self.terms_on(dates.today())

    @property
    def assigned_schedule(self):
        """The schedule from the most recent assignment, which may start in the future."""
        assignments = self.assignment_list()
        return assignments[-1].schedule if assignments else None


class ScheduleAssignment(models.Model):
    """The schedule an employee is on from one Monday onward. Empty means no schedule from then."""

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="assignments")
    schedule = models.ForeignKey(
        Schedule, null=True, blank=True, on_delete=models.PROTECT, related_name="assignments"
    )
    effective_from = models.DateField()  # always a Monday

    class Meta:
        ordering = ["effective_from"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "effective_from"], name="one_assignment_per_employee_week")
        ]

    def __str__(self):
        return f"{self.employee}: {self.schedule or 'no schedule'} from {self.effective_from}"


class TimeEntry(models.Model):
    """One stretch of work. An employee has at most one open entry at a time."""

    class Source(models.TextChoices):
        PUNCH = "punch", "Punch"
        MANUAL = "manual", "Manual"  # both times came from an approved request
        ADJUSTED = "adjusted", "Adjusted"  # clock-out came from an approved request
        ADMIN = "admin", "Admin correction"  # added or changed directly by an admin

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="entries")
    clock_in = models.DateTimeField()
    clock_out = models.DateTimeField(null=True, blank=True)
    # Set when the day ended without a clock-out. The entry then counts zero
    # hours until an approved request supplies the missing time.
    missing_out = models.BooleanField(default=False)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.PUNCH)
    note = models.TextField(blank=True, max_length=1000)  # the reason for the latest admin correction
    request = models.ForeignKey(
        "MissingTimeRequest", null=True, blank=True, on_delete=models.SET_NULL, related_name="entries"
    )

    class Meta:
        ordering = ["clock_in"]
        verbose_name_plural = "time entries"
        indexes = [models.Index(fields=["employee", "clock_in"])]
        constraints = [
            models.UniqueConstraint(
                fields=["employee"],
                condition=Q(clock_out__isnull=True, missing_out=False),
                name="one_open_entry_per_employee",
            ),
            models.CheckConstraint(
                condition=Q(clock_out__isnull=True) | Q(clock_out__gte=F("clock_in")),
                name="clock_out_not_before_clock_in",
            ),
        ]

    is_break = False  # lets templates tell entries from the breaks listed between them

    def __str__(self):
        return f"{self.employee} {self.clock_in:%Y-%m-%d %H:%M}"

    @property
    def is_open(self):
        return self.clock_out is None and not self.missing_out

    @property
    def has_pending_fix(self):
        return self.fix_requests.filter(status=RequestStatus.PENDING).exists()

    def worked(self, now=None):
        if self.clock_out:
            return self.clock_out - self.clock_in
        if self.missing_out:
            return timedelta(0)
        return max((now or timezone.now()) - self.clock_in, timedelta(0))


class Correction(models.Model):
    """The trail of an admin adding, changing or removing a time entry, with their reason."""

    class Action(models.TextChoices):
        ADDED = "added", "Added"
        CHANGED = "changed", "Changed"
        REMOVED = "removed", "Removed"

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="corrections")
    entry = models.ForeignKey(
        TimeEntry, null=True, blank=True, on_delete=models.SET_NULL, related_name="corrections"
    )
    admin = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=10, choices=Action.choices)
    old_clock_in = models.DateTimeField(null=True, blank=True)
    old_clock_out = models.DateTimeField(null=True, blank=True)
    new_clock_in = models.DateTimeField(null=True, blank=True)
    new_clock_out = models.DateTimeField(null=True, blank=True)
    reason = models.TextField(max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_action_display()} entry for {self.employee}"


class RequestStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"


class BaseRequest(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="%(class)ss")
    reason = models.TextField(max_length=1000)
    status = models.CharField(
        max_length=10, choices=RequestStatus.choices, default=RequestStatus.PENDING, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    decision_note = models.TextField(blank=True, max_length=1000)

    class Meta:
        abstract = True
        ordering = ["-created_at"]

    @property
    def is_pending(self):
        return self.status == RequestStatus.PENDING


class MissingTimeRequest(BaseRequest):
    """An employee's claim for time they worked but did not punch."""

    kind = "missing-time"

    clock_in = models.DateTimeField()
    clock_out = models.DateTimeField()
    # Set when the request supplies the clock-out for an existing entry
    # instead of adding a whole new one.
    entry = models.ForeignKey(
        TimeEntry, null=True, blank=True, on_delete=models.SET_NULL, related_name="fix_requests"
    )

    def __str__(self):
        return f"{self.employee}: missing time {self.clock_in:%Y-%m-%d}"

    @property
    def duration(self):
        return self.clock_out - self.clock_in


class LeaveRequest(BaseRequest):
    class LeaveType(models.TextChoices):
        ANNUAL = "annual", "Annual"
        CASUAL = "casual", "Casual"
        SICK = "sick", "Sick"
        UNPAID = "unpaid", "Unpaid"
        OTHER = "other", "Other"

    kind = "leave"

    leave_type = models.CharField(max_length=10, choices=LeaveType.choices, default=LeaveType.CASUAL)
    start_date = models.DateField("first day")
    end_date = models.DateField("last day")

    class Meta(BaseRequest.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__gte=F("start_date")),
                name="leave_end_not_before_start",
                violation_error_message="The last day cannot be before the first day.",
            ),
        ]

    def __str__(self):
        return f"{self.employee}: {self.get_leave_type_display()} leave {self.start_date}"

    @property
    def calendar_days(self):
        return (self.end_date - self.start_date).days + 1


class LeaveAllowance(models.Model):
    """Days of one leave type that each employee may take in a calendar year.

    A leave type with no row here has no limit.
    """

    leave_type = models.CharField(max_length=10, choices=LeaveRequest.LeaveType.choices, unique=True)
    days = models.PositiveSmallIntegerField()

    def __str__(self):
        return f"{self.get_leave_type_display()}: {self.days} days a year"


class Holiday(models.Model):
    date = models.DateField()
    name = models.CharField(max_length=120)
    recurs_yearly = models.BooleanField(
        "repeats every year", default=False, help_text="For holidays that fall on the same date each year."
    )
    tentative = models.BooleanField(
        default=False,
        help_text="For dates that depend on moon sighting. Untick once the date is confirmed.",
    )

    class Meta:
        ordering = ["date", "name"]
        constraints = [models.UniqueConstraint(fields=["date", "name"], name="unique_holiday_date_name")]

    def __str__(self):
        return f"{self.name} ({self.date})"


class LoginThrottle(models.Model):
    """Counts wrong passwords per username so sign-in can be locked for a while."""

    username = models.CharField(max_length=150, unique=True)  # lower-cased; the account may not exist
    failures = models.PositiveSmallIntegerField(default=0)
    window_start = models.DateTimeField()
    locked_until = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.username
