"""Evaluate the eight frozen oscillator claims in research/missions/ta-indicators.

This is NOT a grid. ``sweep_feature_thresholds`` covers a quantile ladder over
every feature and answers "does any cut separate the next session?". This
script answers a different and narrower question, frozen in PREREGISTRATION.md
before any split was read: **do the indicators hold at the settings their own
literature states?** RSI-2 below 10 is Connors' number, RSI-14 below 30 is
Wilder's, %B below 0 is the lower band by construction. Searching for a better
threshold would answer a question nobody in retail is acting on, and would
reintroduce the multiple-comparisons problem that killed digests 001 and 002.

So: one rule per claim, conventional parameter, no alternatives, no second
pass. Every claim is recorded — fired, gate-blocked or failed — because a
best-of-N that hides its N is not evidence.

Two things the frozen validator does not compute, and this script does:

1. **The baseline.** An indicator that makes money but no more than holding
   the same names on the same days has demonstrated nothing. That comparison
   is the one retail evidence almost never reports, so it is computed here for
   every claim.
2. **The decile profile.** A threshold is only interesting if THAT threshold
   is special. If next-session return varies smoothly with the indicator, the
   rule is momentum or mean reversion wearing an oscillator's name. This is
   the test that killed the Fibonacci premise in ta-premises.

The holdout is never opened by this script. It has no code path that can.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

# Frozen 2026-08-24 in research/missions/ta-indicators/PREREGISTRATION.md.
# label, feature, operator, threshold, position, prose
CLAIMS: tuple[tuple[str, str, str, float, int, str], ...] = (
    ("I1-rsi2-oversold",    "rsi_2",     "<",  10.0,  1, "RSI-2 below 10 predicts a higher next session (Connors)"),
    ("I2-rsi2-overbought",  "rsi_2",     ">",  90.0, -1, "RSI-2 above 90 predicts a lower next session (Connors)"),
    ("I3-rsi14-oversold",   "rsi_14",    "<",  30.0,  1, "RSI-14 below 30 predicts a higher next session (Wilder)"),
    ("I4-rsi14-overbought", "rsi_14",    ">",  70.0, -1, "RSI-14 above 70 predicts a lower next session (Wilder)"),
    ("I5-macd-hist-pos",    "macd_hist", ">",   0.0,  1, "a positive MACD histogram predicts a higher next session"),
    ("I6-bbands-lower",     "bb_pctb",   "<",   0.0,  1, "price below the lower Bollinger band reverts up"),
    ("I7-streak-reversal",  "streak",    "<=", -3.0,  1, "three or more down closes predict a higher next session"),
    ("I8-streak-continue",  "streak",    ">=",  3.0,  1, "three or more up closes predict a higher next session"),
)

# The validator's contract is a POSITION (-1, 0, 1), never a boolean — an
# earlier sweep returned True/False from every cell and all 390 failed with
# "signal must return exactly -1, 0, or 1". Zero-of-N is an error, not a
# finding.
CANDIDATE_TEMPLATE = """\
LABEL = {label!r}


def signal(symbol, features):
    value = features[{name!r}]
    return {position!r} if value {operator} {threshold!r} else 0
