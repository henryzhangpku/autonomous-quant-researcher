"""Freeze BTC clock-aligned windows matching Kalshi's contract horizons.

``prepare_crypto_sessions`` cuts the tape into equity-relative blocks
(weekend / overnight / rth) because that is where the structural story lives
for a trader who also watches equities. Kalshi does not settle on those
blocks: its BTC up/down contracts settle on every **clock hour** and every
**15 minutes**, around the clock, weekends included.

This stager cuts the same tape on Kalshi's grid instead, so the question the
gate battery answers is the question the contract asks:

    hourly        [HH:00, HH+1:00)   matches KXBTCD hourly above/below
    quarter_hour  [HH:MM, +15min)    matches KXBTC15M up/down

One row per window. The decision is taken at the window's OPEN, every feature
is computed strictly from windows that CLOSED before that instant, and the
payoff is that window's own return — which is exactly what the contract
settles on (up or down over the window). Emitted in the bars_universe row
shape so the frozen validator, its gates and its splits apply unchanged.

Note on portfolio semantics: ``session`` is the calendar date, so the 24
hourly (or 96 quarter-hour) windows of one day are 24 (or 96) trades sharing
one portfolio observation. That is deliberate — it prevents a single day's
regime from counting as 96 independent days in the gates.

What this CANNOT answer: anything about the Kalshi book. These are bars.
Order-book depth, queue position and fill probability are invisible here, and
the maker-side question needs them (see research/missions/kalshi-maker-side).
This stager answers the prior question — whether the DIRECTION is forecastable
at Kalshi's horizons at all.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WINDOW_KINDS = {"hourly": 60, "quarter_hour": 15}
TIMEFRAMES = {"hourly": "1Hour", "quarter_hour": "15Min"}
WARMUP_WINDOWS = 20
# Bars can be missing on a thin venue; a window whose open cannot be priced
# from a bar at or before it is dropped rather than approximated forward.
PRICE_TOLERANCE_WINDOWS = 4


def _price_at(index: dict[datetime, float], moment: datetime, step: timedelta,
              tolerance: int = PRICE_TOLERANCE_WINDOWS) -> float | None:
    """Last known close at or before `moment`, within `tolerance` steps.

    Never looks forward: reading a bar after the decision instant would be a
    peek at the very move the row is trying to predict.
    """
    probe = moment
    for _ in range(tolerance + 1):
        if probe in index:
            return index[probe]
        probe -= step
    return None


def _signed_streak(prior_rets: list[float], cap: int = 5) -> int:
    """Signed run length of consecutive same-direction windows, most recent
    first. +3 means three up-windows in a row; -2 means two down. A flat most
    recent window is 0 — direction is undefined, not "up"."""
    if not prior_rets or prior_rets[-1] == 0:
        return 0
    sign = 1 if prior_rets[-1] > 0 else -1
    run = 0
    for value in reversed(prior_rets):
        if run >= cap or value == 0 or (value > 0) != (sign > 0):
            break
        run += 1
    return sign * run


def _floor_to(moment: datetime, minutes: int) -> datetime:
    """Snap an instant down onto the window grid."""
    discard = (moment.minute % minutes)
    return moment.replace(minute=moment.minute - discard, second=0, microsecond=0)


def build_rows(bars: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    minutes = WINDOW_KINDS[kind]
    step = timedelta(minutes=minutes)
    index = {b["ts"]: b["close"] for b in bars}
    if not bars:
        return []

    # Enumerate every window on the grid between the first and last bar.
    first = _floor_to(bars[0]["ts"].astimezone(UTC), minutes)
    last = _floor_to(bars[-1]["ts"].astimezone(UTC), minutes)
    raw: list[dict[str, Any]] = []
    cursor = first
    while cursor <= last:
        entry = _price_at(index, cursor, step)
        exit_price = _price_at(index, cursor + step, step)
        if entry and exit_price and entry > 0:
            raw.append({"start": cursor, "entry": entry,
                        "ret": exit_price / entry - 1.0})
        cursor += step

    rows: list[dict[str, Any]] = []
    day_back = timedelta(hours=24)
    week_back = timedelta(days=7)
    for position in range(WARMUP_WINDOWS, len(raw)):
        current = raw[position]
        history = raw[position - WARMUP_WINDOWS:position]  # strictly prior windows
        prior_rets = [item["ret"] for item in history]
        entry_utc = current["start"]
        day_ago = _price_at(index, entry_utc - day_back, step)
        week_ago = _price_at(index, entry_utc - week_back, step)
        if day_ago is None or week_ago is None:
            continue
        local = entry_utc
        rows.append({
            "session": local.date().isoformat(),
            "symbol": f"BTC-{kind.upper()}",
            "features": {
                "prior_window_ret": round(prior_rets[-1], 8),
                "prior_2window_ret": round(sum(prior_rets[-2:]), 8),
                "prior_4window_ret": round(sum(prior_rets[-4:]), 8),
                "mean_prior_ret": round(statistics.mean(prior_rets), 8),
                "vol_prior": round(statistics.pstdev(prior_rets), 8),
                "ret_24h": round(current["entry"] / day_ago - 1.0, 8),
                "ret_7d": round(current["entry"] / week_ago - 1.0, 8),
                # Count of up-moves in the last three windows, 0..3. Kept for
                # continuity with prepare_crypto_sessions, but it cannot
                # express "after three DOWN windows": it is never negative, so
                # eight streak-down hypotheses fired zero trades in the first
                # hourly study and half the streak space was silently
                # untestable. `streak_signed` below is the fix.
                "streak_up": float(sum(1 for r in prior_rets[-3:] if r > 0)),
                # Signed run length of consecutive same-direction windows,
                # capped at +/-5: positive for an up-run, negative for a
                # down-run, 0 when the last window is flat. This is what a
                # streak hypothesis actually needs.
                "streak_signed": float(_signed_streak(prior_rets)),
                # Clock position is the whole point of a Kalshi grid: the
                # contract that settles at 04:00 UTC is a different animal
                # from the one settling at 14:30 when US equities open.
                "hour_utc": float(local.hour),
                "minute_of_hour": float(local.minute),
                "day_of_week": float(local.weekday()),
                "is_weekend": 1.0 if local.weekday() >= 5 else 0.0,
                "month": float(local.month),
            },
            # The window's own return is the tradeable payoff and is exactly
            # what the contract settles on. Both payoff keys carry it so the
            # frozen validator's modes both address this window.
            "next_open_to_close": round(current["ret"], 8),
            "next_close_to_close": round(current["ret"], 8),
        })
    return rows


def _splits(sessions: list[str]) -> dict[str, list[str]]:
    ordered = sorted(set(sessions))
    if len(ordered) < 3:
        raise ValueError("not enough distinct sessions to split")
    a, b = int(len(ordered) * 0.6), int(len(ordered) * 0.8)
    return {
        "discovery": [ordered[0], ordered[a - 1]],
        "validation": [ordered[a], ordered[b - 1]],
        "holdout": [ordered[b], ordered[-1]],
    }


def prepare(*, kind: str, start: str, end: str, out: Path,
            symbol: str = "BTC/USD") -> dict[str, Any]:
    if kind not in WINDOW_KINDS:
        raise ValueError(f"kind must be one of {tuple(WINDOW_KINDS)}")
    try:
        from dotenv import load_dotenv
        load_dotenv(REPOSITORY_ROOT / ".env")
    except ImportError:
        pass
    from research.providers.alpaca import AlpacaHistoricalProvider

    provider = AlpacaHistoricalProvider.from_env()
    timeframe = TIMEFRAMES[kind]
    cursor = datetime.fromisoformat(start).replace(tzinfo=UTC)
    end_utc = datetime.fromisoformat(end).replace(tzinfo=UTC)
    bars: list[dict[str, Any]] = []
    seen: set[datetime] = set()
    while cursor < end_utc:
        chunk_end = min(cursor + timedelta(days=30), end_utc)
        snapshot = provider.bars(asset_class="crypto", symbols=(symbol,),
                                 start_utc=cursor, end_utc=chunk_end,
                                 timeframe=timeframe)
        for bar in snapshot.bars:
            # The provider's Bar carries `timestamp_utc`. Probing a list of
            # plausible names and skipping on miss silently produced an empty
            # tape once; demand the field instead so a contract change is a
            # crash rather than "no decidable windows".
            ts = getattr(bar, "timestamp_utc")
            close = float(getattr(bar, "close", 0.0) or 0.0)
            if ts is None or close <= 0:
                continue
            ts = ts.astimezone(UTC).replace(second=0, microsecond=0)
            if ts in seen:
                continue
            seen.add(ts)
            bars.append({"ts": ts, "close": close})
        cursor = chunk_end
    bars.sort(key=lambda b: b["ts"])
    rows = build_rows(bars, kind)
    if not rows:
        raise RuntimeError(f"no decidable {kind} windows in {start}..{end}")

    out.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows).encode("utf-8")
    (out / "sessions.jsonl").write_bytes(payload)
    manifest = {
        "provider": "alpaca", "asset_class": "crypto", "symbol": symbol,
        "timeframe": timeframe, "kind": kind, "window_minutes": WINDOW_KINDS[kind],
        "bars": len(bars), "row_count": len(rows),
        "first_session": rows[0]["session"], "last_session": rows[-1]["session"],
        "suggested_splits": _splits([r["session"] for r in rows]),
        "data_sha256": hashlib.sha256(payload).hexdigest(),
        "prepared_at_utc": datetime.now(UTC).isoformat(),
        "limitations": [
            "bar closes proxy the window's settlement reference; Kalshi's own "
            "reference may differ in construction",
            "no order-book state: depth, queue position and fill probability "
            "are invisible to a bar snapshot",
            "one calendar date is one portfolio observation, so intraday "
            "windows share a session in the gates",
        ],
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in
                      ("kind", "bars", "row_count", "first_session",
                       "last_session", "data_sha256")}, indent=1))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--kind", required=True, choices=tuple(WINDOW_KINDS))
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--symbol", default="BTC/USD")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    prepare(kind=args.kind, start=args.start, end=args.end, out=args.out,
            symbol=args.symbol)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
