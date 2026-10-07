"""Pages every signed-in employee sees."""

import calendar
from datetime import date, timedelta
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import notifications, services
from .navigation import crumb
from .forms import LeaveForm, LoginForm, MissingTimeForm, OwnPasswordForm
from .models import WEEKDAY_SHORT, LeaveRequest, MissingTimeRequest, RequestStatus, TimeEntry

REQUEST_MODELS = {model.kind: model for model in (MissingTimeRequest, LeaveRequest)}


def employee_only(view):
    """For the personal pages. Admins run the system and keep no attendance of their own."""

    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if request.user.is_staff:
            return redirect("manage_dashboard")
        return view(request, *args, **kwargs)

    return wrapped


def parse_month(value, fallback):
    """First day of the month in a "YYYY-MM" string, or of `fallback`'s month."""
    try:
        year, month = (int(part) for part in value.split("-"))
        if not 2000 <= year <= 2100:
            raise ValueError
        return date(year, month, 1)
    except (AttributeError, ValueError):
        return fallback.replace(day=1)


def month_context(employee, month, now=None):
    """Template context for one employee's month: calendar weeks plus the entries."""
    now = now or timezone.now()
    last = month.replace(day=calendar.monthrange(month.year, month.month)[1])
    weeks = services.build_weeks(employee, month, last, now=now)
    month_days = []
    for week in weeks:
        for day in week.days:
            day.muted = day.date.month != month.month
            if not day.muted:
                month_days.append(day)
    recorded = [day for day in month_days if day.entries]
    return {
        "subject": employee,
        "month": month,
        "prev_month": (month - services.ONE_DAY).replace(day=1),
        "next_month": last + services.ONE_DAY,
        "weeks": weeks,
        "weekday_names": WEEKDAY_SHORT,
        "month_worked": sum((day.worked for day in month_days), timedelta(0)),
        "days_with_entries": recorded,
        "timeline": services.build_timeline(
            [(f"{day.date:%a} {day.date.day} {day.date:%b}", None, day) for day in recorded], now=now
        ),
        "corrections": employee.corrections.select_related("admin")[:20],
    }


def get_request_or_404(kind, **filters):
    model = REQUEST_MODELS.get(kind)
    if model is None:
        raise Http404
    return get_object_or_404(model, **filters)


class SignInView(LoginView):
    authentication_form = LoginForm
    template_name = "attendance/login.html"
    redirect_authenticated_user = True


class OwnPasswordView(PasswordChangeView):
    form_class = OwnPasswordForm
    template_name = "attendance/form_page.html"
    success_url = reverse_lazy("home")
    extra_context = {
        "title": "Change password",
        "submit_label": "Change password",
        "cancel_url": reverse_lazy("home"),
    }

    def form_valid(self, form):
        messages.success(self.request, "Your password has been changed.")
        return super().form_valid(form)


@employee_only
def home(request):
    employee = request.user
    now = timezone.now()
    current = services.today(now)
    week = services.build_weeks(employee, current, current, now=now)[0]
    day = next(day for day in week.days if day.date == current)
    open_entry = services.get_open_entry(employee)
    finished = sum((entry.worked(now) for entry in day.entries if not entry.is_open), timedelta(0))
    unresolved = (
        TimeEntry.objects.filter(employee=employee, missing_out=True)
        .exclude(fix_requests__status=RequestStatus.PENDING)
        .order_by("-clock_in")[:5]
    )
    return render(
        request,
        "attendance/home.html",
        {
            "now": now,
            "now_ms": int(now.timestamp() * 1000),
            "open_entry": open_entry,
            "open_ms": int(open_entry.clock_in.timestamp() * 1000) if open_entry else None,
            "finished_seconds": int(finished.total_seconds()),
            "time_zone": settings.TIME_ZONE,
            "day": day,
            "today_days": [day],
            "week": week,
            "unresolved": unresolved,
        },
    )


