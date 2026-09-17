"""Exhaustive single-feature threshold sweep over the widened bars universe.

WHY THIS LANE EXISTS. The LLM proposer has gone 0-for-27 on canonical runs
while designed grid campaigns are 2-for-2. A proposer samples the space; a
grid COVERS it. With the feature set widened from 11 to 44 (2026-08-16) a
grid finally has something to cover, and "no single feature separates the
next session at any threshold" is itself a publishable negative — one the
sampling loop can never establish, because it can never prove it looked.

This is a PREREGISTERED campaign, not a hand-tuned strategy. Everything
below is declared before a single result is read:

- Signal shape, frozen: one feature, one threshold, one direction. Long when
  ``features[name] > threshold`` (or ``<`` for the short side). No
  conjunctions, no per-cell tuning, no second pass.
- Grid: every feature in the data contract x the declared quantile ladder x
  both directions. Thresholds are the feature's own quantiles ON THE
  DISCOVERY SPLIT ONLY, so the cut points never see validation data.
- Evaluator: research.validators.bars_universe, unmodified, one subprocess
  per cell. This script computes no score of its own — it only enumerates,
  dispatches, and records.
- Promotion rule: the top SHORTLIST cells by discovery score that pass every
  discovery gate get exactly ONE validation shot each. A cell is promoted
  only if it passes every validation gate too. The holdout is never touched.
- Every cell is recorded, including failures and gate-blocked cells. A
  best-of-N result that hides its N is not evidence.

Multiple-comparisons honesty: a grid this wide will produce a good-looking
discovery score by chance alone, which is exactly why discovery ranking
buys nothing but a validation attempt, and why the count of cells tried is
reported beside every survivor.

Output: <out>/discovery.jsonl, <out>/validation.jsonl, <out>/summary.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

from research.experiments.bars_feature_spec import FEATURE_NAMES

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

# Declared BEFORE any result: the quantile ladder the thresholds come from,
# how many cells earn a validation shot, and the directions tested.
QUANTILES: tuple[float, ...] = (0.1, 0.25, 0.5, 0.75, 0.9)
DIRECTIONS: tuple[str, ...] = ("above", "below")
SHORTLIST = 5

# The validator's contract is a POSITION (-1, 0, 1), not a boolean — the
# first sweep run returned True/False from every cell and all 390 failed
# with "signal must return exactly -1, 0, or 1". Zero-of-N is an error, not
# a finding. Each cell is therefore long-when-true; the "below" direction
# covers the mirror image, so shorts need no separate template.
CANDIDATE_TEMPLATE = """\
LABEL = {label!r}


def signal(symbol, features):
    value = features[{name!r}]
    return 1 if value {operator} {threshold!r} else 0
