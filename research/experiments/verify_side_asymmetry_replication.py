"""Frozen side-asymmetry statistic on the preregistered replication universe.

Implements research/missions/side-asymmetry/PREREGISTRATION.md exactly:
per market, diff = net_pct_width(CCS) - net_pct_width(PCS) at 50 bps of
width, full staged window; a market counts only when both sides have >= 60
trades. Replicates iff median(diff) > 0 AND >= 3/4 of counted diffs > 0;
negative median = refuted; anything else = not replicated.

Rows are measured with research/experiments/verify_odte_economics.py's own
measure_dataset (the frozen measurement code) and written in its results
schema. No display.json — the summary text is curated by hand.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.experiments.verify_odte_economics import measure_dataset  # noqa: E402

UNIVERSE = ("DIA", "XLF", "XLE", "SMH", "NFLX", "AMD", "COIN", "PLTR")
DATA = ROOT / ".research" / "data" / "odte" / "replication"
OUT = ROOT / ".research" / "verifications" / "side-asymmetry-replication-v1"


def main() -> int:
    rows = []
    staged: dict[str, dict[str, dict]] = {}
    missing = []
    for name in UNIVERSE:
        for tag in ("pcs", "ccs"):
            directory = DATA / f"{name.lower()}-{tag}-nm"
            if not (directory / "manifest.json").is_file():
                missing.append(directory.name)
                continue
            result = measure_dataset(directory)
            rows.append(result)
            staged.setdefault(name, {})[result["side"]] = result

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(
        json.dumps({"rows": rows}, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"staged datasets: {len(rows)}; missing/dropped: {missing or 'none'}\n")
    print(f"{'market':<7} {'PCS rows':>9} {'CCS rows':>9} {'PCS win':>8} {'CCS win':>8} "
          f"{'PCS credit':>11} {'CCS credit':>11} {'PCS net':>8} {'CCS net':>8} {'diff':>7}  counted")
    diffs = []
    for name in UNIVERSE:
        sides = staged.get(name, {})
        pcs, ccs = sides.get("PCS"), sides.get("CCS")
        if not pcs or not ccs:
            print(f"{name:<7}  DROPPED (side missing: "
                  f"{'PCS' if not pcs else ''}{'CCS' if not ccs else ''})")
            continue
        diff = round(ccs["net_mean_pct_width"] - pcs["net_mean_pct_width"], 3)
        counted = pcs["trades"] >= 60 and ccs["trades"] >= 60
        if counted:
            diffs.append(diff)
        print(f"{name:<7} {pcs['trades']:>9} {ccs['trades']:>9} "
              f"{pcs['win_rate']:>8} {ccs['win_rate']:>8} "
              f"{pcs['credit_mean_pct_width']:>11} {ccs['credit_mean_pct_width']:>11} "
              f"{pcs['net_mean_pct_width']:>8} {ccs['net_mean_pct_width']:>8} "
              f"{diff:>7}  {'yes' if counted else 'NO (<60 trades)'}")

    print()
    if len(staged) < 6:
        verdict = "INSUFFICIENT DATA (fewer than 6 of 8 markets staged)"
    else:
        median = statistics.median(diffs)
        frac_pos = sum(d > 0 for d in diffs) / len(diffs)
        print(f"counted markets: {len(diffs)} of {len(UNIVERSE)}; diffs: {diffs}")
        print(f"median diff: {round(median, 3)} pct-width; fraction positive: {frac_pos:.3f}")
        if median < 0:
            verdict = "REFUTED (negative median diff)"
        elif median > 0 and frac_pos >= 0.75:
            verdict = "REPLICATED (median diff > 0 and >= 3/4 of markets positive)"
        else:
            verdict = "NOT REPLICATED (frozen decision rule not met)"
    print(f"VERDICT: {verdict}")
    print(f"\nresults.json -> {OUT / 'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
