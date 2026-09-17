"""SPY 0DTE put-credit campaign: how much execution cost can it survive?

629 sessions of ACTUAL traded option bars (2024-01-19 → 2026-08-10), entry
10:00 ET, exit at intrinsic because a 0DTE settles mechanically. P&L is a
fraction of width, the same unit the overnight-spx program reports, so the
two families are directly comparable.

Unconditionally this structure earns +1.199% of width per session at a 95.2%
win rate before costs. Whether that is a business depends entirely on
execution, and this repo has now twice killed a candidate at
`survives_double_cost` against a cost nobody measured. So this campaign does
not pick a cost and hide behind it. It runs the SAME cells at three declared
levels and reports the breakeven.

Declared BEFORE any result is read, in full:

- Cost levels, all expressed as fraction of width per round trip, evaluated
  independently: 0.5%, 1.0%, 1.5%. The 1.5% figure is the level the
  overnight-spx program measured on live SPXW quotes, so it is the
  conservative anchor; SPY penny-wide strikes should execute inside it, but
  no capture backs that here and the campaign refuses to assume it. Each
  level's doubled-cost gate stresses 2x that level.
- UNCONDITIONAL BASELINE FIRST at every cost level. The structure's own
  premium is the thing being measured; any conditional cell must be read as
  an improvement on it.
- Grid: `credit_frac` (the credit received as a fraction of width, i.e. how
  richly the market priced that morning's risk) at 0.10/0.15/0.20/0.25/0.30,
  and `day_of_week` at 0..4, each with {lt, gt} x {long-the-structure, skip}.
  Direction is fixed to +1: this is a credit structure, and shorting it means
  BUYING a debit spread, which the overnight program already found negative
  everywhere. Cells select WHEN to put the trade on, not which way.
- Gates: shipped bars_universe set, single-symbol configuration. min_trades
  150 discovery / 60 validation against 377 and 126 available sessions.
- Splits: the snapshot's own chronological 60/20/20, frozen at prepare time.
- Promotion rule: at each cost level independently, the top THREE discovery
  cells by score among full gate-passers — no more, no substitutions — each
  evaluated once on validation. The holdout is never touched.

The headline result is not a promotion, it is the breakeven cost: the level
at or below which this structure clears every gate. That number is directly
actionable — it says what execution quality the trade requires.

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
OUT_DIR = REPOSITORY_ROOT / ".research" / "sweeps" / "spy-odte-credit-v1"
SNAPSHOT_DIR = REPOSITORY_ROOT / ".research" / "data" / "odte" / "spy-pcs-w5-o5"
SHORTLIST = 3
# Fraction of width per round trip, expressed in the validator's bps units.
COST_LEVELS = {"0.5pct": 50.0, "1.0pct": 100.0, "1.5pct": 150.0}

GRID: tuple[tuple[str, tuple[float, ...]], ...] = (
    ("credit_frac", (0.10, 0.15, 0.20, 0.25, 0.30)),
    ("day_of_week", (0.0, 1.0, 2.0, 3.0)),
)
COMPARISONS = ("lt", "gt")

CONDITIONAL = """
LABEL = "{feature} {op} {threshold:g}"

def signal(symbol, features):
    if features["{feature}"] {op_py} {threshold}:
        return 1
    return 0
"""

BASELINE = """
LABEL = "unconditional credit spread"

def signal(symbol, features):
    return 1