"""

OPS = {
    "<": lambda v, t: v < t,
    ">": lambda v, t: v > t,
    "<=": lambda v, t: v <= t,
    ">=": lambda v, t: v >= t,
}


def load_rows(data: Path) -> list[dict[str, Any]]:
    rows = []
    with data.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def in_split(row: dict[str, Any], bounds: list[str]) -> bool:
    return bounds[0] <= row["session"] <= bounds[1]


def run_validator(candidate: Path, spec: Path, data: Path, split: str) -> dict[str, Any]:
    """One subprocess per claim per split, evaluator unmodified."""
    completed = subprocess.run(
        [
            sys.executable, "-m", "research.validators.bars_universe",
            "--candidate", str(candidate), "--spec", str(spec),
            "--data", str(data), "--split", split,
        ],
        capture_output=True, text=True, cwd=REPOSITORY_ROOT,
    )
    for line in completed.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {"status": "failed", "error": (completed.stderr or "no evaluator output")[:400]}


def arithmetic(rows: list[dict[str, Any]], claim, payoff: str, cost_bps: float) -> dict[str, Any]:
    """Baseline and firing stats, computed independently of the validator.

    ``rule_net`` is the position-signed payoff less cost on the sessions the
    rule fires. ``baseline_same_sessions`` is being LONG those same sessions —
    the frozen comparison. ``baseline_full_split`` is being long every session
    in the split, which is the comparison a reader actually cares about and is
    reported alongside because the frozen one is degenerate for short claims
    (a short rule's net is the sign-flipped long baseline by construction, so
    it can never differ from it). Recorded, not silently substituted.
    """
    _, name, operator, threshold, position, _ = claim
    cost = cost_bps / 10_000.0
    test = OPS[operator]

    fired = [r for r in rows if test(r["features"][name], threshold)]
    if not fired:
        return {"n": 0, "note": "rule never fires on this split"}

    rule = [position * r[payoff] - cost for r in fired]
    base_same = [r[payoff] - cost for r in fired]
    base_full = [r[payoff] - cost for r in rows]

    return {
        "n": len(fired),
        "n_symbols": len({r["symbol"] for r in fired}),
        "fire_rate": round(len(fired) / len(rows), 4),
        "rule_net_mean_bps": round(statistics.fmean(rule) * 10_000, 3),
        "baseline_same_sessions_bps": round(statistics.fmean(base_same) * 10_000, 3),
        "baseline_full_split_bps": round(statistics.fmean(base_full) * 10_000, 3),
        "beats_baseline_same": statistics.fmean(rule) > statistics.fmean(base_same),
        "beats_baseline_full": statistics.fmean(rule) > statistics.fmean(base_full),
    }


def decile_profile(rows: list[dict[str, Any]], name: str, payoff: str) -> list[dict[str, Any]]:
    """Mean next-session return by decile of the indicator's own distribution.

    If this is monotone or flat, the threshold is decoration: the rule is
    riding a smooth relationship and the famous number contributes nothing.
    """
    ordered = sorted(rows, key=lambda r: r["features"][name])
    size = len(ordered) // 10
    if size == 0:
        return []
    out = []
    for d in range(10):
        chunk = ordered[d * size: (d + 1) * size] if d < 9 else ordered[9 * size:]
        out.append({
            "decile": d + 1,
            "lo": round(chunk[0]["features"][name], 4),
            "hi": round(chunk[-1]["features"][name], 4),
            "n": len(chunk),
            "mean_bps": round(statistics.fmean(r[payoff] for r in chunk) * 10_000, 2),
        })
    return out


def bandwidth_magnitude(rows: list[dict[str, Any]], payoff: str) -> dict[str, Any]:
    """The squeeze premise is directionless, so it is tested on |return| only.

    Reporting it as a direction rule would invent content the premise does not
    claim. Supported here means "precedes larger moves", never "buy".
    """
    ordered = sorted(rows, key=lambda r: r["features"]["bb_bandwidth"])
    size = len(ordered) // 10
    if size == 0:
        return {}
    low, rest = ordered[:size], ordered[size:]
    return {
        "n_low_decile": len(low),
        "mean_abs_move_low_bps": round(statistics.fmean(abs(r[payoff]) for r in low) * 10_000, 2),
        "mean_abs_move_rest_bps": round(statistics.fmean(abs(r[payoff]) for r in rest) * 10_000, 2),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--split", choices=("discovery", "validation"), default="discovery",
        help="holdout is deliberately not offered; it stays sealed until "
             "discovery and validation are both complete and reviewed",
    )
    args = parser.parse_args(argv)

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    payoff, cost_bps = spec["payoff"], spec["cost_bps"]
    bounds = spec["splits"][args.split]
    universe = set(spec["universe"])

    every = load_rows(args.data)
    rows = [r for r in every if in_split(r, bounds) and r["symbol"] in universe]
    args.out.mkdir(parents=True, exist_ok=True)

    print(f"split={args.split} {bounds[0]}..{bounds[1]}  rows={len(rows)}  "
          f"symbols={len({r['symbol'] for r in rows})}  payoff={payoff}  cost={cost_bps}bps")

    records = []
    for claim in CLAIMS:
        label, name, operator, threshold, position, prose = claim
        candidate = args.out / f"{label}.py"
        candidate.write_text(CANDIDATE_TEMPLATE.format(
            label=label, name=name, operator=operator,
            threshold=threshold, position=position), encoding="utf-8")

        verdict = run_validator(candidate, args.spec, args.data, args.split)
        stats = arithmetic(rows, claim, payoff, cost_bps)
        record = {
            "label": label, "claim": prose,
            "rule": f"{name} {operator} {threshold}", "position": position,
            "split": args.split,
            "validator_status": verdict.get("status", "?"),
            # Keep every scalar the evaluator emits. The first pass captured a
            # hand-picked subset and hid the field that decides this mission:
            # net_trade_mean_bps and net_portfolio_mean_bps disagree by 44 bps
            # on I3, and only the portfolio one is what a book actually earns.
            "validator": {k: v for k, v in verdict.items()
                          if not isinstance(v, (dict, list)) or k == "gates"},
            "arithmetic": stats,
            "decile_profile": decile_profile(rows, name, payoff) if name != "streak" else [],
        }
        records.append(record)
        n = stats.get("n", 0)
        net = stats.get("rule_net_mean_bps")
        base = stats.get("baseline_same_sessions_bps")
        print(f"  {label:22s} n={n:5d}  net={net!s:>9}bps  base={base!s:>9}bps  "
              f"validator={record['validator_status']}")

    summary = {
        "mission": "ta-indicators",
        "split": args.split,
        "preregistration": "research/missions/ta-indicators/PREREGISTRATION.md",
        "claims_evaluated": len(records),
        "rows": len(rows),
        "bb_bandwidth_magnitude": bandwidth_magnitude(rows, payoff),
        "records": records,
    }
    (args.out / f"{args.split}.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\nwrote {args.out / (args.split + '.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
