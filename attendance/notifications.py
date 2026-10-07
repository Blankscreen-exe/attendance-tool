"""Email notices.

Sending must never get in the way of the work it reports on: if the mail
server is down or misconfigured, the failure is logged and the page carries on.
"""

import logging

from django.core.mail import send_mail
from django.urls import reverse
from django.utils import timezone

from .models import Correction, Employee, RequestStatus
from .templatetags.attendance_tags import duration

logger = logging.getLogger(__name__)


def _send(subject, lines, recipients):
    recipients = [address for address in recipients if address]
    if not recipients:
        return 0
    try:
        return send_mail(f"[Attendance] {subject}", "\n".join(lines), None, recipients)
    except Exception:
        logger.exception("Could not send email %r to %s", subject, recipients)
        return 0


def _admin_addresses():
    return list(
        Employee.objects.filter(is_staff=True, is_active=True).exclude(email="").values_list("email", flat=True)
    )


def _clock(moment):
    return timezone.localtime(moment).strftime("%I:%M %p").lstrip("0")


def _day(moment):
    return timezone.localtime(moment).strftime("%a %d %b %Y")


def _span(clock_in, clock_out):
    return f"{_day(clock_in)}, {_clock(clock_in)} to {_clock(clock_out) if clock_out else 'no clock-out'}"


def describe(item):
    """A request in one line of plain text."""
    if item.kind == "leave":
        days = "1 day" if item.calendar_days == 1 else f"{item.calendar_days} days"
        dates = f"{item.start_date:%a %d %b %Y}"
        if item.end_date != item.start_date:
            dates += f" to {item.end_date:%a %d %b %Y}"
        return f"{item.get_leave_type_display()} leave, {dates} ({days})"
    what = "Missing clock-out" if item.entry_id else "Missing time"
    return f"{what}: {_span(item.clock_in, item.clock_out)} ({duration(item.duration)})"


def request_submitted(http_request, item):
    """Tells the admins that a request is waiting for them."""
    return _send(
        f"New request from {item.employee.display_name}",
        [
            f"{item.employee.display_name} sent a request:",
            "",
            describe(item),
            f"Reason: {item.reason}",
            "",
            f"Approve or reject it: {http_request.build_absolute_uri(reverse('manage_requests'))}",
        ],
        _admin_addresses(),
    )


def request_decided(http_request, item):
    """Tells the employee what happened to their request."""
    outcome = {
        RequestStatus.APPROVED: "approved",
        RequestStatus.REJECTED: "rejected",
        RequestStatus.CANCELLED: "cancelled",
    }[item.status]
    lines = [f"Your request was {outcome}:", "", describe(item)]
    if item.decision_note:
        lines += [f"Note from {item.decided_by.display_name if item.decided_by else 'the admin'}: {item.decision_note}"]
    lines += ["", f"Your requests: {http_request.build_absolute_uri(reverse('my_requests'))}"]
    return _send(f"Your request was {outcome}", lines, [item.employee.email])


def record_corrected(http_request, correction):
    """Tells the employee that an admin changed their attendance record."""
    if correction.action == Correction.Action.ADDED:
        change = [f"Added: {_span(correction.new_clock_in, correction.new_clock_out)}"]
    elif correction.action == Correction.Action.REMOVED:
        change = [f"Removed: {_span(correction.old_clock_in, correction.old_clock_out)}"]
    else:
        change = [
            f"Was: {_span(correction.old_clock_in, correction.old_clock_out)}",
            f"Now: {_span(correction.new_clock_in, correction.new_clock_out)}",
        ]
    admin = correction.admin.display_name if correction.admin else "An admin"
    return _send(
        "Your attendance record was corrected",
        [
            f"{admin} corrected your attendance record.",
            "",
            *change,
            f"Reason: {correction.reason}",
            "",
            f"Your calendar: {http_request.build_absolute_uri(reverse('calendar'))}",
        ],
        [correction.employee.email],
    )
