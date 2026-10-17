"""Stage the intraday leg-path snapshot for the 0DTE intervention mission.

Every promoted-or-nearly-promoted cell of the 0DTE credit family is
hold-to-expiry: enter, settle at intrinsic, never manage. The intervention
mission (research/missions/odte-intervention/PREREGISTRATION.md) tests
whether intraday management rules beat doing nothing, and that question
needs the path, not just the endpoints: both sides' short and long legs,
marked hourly from entry to exit, plus the underlying's own hourly path.

Per name and session: enter at 12:00 ET with the family's pct-defined
near-money geometry on BOTH sides (put and call staged independently — a
session can ship with only one side), fetch each of the four legs ONCE over
entry-2h..exit+2h, and record the raw hourly marks at 12/13/14/15 ET. Raw
means raw: no carry-forward here. Carry-forward is a sweep-time convention
and belongs to the sweep, where it stays auditable; the snapshot records
what actually printed.

Strikes follow the qqq-nearmoney / friday-names convention (offset =
max($1, round(spot * offset_pct)), width = max($2, round(spot *
width_pct))); with ``--probe-grids`` each leg is the first strike on the
$1/$2.5/$5 grid that traded, mirroring prepare_odte_credit.build, grid
cached per calendar month, long-leg width-repair included on both sides.

One frozen convention, declared: a leg's entry-hour mark must be the actual
12:00 ET bar, not a look-back fallback. prepare_odte_credit priced entry
with _at's back_hours leniency; this snapshot cannot, because the sweep's
carry-forward anchor ("entry hour always present by construction") is only
true if the 12:00 mark is real. Sessions failing that bar are skipped as
no_option_bars, exactly where the credit snapshot skipped missing entries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from research.experiments.prepare_odte_credit import (
    _at,
    _hour_index,
    _splits,
    _strikes,
    occ_symbol,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ET = ZoneInfo("America/New_York")

SIDES = ("put", "call")
MARK_HOURS_ET = (12, 13, 14, 15)
ENTRY_HOUR_ET = 12
EXIT_HOUR_ET = 15


def _hour_marks(index: dict[datetime, float], day: Any) -> dict[str, float]:
    """Raw closes at 12..15 ET; only hours that actually printed appear."""
    marks: dict[str, float] = {}
    for hour in MARK_HOURS_ET:
        moment = datetime.combine(day, datetime.min.time(), ET).replace(hour=hour)
        key = moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        if key in index:
            marks[str(hour)] = index[key]
    return marks


def build(*, underlying: str, start: str, end: str, out: Path,
          entry_hour_et: int = ENTRY_HOUR_ET, exit_hour_et: int = EXIT_HOUR_ET,
          strike_step: float = 1.0,
          width_pct: float = 0.0064, offset_pct: float = 0.0026,
          probe_grids: bool = False,
          min_sessions: int = 60,
          provider: Any | None = None,
          leg_cache: dict[str, dict[datetime, float]] | None = None,
          verbose: bool = False) -> dict[str, Any]:
    if entry_hour_et != ENTRY_HOUR_ET or exit_hour_et != EXIT_HOUR_ET:
        raise ValueError("the path snapshot is frozen at 12:00 ET entry, 15:00 ET exit")
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

    sessions = sorted({ts.astimezone(ET).date() for ts in spot})
    rows: list[dict[str, Any]] = []
    # no_spot counts sessions; no_option_bars and degenerate count side-level
    # failures, because the two sides are staged independently.
    skipped: dict[str, int] = {"no_spot": 0, "no_option_bars": 0, "degenerate": 0}
    grid_cache: dict[str, float] = {}

    def fetch_leg(symbol: str, entry_utc: datetime, exit_utc: datetime) -> dict[datetime, float]:
        # One request per leg, one window per session: the response carries
        # every hourly bar in entry-2h..exit+2h, indexed by hour afterwards.
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
        if verbose and (len(rows) + skipped["no_spot"]) % 25 == 0:
            print(f"  ... {underlying} {day} rows={len(rows)} skipped={skipped}", flush=True)
        entry_et = datetime.combine(day, datetime.min.time(), ET).replace(hour=entry_hour_et)
        exit_et = datetime.combine(day, datetime.min.time(), ET).replace(hour=exit_hour_et)
        entry_utc, exit_utc = entry_et.astimezone(UTC), exit_et.astimezone(UTC)
        underlying_entry = _at(spot, entry_utc, back_hours=1)
        underlying_exit = _at(spot, exit_utc, back_hours=1)
        if underlying_entry is None or underlying_exit is None:
            skipped["no_spot"] += 1
            continue

        day_offset = max(1.0, round(underlying_entry * offset_pct))
        day_width = max(2.0, round(underlying_entry * width_pct))
        expiry = datetime.combine(day, datetime.min.time(), UTC)
        spot_marks = _hour_marks(spot, day)

        row: dict[str, Any] = {
            "session": day.isoformat(),
            "symbol": f"{underlying}-0DTE-PATH-NM",
            "spot": spot_marks,
        }

        for side in SIDES:
            right = "P" if side == "put" else "C"
            side_width = day_width
            if probe_grids:
                # Mirror prepare_odte_credit's friday-names path, one twist:
                # the probe tests the actual 12:00 ET bar, not _at's
                # look-back, so the sweep's carry-forward anchor is real.
                def fetch_first(target: float) -> tuple[float, dict[datetime, float]] | None:
                    cache_key = f"{day:%Y-%m}"
                    grids = ([grid_cache[cache_key]] if cache_key in grid_cache else []) \
                        + [strike_step, 2.5, 5.0]
                    seen: set[float] = set()
                    for grid in grids:
                        strike = round(target / grid) * grid
                        if strike in seen or strike <= 0:
                            continue
                        seen.add(strike)
                        index = fetch_leg(occ_symbol(underlying, expiry, right, strike),
                                          entry_utc, exit_utc)
                        if _hour_marks(index, day).get(str(entry_hour_et)) is not None:
                            grid_cache[cache_key] = grid
                            return strike, index
                    return None

                if side == "put":
                    short_leg = fetch_first(underlying_entry - day_offset)
                    long_leg = fetch_first(underlying_entry - day_offset - day_width) \
                        if short_leg else None
                    if short_leg and long_leg and long_leg[0] >= short_leg[0]:
                        long_leg = fetch_first(short_leg[0] - max(day_width, 2.5))
                else:
                    short_leg = fetch_first(underlying_entry + day_offset)
                    long_leg = fetch_first(underlying_entry + day_offset + day_width) \
                        if short_leg else None
                    if short_leg and long_leg and long_leg[0] <= short_leg[0]:
                        long_leg = fetch_first(short_leg[0] + max(day_width, 2.5))
                if not short_leg or not long_leg:
                    skipped["no_option_bars"] += 1
                    continue
                short_strike, short_index = short_leg
                long_strike, long_index = long_leg
                side_width = abs(short_strike - long_strike)
            else:
                short_strike, long_strike = _strikes(side, underlying_entry,
                                                     day_width, day_offset, strike_step)
                short_index = fetch_leg(occ_symbol(underlying, expiry, right, short_strike),
                                        entry_utc, exit_utc)
                long_index = fetch_leg(occ_symbol(underlying, expiry, right, long_strike),
                                       entry_utc, exit_utc)

            short_marks = _hour_marks(short_index, day)
            long_marks = _hour_marks(long_index, day)
            short_in = short_marks.get(str(entry_hour_et))
            long_in = long_marks.get(str(entry_hour_et))
            if short_in is None or long_in is None:
                skipped["no_option_bars"] += 1
                continue
            credit = short_in - long_in
            if not 0.0 < credit < side_width:
                skipped["degenerate"] += 1
                continue
            row[side] = {
                "short_strike": short_strike,
                "long_strike": long_strike,
                "width": side_width,
                "credit": round(credit, 4),
                "short_marks": short_marks,
                "long_marks": long_marks,
            }

        if "put" not in row and "call" not in row:
            continue
        rows.append(row)

    if len(rows) < min_sessions:
        raise ValueError(f"only {len(rows)} usable sessions (skipped {skipped}); widen the range")

    out.mkdir(parents=True, exist_ok=True)
    payload = ("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n").encode("utf-8")
    (out / "sessions.jsonl").write_bytes(payload)
    days = [row["session"] for row in rows]
    manifest = {
        "provider": "alpaca", "asset_class": "option", "underlying": underlying,
        "structure": "0DTE path snapshot, both sides, near-money pct-defined",
        "entry_hour_et": entry_hour_et,
        "exit_hour_et": exit_hour_et,
        "pricing": "actual traded option bars; raw hourly marks 12-15 ET "
                   "(no carry-forward at staging); settle at intrinsic (0DTE)",
        "skipped_unit": "no_spot counts sessions; no_option_bars and degenerate "
                        "count side-level failures (sides staged independently)",
        "row_count": len(rows), "skipped": skipped,
        "first_session": days[0], "last_session": days[-1],
        "session_count": len(days),
        "data_sha256": hashlib.sha256(payload).hexdigest(),
        "suggested_splits": _splits(days),
        "width_pct": width_pct,
        "offset_pct": offset_pct,
        "probe_grids": probe_grids,
        "prepared_at_utc": datetime.now(UTC).isoformat(),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                       encoding="utf-8")
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--underlying", default="SPY")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--width-pct", type=float, default=0.0064)
    parser.add_argument("--offset-pct", type=float, default=0.0026)
    parser.add_argument("--probe-grids", action="store_true",
                        help="probe $1/$2.5/$5 strike grids per leg (single-name convention)")
    parser.add_argument("--min-sessions", type=int, default=60,
                        help="usable-session floor; lower only for smoke tests")
    args = parser.parse_args(argv)
    print(json.dumps(build(underlying=args.underlying, start=args.start, end=args.end,
                           out=args.out, width_pct=args.width_pct,
                           offset_pct=args.offset_pct, probe_grids=args.probe_grids,
                           min_sessions=args.min_sessions, verbose=True),
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
