"""Overnight DEBIT verticals under the honest economics: closing the gap.

Campaign 1 rejected every overnight debit spread — but at 5%-of-width costs
and vrp 1.1, both of which punish buyers. Campaign 2's measured economics
(cost 1.5%, vrp 1.0) swept put credits only, so the buyer's side was never
re-asked under the fair terms. This grid closes that gap with the same
frozen v2 evaluator the promoted policy passed.

Declared BEFORE any result is read: entries 135..915 step 90 (plus 915),
structures {call debit, put debit}, widths {20, 25}, offsets {0, 10, 20},
exits {settlement, morning}. Cost 0.015 of width, vrp 1.0, same pinned
snapshot, same promotion rule (top-3 gate-passers -> one validation shot).
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DATA_HASH = "29167dd552d786af24d644764eff434006c7a7754c48ceb09e002a15f1d9e69c"
OUT_DIR = REPOSITORY_ROOT / ".research" / "sweeps" / "overnight-debit-v1"

ENTRY_MINUTES = tuple(range(135, 901, 90)) + (915,)
STRUCTURES = ("spx_call_debit_spread", "spx_put_debit_spread")
WIDTHS = (20.0, 25.0)
OFFSETS = (0.0, 10.0, 20.0)
EXITS = ("settlement", "morning")
COST_FRAC = "0.015"
VRP = "1.0"
SHORTLIST = 3

CANDIDATE = """
    from research.backtest import {structure}

    ENTRY_MIN = {minute}
    LABEL = "debit {structure} w{width:g} o{offset:g} @{minute}"

    def signal(session, entry_minute):
        return True

    def structure(session, entry_price):
        return {structure}(entry_price, width={width}, otm_offset={offset})
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
            for structure in STRUCTURES:
                for width in WIDTHS:
                    for offset in OFFSETS:
                        candidate = OUT_DIR / f"cand_{minute}_{structure}_{width:g}_{offset:g}.py"
                        candidate.write_text(textwrap.dedent(CANDIDATE.format(
                            minute=minute, structure=structure, width=width, offset=offset,
                        )), encoding="utf-8")
                        for exit_mode in EXITS:
                            payload = run_cell(candidate, "discovery", exit_mode)
                            row = {"entry_minute": minute, "structure": structure,
                                   "width": width, "offset": offset, "exit": exit_mode, **payload}
                            handle.write(json.dumps(row, sort_keys=True) + "\n")
                            handle.flush()
                            discovery_rows.append(row)
                            print(f"disc {minute:>3} {structure.replace('spx_', ''):<22} "
                                  f"w{width:g} o{offset:g} {exit_mode:<10}: "
                                  f"net={payload.get('net_mean_w')} status={payload.get('status')}")

    passers = [row for row in discovery_rows if row.get("status") == "passed"]
    passers.sort(key=lambda row: row.get("score", float("-inf")), reverse=True)
    shortlist = passers[:SHORTLIST]
    print(f"\n{len(passers)} discovery gate-passers; shortlist of {len(shortlist)}")

    validation_rows = []
    with (OUT_DIR / "validation.jsonl").open("w", encoding="utf-8") as handle:
        for row in shortlist:
            candidate = OUT_DIR / f"cand_{row['entry_minute']}_{row['structure']}_{row['width']:g}_{row['offset']:g}.py"
            payload = run_cell(candidate, "validation", row["exit"])
            out = {"entry_minute": row["entry_minute"], "structure": row["structure"],
                   "width": row["width"], "offset": row["offset"], "exit": row["exit"], **payload}
            handle.write(json.dumps(out, sort_keys=True) + "\n")
            validation_rows.append(out)
            print(f"VAL  {row['entry_minute']:>3} {row['structure']} w{row['width']:g} "
                  f"o{row['offset']:g}: status={payload.get('status')}")

    summary = {
        "question": "do overnight DEBIT verticals work at measured costs (campaign-2 economics)?",
        "grid_cells": len(discovery_rows),
        "discovery_gate_passers": len(passers),
        "shortlist": [
            {key: row[key] for key in ("entry_minute", "structure", "width", "offset", "exit", "score", "net_mean_w")}
            for row in shortlist
        ],
        "validation": [
            {key: row.get(key) for key in ("entry_minute", "structure", "width", "offset", "exit",
                                           "status", "score", "net_mean_w", "net_weekly_sharpe",
                                           "weekly_bootstrap_lb_w", "gates")}
            for row in validation_rows
        ],
        "promoted": [
            {key: row[key] for key in ("entry_minute", "structure", "width", "offset", "exit")}
            for row in validation_rows if row.get("status") == "passed"
        ],
        "economics": {"cost_frac": COST_FRAC, "vrp": VRP, "data_hash": DATA_HASH},
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("\npromoted: " + json.dumps(summary["promoted"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
