"""Designed verification: 0DTE credit-spread economics across markets and sides.

Walks every staged odte snapshot (.research/data/odte/**/sessions.jsonl) and
measures, per dataset: win rate, credit collected, per-trade ROI gross and net
of cost (percent of spread width), tail loss, daily Sharpe, and per-split
stability. Answers the trader question: which side x geometry x market clears
ROI > 10% of width per trade, and at what win rate / tail cost?

Measurement only — no candidate code, no orders, no new data. Writes
.research/verifications/<name>/results.json for the web evidence surface.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ODTE_ROOT = REPOSITORY_ROOT / ".research" / "data" / "odte"
VERIFY_ROOT = REPOSITORY_ROOT / ".research" / "verifications"

COST_BPS = 50.0  # matches the frozen sweep policy for these snapshots

SIDE_RE = re.compile(r"-0DTE-(PCS|CCS)")


def _load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _stats(rows: list[dict]) -> dict:
    payoffs = [float(row["next_open_to_close"]) for row in rows]
    cost = COST_BPS / 10_000.0
    net = [p - cost for p in payoffs]
    sharpe = 0.0
    if len(net) > 1:
        deviation = statistics.stdev(net)
        if deviation:
            sharpe = statistics.mean(net) / deviation * (252 ** 0.5)
    return {
        "trades": len(payoffs),
        "win_rate": round(sum(p > 0 for p in payoffs) / len(payoffs), 4),
        "gross_mean_pct_width": round(statistics.mean(payoffs) * 100, 3),
        "net_mean_pct_width": round(statistics.mean(net) * 100, 3),
        "p05_pct_width": round(sorted(payoffs)[max(0, int(0.05 * len(payoffs)) - 1)] * 100, 3),
        "worst_pct_width": round(min(payoffs) * 100, 3),
        "net_sharpe_daily": round(sharpe, 3),
    }


def _parse_label(symbol: str, fallback: str) -> tuple[str, str]:
    match = SIDE_RE.search(symbol)
    if match:
        return symbol[: match.start()], match.group(1)
    return fallback, "PCS" if "PCS" in symbol else "CCS" if "CCS" in symbol else "?"


def measure_dataset(directory: Path) -> dict:
    rows = _load_rows(directory / "sessions.jsonl")
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    symbol = rows[0]["symbol"]
    underlier, side = _parse_label(symbol, directory.name)
    credits = [float(row["features"]["credit_frac"]) for row in rows]
    result = {
        "dataset": directory.name,
        "underlier": underlier,
        "side": side,
        "credit_mean_pct_width": round(statistics.mean(credits) * 100, 3),
        "sessions": len(rows),
        "first_session": rows[0]["session"],
        "last_session": rows[-1]["session"],
        **_stats(rows),
    }
    splits = manifest.get("suggested_splits") or {}
    by_split = {}
    for split, window in splits.items():
        part = [row for row in rows if window[0] <= row["session"] <= window[1]]
        if part:
            by_split[split] = _stats(part)
    if by_split:
        result["splits"] = by_split
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="odte-credit-both-sides-v1")
    args = parser.parse_args()

    datasets = sorted(
        p for p in ODTE_ROOT.rglob("sessions.jsonl") if p.parent.name != "__pycache__"
    )
    rows = [measure_dataset(p.parent) for p in datasets]

    out_dir = VERIFY_ROOT / args.name
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(
        json.dumps({"rows": rows}, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(rows, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
