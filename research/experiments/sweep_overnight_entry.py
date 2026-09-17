"""Preregistered entry-minute sweep over the frozen overnight evaluator.

FINDINGS.md follow-up 3: pin the time boundary of the overnight put-credit
premium with a DESIGNED grid instead of LLM proposals. Every grid point is an
unconditional put-credit candidate evaluated by the untouched, frozen
`research.validators.overnight_spx` CLI against the hash-pinned snapshot; this
module writes candidates and collects validator JSON, it computes nothing.

The grid is declared here, in full, before any result is read:
entry minutes 135..915 step 45 (plus 915), widths {20, 25}, short-strike
offsets {10, 15, 20}. Output: one JSONL row per grid point under
`.research/sweeps/overnight-entry-v1/`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DATA_HASH = "29167dd552d786af24d644764eff434006c7a7754c48ceb09e002a15f1d9e69c"
OUT_DIR = REPOSITORY_ROOT / ".research" / "sweeps" / "overnight-entry-v1"

ENTRY_MINUTES = tuple(range(135, 901, 45)) + (915,)
WIDTHS = (20.0, 25.0)
OFFSETS = (10.0, 15.0, 20.0)

CANDIDATE = """
    from research.backtest import spx_put_credit_spread

    ENTRY_MIN = {minute}
    LABEL = "sweep put credit w{width:g} o{offset:g} @{minute}"

    def signal(session, entry_minute):
        return True

    def structure(session, entry_price):
        return spx_put_credit_spread(entry_price, width={width}, otm_offset={offset})
"""


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results_path = OUT_DIR / "results.jsonl"
    rows = []
    with results_path.open("w", encoding="utf-8") as handle:
        for minute in ENTRY_MINUTES:
            for width in WIDTHS:
                for offset in OFFSETS:
                    candidate = OUT_DIR / f"cand_{minute}_{width:g}_{offset:g}.py"
                    candidate.write_text(
                        textwrap.dedent(
                            CANDIDATE.format(minute=minute, width=width, offset=offset)
                        ),
                        encoding="utf-8",
                    )
                    completed = subprocess.run(
                        [sys.executable, "-m", "research.validators.overnight_spx",
                         "--candidate", str(candidate),
                         "--data-hash", DATA_HASH, "--split", "discovery"],
                        cwd=REPOSITORY_ROOT, text=True, capture_output=True, timeout=300,
                    )
                    payload_line = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else "{}"
                    try:
                        payload = json.loads(payload_line)
                    except json.JSONDecodeError:
                        payload = {"status": "unparseable", "raw": payload_line[:500]}
                    row = {"entry_minute": minute, "width": width, "offset": offset, **payload}
                    handle.write(json.dumps(row, sort_keys=True) + "\n")
                    handle.flush()
                    rows.append(row)
                    print(f"{minute:>3} w{width:g} o{offset:g}: "
                          f"net={payload.get('net_mean_w')} score={payload.get('score')}")
    positive = [row for row in rows if isinstance(row.get("net_mean_w"), (int, float)) and row["net_mean_w"] > 0]
    print(f"\n{len(rows)} grid points, {len(positive)} with positive net mean; results: {results_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
