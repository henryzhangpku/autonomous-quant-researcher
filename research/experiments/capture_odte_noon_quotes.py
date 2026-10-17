"""Capture executable 0DTE credit-spread economics at 12:00 ET for the noon M0 cells.

odte-intervention (research/missions/odte-intervention/RESULTS.md) found the
unmanaged near-money spread entered at 12:00 ET clears the full bar on SPY put,
QQQ put and QQQ call, and named precondition (1) before any holdout is spent:
entry-cost capture at 12:00 ET. capture_odte_quotes.py measures 10:00 ET with
dollar geometry on SPY puts only, so it cannot answer this.

Geometry is the cell's, not a new one: short strike offset = max($1,
round(spot * 0.26%)), width = max($2, round(spot * 0.64%)), $1 strike grid
(prepare_odte_path.py, index-ETF path). Entry cost = (mid credit - cross
credit) / width, the 10:00 harness's definition, judged against the two cost
levels the mission evaluated: 0.5% and 1.0% of width.

Historical NBBO is not available to us (Polygon plan returns 403 on
/v3/quotes; Alpaca serves only the latest option quote), so this is a forward
capture. One JSON line per run, append-only, under
.research/captures/odte-noon-quotes/. It never places an order and never
reads an account. Run at 12:00 ET on trading days; a run outside 11:55-12:05
ET is recorded with in_window=false so it cannot be mistaken for evidence.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPOSITORY_ROOT / ".research" / "captures" / "odte-noon-quotes"
ET = ZoneInfo("America/New_York")
OFFSET_PCT, WIDTH_PCT = 0.0026, 0.0064  # qqq-nearmoney / friday-names convention
COST_LEVELS = (0.005, 0.010)
# The three cells that cleared the bar at 12:00 ET.
CELLS = (("SPY", "put"), ("QQQ", "put"), ("QQQ", "call"))


def geometry(spot: float, side: str) -> tuple[float, float, float, float]:
    """(short strike, long strike, width, offset), as prepare_odte_path places them."""
    offset = max(1.0, round(spot * OFFSET_PCT))
    width = max(2.0, round(spot * WIDTH_PCT))
    sign = -1.0 if side == "put" else 1.0
    short = float(round(spot + sign * offset))
    long = float(round(spot + sign * (offset + width)))
    return short, long, abs(short - long), offset


def occ(underlying: str, day, side: str, strike: float) -> str:
    return f"{underlying}{day:%y%m%d}{'P' if side == 'put' else 'C'}{int(round(strike * 1000)):08d}"


def capture_cell(options, underlying: str, side: str, spot: float, now_et: datetime) -> dict:
    from alpaca.data.requests import OptionLatestQuoteRequest

    short, long, width, offset = geometry(spot, side)
    legs = [occ(underlying, now_et.date(), side, short), occ(underlying, now_et.date(), side, long)]
    record: dict = {"underlying": underlying, "side": side, "spot_mid": round(spot, 4),
                    "short_strike": short, "long_strike": long, "width": width, "offset": offset,
                    "legs": legs}
    quotes = options.get_option_latest_quote(OptionLatestQuoteRequest(symbol_or_symbols=legs))
    missing = [leg for leg in legs if leg not in quotes]
    if missing:
        record["error"] = f"no quote for {missing}"
        return record
    sq, lq = quotes[legs[0]], quotes[legs[1]]
    mid = (sq.bid_price + sq.ask_price) / 2.0 - (lq.bid_price + lq.ask_price) / 2.0
    cross = sq.bid_price - lq.ask_price
    cost = (mid - cross) / width
    record.update({
        "short": {"bid": sq.bid_price, "ask": sq.ask_price, "ts": sq.timestamp.isoformat()},
        "long": {"bid": lq.bid_price, "ask": lq.ask_price, "ts": lq.timestamp.isoformat()},
        "mid_credit": round(mid, 4),
        "cross_credit": round(cross, 4),
        "mid_credit_frac_width": round(mid / width, 6),
        "entry_cost_frac_width": round(cost, 6),
        "within_cost": {f"{c:.3f}": bool(cost <= c) for c in COST_LEVELS},
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
    names = sorted({u for u, _ in CELLS})
    quotes = stock.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=names))
    spots = {u: (quotes[u].bid_price + quotes[u].ask_price) / 2.0 for u in names}
    now_et = datetime.now(ET)
    cells = []
    for underlying, side in CELLS:
        try:
            cells.append(capture_cell(options, underlying, side, spots[underlying], now_et))
        except Exception as exc:  # one failed cell must not lose the others' quotes
            cells.append({"underlying": underlying, "side": side, "error": repr(exc)})
    return {
        "captured_at_et": now_et.isoformat(),
        "in_window": time(11, 55) <= now_et.time() <= time(12, 5) and now_et.weekday() < 5,
        "geometry": {"offset_pct": OFFSET_PCT, "width_pct": WIDTH_PCT, "strike_step": 1.0},
        "cells": cells,
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
