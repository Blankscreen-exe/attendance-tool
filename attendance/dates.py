"""Attendance days and weeks in the company timezone."""

from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.utils import timezone

ONE_DAY = timedelta(days=1)

# A Monday long before any real data. Schedules and assignments that have
# "always" applied are dated from here.
BEGINNING = date(2000, 1, 3)


def _rollover():
    return timedelta(hours=settings.DAY_ROLLOVER_HOUR)


def work_date(moment):
    """The attendance day a moment belongs to, in the company timezone."""
    return (timezone.localtime(moment) - _rollover()).date()


def day_start(day):
    """The moment an attendance day begins."""
    return timezone.make_aware(datetime.combine(day, time.min) + _rollover())


def today(now=None):
    return work_date(now or timezone.now())


def week_start(day):
    """The Monday of the week a day falls in."""
    return day - timedelta(days=day.weekday())


def clock_text(moment):
    """A moment as a time of day in the company timezone, e.g. "9:05 AM"."""
    return timezone.localtime(moment).strftime("%I:%M %p").lstrip("0")


def duration_text(length):
    """A length of time as hours and minutes, e.g. "7h 05m"."""
    minutes = int(max(length, timedelta(0)).total_seconds() // 60)
    return f"{minutes // 60}h {minutes % 60:02d}m"
