"""Freeze the stitched ES overnight 5-minute snapshot for the overnight program.

Data acquisition happens here, outside candidate execution, per CLAUDE.md.
Polygon/Massive is the explicitly secondary futures source (Alpaca carries no
futures); its per-contract history begins around 2024-09, which bounds the
whole program and is recorded in the manifest rather than papered over.

Polygon publishes no back-adjusted continuous contract and its contract
directory answers only current listings, so the quarterly ES tickers are
derived here from the CME cycle (H/M/U/Z, third-Friday expiry) and stitched
deterministically: for every overnight session both the front and next
contract are eligible, and the one with the larger session volume wins. The
winner is recorded per session; ties and rolls are therefore reproducible
evidence, not judgment.

Output: research/data/es_overnight_5min.csv.gz (day, minute, o, h, l, c,
volume, contract) plus es_overnight_manifest.json (sha256, provenance,
per-session contract map, dropped-session reasons, suggested splits).
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from research.backtest.overnight import (
    SESSION_MINUTES,
    minute_index,
)
from research.providers.massive import MassiveHistoricalProvider

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ET = ZoneInfo("America/New_York")
_MONTH_CODES = {3: "H", 6: "M", 9: "U", 12: "Z"}
_SETTLEMENT_BAR = SESSION_MINUTES - 5
_LATEST_FIRST_BAR = 60


def _third_friday(year: int, month: int) -> date:
    day = date(year, month, 15)
    while day.weekday() != 4:
        day += timedelta(days=1)
    return day


def quarterly_contracts(start: date, end: date) -> list[tuple[str, date]]:
    """(ticker, expiry) for every ES quarterly whose life overlaps the range."""
    out: list[tuple[str, date]] = []
    for year in range(start.year, end.year + 2):
        for month, code in sorted(_MONTH_CODES.items()):
            expiry = _third_friday(year, month)
            if expiry < start:
                continue
            ticker = f"ES{code}{year % 10}"
            out.append((ticker, expiry))
            if expiry > end + timedelta(days=120):
                return out
    return out


def _session_day(ts_et: datetime) -> date | None:
    """Map a bar timestamp to its overnight session's settlement day."""
    hm = ts_et.hour * 60 + ts_et.minute
    if hm >= 18 * 60:
        day = ts_et.date() + timedelta(days=1)
    elif hm < 16 * 60:
        day = ts_et.date()
    else:
        return None                      # 16:00-18:00 ET: settlement-to-reopen gap
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _pull_contract(
    provider: MassiveHistoricalProvider, ticker: str, start: date, end: date
) -> list[tuple[datetime, float, float, float, float, float]]:
    snapshot = provider.futures_aggregate_bars(
        ticker=ticker,
        start_utc=datetime.combine(start, datetime.min.time(), tzinfo=UTC),
        end_utc=datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=UTC),
        multiplier=5,
        timespan="minute",
    )
    return [
        (bar.timestamp_utc, bar.open, bar.high, bar.low, bar.close, bar.volume or 0.0)
        for bar in snapshot.bars
    ]


