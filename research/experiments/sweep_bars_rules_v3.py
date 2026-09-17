"""Bars campaign 3: the same grid, on a universe wide enough to measure breadth.

Campaign 2 produced one near-miss — `ret_5d < -0.05` short, market-relative —
which improved out of sample (Sharpe 1.12 -> 2.06, +9.7 -> +15.7 bps/session)
and failed a single gate: `positive_symbol_fraction` at 0.375 against 0.40.
With eight symbols that gate moves in 12.5-point steps, so it could not
measure breadth finely enough to pass or fail the hypothesis on its merits.
The constraint was the universe, not the rule.

PRIOR HYPOTHESIS UNDER TEST, named before this campaign runs:

    ret_5d < -0.05  ->  short, next_close_to_close_rel

Declaring it here is what makes its result evidence rather than a second
fishing trip: it is a specific prediction, made in advance, on a universe
and a data window it has never seen. It either survives on 30 names or it
does not, and either way the answer means something. The full grid still runs
so the campaign cannot become a single-cell confirmation exercise.

Declared BEFORE any result is read, in full:

- Universe: 30 liquid US equity ETFs — the eleven SPDR sectors, five broad
  index funds, and fourteen industry/thematic funds with continuous history
  over the window. Chosen for liquidity and sector span, fixed before the
  first cell runs. SPY is the benchmark and is included.
- Grid: unchanged from campaigns 1 and 2 — same features, same thresholds,
  same comparisons, same directions, both market-relative payoff modes.
- Economics: 5 bps per trade, doubled-cost stress at 10 bps. Unchanged.
- Gates: `positive_symbol_fraction` stays at 0.40, deliberately, so this
  campaign is directly comparable to campaign 2 on the measure that decided
  it. Two gates are TIGHTENED because 30 names make the old settings
  meaningless, and tightening before seeing results is conservative:
    * `max_concentration` 0.35 -> 0.20. At 30 symbols, 35% would let a
      single-name finding pass while wearing a diversified costume. 0.20 is
      still six times equal weight.
    * `min_symbols` 6 -> 10, and `min_trades` 120/50 -> 200/80, since both
      scale with universe size.
- Splits: the snapshot's own chronological 60/20/20, frozen at prepare time.
- Promotion rule: top THREE discovery cells by score among full gate-passers,
  no more and no substitutions, each evaluated once on validation at
  identical economics. The holdout is never touched by this script.

Output: <out>/discovery.jsonl, <out>/validation.jsonl, <out>/summary.json.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPOSITORY_ROOT / ".research" / "sweeps" / "bars-rules-v3"
SNAPSHOT_DIR = REPOSITORY_ROOT / ".research" / "data" / "bars" / "bars-campaign-3"

UNIVERSE = (
    # broad index
    "SPY", "QQQ", "IWM", "DIA", "MDY",
    # SPDR sectors (11)
    "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
    # industry / thematic
    "SMH", "XBI", "XOP", "XRT", "XHB", "ITB", "KRE", "IYT", "XME", "GDX",
    "IBB", "IGV", "JETS", "TAN",
)
BENCHMARK = "SPY"
START, END = "2021-08-01", "2026-08-11"
SHORTLIST = 3
PRIOR_HYPOTHESIS = {
    "feature": "ret_5d", "op": "lt", "threshold": -0.05,
    "direction": -1, "payoff": "next_close_to_close_rel",
}

GRID: tuple[tuple[str, tuple[float, ...]], ...] = (
    ("ret_1d", (-0.02, -0.01, -0.005, 0.005, 0.01, 0.02)),
    ("ret_5d", (-0.05, -0.02, 0.02, 0.05)),
    ("ret_20d", (-0.05, 0.05)),
    ("gap_open", (-0.01, -0.005, 0.005, 0.01)),
    ("range_pos", (0.2, 0.35, 0.65, 0.8)),
    ("vol_ratio_5_20", (0.8, 1.2, 1.5)),
    ("rv_5d", (0.01, 0.02)),
    ("dist_high_20", (-0.05, -0.02)),
    ("dist_low_20", (0.02, 0.05)),
)
COMPARISONS = ("lt", "gt")
DIRECTIONS = (1, -1)
PAYOFFS = ("next_open_to_close_rel", "next_close_to_close_rel")

CANDIDATE = """
LABEL = "{feature} {op} {threshold:g} -> {direction:+d}"

