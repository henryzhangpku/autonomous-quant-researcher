"""The calm-day variant: does removing turbulent entries clear the full bar?

SPY w5/o2 and its QQQ replication both pass seven of eight conditions and
fail the same one — the discovery raw weekly bootstrap LB — with the damage
named to specific vol-shock weeks. The declared follow-up is a conditioned
variant whose stress marker comes from EXTERNAL convention, not from these
tapes.

THE MARKER, fixed before any result: skip entry when the underlying's prior
session close-to-close move exceeded +/-1%. One percent is a round-number
convention this repo already declared in the BTC campaign before this tape
existed; VIX>25 was the first choice but VIX is plan-gated at the provider.
No threshold search: 1% is the number, win or lose.

CELLS, all declared here:
  calm      enter unless |prior day move| >= 1%   <- the hypothesis
  turbulent enter ONLY when |prior day move| >= 1% <- must be the loss bucket
                                                     for the mechanism to hold
  baseline  enter every session                    <- the 7/8 result, anchor

Bar for the calm cell, unchanged: every validator gate + positive raw AND
drift-neutral weekly bootstrap LBs, both splits, 0.5% cost, on BOTH
underlyings. The turbulent cell is diagnostic only. Holdouts stay sealed.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPOSITORY_ROOT / ".research" / "sweeps" / "odte-calm-condition-v1"
CALM_LIMIT = 0.01
COST_BPS = 50.0

TAPES = {
    "SPY-w5o2": {"snapshot": REPOSITORY_ROOT / ".research" / "data" / "odte" / "geometry" / "w5o2",
                 "underlying": "SPY"},
    "QQQ-nearmoney": {"snapshot": REPOSITORY_ROOT / ".research" / "data" / "odte" / "qqq-nearmoney",
                      "underlying": "QQQ"},
}

CELLS = {
    "calm": "return 1 if features['prior_day_abs_ret'] < {limit} else 0",
    "turbulent": "return 1 if features['prior_day_abs_ret'] >= {limit} else 0",
    "baseline": "return 1",
}

from research.experiments.sweep_odte_geometry import drift_decompose, weekly_bootstrap_lb  # noqa: E402


def daily_closes(underlying: str, start: str, end: str) -> dict[str, float]:
    from dotenv import load_dotenv

    load_dotenv(REPOSITORY_ROOT / ".env")
    from research.providers.alpaca import AlpacaHistoricalProvider

    provider = AlpacaHistoricalProvider.from_env()
    bars = provider.bars(asset_class="equity", symbols=(underlying,),
                         start_utc=datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=UTC),
                         end_utc=datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=UTC)
                         + timedelta(days=1),
                         timeframe="1Day")
    return {bar.timestamp_utc.date().isoformat(): bar.close for bar in bars.bars}


def enrich(tape: str, cfg: dict) -> tuple[Path, dict]:
    """Join prior-session close-to-close onto the frozen snapshot rows.

    The feature is computed from data strictly BEFORE each session's entry —
    the prior two daily closes — so causality matches the family contract.
    Rows whose prior day is unknown are dropped rather than defaulted.
    """
    rows = [json.loads(line) for line in
            (cfg["snapshot"] / "sessions.jsonl").read_text(encoding="utf-8").splitlines()]
    manifest = json.loads((cfg["snapshot"] / "manifest.json").read_text(encoding="utf-8"))
    closes = daily_closes(cfg["underlying"], "2023-12-01", manifest["last_session"])
    days = sorted(closes)
    prior: dict[str, float] = {}
    for index in range(2, len(days)):
        c1, c0 = closes[days[index - 1]], closes[days[index - 2]]
        if c0:
            prior[days[index]] = abs(c1 / c0 - 1.0)
    kept = []
    for row in rows:
        move = prior.get(row["session"])
        if move is None:
            continue
        row = dict(row)
        row["features"] = dict(row["features"], prior_day_abs_ret=round(move, 8))
        kept.append(row)
    out = OUT_DIR / f"snapshot-{tape}"
    out.mkdir(parents=True, exist_ok=True)
    payload = ("\n".join(json.dumps(r, sort_keys=True) for r in kept) + "\n").encode("utf-8")
    (out / "sessions.jsonl").write_bytes(payload)
    enriched = dict(manifest, row_count=len(kept),
                    data_sha256=hashlib.sha256(payload).hexdigest(),
                    enrichment="prior_day_abs_ret from prior two daily closes")
    (out / "manifest.json").write_text(json.dumps(enriched, indent=2, sort_keys=True) + "\n",
                                       encoding="utf-8")
    return out, enriched


def evaluate(tape: str, snap_dir: Path, manifest: dict) -> dict:
    rows = [json.loads(line) for line in
            (snap_dir / "sessions.jsonl").read_text(encoding="utf-8").splitlines()]
    splits = manifest["suggested_splits"]
    spec_payload = {
        "name": f"odte-calm-{tape}",
        "universe": [rows[0]["symbol"]],
        "splits": splits,
        "min_trades": {"discovery": 120, "validation": 50, "holdout": 50},
        "min_sessions": {"discovery": 120, "validation": 50, "holdout": 50},
        "min_symbols": 1, "max_concentration": 1.0,
        "cost_bps": COST_BPS, "target_sharpe": 0.75,
        "payoff": "next_open_to_close",
    }
    spec_path = OUT_DIR / f"spec-{tape}.json"
    raw = (json.dumps(spec_payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    spec_path.write_bytes(raw)
    spec_hash = hashlib.sha256(raw).hexdigest()

    results: dict[str, dict] = {}
    for name, body in CELLS.items():
        candidate = OUT_DIR / f"c_{tape}_{name}.py"
        candidate.write_text(textwrap.dedent(f"""
            LABEL = "{name}"

            def signal(symbol, features):
                {body.format(limit=CALM_LIMIT)}
            """).strip() + "\n", encoding="utf-8")
        for split in ("discovery", "validation"):
            completed = subprocess.run(
                [sys.executable, "-m", "research.validators.bars_universe",
                 "--candidate", str(candidate), "--spec", str(spec_path),
                 "--spec-hash", spec_hash,
                 "--data", str(snap_dir / "sessions.jsonl"),
                 "--data-hash", manifest["data_sha256"], "--split", split],
                cwd=REPOSITORY_ROOT, text=True, capture_output=True, timeout=300)
            line = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else "{}"
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                payload = {"status": "unparseable"}
            window = splits[split]
            def fires(features: dict) -> bool:
                move = features["prior_day_abs_ret"]
                if name == "calm":
                    return move < CALM_LIMIT
                if name == "turbulent":
                    return move >= CALM_LIMIT
                return True

            member = [r for r in rows
                      if window[0] <= r["session"] <= window[1] and fires(r["features"])]
            cost_frac = COST_BPS / 10_000.0
            raw_pairs = [(r["session"], r["next_open_to_close"] - cost_frac) for r in member]
            decomp = drift_decompose(member, window[0], window[1], cost_frac) if member else {}
            results[f"{name}/{split}"] = {
                "validator": payload.get("status"),
                "n": len(member),
                "net_bps_w": payload.get("net_portfolio_mean_bps"),
                "sharpe": payload.get("net_sharpe"),
                "failed": [k for k, ok in (payload.get("gates") or {}).items() if not ok],
                "raw_lb": round(weekly_bootstrap_lb(raw_pairs, 20260814), 6) if member else None,
                "resid_lb": decomp.get("residual_weekly_bootstrap_lb"),
                "drift_share": decomp.get("drift_share"),
            }
            r = results[f"{name}/{split}"]
            print(f"  {name:<10} {split:<11}: {str(r['validator']):<9} n={r['n']:>4} "
                  f"net={r['net_bps_w']} rawLB={r['raw_lb']} residLB={r['resid_lb']}")
    calm = results["calm/discovery"], results["calm/validation"]
    results["clears_full_bar"] = all(
        c["validator"] == "passed" and (c["raw_lb"] or -1) > 0 and (c["resid_lb"] or -1) > 0
        for c in calm)
    return results


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary: dict = {"marker": f"skip when |prior day close-to-close| >= {CALM_LIMIT:.0%} "
                               "(external convention, no search)",
                     "cost_bps_of_width": COST_BPS, "tapes": {}}
    for tape, cfg in TAPES.items():
        print(f"=== {tape} ===")
        snap_dir, manifest = enrich(tape, cfg)
        print(f"  rows after enrichment: {manifest['row_count']}")
        summary["tapes"][tape] = evaluate(tape, snap_dir, manifest)
        print(f"  CLEARS FULL BAR: {summary['tapes'][tape]['clears_full_bar']}\n")
    both = all(t["clears_full_bar"] for t in summary["tapes"].values())
    summary["clears_on_both_underlyings"] = both
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                                          encoding="utf-8")
    print("CLEARS ON BOTH UNDERLYINGS:", both)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