"""


def _load_rows(data_path: Path) -> list[dict[str, Any]]:
    rows = []
    with data_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _discovery_quantiles(
    rows: Sequence[dict[str, Any]], window: Sequence[str], feature: str,
) -> list[float]:
    """Threshold candidates from the DISCOVERY split only.

    Taking cut points from the whole file would let a validation-era outlier
    choose the threshold that is then 'confirmed' on validation — leakage
    through the grid rather than through the candidate.
    """
    start, end = window
    values = sorted(
        row["features"][feature]
        for row in rows
        if start <= row["session"] <= end and feature in row["features"]
    )
    if len(values) < 20:
        return []
    cuts = []
    for q in QUANTILES:
        cut = values[min(int(q * len(values)), len(values) - 1)]
        if cut not in cuts:
            cuts.append(round(cut, 8))
    return cuts


def run_cell(
    candidate_path: Path, spec: Path, data: Path, split: str,
) -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable, "-m", "research.validators.bars_universe",
            "--candidate", str(candidate_path), "--spec", str(spec),
            "--data", str(data), "--split", split,
        ],
        capture_output=True, text=True, cwd=REPOSITORY_ROOT,
    )
    for line in completed.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {"status": "failed", "error": (completed.stderr or "no evaluator output")[:300]}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--features", default="", help="comma-separated subset; default is the whole contract",
    )
    args = parser.parse_args(argv)

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    rows = _load_rows(args.data)
    discovery_window = spec["splits"]["discovery"]
    features = (
        tuple(name.strip() for name in args.features.split(",") if name.strip())
        or FEATURE_NAMES
    )

    args.out.mkdir(parents=True, exist_ok=True)
    work = args.out / "cells"
    work.mkdir(exist_ok=True)

    # ENUMERATE THE WHOLE GRID FIRST, and write it down before running any of
    # it. A grid decided while results arrive is not a preregistered grid.
    grid: list[dict[str, Any]] = []
    for name in features:
        for threshold in _discovery_quantiles(rows, discovery_window, name):
            for direction in DIRECTIONS:
                grid.append({
                    "feature": name,
                    "threshold": threshold,
                    "direction": direction,
                    "label": f"{name} {'>' if direction == 'above' else '<'} {threshold:g}",
                })
    (args.out / "grid.json").write_text(
        json.dumps({"cells": len(grid), "quantiles": list(QUANTILES),
                    "directions": list(DIRECTIONS), "shortlist": SHORTLIST,
                    "grid": grid}, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"declared grid: {len(grid)} cells over {len(features)} features", flush=True)

    discovery: list[dict[str, Any]] = []
    for index, cell in enumerate(grid, start=1):
        path = work / f"cell_{index:04d}.py"
        path.write_text(
            CANDIDATE_TEMPLATE.format(
                label=cell["label"], name=cell["feature"],
                operator=">" if cell["direction"] == "above" else "<",
                threshold=cell["threshold"],
            ),
            encoding="utf-8",
        )
        result = run_cell(path, args.spec, args.data, "discovery")
        discovery.append({**cell, "result": result})
        if index % 25 == 0 or index == len(grid):
            print(f"  discovery {index}/{len(grid)}", flush=True)
    (args.out / "discovery.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in discovery), encoding="utf-8",
    )

    def evaluated_ok(result: dict[str, Any]) -> bool:
        # passed/rejected both mean THE EVALUATION RAN; only "failed" means it
        # did not. The first version treated everything but a nonexistent
        # status "ok" as unevaluated and reported a campaign error over 390
        # perfectly good evaluations.
        return result.get("status") in ("passed", "rejected")

    def passed(entry: dict[str, Any]) -> bool:
        result = entry["result"]
        return bool(result.get("status") == "passed" and all((result.get("gates") or {}).values()))

    gate_passers = [entry for entry in discovery if passed(entry)]
    gate_passers.sort(key=lambda e: e["result"].get("score", float("-inf")), reverse=True)
    shortlist = gate_passers[:SHORTLIST]

    validation: list[dict[str, Any]] = []
    for index, entry in enumerate(shortlist, start=1):
        path = work / f"validate_{index:02d}.py"
        path.write_text(
            CANDIDATE_TEMPLATE.format(
                label=entry["label"], name=entry["feature"],
                operator=">" if entry["direction"] == "above" else "<",
                threshold=entry["threshold"],
            ),
            encoding="utf-8",
        )
        validation.append({**entry, "validation": run_cell(path, args.spec, args.data, "validation")})
    (args.out / "validation.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in validation), encoding="utf-8",
    )

    promoted = [
        entry for entry in validation
        if entry["validation"].get("status") == "passed"
        and all((entry["validation"].get("gates") or {}).values())
    ]

    scores = [e["result"]["score"] for e in discovery if evaluated_ok(e["result"])]
    summary = {
        "campaign": "feature-threshold-sweep-v1",
        "question": (
            "Does any single feature in the data contract separate the next "
            "session's payoff at any declared threshold?"
        ),
        "grid_cells": len(grid),
        "features_tested": len(features),
        "evaluated": len(scores),
        "discovery_gate_passers": len(gate_passers),
        "shortlist": [
            {"label": e["label"], "score": e["result"].get("score"),
             "trades": e["result"].get("trades")}
            for e in shortlist
        ],
        "validation": [
            {"label": e["label"], "score": e["validation"].get("score"),
             "gates": e["validation"].get("gates")}
            for e in validation
        ],
        "promoted": [
            {"label": e["label"], "discovery_score": e["result"].get("score"),
             "validation_score": e["validation"].get("score")}
            for e in promoted
        ],
        "best_discovery_score": max(scores) if scores else None,
        "median_discovery_score": round(statistics.median(scores), 6) if scores else None,
        "holdout": "untouched",
    }
    # Zero-of-N evaluating is an ERROR, never a finding. The first run of this
    # campaign returned "no threshold survived" while every one of 390 cells
    # had failed on a template bug — a false negative claim that would have
    # gone on the record (and on the stream) as science.
    if scores:
        summary["outcome"] = (
            f"{len(promoted)} of {len(grid)} declared cells passed every discovery "
            f"and validation gate."
            if promoted else
            f"No single-feature threshold survived. {len(grid)} cells declared, "
            f"{len(gate_passers)} passed the discovery gates, none survived validation."
        )
    else:
        first_error = next(
            (str(e["result"].get("error", ""))[:120] for e in discovery
             if e["result"].get("status") != "ok"),
            "unknown",
        )
        summary["outcome"] = (
            f"CAMPAIGN ERROR — no scientific conclusion. All {len(grid)} cells "
            f"failed evaluation (first error: {first_error})."
        )
        summary["campaign_error"] = True
    (args.out / "summary.json").write_text(
        json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8",
    )
    print(summary["outcome"])
    print(f"wrote {args.out / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
