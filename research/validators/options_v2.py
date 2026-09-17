"""SPY RTH options validator v2: the founding engine wrapped in the gate set.

`research.validators.options` scores a single control-relative number, which
the spy-buy-the-dip postmortem (2026-08-13) showed can accept a signal wearing
an absolutely-losing trade: +7.3pp OVER control across 2016-2021 while losing
-21pp of width in every one of those years. v2 keeps the same candidate
interface and chronological engine but judges ABSOLUTE economics through the
same hard battery the overnight program uses: sample floors, positive net
mean, both halves, without-best-week, doubled cost, and a weekly-cluster
bootstrap lower bound. Control-relative edge is reported as diagnostics only.

Pricing and costs are the founding study's validated layer: Black-Scholes
with vrp 1.10 (checked against real SPY 0DTE quotes to ~2pp) and a declared
``--cost-frac`` of width per round trip (default the founding 0.03).
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

from research.backtest import BacktestEngine, CostModel, Session, SessionStore
from research.validators.options import SPLITS, _load_contract
from research.validators.options import preflight_candidate as _preflight
from research.validators.overnight_spx import (
    _week_key,
    _weekly_bootstrap_lower_bound,
)

EVALUATOR_VERSION = "spy-options-gated-v2"
MIN_TRADES = {"discovery": 100, "validation": 50, "holdout": 30}
MIN_WEEKS = {"discovery": 40, "validation": 20, "holdout": 12}
TARGET_SHARPE = 1.0


def evaluate_candidate_v2(
    candidate_path: Path,
    sessions: list[Session],
    *,
    split: str,
    cost_frac: float,
) -> dict[str, Any]:
    if split not in SPLITS:
        raise ValueError(f"unknown split: {split}")
    if not 0.0 < cost_frac < 0.5:
        raise ValueError("cost_frac must be a fraction of width in (0, 0.5)")
    contract = _load_contract(candidate_path)
    start, end = SPLITS[split]
    selected = [session for session in sessions if start <= session.day <= end]
    if not selected:
        raise ValueError(f"no sessions in fixed {split} split")

    result = BacktestEngine(costs=CostModel(cost_frac=cost_frac)).run(
        sessions=selected,
        entry_hm=contract.entry_hm,
        signal=contract.signal,  # type: ignore[arg-type]
        structure=contract.structure,  # type: ignore[arg-type]
        label=contract.label,
    )

    by_week: dict[str, list[float]] = defaultdict(list)
    by_week_double: dict[str, list[float]] = defaultdict(list)
    for row in result.rows:
        net = float(row["pnl_w"])
        by_week[_week_key(row["date"])].append(net)
        by_week_double[_week_key(row["date"])].append(net - cost_frac)
    weekly = [statistics.mean(by_week[week]) for week in sorted(by_week)]
    weekly_double = [statistics.mean(by_week_double[week]) for week in sorted(by_week_double)]
    midpoint = max(1, len(weekly) // 2)
    without_best = weekly.copy()
    if without_best:
        without_best.remove(max(without_best))
    bootstrap_lb = _weekly_bootstrap_lower_bound(weekly)

    gates = {
        "minimum_trades": len(result.rows) >= MIN_TRADES[split],
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
    major = {"minimum_trades", "minimum_weeks"}
    gate_penalty = sum(10.0 if name in major else 1.0 for name, passed in gates.items() if not passed)
    gates_pass = all(gates.values())
    score = sharpe if gates_pass else min(sharpe, TARGET_SHARPE - 0.000001) - gate_penalty

    control_net = [float(row["pnl_w"]) for row in result.control_rows]
    by_year: dict[str, list[float]] = defaultdict(list)
    for row in result.rows:
        by_year[row["date"][:4]].append(float(row["pnl_w"]))
    return {
        "status": "passed" if gates_pass else "rejected",
        "evaluator_version": EVALUATOR_VERSION,
        "split": split,
        "label": contract.label,
        "entry_hm": list(contract.entry_hm),
        "cost_frac": cost_frac,
        "signal_trades": len(result.rows),
        "signal_weeks": len(weekly),
        "control_trades": len(result.control_rows),
        "net_mean_w": round(statistics.mean([float(r["pnl_w"]) for r in result.rows]), 6) if result.rows else 0.0,
        "net_weekly_mean_w": round(statistics.mean(weekly), 6) if weekly else 0.0,
        "control_mean_w": round(statistics.mean(control_net), 6) if control_net else 0.0,
        "by_year_net_mean_w": {year: round(statistics.mean(values), 6) for year, values in sorted(by_year.items())},
        "weekly_bootstrap_lb_w": round(bootstrap_lb, 6) if weekly else None,
        "net_weekly_sharpe": round(sharpe, 6),
        "gates": gates,
        "gate_penalty": gate_penalty,
        "score": round(score, 6),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gate-battery validation of one SPY RTH candidate.")
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--split", choices=tuple(SPLITS), default="discovery")
    parser.add_argument("--cost-frac", type=float, default=0.03)
    parser.add_argument("--preflight-only", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.preflight_only:
            payload = _preflight(args.candidate)
        else:
            sessions = SessionStore.load(args.symbol).sessions()
            payload = evaluate_candidate_v2(
                args.candidate, sessions, split=args.split, cost_frac=args.cost_frac
            )
    except Exception as exc:  # noqa: BLE001 - candidate failures are validator evidence
        print(json.dumps({"error": str(exc), "status": "failed"}, sort_keys=True))
        return 2
    print(json.dumps(payload, sort_keys=True))
    if not args.preflight_only:
        print(f"score={payload['score']:.6f}")
        if payload["status"] != "passed":
            failed = ",".join(name for name, passed in payload["gates"].items() if not passed)
            print(f"robustness_gates_failed={failed}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
