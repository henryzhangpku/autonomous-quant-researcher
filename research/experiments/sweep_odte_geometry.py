"""0DTE geometry cross: find where the credit premium actually peaks.

The w5/o5 campaign promoted, then the peer's audit showed the preregistered
bar it truly faces: the weekly bootstrap lower bound of its DRIFT-NEUTRAL
payoff is marginally negative on both splits. Mostly not drift (13-30%), but
not distinguishable from zero at the 5th percentile either. This sweep asks
whether some neighbouring geometry clears that bar rather than sitting on
the fence.

Declared BEFORE any result is read, in full:

- Geometries: the prepared cross through w5/o5 — offsets 2/5/10 at width 5,
  widths 2/10 at offset 5. Unconditional structure only; the original
  campaign already showed no conditional cell improved on it.
- Cost levels per geometry: 0.5% and 1.0% of width round trip (the bracket
  the original campaign identified). Doubled-cost gate stresses 2x each.
- Acceptance bar, per geometry, ALL required at the 0.5% level:
    1. every shipped bars_universe gate on discovery AND validation;
    2. weekly bootstrap lower bound (5th percentile, 2000 resamples of
       weekly means) POSITIVE on both splits, computed on the RAW payoff;
    3. the same bootstrap POSITIVE on both splits for the DRIFT-NEUTRAL
       residual: payoff minus its OLS projection on the underlying's own
       session move. This is the control the w5/o5 campaign lacked.
- Drift share (R^2-weighted component of mean) is reported for every
  geometry so a long-delta artifact is visible even where the bar fails.
- The holdout split of every geometry stays sealed regardless of outcome.

No shortlist ranking here: five preregistered cells, each judged against an
absolute bar. Multiple geometries may pass, or none.

Output: <out>/results.jsonl, <out>/summary.json.
"""

from __future__ import annotations

import hashlib
import json
import random
import statistics
import subprocess
import sys
import textwrap
from collections import defaultdict
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPOSITORY_ROOT / ".research" / "sweeps" / "spy-odte-geometry-v1"
DATA_ROOT = REPOSITORY_ROOT / ".research" / "data" / "odte" / "geometry"
COST_LEVELS = {"0.5pct": 50.0, "1.0pct": 100.0}
BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 20260814

BASELINE = """
LABEL = "unconditional credit spread"

def signal(symbol, features):
    return 1
"""


def weekly_bootstrap_lb(pairs: list[tuple[str, float]], seed: int) -> float:
    """5th percentile of the mean of resampled weekly means."""
    by_week: dict[str, list[float]] = defaultdict(list)
    for day, value in pairs:
        year, week, _ = __import__("datetime").date.fromisoformat(day).isocalendar()
        by_week[f"{year}-{week:02d}"].append(value)
    weekly = [statistics.mean(vals) for vals in by_week.values()]
    if len(weekly) < 8:
        return float("nan")
    rng = random.Random(seed)
    means = sorted(
        statistics.mean(rng.choices(weekly, k=len(weekly))) for _ in range(BOOTSTRAP_N)
    )
    return means[int(0.05 * BOOTSTRAP_N)]


def drift_decompose(rows: list[dict], start: str, end: str, cost_frac: float) -> dict:
    """OLS of net payoff on the underlying's own session move, plus bootstraps."""
    sel = [r for r in rows if start <= r["session"] <= end]
    if len(sel) < 30:
        return {"n": len(sel)}
    y = [r["next_open_to_close"] - cost_frac for r in sel]
    x = [r["spy_session_ret"] for r in sel]
    mx, my = statistics.mean(x), statistics.mean(y)
    varx = sum((a - mx) ** 2 for a in x)
    beta = sum((a - mx) * (b - my) for a, b in zip(x, y)) / varx if varx else 0.0
    drift = beta * mx
    resid_pairs = [(r["session"], (b - beta * a)) for r, a, b in zip(sel, x, y)]
    raw_pairs = [(r["session"], b) for r, b in zip(sel, y)]
    return {
        "n": len(sel),
        "net_mean_w": round(my, 6),
        "beta": round(beta, 4),
        "drift_component_w": round(drift, 6),
        "alpha_component_w": round(my - drift, 6),
        "drift_share": round(drift / my, 4) if my else None,
        "raw_weekly_bootstrap_lb": round(weekly_bootstrap_lb(raw_pairs, BOOTSTRAP_SEED), 6),
        "residual_weekly_bootstrap_lb": round(
            weekly_bootstrap_lb(resid_pairs, BOOTSTRAP_SEED + 1), 6),
    }


