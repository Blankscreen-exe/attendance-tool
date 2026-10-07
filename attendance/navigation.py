"""The menu, and which part of it a page belongs to.

The sidebar and the breadcrumb trail both read from here, so they cannot
disagree about where a page lives.
"""

from dataclasses import dataclass

from django.urls import reverse


@dataclass(frozen=True)
class Section:
    label: str
    url_name: str
    icon: str
    prefixes: tuple  # a page belongs here when its URL name starts with one of these

    def owns(self, url_name):
        return any(url_name.startswith(prefix) for prefix in self.prefixes)


ADMIN_MENU = (
    Section("Today", "manage_dashboard", "pulse", ("manage_dashboard",)),
    Section("Attendance", "manage_review", "bars", ("manage_review", "manage_monthly")),
    Section("Requests", "manage_requests", "inbox", ("manage_request",)),
    Section("Employees", "manage_employees", "people", ("manage_employee", "manage_entry")),
    Section("Schedules", "manage_schedules", "grid", ("manage_schedule",)),
    Section("Holidays", "manage_holidays", "star", ("manage_holiday",)),
    Section("Leave", "manage_leave", "briefcase", ("manage_leave",)),
    Section("Email", "manage_email", "mail", ("manage_email",)),
)

EMPLOYEE_MENU = (
    Section("Clock", "home", "clock", ("home",)),
    Section("Calendar", "calendar", "calendar", ("calendar",)),
    Section("Requests", "my_requests", "inbox", ("my_requests", "missing_time_new", "leave_new")),
)

# Shown at the foot of the sidebar for everyone.
ACCOUNT = Section("Change password", "password_change", "key", ("password_change",))


def menu_for(user):
    return ADMIN_MENU if user.is_staff else EMPLOYEE_MENU


def section_for(user, url_name):
    """The section a page belongs to, or None for a page outside this user's menu."""
    for section in (*menu_for(user), ACCOUNT):
        if section.owns(url_name):
            return section
    return None


def crumb(label, url_name=None, *args, query=""):
    """One step of the breadcrumb trail below the section.

    Views list these in `breadcrumbs`, ending with the current page. Leave out
    the URL name for a step that is not a link.
    """
    return {"label": label, "url": reverse(url_name, args=args) + query if url_name else None}