def signal(symbol, features):
    if features["{feature}"] {op_py} {threshold}:
        return {direction}
    return 0
"""


def build_snapshot() -> dict:
    from research.experiments.prepare_bars_universe import prepare

    manifest_path = SNAPSHOT_DIR / "manifest.json"
    if manifest_path.is_file():
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    return prepare(symbols=UNIVERSE, start=START, end=END, out=SNAPSHOT_DIR,
                   benchmark=BENCHMARK)


def write_specs(manifest: dict) -> dict[str, tuple[Path, str]]:
    base = {
        "name": "bars-rules-v3",
        "universe": list(UNIVERSE),
        "splits": manifest["suggested_splits"],
        "min_trades": {"discovery": 200, "validation": 80, "holdout": 80},
        "min_sessions": {"discovery": 60, "validation": 25, "holdout": 25},
        "min_symbols": 10,
        "max_concentration": 0.20,
        "cost_bps": 5.0,
        "target_sharpe": 0.75,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written: dict[str, tuple[Path, str]] = {}
    for payoff in PAYOFFS:
        path = OUT_DIR / f"spec-{payoff}.json"
        raw = (json.dumps(dict(base, payoff=payoff), indent=2, sort_keys=True) + "\n").encode("utf-8")
        path.write_bytes(raw)
        written[payoff] = (path, hashlib.sha256(raw).hexdigest())
    return written


def run_cell(candidate: Path, spec: tuple[Path, str], data_hash: str, split: str) -> dict:
    spec_path, spec_hash = spec
    completed = subprocess.run(
        [sys.executable, "-m", "research.validators.bars_universe",
         "--candidate", str(candidate), "--spec", str(spec_path), "--spec-hash", spec_hash,
         "--data", str(SNAPSHOT_DIR / "sessions.jsonl"), "--data-hash", data_hash,
         "--split", split],
        cwd=REPOSITORY_ROOT, text=True, capture_output=True, timeout=600,
    )
    line = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else "{}"
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {"status": "unparseable", "raw": line[:300]}


def _candidate_path(feature: str, op: str, threshold: float, direction: int) -> Path:
    path = OUT_DIR / f"c_{feature}_{op}_{threshold:g}_{direction:+d}.py"
    op_py = "<" if op == "lt" else ">"
    path.write_text(textwrap.dedent(CANDIDATE.format(
        feature=feature, op=op, op_py=op_py,
        threshold=threshold, direction=direction)).lstrip(), encoding="utf-8")
    return path


def main() -> int:
    manifest = build_snapshot()
    data_hash = manifest["data_sha256"]
    specs = write_specs(manifest)
    print(f"snapshot: {manifest['session_count']} sessions, {manifest['row_count']} rows, "
          f"{len(UNIVERSE)} symbols, benchmark {manifest.get('benchmark')}, "
          f"provider {manifest['provider']}")
    print(f"splits: {json.dumps(manifest['suggested_splits'])}\n")

    discovery_rows = []
    with (OUT_DIR / "discovery.jsonl").open("w", encoding="utf-8") as handle:
        for feature, thresholds in GRID:
            for threshold in thresholds:
                for op in COMPARISONS:
                    for direction in DIRECTIONS:
                        candidate = _candidate_path(feature, op, threshold, direction)
                        for payoff in PAYOFFS:
                            payload = run_cell(candidate, specs[payoff], data_hash, "discovery")
                            row = {"feature": feature, "op": op, "threshold": threshold,
                                   "direction": direction, "payoff": payoff, **payload}
                            handle.write(json.dumps(row, sort_keys=True) + "\n")
                            handle.flush()
                            discovery_rows.append(row)
                            flag = "PASS" if payload.get("status") == "passed" else "    "
                            print(f"{flag} {feature:>14} {op} {threshold:>7g} {direction:+d} "
                                  f"{payoff[5:24]:<22} sess_bps={payload.get('net_portfolio_mean_bps')} "
                                  f"sharpe={payload.get('net_sharpe')}")

    passers = [r for r in discovery_rows if r.get("status") == "passed"]
    passers.sort(key=lambda r: r.get("score", float("-inf")), reverse=True)
    shortlist = passers[:SHORTLIST]
    print(f"\n{len(discovery_rows)} cells, {len(passers)} discovery gate-passers; "
          f"shortlist of {len(shortlist)} goes to validation")

    # The prior hypothesis is validated whether or not it reaches the shortlist:
    # a named prediction must be answered, not quietly dropped for ranking below
    # two other cells.
    def matches_prior(row: dict) -> bool:
        return all(row.get(k) == v for k, v in PRIOR_HYPOTHESIS.items())

    prior_disc = next((r for r in discovery_rows if matches_prior(r)), None)
    to_validate = list(shortlist)
    prior_in_shortlist = any(matches_prior(r) for r in shortlist)
    if prior_disc is not None and not prior_in_shortlist:
        print("prior hypothesis is not in the shortlist; validating it anyway, "
              "reported separately and NOT eligible for promotion")
        to_validate.append(prior_disc)

    validation_rows = []
    with (OUT_DIR / "validation.jsonl").open("w", encoding="utf-8") as handle:
        for row in to_validate:
            candidate = _candidate_path(row["feature"], row["op"], row["threshold"], row["direction"])
            payload = run_cell(candidate, specs[row["payoff"]], data_hash, "validation")
            out = {k: row[k] for k in ("feature", "op", "threshold", "direction", "payoff")}
            out["is_prior_hypothesis"] = matches_prior(row)
            out["promotion_eligible"] = any(matches_prior(row) == matches_prior(s) and
                                            row is s for s in shortlist) or row in shortlist
            out.update(payload)
            handle.write(json.dumps(out, sort_keys=True) + "\n")
            validation_rows.append(out)
            tag = " [PRIOR]" if out["is_prior_hypothesis"] else ""
            print(f"VAL{tag} {out['feature']} {out['op']} {out['threshold']:g} {out['direction']:+d} "
                  f"{out['payoff']}: status={payload.get('status')} "
                  f"sess_bps={payload.get('net_portfolio_mean_bps')} "
                  f"sharpe={payload.get('net_sharpe')} "
                  f"sym_frac={payload.get('positive_symbol_fraction')}")

    summary = {
        "grid_cells": len(discovery_rows),
        "discovery_gate_passers": len(passers),
        "universe": list(UNIVERSE),
        "benchmark": BENCHMARK,
        "snapshot": {k: manifest[k] for k in ("provider", "session_count", "row_count", "data_sha256")},
        "splits": manifest["suggested_splits"],
        "prior_hypothesis": PRIOR_HYPOTHESIS,
        "prior_discovery": {k: (prior_disc or {}).get(k) for k in
                            ("status", "score", "net_sharpe", "net_portfolio_mean_bps",
                             "positive_symbol_fraction", "max_symbol_concentration", "gates")},
        "shortlist": [{k: r[k] for k in ("feature", "op", "threshold", "direction", "payoff", "score")}
                      for r in shortlist],
        "validation": [{k: r.get(k) for k in ("feature", "op", "threshold", "direction", "payoff",
                                              "is_prior_hypothesis", "promotion_eligible", "status",
                                              "score", "net_sharpe", "net_portfolio_mean_bps",
                                              "net_trade_mean_bps", "positive_symbol_fraction",
                                              "max_symbol_concentration", "trades_per_session",
                                              "trade_minus_session_bps", "trades", "gates")}
                       for r in validation_rows],
        "promoted": [{k: r[k] for k in ("feature", "op", "threshold", "direction", "payoff")}
                     for r in validation_rows
                     if r.get("status") == "passed" and r.get("promotion_eligible")],
        "economics": {"cost_bps": 5.0, "data_hash": data_hash,
                      "max_concentration": 0.20, "min_symbols": 10},
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                                          encoding="utf-8")
    print("\npromoted: " + json.dumps(summary["promoted"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
