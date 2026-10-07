"""Pages for admins: review attendance, decide requests, and set things up."""

import calendar
import csv
from collections import Counter
from datetime import date, timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, ProtectedError, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import lockout, notifications, pk_holidays, services
from .forms import (
    EmployeeCreateForm,
    EmployeeForm,
    EntryForm,
    HolidayForm,
    LeaveAllowanceForm,
    RemoveEntryForm,
    ResetPasswordForm,
    ReviewFilterForm,
    ScheduleForm,
)
from .models import (
    Employee,
    Holiday,
    LeaveAllowance,
    LeaveRequest,
    MissingTimeRequest,
    RequestStatus,
    Schedule,
    TimeEntry,
)
from .views import get_request_or_404, month_context, parse_month

MAX_REVIEW_DAYS = 371  # 53 weeks
COUNTED_STATES = ("met", "short", "progress")
SCHEDULE_HISTORY = "assignments__schedule__versions"  # everything needed to work out anyone's schedule


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapped


def tracked_employees():
    """Active people whose attendance is kept. Admins are owners and never clock in."""
    return Employee.objects.filter(is_active=True, is_staff=False).prefetch_related(SCHEDULE_HISTORY)


def form_page(request, form, title, cancel_url, submit_label="Save", intro="", danger=False):
    return render(
        request,
        "attendance/form_page.html",
        {
            "form": form,
            "title": title,
            "intro": intro,
            "submit_label": submit_label,
            "cancel_url": cancel_url,
            "danger": danger,
        },
    )


# --- Dashboard --------------------------------------------------------------


def _holiday_notices(current):
    notices = []
    soon = services.holiday_occurrences(current, current + timedelta(days=45))
    tentative = sorted({holiday.name for _, holiday in soon if holiday.tentative})
    if tentative:
        notices.append(
            f"{', '.join(tentative)}: the dates are still tentative. "
            "Confirm them on the Holidays page once they are announced."
        )
    for year in sorted({current.year, (current + timedelta(days=60)).year}):
        if not Holiday.objects.filter(recurs_yearly=False, date__year=year).exists():
            notices.append(
                f"No one-off holidays (such as Eid) are set for {year}. Add them on the Holidays page."
            )
    return notices


@admin_required
def dashboard(request):
    now = timezone.now()
    current = services.today(now)
    first = services.week_start(current)
    holidays = services.holiday_map(first, first + timedelta(days=6))
    rows = []
    for employee in tracked_employees():
        week = services.build_weeks(employee, current, current, now=now, holidays=holidays)[0]
        day = next(day for day in week.days if day.date == current)
        open_entry = next((entry for entry in day.entries if entry.is_open), None)
        last_entry = day.entries[-1] if day.entries else None
        rows.append(
            {"employee": employee, "week": week, "day": day, "open_entry": open_entry, "last_entry": last_entry}
        )
    return render(
        request,
        "attendance/manage/dashboard.html",
        {
            "today": current,
            "rows": rows,
            "clocked_in": sum(1 for row in rows if row["open_entry"]),
            "notices": _holiday_notices(current),
        },
    )


# --- Review and export ------------------------------------------------------


