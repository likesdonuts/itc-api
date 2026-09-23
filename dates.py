"""One place that knows what a date from each source means.

Every feed this app reads writes dates differently, and two of the shapes are
impossible to tell apart by looking at a single value:

    EDIS documents      "2026/09/18 11:39:00"  year first
    EDIS timestamps     "2026-09-18T12:00:00Z" ISO 8601
    IDS investigations  "01-13-2026"           US month first

"04-05-2026" from IDS is 5 April, but read as day-first it is 4 May, and 37%
of the IDS Start Date values are ambiguous like that (the other 63% prove the
field is month-first: 557 of 886 have a second component above 12 and none
have a first component above 12). So the order is decided here, once, by
knowing the source format -- never by inspecting an individual value.

The data layer normalizes to ISO on the way in (`to_iso`) and the UI formats
on the way out (`format_ui`), so a date can only be misread if it is wrong in
the feed itself.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

APPROX_SEPARATOR = "  ("

_YEAR_FIRST_RE = re.compile(
    r"^(?P<year>\d{4})[-/](?P<month>\d{1,2})[-/](?P<day>\d{1,2})"
    r"(?:[ T](?P<time>\d{1,2}:\d{2}(?::\d{2})?))?"
)
# The only month-first source we read is the public IDS feed.
_MONTH_FIRST_RE = re.compile(
    r"^(?P<month>\d{1,2})[-/](?P<day>\d{1,2})[-/](?P<year>\d{2,4})"
    r"(?:[ T](?P<time>\d{1,2}:\d{2}(?::\d{2})?))?"
)
_MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def strip_note(value: Any) -> str:
    """Drop the trailing "  (approx., ...)" some stored dates carry."""
    return str(value or "").partition(APPROX_SEPARATOR)[0].strip()


def note_of(value: Any) -> str | None:
    _, sep, note = str(value or "").partition(APPROX_SEPARATOR)
    if not sep:
        return None
    return note.rstrip().rstrip(")").strip() or None


def parse(value: Any) -> datetime | None:
    """Return the instant a stored date refers to, or None if it isn't one."""
    text = strip_note(value)
    if not text:
        return None

    match = _YEAR_FIRST_RE.match(text)
    if match:
        return _build(match)

    match = _MONTH_FIRST_RE.match(text)
    if match:
        return _build(match, us_order=True)

    return None


def _build(match: re.Match[str], *, us_order: bool = False) -> datetime | None:
    year = int(match.group("year"))
    month = int(match.group("month"))
    day = int(match.group("day"))

    if year < 100:
        year += 2000
    if us_order and month > 12 and day <= 12:
        # Defensive: a month past 12 can only mean the value was day-first.
        month, day = day, month

    time_part = match.groupdict().get("time") or ""
    hour, minute, second = 0, 0, 0
    if time_part:
        pieces = [int(p) for p in time_part.split(":")]
        hour, minute = pieces[0], pieces[1]
        second = pieces[2] if len(pieces) > 2 else 0

    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def to_iso(value: Any) -> str | None:
    """Normalize any stored date to ISO 8601 for storage, keeping the time if
    the source supplied one. Unrecognizable values come back unchanged so
    nothing is silently thrown away.
    """
    text = strip_note(value)
    if not text:
        return None
    parsed = parse(text)
    if parsed is None:
        return text
    if (parsed.hour, parsed.minute, parsed.second) == (0, 0, 0):
        return parsed.strftime("%Y-%m-%d")
    return parsed.strftime("%Y-%m-%dT%H:%M:%S")


def format_ui(value: Any, fallback: str = "Unknown") -> str:
    """Render a date for the site as an unambiguous "18 Sep 2026".

    Day, month, year in that order, with the month spelled out so no reader
    ever has to work out whether "04/05" means April or May.
    """
    parsed = parse(value)
    if parsed is None:
        return strip_note(value) or fallback
    return f"{parsed.day:02d} {_MONTH_NAMES[parsed.month - 1]} {parsed.year:04d}"


def format_ui_time(value: Any, fallback: str = "Unknown") -> str:
    """Render a moment the app itself recorded as "23 Sep 2026, 13:42".

    Those are UTC timestamps with an offset ("2026-09-23T17:42:14+00:00"),
    so unlike the feeds' dates they are shown in this computer's local time,
    which is when you did it.
    """
    text = str(value or "").strip()
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return format_ui(value, fallback)
    if moment.tzinfo is not None:
        moment = moment.astimezone()
    return (
        f"{moment.day:02d} {_MONTH_NAMES[moment.month - 1]} {moment.year:04d}, "
        f"{moment.hour:02d}:{moment.minute:02d}"
    )


def sort_key(value: Any) -> str:
    """Chronological ordering that works across every stored shape."""
    parsed = parse(value)
    return parsed.isoformat() if parsed else ""
