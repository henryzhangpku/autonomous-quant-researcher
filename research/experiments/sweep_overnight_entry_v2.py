"""Campaign 2: preregistered grid promotion through evaluator v2.

Declared BEFORE any result is read, in full:

- Grid: entry minutes 135..900 step 45 plus 915; widths {20, 25}; short-strike
  offsets {10, 15, 20}; exit modes {settlement, morning}; unconditional put
  credit only (campaign 1's evidence family). 228 cells.
- Economics: cost_frac 0.015 (above the first live curb measurement of
  0.006-0.010), vrp 1.0 (no assumed premium - conservative for credits),
  EWMA vol. Frozen snapshot 29167dd5..., discovery split.
- Promotion rule: the top THREE discovery cells by score among full
  gate-passers - no more, no substitutions - are evaluated once on the
  validation split at identical economics. A cell promotes only if it passes
  every validation gate. The 2026-07+ holdout stays sealed regardless.

Output: <out>/discovery.jsonl, <out>/validation.jsonl, <out>/summary.json.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DATA_HASH = "29167dd552d786af24d644764eff434006c7a7754c48ceb09e002a15f1d9e69c"
OUT_DIR = REPOSITORY_ROOT / ".research" / "sweeps" / "overnight-entry-v2"

ENTRY_MINUTES = tuple(range(135, 901, 45)) + (915,)
WIDTHS = (20.0, 25.0)
OFFSETS = (10.0, 15.0, 20.0)
EXITS = ("settlement", "morning")
COST_FRAC = "0.015"
VRP = "1.0"
SHORTLIST = 3

CANDIDATE = """
    from research.backtest import spx_put_credit_spread

    ENTRY_MIN = {minute}
    LABEL = "v2 put credit w{width:g} o{offset:g} @{minute} {exit}"

    def signal(session, entry_minute):
        return True

    def structure(session, entry_price):
        return spx_put_credit_spread(entry_price, width={width}, otm_offset={offset})
"""


def run_cell(candidate: Path, split: str, exit_mode: str) -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", "research.validators.overnight_spx_v2",
         "--candidate", str(candidate), "--data-hash", DATA_HASH,
         "--split", split, "--cost-frac", COST_FRAC, "--vrp", VRP,
         "--exit", exit_mode],
        cwd=REPOSITORY_ROOT, text=True, capture_output=True, timeout=300,
    )
    line = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else "{}"
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {"status": "unparseable", "raw": line[:500]}


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    discovery_rows = []
    with (OUT_DIR / "discovery.jsonl").open("w", encoding="utf-8") as handle:
        for minute in ENTRY_MINUTES:
            for width in WIDTHS:
                for offset in OFFSETS:
                    candidate = OUT_DIR / f"cand_{minute}_{width:g}_{offset:g}.py"
                    candidate.write_text(textwrap.dedent(CANDIDATE.format(
                        minute=minute, width=width, offset=offset, exit="")), encoding="utf-8")
                    for exit_mode in EXITS:
                        payload = run_cell(candidate, "discovery", exit_mode)
                        row = {"entry_minute": minute, "width": width, "offset": offset,
                               "exit": exit_mode, **payload}
                        handle.write(json.dumps(row, sort_keys=True) + "\n")
                        handle.flush()
                        discovery_rows.append(row)
                        print(f"disc {minute:>3} w{width:g} o{offset:g} {exit_mode:<10}: "
                              f"net={payload.get('net_mean_w')} status={payload.get('status')} "
                              f"score={payload.get('score')}")

    passers = [row for row in discovery_rows if row.get("status") == "passed"]
    passers.sort(key=lambda row: row.get("score", float("-inf")), reverse=True)
    shortlist = passers[:SHORTLIST]
    print(f"\n{len(passers)} discovery gate-passers; shortlist of {len(shortlist)} goes to validation")

    validation_rows = []
    with (OUT_DIR / "validation.jsonl").open("w", encoding="utf-8") as handle:
        for row in shortlist:
            candidate = OUT_DIR / f"cand_{row['entry_minute']}_{row['width']:g}_{row['offset']:g}.py"
            payload = run_cell(candidate, "validation", row["exit"])
            out = {"entry_minute": row["entry_minute"], "width": row["width"],
                   "offset": row["offset"], "exit": row["exit"], **payload}
            handle.write(json.dumps(out, sort_keys=True) + "\n")
            validation_rows.append(out)
            print(f"VAL  {row['entry_minute']:>3} w{row['width']:g} o{row['offset']:g} "
                  f"{row['exit']:<10}: net={payload.get('net_mean_w')} "
                  f"status={payload.get('status')} score={payload.get('score')}")

    summary = {
        "grid_cells": len(discovery_rows),
        "discovery_gate_passers": len(passers),
        "shortlist": [
            {key: row[key] for key in ("entry_minute", "width", "offset", "exit", "score", "net_mean_w")}
            for row in shortlist
        ],
        "validation": [
            {key: row.get(key) for key in ("entry_minute", "width", "offset", "exit", "status", "score",
                                           "net_mean_w", "net_weekly_sharpe", "weekly_bootstrap_lb_w", "gates")}
            for row in validation_rows
        ],
        "promoted": [
            {key: row[key] for key in ("entry_minute", "width", "offset", "exit")}
            for row in validation_rows if row.get("status") == "passed"
        ],
        "economics": {"cost_frac": COST_FRAC, "vrp": VRP, "data_hash": DATA_HASH},
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("\n" + json.dumps(summary["promoted"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
