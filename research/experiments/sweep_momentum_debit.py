"""The Xueqiu trade, gated: morning-momentum call debit spreads on SPY 0DTE.

The 2020 post (the $40K->$4M thread) bought near-ATM SPX 0DTE call debit
spreads on upward-moving days. The book fact-check found its underlying
tailwind is real: gap-up mornings still rising at 9:55 keep rising to
10:55/13:00 ~60% of the time. This campaign asks the only question that
matters for trading it: does a debit spread MONETIZE that 60% after spread
pricing and costs, on ten years of data, through the full gate battery?

Declared BEFORE any result is read, in full:

- Signal, frozen: up >= +0.15% from the open at the entry minute (the
  momentum reading the fact-check validated; thresholds match it).
- Grid: entries {(9,55), (10,30), (11,30)} x call debit widths {1, 2, 3} x
  otm offsets {0, 1} = 18 cells. SPY dollars proxy SPX points x10.
- Economics: cost 3% of width (founding, quote-validated), doubled by the
  survival gate. Evaluator: options_v2, settle at close (the post rode
  winners rather than clipping them).
- Promotion rule: top-3 discovery gate-passers -> one validation shot each;
  the 2025+ holdout stays sealed.

Output: <out>/discovery.jsonl, <out>/validation.jsonl, <out>/summary.json.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPOSITORY_ROOT / ".research" / "sweeps" / "momentum-debit-v1"

ENTRIES = ((9, 55), (10, 30), (11, 30))
WIDTHS = (1.0, 2.0, 3.0)
OFFSETS = (0.0, 1.0)
COST_FRAC = "0.03"
SHORTLIST = 3
MOMENTUM_THRESHOLD = 0.15

CANDIDATE = """
    from research.backtest import call_debit_spread

    ENTRY_HM = ({hour}, {minute})
    LABEL = "momentum call debit w{width:g} o{offset:g} @{hour:02d}{minute:02d}"

    def signal(session, entry_minute):
        observed = session.ret_from_open(entry_minute)
        return observed is not None and observed >= {threshold}

    def structure(session, entry_price):
        return call_debit_spread(entry_price, width={width}, otm_offset={offset})
"""


def run_cell(candidate: Path, split: str) -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", "research.validators.options_v2",
         "--candidate", str(candidate), "--symbol", "SPY",
         "--split", split, "--cost-frac", COST_FRAC],
        cwd=REPOSITORY_ROOT, text=True, capture_output=True, timeout=600,
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
        for hour, minute in ENTRIES:
            for width in WIDTHS:
                for offset in OFFSETS:
                    candidate = OUT_DIR / f"cand_{hour}{minute:02d}_{width:g}_{offset:g}.py"
                    candidate.write_text(textwrap.dedent(CANDIDATE.format(
                        hour=hour, minute=minute, width=width, offset=offset,
                        threshold=MOMENTUM_THRESHOLD,
                    )), encoding="utf-8")
                    payload = run_cell(candidate, "discovery")
                    row = {"entry": f"{hour:02d}:{minute:02d}", "structure": "call_debit_spread",
                           "width": width, "offset": offset, **payload}
                    handle.write(json.dumps(row, sort_keys=True) + "\n")
                    handle.flush()
                    discovery_rows.append(row)
                    print(f"disc {hour:02d}:{minute:02d} w{width:g} o{offset:g}: "
                          f"net={payload.get('net_mean_w')} status={payload.get('status')} "
                          f"score={payload.get('score')}")

    passers = [row for row in discovery_rows if row.get("status") == "passed"]
    passers.sort(key=lambda row: row.get("score", float("-inf")), reverse=True)
    shortlist = passers[:SHORTLIST]
    print(f"\n{len(passers)} discovery gate-passers; shortlist of {len(shortlist)}")

    validation_rows = []
    with (OUT_DIR / "validation.jsonl").open("w", encoding="utf-8") as handle:
        for row in shortlist:
            hour, minute = (int(part) for part in row["entry"].split(":"))
            candidate = OUT_DIR / f"cand_{hour}{minute:02d}_{row['width']:g}_{row['offset']:g}.py"
            payload = run_cell(candidate, "validation")
            out = {"entry": row["entry"], "structure": "call_debit_spread",
                   "width": row["width"], "offset": row["offset"], **payload}
            handle.write(json.dumps(out, sort_keys=True) + "\n")
            validation_rows.append(out)
            print(f"VAL  {row['entry']} w{row['width']:g} o{row['offset']:g}: "
                  f"net={payload.get('net_mean_w')} status={payload.get('status')}")

    summary = {
        "signal_frozen": f"ret_from_open(entry) >= {MOMENTUM_THRESHOLD}",
        "inspiration": "Xueqiu 2020 thread: near-ATM SPX 0DTE call debit spreads on up days",
        "grid_cells": len(discovery_rows),
        "discovery_gate_passers": len(passers),
        "shortlist": [
            {key: row[key] for key in ("entry", "structure", "width", "offset", "score", "net_mean_w")}
            for row in shortlist
        ],
        "validation": [
            {key: row.get(key) for key in ("entry", "structure", "width", "offset", "status", "score",
                                           "net_mean_w", "net_weekly_sharpe", "weekly_bootstrap_lb_w",
                                           "by_year_net_mean_w", "gates")}
            for row in validation_rows
        ],
        "promoted": [
            {key: row[key] for key in ("entry", "structure", "width", "offset")}
            for row in validation_rows if row.get("status") == "passed"
        ],
        "economics": {"cost_frac": COST_FRAC},
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("\npromoted: " + json.dumps(summary["promoted"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
