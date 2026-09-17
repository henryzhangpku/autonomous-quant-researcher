"""Is an overnight cell alpha, or is it upward drift wearing an option?

The hold-through sweep (`sweep_overnight_1dte.py`) produced exactly one
validation passer: ATM (offset 0) call debit, width 20, entered 20:15 ET --
the same entry minute as the already-promoted campaign-2 put credit -- and
settled at TOMORROW's 16:00 ET close. Two facts about the shape demand a
control before anything is promoted:

1. Both surviving families on this horizon -- put credit and call debit --
   are LONG delta, and both peaked at offset 0, the maximum-delta cell.
2. The payoff grew when the hold was extended from one session to two.

That is the exact signature the bars campaign already named: "a condition
whose opposite passes equally carries no information", where every passer
won by being invested across a rising window. The snapshot starts 2024-09
and contains no bear market, so directional exposure is paid for free here.

This diagnostic asks one falsifiable question: with the underlying's move
over the identical hold regressed out, does the cell still pay?

DECLARED BEFORE READING ANY RESULT -- the attribution is honest only if the
reproduction is exact, so the script first recomputes the frozen validator's
own per-trade economics and ABORTS unless its aggregate matches
`overnight_spx_v2` to 1e-9. Nothing here re-prices, re-costs, or re-gates:
pricing comes from the same `research.backtest` primitives, and the pass/fail
rule is the frozen `_weekly_bootstrap_lower_bound` applied unchanged to the
drift-neutral series.

Verdict rule, declared in advance:
- alpha (the drift-neutral mean) > 0 AND its weekly bootstrap lower bound > 0
  on BOTH discovery and validation -> the edge survives the control.
- otherwise -> the cell is drift, and it must not be promoted.

The same control applies to any overnight cell, so the geometry is a command
line argument and the 1DTE passer is only the default. The rule above was
fixed before the first cell was run and does not change per cell.

The regression uses the full-sample beta, which is lookahead. That is
deliberate and permissible HERE because this is an attribution diagnostic,
not a candidate: lookahead in the control biases the test IN FAVOUR of
finding alpha, so a negative verdict is conservative and a positive one
would still need a causal implementation before any promotion.
"""

from __future__ import annotations

import json
import math
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import argparse

from research.backtest import (
    spx_call_credit_spread,
    spx_call_debit_spread,
    spx_put_credit_spread,
    spx_put_debit_spread,
)
from research.backtest.overnight import (
    CandidateOvernightView,
    OvernightSession,
    trailing_remaining_vol,
)
from research.validators.overnight_spx import (
    DEFAULT_DATA,
    SPLITS,
    _load_sessions,
    _validate_structure,
    _week_key,
    _weekly_bootstrap_lower_bound,
)
from research.validators.overnight_spx_v2 import EWMA_DECAY

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DATA_HASH = "29167dd552d786af24d644764eff434006c7a7754c48ceb09e002a15f1d9e69c"
OUT_ROOT = REPOSITORY_ROOT / ".research" / "sweeps"
COST_FRAC = 0.015
VRP = 1.0
REPRODUCTION_TOLERANCE = 1e-9

STRUCTURES = {
    "spx_call_debit_spread": spx_call_debit_spread,
    "spx_put_debit_spread": spx_put_debit_spread,
    "spx_put_credit_spread": spx_put_credit_spread,
    "spx_call_credit_spread": spx_call_credit_spread,
}

#: Mirror cells: same entry, same geometry, each delta sign.
#:
#: CAVEAT, recorded after the first run: for verticals at identical strikes a
#: debit and its credit counterpart are exact complements, so their nets sum
#: to precisely -2 x cost_frac by construction (measured: -0.03 on every
#: pair). The mirror therefore CONFIRMS the pricing is internally consistent
#: but carries no independent information about direction. It is kept as a
#: consistency check only; the drift regression is the actual discriminator.
MIRROR_CELLS = (
    ("spx_call_debit_spread", "long delta"),
    ("spx_put_credit_spread", "long delta"),
    ("spx_put_debit_spread", "short delta"),
    ("spx_call_credit_spread", "short delta"),
)


