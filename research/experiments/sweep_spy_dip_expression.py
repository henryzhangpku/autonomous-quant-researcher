"""SPY dip-expression campaign: frozen signal, searched expression, hard gates.

The validated fact (2026-08-13 postmortem): "SPY down >= 0.5% from open in the
mid-afternoon" selects better-than-average afternoons in BOTH 2016-2021 and
2022-2024 - but the expression it was found wearing (ATM 2-wide call debit)
loses absolutely in every unseen year. This campaign freezes the SIGNAL and
searches only its EXPRESSION. The dip threshold is deliberately NOT searched:
re-mining the condition would spend the signal's out-of-sample credibility.

Declared BEFORE any result is read, in full:

- Signal, frozen: ret_from_open(entry_minute) <= -0.5 (percent).
- Grid: entries {(14,0), (14,30), (15,0)} ET x structures {call debit,
  put credit} x widths {1, 2, 3} x otm offsets {0, 1, 2} = 54 cells.
- Economics: cost_frac 0.03 of width (founding, quote-validated), doubled to
  0.06 by the survival gate. Evaluator: research.validators.options_v2.
- Promotion rule: top THREE discovery (2016-2021) gate-passers by score go to
  one validation (2022-2024) shot each; promotion requires every validation
  gate. The 2025+ holdout stays sealed.

Output: <out>/discovery.jsonl, <out>/validation.jsonl, <out>/summary.json.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPOSITORY_ROOT / ".research" / "sweeps" / "spy-dip-expression-v1"

ENTRIES = ((14, 0), (14, 30), (15, 0))
STRUCTURES = ("call_debit_spread", "put_credit_spread")
WIDTHS = (1.0, 2.0, 3.0)
OFFSETS = (0.0, 1.0, 2.0)
COST_FRAC = "0.03"
SHORTLIST = 3
DIP_THRESHOLD = -0.5

CANDIDATE = """
    from research.backtest import {structure}

    ENTRY_HM = ({hour}, {minute})
    LABEL = "dip {structure} w{width:g} o{offset:g} @{hour:02d}{minute:02d}"

    def signal(session, entry_minute):
        observed = session.ret_from_open(entry_minute)
        return observed is not None and observed <= {threshold}

    def structure(session, entry_price):
        return {structure}(entry_price, width={width}, otm_offset={offset})
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
            for structure in STRUCTURES:
                for width in WIDTHS:
                    for offset in OFFSETS:
                        candidate = OUT_DIR / f"cand_{hour}{minute:02d}_{structure}_{width:g}_{offset:g}.py"
                        candidate.write_text(textwrap.dedent(CANDIDATE.format(
                            structure=structure, hour=hour, minute=minute,
                            width=width, offset=offset, threshold=DIP_THRESHOLD,
                        )), encoding="utf-8")
                        payload = run_cell(candidate, "discovery")
                        row = {"entry": f"{hour:02d}:{minute:02d}", "structure": structure,
                               "width": width, "offset": offset, **payload}
                        handle.write(json.dumps(row, sort_keys=True) + "\n")
                        handle.flush()
                        discovery_rows.append(row)
                        print(f"disc {hour:02d}:{minute:02d} {structure:<18} w{width:g} o{offset:g}: "
                              f"net={payload.get('net_mean_w')} status={payload.get('status')} "
                              f"score={payload.get('score')}")

    passers = [row for row in discovery_rows if row.get("status") == "passed"]
    passers.sort(key=lambda row: row.get("score", float("-inf")), reverse=True)
    shortlist = passers[:SHORTLIST]
    print(f"\n{len(passers)} discovery gate-passers; shortlist of {len(shortlist)} goes to validation")

    validation_rows = []
    with (OUT_DIR / "validation.jsonl").open("w", encoding="utf-8") as handle:
        for row in shortlist:
            hour, minute = (int(part) for part in row["entry"].split(":"))
            candidate = OUT_DIR / f"cand_{hour}{minute:02d}_{row['structure']}_{row['width']:g}_{row['offset']:g}.py"
            payload = run_cell(candidate, "validation")
            out = {"entry": row["entry"], "structure": row["structure"],
                   "width": row["width"], "offset": row["offset"], **payload}
            handle.write(json.dumps(out, sort_keys=True) + "\n")
            validation_rows.append(out)
            print(f"VAL  {row['entry']} {row['structure']:<18} w{row['width']:g} o{row['offset']:g}: "
                  f"net={payload.get('net_mean_w')} status={payload.get('status')} "
                  f"score={payload.get('score')}")

    summary = {
        "signal_frozen": f"ret_from_open(entry) <= {DIP_THRESHOLD}",
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
