"""Pakistan's public holidays, used as the default holiday list.

Fixed-date holidays repeat every year. Islamic holidays follow the lunar
calendar and are officially announced after moon sighting, so the dates
calculated here are estimates that can be off by a day or two; they are
stored as tentative for an admin to confirm.
"""

import math
from datetime import date

# (month, day, name)
FIXED_HOLIDAYS = [
    (2, 5, "Kashmir Day"),
    (3, 23, "Pakistan Day"),
    (5, 1, "Labour Day"),
    (5, 28, "Youm-e-Takbeer"),
    (8, 14, "Independence Day"),
    (11, 9, "Iqbal Day"),
    (12, 25, "Quaid-e-Azam Day"),
]

# (name, hijri month, first hijri day, number of days)
LUNAR_HOLIDAYS = [
    ("Ashura", 1, 9, 2),
    ("Eid Milad un-Nabi", 3, 12, 1),
    ("Eid ul-Fitr", 10, 1, 3),
    ("Eid ul-Adha", 12, 10, 3),
]

LUNAR_NAMES = [name for name, *_ in LUNAR_HOLIDAYS]

_HIJRI_EPOCH = 1948439.5  # Julian day of 1 Muharram, year 1
_ORDINAL_OFFSET = 1721424.5  # Julian day minus Python's proleptic Gregorian ordinal


def hijri_to_gregorian(year, month, day):
    """Convert a date in the tabular (arithmetic) Islamic calendar."""
    julian_day = (
        day
        + math.ceil(29.5 * (month - 1))
        + (year - 1) * 354
        + (3 + 11 * year) // 30
        + _HIJRI_EPOCH
        - 1
    )
    return date.fromordinal(int(julian_day - _ORDINAL_OFFSET))


def add_fixed_holidays(holiday_model, base_year=2020):
    """Adds the fixed-date holidays as yearly repeating entries."""
    for month, day, name in FIXED_HOLIDAYS:
        holiday_model.objects.get_or_create(
            date=date(base_year, month, day), name=name, defaults={"recurs_yearly": True}
        )


def add_lunar_holidays(holiday_model, year):
    """Adds estimated Islamic holidays for a year, skipping any that are already there."""
    existing = set(
        holiday_model.objects.filter(date__year=year, name__in=LUNAR_NAMES).values_list("name", flat=True)
    )
    new = [
        holiday_model(date=day, name=name, tentative=True)
        for day, name in lunar_holidays(year)
        if name not in existing
    ]
    holiday_model.objects.bulk_create(new)
    return len(new)


def lunar_holidays(gregorian_year):
    """Estimated (date, name) pairs for the Islamic holidays in a Gregorian year."""
    approx_hijri_year = int((gregorian_year - 622) * 33 / 32)
    found = []
    for hijri_year in range(approx_hijri_year - 1, approx_hijri_year + 3):
        for name, month, first_day, length in LUNAR_HOLIDAYS:
            for offset in range(length):
                day = hijri_to_gregorian(hijri_year, month, first_day + offset)
                if day.year == gregorian_year:
                    found.append((day, name))
    return sorted(found)
