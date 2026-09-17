"""Summarize staged 0DTE credit datasets: rows, range, credit, skips, sha."""

from __future__ import annotations

import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / ".research" / "data" / "odte"


def summarize(d: Path) -> None:
    manifest_path = d / "manifest.json"
    if not manifest_path.is_file():
        print(f"MISSING  {d.relative_to(DATA)}")
        return
    m = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (d / "sessions.jsonl").read_text().splitlines()]
    mean_cf = statistics.mean(r["features"]["credit_frac"] for r in rows)
    geom = m.get("structure", "")
    if "width" in m:
        geom += f" w{m['width']:g}o{m['offset']:g}"
    else:
        geom += f" wpct={m.get('width_pct')} opct={m.get('offset_pct')}"
    print(f"{d.relative_to(DATA)}  und={m['underlying']} side={m.get('side', '?')} | {geom}")
    print(f"  rows={m['row_count']} sessions={m['first_session']}..{m['last_session']} "
          f"mean_credit_frac={mean_cf:.6f} skipped={m['skipped']}")
    print(f"  data_sha256={m['data_sha256']}")


def main() -> int:
    dirs = sorted(DATA / "geometry-calls" / g for g in ("w2o5", "w5o2", "w5o5", "w5o10", "w10o5"))
    dirs += [DATA / "qqq-call-nm", DATA / "iwm-call-nm"]
    for name in ("aapl", "amzn", "googl", "meta", "msft", "nvda", "tsla", "avgo", "ibit"):
        dirs += [DATA / f"{name}-pcs-nm", DATA / f"{name}-ccs-nm"]
    for d in dirs:
        summarize(d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