def _review(request):
    """The filtered review data shared by the page and both exports."""
    current = services.today()
    start, end, employee = current.replace(day=1), current, None
    form = ReviewFilterForm(
        request.GET if "start" in request.GET else None, initial={"start": start, "end": end}
    )
    if form.is_bound and form.is_valid():
        start, end, employee = (form.cleaned_data[key] for key in ("start", "end", "employee"))
    if end < start:
        start, end = end, start
    if (end - start).days > MAX_REVIEW_DAYS:
        end = start + timedelta(days=MAX_REVIEW_DAYS)
        messages.warning(request, "The range was shortened to 53 weeks.")

    employees = [employee] if employee else tracked_employees()
    first = services.week_start(start)
    last = services.week_start(end) + timedelta(days=6)
    holidays = services.holiday_map(first, last)

    rows = []
    for person in employees:
        weeks = services.build_weeks(person, start, end, holidays=holidays)
        counted = [week for week in weeks if week.state in COUNTED_STATES]
        rows.append(
            {
                "employee": person,
                "weeks": weeks,
                "worked": sum((week.worked for week in weeks), timedelta(0)),
                "target": sum((week.target for week in counted), timedelta(0)) if counted else None,
                "met": sum(1 for week in weeks if week.state == "met"),
                "short": sum(1 for week in weeks if week.state == "short"),
            }
        )
    week_starts = [first + timedelta(weeks=index) for index in range((last - first).days // 7 + 1)]
    return {"form": form, "start": first, "end": last, "rows": rows, "week_starts": week_starts}


@admin_required
def review(request):
    context = _review(request)
    context["query"] = request.GET.urlencode()
    return render(request, "attendance/manage/review.html", context)


def _hours(value):
    return f"{value.total_seconds() / 3600:.2f}"


def _clock(moment):
    return timezone.localtime(moment).strftime("%H:%M") if moment else ""


def _csv_safe(value):
    """Stops spreadsheet programs from running employee-written text as a formula."""
    text = str(value)
    return f"'{text}" if text[:1] in ("=", "+", "-", "@") else text


def _csv_response(filename, header, rows):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("\ufeff")  # byte order mark, so Excel recognises UTF-8
    writer = csv.writer(response)
    writer.writerow(header)
    writer.writerows(rows)
    return response


@admin_required
def export_daily(request):
    data = _review(request)
    rows = []
    for row in data["rows"]:
        employee = row["employee"]
        for week in row["weeks"]:
            for day in week.days:
                if day.status in ("before", "future", "none"):
                    continue
                punches = "; ".join(
                    f"{_clock(entry.clock_in)}-{_clock(entry.clock_out) or '?'}" for entry in day.entries
                )
                reasons = "; ".join(
                    entry.note or entry.request.reason for entry in day.entries if entry.note or entry.request
                )
                rows.append(
                    [
                        _csv_safe(employee.display_name),
                        _csv_safe(employee.username),
                        day.date.isoformat(),
                        day.date.strftime("%a"),
                        "Not in yet" if day.status == "today" else _csv_safe(day.label),
                        _hours(day.worked),
                        punches,
                        _csv_safe(reasons),
                    ]
                )
    header = ["Employee", "Username", "Date", "Day", "Status", "Hours", "Punches", "Reason for manual entries"]
    return _csv_response(f"attendance-daily-{data['start']}-to-{data['end']}.csv", header, rows)


@admin_required
def export_weekly(request):
    results = {
        "met": "Completed",
        "short": "Short",
        "progress": "In progress",
        "future": "Upcoming",
        "untracked": "No target",
    }
    data = _review(request)
    rows = []
    for row in data["rows"]:
        employee = row["employee"]
        for week in row["weeks"]:
            if week.state == "before":
                continue
            rows.append(
                [
                    _csv_safe(employee.display_name),
                    _csv_safe(employee.username),
                    week.start.isoformat(),
                    week.end.isoformat(),
                    _hours(week.worked),
                    _hours(week.target) if week.target is not None else "",
                    _hours(week.difference) if week.target is not None else "",
                    results[week.state],
                ]
            )
    header = ["Employee", "Username", "Week start", "Week end", "Hours worked", "Target", "Difference", "Result"]
    return _csv_response(f"attendance-weekly-{data['start']}-to-{data['end']}.csv", header, rows)


# --- Monthly summary --------------------------------------------------------


def _monthly(request):
    """One row per employee for a month. A month still running is counted up to yesterday."""
    current = services.today()
    month = parse_month(request.GET.get("month"), current)
    last = month.replace(day=calendar.monthrange(month.year, month.month)[1])
    through = min(last, current - services.ONE_DAY)
    rows = []
    if through >= month:
        # Anyone active, plus anyone since deactivated who has time recorded in the month.
        recorded = Q(
            entries__clock_in__gte=services.day_start(month),
            entries__clock_in__lt=services.day_start(through + services.ONE_DAY),
        )
        employees = (
            Employee.objects.filter(is_staff=False)
            .filter(Q(is_active=True) | recorded)
            .distinct()
            .prefetch_related(SCHEDULE_HISTORY)
        )
        holidays = services.holiday_map(month, through)
        rows = [services.month_summary(employee, month, through, holidays=holidays) for employee in employees]
    return {
        "month": month,
        "through": through,
        "complete": through == last,
        "rows": rows,
        "prev_month": (month - services.ONE_DAY).replace(day=1),
        "next_month": last + services.ONE_DAY,
    }


@admin_required
def monthly(request):
    return render(request, "attendance/manage/monthly.html", _monthly(request))


@admin_required
def export_monthly(request):
    data = _monthly(request)
    rows = [
        [
            _csv_safe(row.employee.display_name),
            _csv_safe(row.employee.username),
            _csv_safe(row.terms.name) if row.terms else "",
            row.expected_days,
            row.present,
            row.missing,
            row.absent,
            row.leave,
            row.holidays,
            _hours(row.worked),
            _hours(row.target) if row.target is not None else "",
            _hours(row.difference) if row.target is not None else "",
        ]
        for row in data["rows"]
    ]
    header = [
        "Employee",
        "Username",
        "Schedule",
        "Expected days",
        "Present",
        "No clock-out",
        "Absent",
        "Leave",
        "Holidays",
        "Hours worked",
        "Target",
        "Difference",
    ]
    return _csv_response(f"attendance-{data['month']:%Y-%m}.csv", header, rows)


# --- Requests ---------------------------------------------------------------


@admin_required
def requests_inbox(request):
    pending = RequestStatus.PENDING
    waiting = sorted(
        [
            *MissingTimeRequest.objects.filter(status=pending).select_related("employee", "entry"),
            *LeaveRequest.objects.filter(status=pending).select_related("employee"),
        ],
        key=lambda item: item.created_at,
    )
    allowances = dict(LeaveAllowance.objects.values_list("leave_type", "days"))
    for item in waiting:
        if item.kind == "leave":
            item.days_used = len(services.working_leave_days(item.employee, item.start_date, item.end_date))
            item.balance = services.leave_balance_for(item, allowances)
    decided = sorted(
        [
            *MissingTimeRequest.objects.exclude(status=pending).select_related("employee", "decided_by")[:30],
            *LeaveRequest.objects.exclude(status=pending).select_related("employee", "decided_by")[:30],
        ],
        key=lambda item: item.decided_at or item.created_at,
        reverse=True,
    )
    return render(
        request,
        "attendance/manage/requests.html",
        {"pending": waiting, "decided": decided[:30]},
    )


@admin_required
@require_POST
def request_decide(request, kind, pk):
    item = get_request_or_404(kind, pk=pk)
    approve = request.POST.get("decision") == "approve"
    try:
        decided = services.decide(item, request.user, approve, request.POST.get("note", ""))
    except ValidationError as error:
        messages.error(request, f"Not saved. {' '.join(error.messages)}")
    else:
        notifications.request_decided(request, decided)
        messages.success(request, f"Request {'approved' if approve else 'rejected'}.")
    return redirect("manage_requests")


@admin_required
@require_POST
def leave_cancel(request, pk):
    leave = get_object_or_404(LeaveRequest, pk=pk)
    try:
        cancelled = services.cancel(leave, by=request.user)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        notifications.request_decided(request, cancelled)
        messages.success(request, "Leave cancelled.")
    return redirect("manage_requests")


# --- Employees --------------------------------------------------------------


@admin_required
def employees(request):
    people = Employee.objects.prefetch_related(SCHEDULE_HISTORY).order_by("-is_active", "first_name", "username")
    locked = lockout.locked_usernames()
    for person in people:
        person.locked_until = locked.get(person.username.lower())
    return render(request, "attendance/manage/employees.html", {"employees": people})


@admin_required
def employee_new(request):
    form = EmployeeCreateForm(request.POST or None, acting_user=request.user)
    if request.method == "POST" and form.is_valid():
        employee = form.save()
        messages.success(request, f"{employee.display_name} can now sign in as {employee.username}.")
        return redirect("manage_employees")
    return form_page(request, form, "Add employee", reverse("manage_employees"), "Add employee")


@admin_required
def employee_edit(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    form = EmployeeForm(request.POST or None, instance=employee, acting_user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Employee saved.")
        return redirect("manage_employees")
    return form_page(request, form, f"Edit {employee.display_name}", reverse("manage_employees"))


@admin_required
def employee_password(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    form = ResetPasswordForm(employee, request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        lockout.clear(employee.username)
        if employee.pk == request.user.pk:
            update_session_auth_hash(request, employee)  # keeps the admin signed in
        messages.success(request, f"Password changed for {employee.display_name}.")
        return redirect("manage_employees")
    return form_page(
        request,
        form,
        f"Set a new password for {employee.display_name}",
        reverse("manage_employees"),
        "Set password",
    )


@admin_required
def employee_detail(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    current = services.today()
    month = parse_month(request.GET.get("month"), current)
    context = month_context(employee, month)
    context.update(
        calendar_url=reverse("manage_employee_detail", args=[employee.pk]),
        leave_balances=services.leave_balances(employee, month.year),
        assignments=employee.assignment_list()[::-1],
    )
    return render(request, "attendance/manage/employee_detail.html", context)


# --- Corrections to the record ----------------------------------------------


def _parse_day(value, fallback):
    try:
        day = date.fromisoformat(value)
    except (TypeError, ValueError):
        return fallback
    return day if 2000 <= day.year <= 2100 else fallback


def _month_of(employee, moment):
    """The admin's page for an employee, opened on the month a moment falls in."""
    month = services.work_date(moment).strftime("%Y-%m")
    return f"{reverse('manage_employee_detail', args=[employee.pk])}?month={month}"


def _save_entry(request, form, employee, entry=None):
    """Applies a valid entry form. Returns the correction, or None with the error put on the form."""
    try:
        correction = services.correct_entry(
            request.user,
            employee,
            form.cleaned_data["clock_in"],
            form.cleaned_data["clock_out"],
            form.cleaned_data["reason"],
            entry=entry,
        )
    except ValidationError as error:
        form.add_error(None, error)
        return None
    notifications.record_corrected(request, correction)
    return correction


@admin_required
def entry_new(request, employee_pk):
    employee = get_object_or_404(Employee, pk=employee_pk, is_staff=False)
    initial = {"date": _parse_day(request.GET.get("date"), services.today())}
    form = EntryForm(request.POST or None, employee=employee, initial=initial)
    if request.method == "POST" and form.is_valid():
        correction = _save_entry(request, form, employee)
        if correction:
            messages.success(request, "Time added to the record.")
            return redirect(_month_of(employee, correction.new_clock_in))
    return form_page(
        request,
        form,
        f"Add time for {employee.display_name}",
        reverse("manage_employee_detail", args=[employee.pk]),
        "Add time",
        intro="For time that was worked but not recorded. It is marked as an admin correction, with your reason.",
    )


@admin_required
def entry_edit(request, pk):
    entry = get_object_or_404(TimeEntry.objects.select_related("employee"), pk=pk)
    employee = entry.employee
    form = EntryForm(request.POST or None, employee=employee, entry=entry)
    if request.method == "POST" and form.is_valid():
        correction = _save_entry(request, form, employee, entry=entry)
        if correction:
            messages.success(request, "Entry corrected.")
            return redirect(_month_of(employee, correction.new_clock_in))
    return form_page(
        request,
        form,
        f"Correct an entry for {employee.display_name}",
        _month_of(employee, entry.clock_in),
        "Save correction",
        intro="The entry is marked as an admin correction, and the old times are kept in the correction history.",
    )


@admin_required
def entry_remove(request, pk):
    entry = get_object_or_404(TimeEntry.objects.select_related("employee"), pk=pk)
    employee = entry.employee
    back = _month_of(employee, entry.clock_in)
    form = RemoveEntryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        correction = services.remove_entry(request.user, entry, form.cleaned_data["reason"])
        notifications.record_corrected(request, correction)
        messages.success(request, "Entry removed.")
        return redirect(back)
    clock_in = timezone.localtime(entry.clock_in)
    clock_out = f"{timezone.localtime(entry.clock_out):%I:%M %p}" if entry.clock_out else "no clock-out"
    return form_page(
        request,
        form,
        f"Remove an entry for {employee.display_name}?",
        back,
        "Remove entry",
        intro=f"{clock_in:%A %d %B %Y}, {clock_in:%I:%M %p} to {clock_out}. "
        "The times stay in the correction history.",
        danger=True,
    )


# --- Schedules --------------------------------------------------------------


@admin_required
def schedules(request):
    """Who is expected to work when in one week, plus the schedules themselves."""
    current = services.today()
    monday = services.week_start(_parse_day(request.GET.get("week"), current))
    holidays = services.holiday_map(monday, monday + timedelta(days=6))
    employees = list(tracked_employees())
    rows = [
        {
            "employee": employee,
            "week": services.build_weeks(employee, monday, monday, holidays=holidays)[0],
        }
        for employee in employees
    ]
    days = [
        {"date": day, "holiday": holidays.get(day), "is_today": day == current}
        for day in (monday + timedelta(days=offset) for offset in range(7))
    ]
    on_schedule_now = Counter(employee.terms.schedule_id for employee in employees if employee.terms)
    definitions = list(Schedule.objects.prefetch_related("versions").annotate(use_count=Count("assignments")))
    for schedule in definitions:
        schedule.employee_count = on_schedule_now[schedule.pk]
    return render(
        request,
        "attendance/manage/schedules.html",
        {
            "schedules": definitions,
            "rows": rows,
            "days": days,
            "monday": monday,
            "prev_week": monday - timedelta(days=7),
            "next_week": monday + timedelta(days=7),
            "is_current_week": monday == services.week_start(current),
        },
    )


@admin_required
def schedule_edit(request, pk=None):
    schedule = get_object_or_404(Schedule, pk=pk) if pk else None
    form = ScheduleForm(request.POST or None, schedule=schedule)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Schedule saved.")
        return redirect("manage_schedules")
    title = f"Edit {schedule.name}" if schedule else "Add schedule"
    intro = (
        "Weeks before the date you choose keep the hours and days they had."
        if schedule
        else "Tick the days people on this schedule normally work."
    )
    return form_page(request, form, title, reverse("manage_schedules"), intro=intro)


@admin_required
@require_POST
def schedule_delete(request, pk):
    schedule = get_object_or_404(Schedule, pk=pk)
    try:
        schedule.delete()
    except ProtectedError:
        messages.error(request, "This schedule is part of someone's history, so it cannot be deleted.")
    else:
        messages.success(request, "Schedule deleted.")
    return redirect("manage_schedules")


# --- Leave ------------------------------------------------------------------


@admin_required
def leave_overview(request):
    """Yearly leave allowances, and where every employee stands against them."""
    year = _parse_year(request.GET.get("year"), services.today().year)
    form = LeaveAllowanceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Leave allowances saved.")
        return redirect(f"{reverse('manage_leave')}?year={year}")

    allowances = dict(LeaveAllowance.objects.values_list("leave_type", "days"))
    leave_types = LeaveRequest.LeaveType.choices
    rows = []
    for employee in tracked_employees():
        balances = {balance.leave_type: balance for balance in services.leave_balances(employee, year, allowances)}
        rows.append({"employee": employee, "cells": [balances.get(value) for value, _ in leave_types]})
    return render(
        request,
        "attendance/manage/leave.html",
        {"year": year, "form": form, "rows": rows, "leave_types": [label for _, label in leave_types]},
    )


# --- Holidays ---------------------------------------------------------------


def _parse_year(value, fallback):
    try:
        year = int(value)
    except (TypeError, ValueError):
        return fallback
    return year if 2000 <= year <= 2100 else fallback


@admin_required
def holidays(request):
    year = _parse_year(request.GET.get("year"), services.today().year)
    occurrences = services.holiday_occurrences(date(year, 1, 1), date(year, 12, 31))
    return render(request, "attendance/manage/holidays.html", {"year": year, "occurrences": occurrences})


@admin_required
def holiday_edit(request, pk=None):
    holiday = get_object_or_404(Holiday, pk=pk) if pk else None
    form = HolidayForm(request.POST or None, instance=holiday)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        messages.success(request, "Holiday saved.")
        return redirect(f"{reverse('manage_holidays')}?year={saved.date.year}")
    title = f"Edit {holiday.name}" if holiday else "Add holiday"
    return form_page(request, form, title, reverse("manage_holidays"))


@admin_required
@require_POST
def holiday_delete(request, pk):
    holiday = get_object_or_404(Holiday, pk=pk)
    year = _parse_year(request.POST.get("year"), holiday.date.year)
    holiday.delete()
    messages.success(request, f"{holiday.name} removed.")
    return redirect(f"{reverse('manage_holidays')}?year={year}")


@admin_required
@require_POST
def holiday_preset(request):
    year = _parse_year(request.POST.get("year"), services.today().year)
    added = pk_holidays.add_lunar_holidays(Holiday, year)
    if added:
        messages.success(
            request, f"Added {added} estimated dates for {year}. They are marked tentative until you confirm them."
        )
    else:
        messages.info(request, f"The Islamic holidays for {year} are already in the list.")
    return redirect(f"{reverse('manage_holidays')}?year={year}")
