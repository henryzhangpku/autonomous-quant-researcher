"""Freeze BTC session windows as decidable rows for the bars evaluator.

Crypto trades continuously while equities do not, so the Friday-close to
Monday-open window is a real structural feature rather than a statistical
accident: information accumulates for 65.5 hours with no equity market to
price it. This snapshot cuts BTC's hourly tape into the three windows a
trader can actually take:

    weekend    Fri 16:00 ET -> Mon 09:30 ET   (65.5h, no equity session)
    overnight  16:00 ET     -> next 09:30 ET  (17.5h, weeknights)
    rth        09:30 ET     -> 16:00 ET       (6.5h, equity hours)

Each row is one window. The decision is taken at the window's OPEN, features
are computed strictly from bars at or before that instant, and the payoff is
that window's own return. Emitted in the bars_universe row shape so the
frozen validator, its gates, and its splits apply unchanged — the evaluator
is not re-implemented for crypto.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ET = ZoneInfo("America/New_York")
SESSION_KINDS = ("weekend", "overnight", "rth", "monday_rth")
WARMUP_WINDOWS = 20


def _et(moment: datetime) -> datetime:
    return moment.astimezone(ET)


def _window_bounds(bars: list[dict[str, Any]], kind: str) -> list[tuple[datetime, datetime]]:
    """Enumerate (open, close) instants for one window kind over the tape."""
    if not bars:
        return []
    first, last = bars[0]["ts"], bars[-1]["ts"]
    day = _et(first).date()
    end_day = _et(last).date()
    bounds: list[tuple[datetime, datetime]] = []
    while day <= end_day:
        weekday = day.weekday()  # 0 Mon .. 6 Sun
        if kind == "weekend" and weekday == 4:  # Friday
            start = datetime.combine(day, datetime.min.time(), ET).replace(hour=16)
            close = datetime.combine(day + timedelta(days=3), datetime.min.time(), ET).replace(hour=9, minute=30)
            bounds.append((start, close))
        elif kind == "overnight" and weekday in (0, 1, 2, 3):  # Mon-Thu evening
            start = datetime.combine(day, datetime.min.time(), ET).replace(hour=16)
            close = datetime.combine(day + timedelta(days=1), datetime.min.time(), ET).replace(hour=9, minute=30)
            bounds.append((start, close))
        elif kind == "monday_rth" and weekday == 0:  # Monday only
            start = datetime.combine(day, datetime.min.time(), ET).replace(hour=9, minute=30)
            close = datetime.combine(day, datetime.min.time(), ET).replace(hour=16)
            bounds.append((start, close))
        elif kind == "rth" and weekday <= 4:
            start = datetime.combine(day, datetime.min.time(), ET).replace(hour=9, minute=30)
            close = datetime.combine(day, datetime.min.time(), ET).replace(hour=16)
            bounds.append((start, close))
        day += timedelta(days=1)
    return bounds


def _price_at(index: dict[datetime, float], moment: datetime, tolerance_hours: int = 2) -> float | None:
    """Last known close at or before `moment`, within a tolerance.

    Hourly bars do not land on 09:30, so the open of a window is priced from
    the most recent bar at or before it — never after, which would be a peek.
    """
    probe = moment.replace(minute=0, second=0, microsecond=0)
    for _ in range(tolerance_hours + 1):
        if probe in index:
            return index[probe]
        probe -= timedelta(hours=1)
    return None


def build_rows(bars: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    index = {bar["ts"].astimezone(UTC).replace(minute=0, second=0, microsecond=0): bar["close"]
             for bar in bars}
    bounds = _window_bounds(bars, kind)
    raw: list[dict[str, Any]] = []
    for start, close in bounds:
        entry = _price_at(index, start.astimezone(UTC))
        exit_price = _price_at(index, close.astimezone(UTC))
        if entry is None or exit_price is None or entry <= 0:
            continue
        raw.append({"start": start, "close": close, "entry": entry,
                    "exit": exit_price, "ret": exit_price / entry - 1.0})

    rows: list[dict[str, Any]] = []
    for position in range(WARMUP_WINDOWS, len(raw)):
        current = raw[position]
        history = raw[position - WARMUP_WINDOWS:position]  # strictly prior windows
        prior_rets = [item["ret"] for item in history]
        # Trailing full-day context, also strictly before the decision instant.
        entry_utc = current["start"].astimezone(UTC)
        day_ago = _price_at(index, entry_utc - timedelta(hours=24))
        week_ago = _price_at(index, entry_utc - timedelta(hours=24 * 7))
        if day_ago is None or week_ago is None:
            continue
        extra: dict[str, float] = {}
        if kind == "monday_rth":
            # The weekend move is fully known at Monday 09:30 — it printed
            # while equities were shut. This is the whole question: does the
            # session that follows continue that move or fade it?
            friday_close = _price_at(index, entry_utc - timedelta(hours=65, minutes=30))
            if friday_close is None:
                continue
            weekend_ret = current["entry"] / friday_close - 1.0
            extra = {
                "weekend_ret": round(weekend_ret, 8),
                "weekend_abs": round(abs(weekend_ret), 8),
            }
        rows.append({
            "session": current["start"].date().isoformat(),
            "symbol": f"BTC-{kind.upper()}",
            "features": {
                **extra,
                "prior_window_ret": round(prior_rets[-1], 8),
                "prior_2window_ret": round(sum(prior_rets[-2:]), 8),
                "mean_prior_ret": round(statistics.mean(prior_rets), 8),
                "vol_prior": round(statistics.pstdev(prior_rets), 8),
                "ret_24h": round(current["entry"] / day_ago - 1.0, 8),
                "ret_7d": round(current["entry"] / week_ago - 1.0, 8),
                "streak_up": float(sum(1 for r in prior_rets[-3:] if r > 0)),
                "month": float(current["start"].month),
            },
            # The window's own return is the tradeable payoff. Both payoff keys
            # carry it so the frozen validator's modes both address this window.
            "next_open_to_close": round(current["ret"], 8),
            "next_close_to_close": round(current["ret"], 8),
        })
    return rows


def prepare(*, kind: str, start: str, end: str, out: Path, symbol: str = "BTC/USD") -> dict[str, Any]:
    if kind not in SESSION_KINDS:
        raise ValueError(f"kind must be one of {SESSION_KINDS}")
    try:
        from dotenv import load_dotenv

        load_dotenv(REPOSITORY_ROOT / ".env")
    except ImportError:
        pass
    from research.providers.alpaca import AlpacaHistoricalProvider

    provider = AlpacaHistoricalProvider.from_env()
    start_utc = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=UTC)
    end_utc = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=UTC) + timedelta(days=1)
    bars: list[dict[str, Any]] = []
    cursor = start_utc
    # Alpaca pages long crypto ranges; walk it in 90-day slices so a silent
    # truncation cannot masquerade as a shorter history.
    while cursor < end_utc:
        chunk_end = min(cursor + timedelta(days=90), end_utc)
        snapshot = provider.bars(asset_class="crypto", symbols=(symbol,),
                                 start_utc=cursor, end_utc=chunk_end, timeframe="1Hour")
        bars.extend({"ts": bar.timestamp_utc, "close": bar.close} for bar in snapshot.bars)
        cursor = chunk_end
    bars.sort(key=lambda bar: bar["ts"])
    deduped: list[dict[str, Any]] = []
    for bar in bars:
        if not deduped or bar["ts"] != deduped[-1]["ts"]:
            deduped.append(bar)
    if not deduped:
        raise ValueError(f"no {symbol} bars returned for {start}..{end}")

    rows = build_rows(deduped, kind)
    if len(rows) < 60:
        raise ValueError(f"only {len(rows)} decidable {kind} windows; widen the range")

    out.mkdir(parents=True, exist_ok=True)
    payload = ("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n").encode("utf-8")
    (out / "sessions.jsonl").write_bytes(payload)
    sessions = sorted({row["session"] for row in rows})
    manifest = {
        "provider": "alpaca", "asset_class": "crypto", "symbol": symbol,
        "session_kind": kind, "hourly_bars": len(deduped),
        "first_session": sessions[0], "last_session": sessions[-1],
        "session_count": len(sessions), "row_count": len(rows),
        "data_sha256": hashlib.sha256(payload).hexdigest(),
        "suggested_splits": _splits(sessions),
        "warmup_windows": WARMUP_WINDOWS,
        "prepared_at_utc": datetime.now(UTC).isoformat(),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                       encoding="utf-8")
    return manifest


def _splits(sessions: list[str]) -> dict[str, list[str]]:
    discovery_end = sessions[int(len(sessions) * 0.6) - 1]
    validation_end = sessions[int(len(sessions) * 0.8) - 1]
    later = [s for s in sessions if s > discovery_end]
    rest = [s for s in later if s > validation_end]
    return {"discovery": [sessions[0], discovery_end],
            "validation": [later[0], validation_end],
            "holdout": [rest[0], sessions[-1]]}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=SESSION_KINDS, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(prepare(kind=args.kind, start=args.start, end=args.end, out=args.out),
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
