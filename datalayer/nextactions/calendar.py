"""Counting days the way the Commission's rules do (19 CFR 201.14(a)).

    "Computation of any period of time ... shall begin with the first
    business day following the day on which the act or event initiating such
    period of time shall have occurred. The last day of the period so
    computed is to be included, unless it is a Saturday, Sunday, or Federal
    legal holiday, in which event the period runs until the end of the next
    business day. When the period of time prescribed or allowed is less than
    7 days, intermediate Saturdays, Sundays, and Federal legal holidays shall
    be excluded from the computation."

Federal legal holidays are the ones in 5 U.S.C. 6103(a), moved to the Friday
before or the Monday after when they fall on a weekend. Early or unplanned
Commission closings cannot be known in advance and are not modeled.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The nth (1-based) weekday (Mon=0) of a month; n=-1 for the last."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


@lru_cache(maxsize=None)
def federal_holidays(year: int) -> frozenset[date]:
    days = {
        _observed(date(year, 1, 1)),                # New Year's Day
        _nth_weekday(year, 1, 0, 3),                # Birthday of Martin Luther King, Jr.
        _nth_weekday(year, 2, 0, 3),                # Washington's Birthday
        _nth_weekday(year, 5, 0, -1),               # Memorial Day
        _observed(date(year, 7, 4)),                # Independence Day
        _nth_weekday(year, 9, 0, 1),                # Labor Day
        _nth_weekday(year, 10, 0, 2),               # Columbus Day
        _observed(date(year, 11, 11)),              # Veterans Day
        _nth_weekday(year, 11, 3, 4),               # Thanksgiving Day
        _observed(date(year, 12, 25)),              # Christmas Day
    }
    if year >= 2021:
        days.add(_observed(date(year, 6, 19)))      # Juneteenth
    # New Year's Day on a Saturday is observed on the last Friday of the year before.
    if date(year + 1, 1, 1).weekday() == 5:
        days.add(date(year, 12, 31))
    return frozenset(days)


def is_business_day(day: date) -> bool:
    return day.weekday() < 5 and day not in federal_holidays(day.year)


def next_business_day(day: date) -> date:
    """The day itself if it is a business day, else the next one."""
    while not is_business_day(day):
        day += timedelta(days=1)
    return day


def previous_business_day(day: date) -> date:
    """The day itself if it is a business day, else the one before: the
    last day something due "no later than" a weekend can actually be filed."""
    while not is_business_day(day):
        day -= timedelta(days=1)
    return day


def period_end(event: date, days: int) -> date:
    """The last day of a period of `days` days after `event` (201.14(a))."""
    start = next_business_day(event + timedelta(days=1))
    if days < 7:
        # Short periods count business days only.
        day, counted = start, 1
        while counted < days:
            day += timedelta(days=1)
            if is_business_day(day):
                counted += 1
        return day
    return next_business_day(start + timedelta(days=days - 1))


def months_before(day: date, months: int) -> date:
    """The same day of the month `months` earlier (the last day of that month
    when it is shorter): "no later than 4 months before the target date"."""
    month = day.month - months
    year = day.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    for candidate in range(day.day, 27, -1):
        try:
            return date(year, month, candidate)
        except ValueError:
            continue
    return date(year, month, min(day.day, 28))


def parse(value: object) -> date | None:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None
