"""Does the 1DTE horizon price its own two-day variance correctly?

The attribution diagnostic left a residual: after the underlying's move is
regressed out, the promoted 1DTE call debit still shows +3.3% to +5.0% of
width that direction does not explain. Before that residual is called alpha,
the cheaper explanation has to be excluded -- that the evaluator prices the
two-day hold with too MUCH volatility.

Mechanism: for an ATM debit vertical, raising sigma lifts the short wing more
than the long wing in relative terms, so the modeled DEBIT falls. Overstate
the variance and every long debit spread is bought too cheaply, and every
credit spread is sold too cheaply -- which is exactly the sign pattern the
mirror cells show.

The v2 validator extends a one-session estimate to the 1DTE hold with
    sigma_2d = sqrt(sigma_entry**2 + sigma_135**2)
i.e. it adds one full evening-to-settle session of variance. That is an
approximation, and it has never been checked against what the market did.

This script compares, per split, the modeled sigma actually used against the
realized dispersion of the same entry -> next-settlement log returns. A ratio
above 1 means the evaluator systematically overstates two-day variance, and
the residual is a pricing artifact rather than an edge.

Read-only: it prices nothing, gates nothing, and promotes nothing.
"""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any

from research.backtest.overnight import OvernightSession, trailing_remaining_vol
from research.validators.overnight_spx import DEFAULT_DATA, SPLITS, _load_sessions
from research.validators.overnight_spx_v2 import EWMA_DECAY

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DATA_HASH = "29167dd552d786af24d644764eff434006c7a7754c48ceb09e002a15f1d9e69c"
OUT_DIR = REPOSITORY_ROOT / ".research" / "sweeps" / "overnight-1dte-attribution"
ENTRY_MINUTE = 135


def compare(sessions: list[OvernightSession], split: str) -> dict[str, Any]:
    vol_by_day = trailing_remaining_vol(sessions, decay=EWMA_DECAY)
    start, end = SPLITS[split]
    modeled: list[float] = []
    realized_returns: list[float] = []

    for index, session in enumerate(sessions):
        if not start <= session.day <= end or not session.is_complete():
            continue
        sigma_by_minute = vol_by_day.get(session.day)
        if sigma_by_minute is None:
            continue
        next_session = sessions[index + 1] if index + 1 < len(sessions) else None
        if next_session is None or not next_session.is_complete():
            continue
        entry_price = session.price_at(ENTRY_MINUTE)
        settlement = next_session.settlement
        if entry_price is None or settlement is None:
            continue
        # The exact sigma the v2 validator hands the pricer on this horizon.
        modeled.append(math.sqrt(
            sigma_by_minute[ENTRY_MINUTE] ** 2 + sigma_by_minute[135] ** 2
        ))
        realized_returns.append(math.log(settlement / entry_price))

    mean_modeled = statistics.mean(modeled)
    # Realized total log vol over the same holds, de-meaned (dispersion, not drift).
    realized_sigma = statistics.stdev(realized_returns)
    # Un-de-meaned second moment: what an option actually had to cover.
    realized_rms = math.sqrt(statistics.mean(r * r for r in realized_returns))
    return {
        "split": split,
        "holds": len(modeled),
        "mean_modeled_sigma": round(mean_modeled, 6),
        "realized_sigma_demeaned": round(realized_sigma, 6),
        "realized_rms": round(realized_rms, 6),
        "modeled_over_realized_sigma": round(mean_modeled / realized_sigma, 4),
        "modeled_over_realized_rms": round(mean_modeled / realized_rms, 4),
        "mean_realized_log_ret": round(statistics.mean(realized_returns), 6),
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sessions = _load_sessions(DEFAULT_DATA, DATA_HASH)
    report = {
        "question": "does the v2 sqrt(sigma_entry^2 + sigma_135^2) extension overstate two-day variance?",
        "entry_minute": ENTRY_MINUTE,
        "data_hash": DATA_HASH,
        "splits": [compare(sessions, split) for split in ("discovery", "validation")],
    }
    for row in report["splits"]:
        print(f"[{row['split']}] holds={row['holds']} modeled={row['mean_modeled_sigma']} "
              f"realized(demeaned)={row['realized_sigma_demeaned']} rms={row['realized_rms']} "
              f"-> ratio {row['modeled_over_realized_sigma']} (vs rms {row['modeled_over_realized_rms']})")
    ratios = [row["modeled_over_realized_sigma"] for row in report["splits"]]
    report["verdict"] = (
        "the 1DTE variance extension OVERSTATES realized dispersion; long debit "
        "structures are modeled too cheap on this horizon"
        if min(ratios) > 1.05
        else "the extension is within 5% of realized dispersion on at least one split"
    )
    (OUT_DIR / "vol_model_check.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print("\nVERDICT: " + report["verdict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
