"""Staging driver: side-asymmetry replication universe (preregistered).

Eight markets NOT in the original twelve, both sides, pct-defined near-money
0DTE credit spreads (offset_pct 0.0026, width_pct 0.0064, grid probing),
entry 10:00 ET, exit at intrinsic. Idempotent: an existing manifest.json
means the dataset is already staged. Names that fail the row floor are
recorded in replication/DROPPED.json with the reason and the run continues.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from research.providers.alpaca import AlpacaHistoricalProvider  # noqa: E402
from research.experiments import prepare_odte_credit as prep  # noqa: E402

START, END = "2024-01-19", "2026-08-21"
OUT_ROOT = ROOT / ".research" / "data" / "odte" / "replication"
WIDTH_PCT, OFFSET_PCT = 0.0064, 0.0026
UNIVERSE = ("DIA", "XLF", "XLE", "SMH", "NFLX", "AMD", "COIN", "PLTR")

provider = AlpacaHistoricalProvider.from_env()
leg_cache: dict = {}
dropped: dict[str, str] = {}


def run(underlying: str, side: str, out: Path) -> None:
    if (out / "manifest.json").is_file():
        print(f"SKIP (exists) {out}", flush=True)
        return
    t0 = time.time()
    print(f"START {underlying} {side} -> {out}", flush=True)
    try:
        m = prep.build(underlying=underlying, start=START, end=END, out=out, side=side,
                       width_pct=WIDTH_PCT, offset_pct=OFFSET_PCT, probe_grids=True,
                       provider=provider, leg_cache=leg_cache, verbose=True)
        print(f"DONE  {out} rows={m['row_count']} skipped={m['skipped']} "
              f"{m['first_session']}..{m['last_session']} ({time.time() - t0:.0f}s)", flush=True)
    except Exception as exc:  # noqa: BLE001 - record and continue with the rest
        dropped[f"{underlying}-{side}"] = repr(exc)
        print(f"FAIL  {out}: {exc!r}", flush=True)
        traceback.print_exc()


def main() -> int:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    for name in UNIVERSE:
        for side, tag in (("put", "pcs"), ("call", "ccs")):
            run(name, side, OUT_ROOT / f"{name.lower()}-{tag}-nm")
    if dropped:
        (OUT_ROOT / "DROPPED.json").write_text(
            json.dumps({"dropped": dropped, "recorded_at_utc": datetime.now(UTC).isoformat()},
                       indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"ALL DONE dropped={dropped}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