def trade_rows(sessions: list[OvernightSession], cell: argparse.Namespace, split: str) -> list[dict[str, Any]]:
    """Per-trade economics for one cell, mirroring the frozen v2 loop exactly.

    Every pricing, skip, and cost decision below is the validator's; the one
    addition is ``underlying_ret``, the move the trade was exposed to.
    """
    build = STRUCTURES[cell.structure]
    vol_by_day = trailing_remaining_vol(sessions, decay=EWMA_DECAY)
    start, end = SPLITS[split]
    rows: list[dict[str, Any]] = []
    for index, session in enumerate(sessions):
        if not start <= session.day <= end:
            continue
        if not session.is_complete():
            continue
        sigma_by_minute = vol_by_day.get(session.day)
        if sigma_by_minute is None:
            continue
        entry_price = session.price_at(cell.entry_minute)
        settlement = session.settlement
        if cell.horizon == "next_session":
            next_session = sessions[index + 1] if index + 1 < len(sessions) else None
            if next_session is None or not next_session.is_complete():
                continue
            settlement = next_session.settlement
        if entry_price is None or settlement is None:
            continue
        # Constructed and discarded on purpose: the frozen loop builds the view
        # here, so building it keeps any construction-time skip identical. The
        # cells below are unconditional, so the view itself is never read.
        CandidateOvernightView.at(session, cell.entry_minute)
        structure = _validate_structure(
            build(entry_price, width=cell.width, otm_offset=cell.offset)
        )
        sigma_entry = sigma_by_minute[cell.entry_minute]
        if cell.horizon == "next_session":
            sigma_entry = math.sqrt(sigma_entry**2 + sigma_by_minute[135] ** 2)
        debit = structure.model_entry_cost(entry_price, sigma_entry * VRP)
        width = structure.width
        if not -width < debit < width:
            continue
        net = (structure.settle(settlement) - debit - COST_FRAC * width) / width
        rows.append({
            "day": session.day,
            "week": _week_key(session.day),
            "net_w": net,
            "underlying_ret": (settlement - entry_price) / entry_price,
        })
    return rows


def run_validator(out_dir: Path, structure_name: str, cell: argparse.Namespace,
                  split: str) -> dict[str, Any]:
    """The frozen validator's own answer for a cell, unmodified."""
    out_dir.mkdir(parents=True, exist_ok=True)
    candidate = out_dir / f"cand_{structure_name}.py"
    candidate.write_text(
        f"from research.backtest import {structure_name}\n\n"
        f"ENTRY_MIN = {cell.entry_minute}\n"
        f'LABEL = "{structure_name} w{cell.width:g} o{cell.offset:g} @{cell.entry_minute}"\n\n'
        "def signal(session, entry_minute):\n    return True\n\n"
        "def structure(session, entry_price):\n"
        f"    return {structure_name}(entry_price, width={cell.width}, otm_offset={cell.offset})\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, "-m", "research.validators.overnight_spx_v2",
         "--candidate", str(candidate), "--data-hash", DATA_HASH,
         "--split", split, "--cost-frac", str(COST_FRAC), "--vrp", str(VRP),
         "--exit", "settlement", "--horizon", cell.horizon],
        cwd=REPOSITORY_ROOT, text=True, capture_output=True, timeout=300,
    )
    return json.loads(completed.stdout.strip().splitlines()[0])


def weekly_means(rows: list[dict[str, Any]], key: str) -> list[float]:
    by_week: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_week[row["week"]].append(row[key])
    return [statistics.mean(by_week[week]) for week in sorted(by_week)]


