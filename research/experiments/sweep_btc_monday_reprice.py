"""BTC Monday campaign: is the known weekend move continued or faded?

By Monday 09:30 ET the weekend move is not news. It printed over 65.5 hours
while every equity market was shut, and anyone can read it. The question this
campaign asks is what the following session does with a move that is already
public: continue it, fade it, or nothing. That is a boundary effect with a
mechanism — a large, unhedgeable-in-equities move meets the return of equity-
hours participants — rather than a threshold hunted through a feature list.

Declared BEFORE any result is read, in full:

- Instrument and window: BTC/USD spot, Monday 09:30 ET -> Monday 16:00 ET,
  243 Mondays from 2021-12-20 to 2026-08-10. Decision at 09:30, payoff is
  that session's own return.
- The conditioning feature is `weekend_ret`, the Friday 16:00 -> Monday 09:30
  return, complete and observable at the decision instant.
- UNCONDITIONAL BASELINE FIRST, both directions, before any conditional cell.
  The weekend campaign showed why: an unconditional drift will otherwise be
  read as a signal wearing a threshold.
- Grid, fixed here: `weekend_ret` at +/-1%, +/-2%, +/-3%, +/-5% and
  `weekend_abs` at 2%, 3%, 5%, each with {lt, gt} x {long, short}. Both
  directions are always tested so continuation and fade are answered
  symmetrically; a mechanism that only works on the profitable side is a
  coincidence.
- Economics: 10 bps round trip, doubled-cost stress 20 bps.
- Gates: shipped bars_universe set, single-symbol configuration. min_trades
  40 discovery / 15 validation against 145 and 49 available Mondays, so a
  rule must fire on roughly a third of them.
- Splits: the snapshot's own chronological 60/20/20, frozen at prepare time.
- Promotion rule: top THREE discovery cells by score among full gate-passers,
  no more and no substitutions, each evaluated once on validation. The
  holdout is never touched by this script.

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
OUT_DIR = REPOSITORY_ROOT / ".research" / "sweeps" / "btc-monday-reprice-v1"
SNAPSHOT_DIR = REPOSITORY_ROOT / ".research" / "data" / "crypto" / "btc-monday-rth"
SHORTLIST = 3
COST_BPS = 10.0

GRID: tuple[tuple[str, tuple[float, ...]], ...] = (
    ("weekend_ret", (-0.05, -0.03, -0.02, -0.01, 0.01, 0.02, 0.03, 0.05)),
    ("weekend_abs", (0.02, 0.03, 0.05)),
)
COMPARISONS = ("lt", "gt")
DIRECTIONS = (1, -1)

CONDITIONAL = """
LABEL = "{feature} {op} {threshold:g} -> {direction:+d}"

def signal(symbol, features):
    if features["{feature}"] {op_py} {threshold}:
        return {direction}
    return 0
"""

BASELINE = """
LABEL = "unconditional {direction:+d}"

def signal(symbol, features):
    return {direction}
