"""Buy-the-dip overnight call debit spreads on SPX, gated (Henry, 2026-08-17).

"Can buying the dip overnight with an SPX debit spread make money
CONSISTENTLY?" The lab's adjacent evidence: plain overnight debit spreads
found no survivor (overnight-1dte-v1), single-leg long calls died at every
entry, and the only validated overnight edge is the short-side put credit.
What none of those isolated is the CONDITION — entering only after a down
day, when the overnight premium literature says the odds tilt. This grid
asks exactly that.

Declared BEFORE any result is read, in full:

- Signal, frozen: the RTH day is down at least X% at entry —
  ret_from_prior_close(entry_minute) <= -X for X in {0.5, 1.0, 1.5}.
- Grid: entries {135, 225, 315} (20:15 / 21:45 / 23:15 ET curb) x widths
  {20, 25} x otm offsets {0, 10} = 36 cells, all call debit spreads
  (buying the dip is bullish).
- Economics: cost 1.5% of width, vrp 1.0, the same pinned overnight
  snapshot and frozen overnight_spx_v2 evaluator the promoted policy
  passed. Exit at next-day cash settlement.
- Promotion rule: top-3 discovery gate-passers get one validation shot
  each; the 2025+ holdout stays sealed.

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
OUT_DIR = REPOSITORY_ROOT / ".research" / "sweeps" / "overnight-dip-debit-v1"

DIP_THRESHOLDS = (0.5, 1.0, 1.5)
ENTRY_MINUTES = (135, 225, 315)
WIDTHS = (20.0, 25.0)
OFFSETS = (0.0, 10.0)
COST_FRAC = "0.015"
VRP = "1.0"
SHORTLIST = 3

CANDIDATE = """
    from research.backtest import spx_call_debit_spread

    ENTRY_MIN = {minute}
    LABEL = "dip{dip:g} call debit w{width:g} o{offset:g} @{minute}"

    def signal(session, entry_minute):
        day_move = session.ret_from_prior_close(entry_minute)
        return day_move is not None and day_move <= -{dip}

    def structure(session, entry_price):
        return spx_call_debit_spread(entry_price, width={width}, otm_offset={offset})
"""


def run_cell(candidate: Path, split: str) -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", "research.validators.overnight_spx_v2",
         "--candidate", str(candidate), "--data-hash", DATA_HASH,
         "--split", split, "--cost-frac", COST_FRAC, "--vrp", VRP,
         "--exit", "settlement", "--horizon", "next_session"],
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
        for dip in DIP_THRESHOLDS:
            for minute in ENTRY_MINUTES:
                for width in WIDTHS:
                    for offset in OFFSETS:
                        candidate = OUT_DIR / f"cand_dip{dip:g}_{minute}_w{width:g}_o{offset:g}.py"
                        candidate.write_text(textwrap.dedent(CANDIDATE.format(
                            dip=dip, minute=minute, width=width, offset=offset,
                        )), encoding="utf-8")
                        payload = run_cell(candidate, "discovery")
                        row = {"dip": dip, "entry_minute": minute,
                               "structure": "spx_call_debit_spread",
                               "width": width, "offset": offset,
                               "exit": "settlement", **payload}
                        handle.write(json.dumps(row, sort_keys=True) + "\n")
                        handle.flush()
                        discovery_rows.append(row)
                        print(f"disc dip{dip:g} @{minute:>3} w{width:g} o{offset:g}: "
                              f"net={payload.get('net_mean_w')} status={payload.get('status')}")

    passers = [row for row in discovery_rows if row.get("status") == "passed"]
    passers.sort(key=lambda row: row.get("score", float("-inf")), reverse=True)
    shortlist = passers[:SHORTLIST]
    print(f"\n{len(passers)} discovery gate-passers; shortlist of {len(shortlist)}")

    validation_rows = []
    with (OUT_DIR / "validation.jsonl").open("w", encoding="utf-8") as handle:
        for row in shortlist:
            candidate = OUT_DIR / f"cand_dip{row['dip']:g}_{row['entry_minute']}_w{row['width']:g}_o{row['offset']:g}.py"
            payload = run_cell(candidate, "validation")
            out = {"dip": row["dip"], "entry_minute": row["entry_minute"],
                   "structure": row["structure"], "width": row["width"],
                   "offset": row["offset"], "exit": row["exit"], **payload}
            handle.write(json.dumps(out, sort_keys=True) + "\n")
            validation_rows.append(out)
            print(f"VAL  dip{row['dip']:g} @{row['entry_minute']:>3} w{row['width']:g} "
                  f"o{row['offset']:g}: status={payload.get('status')}")

    summary = {
        "question": "does buying the dip (down-day close) with an overnight SPX call debit spread pay after costs?",
        "grid_cells": len(discovery_rows),
        "discovery_gate_passers": len(passers),
        "shortlist": [
            {key: row[key] for key in ("dip", "entry_minute", "structure", "width",
                                       "offset", "exit", "score", "net_mean_w")}
            for row in shortlist
        ],
        "validation": [
            {key: row.get(key) for key in ("dip", "entry_minute", "structure", "width",
                                           "offset", "exit", "status", "score", "net_mean_w",
                                           "net_weekly_sharpe", "weekly_bootstrap_lb_w", "gates")}
            for row in validation_rows
        ],
        "promoted": [
            {key: row[key] for key in ("dip", "entry_minute", "structure", "width", "offset", "exit")}
            for row in validation_rows if row.get("status") == "passed"
        ],
        "economics": {"cost_frac": COST_FRAC, "vrp": VRP, "data_hash": DATA_HASH},
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("\npromoted: " + json.dumps(summary["promoted"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
