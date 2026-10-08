"""NYSE (XNYS) full-day session calendar, computed by rule.

Standard library only. The rules are the exchange's published holiday rules
from 2022 (the first year Juneteenth was observed) onward, plus the special
closures listed in ``SPECIAL_CLOSURES``. Early closes are sessions. A date
outside the supported range raises rather than guessing: a calendar that is
silently wrong moves every lookback window it touches.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

FIRST_SUPPORTED = date(2022, 1, 1)
LAST_SUPPORTED = date(2030, 12, 31)

# Unscheduled full-day closures inside the supported range.
SPECIAL_CLOSURES = frozenset({
    date(2025, 1, 9),  # national day of mourning, President Carter
})


class CalendarRangeError(ValueError):
    """The date lies outside the range this calendar is defined for."""


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    following = date(year + (month == 12), month % 12 + 1, 1)
    last = following - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    # Anonymous Gregorian algorithm (Meeus/Jones/Butcher).
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741 - the algorithm's own name
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


@lru_cache(maxsize=None)
def holidays(year: int) -> frozenset[date]:
    if not FIRST_SUPPORTED.year <= year <= LAST_SUPPORTED.year:
        raise CalendarRangeError(f"XNYS calendar is not defined for {year}")
    days: set[date] = set()
    new_year = date(year, 1, 1)
    # NYSE does not observe New Year's Day on the preceding Friday.
    if new_year.weekday() == 6:
        days.add(new_year + timedelta(days=1))
    elif new_year.weekday() < 5:
        days.add(new_year)
    days.add(_nth_weekday(year, 1, 0, 3))   # Martin Luther King Jr. Day
    days.add(_nth_weekday(year, 2, 0, 3))   # Washington's Birthday
    days.add(_easter(year) - timedelta(days=2))  # Good Friday
    days.add(_last_weekday(year, 5, 0))     # Memorial Day
    for month, day in ((6, 19), (7, 4), (12, 25)):
        days.add(_observed(date(year, month, day)))
    days.add(_nth_weekday(year, 9, 0, 1))   # Labor Day
    days.add(_nth_weekday(year, 11, 3, 4))  # Thanksgiving
    days.update(day for day in SPECIAL_CLOSURES if day.year == year)
    return frozenset(days)


def is_session(day: date) -> bool:
    if not FIRST_SUPPORTED <= day <= LAST_SUPPORTED:
        raise CalendarRangeError(f"XNYS calendar is not defined for {day.isoformat()}")
    return day.weekday() < 5 and day not in holidays(day.year)


def sessions(start: date, end: date) -> list[date]:
    """Every XNYS session in the closed interval [start, end]."""
    if end < start:
        return []
    is_session(start)
    is_session(end)
    out: list[date] = []
    day = start
    while day <= end:
        if is_session(day):
            out.append(day)
        day += timedelta(days=1)
    return out


def last_session_of_each_week(days: list[date]) -> list[date]:
    """The latest supplied session in each ISO week, in order."""
    latest: dict[tuple[int, int], date] = {}
    for day in days:
        iso = day.isocalendar()
        key = (iso.year, iso.week)
        if key not in latest or day > latest[key]:
            latest[key] = day
    return sorted(latest.values())
