"""Staging driver: 0DTE leg-path snapshots for the intervention mission.

Shares one Alpaca provider and one leg-price cache across all twelve names
so overlapping strikes cost one fetch. Idempotent: an existing manifest.json
means the dataset is already staged. SPY/QQQ/IWM take the plain pct-defined
strikes; the nine single names probe the $1/$2.5/$5 strike grids, the
friday-names convention.
"""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from research.providers.alpaca import AlpacaHistoricalProvider  # noqa: E402
from research.experiments import prepare_odte_path as prep  # noqa: E402

START, END = "2024-01-19", "2026-08-21"
DATA = ROOT / ".research" / "data" / "odte" / "path"
WIDTH_PCT, OFFSET_PCT = 0.0064, 0.0026  # qqq-nearmoney / friday-names convention
INDEX_NAMES = ("SPY", "QQQ", "IWM")
SINGLE_NAMES = ("NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "IBIT")

provider = AlpacaHistoricalProvider.from_env()
leg_cache: dict = {}


def run(underlying: str, out: Path, **kw) -> None:
    if (out / "manifest.json").is_file():
        print(f"SKIP (exists) {out}", flush=True)
        return
    t0 = time.time()
    print(f"START {underlying} -> {out}", flush=True)
    try:
        m = prep.build(underlying=underlying, start=START, end=END, out=out,
                       width_pct=WIDTH_PCT, offset_pct=OFFSET_PCT,
                       provider=provider, leg_cache=leg_cache, verbose=True, **kw)
        print(f"DONE  {out} rows={m['row_count']} skipped={m['skipped']} "
              f"{m['first_session']}..{m['last_session']} ({time.time() - t0:.0f}s)", flush=True)
    except Exception as exc:  # noqa: BLE001 - record and continue with the rest
        print(f"FAIL  {out}: {exc!r}", flush=True)
        traceback.print_exc()


def main() -> int:
    for name in INDEX_NAMES:
        run(name, DATA / name.lower())
    for name in SINGLE_NAMES:
        run(name, DATA / name.lower(), probe_grids=True)
    print("ALL DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
