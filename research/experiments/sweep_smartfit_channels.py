"""Evaluate the five frozen claims in research/missions/smartfit-channels.

This is NOT a grid. ``sweep_feature_thresholds`` covers a quantile ladder over
every feature and answers "does any cut separate the next session?". This
script answers a different and narrower question, frozen in PREREGISTRATION.md
before the snapshot was staged: **does the SmartFit channel breakout hold at
the settings its own author ships?** |r| >= 0.50, >= 5 bars and z = 1.96 are
that script's input defaults, read from its published Pine source; the ADX
gate is off by default there, which is why turning it on is its own claim
rather than a tweak to another one.

Searching for better thresholds would answer a question nobody is acting on
and would reintroduce the multiple-comparisons problem that killed digests
001 and 002. So: one rule per claim, no alternatives, no second pass. Every
claim is recorded — fired, gate-blocked or failed — because a best-of-N that
hides its N is not evidence.

Three things the frozen validator does not compute, and this script does:

1. **The baseline**, as long every session in the split. The "same sessions"
   wording inherited from ta-premises/ta-indicators is degenerate for a
   long-only rule — the two series are identical by construction — and that
   defect is corrected here rather than copied forward.
2. **The decile profile** of chan_pearson and chan_z. A threshold is only
   interesting if THAT threshold is special; a smooth profile means the rule
   is riding a gradient and the number is decoration. This is the test that
   killed Fibonacci in ta-premises and Connors' 10 in ta-indicators.
3. **Whether the ADX filter earns its place**, by splitting each breakout
   claim's OWN sessions at ADX 20 rather than comparing two differently-sized
   samples. A filter that only removes trades has removed sample, not noise.

The holdout is never opened by this script. It has no code path that can.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

# Frozen 2026-08-27 in research/missions/smartfit-channels/PREREGISTRATION.md.
# The |r| >= 0.50 and >= 5-bar gates are already inside chan_breakout, exactly
# as they sit inside the source's isBullishBreak — they are not re-stated here.
# label, position, source expression, python predicate, prose
CLAIMS: tuple[tuple[str, int, str, Callable[[dict[str, float]], bool], str], ...] = (
    (
        "S1-breakout-long", 1,
        "features['chan_breakout'] > 0.5",
        lambda f: f["chan_breakout"] > 0.5,
        "an upside channel breakout predicts a higher next session (author's default long signal)",
    ),
    (
        "S2-breakout-short", -1,
        "features['chan_breakout'] < -0.5",
        lambda f: f["chan_breakout"] < -0.5,
        "a downside channel breakout predicts a lower next session (author's default short signal)",
    ),
    (
        "S3-breakout-long-adx", 1,
        "features['chan_breakout'] > 0.5 and features['adx_14'] >= 20.0",
        lambda f: f["chan_breakout"] > 0.5 and f["adx_14"] >= 20.0,
        "the ADX gate improves the upside breakout (author's stated purpose for the filter)",
    ),
    (
        "S4-breakout-short-adx", -1,
        "features['chan_breakout'] < -0.5 and features['adx_14'] >= 20.0",
        lambda f: f["chan_breakout"] < -0.5 and f["adx_14"] >= 20.0,
        "the ADX gate improves the downside breakout",
    ),
    (
        "S5-band-reversion", 1,
        "features['chan_pearson'] >= 0.5 and features['chan_z'] < -1.96",
        lambda f: f["chan_pearson"] >= 0.5 and f["chan_z"] < -1.96,
        "price at the lower band of a well-fitted rising channel reverts up "
        "(the ta-premises P2 premise, on the anchored channel)",
    ),
)

# The validator's contract is a POSITION (-1, 0, 1), never a boolean — an
# earlier sweep returned True/False from every cell and all 390 failed with
# "signal must return exactly -1, 0, or 1". Zero-of-N is an error, not a
# finding.
CANDIDATE_TEMPLATE = """\
LABEL = {label!r}


def signal(symbol, features):
    return {position!r} if {expression} else 0
