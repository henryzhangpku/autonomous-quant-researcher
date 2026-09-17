"""Overnight single-leg long options: the lottery-ticket side, measured.

Completes the structure space: campaign 2 answered credit verticals
(promoted), the debit grid answers verticals bought; this answers the bare
long call / long put held from an overnight entry to the 16:00 ET cash
settlement. The risk unit for a single leg is the PREMIUM PAID, so returns
here are on-premium, not on-width, and sizing comparisons across the three
studies must respect that.

Declared BEFORE any result is read: entries 135..915 step 90 (plus 915),
sides {call, put}, strike offsets {0, 10, 20, 40} points OTM on the 5-grid.
Pricing: the same causal EWMA remaining-variance Black-Scholes as evaluator
v2, vrp 1.0. Cost: 6% of premium per round trip declared (single-leg curb
books quote wider in premium terms than verticals do in width terms), with
the doubled-cost gate at 12%. Same weekly-cluster gate battery, discovery
split only unless something passes (then the standard top-3 validation
rule applies before anyone gets excited).
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

from research.backtest.overnight import (
    OvernightStore,
    trailing_remaining_vol,
)
from research.backtest.structures import bs_price
from research.validators.overnight_spx import (
    MIN_TRADES,
    MIN_WEEKS,
    SPLITS,
    _week_key,
    _weekly_bootstrap_lower_bound,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPOSITORY_ROOT / ".research" / "sweeps" / "overnight-singles-v1"

ENTRY_MINUTES = tuple(range(135, 901, 90)) + (915,)
SIDES = ("C", "P")
OFFSETS = (0.0, 10.0, 20.0, 40.0)
COST_FRAC_PREMIUM = 0.06
EWMA_DECAY = 0.85
VRP = 1.0


def evaluate_cell(sessions, vol_by_day, split, entry_minute, cp, offset):
    start, end = SPLITS[split]
    rows = []
    for session in sessions:
        if not start <= session.day <= end or not session.is_complete():
            continue
        sigma_by_minute = vol_by_day.get(session.day)
        if sigma_by_minute is None:
            continue
        entry_price = session.price_at(entry_minute)
        settlement = session.settlement
        if entry_price is None or settlement is None:
            continue
        raw_strike = entry_price + offset if cp == "C" else entry_price - offset
        strike = round(raw_strike / 5.0) * 5.0
        premium = bs_price(cp, entry_price, strike, sigma_by_minute[entry_minute] * VRP)
        if premium < 0.25:  # sub-quarter-point premium is not a tradable book
            continue
        intrinsic = max(settlement - strike, 0.0) if cp == "C" else max(strike - settlement, 0.0)
        net = (intrinsic - premium - COST_FRAC_PREMIUM * premium) / premium
        rows.append({"day": session.day, "week": _week_key(session.day), "net": net})

    by_week = defaultdict(list)
    for row in rows:
        by_week[row["week"]].append(row["net"])
    weekly = [statistics.mean(by_week[week]) for week in sorted(by_week)]
    weekly_double = [value - COST_FRAC_PREMIUM for value in weekly]
    midpoint = max(1, len(weekly) // 2)
    without_best = weekly.copy()
    if without_best:
        without_best.remove(max(without_best))
    bootstrap_lb = _weekly_bootstrap_lower_bound(weekly)
    gates = {
        "minimum_trades": len(rows) >= MIN_TRADES[split],
        "minimum_weeks": len(weekly) >= MIN_WEEKS[split],
        "positive_net_mean": bool(weekly) and statistics.mean(weekly) > 0,
        "positive_both_halves": len(weekly) >= 4
        and statistics.mean(weekly[:midpoint]) > 0
        and statistics.mean(weekly[midpoint:]) > 0,
        "survives_without_best_week": bool(without_best) and statistics.mean(without_best) > 0,
        "survives_double_cost": bool(weekly_double) and statistics.mean(weekly_double) > 0,
        "weekly_bootstrap_positive": bootstrap_lb > 0,
    }
    if len(weekly) >= 2:
        deviation = statistics.stdev(weekly)
        sharpe = statistics.mean(weekly) / deviation * math.sqrt(52.0) if deviation else 0.0
    else:
        sharpe = 0.0
    return {
        "entry_minute": entry_minute, "side": "call" if cp == "C" else "put",
        "offset": offset, "trades": len(rows), "weeks": len(weekly),
        "net_mean_on_premium": round(statistics.mean([r["net"] for r in rows]), 6) if rows else None,
        "weekly_bootstrap_lb": round(bootstrap_lb, 6) if weekly else None,
        "weekly_sharpe": round(sharpe, 6),
        "gates": gates,
        "status": "passed" if all(gates.values()) else "rejected",
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sessions = OvernightStore.load().sessions()
    vol_by_day = trailing_remaining_vol(sessions, decay=EWMA_DECAY)
    results = []
    for entry_minute in ENTRY_MINUTES:
        for cp in SIDES:
            for offset in OFFSETS:
                cell = evaluate_cell(sessions, vol_by_day, "discovery", entry_minute, cp, offset)
                results.append(cell)
                print(f"{entry_minute:>3} {cell['side']:<4} o{offset:g}: "
                      f"net={cell['net_mean_on_premium']} status={cell['status']}")
    passers = [cell for cell in results if cell["status"] == "passed"]
    summary = {
        "question": "does any bare long call/put overnight beat its premium after costs?",
        "grid_cells": len(results),
        "discovery_gate_passers": len(passers),
        "promoted": [],
        "validation": [],
        "shortlist": sorted(passers, key=lambda c: c["weekly_sharpe"], reverse=True)[:3],
        "rows": results,
        "economics": {"cost_frac_premium": COST_FRAC_PREMIUM, "vrp": VRP, "unit": "return on premium"},
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\n{len(passers)} of {len(results)} cells passed; results: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
