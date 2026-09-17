"""Data access for the 0DTE backtest engine. The ONLY module that hits the network.

Two sources:

- ``SessionStore``: intraday underlying bars from the local gzip CSV produced
  by ``research/experiments/download_spy_intraday.py`` (Alpaca SIP, adjusted).
  Adjusted prices are CORRECT for same-day return signals (the dividend
  multiplier cancels within a session) and WRONG for strike space.
- ``AlpacaOptionData``: raw underlying prices and real option leg bars from
  Alpaca (OPRA, available 2024-02 onward). Everything that touches strikes
  goes through here.

Data-quality guards live here, next to the data, not inside strategies:
raw-vs-adjusted strike handling, leg staleness (max 15 minutes before entry),
and degenerate-debit rejection. Each guard exists because its absence produced
a specific wrong number during the founding study (see README).
"""

from __future__ import annotations

import csv
import gzip
import math
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
ET = ZoneInfo("America/New_York")

#: Alpaca's native OPRA history begins here. Requests before it return nothing.
OPTION_DATA_START = date(2024, 2, 1)

#: A leg price older than this (minutes before entry) is stale, not a price.
MAX_LEG_STALENESS_MIN = 15


def occ_symbol(underlying: str, day: date, cp: str, strike: float) -> str:
    """OCC option symbol, e.g. SPY240815C00450000."""
    return f"{underlying}{day:%y%m%d}{cp}{int(round(strike * 1000)):08d}"


@dataclass(frozen=True)
class Session:
    """One trading day of intraday bars: (minutes_since_midnight_ET, o, h, l, c)."""

    day: str                       # ISO date
    bars: tuple[tuple[int, float, float, float, float], ...]

    @property
    def open(self) -> float:
        return self.bars[0][1]

    @property
    def close(self) -> float:
        return self.bars[-1][4]

    @property
    def last_minute(self) -> int:
        return self.bars[-1][0]

    def bar_at(self, hm: int) -> tuple[int, float, float, float, float] | None:
        for b in self.bars:
            if b[0] == hm:
                return b
        return None

    def price_at(self, hm: int) -> float | None:
        b = self.bar_at(hm)
        return b[1] if b else None    # bar OPEN: the tradable price at that minute

    def ret_from_open(self, hm: int) -> float | None:
        """% return open -> minute hm. Adjustment-invariant (same-day ratio)."""
        px = self.price_at(hm)
        return (px / self.open - 1.0) * 100.0 if px is not None else None

    def first_touch(self, level: float, after_hm: int) -> int | None:
        """First minute strictly after `after_hm` whose bar HIGH reaches level."""
        for hm, _o, h, _l, _c in self.bars:
            if hm > after_hm and h >= level:
                return hm
        return None

    def is_full_day(self) -> bool:
        """Reject half days: require an afternoon that reaches 15:45 ET."""
        return self.last_minute >= 15 * 60 + 45


@dataclass(frozen=True)
class CandidateSessionView:
    """Causal, read-only session surface exposed to generated candidates."""

    day: str
    open: float
    _bars: tuple[tuple[int, float, float, float, float], ...]
    _cutoff_hm: int

    @classmethod
    def at(cls, session: Session, cutoff_hm: int) -> "CandidateSessionView":
        return cls(
            day=session.day,
            open=session.open,
            _bars=tuple(bar for bar in session.bars if bar[0] <= cutoff_hm),
            _cutoff_hm=cutoff_hm,
        )

    def price_at(self, hm: int) -> float | None:
        if hm > self._cutoff_hm:
            return None
        bar = next((bar for bar in self._bars if bar[0] == hm), None)
        return bar[1] if bar else None

    def ret_from_open(self, hm: int) -> float | None:
        price = self.price_at(hm)
        return (price / self.open - 1.0) * 100.0 if price is not None else None

    def first_touch(self, level: float, after_hm: int) -> int | None:
        for hm, _open, high, _low, _close in self._bars:
            if after_hm < hm <= self._cutoff_hm and high >= level:
                return hm
        return None

    def first_touch_from_open(self, open_offset: float, after_hm: int) -> int | None:
        """Directional touch: highs for upside offsets, lows for downside offsets."""
        level = self.open + open_offset
        for hm, _open, high, low, _close in self._bars:
            touched = high >= level if open_offset >= 0 else low <= level
            if after_hm < hm <= self._cutoff_hm and touched:
                return hm
        return None


