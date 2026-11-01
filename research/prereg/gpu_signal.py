"""The pre-registered GPU rental signal, as a pure function of published fixings.

Signal: the log change in an index's published value over 63 XNYS sessions
(about three months). This module holds only the arithmetic and the rules
that decide whether the arithmetic is allowed to run. Reading the tape is the
provider's job (``research.providers.gpu_index``); this file never touches a
path or a network.

Rules, each fixed in ``preregistration.json``:

- one value per (index, date): the revision that was live at the as-of time;
- only ``published`` rows count; a ``withheld`` day is missing, never filled;
- only XNYS session dates count; weekend and holiday fixings are ignored;
- a window may not span a methodology change (the index's own rule: a series
  is split at a methodology version, not spliced across one);
- the anchor is the fixing 63 sessions back, or the nearest earlier fixing
  within 3 sessions of it; and
- at least 90% of the sessions in the window must carry a fixing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Iterable, Sequence

from research.prereg import xnys

LOOKBACK_SESSIONS = 63
ANCHOR_TOLERANCE_SESSIONS = 3
MIN_WINDOW_COVERAGE = 0.90
MAX_STALENESS_SESSIONS = 3


@dataclass(frozen=True)
class Fixing:
    index_code: str
    index_date: date
    revision: int
    status: str
    value: float | None
    methodology_version: str
    published_at: datetime
    superseded_at: datetime | None


class SignalStatus(StrEnum):
    LIVE = "live"
    INSUFFICIENT_HISTORY = "insufficient_history"
    INSUFFICIENT_COVERAGE = "insufficient_coverage"
    STALE = "stale"
    NO_DATA = "no_data"


STATUS_TEXT = {
    SignalStatus.LIVE: "live",
    SignalStatus.INSUFFICIENT_HISTORY: "insufficient history for the 3-month signal",
    SignalStatus.INSUFFICIENT_COVERAGE: "3-month window too sparse to compute the signal",
    SignalStatus.STALE: "latest published fixing is too old to sign a signal",
    SignalStatus.NO_DATA: "no published fixing",
}


@dataclass(frozen=True)
class SessionValue:
    day: date
    value: float
    methodology_version: str


@dataclass(frozen=True)
class SignalReading:
    index_code: str
    as_of_session: date
    status: SignalStatus
    message: str
    latest_fixing: date | None
    methodology_version: str | None
    history_sessions: int
    required_sessions: int
    window_coverage: float | None
    anchor: date | None
    value: float | None


def live_revisions(fixings: Iterable[Fixing], as_of: datetime | None = None) -> list[Fixing]:
    """One row per (index, date): the revision live at ``as_of`` (now if None)."""
    chosen: dict[tuple[str, date], Fixing] = {}
    for row in fixings:
        if as_of is None:
            live = row.superseded_at is None
        else:
            live = row.published_at <= as_of and (
                row.superseded_at is None or row.superseded_at > as_of)
        if not live:
            continue
        key = (row.index_code, row.index_date)
        if key in chosen:
            raise ValueError(f"two live revisions for {key[0]} {key[1].isoformat()}")
        chosen[key] = row
    return sorted(chosen.values(), key=lambda row: (row.index_code, row.index_date))


def session_series(fixings: Iterable[Fixing], index_code: str,
                   as_of: datetime | None = None) -> list[SessionValue]:
    out: list[SessionValue] = []
    for row in live_revisions(fixings, as_of):
        if row.index_code != index_code or row.status != "published":
            continue
        if row.value is None or not math.isfinite(row.value) or row.value <= 0:
            raise ValueError(f"published fixing {index_code} {row.index_date} has no positive value")
        if xnys.is_session(row.index_date):
            out.append(SessionValue(row.index_date, row.value, row.methodology_version))
    return out


def required_sessions() -> int:
    """Sessions a window spans, both endpoints included."""
    return LOOKBACK_SESSIONS + 1


def signal_at(series: Sequence[SessionValue], index_code: str, as_of_session: date) -> SignalReading:
    """The signal as it could be known at the close of ``as_of_session``."""
    if not xnys.is_session(as_of_session):
        raise ValueError(f"{as_of_session.isoformat()} is not an XNYS session")
    known = [item for item in series if item.day <= as_of_session]
    need = required_sessions()

    def reading(status: SignalStatus, **fields: object) -> SignalReading:
        base: dict[str, object] = {
            "latest_fixing": None, "methodology_version": None, "history_sessions": 0,
            "window_coverage": None, "anchor": None, "value": None,
        }
        base.update(fields)
        return SignalReading(index_code=index_code, as_of_session=as_of_session, status=status,
                             message=STATUS_TEXT[status], required_sessions=need,
                             **base)  # type: ignore[arg-type]

    if not known:
        return reading(SignalStatus.NO_DATA)
    latest = known[-1]
    version = latest.methodology_version
    # The usable history is the unbroken run of the latest methodology version.
    run_start = len(known) - 1
    while run_start > 0 and known[run_start - 1].methodology_version == version:
        run_start -= 1
    usable = known[run_start:]
    spanned = len(xnys.sessions(usable[0].day, as_of_session))
    lag = len(xnys.sessions(latest.day, as_of_session)) - 1
    common = {"latest_fixing": latest.day, "methodology_version": version,
              "history_sessions": spanned}
    if lag > MAX_STALENESS_SESSIONS:
        return reading(SignalStatus.STALE, **common)
    calendar = xnys.sessions(usable[0].day, latest.day)
    if len(calendar) < need:
        return reading(SignalStatus.INSUFFICIENT_HISTORY, **common)
    target = calendar[-need]
    earliest = calendar[max(0, len(calendar) - need - ANCHOR_TOLERANCE_SESSIONS)]
    candidates = [item for item in usable if earliest <= item.day <= target]
    if not candidates:
        return reading(SignalStatus.INSUFFICIENT_COVERAGE, **common)
    anchor = candidates[-1]
    window = xnys.sessions(anchor.day, latest.day)
    present = sum(1 for item in usable if anchor.day <= item.day <= latest.day)
    coverage = present / len(window)
    common["window_coverage"] = coverage
    common["anchor"] = anchor.day
    if coverage < MIN_WINDOW_COVERAGE:
        return reading(SignalStatus.INSUFFICIENT_COVERAGE, **common)
    return reading(SignalStatus.LIVE, value=math.log(latest.value / anchor.value), **common)


def earliest_live_session(series: Sequence[SessionValue]) -> date | None:
    """First session the signal could go live if every later session publishes."""
    if not series:
        return None
    version = series[-1].methodology_version
    start = len(series) - 1
    while start > 0 and series[start - 1].methodology_version == version:
        start -= 1
    first = series[start].day
    horizon = xnys.sessions(first, xnys.LAST_SUPPORTED)
    if len(horizon) < required_sessions():
        return None
    return horizon[required_sessions() - 1]
