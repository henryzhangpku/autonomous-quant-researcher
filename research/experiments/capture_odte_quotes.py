"""Capture executable SPY 0DTE put-credit economics at the campaign's entry time.

The spy-odte-credit promotion is conditional on one number nobody has
measured: the cost of crossing into the spread at 10:00 ET. The backtest says
the structure clears every gate at or below 0.5% of width and fails at 1.0%,
so this harness records where reality sits.

It writes one JSON line per run under .research/captures/spy-odte-quotes/,
append-only, so a week of runs answers the question with a distribution
rather than an anecdote. It never places an order and never reads an account.

Run at 10:00 ET on trading days. A single pre-market sample is not evidence:
quotes at the open are wider than quotes at 05:00, and the campaign trades
10:00.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPOSITORY_ROOT / ".research" / "captures" / "spy-odte-quotes"
ET = ZoneInfo("America/New_York")
# Geometries to record each run. o5 is the original campaign cell; o2 is the
# strongest point on the measured surface and therefore the one whose entry
# cost actually decides anything.
GEOMETRIES = ((5.0, 5.0), (5.0, 2.0))


def capture_one(stock, options, spot: float, now_et, width: float, offset: float) -> dict:
    from alpaca.data.requests import OptionLatestQuoteRequest

    short_strike = round(spot - offset)
    long_strike = short_strike - width

    def occ(strike: float) -> str:
        return f"SPY{now_et.date():%y%m%d}P{int(round(strike * 1000)):08d}"

    legs = [occ(short_strike), occ(long_strike)]
    quotes = options.get_option_latest_quote(OptionLatestQuoteRequest(symbol_or_symbols=legs))
    record: dict = {"width": width, "offset": offset,
                    "short_strike": short_strike, "long_strike": long_strike, "legs": legs}
    missing = [leg for leg in legs if leg not in quotes]
    if missing:
        record["error"] = f"no quote for {missing}"
        return record
    short_q, long_q = quotes[legs[0]], quotes[legs[1]]
    mid_credit = ((short_q.bid_price + short_q.ask_price) / 2.0
                  - (long_q.bid_price + long_q.ask_price) / 2.0)
    cross_credit = short_q.bid_price - long_q.ask_price
    record.update({
        "short": {"bid": short_q.bid_price, "ask": short_q.ask_price},
        "long": {"bid": long_q.bid_price, "ask": long_q.ask_price},
        "mid_credit": round(mid_credit, 4),
        "cross_credit": round(cross_credit, 4),
        "mid_credit_frac_width": round(mid_credit / width, 6),
        "entry_cost_frac_width": round((mid_credit - cross_credit) / width, 6),
        "clears_breakeven": bool((mid_credit - cross_credit) / width <= 0.005),
    })
    return record


def capture() -> dict:
    from dotenv import load_dotenv

    load_dotenv(REPOSITORY_ROOT / ".env")
    from alpaca.data.historical import OptionHistoricalDataClient, StockHistoricalDataClient
    from alpaca.data.requests import StockLatestQuoteRequest

    key, secret = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_API_SECRET")
    stock = StockHistoricalDataClient(key, secret)
    options = OptionHistoricalDataClient(key, secret)

    quote = stock.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols="SPY"))["SPY"]
    spot = (quote.bid_price + quote.ask_price) / 2.0
    now_et = datetime.now(ET)
    return {
        "captured_at_et": now_et.isoformat(),
        "spy_mid": round(spot, 4),
        "breakeven_frac_width": 0.005,
        "geometries": [capture_one(stock, options, spot, now_et, width, offset)
                       for width, offset in GEOMETRIES],
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    record = capture()
    with (OUT_DIR / "quotes.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