class SessionStore:
    """Loads the downloaded bar file into Session objects."""

    def __init__(self, sessions: list[Session]):
        self._sessions = sessions

    @classmethod
    def load(cls, symbol: str = "SPY") -> "SessionStore":
        path = REPO_ROOT / "research" / "data" / f"{symbol.lower()}_5min.csv.gz"
        if not path.exists():
            raise FileNotFoundError(
                f"{path} not found - run research/experiments/download_spy_intraday.py "
                f"--symbol {symbol} first (outside the loop, with Alpaca credentials)."
            )
        by_day: dict[str, list[tuple[int, float, float, float, float]]] = {}
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            next(reader)
            for row in reader:
                ts = datetime.fromisoformat(row[0]).astimezone(ET)
                by_day.setdefault(ts.date().isoformat(), []).append(
                    (ts.hour * 60 + ts.minute, float(row[1]), float(row[2]),
                     float(row[3]), float(row[4]))
                )
        sessions = [
            Session(day=d, bars=tuple(sorted(bars)))
            for d, bars in sorted(by_day.items())
        ]
        return cls(sessions)

    def sessions(self) -> list[Session]:
        return list(self._sessions)


@dataclass
class AlpacaOptionData:
    """Real option pricing + raw underlying prices for the 2024-02+ window."""

    api_key: str
    api_secret: str
    underlying: str = "SPY"
    _raw_cache: dict[str, tuple[float, float] | None] = field(default_factory=dict)

    @classmethod
    def from_env(cls, underlying: str = "SPY") -> "AlpacaOptionData":
        key = os.getenv("ALPACA_API_KEY", "")
        secret = os.getenv("ALPACA_API_SECRET", "") or os.getenv("ALPACA_SECRET_KEY", "")
        if not key or not secret:
            raise RuntimeError(
                "ALPACA_API_KEY / ALPACA_API_SECRET not set. Real-quote pricing runs "
                "outside the research loop, which strips credentials by design."
            )
        return cls(api_key=key, api_secret=secret, underlying=underlying)

    # -- raw underlying -----------------------------------------------------

    def raw_prices(self, day: date, minutes: list[int]) -> dict[int, float] | None:
        """RAW (unadjusted) bar-open prices at the requested ET minutes.

        Strikes live in raw price space. The stored CSV is adjusted; using it
        for strikes made "ATM" ~3% ITM and produced a 74.9% D/W in the
        founding study. Anything strike-adjacent must come through here.
        """
        from alpaca.data.enums import Adjustment, DataFeed
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        client = StockHistoricalDataClient(self.api_key, self.api_secret)
        bars = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=self.underlying, timeframe=TimeFrame.Minute,
            start=datetime.combine(day, time(13, 0), tzinfo=timezone.utc),
            end=datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=timezone.utc),
            adjustment=Adjustment.RAW, feed=DataFeed.SIP,
        )).data.get(self.underlying, [])
        out: dict[int, float] = {}
        closes: dict[int, float] = {}
        for bar in bars:
            ts = bar.timestamp.astimezone(ET)
            hm = ts.hour * 60 + ts.minute
            if hm in minutes:
                out[hm] = float(bar.open)
            closes[hm] = float(bar.close)
        # 16:00 settlement proxy: close of the 15:59 bar.
        if 16 * 60 in minutes and 15 * 60 + 59 in closes:
            out[16 * 60] = closes[15 * 60 + 59]
        return out if len(out) == len(set(minutes)) else None

    # -- real option legs ---------------------------------------------------

    def leg_prices(
        self, day: date, legs: list[tuple[str, float]], entry_minute: int
    ) -> dict[tuple[str, float], float] | None:
        """Close of the freshest 1-min option bar at/just before entry, per leg.

        Rejects legs whose freshest bar is older than MAX_LEG_STALENESS_MIN:
        option quotes go stale rather than absent, so an old bar returns a
        plausible number that is not a price.
        """
        if day < OPTION_DATA_START:
            return None
        from alpaca.data.historical import OptionHistoricalDataClient
        from alpaca.data.requests import OptionBarsRequest
        from alpaca.data.timeframe import TimeFrame

        client = OptionHistoricalDataClient(self.api_key, self.api_secret)
        syms = {occ_symbol(self.underlying, day, cp, k): (cp, k) for cp, k in legs}
        try:
            bars = client.get_option_bars(OptionBarsRequest(
                symbol_or_symbols=list(syms), timeframe=TimeFrame.Minute,
                start=datetime.combine(day, time(13, 30), tzinfo=timezone.utc),
                end=datetime.combine(day, time(21, 10), tzinfo=timezone.utc),
            )).data
        except Exception:  # noqa: BLE001 - a fetch failure is a skip, not a crash
            return None

        out: dict[tuple[str, float], float] = {}
        for sym, leg in syms.items():
            best: tuple[int, float] | None = None
            for bar in bars.get(sym, []):
                ts = bar.timestamp.astimezone(ET)
                hm = ts.hour * 60 + ts.minute
                if hm <= entry_minute and (best is None or hm > best[0]):
                    best = (hm, float(bar.close))
            if best is None or entry_minute - best[0] > MAX_LEG_STALENESS_MIN:
                return None
            out[leg] = best[1]
        return out

    def leg_pricer(self) -> Callable:
        """Adapter handed to BacktestEngine.run(leg_pricer=...)."""
        return self.leg_prices
