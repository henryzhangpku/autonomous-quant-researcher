"""Freeze RTH intraday-session snapshots for the bars-universe validator.

One row per regular-trading-hours session per symbol, decided intraday at a
fixed clock time (default 10:30 ET). Features are computed strictly from bars
that COMPLETED before the decision instant plus the decision bar's open (the
tradable price at that minute, per research/backtest/data.py semantics) — no
post-decision bar is ever read, so leakage is structurally impossible.

The tradeable payoff is decision-time price -> session close, stored under
``next_open_to_close`` so the frozen validator, its gates, and its splits
apply unchanged; ``next_close_to_close`` keeps the session-close -> next-
session-close reference horizon.

Output: <out>/sessions.jsonl + <out>/manifest.json (sha256, provenance,
suggested chronological splits) — the same shape as prepare_bars_universe.

SPY 5-minute bars are already staged at research/data/spy_5min.csv.gz
(columns timestamp_utc,open,high,low,close,volume, 2016+); when the request
is for SPY and the staged range covers it, the staged file is used instead of
re-fetching.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Sequence
from zoneinfo import ZoneInfo

from research.experiments.prepare_bars_universe import _suggested_splits

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ET = ZoneInfo("America/New_York")

SPY_STAGED_PATH = REPOSITORY_ROOT / "research" / "data" / "spy_5min.csv.gz"

RTH_OPEN_HM = 9 * 60 + 30          # 09:30 ET, first bar's open minute
RTH_CLOSE_HM = 16 * 60             # 16:00 ET; last bar opens 15:55
OPENING_RANGE_END_HM = 10 * 60     # first 30 minutes
FIRST_HOUR_END_HM = 10 * 60 + 30   # first hour of the session

# vol_ratio_open averages 20 prior sessions; that also covers the 6 prior
# closes prior_ret_5d/rv_5d need.
MIN_PRIOR_SESSIONS = 20

# Alpaca pages long ranges; walk in 90-day slices so a silent truncation
# cannot masquerade as a shorter history (same discipline as
# prepare_crypto_sessions).
FETCH_CHUNK_DAYS = 90

BarDict = dict[str, Any]  # {"ts": aware datetime, "open"/"high"/"low"/"close"/"volume": float}
FetchFn = Callable[[str, datetime, datetime], list[BarDict]]


def _safe(value: float, fallback: float = 0.0) -> float:
    """Finite or the fallback; a NaN/inf feature poisons every candidate."""
    return round(value, 8) if isinstance(value, (int, float)) and math.isfinite(value) else fallback


def _parse_decision_hhmm(value: str) -> int:
    try:
        hour, minute = (int(part) for part in value.split(":"))
    except ValueError:
        raise ValueError(f"--decision-hhmm must look like '10:30', got {value!r}") from None
    hm = hour * 60 + minute
    if hm < OPENING_RANGE_END_HM or hm >= RTH_CLOSE_HM or (hm - RTH_OPEN_HM) % 5:
        raise ValueError(
            "decision time must be a 5-minute boundary inside "
            "10:00..15:55 ET (the opening range must be complete)"
        )
    return hm


def _sessions_from_bars(bars: list[BarDict]) -> list[tuple[str, list[tuple[int, float, float, float, float, float]]]]:
    """Group 5-minute bars into RTH sessions: (day, [(hm, o, h, l, c, v), ...])."""
    by_day: dict[str, list[tuple[int, float, float, float, float, float]]] = {}
    for bar in sorted(bars, key=lambda item: item["ts"]):
        moment = bar["ts"].astimezone(ET)
        hm = moment.hour * 60 + moment.minute
        if not (RTH_OPEN_HM <= hm < RTH_CLOSE_HM):
            continue
        by_day.setdefault(moment.date().isoformat(), []).append(
            (hm, float(bar["open"]), float(bar["high"]), float(bar["low"]),
             float(bar["close"]), float(bar.get("volume") or 0.0))
        )
    return [(day, sorted(set(bars_for_day))) for day, bars_for_day in sorted(by_day.items())]


def _feature_rows(symbol: str,
                  sessions: list[tuple[str, list[tuple[int, float, float, float, float, float]]]],
                  decision_hm: int) -> list[dict[str, Any]]:
    """One row per decidable session; features never touch bars at/after the
    decision bar's own trades (only its open, the tradable price, is read)."""
    rows: list[dict[str, Any]] = []
    closes = [session_bars[-1][4] for _day, session_bars in sessions]
    first_hour_volumes = [
        sum(bar[5] for bar in session_bars if bar[0] < FIRST_HOUR_END_HM)
        for _day, session_bars in sessions
    ]
    for index in range(MIN_PRIOR_SESSIONS, len(sessions) - 1):
        day, session_bars = sessions[index]
        next_close = closes[index + 1]
        # A full RTH session opens at 09:30 and trades into the close; skip
        # partial tapes (late starts, early halts) rather than computing
        # features on a session that is not the thing the name promises.
        if not session_bars or session_bars[0][0] != RTH_OPEN_HM or session_bars[-1][0] < RTH_CLOSE_HM - 5:
            continue
        decision_bar = next((bar for bar in session_bars if bar[0] == decision_hm), None)
        if decision_bar is None:
            continue
        decision_price = decision_bar[1]  # bar OPEN: the tradable price at that minute
        session_open = session_bars[0][1]
        session_close = session_bars[-1][4]
        if not (decision_price > 0 and session_open > 0 and session_close > 0 and next_close > 0):
            continue
        # Strictly completed bars: everything the decision may read.
        completed = [bar for bar in session_bars if bar[0] < decision_hm]
        opening_range = [bar for bar in session_bars if bar[0] < OPENING_RANGE_END_HM]
        or_high = max(bar[2] for bar in opening_range)
        or_low = min(bar[3] for bar in opening_range)
        or_span = or_high - or_low
        volume_sum = sum(bar[5] for bar in completed)
        vwap = (sum(bar[4] * bar[5] for bar in completed) / volume_sum) if volume_sum else 0.0
        prior_first_hour = first_hour_volumes[index - MIN_PRIOR_SESSIONS:index]
        prior_first_hour_mean = statistics.mean(prior_first_hour)
        # Returns of the five COMPLETED sessions before today; today's own
        # close is not known at the decision instant.
        prior_returns = [
            closes[back] / closes[back - 1] - 1.0 if closes[back - 1] else 0.0
            for back in range(index - 5, index)
        ]
        rows.append({
            "session": day,
            "symbol": symbol,
            "features": {
                "day_of_week": float(datetime.strptime(day, "%Y-%m-%d").weekday()),
                "session_ret_so_far": _safe(decision_price / session_open - 1.0),
                "vwap_dist": _safe(decision_price / vwap - 1.0) if vwap else 0.0,
                "or_ret": _safe(opening_range[-1][4] / session_open - 1.0),
                "or_break": min(1.0, max(0.0, _safe((decision_price - or_low) / or_span, 0.5)))
                            if or_span else 0.5,
                "prior_ret_1d": _safe(closes[index - 1] / closes[index - 2] - 1.0),
                "prior_ret_5d": _safe(closes[index - 1] / closes[index - 6] - 1.0),
                "gap_open": _safe(session_open / closes[index - 1] - 1.0),
                "rv_5d": _safe(statistics.stdev(prior_returns)),
                "vol_ratio_open": _safe(first_hour_volumes[index] / prior_first_hour_mean, 1.0)
                                  if prior_first_hour_mean else 1.0,
            },
            # The tradeable horizon: decision-time price -> this session's close.
            "next_open_to_close": _safe(session_close / decision_price - 1.0),
            # Reference horizon: this close -> next session's close.
            "next_close_to_close": _safe(next_close / session_close - 1.0),
        })
    return rows


