"""Freeze several 0DTE put-credit geometries from one pass over the tape.

The spy-odte-credit promotion sampled exactly one geometry — width 5, short
strike 5 points OTM. The overnight-spx program found geometry decisive there
(width 50 or offset 30 destroyed its premium), so the promoted cell is either
the peak of a surface or a lucky point on it, and the campaign cannot tell
which.

This builds a cross through that point: width fixed at 5 while the offset
moves, and offset fixed at 5 while the width moves. Five geometries, and
because they share strikes the whole cross costs about five option requests
per session instead of ten.

Each geometry is written as its own snapshot in the bars_universe row shape,
so the frozen validator and its gates evaluate every one of them unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ET = ZoneInfo("America/New_York")

# (width, offset) — a cross through the promoted w5/o5 cell.
GEOMETRIES: tuple[tuple[float, float], ...] = (
    (5.0, 2.0), (5.0, 5.0), (5.0, 10.0),   # offset varies
    (2.0, 5.0), (10.0, 5.0),               # width varies
)


def occ_symbol(underlying: str, expiry_date, strike: float) -> str:
    return f"{underlying}{expiry_date:%y%m%d}P{int(round(strike * 1000)):08d}"


def _index(bars: Sequence[Any]) -> dict[datetime, float]:
    return {bar.timestamp_utc.replace(minute=0, second=0, microsecond=0): bar.close
            for bar in bars}


def _at(index: dict[datetime, float], moment: datetime, back_hours: int = 3) -> float | None:
    probe = moment.replace(minute=0, second=0, microsecond=0)
    for _ in range(back_hours + 1):
        if probe in index:
            return index[probe]
        probe -= timedelta(hours=1)
    return None


def build(*, underlying: str, start: str, end: str, out_root: Path,
          entry_hour_et: int = 10, exit_hour_et: int = 15) -> dict[str, Any]:
    try:
        from dotenv import load_dotenv

        load_dotenv(REPOSITORY_ROOT / ".env")
    except ImportError:
        pass
    from research.providers.alpaca import AlpacaHistoricalProvider
    from research.providers.contracts import CapabilityProbeError

    provider = AlpacaHistoricalProvider.from_env()
    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=UTC)
    end_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=UTC)

    spot_bars: list[Any] = []
    cursor = start_dt
    while cursor < end_dt:
        chunk_end = min(cursor + timedelta(days=90), end_dt)
        spot_bars.extend(provider.bars(asset_class="equity", symbols=(underlying,),
                                       start_utc=cursor, end_utc=chunk_end,
                                       timeframe="1Hour").bars)
        cursor = chunk_end
    spot = _index(spot_bars)
    if not spot:
        raise ValueError(f"no {underlying} bars for {start}..{end}")

    rows_by_geometry: dict[tuple[float, float], list[dict[str, Any]]] = {g: [] for g in GEOMETRIES}
    skipped = 0
    for day in sorted({ts.astimezone(ET).date() for ts in spot}):
        if day.weekday() > 4:
            continue
        entry_utc = datetime.combine(day, datetime.min.time(), ET).replace(
            hour=entry_hour_et).astimezone(UTC)
        exit_utc = datetime.combine(day, datetime.min.time(), ET).replace(
            hour=exit_hour_et).astimezone(UTC)
        spot_in, spot_out = _at(spot, entry_utc, 1), _at(spot, exit_utc, 1)
        if spot_in is None or spot_out is None:
            skipped += 1
            continue

        # Union of the strikes this cross needs, fetched once per session.
        needed: set[float] = set()
        for width, offset in GEOMETRIES:
            short_k = float(round(spot_in - offset))
            needed.add(short_k)
            needed.add(short_k - width)
        prices: dict[float, float | None] = {}
        for strike in sorted(needed):
            try:
                snap = provider.bars(asset_class="option",
                                     symbols=(occ_symbol(underlying, day, strike),),
                                     start_utc=entry_utc - timedelta(hours=2),
                                     end_utc=entry_utc + timedelta(hours=2),
                                     timeframe="1Hour")
                prices[strike] = _at(_index(snap.bars), entry_utc, 2)
            except CapabilityProbeError:
                prices[strike] = None

        for width, offset in GEOMETRIES:
            short_k = float(round(spot_in - offset))
            long_k = short_k - width
            short_px, long_px = prices.get(short_k), prices.get(long_k)
            if short_px is None or long_px is None:
                continue
            credit = short_px - long_px
            if not 0.0 < credit < width:
                continue
            intrinsic = max(0.0, short_k - spot_out) - max(0.0, long_k - spot_out)
            rows_by_geometry[(width, offset)].append({
                "session": day.isoformat(),
                "symbol": f"{underlying}-0DTE-PCS-w{width:g}o{offset:g}",
                "features": {
                    "credit_frac": round(credit / width, 8),
                    "otm_pct": round(offset / spot_in, 8),
                    "day_of_week": float(day.weekday()),
                },
                "next_open_to_close": round((credit - intrinsic) / width, 8),
                "next_close_to_close": round((credit - intrinsic) / width, 8),
                # Analysis-only field (not a feature, candidates never see it):
                # the underlying's own entry->exit move, so drift share can be
                # decomposed per geometry. The peer's audit of the w5/o5
                # campaign had to rebuild the snapshot to compute this.
                "spy_session_ret": round(spot_out / spot_in - 1.0, 8),
            })

    manifests: dict[str, Any] = {"skipped_sessions": skipped, "geometries": {}}
    for (width, offset), rows in rows_by_geometry.items():
        if len(rows) < 100:
            manifests["geometries"][f"w{width:g}o{offset:g}"] = {"rows": len(rows), "usable": False}
            continue
        out = out_root / f"w{width:g}o{offset:g}"
        out.mkdir(parents=True, exist_ok=True)
        payload = ("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n").encode("utf-8")
        (out / "sessions.jsonl").write_bytes(payload)
        days = [r["session"] for r in rows]
        manifest = {
            "provider": "alpaca", "underlying": underlying, "structure": "put_credit_spread",
            "width": width, "offset": offset, "entry_hour_et": entry_hour_et,
            "pricing": "actual traded option bars; exit at intrinsic (0DTE)",
            "row_count": len(rows), "first_session": days[0], "last_session": days[-1],
            "data_sha256": hashlib.sha256(payload).hexdigest(),
            "suggested_splits": _splits(days),
        }
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                           encoding="utf-8")
        manifests["geometries"][f"w{width:g}o{offset:g}"] = {"rows": len(rows), "usable": True}
    return manifests


def _splits(days: list[str]) -> dict[str, list[str]]:
    discovery_end = days[int(len(days) * 0.6) - 1]
    validation_end = days[int(len(days) * 0.8) - 1]
    later = [d for d in days if d > discovery_end]
    rest = [d for d in later if d > validation_end]
    return {"discovery": [days[0], discovery_end],
            "validation": [later[0], validation_end],
            "holdout": [rest[0], days[-1]]}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--underlying", default="SPY")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(build(underlying=args.underlying, start=args.start,
                           end=args.end, out_root=args.out_root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
