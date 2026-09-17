"""Replication (third tape): the near-money 0DTE put credit on IWM.

Same named cell as the QQQ replication, stated before any IWM data was read;
small-cap tape, different vol surface, same bar. Width floors at $2 because
IWM strikes are $1 and 0.64% of ~$240 rounds below it.

The SPY geometry cross left one cell a hair short of its full preregistered
bar: w5/o2 — short strike ~0.26% OTM, width ~0.64% of spot — which failed
only the discovery raw bootstrap LB (-0.000135) while passing both validator
splits, both validation bootstraps, and 1.0% cost out of sample at 6.2%
drift share. Re-running the same tape at neighbouring offsets would be
multiplicity; the declared test is the SAME structure, in percentage terms,
on an underlying the campaign has never touched.

THE ONE CELL, named before any QQQ data is read:

    QQQ 0DTE put credit spread, entered 10:00 ET, held to expiry.
    Short strike:  nearest $1 strike to 0.26% below spot at entry.
    Width:         nearest $1 to 0.64% of spot (min $2).
    Unconditional. No grid, no thresholds, no alternatives.

Acceptance bar, identical to the geometry sweep and declared here again:
every shipped validator gate on discovery AND validation, and a POSITIVE
weekly bootstrap lower bound on the raw payoff AND on the drift-neutral
residual, on both splits, at 0.5%-of-width round-trip cost. All eight
conditions or nothing. The QQQ holdout split is never touched.

Pricing follows the SPY snapshots: actual traded option bars, exit at
intrinsic because a 0DTE settles mechanically.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPOSITORY_ROOT / ".research" / "sweeps" / "iwm-odte-replication-v1"
SNAPSHOT_DIR = REPOSITORY_ROOT / ".research" / "data" / "odte" / "iwm-nearmoney"
ET = ZoneInfo("America/New_York")
OFFSET_PCT = 0.0026
WIDTH_PCT = 0.0064
COST_LEVELS = {"0.5pct": 50.0, "1.0pct": 100.0}

from research.experiments.prepare_odte_geometry import _at, _index, occ_symbol  # noqa: E402
from research.experiments.sweep_odte_geometry import drift_decompose, weekly_bootstrap_lb  # noqa: E402


def build_snapshot(start: str, end: str) -> dict:
    try:
        from dotenv import load_dotenv

        load_dotenv(REPOSITORY_ROOT / ".env")
    except ImportError:
        pass
    from research.providers.alpaca import AlpacaHistoricalProvider
    from research.providers.contracts import CapabilityProbeError

    manifest_path = SNAPSHOT_DIR / "manifest.json"
    if manifest_path.is_file():
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    provider = AlpacaHistoricalProvider.from_env()
    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=UTC)
    end_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=UTC)
    spot_bars = []
    cursor = start_dt
    while cursor < end_dt:
        chunk = min(cursor + timedelta(days=90), end_dt)
        spot_bars.extend(provider.bars(asset_class="equity", symbols=("IWM",),
                                       start_utc=cursor, end_utc=chunk,
                                       timeframe="1Hour").bars)
        cursor = chunk
    spot = _index(spot_bars)

    rows = []
    skipped = 0
    for day in sorted({ts.astimezone(ET).date() for ts in spot}):
        if day.weekday() > 4:
            continue
        entry_utc = datetime.combine(day, datetime.min.time(), ET).replace(hour=10).astimezone(UTC)
        exit_utc = datetime.combine(day, datetime.min.time(), ET).replace(hour=15).astimezone(UTC)
        spot_in, spot_out = _at(spot, entry_utc, 1), _at(spot, exit_utc, 1)
        if spot_in is None or spot_out is None:
            skipped += 1
            continue
        offset = max(1.0, round(spot_in * OFFSET_PCT))
        width = max(2.0, round(spot_in * WIDTH_PCT))
        short_k = float(round(spot_in - offset))
        long_k = short_k - width
        prices = {}
        for strike in (short_k, long_k):
            try:
                snap = provider.bars(asset_class="option",
                                     symbols=(occ_symbol("IWM", day, strike),),
                                     start_utc=entry_utc - timedelta(hours=2),
                                     end_utc=entry_utc + timedelta(hours=2),
                                     timeframe="1Hour")
                prices[strike] = _at(_index(snap.bars), entry_utc, 2)
            except CapabilityProbeError:
                prices[strike] = None
        short_px, long_px = prices.get(short_k), prices.get(long_k)
        if short_px is None or long_px is None:
            skipped += 1
            continue
        credit = short_px - long_px
        if not 0.0 < credit < width:
            skipped += 1
            continue
        intrinsic = max(0.0, short_k - spot_out) - max(0.0, long_k - spot_out)
        rows.append({
            "session": day.isoformat(),
            "symbol": "IWM-0DTE-PCS-NM",
            "features": {"credit_frac": round(credit / width, 8),
                         "day_of_week": float(day.weekday())},
            "next_open_to_close": round((credit - intrinsic) / width, 8),
            "next_close_to_close": round((credit - intrinsic) / width, 8),
            "spy_session_ret": round(spot_out / spot_in - 1.0, 8),
        })

    if len(rows) < 200:
        raise ValueError(f"only {len(rows)} usable sessions (skipped {skipped})")
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    payload = ("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n").encode("utf-8")
    (SNAPSHOT_DIR / "sessions.jsonl").write_bytes(payload)
    days = [r["session"] for r in rows]
    d_end = days[int(len(days) * 0.6) - 1]
    v_end = days[int(len(days) * 0.8) - 1]
    later = [d for d in days if d > d_end]
    rest = [d for d in later if d > v_end]
    manifest = {
        "underlying": "IWM", "structure": "put_credit_spread near-money (pct-defined)",
        "offset_pct": OFFSET_PCT, "width_pct": WIDTH_PCT,
        "pricing": "actual traded option bars; exit at intrinsic (0DTE)",
        "row_count": len(rows), "skipped": skipped,
        "first_session": days[0], "last_session": days[-1],
        "data_sha256": hashlib.sha256(payload).hexdigest(),
        "suggested_splits": {"discovery": [days[0], d_end],
                             "validation": [later[0], v_end],
                             "holdout": [rest[0], days[-1]]},
    }
    (SNAPSHOT_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    manifest = build_snapshot("2024-01-01", "2026-08-11")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"QQQ snapshot: {manifest['row_count']} sessions "
          f"{manifest['first_session']} -> {manifest['last_session']} "
          f"(skipped {manifest['skipped']})")
    splits = manifest["suggested_splits"]
    print(f"splits: {json.dumps(splits)}\n")

    candidate = OUT_DIR / "c_base.py"
    candidate.write_text(textwrap.dedent("""
        LABEL = "unconditional near-money credit spread"

        def signal(symbol, features):
            return 1
        """).strip() + "\n", encoding="utf-8")
    rows = [json.loads(line) for line in
            (SNAPSHOT_DIR / "sessions.jsonl").read_text(encoding="utf-8").splitlines()]

    results: dict[str, dict] = {}
    for label, cost_bps in COST_LEVELS.items():
        spec_payload = {
            "name": f"iwm-odte-replication-{label}",
            "universe": ["IWM-0DTE-PCS-NM"],
            "splits": splits,
            "min_trades": {"discovery": 150, "validation": 60, "holdout": 60},
            "min_sessions": {"discovery": 150, "validation": 60, "holdout": 60},
            "min_symbols": 1, "max_concentration": 1.0,
            "cost_bps": cost_bps, "target_sharpe": 0.75,
            "payoff": "next_open_to_close",
        }
        spec_path = OUT_DIR / f"spec-{label}.json"
        raw = (json.dumps(spec_payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
        spec_path.write_bytes(raw)
        spec_hash = hashlib.sha256(raw).hexdigest()
        for split in ("discovery", "validation"):
            completed = subprocess.run(
                [sys.executable, "-m", "research.validators.bars_universe",
                 "--candidate", str(candidate), "--spec", str(spec_path),
                 "--spec-hash", spec_hash,
                 "--data", str(SNAPSHOT_DIR / "sessions.jsonl"),
                 "--data-hash", manifest["data_sha256"], "--split", split],
                cwd=REPOSITORY_ROOT, text=True, capture_output=True, timeout=300)
            line = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else "{}"
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                payload = {"status": "unparseable"}
            decomp = drift_decompose(rows, splits[split][0], splits[split][1],
                                     cost_bps / 10_000.0)
            results[f"{label}/{split}"] = {"validator": payload.get("status"),
                                           "net_bps_w": payload.get("net_portfolio_mean_bps"),
                                           "sharpe": payload.get("net_sharpe"),
                                           "failed": [k for k, ok in
                                                      (payload.get("gates") or {}).items() if not ok],
                                           **decomp}
            r = results[f"{label}/{split}"]
            print(f"{label} {split:<11}: {r['validator']:<9} net={r['net_bps_w']} "
                  f"sharpe={r['sharpe']} drift_share={r.get('drift_share')} "
                  f"rawLB={r.get('raw_weekly_bootstrap_lb')} "
                  f"residLB={r.get('residual_weekly_bootstrap_lb')}")

    half = results["0.5pct/discovery"], results["0.5pct/validation"]
    passes = all(r["validator"] == "passed"
                 and (r.get("raw_weekly_bootstrap_lb") or -1) > 0
                 and (r.get("residual_weekly_bootstrap_lb") or -1) > 0 for r in half)
    summary = {
        "cell": "QQQ 0DTE put credit, short 0.26% OTM, width 0.64% of spot, "
                "10:00 ET entry, held to expiry, unconditional",
        "bar": "validator + positive raw and drift-neutral weekly bootstrap LBs, "
               "both splits, 0.5% cost — all eight or nothing",
        "snapshot": {k: manifest[k] for k in
                     ("row_count", "skipped", "first_session", "last_session", "data_sha256")},
        "splits": splits,
        "results": results,
        "clears_full_bar": passes,
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                                          encoding="utf-8")
    print(f"\nCLEARS FULL BAR: {passes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