def _load_staged_spy(start: str, end: str, path: Path = SPY_STAGED_PATH) -> list[BarDict] | None:
    """Bars from the staged SPY file when it covers [start, end], else None."""
    if not path.exists():
        return None
    bars: list[BarDict] = []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        for row in reader:
            bars.append({
                "ts": datetime.fromisoformat(row[0]).astimezone(UTC),
                "open": float(row[1]), "high": float(row[2]),
                "low": float(row[3]), "close": float(row[4]),
                "volume": float(row[5]) if len(row) > 5 else 0.0,
            })
    if not bars:
        return None
    days = [bar["ts"].astimezone(ET).date().isoformat() for bar in bars]
    if min(days) > start or max(days) < end:
        return None
    start_day, end_day = start, end
    return [bar for bar in bars
            if start_day <= bar["ts"].astimezone(ET).date().isoformat() <= end_day]


def _collect(snapshot: Any) -> list[BarDict]:
    return [{
        "ts": bar.timestamp_utc, "open": bar.open, "high": bar.high,
        "low": bar.low, "close": bar.close, "volume": bar.volume or 0.0,
    } for bar in snapshot.bars]


def _chunked(start_utc: datetime, end_utc: datetime) -> list[tuple[datetime, datetime]]:
    chunks = []
    cursor = start_utc
    while cursor < end_utc:
        chunk_end = min(cursor + timedelta(days=FETCH_CHUNK_DAYS), end_utc)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end
    return chunks


def _alpaca_fetch(provider: Any) -> FetchFn:
    def fetch(symbol: str, start_utc: datetime, end_utc: datetime) -> list[BarDict]:
        bars: list[BarDict] = []
        for chunk_start, chunk_end in _chunked(start_utc, end_utc):
            snapshot = provider.bars(
                asset_class="equity", symbols=(symbol,),
                start_utc=chunk_start, end_utc=chunk_end, timeframe="5Min",
            )
            bars.extend(_collect(snapshot))
        return bars
    return fetch


