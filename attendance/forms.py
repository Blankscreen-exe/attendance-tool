from datetime import datetime, timedelta
from decimal import Decimal

from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, SetPasswordForm
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone

from . import lockout, services
from .models import WEEKDAY_FIELDS, Employee, Holiday, LeaveAllowance, LeaveRequest, Schedule


def date_input():
    return forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})


def time_input():
    return forms.TimeInput(format="%H:%M", attrs={"type": "time"})


class StyledFormMixin:
    """Gives every widget the CSS class the stylesheet expects."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for form_field in self.fields.values():
            widget = form_field.widget
            css = "checkbox" if isinstance(widget, forms.CheckboxInput) else "input"
            widget.attrs["class"] = f"{widget.attrs.get('class', '')} {css}".strip()


class LoginForm(StyledFormMixin, AuthenticationForm):
    def clean(self):
        username = self.cleaned_data.get("username")
        self._refuse_if_locked(username)
        try:
            return super().clean()
        except forms.ValidationError:
            self._refuse_if_locked(username)  # this attempt may have been the one that locked it
            raise

    def _refuse_if_locked(self, username):
        until = lockout.locked_until(username)
        if until:
            minutes = max(1, round((until - timezone.now()).total_seconds() / 60))
            plural = "" if minutes == 1 else "s"
            raise forms.ValidationError(
                f"Too many wrong passwords. Try again in {minutes} minute{plural}, "
                "or ask an admin to set a new password."
            )


class OwnPasswordForm(StyledFormMixin, PasswordChangeForm):
    pass


class ResetPasswordForm(StyledFormMixin, SetPasswordForm):
    pass


# --- Employee requests ------------------------------------------------------


class MissingTimeForm(StyledFormMixin, forms.Form):
    date = forms.DateField(widget=date_input())
    time_in = forms.TimeField(label="Clock-in time", widget=time_input())
    time_out = forms.TimeField(
        label="Clock-out time",
        widget=time_input(),
        help_text="If this is earlier than the clock-in time, it is taken as the next day.",
    )
    reason = forms.CharField(
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Why the time was not recorded with the clock button.",
    )

    def __init__(self, *args, employee, entry=None, **kwargs):
        """Pass `entry` to ask only for the clock-out of an entry that is missing one."""
        self.employee = employee
        self.entry = entry
        super().__init__(*args, **kwargs)
        if entry is not None:
            del self.fields["date"]
            del self.fields["time_in"]

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        if self.entry is not None:
            clock_in = self.entry.clock_in
            day = timezone.localtime(clock_in).date()
        else:
            day = cleaned["date"]
            clock_in = timezone.make_aware(datetime.combine(day, cleaned["time_in"]))
        clock_out = timezone.make_aware(datetime.combine(day, cleaned["time_out"]))
        if clock_out <= clock_in:
            clock_out += timedelta(days=1)
        services.validate_missing_time(self.employee, clock_in, clock_out, entry=self.entry)
        cleaned["clock_in"] = clock_in
        cleaned["clock_out"] = clock_out
        return cleaned


class LeaveForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = LeaveRequest
        fields = ["leave_type", "start_date", "end_date", "reason"]
        widgets = {
            "start_date": date_input(),
            "end_date": date_input(),
            "reason": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, employee, **kwargs):
        self.employee = employee
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        # A last day before the first is reported by the model's own constraint.
        if start and end and end >= start:
            services.validate_leave(self.employee, start, end)
        return cleaned


# --- Admin ------------------------------------------------------------------


class EntryForm(StyledFormMixin, forms.Form):
    """An admin adds a time entry, or corrects the times of an existing one."""

    date = forms.DateField(widget=date_input())
    time_in = forms.TimeField(label="Clock-in time", widget=time_input())
    time_out = forms.TimeField(
        label="Clock-out time",
        widget=time_input(),
        help_text="If this is earlier than the clock-in time, it is taken as the next day.",
    )
    reason = forms.CharField(
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Kept with the entry and shown to the employee.",
    )

    def __init__(self, *args, employee, entry=None, **kwargs):
        self.employee = employee
        self.entry = entry
        if entry is not None:
            kwargs["initial"] = {
                "date": timezone.localtime(entry.clock_in).date(),
                "time_in": self._to_minute(entry.clock_in),
                "time_out": self._to_minute(entry.clock_out) if entry.clock_out else None,
            }
        super().__init__(*args, **kwargs)

    @staticmethod
    def _to_minute(moment):
        return timezone.localtime(moment).time().replace(second=0, microsecond=0)

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        day = cleaned["date"]
        clock_in = timezone.make_aware(datetime.combine(day, cleaned["time_in"]))
        clock_out = timezone.make_aware(datetime.combine(day, cleaned["time_out"]))
        if clock_out <= clock_in:
            clock_out += timedelta(days=1)
        # A clock-in that was not edited keeps its exact recorded second.
        untouched = self.entry is not None and (day, cleaned["time_in"]) == (
            self.initial["date"],
            self.initial["time_in"],
        )
        if untouched:
            clock_in = self.entry.clock_in
        services.validate_entry_times(self.employee, clock_in, clock_out, exclude_entry=self.entry)
        cleaned["clock_in"] = clock_in
        cleaned["clock_out"] = clock_out
        return cleaned


class RemoveEntryForm(StyledFormMixin, forms.Form):
    reason = forms.CharField(
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Kept in the correction history and shown to the employee.",
    )


class EmployeeForm(StyledFormMixin, forms.ModelForm):
    schedule = forms.ModelChoiceField(
        queryset=Schedule.objects.all(),
        required=False,
        empty_label="No schedule",
        help_text="Sets the weekly hours target. Leave empty to record hours without a target.",
    )
    schedule_from = forms.DateField(
        label="Schedule applies from",
        required=False,
        widget=date_input(),
        help_text="Only used when you change the schedule. It starts from the Monday of that week; "
        "earlier weeks stay on the old one.",
    )

    class Meta:
        model = Employee
        fields = ["username", "first_name", "last_name", "email", "start_date", "is_staff", "is_active"]
        labels = {"is_staff": "Admin", "is_active": "Active"}
        help_texts = {
            "username": "Used to sign in.",
            "email": "Optional. Notices about requests and corrections are sent here.",
            "is_staff": "Admins see everyone's attendance and decide requests. They do not clock in.",
            "is_active": "",
        }
        widgets = {"start_date": date_input()}

    field_order = ["username", "first_name", "last_name", "email", "schedule", "schedule_from", "start_date"]

    def __init__(self, *args, acting_user=None, **kwargs):
        self.acting_user = acting_user
        super().__init__(*args, **kwargs)
        self.is_new = self.instance.pk is None
        if self.is_new:
            # A new employee is simply on their schedule from the start.
            del self.fields["schedule_from"]
            self.assigned = None
        else:
            self.assigned = self.instance.assigned_schedule
            self.fields["schedule"].initial = self.assigned
            self.fields["schedule_from"].initial = services.week_start(services.today())

    def clean(self):
        cleaned = super().clean()
        editing_self = self.instance.pk and self.acting_user and self.instance.pk == self.acting_user.pk
        if editing_self and not (cleaned.get("is_staff") and cleaned.get("is_active")):
            raise forms.ValidationError("You cannot remove your own admin access or deactivate yourself.")
        if cleaned.get("is_staff"):
            cleaned["schedule"] = None  # admins keep no attendance, so a schedule means nothing
        return cleaned

    def save(self):
        employee = super().save()
        schedule = self.cleaned_data.get("schedule")
        if self.is_new:
            if schedule:
                services.assign_schedule(employee, schedule, services.BEGINNING)
        elif schedule != self.assigned:
            services.assign_schedule(employee, schedule, self.cleaned_data.get("schedule_from") or services.today())
        return employee


class EmployeeCreateForm(EmployeeForm):
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Give this to the employee. They can change it after signing in.",
    )

    field_order = ["username", "password", *EmployeeForm.field_order[1:]]

    def clean_password(self):
        password = self.cleaned_data["password"]
        validate_password(password)
        return password

    def save(self):
        self.instance.set_password(self.cleaned_data["password"])
        return super().save()


class ScheduleForm(StyledFormMixin, forms.Form):
    """Creates a schedule, or changes one from a chosen week onward."""

    name = forms.CharField(max_length=100)
    weekly_hours = forms.DecimalField(
        label="Hours per week",
        min_value=Decimal("0.5"),
        max_value=Decimal("112"),
        max_digits=5,
        decimal_places=2,
        help_text="Holidays and approved leave reduce a week's target pro rata.",
    )
    monday = forms.BooleanField(required=False, initial=True)
    tuesday = forms.BooleanField(required=False, initial=True)
    wednesday = forms.BooleanField(required=False, initial=True)
    thursday = forms.BooleanField(required=False, initial=True)
    friday = forms.BooleanField(required=False, initial=True)
    saturday = forms.BooleanField(required=False)
    sunday = forms.BooleanField(required=False)
    effective_from = forms.DateField(
        label="Changes apply from",
        widget=date_input(),
        help_text="New hours or days start from the Monday of that week. Earlier weeks keep what they had. "
        "To correct a mistake, pick the date the mistake began.",
    )

    def __init__(self, *args, schedule=None, **kwargs):
        self.schedule = schedule
        self.latest = schedule.latest if schedule else None
        if self.latest:
            this_week = services.week_start(services.today())
            kwargs["initial"] = {
                "name": schedule.name,
                "weekly_hours": f"{self.latest.weekly_hours.normalize():f}",  # "40", not "40.00"
                "effective_from": max(this_week, self.latest.effective_from),
                **{day: getattr(self.latest, day) for day in WEEKDAY_FIELDS},
            }
        super().__init__(*args, **kwargs)
        if not self.latest:
            del self.fields["effective_from"]  # a new schedule has no earlier weeks to protect

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        taken = Schedule.objects.filter(name__iexact=name)
        if self.schedule:
            taken = taken.exclude(pk=self.schedule.pk)
        if taken.exists():
            raise forms.ValidationError("There is already a schedule with this name.")
        return name

    def clean(self):
        cleaned = super().clean()
        if not any(cleaned.get(day) for day in WEEKDAY_FIELDS):
            raise forms.ValidationError("Pick at least one working day.")
        return cleaned

    def save(self):
        cleaned = self.cleaned_data
        schedule = self.schedule or Schedule()
        schedule.name = cleaned["name"]
        schedule.save()
        weekdays = {day: cleaned[day] for day in WEEKDAY_FIELDS}
        if not self.latest:
            services.set_schedule_terms(schedule, cleaned["weekly_hours"], weekdays, services.BEGINNING)
        else:
            unchanged = (
                self.latest.weekly_hours == cleaned["weekly_hours"]
                and all(getattr(self.latest, day) == weekdays[day] for day in WEEKDAY_FIELDS)
                and services.week_start(cleaned["effective_from"]) == self.initial["effective_from"]
            )
            if not unchanged:
                services.set_schedule_terms(schedule, cleaned["weekly_hours"], weekdays, cleaned["effective_from"])
        return schedule


def _allowance_field(label):
    return forms.IntegerField(label=label, required=False, min_value=0, max_value=366)


class LeaveAllowanceForm(StyledFormMixin, forms.Form):
    """Days per leave type that each employee may take in a year. Empty means no limit."""

    # One field per LeaveRequest.LeaveType value.
    annual = _allowance_field("Annual")
    casual = _allowance_field("Casual")
    sick = _allowance_field("Sick")
    unpaid = _allowance_field("Unpaid")
    other = _allowance_field("Other")

    def __init__(self, *args, **kwargs):
        kwargs["initial"] = dict(LeaveAllowance.objects.values_list("leave_type", "days"))
        super().__init__(*args, **kwargs)

    def save(self):
        for leave_type in LeaveRequest.LeaveType.values:
            days = self.cleaned_data.get(leave_type)
            if days is None:
                LeaveAllowance.objects.filter(leave_type=leave_type).delete()
            else:
                LeaveAllowance.objects.update_or_create(leave_type=leave_type, defaults={"days": days})


class TestEmailForm(StyledFormMixin, forms.Form):
    to = forms.EmailField(label="Send a test email to")


class HolidayForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Holiday
        fields = ["date", "name", "recurs_yearly", "tentative"]
        widgets = {"date": date_input()}


class ReviewFilterForm(StyledFormMixin, forms.Form):
    start = forms.DateField(label="From", widget=date_input())
    end = forms.DateField(label="To", widget=date_input())
    employee = forms.ModelChoiceField(
        queryset=Employee.objects.filter(is_staff=False), required=False, empty_label="All active employees"
    )
