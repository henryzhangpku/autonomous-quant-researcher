"""Freeze real Kalshi 15-minute BTC/ETH up-down history.

Same shape and discipline as ``prepare_kalshi_hourly``: public read-only
/trade-api/v2, one JSONL row per settled market with the final minutes of yes
bid/ask candles plus the settlement result. The 15-minute series (KXBTC15M,
KXETH15M) are single up/down markets per window rather than threshold ladders,
so every market is near-money by construction — none of the 95% pinned-strike
dead weight the hourly ladders carry — and the visible books are far deeper
(six-figure open interest per window observed at staging time).

The candle window is 20 minutes: the market's whole 15-minute life plus a few
minutes of pre-open, so the first candle is the earliest quotable price.

Output: research/data/kalshi_15m.jsonl.gz plus a manifest with counts, drops
and sha256. No account surface is touched; requests are unauthenticated reads.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from research.experiments.prepare_kalshi_hourly import (
    _get,
    market_candles,
    settled_markets,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SERIES = ("KXBTC15M", "KXETH15M")
WINDOW_MINUTES = 20


def prepare(*, days: int, out_dir: Path,
            series: tuple[str, ...] = SERIES) -> dict[str, Any]:
    import research.experiments.prepare_kalshi_hourly as hourly

    # The shared candle fetcher reads its window length from the hourly
    # module; 65 minutes of candles for a 15-minute market is mostly empty
    # pre-listing air, so narrow it for the duration of this run.
    original_window = hourly.WINDOW_MINUTES
    hourly.WINDOW_MINUTES = WINDOW_MINUTES
    try:
        min_close_ts = int((datetime.now(UTC) - timedelta(days=days)).timestamp())
        # A staging run over ~8,500 markets takes the better part of an hour
        # and background tasks can be killed mid-flight — the first run died at
        # 4,400 markets with nothing on disk. Checkpoint every row to an
        # append-only .part file and resume by ticker, so a kill costs one
        # market rather than the run.
        out_dir.mkdir(parents=True, exist_ok=True)
        part_path = out_dir / "kalshi_15m.part.jsonl"
        rows: list[dict[str, Any]] = []
        done: set[str] = set()
        if part_path.exists():
            for line in part_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    rows.append(row)
                    done.add(row["ticker"])
            print(f"resuming: {len(done)} markets already checkpointed", flush=True)
        dropped = {"no_candles": 0, "fetch_error": 0}
        per_series: dict[str, int] = {}
        part = part_path.open("a", encoding="utf-8")
        for one_series in series:
            listed = settled_markets(one_series, min_close_ts)
            per_series[one_series] = len(listed)
            print(f"{one_series}: {len(listed)} settled markets in the window", flush=True)
            for index, market in enumerate(listed):
                ticker = market.get("ticker") or ""
                if ticker in done:
                    continue
                try:
                    candles = market_candles(one_series, ticker, market["close_time"])
                except Exception:  # noqa: BLE001 — a missing market is a count, not a crash
                    dropped["fetch_error"] += 1
                    continue
                if not candles:
                    dropped["no_candles"] += 1
                    continue
                row = {
                    "series": one_series,
                    "event": market.get("event_ticker"),
                    "ticker": ticker,
                    "strike": market.get("floor_strike"),
                    "strike_type": market.get("strike_type"),
                    "close_time": market["close_time"],
                    "result": market.get("result"),
                    "candles": candles,
                }
                rows.append(row)
                part.write(json.dumps(row, sort_keys=True) + "\n")
                part.flush()
                if (index + 1) % 200 == 0:
                    print(f"  {one_series}: {index + 1}/{len(listed)} fetched", flush=True)
    finally:
        hourly.WINDOW_MINUTES = original_window

    part.close()
    rows.sort(key=lambda r: (r["close_time"], r["ticker"]))
    data_path = out_dir / "kalshi_15m.jsonl.gz"
    payload = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows).encode("utf-8")
    with gzip.open(data_path, "wb") as handle:
        handle.write(payload)
    manifest = {
        "base": "https://api.elections.kalshi.com/trade-api/v2",
        "provider": "kalshi-public",
        "series": list(SERIES),
        "requested_days": days,
        "window_minutes": WINDOW_MINUTES,
        "listed_per_series": per_series,
        "markets": len(rows),
        "dropped": dropped,
        "first_close": rows[0]["close_time"] if rows else None,
        "last_close": rows[-1]["close_time"] if rows else None,
        "data_sha256": hashlib.sha256(payload).hexdigest(),
        "prepared_at_utc": datetime.now(UTC).isoformat(),
        "limitations": [
            "candle closes proxy executable prices; intraminute book moves unseen",
            "fees not in the data; evaluate with the Kalshi taker formula 0.07*P*(1-P)",
            "window is recent crypto regime only",
        ],
    }
    (out_dir / "kalshi_15m_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    part_path.unlink(missing_ok=True)  # the finished archive supersedes the checkpoint
    print(json.dumps({k: manifest[k] for k in ("markets", "dropped", "first_close", "last_close")}, indent=1))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=45)
    parser.add_argument("--out", type=Path, default=REPOSITORY_ROOT / "research" / "data")
    args = parser.parse_args()
    prepare(days=args.days, out_dir=args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