def _massive_fetch(provider: Any) -> FetchFn:
    def fetch(symbol: str, start_utc: datetime, end_utc: datetime) -> list[BarDict]:
        bars: list[BarDict] = []
        for chunk_start, chunk_end in _chunked(start_utc, end_utc):
            snapshot = provider.stock_aggregate_bars(
                ticker=symbol, start_utc=chunk_start, end_utc=chunk_end,
                multiplier=5, timespan="minute",
            )
            bars.extend(_collect(snapshot))
        return bars
    return fetch


def _resolve_provider(name: str) -> tuple[str, FetchFn]:
    """Pick the bar source explicitly; never silently fall back between them.

    'auto' prefers Alpaca (the stated primary) and uses Polygon/Massive only
    when Alpaca has no credentials; the choice is recorded in the manifest.
    """
    from research.providers.contracts import CapabilityProbeError
    from research.providers.alpaca import AlpacaHistoricalProvider
    from research.providers.massive import MassiveHistoricalProvider

    # Provider credentials live in the repo .env; not every launcher inherits them.
    try:
        from dotenv import load_dotenv  # noqa: PLC0415

        load_dotenv(REPOSITORY_ROOT / ".env")
    except ImportError:
        pass

    if name in {"alpaca", "auto"}:
        try:
            return "alpaca", _alpaca_fetch(AlpacaHistoricalProvider.from_env())
        except CapabilityProbeError:
            if name == "alpaca":
                raise
    return "massive", _massive_fetch(MassiveHistoricalProvider.from_env())


def prepare(*, symbols: tuple[str, ...], start: str, end: str, out: Path,
            decision_hhmm: str = "10:30", source: str = "auto") -> dict[str, Any]:
    decision_hm = _parse_decision_hhmm(decision_hhmm)
    provider_name, fetch = _resolve_provider(source)
    start_utc = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=UTC)
    end_utc = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=UTC) + timedelta(days=1)

    by_symbol: dict[str, list[BarDict]] = {}
    staged_sources: dict[str, str] = {}
    for symbol in symbols:
        staged = _load_staged_spy(start, end, SPY_STAGED_PATH) if symbol == "SPY" else None
        if staged is not None:
            by_symbol[symbol] = staged
            try:
                staged_sources[symbol] = str(SPY_STAGED_PATH.relative_to(REPOSITORY_ROOT))
            except ValueError:  # tests stage outside the repo
                staged_sources[symbol] = str(SPY_STAGED_PATH)
        else:
            by_symbol[symbol] = fetch(symbol, start_utc, end_utc)
    missing = [symbol for symbol in symbols if not by_symbol.get(symbol)]
    if missing:
        raise ValueError(f"no bars returned for: {', '.join(missing)}; refusing partial universe")

    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        rows.extend(_feature_rows(symbol, _sessions_from_bars(by_symbol[symbol]), decision_hm))
    rows.sort(key=lambda row: (row["session"], row["symbol"]))
    if not rows:
        raise ValueError("no decidable sessions after warmup; widen the date range")

    out.mkdir(parents=True, exist_ok=True)
    data_path = out / "sessions.jsonl"
    # Write bytes, not text: on Windows write_text translates \n to \r\n, so a
    # hash taken over the pre-write string never matches the file read back.
    payload = ("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n").encode("utf-8")
    data_path.write_bytes(payload)
    data_hash = hashlib.sha256(payload).hexdigest()

    sessions = sorted({row["session"] for row in rows})
    manifest = {
        "provider": provider_name, "asset_class": "equity", "timeframe": "5Min",
        "symbols": list(symbols), "requested_start": start, "requested_end": end,
        "first_session": sessions[0], "last_session": sessions[-1],
        "session_count": len(sessions), "row_count": len(rows),
        "data_sha256": data_hash,
        "decision_hhmm": decision_hhmm,
        "staged_sources": staged_sources,
        "suggested_splits": {name: list(window) for name, window in _suggested_splits(sessions).items()},
        "prepared_at_utc": datetime.now(UTC).isoformat(),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                       encoding="utf-8")
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", required=True, help="comma-separated, e.g. SPY,QQQ,IWM")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--decision-hhmm", default="10:30",
                        help="ET decision time, 5-min boundary in 10:00..15:55 (default 10:30)")
    parser.add_argument("--source", default="auto", choices=("auto", "alpaca", "massive"))
    args = parser.parse_args(argv)
    symbols = tuple(symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip())
    manifest = prepare(symbols=symbols, start=args.start, end=args.end, out=args.out,
                       decision_hhmm=args.decision_hhmm, source=args.source)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
