"""RTH entry-hour surface for the SPY 0DTE near-money put credit.

Goal: an evidence-backed entry policy for the 0DTE trader's RTH AUTO slot.
The app's current book strategy enters at 9:55 because a book says so; the
near-money credit campaign entered at 10:00 because the snapshot did. The
overnight program's central discovery was that entry time is the axis that
decides whether its premium exists. This campaign asks that question for the
regular session.

Declared BEFORE any result is read, in full:

- Grid: entry hours 10:00-14:00 ET (hourly bars set the granularity) x
  offsets {2, 5} at width 5, SPY, exit at intrinsic. Ten cells. Later entries
  hold less time and collect less premium but see more of the day's
  information; where the trade stops clearing its bar is exactly the number
  the AUTO slot needs.
- Pricing: actual traded option bars, as in every campaign of this family.
- Bar per cell, absolute, no shortlist: every validator gate on discovery AND
  validation plus positive raw and drift-neutral weekly bootstrap LBs on both
  splits, at 0.5%-of-width cost. All eight or nothing, per cell.
- The w5/o2/10:00 cell is already known to sit at 7/8; it re-runs here
  unchanged as the anchor. Holdouts stay sealed.

Output: <out>/results.jsonl, <out>/summary.json.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPOSITORY_ROOT / ".research" / "sweeps" / "spy-odte-entry-hours-v1"
DATA_ROOT = REPOSITORY_ROOT / ".research" / "data" / "odte" / "entry-hours"
ENTRY_HOURS = (10, 11, 12, 13, 14)
OFFSETS = (2.0, 5.0)
WIDTH = 5.0
COST_BPS = 50.0

from research.experiments.sweep_odte_geometry import drift_decompose, weekly_bootstrap_lb  # noqa: E402


def ensure_snapshot(entry_hour: int, offset: float) -> tuple[Path, dict]:
    from research.experiments.prepare_odte_credit import build

    out = DATA_ROOT / f"e{entry_hour}o{offset:g}"
    manifest_path = out / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return out, _ensure_session_ret(out, manifest, entry_hour)
    manifest = build(underlying="SPY", start="2024-01-01", end="2026-08-11", out=out,
                     entry_hour_et=entry_hour, width=WIDTH, offset=offset)
    return out, manifest


def _ensure_session_ret(out: Path, manifest: dict, entry_hour: int) -> dict:
    """Backfill spy_session_ret on snapshots built before the field existed.

    The option legs are the expensive part of a snapshot; the underlying's own
    move is one cheap equity fetch. Enriching in place beats re-pulling half an
    hour of option tape per cell.
    """
    rows = [json.loads(line) for line in
            (out / "sessions.jsonl").read_text(encoding="utf-8").splitlines()]
    if rows and "spy_session_ret" in rows[0]:
        return manifest
    from datetime import UTC, datetime, timedelta
    from zoneinfo import ZoneInfo

    from research.experiments.prepare_odte_geometry import _at, _index

    ET = ZoneInfo("America/New_York")
    from research.providers.alpaca import AlpacaHistoricalProvider

    provider = AlpacaHistoricalProvider.from_env()
    spot_bars = []
    cursor = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2026, 8, 12, tzinfo=UTC)
    while cursor < end:
        chunk = min(cursor + timedelta(days=90), end)
        spot_bars.extend(provider.bars(asset_class="equity", symbols=("SPY",),
                                       start_utc=cursor, end_utc=chunk,
                                       timeframe="1Hour").bars)
        cursor = chunk
    spot = _index(spot_bars)
    exit_hour = manifest.get("exit_hour_et", 15)
    kept = []
    for row in rows:
        day = datetime.strptime(row["session"], "%Y-%m-%d").date()
        entry_utc = datetime.combine(day, datetime.min.time(), ET).replace(
            hour=entry_hour).astimezone(UTC)
        exit_utc = datetime.combine(day, datetime.min.time(), ET).replace(
            hour=exit_hour).astimezone(UTC)
        spot_in, spot_out = _at(spot, entry_utc, 1), _at(spot, exit_utc, 1)
        if spot_in is None or spot_out is None:
            continue
        kept.append(dict(row, spy_session_ret=round(spot_out / spot_in - 1.0, 8)))
    payload = ("\n".join(json.dumps(r, sort_keys=True) for r in kept) + "\n").encode("utf-8")
    (out / "sessions.jsonl").write_bytes(payload)
    manifest = dict(manifest, row_count=len(kept),
                    data_sha256=hashlib.sha256(payload).hexdigest())
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                       encoding="utf-8")
    return manifest


def evaluate(cell: str, snap: Path, manifest: dict) -> dict:
    rows = [json.loads(line) for line in
            (snap / "sessions.jsonl").read_text(encoding="utf-8").splitlines()]
    splits = manifest["suggested_splits"]
    spec_payload = {
        "name": f"spy-odte-hours-{cell}",
        "universe": [rows[0]["symbol"]],
        "splits": splits,
        "min_trades": {"discovery": 150, "validation": 60, "holdout": 60},
        "min_sessions": {"discovery": 150, "validation": 60, "holdout": 60},
        "min_symbols": 1, "max_concentration": 1.0,
        "cost_bps": COST_BPS, "target_sharpe": 0.75,
        "payoff": "next_open_to_close",
    }
    spec_path = OUT_DIR / f"spec-{cell}.json"
    raw = (json.dumps(spec_payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    spec_path.write_bytes(raw)
    spec_hash = hashlib.sha256(raw).hexdigest()
    candidate = OUT_DIR / "c_base.py"
    if not candidate.exists():
        candidate.write_text(textwrap.dedent("""
            LABEL = "unconditional credit spread"

            def signal(symbol, features):
                return 1
            """).strip() + "\n", encoding="utf-8")

    out: dict = {"cell": cell, "rows": manifest["row_count"]}
    for split in ("discovery", "validation"):
        completed = subprocess.run(
            [sys.executable, "-m", "research.validators.bars_universe",
             "--candidate", str(candidate), "--spec", str(spec_path),
             "--spec-hash", spec_hash,
             "--data", str(snap / "sessions.jsonl"),
             "--data-hash", manifest["data_sha256"], "--split", split],
            cwd=REPOSITORY_ROOT, text=True, capture_output=True, timeout=300)
        line = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else "{}"
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            payload = {"status": "unparseable"}
        window = splits[split]
        cost_frac = COST_BPS / 10_000.0
        member = [r for r in rows if window[0] <= r["session"] <= window[1]]
        raw_pairs = [(r["session"], r["next_open_to_close"] - cost_frac) for r in member]
        decomp = drift_decompose(member, window[0], window[1], cost_frac)
        out[split] = {
            "validator": payload.get("status"),
            "net_bps_w": payload.get("net_portfolio_mean_bps"),
            "sharpe": payload.get("net_sharpe"),
            "failed": [k for k, ok in (payload.get("gates") or {}).items() if not ok],
            "raw_lb": round(weekly_bootstrap_lb(raw_pairs, 20260814), 6),
            "resid_lb": decomp.get("residual_weekly_bootstrap_lb"),
            "drift_share": decomp.get("drift_share"),
        }
    out["clears_full_bar"] = all(
        out[s]["validator"] == "passed"
        and (out[s]["raw_lb"] or -1) > 0
        and (out[s]["resid_lb"] or -1) > 0
        for s in ("discovery", "validation"))
    return out


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for entry_hour in ENTRY_HOURS:
        for offset in OFFSETS:
            cell = f"e{entry_hour}o{offset:g}"
            print(f"=== {cell} ===")
            try:
                snap, manifest = ensure_snapshot(entry_hour, offset)
            except Exception as exc:  # noqa: BLE001 - a cell without data is a result
                print(f"  snapshot failed: {exc}")
                results.append({"cell": cell, "error": str(exc)[:200]})
                continue
            out = evaluate(cell, snap, manifest)
            results.append(out)
            for split in ("discovery", "validation"):
                r = out[split]
                print(f"  {split:<11}: {r['validator']:<9} net={r['net_bps_w']} "
                      f"rawLB={r['raw_lb']} residLB={r['resid_lb']}")
            print(f"  CLEARS FULL BAR: {out['clears_full_bar']}")
    with (OUT_DIR / "results.jsonl").open("w", encoding="utf-8") as handle:
        for row in results:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    passing = [r["cell"] for r in results if r.get("clears_full_bar")]
    summary = {
        "structure": f"SPY 0DTE put credit, width {WIDTH:g}, exit at intrinsic",
        "grid": {"entry_hours_et": list(ENTRY_HOURS), "offsets": list(OFFSETS)},
        "bar": "all eight conditions per cell at 0.5% cost; no shortlist",
        "cells": {r["cell"]: r.get("clears_full_bar", False) for r in results},
        "clears_full_bar": passing,
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                                          encoding="utf-8")
    print("\nclears full bar: " + json.dumps(passing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
