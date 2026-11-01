"""Daily split- and dividend-adjusted bars through the repository's Alpaca provider.

Two passes per symbol: ``adjustment="all"`` (splits and dividends — the
series returns are computed from) and ``adjustment="raw"`` (as traded). The
raw pass exists only to audit the adjustment: ``raw_close / close`` is the
cumulative adjustment factor, and it may step only on a corporate-action day.

An all-adjusted series is rescaled backwards every time a new dividend is
paid, so its *levels* are not point-in-time; its *returns* are. Use the
levels only through returns.

Every provider response is cached on disk as the provider snapshot JSON; a
rebuild reads the cache and never calls the network.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Callable, Mapping, Sequence

from research.prereg import xnys
from research.providers.alpaca import AlpacaHistoricalProvider

SYMBOLS = ("NVDA", "AMD", "AVGO", "TSM", "MU", "SMCI", "ORCL", "MSFT", "AMZN", "GOOGL",
           "META", "CRWV", "NBIS")
BENCHMARKS = ("SPY", "QQQ", "SMH")
DEFAULT_START = date(2023, 1, 1)
ADJUSTMENTS = ("all", "raw")
FEED = "sip"
# A one-day adjusted move beyond this is reported for review (a missed split
# shows up here as roughly -50% to -95%).
OUTLIER_LOG_RETURN = math.log(1.4)
# Day-over-day change in raw/adjusted beyond this is a corporate-action step.
FACTOR_STEP = 0.02

PRICE_COLUMNS = ("symbol", "session", "open", "high", "low", "close", "volume", "vwap",
                 "trade_count", "raw_close", "adj_factor")


def request_factory(adjustment: str, feed: str = FEED) -> Callable[..., Any]:
    """A request factory for the provider that pins ``adjustment`` and ``feed``.

    The provider's default request is split-adjusted only; total-return work
    needs dividends too, so this supplies the request instead of editing the
    provider's default.
    """

    def make(*, asset_class: str, symbols: tuple[str, ...], start_utc: datetime,
             end_utc: datetime, timeframe: str, limit: int | None) -> Any:
        if asset_class != "equity" or timeframe != "1Day":
            raise ValueError("outcome prices are daily equity bars only")
        from alpaca.data.enums import Adjustment, DataFeed  # noqa: PLC0415
        from alpaca.data.requests import StockBarsRequest  # noqa: PLC0415
        from alpaca.data.timeframe import TimeFrame  # noqa: PLC0415

        kwargs: dict[str, Any] = {
            "symbol_or_symbols": symbols[0] if len(symbols) == 1 else list(symbols),
            "timeframe": TimeFrame.Day, "start": start_utc, "end": end_utc,
            "adjustment": Adjustment(adjustment), "feed": DataFeed(feed),
        }
        if limit is not None:
            kwargs["limit"] = limit
        return StockBarsRequest(**kwargs)

    return make


@dataclass
class PriceCache:
    root: Path

    def path(self, symbol: str, adjustment: str, start: date, end: date) -> Path:
        return self.root / adjustment / f"{symbol}_{start.isoformat()}_{end.isoformat()}.json"


def fetch_snapshot(cache: PriceCache, symbol: str, adjustment: str, start: date, end: date,
                   provider_for: Callable[[str], AlpacaHistoricalProvider] | None,
                   ) -> tuple[dict[str, Any], str]:
    """The provider snapshot JSON for one symbol/adjustment, from cache when present."""
    path = cache.path(symbol, adjustment, start, end)
    if path.exists():
        raw = path.read_bytes()
        return json.loads(raw), hashlib.sha256(raw).hexdigest()
    if provider_for is None:
        raise FileNotFoundError(f"not cached and no provider: {path}")
    provider = provider_for(adjustment)
    snapshot = provider.bars(
        asset_class="equity", symbols=(symbol,),
        start_utc=datetime(start.year, start.month, start.day, tzinfo=timezone.utc),
        end_utc=datetime(end.year, end.month, end.day, tzinfo=timezone.utc) + timedelta(days=1),
        timeframe="1Day",
    )
    payload = snapshot.to_json()
    payload["provenance"]["parameters"]["adjustment"] = adjustment
    payload["provenance"]["parameters"]["feed"] = FEED
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    _atomic_write(path, raw)
    return payload, hashlib.sha256(raw).hexdigest()


def _session(timestamp: str) -> date:
    # Alpaca stamps a daily bar at 04:00/05:00 UTC, i.e. midnight New York,
    # on the session date itself.
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date()


def _untraded(bar: Mapping[str, Any]) -> bool:
    return not bar.get("volume") and not bar.get("trade_count")


def merge_rows(symbol: str, adjusted: Mapping[str, Any], raw: Mapping[str, Any]
               ) -> tuple[list[dict[str, Any]], list[str]]:
    """Adjusted bars joined to the raw close; untraded placeholder bars are dropped.

    A bar with zero volume and zero trades is a carried-forward placeholder
    (NBIS, halted as YNDX until 2024-10-21, is served at a constant 18.94
    with no volume for every session before). It is not a price; it is
    excluded and its session reported, never kept as a flat return.
    """
    raw_close = {_session(bar["timestamp_utc"]): bar["close"] for bar in raw["bars"]}
    rows = []
    dropped: list[str] = []
    seen: set[date] = set()
    for bar in adjusted["bars"]:
        session = _session(bar["timestamp_utc"])
        if session in seen:
            raise ValueError(f"{symbol}: more than one daily bar for {session}")
        seen.add(session)
        if _untraded(bar):
            dropped.append(session.isoformat())
            continue
        rc = raw_close.get(session)
        rows.append({
            "symbol": symbol, "session": session, "open": bar["open"], "high": bar["high"],
            "low": bar["low"], "close": bar["close"], "volume": bar["volume"], "vwap": bar["vwap"],
            "trade_count": bar["trade_count"], "raw_close": rc,
            "adj_factor": None if rc is None or not bar["close"] else rc / bar["close"],
        })
    rows.sort(key=lambda r: r["session"])
    return rows, dropped


def sanity(rows: Sequence[Mapping[str, Any]], *, calendar_end: date | None = None) -> dict[str, Any]:
    """Adjustment and coverage audit for one symbol's merged rows."""
    if not rows:
        return {"sessions": 0}
    outliers, steps, non_sessions = [], [], []
    for prev, cur in zip(rows, rows[1:]):
        if prev["close"] > 0 and cur["close"] > 0:
            move = math.log(cur["close"] / prev["close"])
            if abs(move) > OUTLIER_LOG_RETURN:
                outliers.append({"session": cur["session"].isoformat(), "log_return": round(move, 4)})
        if prev["adj_factor"] and cur["adj_factor"]:
            change = cur["adj_factor"] / prev["adj_factor"] - 1.0
            if abs(change) > FACTOR_STEP:
                steps.append({"session": cur["session"].isoformat(), "factor_change": round(change, 4),
                              "raw_close_ratio": round(cur["raw_close"] / prev["raw_close"], 4)})
    sessions = [r["session"] for r in rows]
    for day in sessions:
        try:
            if not xnys.is_session(day):
                non_sessions.append(day.isoformat())
        except xnys.CalendarRangeError:
            pass
    expected = xnys.sessions(sessions[0], calendar_end or sessions[-1])
    present = set(sessions)
    missing = [d.isoformat() for d in expected if d not in present]
    return {
        "sessions": len(rows), "first_session": sessions[0].isoformat(),
        "last_session": sessions[-1].isoformat(),
        "missing_xnys_sessions": missing, "bars_on_non_sessions": non_sessions,
        "adjusted_outlier_days": outliers, "adjustment_factor_steps": steps,
        "rows_without_raw_close": sum(1 for r in rows if r["raw_close"] is None),
    }


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(round(value, 8))
    return str(value)


