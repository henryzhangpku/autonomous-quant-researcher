"""Overnight SPX-complex session model over stitched ES futures bars.

The overnight program studies SPXW 0DTE/1DTE defined-risk verticals entered
during the CBOE global trading hours curb (evening through pre-open) and cash
settled at that day's 16:00 ET close. SPY does not trade 20:00-04:00 ET, so
the only honest overnight underlying is the stitched front-month ES series
produced by ``research/experiments/prepare_overnight_spx.py`` (Polygon/Massive
futures, point-in-time, provenance in the frozen manifest).

Conventions:

- One session = evening 18:00 ET on the PRIOR calendar day through 16:00 ET on
  the session (settlement/expiry) day.
- Bar minutes are indexed from the 18:00 ET session start: 0 = 18:00 ET,
  135 = 20:15 ET, 915 = 09:15 ET next morning, 1320 = 16:00 ET settlement.
- Entry prices are bar OPENs (the tradable price at that minute); settlement
  is the close of the 15:55 ET bar, the same convention the 0DTE engine uses
  for its 16:00 proxy.
- ES is a proxy for SPX: strikes derive from the ES level snapped to the
  5-point SPX grid, and settlement uses the ES 16:00 level. The ES-SPX basis
  is not modeled; that limitation is recorded by the validator, not hidden.

Modeled pricing only. There is no overnight OPRA tape; entry marks come from
the same minimal Black-Scholes layer the founding study validated against real
SPY 0DTE quotes, with the total-variance horizon estimated causally from the
trailing sessions' own realized 5-minute variance.
"""

from __future__ import annotations

import csv
import gzip
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
ET = ZoneInfo("America/New_York")

#: Session minute bounds: 18:00 ET start, 16:00 ET settlement.
SESSION_MINUTES = 22 * 60
#: Candidate entries are restricted to the app's overnight window,
#: 20:15 ET (135) through 09:15 ET (915), on the 5-minute grid.
ENTRY_MIN_FLOOR = 135
ENTRY_MIN_CEIL = 915
#: A usable session must start by 19:00 ET and reach the 15:55 ET bar.
_LATEST_FIRST_BAR = 60
_SETTLEMENT_BAR = SESSION_MINUTES - 5
#: Trailing sessions used for the causal remaining-variance estimate.
VOL_WINDOW_SESSIONS = 20


def minute_index(hm_et: int) -> int:
    """Map minutes-since-midnight ET onto the 18:00-anchored session index."""
    return hm_et - 18 * 60 if hm_et >= 18 * 60 else hm_et + 6 * 60


@dataclass(frozen=True)
class OvernightSession:
    """One overnight-through-settlement session of stitched ES 5-minute bars.

    ``bars`` entries are (session_minute, open, high, low, close).
    ``prior_close`` is the prior session's settlement level, and the prior
    RTH extremes give candidates yesterday's day-session context without
    exposing raw history.
    """

    day: str                       # ISO settlement/expiry date
    bars: tuple[tuple[int, float, float, float, float], ...]
    contract: str
    prior_close: float | None
    prior_rth_high: float | None
    prior_rth_low: float | None

    @property
    def evening_open(self) -> float:
        return self.bars[0][1]

    @property
    def first_minute(self) -> int:
        return self.bars[0][0]

    @property
    def last_minute(self) -> int:
        return self.bars[-1][0]

    def bar_at(self, minute: int) -> tuple[int, float, float, float, float] | None:
        for bar in self.bars:
            if bar[0] == minute:
                return bar
        return None

    def price_at(self, minute: int) -> float | None:
        bar = self.bar_at(minute)
        return bar[1] if bar else None

    @property
    def settlement(self) -> float | None:
        bar = self.bar_at(_SETTLEMENT_BAR)
        return bar[4] if bar else None

    def is_complete(self) -> bool:
        return self.first_minute <= _LATEST_FIRST_BAR and self.bar_at(_SETTLEMENT_BAR) is not None

    def remaining_variance(self, minute: int) -> float:
        """Sum of squared 5-minute log returns from ``minute`` to settlement."""
        total = 0.0
        prev: float | None = None
        for bar_minute, _open, _high, _low, close in self.bars:
            if bar_minute < minute:
                continue
            if prev is not None and prev > 0 and close > 0:
                step = math.log(close / prev)
                total += step * step
            prev = close
        return total