def attribute(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Split each trade's net into the part the market's move explains, and the rest."""
    nets = [row["net_w"] for row in rows]
    rets = [row["underlying_ret"] for row in rows]
    mean_net, mean_ret = statistics.mean(nets), statistics.mean(rets)
    variance = sum((ret - mean_ret) ** 2 for ret in rets)
    beta = (
        sum((ret - mean_ret) * (net - mean_net) for ret, net in zip(rets, nets)) / variance
        if variance
        else 0.0
    )
    alpha = mean_net - beta * mean_ret
    for row in rows:
        # The trade's P&L with its directional exposure priced out.
        row["net_w_drift_neutral"] = row["net_w"] - beta * row["underlying_ret"]

    neutral_weekly = weekly_means(rows, "net_w_drift_neutral")
    raw_weekly = weekly_means(rows, "net_w")
    neutral_lb = _weekly_bootstrap_lower_bound(neutral_weekly)
    return {
        "trades": len(rows),
        "weeks": len(raw_weekly),
        "mean_net_w": round(mean_net, 6),
        "mean_underlying_ret": round(mean_ret, 6),
        "beta_net_per_unit_ret": round(beta, 4),
        "drift_component_w": round(beta * mean_ret, 6),
        "drift_share_of_payoff": round(beta * mean_ret / mean_net, 4) if mean_net else None,
        "alpha_drift_neutral_w": round(alpha, 6),
        "alpha_weekly_bootstrap_lb_w": round(neutral_lb, 6) if neutral_weekly else None,
        "raw_weekly_bootstrap_lb_w": round(_weekly_bootstrap_lower_bound(raw_weekly), 6),
        "alpha_survives": bool(alpha > 0 and neutral_lb > 0),
    }


def mirror_test(out_dir: Path, cell: argparse.Namespace, split: str) -> list[dict[str, Any]]:
    """Consistency check across delta signs (see MIRROR_CELLS for its limits)."""
    results = []
    for structure_name, description in MIRROR_CELLS:
        payload = run_validator(out_dir / "mirrors", structure_name, cell, split)
        results.append({
            "structure": structure_name,
            "delta": description,
            "split": split,
            "net_mean_w": payload.get("net_mean_w"),
            "status": payload.get("status"),
        })
        print(f"  mirror {structure_name:<24} {description:<12} net={payload.get('net_mean_w')}")
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--structure", choices=tuple(STRUCTURES), default="spx_call_debit_spread")
    parser.add_argument("--entry-minute", type=int, default=135, help="135 = 20:15 ET")
    parser.add_argument("--width", type=float, default=20.0)
    parser.add_argument("--offset", type=float, default=0.0)
    parser.add_argument("--horizon", choices=("entry_session", "next_session"), default="next_session")
    parser.add_argument("--label", default="1dte-passer", help="names the output directory")
    parser.add_argument(
        "--splits", default="discovery,validation",
        help="splits to attribute. Naming 'holdout' SPENDS the sealed split: it is "
             "the one shot that decides a promotion and it does not reset.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    cell = build_parser().parse_args(argv)
    out_dir = OUT_ROOT / f"overnight-attribution-{cell.label}"
    out_dir.mkdir(parents=True, exist_ok=True)
    sessions = _load_sessions(DEFAULT_DATA, DATA_HASH)

    report: dict[str, Any] = {
        "question": "is this cell's payoff alpha, or the market's drift?",
        "cell": {"entry_minute": cell.entry_minute, "structure": cell.structure,
                 "width": cell.width, "offset": cell.offset, "exit": "settlement",
                 "horizon": cell.horizon},
        "economics": {"cost_frac": COST_FRAC, "vrp": VRP, "data_hash": DATA_HASH},
        "splits": {},
    }
    print(f"cell: {cell.structure} w{cell.width:g} o{cell.offset:g} "
          f"@{cell.entry_minute} horizon={cell.horizon}")

    for split in [name.strip() for name in cell.splits.split(",") if name.strip()]:
        rows = trade_rows(sessions, cell, split)
        reference = run_validator(out_dir, cell.structure, cell, split)
        reproduced = round(statistics.mean([row["net_w"] for row in rows]), 6)
        drift = abs(reproduced - reference["net_mean_w"])
        print(f"\n[{split}] reproduction: mine={reproduced} validator={reference['net_mean_w']} "
              f"delta={drift:.2e} trades={len(rows)}/{reference['signal_trades']}")
        if drift > REPRODUCTION_TOLERANCE or len(rows) != reference["signal_trades"]:
            print("ABORT: reproduction does not match the frozen validator; "
                  "the attribution would be measuring a different trade.", file=sys.stderr)
            return 2

        analysis = attribute(rows)
        analysis["mirrors"] = mirror_test(out_dir, cell, split)
        report["splits"][split] = analysis
        print(f"[{split}] mean net={analysis['mean_net_w']} "
              f"= drift {analysis['drift_component_w']} + alpha {analysis['alpha_drift_neutral_w']} "
              f"(drift share {analysis['drift_share_of_payoff']}); "
              f"alpha bootstrap LB={analysis['alpha_weekly_bootstrap_lb_w']} "
              f"-> alpha_survives={analysis['alpha_survives']}")

    report["verdict"] = (
        "alpha survives the drift control on both splits"
        if all(report["splits"][split]["alpha_survives"] for split in report["splits"])
        else "DRIFT: the cell does not survive its own directional control; do not promote"
    )
    (out_dir / "attribution.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print("\nVERDICT: " + report["verdict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
