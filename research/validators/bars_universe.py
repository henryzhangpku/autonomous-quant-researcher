"""Spec-driven portfolio evaluator for daily-bars universes.

One validator serves every compiled bars-family idea. The idea-specific
surface (universe, split dates, sample gates, costs, payoff mode) lives in a
frozen JSON spec produced by the mission compiler; this module never changes
per idea, so evaluator semantics stay fixed while missions vary.

Decision model, fixed for the family: the candidate observes features built
strictly from bars up to and including session T and returns a position for
session T+1. Payoff is the next session's open-to-close (default) or
close-to-close return, minus costs. Equal-weight daily portfolio, identical
gate framework to the CF universe family.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import inspect
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Sequence

from research.experiments.bars_feature_spec import FEATURE_FIXTURE

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REQUIRED_SPLITS = ("discovery", "validation", "holdout")
PAYOFF_MODES = (
    "next_open_to_close",
    "next_close_to_close",
    # Benchmark-relative: the same forward window, minus the benchmark's return
    # on that session. A rule must beat the market on the sessions it picks
    # instead of passing on drift, which is how bars campaign 1 produced two
    # opposite conditions that both "worked" long.
    "next_open_to_close_rel",
    "next_close_to_close_rel",
)


@dataclass(frozen=True)
class UniverseSpec:
    """Frozen per-mission evaluation surface. Loaded, hash-checked, never mutated."""

    name: str
    universe: tuple[str, ...]
    splits: dict[str, tuple[str, str]]
    min_trades: dict[str, int]
    min_sessions: dict[str, int]
    min_symbols: int
    max_concentration: float
    cost_bps: float
    target_sharpe: float
    payoff: str


@dataclass(frozen=True)
class CandidateContract:
    signal: Callable[[str, dict[str, float]], int]
    label: str


class FeatureView(dict):
    """Feature mapping that names what exists when a candidate invents a key.

    A proposer that reaches for `features['close']` otherwise fails with a
    bare KeyError whose message is just `'close'`, which tells the loop
    nothing and burns a trial. Saying what is available turns the mistake
    into feedback the next proposal can act on.
    """

    def __missing__(self, key: object) -> float:
        raise KeyError(
            f"unknown feature {key!r}; available features are: "
            + ", ".join(sorted(self))
        )


def load_spec(path: Path, expected_hash: str | None = None) -> UniverseSpec:
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if expected_hash and actual != expected_hash:
        raise ValueError(f"frozen spec hash mismatch: expected {expected_hash}, got {actual}")
    data = json.loads(raw)
    universe = tuple(str(symbol) for symbol in data["universe"])
    if not universe:
        raise ValueError("spec universe may not be empty")
    splits = {name: (str(window[0]), str(window[1])) for name, window in data["splits"].items()}
    if tuple(sorted(splits)) != tuple(sorted(REQUIRED_SPLITS)):
        raise ValueError(f"spec splits must be exactly {REQUIRED_SPLITS}")
    ordered = [splits[name] for name in REQUIRED_SPLITS]
    for earlier, later in zip(ordered, ordered[1:]):
        if earlier[1] >= later[0]:
            raise ValueError("spec splits must be chronological and non-overlapping")
    payoff = str(data.get("payoff", PAYOFF_MODES[0]))
    if payoff not in PAYOFF_MODES:
        raise ValueError(f"spec payoff must be one of {PAYOFF_MODES}")
    max_concentration = float(data["max_concentration"])
    if not 0.0 < max_concentration <= 1.0:
        raise ValueError("spec max_concentration must be in (0, 1]")
    spec = UniverseSpec(
        name=str(data["name"]),
        universe=universe,
        splits=splits,
        min_trades={name: int(data["min_trades"][name]) for name in REQUIRED_SPLITS},
        min_sessions={name: int(data["min_sessions"][name]) for name in REQUIRED_SPLITS},
        min_symbols=int(data["min_symbols"]),
        max_concentration=max_concentration,
        cost_bps=float(data["cost_bps"]),
        target_sharpe=float(data["target_sharpe"]),
        payoff=payoff,
    )
    if spec.min_symbols < 1 or spec.min_symbols > len(spec.universe):
        raise ValueError("spec min_symbols must be between 1 and the universe size")
    if spec.cost_bps < 0:
        raise ValueError("spec cost_bps may not be negative")
    return spec


def _load_contract(path: Path) -> CandidateContract:
    spec = importlib.util.spec_from_file_location("autonomous-quant-researcher_bars_universe_candidate", path.resolve())
    if spec is None or spec.loader is None:
        raise ValueError(f"could not load candidate: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return _contract_from_module(module)


def _contract_from_module(module: ModuleType) -> CandidateContract:
    signal = getattr(module, "signal", None)
    if not callable(signal):
        raise TypeError("candidate must define signal(symbol, features)")
    parameters = tuple(inspect.signature(signal).parameters.values())
    if tuple(parameter.name for parameter in parameters) != ("symbol", "features"):
        raise TypeError("candidate signal must have exact signature signal(symbol, features)")
    label = getattr(module, "LABEL", "bars universe candidate")
    if not isinstance(label, str) or not label.strip():
        raise TypeError("candidate LABEL must be a non-empty string")
    return CandidateContract(signal, label.strip())


# Derived, never re-typed: three files must agree on this set exactly, and a
# hand-kept copy is how a candidate ends up rejected for naming a feature the
# data actually carries.
PREFLIGHT_FIXTURE = dict(FEATURE_FIXTURE)


def _referenced_features(source: str) -> set[str]:
    """Every feature name the code mentions, on any execution path.

    Executing one fixture vector cannot prove feature names are valid: a
    short-circuited branch hides its lookups until real data takes it, and by
    then a scientific trial has been spent. Static extraction has no branches
    to hide behind.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "features"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            names.add(node.slice.value)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "features"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            names.add(node.args[0].value)
    return names