"""


def write_spec(manifest: dict, label: str, cost_bps: float) -> tuple[Path, str]:
    spec = {
        "name": f"spy-odte-credit-{label}",
        "universe": ["SPY-0DTE-PCS"],
        "splits": manifest["suggested_splits"],
        "min_trades": {"discovery": 150, "validation": 60, "holdout": 60},
        "min_sessions": {"discovery": 150, "validation": 60, "holdout": 60},
        "min_symbols": 1,
        "max_concentration": 1.0,
        "cost_bps": cost_bps,
        "target_sharpe": 0.75,
        "payoff": "next_open_to_close",
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"spec-{label}.json"
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
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"snapshot: {manifest['row_count']} sessions "
          f"{manifest['first_session']} -> {manifest['last_session']}, "
          f"{manifest['structure']} w{manifest['width']:g} o{manifest['offset']:g}, "
          f"pricing: {manifest['pricing']}")
    print(f"splits: {json.dumps(manifest['suggested_splits'])}\n")

    base_path = OUT_DIR / "c_base.py"
    base_path.write_text(textwrap.dedent(BASELINE).strip() + "\n", encoding="utf-8")

    all_discovery: list[dict] = []
    all_validation: list[dict] = []
    baselines: dict[str, dict] = {}

    for label, cost_bps in COST_LEVELS.items():
        spec = write_spec(manifest, label, cost_bps)
        print(f"=== COST {label} ({cost_bps:g} bps of width per round trip) ===")
        for split in ("discovery", "validation"):
            payload = run(base_path, spec, data_hash, split)
            baselines[f"{label}/{split}"] = payload
            failed = [k for k, ok in (payload.get("gates") or {}).items() if not ok]
            print(f"  baseline {split:<11}: status={payload.get('status'):<9} "
                  f"n={payload.get('trades'):>4} "
                  f"net={payload.get('net_portfolio_mean_bps')} bps of width "
                  f"sharpe={payload.get('net_sharpe')}")
            if failed:
                print(f"      failed: {failed}")

        rows = [{"cost": label, "feature": "UNCONDITIONAL", "op": "-", "threshold": 0.0,
                 **baselines[f"{label}/discovery"]}]
        for feature, thresholds in GRID:
            for threshold in thresholds:
                for op in COMPARISONS:
                    op_py = "<" if op == "lt" else ">"
                    path = OUT_DIR / f"c_{feature}_{op}_{threshold:g}.py"
                    path.write_text(textwrap.dedent(CONDITIONAL.format(
                        feature=feature, op=op, op_py=op_py, threshold=threshold)).lstrip(),
                        encoding="utf-8")
                    payload = run(path, spec, data_hash, "discovery")
                    rows.append({"cost": label, "feature": feature, "op": op,
                                 "threshold": threshold, **payload})
        all_discovery.extend(rows)

        passers = [r for r in rows if r.get("status") == "passed"]
        passers.sort(key=lambda r: r.get("score", float("-inf")), reverse=True)
        shortlist = passers[:SHORTLIST]
        print(f"  {len(rows)} cells, {len(passers)} discovery passers, "
              f"{len(shortlist)} to validation")
        for row in shortlist:
            path = base_path if row["feature"] == "UNCONDITIONAL" else \
                OUT_DIR / f"c_{row['feature']}_{row['op']}_{row['threshold']:g}.py"
            payload = run(path, spec, data_hash, "validation")
            out = {"cost": label, **{k: row[k] for k in ("feature", "op", "threshold")}, **payload}
            all_validation.append(out)
            failed = [k for k, ok in (payload.get("gates") or {}).items() if not ok]
            print(f"  VAL {out['feature']} {out['op']} {out['threshold']:g}: "
                  f"status={payload.get('status')} n={payload.get('trades')} "
                  f"net={payload.get('net_portfolio_mean_bps')} sharpe={payload.get('net_sharpe')}")
            if failed:
                print(f"      failed: {failed}")
        print()

    with (OUT_DIR / "discovery.jsonl").open("w", encoding="utf-8") as handle:
        for row in all_discovery:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    with (OUT_DIR / "validation.jsonl").open("w", encoding="utf-8") as handle:
        for row in all_validation:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    promoted = [r for r in all_validation if r.get("status") == "passed"]
    breakeven = [label for label in COST_LEVELS
                 if any(r["cost"] == label and r.get("status") == "passed" for r in all_validation)]
    summary = {
        "structure": f"{manifest['underlying']} 0DTE put credit spread, "
                     f"width {manifest['width']:g}, offset {manifest['offset']:g}, "
                     f"entry {manifest['entry_hour_et']}:00 ET, exit at intrinsic",
        "pricing": manifest["pricing"],
        "snapshot": {k: manifest[k] for k in
                     ("row_count", "first_session", "last_session", "skipped", "data_sha256")},
        "splits": manifest["suggested_splits"],
        "cost_levels_bps_of_width": COST_LEVELS,
        "baselines": {k: {f: p.get(f) for f in
                          ("status", "trades", "net_portfolio_mean_bps", "net_sharpe", "gates")}
                      for k, p in baselines.items()},
        "validation": [{k: r.get(k) for k in ("cost", "feature", "op", "threshold", "status",
                                              "net_portfolio_mean_bps", "net_sharpe", "trades",
                                              "gates")} for r in all_validation],
        "promoted": [{k: r[k] for k in ("cost", "feature", "op", "threshold")} for r in promoted],
        "cost_levels_with_a_promotion": breakeven,
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                                          encoding="utf-8")
    print("cost levels that produced a promotion: " + json.dumps(breakeven))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
