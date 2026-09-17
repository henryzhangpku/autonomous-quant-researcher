"""Verify the Zero-to-Profit timing table on ten years of SPY bars.

The book's chapter 1 makes six explicit probability claims for the 9:55 AM ET
setup (gap direction x 9:30-9:55 move -> dip buy or short):

    gap down + drop  -> dip buy: 80% | gap up + rise         -> short: 80%
    gap down + flat  -> dip buy: 70% | gap up + flat         -> short: 65%
    gap up   + drop  -> dip buy: 60% | gap down + rise       -> short: 60%

This is a DESIGNED verification, declared before any result is read:

- Data: the frozen SPY 5-minute store (Alpaca SIP, adjusted; same-day ratios
  are adjustment-invariant, and every quantity here is a same-day or
  close-to-open ratio).
- Gap: today's 9:30 open vs the prior session's 15:55 close. The book gives
  no thresholds, so both a sign reading (any gap) and a 0.1% materiality
  floor are reported.
- Morning move 9:30 -> 9:55: drop <= -0.15%, rise >= +0.15%, consolidate
  in between (declared here; the book gives none).
- Win: the direction called at 9:55 is correct at the exit - both book exits
  reported: 10:55 AM and 1:00 PM ET. Underlying direction only; option
  pricing, sizing, and averaging are the NEXT verification layer.
- Splits reported separately: 2016-2021, 2022-2024, 2025+ (the SPY holdout
  seal protects option-strategy campaigns; a bars-level direction tally is
  diagnostic evidence, listed last and clearly labeled).

Output: .research/verifications/book-timing-v1/results.json
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from research.backtest import SessionStore

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPOSITORY_ROOT / ".research" / "verifications" / "book-timing-v1"

ENTRY = 9 * 60 + 55
EXITS = {"1055": 10 * 60 + 55, "1300": 13 * 60}
MOVE_THRESHOLD = 0.15   # percent, 9:30 -> 9:55
GAP_FLOOR = 0.1         # percent, for the materiality variant
ERAS = (("2016-2021", "2016-01-01", "2021-12-31"),
        ("2022-2024", "2022-01-01", "2024-12-31"),
        ("2025+", "2025-01-01", "9999-12-31"))

CLAIMS = {
    ("down", "drop"): ("dip_buy", 80),
    ("down", "flat"): ("dip_buy", 70),
    ("up", "drop"): ("dip_buy", 60),
    ("up", "rise"): ("short", 80),
    ("up", "flat"): ("short", 65),
    ("down", "rise"): ("short", 60),
}


def main() -> int:
    sessions = SessionStore.load("SPY").sessions()
    tallies: dict[tuple, dict] = defaultdict(lambda: defaultdict(int))
    prior_close: float | None = None
    for session in sessions:
        entry_px = session.price_at(ENTRY)
        exit_prices = {name: session.price_at(minute) for name, minute in EXITS.items()}
        open_px = session.open
        this_close = session.close if session.is_full_day() else None
        if prior_close is None or entry_px is None or any(px is None for px in exit_prices.values()):
            prior_close = this_close or prior_close
            continue
        gap_pct = (open_px / prior_close - 1.0) * 100.0
        move_pct = (entry_px / open_px - 1.0) * 100.0
        prior_close = this_close or prior_close

        gap_sign = "down" if gap_pct < 0 else "up"
        if move_pct <= -MOVE_THRESHOLD:
            move = "drop"
        elif move_pct >= MOVE_THRESHOLD:
            move = "rise"
        else:
            move = "flat"
        claim = CLAIMS.get((gap_sign, move))
        if claim is None:
            continue
        side, _claimed = claim
        for era_name, start, end in ERAS:
            if not start <= session.day <= end:
                continue
            for variant in ("any_gap",) + (("material_gap",) if abs(gap_pct) >= GAP_FLOOR else ()):
                for exit_name, exit_px in exit_prices.items():
                    won = exit_px > entry_px if side == "dip_buy" else exit_px < entry_px
                    key = (gap_sign, move, era_name, variant, exit_name)
                    tallies[key]["n"] += 1
                    tallies[key]["wins"] += int(won)

    results = []
    for (gap_sign, move, era, variant, exit_name), tally in sorted(tallies.items()):
        side, claimed = CLAIMS[(gap_sign, move)]
        n, wins = tally["n"], tally["wins"]
        results.append({
            "setup": f"gap {gap_sign} + {move}", "side": side,
            "claimed_pct": claimed, "era": era, "gap_variant": variant,
            "exit": exit_name, "n": n,
            "observed_pct": round(wins / n * 100.0, 1) if n else None,
        })
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "results.json").write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(f"{'setup':<22} {'side':<8} {'claim':>5} | era        variant       exit  n     observed")
    for row in results:
        print(f"{row['setup']:<22} {row['side']:<8} {row['claimed_pct']:>4}% | "
              f"{row['era']:<10} {row['gap_variant']:<13} {row['exit']:<5} "
              f"{row['n']:<5} {row['observed_pct']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
