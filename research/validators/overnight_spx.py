"""Fixed overnight SPX-complex vertical-spread validator for generated candidates.

The mission asks one question: for SPXW 0DTE/1DTE debit/credit verticals
entered during the overnight session, what entry -- timing, condition,
direction, structure -- survives costs and robustness gates? The candidate
declares an entry minute, an entry condition, and a defined-risk vertical;
this module owns chronology, causal views, modeled pricing, costs, splits,
gates, and the score. Candidates cannot touch any of that.

Pricing is the founding minimal Black-Scholes layer with a causal horizon:
sigma_t at the entry minute is the trailing-20-session realized remaining
variance to settlement, times the house VRP multiplier. Costs are charged as
a fraction of width per round trip at a level chosen for the WIDE overnight
SPX curb markets, with a doubled-cost survival gate on top.
"""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Sequence

from research.backtest.overnight import (
    ENTRY_MIN_CEIL,
    ENTRY_MIN_FLOOR,
    CandidateOvernightView,
    OvernightSession,
    OvernightStore,
    trailing_remaining_vol,
)
from research.backtest.structures import Structure

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA = REPOSITORY_ROOT / "research" / "data" / "es_overnight_5min.csv.gz"

SPLITS = {
    "discovery": ("2024-09-01", "2025-11-30"),
    "validation": ("2025-12-01", "2026-06-30"),
    "holdout": ("2026-07-01", "9999-12-31"),
}
MIN_TRADES = {"discovery": 60, "validation": 40, "holdout": 25}
MIN_WEEKS = {"discovery": 24, "validation": 16, "holdout": 8}

#: Round-trip cost as a fraction of width. Overnight SPX curb quotes are wide;
#: 5% of width base with a 10% survival gate is deliberately conservative next
#: to the founding study's 3% for liquid regular-hours SPY.
COST_FRAC = 0.05
VRP_MULT = 1.10
TARGET_SHARPE = 1.0
MIN_WIDTH = 5.0
MAX_WIDTH = 200.0
_BOOTSTRAP_RESAMPLES = 1000
_BOOTSTRAP_SEED = 20260813


@dataclass(frozen=True)
class CandidateContract:
    entry_minute: int
    signal: Callable[[CandidateOvernightView, int], bool]
    structure: Callable[[CandidateOvernightView, float], Structure]
    label: str


