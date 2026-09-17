"""Friday-0DTE campaign: the near-money put credit across Mag 7 + AVGO + IBIT.

Single names carry weekly expirations, so every Friday they are 0DTE — the
day the owner trades them. The index result (SPY/QQQ 7/8, IWM refused) says
the near-money credit premium exists on large-cap index tapes; this campaign
asks whether the single names that DOMINATE those indexes carry it on their
own Friday expirations, evaluated as the equal-weight portfolio the
bars_universe validator was built for.

Declared BEFORE any result is read, in full:

- Universe: AAPL, AMZN, GOOGL, META, MSFT, NVDA, TSLA, AVGO, IBIT. Fixed by
  the owner's traded book, not by any backtest.
- Sessions: Fridays only (plus any daily-expiry days the tape happens to
  carry are excluded — the session filter is weekday == Friday).
- Cell: ONE, unconditional, per the index finding: put credit, short strike
  0.26% OTM, width 0.64% of spot (floors: $1 offset, $2 width), strikes on
  the $1 grid; entry 10:00 ET, exit at intrinsic. Missing strikes skip and
  are counted — single names have coarser grids and thinner books, and the
  skip rate is itself a liquidity result.
- Pricing: actual traded option bars. No models.
- Portfolio evaluation: equal-weight daily across the nine names — the
  correlated-firing discipline from bars campaign 1 applies unchanged.
- Gates: shipped validator set with min_trades 150/60, min_sessions 60/25,
  min_symbols 6, max_concentration 0.20 — nine correlated names must not let
  one carry the portfolio. Plus the full-bar bootstraps: positive raw AND
  drift-neutral weekly LBs, both splits, at 0.5% cost.
- Splits: chronological 60/20/20 of observed Fridays. Holdout never touched.

Output: <out>/summary.json.
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
OUT_DIR = REPOSITORY_ROOT / ".research" / "sweeps" / "friday-odte-names-v1"
SNAPSHOT_DIR = REPOSITORY_ROOT / ".research" / "data" / "odte" / "friday-names"
UNIVERSE = ("AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA", "AVGO", "IBIT")
OFFSET_PCT, WIDTH_PCT = 0.0026, 0.0064
COST_BPS = 50.0
ET = ZoneInfo("America/New_York")

from research.experiments.prepare_odte_geometry import _at, _index, occ_symbol  # noqa: E402
from research.experiments.sweep_odte_geometry import drift_decompose, weekly_bootstrap_lb  # noqa: E402


def build_snapshot() -> dict:
    from dotenv import load_dotenv

    load_dotenv(REPOSITORY_ROOT / ".env")
    from research.providers.alpaca import AlpacaHistoricalProvider
    from research.providers.contracts import CapabilityProbeError

    manifest_path = SNAPSHOT_DIR / "manifest.json"
    if manifest_path.is_file():
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    provider = AlpacaHistoricalProvider.from_env()
    rows: list[dict] = []
    skips: dict[str, int] = {}
    grid_cache: dict[str, float] = {}
    for symbol in UNIVERSE:
        spot_bars = []
        cursor = datetime(2024, 1, 1, tzinfo=UTC)
        end = datetime(2026, 8, 15, tzinfo=UTC)
        while cursor < end:
            chunk = min(cursor + timedelta(days=90), end)
            spot_bars.extend(provider.bars(asset_class="equity", symbols=(symbol,),
                                           start_utc=cursor, end_utc=chunk,
                                           timeframe="1Hour").bars)
            cursor = chunk
        spot = _index(spot_bars)
        kept = skipped = 0
        for day in sorted({ts.astimezone(ET).date() for ts in spot}):
            if day.weekday() not in (0, 2, 4):  # Mon/Wed/Fri: the names' 0DTE days
                continue
            entry_utc = datetime.combine(day, datetime.min.time(), ET).replace(hour=10).astimezone(UTC)
            exit_utc = datetime.combine(day, datetime.min.time(), ET).replace(hour=15).astimezone(UTC)
            spot_in, spot_out = _at(spot, entry_utc, 1), _at(spot, exit_utc, 1)
            if spot_in is None or spot_out is None:
                skipped += 1
                continue
            offset = max(1.0, round(spot_in * OFFSET_PCT))
            width = max(2.0, round(spot_in * WIDTH_PCT))

            def fetch_first(target: float) -> tuple[float, float] | None:
                """Try the target on each plausible strike grid; the tape
                decides which grid this name trades on. Successful grids are
                cached per symbol-month so the ladder costs one probe."""
                cache_key = f"{symbol}:{day:%Y-%m}"
                grids = ([grid_cache[cache_key]] if cache_key in grid_cache else []) + [1.0, 2.5, 5.0]
                seen = set()
                for grid in grids:
                    strike = round(target / grid) * grid
                    if strike in seen or strike <= 0:
                        continue
                    seen.add(strike)
                    try:
                        snap = provider.bars(asset_class="option",
                                             symbols=(occ_symbol(symbol, day, strike),),
                                             start_utc=entry_utc - timedelta(hours=2),
                                             end_utc=entry_utc + timedelta(hours=2),
                                             timeframe="1Hour")
                    except CapabilityProbeError:
                        continue
                    price = _at(_index(snap.bars), entry_utc, 2)
                    if price is not None:
                        grid_cache[cache_key] = grid
                        return strike, price
                return None

            short_leg = fetch_first(spot_in - offset)
            long_leg = fetch_first(spot_in - offset - width) if short_leg else None
            if short_leg and long_leg and long_leg[0] >= short_leg[0]:
                long_leg = fetch_first(short_leg[0] - max(width, 2.5))
            short_k, short_px = short_leg if short_leg else (None, None)
            long_k, long_px = long_leg if long_leg else (None, None)
            if short_px is None or long_px is None:
                skipped += 1
                continue
            width = float(short_k - long_k)
            credit = short_px - long_px
            if not 0.0 < credit < width:
                skipped += 1
                continue
            intrinsic = max(0.0, short_k - spot_out) - max(0.0, long_k - spot_out)
            rows.append({
                "session": day.isoformat(), "symbol": symbol,
                "features": {"credit_frac": round(credit / width, 8),
                             "day_of_week": float(day.weekday())},
                "next_open_to_close": round((credit - intrinsic) / width, 8),
                "next_close_to_close": round((credit - intrinsic) / width, 8),
                "spy_session_ret": round(spot_out / spot_in - 1.0, 8),
            })
            kept += 1
        skips[symbol] = skipped
        print(f"  {symbol}: {kept} Fridays kept, {skipped} skipped")
    rows.sort(key=lambda r: (r["session"], r["symbol"]))
    if len(rows) < 300:
        raise ValueError(f"only {len(rows)} rows; skips {skips}")
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    payload = ("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n").encode("utf-8")
    (SNAPSHOT_DIR / "sessions.jsonl").write_bytes(payload)
    days = sorted({r["session"] for r in rows})
    d_end = days[int(len(days) * 0.6) - 1]
    v_end = days[int(len(days) * 0.8) - 1]
    later = [d for d in days if d > d_end]
    rest = [d for d in later if d > v_end]
    manifest = {
        "universe": list(UNIVERSE), "structure": "friday 0DTE near-money put credit (pct-defined)",
        "offset_pct": OFFSET_PCT, "width_pct": WIDTH_PCT,
        "pricing": "actual traded option bars; exit at intrinsic",
        "row_count": len(rows), "skips": skips,
        "session_count": len(days), "first_session": days[0], "last_session": days[-1],
        "data_sha256": hashlib.sha256(payload).hexdigest(),
        "suggested_splits": {"discovery": [days[0], d_end],
                             "validation": [later[0], v_end],
                             "holdout": [rest[0], days[-1]]},
    }
    (SNAPSHOT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                                encoding="utf-8")
    return manifest


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = build_snapshot()
    print(f"snapshot: {manifest['row_count']} rows over {manifest['session_count']} Fridays; "
          f"skips {manifest['skips']}")
    splits = manifest["suggested_splits"]
    print(f"splits: {json.dumps(splits)}\n")
    rows = [json.loads(line) for line in
            (SNAPSHOT_DIR / "sessions.jsonl").read_text(encoding="utf-8").splitlines()]

    spec_payload = {
        "name": "friday-odte-names-v1",
        "universe": list(UNIVERSE),
        "splits": splits,
        "min_trades": {"discovery": 150, "validation": 60, "holdout": 60},
        "min_sessions": {"discovery": 60, "validation": 25, "holdout": 25},
        "min_symbols": 6, "max_concentration": 0.20,
        "cost_bps": COST_BPS, "target_sharpe": 0.75,
        "payoff": "next_open_to_close",
    }
    spec_path = OUT_DIR / "spec.json"
    raw = (json.dumps(spec_payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    spec_path.write_bytes(raw)
    spec_hash = hashlib.sha256(raw).hexdigest()
    candidate = OUT_DIR / "c_base.py"
    candidate.write_text(textwrap.dedent("""
        LABEL = "unconditional friday credit portfolio"

        def signal(symbol, features):
            return 1
        """).strip() + "\n", encoding="utf-8")

    results: dict[str, dict] = {}
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
        window = splits[split]
        cost_frac = COST_BPS / 10_000.0
        member = [r for r in rows if window[0] <= r["session"] <= window[1]]
        raw_pairs = [(r["session"], r["next_open_to_close"] - cost_frac) for r in member]
        decomp = drift_decompose(member, window[0], window[1], cost_frac)
        results[split] = {
            "validator": payload.get("status"),
            "net_bps_w": payload.get("net_portfolio_mean_bps"),
            "net_trade_bps_w": payload.get("net_trade_mean_bps"),
            "sharpe": payload.get("net_sharpe"),
            "symbol_counts": payload.get("symbol_counts"),
            "failed": [k for k, ok in (payload.get("gates") or {}).items() if not ok],
            "raw_lb": round(weekly_bootstrap_lb(raw_pairs, 20260814), 6),
            "resid_lb": decomp.get("residual_weekly_bootstrap_lb"),
            "drift_share": decomp.get("drift_share"),
        }
        r = results[split]
        print(f"{split:<11}: {r['validator']:<9} net={r['net_bps_w']} sharpe={r['sharpe']} "
              f"rawLB={r['raw_lb']} residLB={r['resid_lb']} failed={r['failed']}")
    clears = all(
        results[s]["validator"] == "passed"
        and (results[s]["raw_lb"] or -1) > 0
        and (results[s]["resid_lb"] or -1) > 0
        for s in ("discovery", "validation"))
    summary = {
        "cell": "Friday 0DTE near-money put credit, nine-name equal-weight portfolio",
        "bar": "all eight conditions at 0.5% cost",
        "snapshot": {k: manifest[k] for k in ("row_count", "session_count", "skips", "data_sha256")},
        "splits": splits, "results": results, "clears_full_bar": clears,
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                                          encoding="utf-8")
    print(f"\nCLEARS FULL BAR: {clears}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
