"""Collect SPY same-day option prices for the quote-priced program (spy-quote-v1).

Runs OUTSIDE the research loop (it needs Alpaca credentials; the loop strips
them). Two steps:

1. ``fetch``: for every session of the raw tape (download_spy_intraday.py
   --adjustment raw) from 2024-02-01, pull 1-minute trade bars of every
   whole-dollar same-day contract a hypothesis structure could use (calls from
   20 below to 40 above the day's entry-price range, puts from 40 below to 20
   above: otm_offset is within +-20 and width within 20), reduce them to the
   5-minute decision grid (research/v3/quote_store.grid_prices) and cache one
   JSON file per day under --work. Re-running fetches only missing days.
2. ``materialize``: write the immutable per-stage quote stores under --root.

Usage::

    python research/experiments/collect_spy_0dte_quotes.py fetch --work research/data/spy_0dte
    python research/experiments/collect_spy_0dte_quotes.py materialize \\
        --work research/data/spy_0dte --root <campaign dir>/quotes
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time as clock
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from research.backtest.data import SessionStore, occ_symbol  # noqa: E402
from research.v3.program import SPY_QUOTE_V1  # noqa: E402
from research.v3.quote_store import GRID, grid_prices, materialize_quote_stores  # noqa: E402

ET = ZoneInfo("America/New_York")
FIRST_DAY = "2024-02-01"
SYMBOLS_PER_REQUEST = 100


def strikes_for(session) -> dict[str, list[int]]:
    entries = [session.price_at(m) for m in GRID]
    entries = [p for p in entries if p is not None]
    lo, hi = round(min(entries)), round(max(entries))
    return {"C": list(range(lo - 20, hi + 41)), "P": list(range(lo - 40, hi + 21))}


def fetch_day(client, session) -> dict:
    from alpaca.data.requests import OptionBarsRequest
    from alpaca.data.timeframe import TimeFrame

    day = date.fromisoformat(session.day)
    wanted = {occ_symbol("SPY", day, cp, k): (cp, k)
              for cp, ks in strikes_for(session).items() for k in ks}
    bars: dict[str, list[tuple[int, float]]] = {}
    symbols = sorted(wanted)
    for i in range(0, len(symbols), SYMBOLS_PER_REQUEST):
        request = OptionBarsRequest(
            symbol_or_symbols=symbols[i:i + SYMBOLS_PER_REQUEST], timeframe=TimeFrame.Minute,
            start=datetime.combine(day, time(13, 0), tzinfo=timezone.utc),
            end=datetime.combine(day, time(21, 30), tzinfo=timezone.utc),
        )
        for attempt in range(5):
            try:
                data = client.get_option_bars(request).data
                break
            except Exception:  # noqa: BLE001 - rate limits and transient errors retry
                if attempt == 4:
                    raise
                clock.sleep(2 ** attempt)
        for sym, rows in data.items():
            for bar in rows:
                ts = bar.timestamp.astimezone(ET)
                bars.setdefault(sym, []).append((ts.hour * 60 + ts.minute, float(bar.close)))
    record = {"day": session.day, "C": {}, "P": {}}
    for sym, (cp, k) in wanted.items():
        prices = grid_prices(bars.get(sym, []))
        if any(p is not None for p in prices):
            record[cp][str(k)] = prices
    return record


def cmd_fetch(args) -> int:
    key = os.getenv("ALPACA_API_KEY", "")
    secret = os.getenv("ALPACA_API_SECRET", "") or os.getenv("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise SystemExit("ALPACA_API_KEY / ALPACA_API_SECRET not set")
    from alpaca.data.historical import OptionHistoricalDataClient

    client = OptionHistoricalDataClient(key, secret)
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    sessions = [s for s in SessionStore.load("SPY-RAW").sessions()
                if s.day >= FIRST_DAY and s.is_full_day()]
    todo = [s for s in sessions if not (work / f"{s.day}.json").exists()]
    print(f"{len(sessions)} sessions, {len(todo)} to fetch", flush=True)
    for n, session in enumerate(todo, 1):
        record = fetch_day(client, session)
        tmp = work / f"{session.day}.json.tmp"
        tmp.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")))
        tmp.rename(work / f"{session.day}.json")
        if n % 20 == 0 or n == len(todo):
            print(f"{n}/{len(todo)} {session.day}: {len(record['C'])} calls, "
                  f"{len(record['P'])} puts", flush=True)
    return 0


def cmd_materialize(args) -> int:
    work = Path(args.work)
    records = [json.loads(p.read_text()) for p in sorted(work.glob("*.json"))]
    source = hashlib.sha256(b"".join(
        json.dumps(r, sort_keys=True, separators=(",", ":")).encode() + b"\n" for r in records
    )).hexdigest()
    manifests = materialize_quote_stores(Path(args.root), records, source_hash=source,
                                         program=SPY_QUOTE_V1)
    for stage, m in manifests.items():
        print(f"{stage}: {m.sessions} sessions, {m.contracts} contract-days")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--work", required=True)
    m = sub.add_parser("materialize")
    m.add_argument("--work", required=True)
    m.add_argument("--root", required=True)
    args = p.parse_args()
    return cmd_fetch(args) if args.cmd == "fetch" else cmd_materialize(args)


if __name__ == "__main__":
    raise SystemExit(main())
