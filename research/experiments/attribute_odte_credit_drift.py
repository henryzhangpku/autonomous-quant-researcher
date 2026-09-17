"""Is the SPY 0DTE put credit alpha, or is it SPY going up?

The spy-odte-credit campaign clears every discovery and validation gate at a
0.5%-of-width round trip. Its shape is the same one that just cost the
overnight-spx promotion its holdout: an UNCONDITIONAL, LONG-DELTA credit
spread with a 95% win rate, measured over 2024-01 to 2026-08 -- a window with
no bear market in it. Selling puts into a rising tape pays whether or not
anything is mispriced.

Its spec also declares `"payoff": "next_open_to_close"`, the ABSOLUTE payoff,
rather than the `_rel` benchmark-relative mode the bars campaign introduced
for exactly this reason ("a condition whose opposite passes equally carries no
information"). So the control has not been applied to this family at all.

Same declared rule as the overnight control, fixed before any result here:
- alpha (the drift-neutral mean) > 0 AND its weekly bootstrap lower bound > 0
  on BOTH discovery and validation -> the edge survives.
- otherwise -> the payoff is direction and it must not be promoted.

Method: regress each session's structure P&L on SPY's own return over the
IDENTICAL window (the 10:00 ET entry spot, already in the snapshot, to that
session's close, fetched from the same Alpaca daily bars). Subtracting
beta * return leaves what direction does not explain, and the frozen weekly
bootstrap decides whether that residual is distinguishable from zero.

The regression uses a full-sample beta, which is lookahead; as in the
overnight control that biases the test TOWARD finding alpha, so a rejection
is conservative.

Read-only: it re-prices nothing. The payoffs are the snapshot's own, built
from actual traded option bars.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from research.providers.alpaca import AlpacaHistoricalProvider
from research.validators.overnight_spx import _weekly_bootstrap_lower_bound

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_DIR = REPOSITORY_ROOT / ".research" / "data" / "odte" / "spy-pcs-w5-o5"
OUT_DIR = REPOSITORY_ROOT / ".research" / "sweeps" / "odte-credit-attribution"
UNDERLYING = "SPY"


def session_closes(sessions: list[str]) -> dict[str, float]:
    """SPY's official close per session, from the same provider as the bars."""
    provider = AlpacaHistoricalProvider.from_env()
    start = datetime.fromisoformat(min(sessions)).replace(tzinfo=UTC)
    end = datetime.fromisoformat(max(sessions)).replace(tzinfo=UTC) + timedelta(days=2)
    snapshot = provider.bars(
        asset_class="equity", symbols=(UNDERLYING,),
        start_utc=start, end_utc=end, timeframe="1Day",
    )
    return {bar.timestamp_utc.date().isoformat(): bar.close for bar in snapshot.bars}


def week_key(session: str) -> str:
    year, week, _ = datetime.fromisoformat(session).isocalendar()
    return f"{year}-W{week:02d}"


def attribute(rows: list[dict[str, Any]]) -> dict[str, Any]:
    nets = [row["net_w"] for row in rows]
    rets = [row["underlying_ret"] for row in rows]
    mean_net, mean_ret = statistics.mean(nets), statistics.mean(rets)
    variance = sum((ret - mean_ret) ** 2 for ret in rets)
    beta = (
        sum((r - mean_ret) * (n - mean_net) for r, n in zip(rets, nets)) / variance
        if variance else 0.0
    )
    alpha = mean_net - beta * mean_ret

    by_week: dict[str, list[float]] = defaultdict(list)
    by_week_raw: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_week[row["week"]].append(row["net_w"] - beta * row["underlying_ret"])
        by_week_raw[row["week"]].append(row["net_w"])
    neutral_weekly = [statistics.mean(by_week[w]) for w in sorted(by_week)]
    raw_weekly = [statistics.mean(by_week_raw[w]) for w in sorted(by_week_raw)]
    neutral_lb = _weekly_bootstrap_lower_bound(neutral_weekly)

    wins = sum(1 for net in nets if net > 0)
    return {
        "trades": len(rows),
        "weeks": len(raw_weekly),
        "win_rate": round(wins / len(rows), 4) if rows else None,
        "mean_net_w": round(mean_net, 6),
        "mean_underlying_ret": round(mean_ret, 6),
        "beta_net_per_unit_ret": round(beta, 4),
        "drift_component_w": round(beta * mean_ret, 6),
        "drift_share_of_payoff": round(beta * mean_ret / mean_net, 4) if mean_net else None,
        "alpha_drift_neutral_w": round(alpha, 6),
        "alpha_weekly_bootstrap_lb_w": round(neutral_lb, 6) if neutral_weekly else None,
        "raw_weekly_bootstrap_lb_w": round(_weekly_bootstrap_lower_bound(raw_weekly), 6),
        "alpha_survives": bool(alpha > 0 and neutral_lb > 0),
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((SNAPSHOT_DIR / "manifest.json").read_text(encoding="utf-8"))
    splits = manifest["suggested_splits"]
    raw = [json.loads(line) for line in
           (SNAPSHOT_DIR / "sessions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    closes = session_closes([row["session"] for row in raw])

    rows_by_split: dict[str, list[dict[str, Any]]] = defaultdict(list)
    missing_close = 0
    for row in raw:
        close = closes.get(row["session"])
        entry = row["features"]["underlying"]
        if close is None or not entry:
            missing_close += 1
            continue
        record = {
            "session": row["session"],
            "week": week_key(row["session"]),
            "net_w": float(row["next_open_to_close"]),
            # SPY's own move over the identical hold: 10:00 ET entry -> close.
            "underlying_ret": (close - entry) / entry,
        }
        for name, (start, end) in splits.items():
            if start <= row["session"] <= end:
                rows_by_split[name].append(record)
    if missing_close:
        print(f"note: {missing_close} sessions dropped for a missing SPY close")

    report: dict[str, Any] = {
        "question": "is the SPY 0DTE put credit alpha, or SPY drift?",
        "structure": {k: manifest.get(k) for k in ("structure", "width", "offset", "entry_hour_et")},
        "splits": {},
    }
    for split in ("discovery", "validation"):
        rows = rows_by_split.get(split, [])
        if not rows:
            print(f"[{split}] no rows")
            continue
        analysis = attribute(rows)
        report["splits"][split] = analysis
        print(f"[{split}] n={analysis['trades']} win={analysis['win_rate']} "
              f"mean={analysis['mean_net_w']} = drift {analysis['drift_component_w']} "
              f"+ alpha {analysis['alpha_drift_neutral_w']} "
              f"(drift share {analysis['drift_share_of_payoff']}); "
              f"alpha LB={analysis['alpha_weekly_bootstrap_lb_w']} "
              f"-> survives={analysis['alpha_survives']}")

    report["verdict"] = (
        "alpha survives the drift control on both splits"
        if report["splits"] and all(s["alpha_survives"] for s in report["splits"].values())
        else "DRIFT: the payoff does not survive its own directional control"
    )
    (OUT_DIR / "attribution.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("\nVERDICT: " + report["verdict"])
    print("NOTE: gross of costs — the snapshot payoff carries no execution cost, "
          "so alpha here is an UPPER bound on what any cost level could earn.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
