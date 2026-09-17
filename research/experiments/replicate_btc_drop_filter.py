"""Replication: does the weekend drop-filter hold on weeknight overnights?

The weekend campaign produced one cell that passed discovery outright and
stayed positive out of sample:

    ret_24h > -0.01  ->  long        (i.e. take the window UNLESS BTC has
                                      fallen more than 1% in the prior 24h)

    weekend discovery : 93 windows, +46.68 bps/window, Sharpe 2.24, PASSED
    weekend validation: 34 windows, +20.47 bps/window, Sharpe 1.03, rejected
                        on positive_both_halves and survives_without_best_day

With 34 validation weekends a single exceptional window carries the mean, and
the gate is right to refuse it. The answer to a thin sample is more data, not
a softer gate.

This script tests the SAME rule, stated in advance and unchanged, on the
weeknight overnight window (16:00 ET -> 09:30 ET, Mon-Thu). That is roughly
four times the sample and it is data the rule has never seen. The mechanism
under test is identical: whether a sharp 24-hour decline predicts continued
weakness across the following non-equity-hours window.

Declared BEFORE any result is read:

- The rule is fixed: `ret_24h > -0.01 -> long`. No threshold search, no
  re-fitting. One cell.
- The unconditional baseline for this window is evaluated FIRST, so the
  rule's result can only be read as an improvement on the drift, never
  mistaken for it.
- The mirror cell `ret_24h < -0.01 -> long` is also evaluated, because the
  mechanism claims that side should be NEGATIVE. A replication that confirms
  only the profitable half is weaker evidence than one that reproduces the
  sign flip.
- Economics, gates and splits identical to the weekend campaign: 10 bps round
  trip, doubled-cost stress at 20 bps, shipped bars_universe gates in
  single-symbol configuration, snapshot's own 60/20/20.
- Discovery and validation are both reported. The holdout is not touched.

A replication cannot promote anything by itself; it can only make the
original finding more or less believable.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPOSITORY_ROOT / ".research" / "sweeps" / "btc-drop-filter-replication"
SNAPSHOT_DIR = REPOSITORY_ROOT / ".research" / "data" / "crypto" / "btc-overnight"
COST_BPS = 10.0

CELLS = (
    ("rule", "return 1 if features['ret_24h'] > -0.01 else 0"),
    ("mirror", "return 1 if features['ret_24h'] < -0.01 else 0"),
    ("baseline", "return 1"),
)


def write_spec(manifest: dict) -> tuple[Path, str]:
    spec = {
        "name": "btc-drop-filter-replication",
        "universe": ["BTC-OVERNIGHT"],
        "splits": manifest["suggested_splits"],
        "min_trades": {"discovery": 50, "validation": 20, "holdout": 20},
        "min_sessions": {"discovery": 50, "validation": 20, "holdout": 20},
        "min_symbols": 1,
        "max_concentration": 1.0,
        "cost_bps": COST_BPS,
        "target_sharpe": 0.75,
        "payoff": "next_open_to_close",
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "spec.json"
    raw = (json.dumps(spec, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def run(candidate: Path, spec: tuple[Path, str], data_hash: str, split: str) -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", "research.validators.bars_universe",
         "--candidate", str(candidate), "--spec", str(spec[0]), "--spec-hash", spec[1],
         "--data", str(SNAPSHOT_DIR / "sessions.jsonl"), "--data-hash", data_hash,
         "--split", split],
        cwd=REPOSITORY_ROOT, text=True, capture_output=True, timeout=300,
    )
    line = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else "{}"
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {"status": "unparseable", "raw": line[:300]}


def main() -> int:
    manifest = json.loads((SNAPSHOT_DIR / "manifest.json").read_text(encoding="utf-8"))
    spec = write_spec(manifest)
    print(f"overnight snapshot: {manifest['row_count']} windows "
          f"{manifest['first_session']} -> {manifest['last_session']}")
    print(f"splits: {json.dumps(manifest['suggested_splits'])}\n")

    results: dict[str, dict] = {}
    for name, body in CELLS:
        path = OUT_DIR / f"c_{name}.py"
        path.write_text(textwrap.dedent(f"""
            LABEL = "{name}"

            def signal(symbol, features):
                {body}
            """).strip() + "\n", encoding="utf-8")
        for split in ("discovery", "validation"):
            payload = run(path, spec, manifest["data_sha256"], split)
            results[f"{name}/{split}"] = payload
            failed = [k for k, ok in (payload.get("gates") or {}).items() if not ok]
            print(f"{name:<9} {split:<11}: status={payload.get('status'):<9} "
                  f"n={payload.get('trades'):>4} "
                  f"bps/window={payload.get('net_portfolio_mean_bps')} "
                  f"sharpe={payload.get('net_sharpe')}")
            if failed:
                print(f"            failed: {failed}")

    summary = {
        "hypothesis": "ret_24h > -0.01 -> long, replicated on weeknight overnight windows",
        "origin": {"campaign": "btc-weekend-v1",
                   "discovery": "93 windows, +46.68 bps, Sharpe 2.24, passed",
                   "validation": "34 windows, +20.47 bps, Sharpe 1.03, rejected"},
        "snapshot": {k: manifest[k] for k in
                     ("symbol", "session_kind", "row_count", "first_session",
                      "last_session", "data_sha256")},
        "splits": manifest["suggested_splits"],
        "results": {key: {k: payload.get(k) for k in
                          ("status", "trades", "net_portfolio_mean_bps", "net_sharpe", "gates")}
                    for key, payload in results.items()},
        "economics": {"cost_bps": COST_BPS},
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                                          encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