def preflight_candidate(
    path: Path, spec: UniverseSpec, rows: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    # The whitelist is the dataset's own feature keys when the frozen rows are
    # at hand: a windowed crypto session or a 0DTE spread row legitimately
    # carries features the daily-bars fixture never names, and rejecting those
    # names made every windowed mission unlaunchable (2026-08-16). Without
    # rows, fall back to the daily-bars fixture.
    if rows:
        whitelist: set[str] = set()
        for row in rows:
            whitelist |= set(row["features"])
        smoke = dict(rows[0]["features"])
    else:
        whitelist = set(PREFLIGHT_FIXTURE)
        smoke = PREFLIGHT_FIXTURE
    unknown = _referenced_features(path.read_text(encoding="utf-8")) - whitelist
    if unknown:
        raise TypeError(
            "candidate references unknown features "
            + ", ".join(sorted(repr(name) for name in unknown))
            + "; available features are: "
            + ", ".join(sorted(whitelist))
        )
    contract = _load_contract(path)
    for symbol in spec.universe[:3] or spec.universe:
        decision = contract.signal(symbol, FeatureView(smoke))
        if type(decision) is not int or decision not in {-1, 0, 1}:
            raise TypeError("candidate signal must return exactly -1, 0, or 1")
    # A rule that never fires on the discovery split can only ever score the
    # no-position penalty: nothing is learned about the market and the trial
    # is wasted. The proposer guesses thresholds without knowing the feature
    # distributions, so this failure mode repeats (2026-08-24: 20 of 24 IBIT
    # 0DTE candidates fired on zero sessions; the 20-day-high family died the
    # same way on AAPL/AVGO). Reject at preflight -- feedback, not budget --
    # and hand back the discovery-split feature ranges so the retry can aim.
    fires: int | None = None
    if rows:
        start, end = spec.splits["discovery"]
        universe = set(spec.universe)
        selected = [
            row for row in rows
            if start <= row["session"] <= end and row["symbol"] in universe
        ]
        if selected:
            fires = 0
            for row in selected:
                decision = contract.signal(row["symbol"], FeatureView(row["features"]))
                if type(decision) is not int or decision not in {-1, 0, 1}:
                    raise TypeError("candidate signal must return exactly -1, 0, or 1")
                if decision != 0:
                    fires += 1
            if fires == 0:
                raise TypeError(
                    "candidate never fires on the discovery split "
                    f"({len(selected)} eligible sessions); its condition lies "
                    "outside the data's actual feature ranges. Discovery "
                    "feature ranges: " + _feature_ranges_text(selected)
                )
    payload = {"status": "preflight_ok", "label": contract.label, "universe": list(spec.universe)}
    if fires is not None:
        payload["discovery_fires"] = fires
    return payload


def _feature_ranges_text(rows: list[dict[str, Any]]) -> str:
    """Per-feature min/p10/p50/p90/max over the given rows, for proposer feedback."""

    values: dict[str, list[float]] = {}
    for row in rows:
        for name, value in row["features"].items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.setdefault(name, []).append(float(value))
    parts: list[str] = []
    for name in sorted(values):
        series = sorted(values[name])
        def quantile(fraction: float) -> float:
            index = round(fraction * (len(series) - 1))
            return series[min(len(series) - 1, max(0, index))]
        parts.append(
            f"{name} min={series[0]:.4g} p10={quantile(0.1):.4g} "
            f"p50={quantile(0.5):.4g} p90={quantile(0.9):.4g} max={series[-1]:.4g}"
        )
    return "; ".join(parts)


def _load_rows(path: Path, expected_hash: str | None) -> list[dict[str, Any]]:
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if expected_hash and actual != expected_hash:
        raise ValueError(f"frozen bars snapshot hash mismatch: expected {expected_hash}, got {actual}")
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    if not rows:
        raise ValueError("frozen bars snapshot is empty")
    return rows


def _sharpe(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    deviation = statistics.stdev(values)
    return statistics.mean(values) / deviation * math.sqrt(252.0) if deviation else 0.0


def evaluate_candidate(
    path: Path,
    spec: UniverseSpec,
    rows: list[dict[str, Any]],
    split: str,
    emit_series: Path | None = None,
) -> dict[str, Any]:
    contract = _load_contract(path)
    start, end = spec.splits[split]
    universe = set(spec.universe)
    selected = [
        row for row in rows
        if start <= row["session"] <= end and row["symbol"] in universe
    ]

    trades: list[dict[str, Any]] = []
    cost = spec.cost_bps / 10_000.0
    for row in selected:
        decision = contract.signal(row["symbol"], FeatureView(row["features"]))
        if type(decision) is not int or decision not in {-1, 0, 1}:
            raise TypeError("candidate signal must return exactly -1, 0, or 1")
        if decision == 0:
            continue
        if spec.payoff not in row:
            # Relative payoffs are absent when the snapshot was built without a
            # benchmark, or on a session the benchmark did not trade. Skipping
            # silently would quietly shrink the sample, so say so.
            raise ValueError(
                f"snapshot row {row['session']}/{row['symbol']} has no {spec.payoff!r}; "
                "rebuild the snapshot with a benchmark to use relative payoffs"
            )
        gross = decision * float(row[spec.payoff])
        trades.append({
            "session": row["session"], "symbol": row["symbol"],
            "gross": gross, "net": gross - cost,
        })

    by_day: dict[str, list[float]] = defaultdict(list)
    by_day_high_cost: dict[str, list[float]] = defaultdict(list)
    by_symbol: dict[str, list[float]] = defaultdict(list)
    for trade in trades:
        by_day[trade["session"]].append(trade["net"])
        by_day_high_cost[trade["session"]].append(trade["net"] - cost)
        by_symbol[trade["symbol"]].append(trade["net"])
    daily = [statistics.mean(by_day[day]) for day in sorted(by_day)]
    if emit_series is not None:
        # Display plumbing for the web equity endpoint: the per-session net
        # portfolio series the evaluator already computes, with its dates.
        # Opt-in side channel only — the payload, gates, and score are
        # byte-identical whether or not this file is written.
        emit_series.parent.mkdir(parents=True, exist_ok=True)
        emit_series.write_text(
            json.dumps(
                [{"date": day, "net": statistics.mean(by_day[day])} for day in sorted(by_day)]
            )
            + "\n",
            encoding="utf-8",
        )
    high_cost_daily = [statistics.mean(by_day_high_cost[day]) for day in sorted(by_day_high_cost)]
    midpoint = max(1, len(daily) // 2)
    without_best = daily.copy()
    if without_best:
        without_best.remove(max(without_best))
    counts = Counter(trade["symbol"] for trade in trades)
    active_symbols = sum(count >= 5 for count in counts.values())
    max_concentration = max(counts.values(), default=0) / len(trades) if trades else 1.0
    positive_symbol_fraction = (
        sum(statistics.mean(values) > 0 for values in by_symbol.values()) / len(by_symbol)
        if by_symbol else 0.0
    )
    single_symbol = len(spec.universe) == 1
    gates = {
        "minimum_trades": len(trades) >= spec.min_trades[split],
        "minimum_sessions": len(daily) >= spec.min_sessions[split],
        "symbol_breadth": active_symbols >= spec.min_symbols,
        "symbol_concentration": single_symbol or max_concentration <= spec.max_concentration,
        "positive_net_mean": bool(daily) and statistics.mean(daily) > 0,
        "positive_both_halves": len(daily) >= 4 and statistics.mean(daily[:midpoint]) > 0 and statistics.mean(daily[midpoint:]) > 0,
        "survives_without_best_day": bool(without_best) and statistics.mean(without_best) > 0,
        "survives_double_cost": bool(high_cost_daily) and statistics.mean(high_cost_daily) > 0,
        "positive_symbol_fraction": positive_symbol_fraction >= (1.0 if single_symbol else 0.4),
    }
    raw_sharpe = _sharpe(daily)
    major = {"minimum_trades", "minimum_sessions", "symbol_breadth"}
    gate_penalty = sum(
        (10.0 if name in major else 1.0) for name, passed in gates.items() if not passed
    )
    gates_pass = all(gates.values())
    score = raw_sharpe if gates_pass else min(raw_sharpe, spec.target_sharpe - 0.000001) - gate_penalty
    return {
        "status": "passed" if gates_pass else "rejected", "split": split,
        "label": contract.label, "spec_name": spec.name, "payoff": spec.payoff,
        "eligible_symbol_sessions": len(selected), "trades": len(trades),
        "portfolio_sessions": len(daily), "active_symbols": active_symbols,
        "symbol_counts": dict(sorted(counts.items())),
        "max_symbol_concentration": round(max_concentration, 6),
        "positive_symbol_fraction": round(positive_symbol_fraction, 6),
        "gross_mean_bps": round(statistics.mean([trade["gross"] for trade in trades]) * 10_000, 6) if trades else 0.0,
        "net_trade_mean_bps": round(statistics.mean([trade["net"] for trade in trades]) * 10_000, 6) if trades else 0.0,
        "net_portfolio_mean_bps": round(statistics.mean(daily) * 10_000, 6) if daily else 0.0,
        # Correlated-firing diagnostics, surfaced at discovery rather than in a
        # post-mortem. A condition that fires universe-wide counts one market
        # event once per symbol in the trade mean while the portfolio mean
        # counts it once; a large gap is that artifact, not an edge.
        "trades_per_session": round(len(trades) / len(daily), 4) if daily else 0.0,
        "trade_minus_session_bps": round(
            (statistics.mean([trade["net"] for trade in trades]) - statistics.mean(daily)) * 10_000, 6
        ) if trades and daily else 0.0,
        "net_sharpe": round(raw_sharpe, 6), "cost_bps": spec.cost_bps,
        "gates": gates, "gate_penalty": gate_penalty, "score": round(score, 6),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate a generated bars-universe candidate.")
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--spec-hash")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--data-hash")
    parser.add_argument("--split", choices=REQUIRED_SPLITS, default="discovery")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument(
        "--emit-series",
        type=Path,
        help="write the per-session net portfolio series as JSON to this path",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        spec = load_spec(args.spec, args.spec_hash)
        rows = _load_rows(args.data, args.data_hash)
        payload = preflight_candidate(args.candidate, spec, rows) if args.preflight_only else evaluate_candidate(
            args.candidate, spec, rows, args.split, emit_series=args.emit_series
        )
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True))
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
