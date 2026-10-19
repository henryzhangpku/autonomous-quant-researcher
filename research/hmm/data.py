"""Candles from the research data lake, as a clean OHLCV frame.

The lake stores Alpaca bars as gzip CSV (ts, open, high, low, close, volume)
under <lake>/bars/<SYMBOL>/<timeframe>.csv.gz, crypto pairs with '/' as '-'.
Lake root: the RESEARCH_DATALAKE_DIR environment variable (required; there is
no default path). Read-only; the lake is synced by the upstream research
service.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

COLUMNS = ["ts", "open", "high", "low", "close", "volume"]


def lake_root() -> Path:
    # QS_DATALAKE_DIR is the pre-rename name, read only as a fallback; removed after one release.
    env = os.environ.get("RESEARCH_DATALAKE_DIR") or os.environ.get("QS_DATALAKE_DIR")
    if env:
        return Path(env)
    raise FileNotFoundError("set RESEARCH_DATALAKE_DIR to the research data lake root")


def bars_path(symbol: str, timeframe: str, root: Path | None = None) -> Path:
    return (root or lake_root()) / "bars" / symbol.upper().replace("/", "-") / f"{timeframe}.csv.gz"


def load_candles(symbol: str, timeframe: str, root: Path | None = None, *, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """OHLCV sorted by ts (UTC), de-duplicated, with no zero/negative prices.

    Gaps are left as gaps: a feature pipeline must not invent candles, and a
    missing hour in a 24/7 series is itself information the model may see
    only through the candles that exist.
    """
    path = bars_path(symbol, timeframe, root)
    if not path.exists():
        raise FileNotFoundError(f"no bars for {symbol} {timeframe} at {path}")
    frame = pd.read_csv(path, compression="gzip")
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    frame = frame[COLUMNS].drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    frame = frame[(frame["close"] > 0) & (frame["open"] > 0) & (frame["high"] > 0) & (frame["low"] > 0)]
    if start:
        frame = frame[frame["ts"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        frame = frame[frame["ts"] <= pd.Timestamp(end, tz="UTC")]
    return frame.reset_index(drop=True)
