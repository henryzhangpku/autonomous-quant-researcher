"""Staging driver: call-side (and stock both-side) 0DTE credit snapshots.

Shares one Alpaca provider and one leg-price cache across all builds so the
SPY geometry cross and the two sides cost the union of strikes, not the sum.
Idempotent: an existing manifest.json means the dataset is already staged.
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
from research.experiments import prepare_odte_credit as prep  # noqa: E402

START, END = "2024-01-19", "2026-08-21"
DATA = ROOT / ".research" / "data" / "odte"
WIDTH_PCT, OFFSET_PCT = 0.0064, 0.0026  # qqq-nearmoney / friday-names convention

provider = AlpacaHistoricalProvider.from_env()
leg_cache: dict = {}


def run(underlying: str, side: str, out: Path, **kw) -> None:
    if (out / "manifest.json").is_file():
        print(f"SKIP (exists) {out}", flush=True)
        return
    t0 = time.time()
    print(f"START {underlying} {side} -> {out}", flush=True)
    try:
        m = prep.build(underlying=underlying, start=START, end=END, out=out, side=side,
                       provider=provider, leg_cache=leg_cache, verbose=True, **kw)
        print(f"DONE  {out} rows={m['row_count']} skipped={m['skipped']} "
              f"{m['first_session']}..{m['last_session']} ({time.time() - t0:.0f}s)", flush=True)
    except Exception as exc:  # noqa: BLE001 - record and continue with the rest
        print(f"FAIL  {out}: {exc!r}", flush=True)
        traceback.print_exc()


def main() -> int:
    # SPY dollar-defined call geometries (puts already live under geometry/).
    for w, o in [(2, 5), (5, 2), (5, 5), (5, 10), (10, 5)]:
        run("SPY", "call", DATA / "geometry-calls" / f"w{w:g}o{o:g}", width=w, offset=o)

    # QQQ / IWM pct-defined near-money calls; existing put dirs untouched.
    for und in ("QQQ", "IWM"):
        run(und, "call", DATA / f"{und.lower()}-call-nm",
            width_pct=WIDTH_PCT, offset_pct=OFFSET_PCT)

    # Single names, both sides, pct-defined near-money, all weekdays.
    for name in ("AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA", "AVGO", "IBIT"):
        for side, tag in (("put", "pcs"), ("call", "ccs")):
            run(name, side, DATA / f"{name.lower()}-{tag}-nm",
                width_pct=WIDTH_PCT, offset_pct=OFFSET_PCT, probe_grids=True)
    print("ALL DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
