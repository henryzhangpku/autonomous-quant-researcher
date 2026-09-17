"""Download one symbol's 5-minute RTH bars from Alpaca into research/data/.

Runs OUTSIDE the autoresearch loop, exactly like capture_implied_move.py: the
loop strips credentials, so data acquisition is a separate, explicit step and
the experiment itself reads only a local file. Re-running extends the file
forward; history already on disk is never rewritten.

Usage::

    uv run python research/experiments/download_spy_intraday.py \\
        --start 2016-01-01

Output: research/data/{symbol}_5min.csv.gz with columns
``timestamp_utc,open,high,low,close,volume`` filtered to regular trading hours
(09:30-16:00 ET). Adjustment=ALL so multi-year return comparisons are not
polluted by dividends; same-day open-to-close ratios are unaffected either way.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
ET = ZoneInfo("America/New_York")


def output_path(symbol: str) -> Path:
    clean = symbol.strip().lower()
    if not clean or not clean.replace("-", "").isalnum():
        raise ValueError("symbol must contain only letters, numbers, or hyphens")
    return REPO_ROOT / "research" / "data" / f"{clean}_5min.csv.gz"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default=None, help="default: today")
    args = parser.parse_args(argv)
    out = output_path(args.symbol)

    key = os.getenv("ALPACA_API_KEY", "")
    secret = os.getenv("ALPACA_API_SECRET", "") or os.getenv("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise SystemExit(
            "ALPACA_API_KEY / ALPACA_API_SECRET not set. This script runs outside "
            "the research loop precisely because the loop strips credentials."
        )

    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else datetime.now(timezone.utc).date()

    # Resume: if the file exists, restart from the day after the last stored bar.
    last_seen: str | None = None
    if out.exists():
        with gzip.open(out, "rt", encoding="utf-8") as fh:
            for line in fh:
                pass
            if line and not line.startswith("timestamp"):
                last_seen = line.split(",", 1)[0]
    if last_seen:
        resume = datetime.fromisoformat(last_seen).date() + timedelta(days=1)
        start = max(start, resume)
        print(f"resuming from {start} (file ends at {last_seen})")
    if start > end:
        print("nothing to fetch; file is current")
        return 0

    client = StockHistoricalDataClient(key, secret)
    request = StockBarsRequest(
        symbol_or_symbols=args.symbol,
        timeframe=TimeFrame(5, TimeFrameUnit.Minute),
        start=datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc),
        end=datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc),
        adjustment=Adjustment.ALL,
        feed=DataFeed.SIP,
    )
    print(f"fetching {args.symbol} 5-min bars {start} .. {end} (SIP, adjusted) ...")
    bars = client.get_stock_bars(request).data.get(args.symbol, [])
    print(f"received {len(bars)} raw bars")

    kept = 0
    write_header = not out.exists()
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "at", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        if write_header:
            writer.writerow(["timestamp_utc", "open", "high", "low", "close", "volume"])
        for bar in bars:
            ts = bar.timestamp.astimezone(ET)
            hm = ts.hour * 60 + ts.minute
            # RTH only: bar timestamps are bar-open, so 09:30 .. 15:55 inclusive.
            if hm < 9 * 60 + 30 or hm > 15 * 60 + 55 or ts.weekday() >= 5:
                continue
            writer.writerow([
                bar.timestamp.astimezone(timezone.utc).isoformat(),
                f"{bar.open:.4f}", f"{bar.high:.4f}", f"{bar.low:.4f}",
                f"{bar.close:.4f}", int(bar.volume),
            ])
            kept += 1
    print(f"appended {kept} RTH bars -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
