"""Designed verification: strike-distance balance for SPY 0DTE put credit spreads.

Answers a trader question with the frozen odte/geometry snapshots: for each
staged width x offset geometry, what are the win rate, per-trade ROI (in
percent of spread width, gross and net of cost), tail loss, and per-split
stability? Measurement only — no candidate code, no orders, no new data.

Writes .research/verifications/<name>/results.json so the finding lands in
the web evidence surface alongside grid campaigns.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GEOMETRY_ROOT = REPOSITORY_ROOT / ".research" / "data" / "odte" / "geometry"
VERIFY_ROOT = REPOSITORY_ROOT / ".research" / "verifications"

COST_BPS = 50.0  # matches the frozen sweep policy for these snapshots


def _load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _window_stats(rows: list[dict]) -> dict:
    payoffs = [float(row["next_open_to_close"]) for row in rows]
    cost = COST_BPS / 10_000.0
    net = [p - cost for p in payoffs]
    return {
        "trades": len(payoffs),
        "win_rate": round(sum(p > 0 for p in payoffs) / len(payoffs), 4),
        "gross_mean_pct_width": round(statistics.mean(payoffs) * 100, 3),
        "net_mean_pct_width": round(statistics.mean(net) * 100, 3),
        "p05_pct_width": round(sorted(payoffs)[max(0, int(0.05 * len(payoffs)) - 1)] * 100, 3),
        "worst_pct_width": round(min(payoffs) * 100, 3),
    }


def measure_geometry(name: str, directory: Path) -> dict:
    rows = _load_rows(directory / "sessions.jsonl")
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    splits = manifest["suggested_splits"]
    by_split = {}
    for split, (start, end) in splits.items():
        window = [row for row in rows if start <= row["session"] <= end]
        if window:
            by_split[split] = _window_stats(window)
    overall = _window_stats(rows)
    daily = [overall["net_mean_pct_width"]]
    credits = [float(row["features"]["credit_frac"]) for row in rows]
    return {
        "geometry": name,
        "sessions": len(rows),
        "credit_mean_pct_width": round(statistics.mean(credits) * 100, 3),
        **overall,
        "net_sharpe_daily": round(
            statistics.mean([float(r["next_open_to_close"]) - COST_BPS / 10_000.0 for r in rows])
            / statistics.stdev([float(r["next_open_to_close"]) - COST_BPS / 10_000.0 for r in rows])
            * (252 ** 0.5), 3,
        ),
        "splits": by_split,
        "_daily_placeholder": daily,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="strike-distance-balance-v1")
    args = parser.parse_args()

    geometries = sorted(p.name for p in GEOMETRY_ROOT.iterdir() if (p / "sessions.jsonl").is_file())
    rows = [measure_geometry(name, GEOMETRY_ROOT / name) for name in geometries]

    out_dir = VERIFY_ROOT / args.name
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(
        json.dumps({"rows": rows}, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(rows, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
