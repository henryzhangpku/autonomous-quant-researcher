"""Bars campaign 1: preregistered single-rule grid through the bars evaluator.

Declared BEFORE any result is read, in full:

- Universe: SPY, QQQ, IWM, DIA, XLF, XLE, XLK, SMH — eight liquid US ETFs,
  chosen for tradability and sector breadth, not for backtest behaviour.
- Grid: every (feature, threshold, comparison, direction) from the table
  below, evaluated under both payoff modes. Each cell is ONE rule: take the
  position when the feature is above/below the threshold, flat otherwise.
  No compound conditions, no parameter fitting inside a cell.
- Economics: 5 bps per trade (the family default), equal-weight daily
  portfolio, gates as shipped in research/validators/bars_universe.py. The
  double-cost gate stresses 10 bps.
- Splits: the snapshot's own chronological 60/20/20, frozen at prepare time.
- Promotion rule: the top THREE discovery cells by score among full
  gate-passers — no more, no substitutions — are evaluated once on the
  validation split at identical economics. A cell promotes only if it passes
  every validation gate. The holdout split is never touched by this script.

The LLM proposer is deliberately absent. Campaign evidence from 2026-08-13
(overnight-spx, and this repo's own idea runs) is that a designed grid finds
the boundary of an effect while a 7B proposer collapses onto one formulation
and exhausts its admission budget. A grid also cannot silently change its
hypothesis after seeing a result.

Output: <out>/discovery.jsonl, <out>/validation.jsonl, <out>/summary.json.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPOSITORY_ROOT / ".research" / "sweeps" / "bars-rules-v1"
SNAPSHOT_DIR = REPOSITORY_ROOT / ".research" / "data" / "bars" / "bars-campaign-1"
SPEC_PATH = OUT_DIR / "spec.json"

UNIVERSE = ("SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK", "SMH")
START, END = "2021-08-01", "2026-08-11"
SHORTLIST = 3

# (feature, thresholds). Thresholds are round numbers picked from the
# feature's natural scale, not tuned: returns in whole percent, ratios and
# positions at obvious fractions.
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
PAYOFFS = ("next_open_to_close", "next_close_to_close")

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
    return prepare(symbols=UNIVERSE, start=START, end=END, out=SNAPSHOT_DIR)


def write_spec(manifest: dict) -> str:
    """Freeze the evaluation surface before the first cell runs."""
    spec = {
        "name": "bars-rules-v1",
        "universe": list(UNIVERSE),
        "splits": manifest["suggested_splits"],
        "min_trades": {"discovery": 120, "validation": 50, "holdout": 50},
        "min_sessions": {"discovery": 60, "validation": 25, "holdout": 25},
        "min_symbols": 6,
        "max_concentration": 0.35,
        "cost_bps": 5.0,
        "target_sharpe": 0.75,
        "payoff": PAYOFFS[0],
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    import hashlib

    written = {}
    for payoff in PAYOFFS:
        spec_payoff = dict(spec, payoff=payoff)
        path = OUT_DIR / f"spec-{payoff}.json"
        raw = (json.dumps(spec_payoff, indent=2, sort_keys=True) + "\n").encode("utf-8")
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
        cwd=REPOSITORY_ROOT, text=True, capture_output=True, timeout=300,
    )
    line = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else "{}"
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {"status": "unparseable", "raw": line[:300]}


def main() -> int:
    manifest = build_snapshot()
    data_hash = manifest["data_sha256"]
    specs = write_spec(manifest)
    print(f"snapshot: {manifest['session_count']} sessions, {manifest['row_count']} rows, "
          f"provider {manifest['provider']}")
    print(f"splits: {json.dumps(manifest['suggested_splits'])}")

    discovery_rows = []
    with (OUT_DIR / "discovery.jsonl").open("w", encoding="utf-8") as handle:
        for feature, thresholds in GRID:
            for threshold in thresholds:
                for op in COMPARISONS:
                    for direction in DIRECTIONS:
                        op_py = "<" if op == "lt" else ">"
                        candidate = OUT_DIR / f"c_{feature}_{op}_{threshold:g}_{direction:+d}.py"
                        candidate.write_text(textwrap.dedent(CANDIDATE.format(
                            feature=feature, op=op, op_py=op_py,
                            threshold=threshold, direction=direction)).lstrip(), encoding="utf-8")
                        for payoff in PAYOFFS:
                            payload = run_cell(candidate, specs[payoff], data_hash, "discovery")
                            row = {"feature": feature, "op": op, "threshold": threshold,
                                   "direction": direction, "payoff": payoff, **payload}
                            handle.write(json.dumps(row, sort_keys=True) + "\n")
                            handle.flush()
                            discovery_rows.append(row)
                            flag = "PASS" if payload.get("status") == "passed" else "    "
                            print(f"{flag} {feature:>14} {op} {threshold:>7g} {direction:+d} "
                                  f"{payoff[5:]:<15} net/trade={payload.get('net_trade_mean_bps')} "
                                  f"sharpe={payload.get('net_sharpe')}")

    passers = [r for r in discovery_rows if r.get("status") == "passed"]
    passers.sort(key=lambda r: r.get("score", float("-inf")), reverse=True)
    shortlist = passers[:SHORTLIST]
    print(f"\n{len(discovery_rows)} cells, {len(passers)} discovery gate-passers; "
          f"shortlist of {len(shortlist)} goes to validation")

    validation_rows = []
    with (OUT_DIR / "validation.jsonl").open("w", encoding="utf-8") as handle:
        for row in shortlist:
            candidate = OUT_DIR / f"c_{row['feature']}_{row['op']}_{row['threshold']:g}_{row['direction']:+d}.py"
            payload = run_cell(candidate, specs[row["payoff"]], data_hash, "validation")
            out = {k: row[k] for k in ("feature", "op", "threshold", "direction", "payoff")}
            out.update(payload)
            handle.write(json.dumps(out, sort_keys=True) + "\n")
            validation_rows.append(out)
            print(f"VAL {out['feature']} {out['op']} {out['threshold']:g} {out['direction']:+d} "
                  f"{out['payoff']}: status={payload.get('status')} "
                  f"net/trade={payload.get('net_trade_mean_bps')} sharpe={payload.get('net_sharpe')}")

    summary = {
        "grid_cells": len(discovery_rows),
        "discovery_gate_passers": len(passers),
        "universe": list(UNIVERSE),
        "snapshot": {k: manifest[k] for k in ("provider", "session_count", "row_count", "data_sha256")},
        "splits": manifest["suggested_splits"],
        "shortlist": [{k: r[k] for k in ("feature", "op", "threshold", "direction", "payoff", "score")}
                      for r in shortlist],
        "validation": [{k: r.get(k) for k in ("feature", "op", "threshold", "direction", "payoff",
                                              "status", "score", "net_sharpe", "net_trade_mean_bps",
                                              "net_portfolio_mean_bps", "trades", "gates")}
                       for r in validation_rows],
        "promoted": [{k: r[k] for k in ("feature", "op", "threshold", "direction", "payoff")}
                     for r in validation_rows if r.get("status") == "passed"],
        "economics": {"cost_bps": 5.0, "data_hash": data_hash},
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                                          encoding="utf-8")
    print("\npromoted: " + json.dumps(summary["promoted"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