def prepare(*, start: str, end: str, out_dir: Path) -> dict[str, Any]:
    start_day = date.fromisoformat(start)
    end_day = date.fromisoformat(end)
    provider = MassiveHistoricalProvider.from_env()
    contracts = quarterly_contracts(start_day, end_day)

    # sessions[day][ticker] -> list of (minute, o, h, l, c, v)
    sessions: dict[date, dict[str, list[tuple[int, float, float, float, float, float]]]] = (
        defaultdict(lambda: defaultdict(list))
    )
    pulled: dict[str, int] = {}
    prior_expiry = start_day - timedelta(days=14)
    for ticker, expiry in contracts:
        window_start = max(start_day, prior_expiry - timedelta(days=14))
        window_end = min(end_day, expiry)
        prior_expiry = expiry
        if window_start > window_end:
            continue
        try:
            bars = _pull_contract(provider, ticker, window_start, window_end)
        except Exception as exc:  # noqa: BLE001 - absence of one contract is evidence
            pulled[ticker] = 0
            print(f"note: {ticker} returned no bars ({exc})")
            continue
        pulled[ticker] = len(bars)
        for ts_utc, open_, high, low, close, volume in bars:
            ts_et = ts_utc.astimezone(ET)
            day = _session_day(ts_et)
            if day is None:
                continue
            minute = minute_index(ts_et.hour * 60 + ts_et.minute)
            if not 0 <= minute <= _SETTLEMENT_BAR:
                continue
            sessions[day][ticker].append((minute, open_, high, low, close, volume))

    kept: list[tuple[str, str, list[tuple[int, float, float, float, float, float]]]] = []
    dropped: dict[str, str] = {}
    contract_map: dict[str, str] = {}
    for day in sorted(sessions):
        by_ticker = sessions[day]
        winner = max(by_ticker, key=lambda ticker: sum(bar[5] for bar in by_ticker[ticker]))
        bars = sorted(by_ticker[winner])
        first_minute = bars[0][0]
        has_settlement = any(bar[0] == _SETTLEMENT_BAR for bar in bars)
        iso = day.isoformat()
        if first_minute > _LATEST_FIRST_BAR:
            dropped[iso] = f"first bar at session minute {first_minute}; evening missing"
            continue
        if not has_settlement:
            dropped[iso] = "no 15:55 ET settlement bar (holiday or early close)"
            continue
        deduped: list[tuple[int, float, float, float, float, float]] = []
        seen: dict[int, tuple[int, float, float, float, float, float]] = {}
        for bar in bars:
            existing = seen.get(bar[0])
            if existing is None:
                seen[bar[0]] = bar
                deduped.append(bar)
            elif existing != bar:
                # Identical repeats are a pagination artifact; conflicting
                # values for one minute would silently corrupt the session.
                raise ValueError(f"{iso} {winner}: conflicting bars at minute {bar[0]}")
        contract_map[iso] = winner
        kept.append((iso, winner, deduped))

    if len(kept) < 60:
        raise ValueError(f"only {len(kept)} complete overnight sessions; refusing a thin snapshot")

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["day", "minute", "open", "high", "low", "close", "volume", "contract"])
    for iso, ticker, bars in kept:
        for minute, open_, high, low, close, volume in bars:
            writer.writerow([iso, minute, open_, high, low, close, volume, ticker])
    payload = buffer.getvalue().encode("utf-8")

    out_dir.mkdir(parents=True, exist_ok=True)
    data_path = out_dir / "es_overnight_5min.csv.gz"
    # Fixed mtime so the gzip container is deterministic and the hash is stable.
    with open(data_path, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
            zipped.write(payload)
    data_hash = hashlib.sha256(data_path.read_bytes()).hexdigest()

    days = [iso for iso, _ticker, _bars in kept]
    discovery_end = days[int(len(days) * 0.6) - 1]
    validation_end = days[int(len(days) * 0.85) - 1]
    manifest = {
        "provider": "massive",
        "asset_class": "future",
        "root": "ES",
        "requested_start": start,
        "requested_end": end,
        "first_session": days[0],
        "last_session": days[-1],
        "session_count": len(days),
        "dropped_sessions": dropped,
        "bars_per_contract": pulled,
        "contract_by_session": contract_map,
        "data_sha256": data_hash,
        "suggested_splits": {
            "discovery": [days[0], discovery_end],
            "validation": [
                next(day for day in days if day > discovery_end),
                validation_end,
            ],
            "holdout": [next(day for day in days if day > validation_end), days[-1]],
        },
        "prepared_at_utc": datetime.now(UTC).isoformat(),
        "limitations": [
            "ES front-month proxies SPX; the ES-SPX basis is not modeled",
            "modeled Black-Scholes entry marks; no overnight OPRA tape exists",
            "provider futures history begins ~2024-09; earlier regimes untested",
        ],
    }
    (out_dir / "es_overnight_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2024-09-01")
    parser.add_argument("--end", required=True)
    parser.add_argument("--out", type=Path, default=REPOSITORY_ROOT / "research" / "data")
    args = parser.parse_args(argv)
    manifest = prepare(start=args.start, end=args.end, out_dir=args.out)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
