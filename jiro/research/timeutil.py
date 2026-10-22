"""Timestamp parsing for source metadata.

Pages state dates in a depressing variety of formats, and a source that will
not say when it was published is more useful than one with a fabricated date.
Everything here returns ``None`` rather than a guess, and every guess that *is*
attempted is anchored to an explicit year so a bare "March 3" cannot silently
resolve to a plausible-but-wrong date.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

_ISO = re.compile(
    r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})"
    r"(?:[T ](?P<hour>\d{2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?"
    r"(?P<tz>Z|[+-]\d{2}:?\d{2})?)?",
)

_SLASHED = re.compile(
    r"(?P<month>\d{1,2})[/.](?P<day>\d{1,2})[/.](?P<year>\d{4})")

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12,
    "december": 12,
}

_LONG_FORM = re.compile(
    r"(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]{3,9})\.?\s+(?P<year>\d{4})",
    re.IGNORECASE,
)

_RFC = re.compile(
    r"(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]{3})\s+(?P<year>\d{4})",
    re.IGNORECASE,
)

#: Month-first long form, e.g. "Oct 12, 2021". Tried only after the
#: day-first form, since "12 Oct 2021" also matches it.
_MONTH_FIRST = re.compile(
    r"(?P<month>[A-Za-z]{3,9})\.?\s+(?P<day>\d{1,2}),?\s+(?P<year>\d{4})",
    re.IGNORECASE,
)


def _to_epoch(year: int, month: int, day: int, hour: int = 0,
              minute: int = 0, second: int = 0) -> Optional[float]:
    try:
        return datetime(
            year, month, day, hour, minute, second,
            tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def parse_timestamp(raw: Optional[str]) -> Optional[float]:
    """Parse a publication timestamp to epoch seconds.

    Returns ``None`` when the value cannot be parsed confidently. Callers store
    the ``None``: an unknown publication date is information, a wrong one is
    not.
    """
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None

    match = _ISO.search(text)
    if match:
        return _to_epoch(
            int(match.group("year")), int(match.group("month")),
            int(match.group("day")), int(match.group("hour") or 0),
            int(match.group("minute") or 0), int(match.group("second") or 0),
        )

    match = _SLASHED.search(text)
    if match:
        return _to_epoch(
            int(match.group("year")), int(match.group("month")),
            int(match.group("day")))

    match = _LONG_FORM.search(text)
    if match:
        month = _MONTHS.get(match.group("month").lower())
        if month:
            return _to_epoch(
                int(match.group("year")), month, int(match.group("day")))

    # RFC 2822 and month-first forms, e.g. "Tue, 12 Oct 2021" / "Oct 12, 2021".
    match = _RFC.search(text) or _MONTH_FIRST.search(text)
    if match:
        month = _MONTHS.get(match.group("month").lower())
        if month:
            return _to_epoch(
                int(match.group("year")), month, int(match.group("day")))
    return None


def format_timestamp(epoch: Optional[float]) -> str:
    """Human-readable UTC date, or ``"unknown"`` when absent."""
    if epoch is None:
        return "unknown"
    try:
        return datetime.fromtimestamp(
            epoch, tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, OSError, OverflowError):
        return "unknown"


def age_days(epoch: Optional[float], *, now: float) -> Optional[float]:
    """Age in days, or ``None`` when the timestamp is unknown."""
    if epoch is None:
        return None
    delta = (now - epoch) / 86400.0
    return max(0.0, delta)


def is_fresh(epoch: Optional[float], *, max_age_days: Optional[float],
             now: float) -> bool:
    """Whether a source satisfies a freshness requirement.

    An unknown publication date is treated as **not** fresh. Assuming freshness
    for a source that will not say when it was published is how a
    "current state of the art" report quietly reports three-year-old news.
    """
    if max_age_days is None:
        return True
    age = age_days(epoch, now=now)
    if age is None:
        return False
    return age <= float(max_age_days)


__all__ = [
    "age_days",
    "format_timestamp",
    "is_fresh",
    "parse_timestamp",
]