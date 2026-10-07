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