@dataclass(frozen=True)
class CandidateOvernightView:
    """Causal, read-only surface exposed to generated candidates.

    Every accessor answers ``None`` past the cutoff; raw bars, settlement,
    and anything after the entry minute are structurally unreachable.
    """

    day: str
    day_of_week: int               # 0=Monday .. 4=Friday (settlement day)
    evening_open: float
    prior_close: float | None
    prior_rth_high: float | None
    prior_rth_low: float | None
    _bars: tuple[tuple[int, float, float, float, float], ...]
    _cutoff: int

    @classmethod
    def at(cls, session: OvernightSession, cutoff: int) -> "CandidateOvernightView":
        return cls(
            day=session.day,
            day_of_week=datetime.strptime(session.day, "%Y-%m-%d").weekday(),
            evening_open=session.evening_open,
            prior_close=session.prior_close,
            prior_rth_high=session.prior_rth_high,
            prior_rth_low=session.prior_rth_low,
            _bars=tuple(bar for bar in session.bars if bar[0] <= cutoff),
            _cutoff=cutoff,
        )

    def price_at(self, minute: int) -> float | None:
        if minute > self._cutoff:
            return None
        bar = next((bar for bar in self._bars if bar[0] == minute), None)
        return bar[1] if bar else None

    def ret_from_evening_open(self, minute: int) -> float | None:
        """% return from the 18:00 ET session open to ``minute``."""
        price = self.price_at(minute)
        return (price / self.evening_open - 1.0) * 100.0 if price is not None else None

    def ret_from_prior_close(self, minute: int) -> float | None:
        """% return from the prior 16:00 ET settlement to ``minute``."""
        price = self.price_at(minute)
        if price is None or not self.prior_close:
            return None
        return (price / self.prior_close - 1.0) * 100.0

    def first_touch_from_evening_open(self, open_offset: float, after_minute: int) -> int | None:
        """First minute after ``after_minute`` touching evening open + offset.

        Highs answer upside offsets, lows answer downside offsets.
        """
        level = self.evening_open + open_offset
        for minute, _open, high, low, _close in self._bars:
            touched = high >= level if open_offset >= 0 else low <= level
            if after_minute < minute <= self._cutoff and touched:
                return minute
        return None


class OvernightStore:
    """Loads the frozen stitched ES overnight bar file into sessions."""

    def __init__(self, sessions: list[OvernightSession]):
        self._sessions = sessions

    @classmethod
    def load(cls, path: Path | None = None) -> "OvernightStore":
        resolved = path or REPO_ROOT / "research" / "data" / "es_overnight_5min.csv.gz"
        if not resolved.exists():
            raise FileNotFoundError(
                f"{resolved} not found - run research/experiments/prepare_overnight_spx.py "
                "first (outside the loop, with the Polygon/Massive key)."
            )
        by_day: dict[str, dict[str, object]] = {}
        with gzip.open(resolved, "rt", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            next(reader)
            for row in reader:
                day, minute = row[0], int(row[1])
                record = by_day.setdefault(day, {"bars": [], "contract": row[7]})
                record["bars"].append(
                    (minute, float(row[2]), float(row[3]), float(row[4]), float(row[5]))
                )
        sessions: list[OvernightSession] = []
        prior_close: float | None = None
        prior_high: float | None = None
        prior_low: float | None = None
        for day in sorted(by_day):
            bars = tuple(sorted(by_day[day]["bars"]))
            session = OvernightSession(
                day=day,
                bars=bars,
                contract=str(by_day[day]["contract"]),
                prior_close=prior_close,
                prior_rth_high=prior_high,
                prior_rth_low=prior_low,
            )
            sessions.append(session)
            rth = [bar for bar in bars if 930 <= bar[0] <= _SETTLEMENT_BAR]
            if rth:
                prior_high = max(bar[2] for bar in rth)
                prior_low = min(bar[3] for bar in rth)
            settlement = session.settlement
            if settlement is not None:
                prior_close = settlement
        return cls(sessions)

    def sessions(self) -> list[OvernightSession]:
        return list(self._sessions)


#: The vol grid runs past the entry ceiling to 09:30 ET (930) so a morning
#: exit can be model-valued with a causal remaining-vol estimate too.
_VOL_GRID_CEIL = 930


def trailing_remaining_vol(
    sessions: list[OvernightSession],
    window: int = VOL_WINDOW_SESSIONS,
    decay: float | None = None,
) -> dict[str, dict[int, float]]:
    """Causal total-vol estimate per (session day, minute).

    For each session, the estimate at minute m is the square root of the mean
    realized remaining variance from m to settlement over the strictly PRIOR
    ``window`` complete sessions -- known before the session starts, so no
    same-session information leaks into entry pricing.

    ``decay`` switches the equal-weight mean to an EWMA (weight decay^age,
    age 0 = most recent prior session). The v1 evaluator's equal-weight
    window adapts too slowly after volatility shocks, which made options look
    cheap in BOTH directions on post-drop nights (campaign 1's artifact);
    an EWMA closes most of that gap while staying strictly causal.
    """
    grid = tuple(range(ENTRY_MIN_FLOOR, _VOL_GRID_CEIL + 1, 5))
    out: dict[str, dict[int, float]] = {}
    history: list[dict[int, float]] = []
    for session in sessions:
        if len(history) >= window:
            tail = history[-window:]
            if decay is None:
                out[session.day] = {
                    minute: math.sqrt(sum(profile[minute] for profile in tail) / len(tail))
                    for minute in grid
                }
            else:
                weights = [decay ** age for age in range(len(tail) - 1, -1, -1)]
                total = sum(weights)
                out[session.day] = {
                    minute: math.sqrt(
                        sum(weight * profile[minute] for weight, profile in zip(weights, tail))
                        / total
                    )
                    for minute in grid
                }
        if session.is_complete():
            history.append({minute: session.remaining_variance(minute) for minute in grid})
    return out
