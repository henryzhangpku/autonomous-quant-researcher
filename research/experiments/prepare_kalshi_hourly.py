"""Freeze real Kalshi hourly-binary history for the four-rules verification.

The Random Trader app runs Henry's four rules on Kalshi's hourly crypto
threshold ladders (KXBTCD, KXETHD). Unlike the SPX program, no pricing model
is needed: Kalshi's public API serves per-minute yes bid/ask OHLC candles for
settled markets plus the settlement result - the snapshot IS executable
prices. Reads are the public /trade-api/v2 (verified unauthenticated by the
app itself); this script stays read-only and touches no account surface.

Output: research/data/kalshi_hourly.jsonl.gz - one row per settled market:
{series, event, ticker, strike, close_time, result, candles:[[ts, yes_bid,
yes_ask, volume], ...]} for the market's final 65 minutes - plus a manifest
with counts, drops, and sha256. Ladder grouping happens at load time via the
event field.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BASE = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = ("KXBTCD", "KXETHD")
REQUEST_SLEEP_SECONDS = 0.15
WINDOW_MINUTES = 65


def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise
        except urllib.error.URLError:
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"kalshi request kept failing: {path}")


def settled_markets(series: str, min_close_ts: int) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    cursor = ""
    while True:
        params: dict[str, Any] = {
            "series_ticker": series, "status": "settled", "limit": 200,
            "min_close_ts": min_close_ts,
        }
        if cursor:
            params["cursor"] = cursor
        payload = _get("/markets", params)
        markets.extend(payload.get("markets") or [])
        cursor = payload.get("cursor") or ""
        time.sleep(REQUEST_SLEEP_SECONDS)
        if not cursor:
            return markets


def market_candles(series: str, ticker: str, close_time: str) -> list[list[float]]:
    close = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
    start_ts = int((close - timedelta(minutes=WINDOW_MINUTES)).timestamp())
    payload = _get(
        f"/series/{series}/markets/{ticker}/candlesticks",
        {"start_ts": start_ts, "end_ts": int(close.timestamp()), "period_interval": 1},
    )
    rows: list[list[float]] = []
    for candle in payload.get("candlesticks") or []:
        try:
            bid = float((candle.get("yes_bid") or {}).get("close_dollars") or 0.0)
            ask = float((candle.get("yes_ask") or {}).get("close_dollars") or 0.0)
            volume = float(candle.get("volume_fp") or 0.0)
            rows.append([int(candle["end_period_ts"]), bid, ask, volume])
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def prepare(*, days: int, out_dir: Path) -> dict[str, Any]:
    min_close_ts = int((datetime.now(UTC) - timedelta(days=days)).timestamp())
    rows: list[dict[str, Any]] = []
    dropped: dict[str, int] = {"no_candles": 0, "fetch_error": 0}
    per_series: dict[str, int] = {}
    for series in SERIES:
        listed = settled_markets(series, min_close_ts)
        per_series[series] = len(listed)
        print(f"{series}: {len(listed)} settled markets in the window")
        for index, market in enumerate(listed):
            ticker = market.get("ticker") or ""
            try:
                candles = market_candles(series, ticker, market["close_time"])
            except Exception:  # noqa: BLE001 - a missing market is a count, not a crash
                dropped["fetch_error"] += 1
                continue
            time.sleep(REQUEST_SLEEP_SECONDS)
            if not candles:
                dropped["no_candles"] += 1
                continue
            rows.append({
                "series": series,
                "event": market.get("event_ticker"),
                "ticker": ticker,
                "strike": market.get("floor_strike"),
                "strike_type": market.get("strike_type"),
                "close_time": market["close_time"],
                "result": market.get("result"),
                "candles": candles,
            })
            if (index + 1) % 200 == 0:
                print(f"  {series}: {index + 1}/{len(listed)} markets pulled")

    if len(rows) < 500:
        raise ValueError(f"only {len(rows)} markets with candles; refusing a thin snapshot")
    rows.sort(key=lambda row: (row["close_time"], row["ticker"]))
    payload = ("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n").encode("utf-8")
    out_dir.mkdir(parents=True, exist_ok=True)
    data_path = out_dir / "kalshi_hourly.jsonl.gz"
    with open(data_path, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
            zipped.write(payload)
    manifest = {
        "provider": "kalshi-public",
        "base": BASE,
        "series": list(SERIES),
        "requested_days": days,
        "markets": len(rows),
        "listed_per_series": per_series,
        "dropped": dropped,
        "first_close": rows[0]["close_time"],
        "last_close": rows[-1]["close_time"],
        "data_sha256": hashlib.sha256(data_path.read_bytes()).hexdigest(),
        "prepared_at_utc": datetime.now(UTC).isoformat(),
        "limitations": [
            "candle closes proxy executable prices; intraminute book moves unseen",
            "fees not in the data; the evaluator models the Kalshi fee formula",
            "window is recent crypto regime only",
        ],
    }
    (out_dir / "kalshi_hourly_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--out", type=Path, default=REPOSITORY_ROOT / "research" / "data")
    args = parser.parse_args(argv)
    manifest = prepare(days=args.days, out_dir=args.out)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