"""


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


def arithmetic(rows: list[dict[str, Any]], claim, payoff: str,
               cost_bps: float) -> dict[str, Any]:
    """Firing stats and the frozen baseline, computed independently.

    ``baseline_full_split`` is being long EVERY session in the split — the
    comparison the preregistration freezes, and the one a reader cares about.
    ``baseline_same_sessions`` is retained only so the degenerate-by-
    construction equality stays visible for long-only claims rather than being
    quietly dropped.
    """
    _, position, _, predicate, _ = claim
    cost = cost_bps / 10_000.0

    fired = [r for r in rows if predicate(r["features"])]
    base_full = [r[payoff] - cost for r in rows]
    if not fired:
        return {
            "n": 0,
            "note": "rule never fires on this split",
            "baseline_full_split_bps": round(statistics.fmean(base_full) * 10_000, 3)
            if base_full else None,
        }

    rule = [position * r[payoff] - cost for r in fired]
    base_same = [r[payoff] - cost for r in fired]
    # A short claim is judged against the always-long baseline with its sign
    # reversed, per the preregistration.
    baseline_full = statistics.fmean(base_full) * position

    return {
        "n": len(fired),
        "n_symbols": len({r["symbol"] for r in fired}),
        "n_sessions": len({r["session"] for r in fired}),
        "fire_rate": round(len(fired) / len(rows), 5),
        "rule_net_mean_bps": round(statistics.fmean(rule) * 10_000, 3),
        "baseline_same_sessions_bps": round(statistics.fmean(base_same) * 10_000, 3),
        "baseline_full_split_bps": round(statistics.fmean(base_full) * 10_000, 3),
        "baseline_signed_for_claim_bps": round(baseline_full * 10_000, 3),
        "beats_baseline": statistics.fmean(rule) > baseline_full,
    }


def decile_profile(rows: list[dict[str, Any]], name: str, payoff: str) -> list[dict[str, Any]]:
    """Mean next-session return by decile of the feature's own distribution.

    If this is monotone or flat, the threshold is decoration: the rule is
    riding a smooth relationship and the author's number contributes nothing.
    """
    ordered = sorted(rows, key=lambda r: r["features"][name])
    size = len(ordered) // 10
    if size == 0:
        return []
    out = []
    for d in range(10):
        chunk = ordered[d * size : (d + 1) * size] if d < 9 else ordered[9 * size :]
        out.append({
            "decile": d + 1,
            "lo": round(chunk[0]["features"][name], 4),
            "hi": round(chunk[-1]["features"][name], 4),
            "n": len(chunk),
            "mean_bps": round(statistics.fmean(r[payoff] for r in chunk) * 10_000, 2),
        })
    return out


def adx_split(rows: list[dict[str, Any]], claim, payoff: str,
              cost_bps: float, threshold: float = 20.0) -> dict[str, Any]:
    """Does the ADX gate improve the breakout, on the breakout's OWN sessions?

    Comparing S3 against S1 directly compares two different-sized samples, so
    a difference could be sample size alone. Splitting S1's own firing
    sessions at ADX 20 asks the author's question properly: of the trades the
    unfiltered signal takes, do the high-ADX ones do better than the ones the
    filter would have removed? A filter that only shrinks the sample without
    lifting the kept half has removed evidence, not noise.
    """
    _, position, _, predicate, _ = claim
    cost = cost_bps / 10_000.0
    fired = [r for r in rows if predicate(r["features"])]
    kept = [r for r in fired if r["features"]["adx_14"] >= threshold]
    dropped = [r for r in fired if r["features"]["adx_14"] < threshold]
    if not kept or not dropped:
        return {"note": "one side of the ADX split is empty", "n_kept": len(kept),
                "n_dropped": len(dropped)}
    kept_net = statistics.fmean(position * r[payoff] - cost for r in kept)
    dropped_net = statistics.fmean(position * r[payoff] - cost for r in dropped)
    return {
        "n_kept": len(kept), "n_dropped": len(dropped),
        "kept_net_bps": round(kept_net * 10_000, 3),
        "dropped_net_bps": round(dropped_net * 10_000, 3),
        "filter_helps": kept_net > dropped_net,
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
        label, position, expression, _, prose = claim
        candidate = args.out / f"{label}.py"
        candidate.write_text(CANDIDATE_TEMPLATE.format(
            label=label, position=position, expression=expression), encoding="utf-8")

        verdict = run_validator(candidate, args.spec, args.data, args.split)
        stats = arithmetic(rows, claim, payoff, cost_bps)
        record = {
            "label": label, "claim": prose, "rule": expression, "position": position,
            "split": args.split,
            "validator_status": verdict.get("status", "?"),
            # Keep every scalar the evaluator emits. An earlier mission captured
            # a hand-picked subset and hid the field that decided it:
            # net_trade_mean_bps and net_portfolio_mean_bps disagreed by 44 bps,
            # and only the portfolio one is what a book actually earns.
            "validator": {k: v for k, v in verdict.items()
                          if not isinstance(v, (dict, list)) or k == "gates"},
            "arithmetic": stats,
        }
        if label in ("S1-breakout-long", "S2-breakout-short"):
            record["adx_gate_test"] = adx_split(rows, claim, payoff, cost_bps)
        records.append(record)
        n = stats.get("n", 0)
        net = stats.get("rule_net_mean_bps")
        base = stats.get("baseline_signed_for_claim_bps")
        print(f"  {label:24s} n={n:5d}  net={net!s:>10}bps  base={base!s:>9}bps  "
              f"validator={record['validator_status']}")

    summary = {
        "mission": "smartfit-channels",
        "split": args.split,
        "preregistration": "research/missions/smartfit-channels/PREREGISTRATION.md",
        "claims_evaluated": len(records),
        "rows": len(rows),
        "decile_profiles": {
            name: decile_profile(rows, name, payoff)
            for name in ("chan_pearson", "chan_z", "adx_14")
        },
        "records": records,
    }
    (args.out / f"{args.split}.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\nwrote {args.out / (args.split + '.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