"""


def write_spec(manifest: dict) -> tuple[Path, str]:
    spec = {
        "name": "btc-monday-reprice-v1",
        "universe": ["BTC-MONDAY_RTH"],
        "splits": manifest["suggested_splits"],
        "min_trades": {"discovery": 40, "validation": 15, "holdout": 15},
        "min_sessions": {"discovery": 40, "validation": 15, "holdout": 15},
        "min_symbols": 1,
        "max_concentration": 1.0,
        "cost_bps": COST_BPS,
        "target_sharpe": 0.75,
        "payoff": "next_open_to_close",
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "spec.json"
    raw = (json.dumps(spec, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def run(candidate: Path, spec: tuple[Path, str], data_hash: str, split: str) -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", "research.validators.bars_universe",
         "--candidate", str(candidate), "--spec", str(spec[0]), "--spec-hash", spec[1],
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
    manifest = json.loads((SNAPSHOT_DIR / "manifest.json").read_text(encoding="utf-8"))
    data_hash = manifest["data_sha256"]
    spec = write_spec(manifest)
    print(f"snapshot: {manifest['row_count']} Mondays "
          f"{manifest['first_session']} -> {manifest['last_session']}")
    print(f"splits: {json.dumps(manifest['suggested_splits'])}\n")

    rows: list[dict] = []
    baselines: dict[str, dict] = {}
    print("=== UNCONDITIONAL BASELINE (Monday RTH) ===")
    for direction in DIRECTIONS:
        path = OUT_DIR / f"c_base_{direction:+d}.py"
        path.write_text(textwrap.dedent(BASELINE.format(direction=direction)).lstrip(), encoding="utf-8")
        for split in ("discovery", "validation"):
            payload = run(path, spec, data_hash, split)
            baselines[f"{direction:+d}/{split}"] = payload
            print(f"  unconditional {direction:+d} {split:<11}: n={payload.get('trades'):>4} "
                  f"bps/session={payload.get('net_portfolio_mean_bps')} "
                  f"sharpe={payload.get('net_sharpe')} status={payload.get('status')}")
        rows.append({"feature": "UNCONDITIONAL", "op": "-", "threshold": 0.0,
                     "direction": direction, **baselines[f"{direction:+d}/discovery"]})
    print()

    with (OUT_DIR / "discovery.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        for feature, thresholds in GRID:
            for threshold in thresholds:
                for op in COMPARISONS:
                    for direction in DIRECTIONS:
                        op_py = "<" if op == "lt" else ">"
                        path = OUT_DIR / f"c_{feature}_{op}_{threshold:g}_{direction:+d}.py"
                        path.write_text(textwrap.dedent(CONDITIONAL.format(
                            feature=feature, op=op, op_py=op_py,
                            threshold=threshold, direction=direction)).lstrip(), encoding="utf-8")
                        payload = run(path, spec, data_hash, "discovery")
                        row = {"feature": feature, "op": op, "threshold": threshold,
                               "direction": direction, **payload}
                        handle.write(json.dumps(row, sort_keys=True) + "\n")
                        handle.flush()
                        rows.append(row)
                        flag = "PASS" if payload.get("status") == "passed" else "    "
                        print(f"{flag} {feature:>12} {op} {threshold:>6g} {direction:+d}: "
                              f"n={payload.get('trades'):>4} "
                              f"bps/session={payload.get('net_portfolio_mean_bps')} "
                              f"sharpe={payload.get('net_sharpe')}")

    passers = [r for r in rows if r.get("status") == "passed"]
    passers.sort(key=lambda r: r.get("score", float("-inf")), reverse=True)
    shortlist = passers[:SHORTLIST]
    print(f"\n{len(rows)} cells, {len(passers)} discovery gate-passers; "
          f"shortlist of {len(shortlist)} goes to validation")

    validation_rows = []
    with (OUT_DIR / "validation.jsonl").open("w", encoding="utf-8") as handle:
        for row in shortlist:
            if row["feature"] == "UNCONDITIONAL":
                path = OUT_DIR / f"c_base_{row['direction']:+d}.py"
            else:
                path = OUT_DIR / f"c_{row['feature']}_{row['op']}_{row['threshold']:g}_{row['direction']:+d}.py"
            payload = run(path, spec, data_hash, "validation")
            out = {k: row[k] for k in ("feature", "op", "threshold", "direction")}
            out.update(payload)
            handle.write(json.dumps(out, sort_keys=True) + "\n")
            validation_rows.append(out)
            failed = [k for k, ok in (payload.get("gates") or {}).items() if not ok]
            print(f"VAL {out['feature']} {out['op']} {out['threshold']:g} {out['direction']:+d}: "
                  f"status={payload.get('status')} n={payload.get('trades')} "
                  f"bps/session={payload.get('net_portfolio_mean_bps')} "
                  f"sharpe={payload.get('net_sharpe')}")
            if failed:
                print(f"    failed: {failed}")

    summary = {
        "question": "Monday 09:30-16:00 ET BTC, conditioned on the known Fri->Mon weekend move",
        "grid_cells": len(rows),
        "discovery_gate_passers": len(passers),
        "baselines": {k: {f: p.get(f) for f in
                          ("status", "trades", "net_portfolio_mean_bps", "net_sharpe")}
                      for k, p in baselines.items()},
        "snapshot": {k: manifest[k] for k in
                     ("symbol", "row_count", "first_session", "last_session", "data_sha256")},
        "splits": manifest["suggested_splits"],
        "shortlist": [{k: r[k] for k in ("feature", "op", "threshold", "direction", "score")}
                      for r in shortlist],
        "validation": [{k: r.get(k) for k in ("feature", "op", "threshold", "direction", "status",
                                              "score", "net_sharpe", "net_portfolio_mean_bps",
                                              "trades", "gates")}
                       for r in validation_rows],
        "promoted": [{k: r[k] for k in ("feature", "op", "threshold", "direction")}
                     for r in validation_rows if r.get("status") == "passed"],
        "economics": {"cost_bps": COST_BPS, "data_hash": data_hash},
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                                          encoding="utf-8")
    print("\npromoted: " + json.dumps(summary["promoted"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