def write_spec(manifest: dict, label: str, cost_bps: float) -> tuple[Path, str]:
    spec = {
        "name": f"spy-odte-geom-{manifest['width']:g}x{manifest['offset']:g}-{label}",
        "universe": [f"SPY-0DTE-PCS-w{manifest['width']:g}o{manifest['offset']:g}"],
        "splits": manifest["suggested_splits"],
        "min_trades": {"discovery": 150, "validation": 60, "holdout": 60},
        "min_sessions": {"discovery": 150, "validation": 60, "holdout": 60},
        "min_symbols": 1, "max_concentration": 1.0,
        "cost_bps": cost_bps, "target_sharpe": 0.75,
        "payoff": "next_open_to_close",
    }
    path = OUT_DIR / f"spec-w{manifest['width']:g}o{manifest['offset']:g}-{label}.json"
    raw = (json.dumps(spec, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def run_validator(spec: tuple[Path, str], data: Path, data_hash: str, split: str) -> dict:
    candidate = OUT_DIR / "c_base.py"
    completed = subprocess.run(
        [sys.executable, "-m", "research.validators.bars_universe",
         "--candidate", str(candidate), "--spec", str(spec[0]), "--spec-hash", spec[1],
         "--data", str(data), "--data-hash", data_hash, "--split", split],
        cwd=REPOSITORY_ROOT, text=True, capture_output=True, timeout=300,
    )
    line = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else "{}"
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {"status": "unparseable", "raw": line[:300]}


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "c_base.py").write_text(textwrap.dedent(BASELINE).strip() + "\n", encoding="utf-8")

    results: list[dict] = []
    for geom_dir in sorted(DATA_ROOT.iterdir()):
        manifest_path = geom_dir / "manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        rows = [json.loads(line) for line in
                (geom_dir / "sessions.jsonl").read_text(encoding="utf-8").splitlines()]
        label_geom = f"w{manifest['width']:g}o{manifest['offset']:g}"
        splits = manifest["suggested_splits"]
        print(f"=== {label_geom}: {manifest['row_count']} sessions ===")

        entry: dict = {"geometry": label_geom, "width": manifest["width"],
                       "offset": manifest["offset"], "rows": manifest["row_count"]}
        for label, cost_bps in COST_LEVELS.items():
            spec = write_spec(manifest, label, cost_bps)
            cost_frac = cost_bps / 10_000.0
            gates = {}
            for split in ("discovery", "validation"):
                payload = run_validator(spec, geom_dir / "sessions.jsonl",
                                        manifest["data_sha256"], split)
                decomp = drift_decompose(rows, splits[split][0], splits[split][1], cost_frac)
                gates[split] = {"validator": payload.get("status"),
                                "net_bps_w": payload.get("net_portfolio_mean_bps"),
                                "sharpe": payload.get("net_sharpe"),
                                "failed": [k for k, ok in (payload.get("gates") or {}).items()
                                           if not ok],
                                **decomp}
                print(f"  {label} {split:<11}: {payload.get('status'):<9} "
                      f"net={payload.get('net_portfolio_mean_bps')} "
                      f"drift_share={decomp.get('drift_share')} "
                      f"rawLB={decomp.get('raw_weekly_bootstrap_lb')} "
                      f"residLB={decomp.get('residual_weekly_bootstrap_lb')}")
            entry[label] = gates
            if label == "0.5pct":
                passes = all(
                    gates[s]["validator"] == "passed"
                    and (gates[s].get("raw_weekly_bootstrap_lb") or -1) > 0
                    and (gates[s].get("residual_weekly_bootstrap_lb") or -1) > 0
                    for s in ("discovery", "validation")
                )
                entry["clears_full_bar_at_0.5pct"] = passes
        results.append(entry)
        print()

    with (OUT_DIR / "results.jsonl").open("w", encoding="utf-8") as handle:
        for row in results:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    passing = [r["geometry"] for r in results if r.get("clears_full_bar_at_0.5pct")]
    summary = {
        "bar": "validator pass + positive raw AND drift-neutral weekly bootstrap LB, "
               "both splits, at 0.5% cost",
        "geometries": {r["geometry"]: r.get("clears_full_bar_at_0.5pct") for r in results},
        "clears_full_bar": passing,
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                                          encoding="utf-8")
    print("clears the full bar at 0.5% cost: " + json.dumps(passing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