def _load_contract(path: Path) -> CandidateContract:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    spec = importlib.util.spec_from_file_location("autonomous-quant-researcher_overnight_candidate", resolved)
    if spec is None or spec.loader is None:
        raise ValueError(f"could not load candidate: {resolved}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return _contract_from_module(module)


def _contract_from_module(module: ModuleType) -> CandidateContract:
    entry = getattr(module, "ENTRY_MIN", None)
    if type(entry) is not int:
        raise TypeError("candidate ENTRY_MIN must be an int (minutes since 18:00 ET)")
    if not ENTRY_MIN_FLOOR <= entry <= ENTRY_MIN_CEIL or entry % 5:
        raise TypeError(
            "candidate ENTRY_MIN must be a five-minute slot between "
            f"{ENTRY_MIN_FLOOR} (20:15 ET) and {ENTRY_MIN_CEIL} (09:15 ET)"
        )
    signal = getattr(module, "signal", None)
    structure = getattr(module, "structure", None)
    for function, name, expected in (
        (signal, "signal", ("session", "entry_minute")),
        (structure, "structure", ("session", "entry_price")),
    ):
        if not callable(function):
            raise TypeError(f"candidate must define callable {name}{expected}")
        parameters = tuple(inspect.signature(function).parameters.values())
        if tuple(parameter.name for parameter in parameters) != expected or any(
            parameter.kind
            not in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
            for parameter in parameters
        ):
            rendered = ", ".join(expected)
            raise TypeError(f"candidate {name} must have exact signature {name}({rendered})")
    label = getattr(module, "LABEL", None)
    if not isinstance(label, str) or not label.strip():
        raise TypeError("candidate LABEL must be a non-empty string")
    return CandidateContract(entry, signal, structure, label.strip())


def _validate_structure(value: object) -> Structure:
    if not isinstance(value, Structure):
        raise TypeError(
            f"candidate structure must return one research.backtest Structure, not {type(value).__name__}"
        )
    if len(value.legs) != 2:
        raise TypeError("candidate structure must be a two-leg vertical")
    cps = {leg.cp for leg in value.legs}
    qtys = sorted(leg.qty for leg in value.legs)
    if len(cps) != 1 or cps - {"C", "P"} or qtys != [-1, 1]:
        raise TypeError("candidate structure must pair one long and one short leg of one type")
    width = value.width
    if not math.isfinite(width) or not MIN_WIDTH <= width <= MAX_WIDTH:
        raise TypeError(f"candidate structure width must be between {MIN_WIDTH:g} and {MAX_WIDTH:g}")
    for leg in value.legs:
        if not math.isfinite(leg.strike) or leg.strike <= 0 or leg.strike % 5:
            raise TypeError("candidate strikes must sit on the 5-point SPX grid")
    return value


def _preflight_sessions() -> tuple[tuple[str, OvernightSession], ...]:
    minutes = tuple(range(0, 1321, 5))

    def make(day: str, slope: float, high_offset: float) -> OvernightSession:
        bars = []
        for index, minute in enumerate(minutes):
            price = 6000.0 + slope * index
            bars.append((minute, price, price + high_offset, price - 1.0, price))
        return OvernightSession(
            day=day, bars=tuple(bars), contract="ESZ9",
            prior_close=6000.0, prior_rth_high=6030.0, prior_rth_low=5970.0,
        )

    sparse_minutes = {0, 135, 555, 915, 1315}
    sparse = OvernightSession(
        day="2025-01-08",
        bars=tuple(
            (minute, 6000.0, 6001.0, 5999.0, 6000.0)
            for minute in sorted(sparse_minutes)
        ),
        contract="ESZ9",
        prior_close=None, prior_rth_high=None, prior_rth_low=None,
    )
    return (
        ("rising", make("2025-01-06", 0.5, 5.0)),
        ("falling", make("2025-01-07", -0.5, 0.5)),
        ("sparse", sparse),
    )


def preflight_candidate(candidate_path: Path) -> dict[str, Any]:
    """Exercise the generated interface on deterministic, data-free sessions."""
    contract = _load_contract(candidate_path)
    for fixture_name, session in _preflight_sessions():
        view = CandidateOvernightView.at(session, contract.entry_minute)
        entry_price = view.price_at(contract.entry_minute) or session.evening_open
        try:
            structure = contract.structure(view, entry_price)
        except Exception as exc:  # noqa: BLE001 - generated-code errors are evidence
            raise TypeError(f"candidate structure failed preflight on {fixture_name}: {exc}") from exc
        _validate_structure(structure)
        try:
            decision = contract.signal(view, contract.entry_minute)
        except Exception as exc:  # noqa: BLE001
            raise TypeError(f"candidate signal failed preflight on {fixture_name}: {exc}") from exc
        if type(decision) is not bool:
            raise TypeError(
                f"candidate signal must return bool on every session, not {type(decision).__name__}"
            )
    return {"status": "preflight_ok", "label": contract.label, "entry_minute": contract.entry_minute}


def _week_key(day: str) -> str:
    iso = datetime.strptime(day, "%Y-%m-%d").isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _weekly_bootstrap_lower_bound(weekly_means: list[float]) -> float:
    """5th percentile of the resampled mean of weekly mean net P&L."""
    if len(weekly_means) < 2:
        return float("-inf")
    rng = random.Random(_BOOTSTRAP_SEED)
    count = len(weekly_means)
    means = sorted(
        statistics.mean(rng.choices(weekly_means, k=count))
        for _ in range(_BOOTSTRAP_RESAMPLES)
    )
    return means[int(0.05 * _BOOTSTRAP_RESAMPLES)]


def evaluate_candidate(
    candidate_path: Path,
    sessions: list[OvernightSession],
    *,
    split: str,
) -> dict[str, Any]:
    if split not in SPLITS:
        raise ValueError(f"unknown split: {split}")
    contract = _load_contract(candidate_path)
    vol_by_day = trailing_remaining_vol(sessions)
    start, end = SPLITS[split]

    rows: list[dict[str, Any]] = []
    control_rows: list[dict[str, Any]] = []
    skipped: dict[str, int] = defaultdict(int)
    for session in sessions:
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
        if entry_price is None or settlement is None:
            skipped["missing_entry_bar"] += 1
            continue
        view = CandidateOvernightView.at(session, contract.entry_minute)
        structure = _validate_structure(contract.structure(view, entry_price))
        # The trailing estimate is already total LOG vol over the remaining
        # horizon (accumulated from squared 5-minute log returns), which is
        # exactly the sigma_t form bs_price expects.
        debit = structure.model_entry_cost(entry_price, sigma_by_minute[contract.entry_minute] * VRP_MULT)
        width = structure.width
        if not -width < debit < width:
            skipped["degenerate_entry"] += 1
            continue
        net = (structure.settle(settlement) - debit - COST_FRAC * width) / width
        row = {
            "day": session.day,
            "week": _week_key(session.day),
            "net_w": net,
            "net_w_double_cost": net - COST_FRAC,
            "debit_w": debit / width,
            "label": structure.label,
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
    if weekly and len(weekly) >= 2:
        deviation = statistics.stdev(weekly)
        sharpe = statistics.mean(weekly) / deviation * math.sqrt(52.0) if deviation else 0.0
    else:
        sharpe = 0.0
    major = {"minimum_trades", "minimum_weeks"}
    gate_penalty = sum(10.0 if name in major else 1.0 for name, passed in gates.items() if not passed)
    gates_pass = all(gates.values())
    score = sharpe if gates_pass else min(sharpe, TARGET_SHARPE - 0.000001) - gate_penalty

    control_mean = (
        statistics.mean([row["net_w"] for row in control_rows]) if control_rows else 0.0
    )
    return {
        "status": "passed" if gates_pass else "rejected",
        "split": split,
        "label": contract.label,
        "entry_minute": contract.entry_minute,
        "entry_clock_et": f"{(contract.entry_minute + 1080) % 1440 // 60:02d}:{contract.entry_minute % 60:02d}",
        "eligible_sessions": len(control_rows),
        "signal_trades": len(rows),
        "signal_weeks": len(weekly),
        "net_mean_w": round(statistics.mean([row["net_w"] for row in rows]), 6) if rows else 0.0,
        "net_weekly_mean_w": round(statistics.mean(weekly), 6) if weekly else 0.0,
        "control_mean_w": round(control_mean, 6),
        "weekly_bootstrap_lb_w": round(bootstrap_lb, 6) if weekly else None,
        "mean_debit_w": round(statistics.mean([row["debit_w"] for row in rows]), 6) if rows else 0.0,
        "net_weekly_sharpe": round(sharpe, 6),
        "cost_frac": COST_FRAC,
        "vrp_mult": VRP_MULT,
        "skipped": dict(sorted(skipped.items())),
        "gates": gates,
        "gate_penalty": gate_penalty,
        "score": round(score, 6),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate one generated overnight candidate.")
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--data-hash")
    parser.add_argument("--split", choices=tuple(SPLITS), default="discovery")
    parser.add_argument("--preflight-only", action="store_true")
    return parser


def _load_sessions(path: Path, expected_hash: str | None) -> list[OvernightSession]:
    if expected_hash:
        import hashlib

        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected_hash:
            raise ValueError(f"frozen snapshot hash mismatch: expected {expected_hash}, got {actual}")
    return OvernightStore.load(path).sessions()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.preflight_only:
            payload = preflight_candidate(args.candidate)
        else:
            sessions = _load_sessions(args.data, args.data_hash)
            payload = evaluate_candidate(args.candidate, sessions, split=args.split)
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