def prices_csv(rows: Sequence[Mapping[str, Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(PRICE_COLUMNS)
    for row in rows:
        writer.writerow([_fmt(row.get(c)) for c in PRICE_COLUMNS])
    return buffer.getvalue().encode("utf-8")


def build(symbols: Sequence[str], start: date, end: date, cache: PriceCache,
          provider_for: Callable[[str], AlpacaHistoricalProvider] | None
          ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Merged rows for every symbol and a per-symbol audit with input hashes."""
    all_rows: list[dict[str, Any]] = []
    audit: dict[str, Any] = {}
    for symbol in symbols:
        snapshots, hashes = {}, {}
        for adjustment in ADJUSTMENTS:
            try:
                snapshots[adjustment], hashes[adjustment] = fetch_snapshot(
                    cache, symbol, adjustment, start, end, provider_for)
            except Exception as exc:  # noqa: BLE001 - a missing symbol is reported, not fatal
                audit[symbol] = {"sessions": 0, "error": f"{type(exc).__name__}: {exc}"}
                break
        if symbol in audit:
            continue
        rows, dropped = merge_rows(symbol, snapshots["all"], snapshots["raw"])
        all_rows.extend(rows)
        audit[symbol] = {**sanity(rows, calendar_end=_last_session_on_or_before(end)),
                         "untraded_bars_dropped": len(dropped),
                         "untraded_range": [dropped[0], dropped[-1]] if dropped else None,
                         "cache_sha256": hashes,
                         "provenance": snapshots["all"]["provenance"]}
    all_rows.sort(key=lambda r: (r["symbol"], r["session"]))
    return all_rows, audit


def _last_session_on_or_before(day: date) -> date:
    while not xnys.is_session(day):
        day -= timedelta(days=1)
    return day


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("wb", delete=False, dir=path.parent, prefix=f".{path.name}.",
                            suffix=".tmp") as temporary:
        temporary.write(data)
        temp_path = Path(temporary.name)
    try:
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
