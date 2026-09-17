"""BTC weekend campaign 1: is the Friday-close to Monday-open window tradeable?

The window is a structural fact, not a data-mined slice. BTC prices
continuously for the 65.5 hours from Friday 16:00 ET to Monday 09:30 ET while
every equity market is shut, so information accumulates with a different set
of participants pricing it. Either that shows up in the window's return or it
does not, and the answer is directly tradeable: BTC spot is open the whole
time, and it is the window the trader already trades.

Declared BEFORE any result is read, in full:

- Instrument and window: BTC/USD spot, Friday 16:00 ET -> Monday 09:30 ET,
  242 windows from 2021-12-24 to 2026-08-07. Decision is taken at the Friday
  open of the window; every feature is computed from bars at or before that
  instant.
- UNCONDITIONAL BASELINE FIRST. Two cells, always-long and always-short, are
  evaluated before any conditional cell. If the window carries a simple
  directional drift, that is the finding, and every conditional result must
  be read as an improvement on it rather than as a discovery in its own
  right. Reporting a conditional edge without knowing the baseline is how a
  drift gets sold as a signal — bars campaign 1 in this repo did exactly that.
- Grid: eight causal features x round-number thresholds x {lt, gt} x
  {long, short}, plus the two baseline cells.
- Economics: 10 bps round trip, doubled-cost stress at 20 bps. BTC/USD spot
  at this venue quotes inside a few bps; 10 is deliberately above it.
- Gates: the shipped bars_universe set, single-symbol configuration
  (min_symbols 1, concentration not applicable, symbol fraction must be 1.0).
  min_trades 50 discovery / 20 validation against 145 and 48 available
  windows, so a rule must fire on roughly a third of weekends to qualify.
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
OUT_DIR = REPOSITORY_ROOT / ".research" / "sweeps" / "btc-weekend-v1"
SNAPSHOT_DIR = REPOSITORY_ROOT / ".research" / "data" / "crypto" / "btc-weekend"
SHORTLIST = 3
COST_BPS = 10.0

GRID: tuple[tuple[str, tuple[float, ...]], ...] = (
    ("prior_window_ret", (-0.05, -0.02, 0.02, 0.05)),
    ("prior_2window_ret", (-0.05, 0.05)),
    ("mean_prior_ret", (-0.01, 0.01)),
    ("vol_prior", (0.02, 0.04)),
    ("ret_24h", (-0.03, -0.01, 0.01, 0.03)),
    ("ret_7d", (-0.05, -0.02, 0.02, 0.05)),
    ("streak_up", (1.0, 2.0)),
    ("month", (3.0, 9.0)),
)
COMPARISONS = ("lt", "gt")
DIRECTIONS = (1, -1)
PAYOFF = "next_open_to_close"

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
        "name": "btc-weekend-v1",
        "universe": ["BTC-WEEKEND"],
        "splits": manifest["suggested_splits"],
        "min_trades": {"discovery": 50, "validation": 20, "holdout": 20},
        "min_sessions": {"discovery": 50, "validation": 20, "holdout": 20},
        "min_symbols": 1,
        "max_concentration": 1.0,
        "cost_bps": COST_BPS,
        "target_sharpe": 0.75,
        "payoff": PAYOFF,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "spec.json"
    raw = (json.dumps(spec, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


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
    manifest = json.loads((SNAPSHOT_DIR / "manifest.json").read_text(encoding="utf-8"))
    data_hash = manifest["data_sha256"]
    spec = write_spec(manifest)
    print(f"snapshot: {manifest['row_count']} weekend windows "
          f"{manifest['first_session']} -> {manifest['last_session']}, "
          f"{manifest['hourly_bars']} hourly bars")
    print(f"splits: {json.dumps(manifest['suggested_splits'])}\n")

    rows: list[dict] = []

    # Baselines first, and printed first, so no conditional result can be read
    # without the drift it has to beat.
    print("=== UNCONDITIONAL BASELINE ===")
    baselines = {}
    for direction in DIRECTIONS:
        path = OUT_DIR / f"c_base_{direction:+d}.py"
        path.write_text(textwrap.dedent(BASELINE.format(direction=direction)).lstrip(), encoding="utf-8")
        for split in ("discovery", "validation"):
            payload = run_cell(path, spec, data_hash, split)
            baselines[f"{direction:+d}/{split}"] = payload
            print(f"  unconditional {direction:+d} {split:<11}: "
                  f"status={payload.get('status')} trades={payload.get('trades')} "
                  f"bps/window={payload.get('net_portfolio_mean_bps')} "
                  f"sharpe={payload.get('net_sharpe')}")
        disc = baselines[f"{direction:+d}/discovery"]
        rows.append({"feature": "UNCONDITIONAL", "op": "-", "threshold": 0.0,
                     "direction": direction, "payoff": PAYOFF, **disc})
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
                        payload = run_cell(path, spec, data_hash, "discovery")
                        row = {"feature": feature, "op": op, "threshold": threshold,
                               "direction": direction, "payoff": PAYOFF, **payload}
                        handle.write(json.dumps(row, sort_keys=True) + "\n")
                        handle.flush()
                        rows.append(row)
                        flag = "PASS" if payload.get("status") == "passed" else "    "
                        print(f"{flag} {feature:>18} {op} {threshold:>6g} {direction:+d}: "
                              f"trades={payload.get('trades'):>4} "
                              f"bps/window={payload.get('net_portfolio_mean_bps')} "
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
            payload = run_cell(path, spec, data_hash, "validation")
            out = {k: row[k] for k in ("feature", "op", "threshold", "direction", "payoff")}
            out.update(payload)
            handle.write(json.dumps(out, sort_keys=True) + "\n")
            validation_rows.append(out)
            print(f"VAL {out['feature']} {out['op']} {out['threshold']:g} {out['direction']:+d}: "
                  f"status={payload.get('status')} trades={payload.get('trades')} "
                  f"bps/window={payload.get('net_portfolio_mean_bps')} "
                  f"sharpe={payload.get('net_sharpe')}")

    summary = {
        "window": "Fri 16:00 ET -> Mon 09:30 ET",
        "grid_cells": len(rows),
        "discovery_gate_passers": len(passers),
        "baselines": {key: {k: payload.get(k) for k in
                            ("status", "trades", "net_portfolio_mean_bps", "net_sharpe", "gates")}
                      for key, payload in baselines.items()},
        "snapshot": {k: manifest[k] for k in
                     ("symbol", "row_count", "first_session", "last_session", "data_sha256")},
        "splits": manifest["suggested_splits"],
        "shortlist": [{k: r[k] for k in ("feature", "op", "threshold", "direction", "score")}
                      for r in shortlist],
        "validation": [{k: r.get(k) for k in ("feature", "op", "threshold", "direction", "status",
                                              "score", "net_sharpe", "net_portfolio_mean_bps",
                                              "net_trade_mean_bps", "trades", "gates")}
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