@employee_only
@require_POST
def punch(request):
    action = request.POST.get("action")
    if action not in ("in", "out"):
        return HttpResponseBadRequest("Unknown action.")
    entry, changed = services.punch(request.user, action)
    if not changed:
        state = "in" if action == "in" else "out"
        messages.info(request, f"You were already clocked {state}, so nothing changed.")
    elif action == "in":
        messages.success(request, f"Clocked in at {services.clock_text(entry.clock_in)}.")
    else:
        worked = services.duration_text(entry.clock_out - entry.clock_in)
        messages.success(request, f"Clocked out at {services.clock_text(entry.clock_out)} after {worked}.")
    return redirect("home")


@employee_only
def my_calendar(request):
    month = parse_month(request.GET.get("month"), services.today())
    context = month_context(request.user, month)
    context["calendar_url"] = reverse("calendar")
    return render(request, "attendance/calendar.html", context)


@employee_only
def my_requests(request):
    employee = request.user
    year = services.today().year
    return render(
        request,
        "attendance/requests.html",
        {
            "missing_requests": employee.missingtimerequests.all()[:50],
            "leave_requests": employee.leaverequests.all()[:50],
            "leave_balances": services.leave_balances(employee, year),
            "year": year,
        },
    )


@employee_only
def missing_time_new(request):
    employee = request.user
    entry = None
    entry_id = request.GET.get("entry")
    if entry_id:
        if not entry_id.isdigit():
            raise Http404
        entry = get_object_or_404(TimeEntry, pk=entry_id, employee=employee, missing_out=True)

    form = MissingTimeForm(request.POST or None, employee=employee, entry=entry)
    if request.method == "POST" and form.is_valid():
        sent = MissingTimeRequest.objects.create(
            employee=employee,
            clock_in=form.cleaned_data["clock_in"],
            clock_out=form.cleaned_data["clock_out"],
            entry=entry,
            reason=form.cleaned_data["reason"],
        )
        notifications.request_submitted(request, sent)
        messages.success(request, "Request sent. The time will count once an admin approves it.")
        return redirect("my_requests")

    if entry:
        title = "Add a missing clock-out"
        intro = (
            f"You clocked in at {services.clock_text(entry.clock_in)} on "
            f"{timezone.localtime(entry.clock_in):%A %d %B} and did not clock out. "
            "Enter the time you stopped working."
        )
    else:
        title = "Log missing time"
        intro = "For time you worked but could not record with the clock button. An admin reviews every request."
    return render(
        request,
        "attendance/form_page.html",
        {
            "form": form,
            "title": title,
            "intro": intro,
            "submit_label": "Send request",
            "cancel_url": reverse("my_requests"),
            "breadcrumbs": [crumb(title)],
        },
    )


@employee_only
def leave_new(request):
    form = LeaveForm(request.POST or None, employee=request.user)
    if request.method == "POST" and form.is_valid():
        leave = form.save(commit=False)
        leave.employee = request.user
        leave.save()
        notifications.request_submitted(request, leave)
        balance = services.leave_balance_for(leave)
        if balance and balance.over_by:
            days = "1 day" if balance.over_by == 1 else f"{balance.over_by} days"
            messages.warning(
                request,
                f"Leave request sent. It goes {days} over your {balance.label.lower()} leave for "
                f"{leave.start_date.year}, so it is up to the admin.",
            )
        else:
            messages.success(request, "Leave request sent.")
        return redirect("my_requests")
    left = [
        f"{balance.label} {max(balance.remaining, 0)}"
        for balance in services.leave_balances(request.user, services.today().year)
        if balance.allowance is not None
    ]
    intro = "Approved leave shows on your calendar and lowers that week's hours target."
    if left:
        intro += f" Days left this year: {', '.join(left)}."
    return render(
        request,
        "attendance/form_page.html",
        {
            "form": form,
            "title": "Request leave",
            "intro": intro,
            "submit_label": "Send request",
            "cancel_url": reverse("my_requests"),
            "breadcrumbs": [crumb("Request leave")],
        },
    )


@employee_only
@require_POST
def request_cancel(request, kind, pk):
    leave_or_missing = get_request_or_404(kind, pk=pk, employee=request.user)
    try:
        services.cancel(leave_or_missing, by=request.user)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, "Request cancelled.")
    return redirect("my_requests")
