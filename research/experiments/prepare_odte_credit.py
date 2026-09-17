"""Freeze 0DTE credit-spread economics from ACTUAL traded option bars.

The one structure this repo has ever promoted is an overnight SPX put credit
spread, and it rests on modeled Black-Scholes marks plus a single night's
cost capture. That assumption is the weakest link in the only real result we
have.

This snapshot removes the assumption for the RTH session. Alpaca serves
historical option bars, so for each expiry day the two legs of a defined-risk
credit spread can be priced from prices that actually traded: the credit
received at entry and the value at exit are both prints, not a model.

For each session: read the underlying at the entry minute, choose the short
strike a fixed distance out of the money (below spot for puts, above for
calls), the long strike a fixed width further out, and read both legs' bars.
The payoff is expressed as a fraction of width, the same unit the
overnight-spx program reports, so the two families are directly comparable.

Strikes may be dollar-defined (``--width``/``--offset``) or pct-defined
(``--width-pct``/``--offset-pct``, the qqq-nearmoney / friday-names
convention: offset = max($1, round(spot * offset_pct)), width = max($2,
round(spot * width_pct))). With ``--probe-grids`` the per-leg strike is the
first strike on the $1/$2.5/$5 grid that actually traded, mirroring the
friday-names single-name path.

Rows are emitted in the bars_universe shape — the frozen validator, its gate
set and its splits apply unchanged, and no evaluator is re-implemented.
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

SIDES = ("put", "call")


def occ_symbol(underlying: str, expiry: datetime, right: str, strike: float) -> str:
    """OCC 21-character option symbol, e.g. SPY260807P00630000."""
    return f"{underlying}{expiry:%y%m%d}{right}{int(round(strike * 1000)):08d}"


def _hour_index(bars: Sequence[Any]) -> dict[datetime, float]:
    return {bar.timestamp_utc.replace(minute=0, second=0, microsecond=0): bar.close
            for bar in bars}


def _at(index: dict[datetime, float], moment: datetime, back_hours: int = 3) -> float | None:
    probe = moment.replace(minute=0, second=0, microsecond=0)
    for _ in range(back_hours + 1):
        if probe in index:
            return index[probe]
        probe -= timedelta(hours=1)
    return None


def intrinsic_value(side: str, short_strike: float, long_strike: float, settle: float) -> float:
    """Spread value at expiry. Put: wins at/above the short strike; call: at/below."""
    if side == "put":
        return max(0.0, short_strike - settle) - max(0.0, long_strike - settle)
    return max(0.0, settle - short_strike) - max(0.0, settle - long_strike)


def _strikes(side: str, spot: float, width: float, offset: float, step: float) -> tuple[float, float]:
    if side == "put":
        short_strike = round((spot - offset) / step) * step
        return short_strike, short_strike - width
    short_strike = round((spot + offset) / step) * step
    return short_strike, short_strike + width


def _symbol_label(underlying: str, side: str, width: float, offset: float, pct: bool) -> str:
    tag = "PCS" if side == "put" else "CCS"
    if pct:
        return f"{underlying}-0DTE-{tag}-NM"
    if side == "call":
        return f"{underlying}-0DTE-{tag}-w{width:g}o{offset:g}"
    return f"{underlying}-0DTE-{tag}"


def build(*, underlying: str, start: str, end: str, out: Path,
          side: str = "put", entry_hour_et: int = 10, exit_hour_et: int = 15,
          width: float = 5.0, offset: float = 5.0,
          strike_step: float = 1.0,
          width_pct: float | None = None, offset_pct: float | None = None,
          probe_grids: bool = False,
          provider: Any | None = None,
          leg_cache: dict[str, dict[datetime, float]] | None = None,
          verbose: bool = False) -> dict[str, Any]:
    if side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}")
    pct = width_pct is not None or offset_pct is not None
    if pct and (width_pct is None or offset_pct is None):
        raise ValueError("pct-defined mode needs both width_pct and offset_pct")
    if provider is None:
        try:
            from dotenv import load_dotenv

            load_dotenv(REPOSITORY_ROOT / ".env")
        except ImportError:
            pass
        from research.providers.alpaca import AlpacaHistoricalProvider

        provider = AlpacaHistoricalProvider.from_env()
    from research.providers.contracts import CapabilityProbeError

    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=UTC)
    end_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=UTC)

    spot_bars: list[Any] = []
    cursor = start_dt
    while cursor < end_dt:
        chunk_end = min(cursor + timedelta(days=90), end_dt)
        snap = provider.bars(asset_class="equity", symbols=(underlying,),
                             start_utc=cursor, end_utc=chunk_end, timeframe="1Hour")
        spot_bars.extend(snap.bars)
        cursor = chunk_end
    spot = _hour_index(spot_bars)
    if not spot:
        raise ValueError(f"no {underlying} hourly bars for {start}..{end}")

    right = "P" if side == "put" else "C"
    sessions = sorted({ts.astimezone(ET).date() for ts in spot})
    rows: list[dict[str, Any]] = []
    skipped: dict[str, int] = {"no_spot": 0, "no_option_bars": 0, "degenerate": 0}
    grid_cache: dict[str, float] = {}

    def fetch_leg(symbol: str, entry_utc: datetime, exit_utc: datetime) -> dict[datetime, float]:
        # One request per leg: the provider's Bar carries no symbol, so a
        # combined two-symbol response cannot be attributed to its legs.
        if leg_cache is not None and symbol in leg_cache:
            return leg_cache[symbol]
        try:
            leg_snap = provider.bars(asset_class="option", symbols=(symbol,),
                                     start_utc=entry_utc - timedelta(hours=2),
                                     end_utc=exit_utc + timedelta(hours=2),
                                     timeframe="1Hour")
            index = _hour_index(leg_snap.bars)
        except CapabilityProbeError:
            index = {}
        if leg_cache is not None:
            leg_cache[symbol] = index
        return index

    for day in sessions:
        if day.weekday() > 4:
            continue
        if verbose and (len(rows) + sum(skipped.values())) % 25 == 0:
            print(f"  ... {underlying} {side} {day} rows={len(rows)} skipped={sum(skipped.values())}",
                  flush=True)
        entry_et = datetime.combine(day, datetime.min.time(), ET).replace(hour=entry_hour_et)
        exit_et = datetime.combine(day, datetime.min.time(), ET).replace(hour=exit_hour_et)
        entry_utc, exit_utc = entry_et.astimezone(UTC), exit_et.astimezone(UTC)
        underlying_entry = _at(spot, entry_utc, back_hours=1)
        underlying_exit = _at(spot, exit_utc, back_hours=1)
        if underlying_entry is None or underlying_exit is None:
            skipped["no_spot"] += 1
            continue

        day_offset = max(1.0, round(underlying_entry * offset_pct)) if pct else offset
        day_width = max(2.0, round(underlying_entry * width_pct)) if pct else width
        expiry = datetime.combine(day, datetime.min.time(), UTC)

        if probe_grids:
            # Mirror the friday-names path: the tape decides which strike
            # grid this name trades on; successful grids are cached per
            # calendar month so the ladder costs one probe.
            def fetch_first(target: float) -> tuple[float, float] | None:
                cache_key = f"{day:%Y-%m}"
                grids = ([grid_cache[cache_key]] if cache_key in grid_cache else []) + [strike_step, 2.5, 5.0]
                seen: set[float] = set()
                for grid in grids:
                    strike = round(target / grid) * grid
                    if strike in seen or strike <= 0:
                        continue
                    seen.add(strike)
                    price = _at(fetch_leg(occ_symbol(underlying, expiry, right, strike),
                                          entry_utc, exit_utc), entry_utc, 2)
                    if price is not None:
                        grid_cache[cache_key] = grid
                        return strike, price
                return None

            if side == "put":
                short_leg = fetch_first(underlying_entry - day_offset)
                long_leg = fetch_first(underlying_entry - day_offset - day_width) if short_leg else None
                if short_leg and long_leg and long_leg[0] >= short_leg[0]:
                    long_leg = fetch_first(short_leg[0] - max(day_width, 2.5))
            else:
                short_leg = fetch_first(underlying_entry + day_offset)
                long_leg = fetch_first(underlying_entry + day_offset + day_width) if short_leg else None
                if short_leg and long_leg and long_leg[0] <= short_leg[0]:
                    long_leg = fetch_first(short_leg[0] + max(day_width, 2.5))
            if not short_leg or not long_leg:
                skipped["no_option_bars"] += 1
                continue
            short_strike, short_in = short_leg
            long_strike, long_in = long_leg
            day_width = abs(short_strike - long_strike)
        else:
            short_strike, long_strike = _strikes(side, underlying_entry, day_width, day_offset, strike_step)
            legs = (occ_symbol(underlying, expiry, right, short_strike),
                    occ_symbol(underlying, expiry, right, long_strike))
            short_px = fetch_leg(legs[0], entry_utc, exit_utc)
            long_px = fetch_leg(legs[1], entry_utc, exit_utc)
            if not short_px or not long_px:
                skipped["no_option_bars"] += 1
                continue
            short_in, long_in = _at(short_px, entry_utc), _at(long_px, entry_utc)
            if short_in is None or long_in is None:
                skipped["no_option_bars"] += 1
                continue

        credit = short_in - long_in
        if not 0.0 < credit < day_width:
            skipped["degenerate"] += 1
            continue

        # Exit at intrinsic: 0DTE expires today, so settlement is mechanical
        # and needs no closing quote. Using intrinsic rather than a thin
        # late-day print avoids crediting a spread with a stale mark.
        intrinsic = intrinsic_value(side, short_strike, long_strike, underlying_exit)
        pnl_fraction = (credit - intrinsic) / day_width

        rows.append({
            "session": day.isoformat(),
            "symbol": _symbol_label(underlying, side, width, offset, pct),
            "features": {
                "credit_frac": round(credit / day_width, 8),
                "otm_pct": round(day_offset / underlying_entry, 8),
                "underlying": round(underlying_entry, 4),
                "day_of_week": float(day.weekday()),
                "gap_open": 0.0,
            },
            "next_open_to_close": round(pnl_fraction, 8),
            "next_close_to_close": round(pnl_fraction, 8),
            "credit": round(credit, 4),
            "short_strike": short_strike,
            "long_strike": long_strike,
            # Analysis-only: the underlying's own entry->exit move, so drift
            # share is computable without rebuilding the snapshot.
            "spy_session_ret": round(underlying_exit / underlying_entry - 1.0, 8),
        })

    if len(rows) < 60:
        raise ValueError(f"only {len(rows)} usable sessions (skipped {skipped}); widen the range")

    out.mkdir(parents=True, exist_ok=True)
    payload = ("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n").encode("utf-8")
    (out / "sessions.jsonl").write_bytes(payload)
    days = [row["session"] for row in rows]
    manifest = {
        "provider": "alpaca", "asset_class": "option", "underlying": underlying,
        "side": side,
        "structure": f"{side}_credit_spread" + (" near-money (pct-defined)" if pct else ""),
        "entry_hour_et": entry_hour_et,
        "exit_hour_et": exit_hour_et,
        "pricing": "actual traded option bars; exit at intrinsic (0DTE)",
        "row_count": len(rows), "skipped": skipped,
        "first_session": days[0], "last_session": days[-1],
        "session_count": len(days),
        "data_sha256": hashlib.sha256(payload).hexdigest(),
        "suggested_splits": _splits(days),
        "prepared_at_utc": datetime.now(UTC).isoformat(),
    }
    if pct:
        manifest["width_pct"] = width_pct
        manifest["offset_pct"] = offset_pct
        manifest["probe_grids"] = probe_grids
    else:
        manifest["width"] = width
        manifest["offset"] = offset
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                       encoding="utf-8")
    return manifest


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
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--side", choices=SIDES, default="put")
    parser.add_argument("--width", type=float, default=5.0)
    parser.add_argument("--offset", type=float, default=5.0)
    parser.add_argument("--width-pct", type=float, default=None)
    parser.add_argument("--offset-pct", type=float, default=None)
    parser.add_argument("--probe-grids", action="store_true",
                        help="probe $1/$2.5/$5 strike grids per leg (single-name convention)")
    parser.add_argument("--entry-hour-et", type=int, default=10)
    args = parser.parse_args(argv)
    print(json.dumps(build(underlying=args.underlying, start=args.start, end=args.end,
                           out=args.out, side=args.side, width=args.width, offset=args.offset,
                           width_pct=args.width_pct, offset_pct=args.offset_pct,
                           probe_grids=args.probe_grids,
                           entry_hour_et=args.entry_hour_et), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
