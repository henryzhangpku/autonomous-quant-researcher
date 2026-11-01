"""Overnight SPX validator v2: measured costs, conservative VRP, EWMA vol.

Campaign 1 (evaluator v1) established that the modeled 5%-of-width cost was
the binding constraint and that its equal-weight trailing vol underpriced
options after volatility shocks. v2 fixes both and freezes its economics in
the COMMAND LINE, so a policy file pins them exactly like the data hash:

- ``--cost-frac`` is declared per campaign (first live curb measurement:
  0.006-0.010 of width; the v2 base is deliberately above it). The
  doubled-cost survival gate stresses 2x the declared value.
- ``--vrp`` defaults to 1.0: no assumed volatility risk premium, which is
  the CONSERVATIVE side for credit structures (smaller modeled credit).
- Vol is an EWMA (decay 0.85) over trailing remaining-variance profiles.
- ``--exit morning`` model-values the structure at 09:30 ET with the causal
  remaining vol instead of holding to cash settlement.

Candidate interface, splits, gates, and score shape are identical to v1.
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

from research.backtest.overnight import (
    CandidateOvernightView,
    OvernightSession,
    trailing_remaining_vol,
)
from research.validators.overnight_spx import (
    DEFAULT_DATA,
    MIN_TRADES,
    MIN_WEEKS,
    SPLITS,
    TARGET_SHARPE,
    _load_contract,
    _load_sessions,
    _validate_structure,
    _week_key,
    _weekly_bootstrap_lower_bound,
    preflight_candidate,
)

EVALUATOR_VERSION = "overnight-spx-causal-v2"
EWMA_DECAY = 0.85
MORNING_EXIT_MINUTE = 930  # 09:30 ET


def evaluate_candidate_v2(
    candidate_path: Path,
    sessions: list[OvernightSession],
    *,
    split: str,
    cost_frac: float,
    vrp: float,
    exit_mode: str,
    horizon: str = "entry_session",
) -> dict[str, Any]:
    if split not in SPLITS:
        raise ValueError(f"unknown split: {split}")
    if not 0.0 < cost_frac < 0.5:
        raise ValueError("cost_frac must be a fraction of width in (0, 0.5)")
    if exit_mode not in {"settlement", "morning"}:
        raise ValueError("exit must be settlement or morning")
    if horizon not in {"entry_session", "next_session"}:
        raise ValueError("horizon must be entry_session or next_session")
    if horizon == "next_session" and exit_mode != "settlement":
        raise ValueError("the next_session horizon settles; it has no morning exit")
    contract = _load_contract(candidate_path)
    vol_by_day = trailing_remaining_vol(sessions, decay=EWMA_DECAY)
    start, end = SPLITS[split]

    rows: list[dict[str, Any]] = []
    control_rows: list[dict[str, Any]] = []
    skipped: dict[str, int] = defaultdict(int)
    for index, session in enumerate(sessions):
        if not start <= session.day <= end:
            continue
        if not session.is_complete():
            skipped["incomplete_session"] += 1
            continue
        sigma_by_minute = vol_by_day.get(session.day)
        if sigma_by_minute is None:
            skipped["vol_warmup"] += 1
            continue
        entry_price = session.price_at(contract.entry_minute)
        settlement = session.settlement
        if horizon == "next_session":
            next_session = sessions[index + 1] if index + 1 < len(sessions) else None
            if next_session is None or not next_session.is_complete():
                skipped["no_next_session"] += 1
                continue
            settlement = next_session.settlement
        if entry_price is None or settlement is None:
            skipped["missing_entry_bar"] += 1
            continue
        view = CandidateOvernightView.at(session, contract.entry_minute)
        structure = _validate_structure(contract.structure(view, entry_price))
        # sigma is trailing total LOG vol to the entry session's settlement;
        # the next_session horizon adds a further full session of variance,
        # approximated causally by the trailing 20:15->settle estimate.
        sigma_entry = sigma_by_minute[contract.entry_minute]
        if horizon == "next_session":
            sigma_entry = math.sqrt(sigma_entry**2 + sigma_by_minute[135] ** 2)
        debit = structure.model_entry_cost(entry_price, sigma_entry * vrp)
        width = structure.width
        if not -width < debit < width:
            skipped["degenerate_entry"] += 1
            continue

        if exit_mode == "settlement":
            exit_value = structure.settle(settlement)
        else:
            morning_price = session.price_at(MORNING_EXIT_MINUTE)
            if morning_price is None:
                skipped["missing_morning_bar"] += 1
                continue
            exit_value = structure.model_entry_cost(
                morning_price, sigma_by_minute[MORNING_EXIT_MINUTE] * vrp
            )

        net = (exit_value - debit - cost_frac * width) / width
        row = {
            "day": session.day,
            "week": _week_key(session.day),
            "net_w": net,
            "net_w_double_cost": net - cost_frac,
            "debit_w": debit / width,
        }
        control_rows.append(row)
        decision = contract.signal(view, contract.entry_minute)
        if type(decision) is not bool:
            raise TypeError(f"candidate signal must return bool, not {type(decision).__name__}")
        if decision:
            rows.append(row)

    by_week: dict[str, list[float]] = defaultdict(list)
    by_week_double: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_week[row["week"]].append(row["net_w"])
        by_week_double[row["week"]].append(row["net_w_double_cost"])
    weekly = [statistics.mean(by_week[week]) for week in sorted(by_week)]
    weekly_double = [statistics.mean(by_week_double[week]) for week in sorted(by_week_double)]
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
    major = {"minimum_trades", "minimum_weeks"}
    gate_penalty = sum(10.0 if name in major else 1.0 for name, passed in gates.items() if not passed)
    gates_pass = all(gates.values())
    score = sharpe if gates_pass else min(sharpe, TARGET_SHARPE - 0.000001) - gate_penalty

    return {
        "status": "passed" if gates_pass else "rejected",
        "evaluator_version": EVALUATOR_VERSION,
        "split": split,
        "label": contract.label,
        "entry_minute": contract.entry_minute,
        "exit_mode": exit_mode,
        "horizon": horizon,
        "cost_frac": cost_frac,
        "vrp": vrp,
        "ewma_decay": EWMA_DECAY,
        "eligible_sessions": len(control_rows),
        "signal_trades": len(rows),
        "signal_weeks": len(weekly),
        "net_mean_w": round(statistics.mean([row["net_w"] for row in rows]), 6) if rows else 0.0,
        "net_weekly_mean_w": round(statistics.mean(weekly), 6) if weekly else 0.0,
        "control_mean_w": round(statistics.mean([row["net_w"] for row in control_rows]), 6) if control_rows else 0.0,
        "weekly_bootstrap_lb_w": round(bootstrap_lb, 6) if weekly else None,
        "mean_debit_w": round(statistics.mean([row["debit_w"] for row in rows]), 6) if rows else 0.0,
        "net_weekly_sharpe": round(sharpe, 6),
        "skipped": dict(sorted(skipped.items())),
        "gates": gates,
        "gate_penalty": gate_penalty,
        "score": round(score, 6),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate one overnight candidate (evaluator v2).")
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--data-hash")
    parser.add_argument("--split", choices=tuple(SPLITS), default="discovery")
    parser.add_argument("--cost-frac", type=float, required=True)
    parser.add_argument("--vrp", type=float, default=1.0)
    parser.add_argument("--exit", choices=("settlement", "morning"), default="settlement")
    parser.add_argument(
        "--horizon", choices=("entry_session", "next_session"), default="entry_session",
        help="next_session holds through TOMORROW's 16:00 ET settlement (true 1DTE hold).",
    )
    parser.add_argument("--preflight-only", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.preflight_only:
            payload = preflight_candidate(args.candidate)
        else:
            sessions = _load_sessions(args.data, args.data_hash)
            payload = evaluate_candidate_v2(
                args.candidate, sessions, split=args.split,
                cost_frac=args.cost_frac, vrp=args.vrp, exit_mode=args.exit,
                horizon=args.horizon,
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
