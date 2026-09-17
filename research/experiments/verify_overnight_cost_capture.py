"""Verify the overnight SPXW put-credit cost captures against the v3 rule.

Read-only analysis of the nightly curb-capture log produced by the Windows
host harness (`~/.qs-tools/spxw-curb-capture/`). It computes nothing that the
frozen validators own: no pricing, no gating of candidates, no score. It only
turns captured executable/mid quotes into the half-spread cost convention the
v2 campaign used (fraction of width), groups them by structure, and applies
the decision rule DECLARED IN `research/missions/overnight-spx/V3_ROADMAP.md`
BEFORE the capture week started.

Expected input: one JSONL row per quote snapshot with fields
    date        (YYYY-MM-DD, the capture night)
    window      (ET clock string, the "20:15" entry window or robustness)
    expiry      (the SPXW expiry date captured)
    width       (vertical width, float)
    offset      (short-strike OTM offset, float)
    exec_credit (executable credit received at the bid side, float)
    mid_credit  (mid-market credit for the same structure, float)
Half-spread cost of width = (mid_credit - exec_credit) / width.

Verdict rule (frozen, not renegotiable):
- qualifying nights  = distinct `date` values having a "20:15" window row for
  the leader cell (width 20, offset 15);
- if qualifying nights < MIN_NIGHTS (5): DEFER -- no cost decision, no
  holdout spend, exit 2 (fail-closed);
- m = median leader-cell half-spread cost:
    m <= 0.015 -> GREEN  (v2 economics stand; proceed at cost 0.015)
    0.015 < m < 0.03 -> REPRICE (holdout cost = m rounded UP to next 0.005)
    m >= 0.03   -> NO TRADE (permanent close of the put-credit line)
The leader cell is the ONLY decision input; the other width/offset cells are
reported as robustness context and cannot change the verdict.

Output: `.research/captures/verify-<YYYYmmdd-HHMM>.json` plus the same table
on stdout.
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = REPOSITORY_ROOT / ".research" / "captures"

#: The v3 roadmap's declared decision parameters (2026-08-18).
MIN_NIGHTS = 5
DECISION_WINDOW = "20:15"
LEADER = {"width": 20.0, "offset": 15.0}
COST_GREEN = 0.015        # m <= this: economics stand
COST_REPRICE_MAX = 0.03   # m >= this: permanent no-trade
REPRICE_STEP = 0.005      # reprice grid step (round UP)

REQUIRED_FIELDS = ("date", "window", "width", "offset", "exec_credit", "mid_credit")


def load_rows(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            missing = [field for field in REQUIRED_FIELDS if field not in row]
            if missing:
                raise ValueError(
                    f"{path}:{line_number}: missing fields {missing}; "
                    "the capture log does not match the declared schema"
                )
            rows.append(row)
    if not rows:
        raise ValueError(f"{path}: no capture rows found")
    return rows


def cost_of_width(row: dict) -> float:
    width = float(row["width"])
    if width <= 0:
        raise ValueError(f"non-positive width {width} in {row}")
    return (float(row["mid_credit"]) - float(row["exec_credit"])) / width


def cell_key(row: dict) -> tuple[float, float]:
    return (float(row["width"]), float(row["offset"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("capture_log", type=Path, help="JSONL capture log from the curb harness")
    parser.add_argument("--window", default=DECISION_WINDOW,
                        help=f"decision window clock (default {DECISION_WINDOW})")
    args = parser.parse_args()

    rows = load_rows(args.capture_log)
    by_cell: dict[tuple[float, float], list[float]] = {}
    for row in rows:
        by_cell.setdefault(cell_key(row), []).append(cost_of_width(row))

    leader = [(r["date"], r["window"]) for r in rows if cell_key(r) == (LEADER["width"], LEADER["offset"])]
    nights = sorted({date for date, _window in leader})
    decision_rows = [
        r for r in rows
        if cell_key(r) == (LEADER["width"], LEADER["offset"]) and r["window"] == args.window
    ]
    qualifying_nights = sorted({r["date"] for r in decision_rows})

    print(f"captures: {len(rows)} rows, {len(nights)} distinct nights, "
          f"{len(by_cell)} cells; decision window {args.window}")

    print("\nper-cell median half-spread cost (fraction of width):")
    for cell in sorted(by_cell):
        values = sorted(by_cell[cell])
        flag = "  <-- leader" if cell == (LEADER["width"], LEADER["offset"]) else ""
        print(f"  w{cell[0]:g} o{cell[1]:g}: n={len(values):3d}  "
              f"median={statistics.median(values):.4f}  mean={statistics.mean(values):.4f}  "
              f"min={values[0]:.4f}  max={values[-1]:.4f}{flag}")

    if len(qualifying_nights) < MIN_NIGHTS:
        report = {
            "status": "deferred",
            "reason": f"only {len(qualifying_nights)}/{MIN_NIGHTS} qualifying nights at "
                      f"{args.window} for the leader cell; no cost decision, no holdout spend",
            "decision_window": args.window,
            "leader": {**LEADER, "qualifying_nights": qualifying_nights},
        }
        OUT_ROOT.mkdir(parents=True, exist_ok=True)
        out = OUT_ROOT / f"verify-{datetime.now():%Y%m%d-%H%M}.json"
        out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("\nVERDICT: DEFERRED (insufficient evidence, fail-closed)")
        print(f"report: {out}")
        return 2

    m = statistics.median([cost_of_width(r) for r in decision_rows])
    if m <= COST_GREEN:
        verdict, holdout_cost = "green", COST_GREEN
        note = "v2 economics stand; proceed to the v3 holdout at cost 0.015"
    elif m < COST_REPRICE_MAX:
        holdout_cost = math_ceil_step(m)
        verdict, note = "reprice", (
            f"reprice the holdout at cost {holdout_cost:.3f} (median {m:.4f} "
            f"rounded UP to the {REPRICE_STEP} grid), declared before opening"
        )
    else:
        verdict, holdout_cost = "no_trade", None
        note = "permanent close of the overnight put-credit line; no holdout is spent"

    report = {
        "status": verdict,
        "decision_window": args.window,
        "qualifying_nights": qualifying_nights,
        "leader_median_cost_w": round(m, 6),
        "holdout_cost_if_spent": holdout_cost,
        "note": note,
        "per_cell_median": {
            f"w{w:g}_o{o:g}": round(statistics.median(values), 6)
            for (w, o), values in sorted(by_cell.items())
        },
        "rule": {"min_nights": MIN_NIGHTS, "green": COST_GREEN,
                 "reprice_max": COST_REPRICE_MAX, "reprice_step": REPRICE_STEP},
    }
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    out = OUT_ROOT / f"verify-{datetime.now():%Y%m%d-%H%M}.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\nVERDICT: {verdict.upper()} (leader median {m:.4f} of width)")
    print(note)
    print(f"report: {out}")
    return 0


def math_ceil_step(value: float) -> float:
    """Round UP to the next multiple of REPRICE_STEP on the 0.005 grid."""
    import math

    return math.ceil(value / REPRICE_STEP - 1e-12) * REPRICE_STEP


if __name__ == "__main__":
    raise SystemExit(main())
