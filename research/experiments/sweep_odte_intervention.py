"""0DTE intervention sweep: does intraday management beat hold-to-expiry?

The 0DTE credit family's one un-cleared condition is the weekly bootstrap
lower bound hovering at zero, and the practitioner's standard answer —
manage the position intraday — has never been tested here. This sweep runs
the frozen management rules of research/missions/odte-intervention/
PREREGISTRATION.md against the leg-path snapshot staged by
prepare_odte_path.py, judged per name x side against M0 (hold) on the same
tape. The rules, triggers, cost model, verdict logic and floors are frozen
there; nothing in this file renegotiates them.

Per name x side x rule the staged rows are derived into a bars_universe
series (payoff = the rule's P&L as a fraction of the original width, gross
of the validator's entry cost, net of management-event costs baked in at
0.5% of width per event) and pushed through the frozen validator on the
discovery and validation splits, at 0.5% and 1.0% entry cost. A managed
rule is SUPPORTED only if every validator gate passes on both splits, raw
and drift-neutral weekly bootstrap LBs are positive on both splits, AND the
rule beats M0 on discovery net, validation net and both raw LBs. Cells
below the sample floor are UNTESTABLE, explicitly not refuted. Holdouts
stay sealed.

Output: <out>/results.jsonl, <out>/summary.json.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import textwrap
from datetime import date
from pathlib import Path
from typing import Any

from research.experiments.prepare_odte_credit import intrinsic_value
from research.experiments.sweep_odte_geometry import (
    BOOTSTRAP_SEED,
    drift_decompose,
    weekly_bootstrap_lb,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPOSITORY_ROOT / ".research" / "sweeps" / "odte-intervention-v1"
DATA_ROOT = REPOSITORY_ROOT / ".research" / "data" / "odte" / "path"
DERIVED_DIR = OUT_DIR / "derived"
COST_LEVELS = {"0.5pct": 50.0, "1.0pct": 100.0}
MIN_SESSIONS = {"discovery": 80, "validation": 35, "holdout": 35}

RULES = ("m0", "m1", "m2", "m3", "m4", "m5")
SIDES = ("put", "call")
MARK_HOURS = (12, 13, 14, 15)
TRIGGER_HOURS = (13, 14, 15)
EVENT_COST_FRAC = 0.005  # 0.5% of width per management event, frozen

BASELINE = """
LABEL = "unconditional credit spread"

def signal(symbol, features):
    return 1
"""


def _carry_forward(marks: dict[str, float]) -> dict[int, float]:
    """hour -> latest raw mark at or before that hour, across 12..15 ET.

    The staging contract guarantees a real 12:00 mark, so every hour of the
    session is covered; a leg with no 13:00 print evaluates its 13:00
    trigger on the 12:00 mark.
    """
    carried: dict[int, float] = {}
    last: float | None = None
    for hour in MARK_HOURS:
        mark = marks.get(str(hour))
        if mark is not None:
            last = mark
        if last is not None:
            carried[hour] = last
    return carried


def _evaluate_side(side: str, row: dict[str, Any]) -> dict[str, float | None]:
    """All six rules for one staged side; None means dropped for that rule."""
    data = row[side]
    width = data["width"]
    credit = data["credit"]
    short = _carry_forward(data["short_marks"])
    long = _carry_forward(data["long_marks"])
    spot = _carry_forward(row["spot"])
    settle = spot[max(MARK_HOURS)]
    value = {h: short[h] - long[h] for h in short if h in long}  # buyback cost
    hold = (credit - intrinsic_value(side, data["short_strike"],
                                     data["long_strike"], settle)) / width

    def first_trigger(breached: Any) -> int | None:
        for hour in TRIGGER_HOURS:
            if hour in value and breached(hour):
                return hour
        return None

    m1_hour = first_trigger(lambda h: value[h] >= 3.0 * credit)
    m2_hour = first_trigger(lambda h: value[h] >= 2.0 * credit)
    if side == "put":
        m3_hour = first_trigger(lambda h: spot[h] < data["short_strike"])
    else:
        m3_hour = first_trigger(lambda h: spot[h] > data["short_strike"])

    results: dict[str, float | None] = {"m0": hold}
    results["m1"] = hold if m1_hour is None \
        else (credit - value[m1_hour]) / width - EVENT_COST_FRAC
    results["m2"] = hold if m2_hour is None \
        else (credit - value[m2_hour]) / width - EVENT_COST_FRAC
    results["m3"] = hold if m3_hour is None \
        else (credit - value[m3_hour]) / width - EVENT_COST_FRAC

    if m1_hour is None:
        results["m4"] = hold
        results["m5"] = hold
    else:
        # M4: close the threatened spread at the mark, sell the opposite
        # side's staged spread (12:00 strikes) at the trigger-hour mark,
        # hold it to settle. Two events.
        opposite = "call" if side == "put" else "put"
        if opposite not in row:
            results["m4"] = None
        else:
            opp = row[opposite]
            opp_short = _carry_forward(opp["short_marks"])
            opp_long = _carry_forward(opp["long_marks"])
            if m1_hour not in opp_short or m1_hour not in opp_long:
                results["m4"] = None
            else:
                opp_credit = opp_short[m1_hour] - opp_long[m1_hour]
                opp_intrinsic = intrinsic_value(opposite, opp["short_strike"],
                                                opp["long_strike"], settle)
                results["m4"] = (credit - value[m1_hour] + opp_credit
                                 - opp_intrinsic) / width - 2.0 * EVENT_COST_FRAC
        # M5: buy back the short leg at its mark, keep the long leg to
        # settle and collect its intrinsic. One event.
        if side == "put":
            runner = max(0.0, data["long_strike"] - settle)
        else:
            runner = max(0.0, settle - data["long_strike"])
        results["m5"] = (credit - short[m1_hour] + runner) / width - EVENT_COST_FRAC
    return results


def evaluate_rules(session_row: dict[str, Any]) -> dict[str, dict[str, float | None]]:
    """rule -> side -> P&L as a fraction of the original width (None = dropped)."""
    per_rule: dict[str, dict[str, float | None]] = {rule: {} for rule in RULES}
    for side in SIDES:
        if side not in session_row:
            continue
        for rule, pnl in _evaluate_side(side, session_row).items():
            per_rule[rule][side] = pnl
    return per_rule


def derive_series(row: dict[str, Any], side: str, rule: str, pnl: float) -> dict[str, Any]:
    """One staged row re-expressed as a bars_universe payoff row."""
    data = row[side]
    payoff = round(pnl, 8)
    return {
        "session": row["session"],
        "symbol": f"{row['symbol']}-{side}-{rule}",
        "features": {
            "credit_frac": round(data["credit"] / data["width"], 8),
            "day_of_week": float(date.fromisoformat(row["session"]).weekday()),
            "underlying": row["spot"][str(MARK_HOURS[0])],
        },
        "next_open_to_close": payoff,
        "next_close_to_close": payoff,
        "spy_session_ret": round(
            row["spot"][str(MARK_HOURS[-1])] / row["spot"][str(MARK_HOURS[0])] - 1.0, 8),
    }


def write_spec(symbol: str, splits: dict, label: str, cost_bps: float) -> tuple[Path, str]:
    spec = {
        "name": f"odte-intervention-{symbol}-{label}",
        "universe": [symbol],
        "splits": splits,
        "min_trades": dict(MIN_SESSIONS),
        "min_sessions": dict(MIN_SESSIONS),
        "min_symbols": 1, "max_concentration": 1.0,
        "cost_bps": cost_bps, "target_sharpe": 0.75,
        "payoff": "next_open_to_close",
    }
    path = OUT_DIR / f"spec-{symbol}-{label}.json"
    raw = (json.dumps(spec, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def run_validator(spec: tuple[Path, str], data: Path, data_hash: str, split: str) -> dict:
    candidate = OUT_DIR / "c_base.py"
    completed = subprocess.run(
        [sys.executable, "-m", "research.validators.bars_universe",
         "--candidate", str(candidate), "--spec", str(spec[0]), "--spec-hash", spec[1],
         "--data", str(data), "--data-hash", data_hash, "--split", split],
        cwd=REPOSITORY_ROOT, text=True, capture_output=True, timeout=300,
    )
    line = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else "{}"
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {"status": "unparseable", "raw": line[:300]}


def _split_counts(rows: list[dict], splits: dict) -> dict[str, int]:
    return {split: sum(1 for r in rows if window[0] <= r["session"] <= window[1])
            for split, window in splits.items()}


def evaluate_cell(name: str, side: str, rule: str, rows: list[dict], drops: int,
                  data_path: Path, data_hash: str, splits: dict) -> dict[str, Any]:
    """One name x side x rule cell through the frozen gate set, both cost levels."""
    symbol = rows[0]["symbol"] if rows else f"{name}-0DTE-PATH-NM-{side}"
    counts = _split_counts(rows, splits)
    cell: dict[str, Any] = {
        "name": name, "side": side, "rule": rule,
        "sessions": len(rows), "drops": drops, "split_counts": counts,
    }
    if counts["discovery"] < MIN_SESSIONS["discovery"] \
            or counts["validation"] < MIN_SESSIONS["validation"]:
        cell["verdict"] = "UNTESTABLE"
        return cell

    for label, cost_bps in COST_LEVELS.items():
        spec = write_spec(symbol, splits, label, cost_bps)
        cost_frac = cost_bps / 10_000.0
        gates: dict[str, Any] = {}
        for split in ("discovery", "validation"):
            payload = run_validator(spec, data_path, data_hash, split)
            decomp = drift_decompose(rows, splits[split][0], splits[split][1], cost_frac)
            gates[split] = {"validator": payload.get("status"),
                            "net_bps_w": payload.get("net_portfolio_mean_bps"),
                            "sharpe": payload.get("net_sharpe"),
                            "failed": [k for k, ok in (payload.get("gates") or {}).items()
                                       if not ok],
                            **decomp}
        cell[label] = gates
    return cell


def verdict(cell: dict[str, Any], anchor: dict[str, Any]) -> dict[str, Any]:
    """The preregistered bar, judged at 0.5% cost against the M0 anchor."""
    if cell.get("verdict") == "UNTESTABLE":
        return {"verdict": "UNTESTABLE"}
    gates = cell["0.5pct"]
    conditions: dict[str, bool] = {}
    conditions["validator_passed_both_splits"] = all(
        gates[s]["validator"] == "passed" for s in ("discovery", "validation"))
    conditions["raw_lb_positive_both_splits"] = all(
        (gates[s].get("raw_weekly_bootstrap_lb") or -1) > 0
        for s in ("discovery", "validation"))
    conditions["residual_lb_positive_both_splits"] = all(
        (gates[s].get("residual_weekly_bootstrap_lb") or -1) > 0
        for s in ("discovery", "validation"))
    if cell["rule"] != "m0" and "0.5pct" in anchor:
        base = anchor["0.5pct"]
        beats: dict[str, bool] = {}
        for split in ("discovery", "validation"):
            net, base_net = gates[split].get("net_bps_w"), base[split].get("net_bps_w")
            lb = gates[split].get("raw_weekly_bootstrap_lb")
            base_lb = base[split].get("raw_weekly_bootstrap_lb")
            beats[f"{split}_net"] = net is not None and base_net is not None and net > base_net
            beats[f"{split}_raw_lb"] = lb is not None and base_lb is not None and lb > base_lb
        conditions["beats_m0"] = all(beats.values())
        conditions["beats_m0_detail"] = beats
    supported = all(v is True for k, v in conditions.items() if not k.endswith("_detail"))
    return {"verdict": "SUPPORTED" if supported else "NOT_SUPPORTED",
            "conditions": conditions}


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    DERIVED_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "c_base.py").write_text(textwrap.dedent(BASELINE).strip() + "\n",
                                       encoding="utf-8")

    results: list[dict[str, Any]] = []
    for name_dir in sorted(DATA_ROOT.iterdir()):
        manifest_path = name_dir / "manifest.json"
        if not manifest_path.is_file():
            continue
        name = name_dir.name
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        staged = [json.loads(line) for line in
                  (name_dir / "sessions.jsonl").read_text(encoding="utf-8").splitlines()]
        splits = manifest["suggested_splits"]
        print(f"=== {name}: {len(staged)} staged sessions ===", flush=True)

        # Evaluate every staged row once; the six rules share the marks.
        evaluated = [(row, evaluate_rules(row)) for row in staged]
        sides = sorted({side for row in staged for side in SIDES if side in row})
        anchors: dict[str, dict[str, Any]] = {}
        cells: dict[tuple[str, str], dict[str, Any]] = {}
        for side in sides:
            for rule in RULES:
                derived: list[dict[str, Any]] = []
                drops = 0
                for row, per_rule in evaluated:
                    if side not in row:
                        continue  # side never staged here; not part of this cell
                    pnl = per_rule[rule].get(side)
                    if pnl is None:
                        drops += 1  # preregistered fallback: out of THIS rule's series
                        continue
                    derived.append(derive_series(row, side, rule, pnl))
                if not derived:
                    continue
                payload = ("\n".join(json.dumps(r, sort_keys=True) for r in derived)
                           + "\n").encode("utf-8")
                tag = f"{name}-{side}-{rule}"
                data_path = DERIVED_DIR / f"{tag}.jsonl"
                data_path.write_bytes(payload)
                data_hash = hashlib.sha256(payload).hexdigest()
                cell = evaluate_cell(name, side, rule, derived, drops,
                                     data_path, data_hash, splits)
                cell["data_sha256"] = data_hash
                cells[(side, rule)] = cell
                if rule == "m0":
                    anchors[side] = cell
                if cell.get("verdict") == "UNTESTABLE":
                    print(f"  {side} {rule}: UNTESTABLE "
                          f"(sessions={cell['split_counts']} drops={drops})", flush=True)
                else:
                    half = cell["0.5pct"]
                    print(f"  {side} {rule}: "
                          f"disc net={half['discovery'].get('net_bps_w')} "
                          f"rawLB={half['discovery'].get('raw_weekly_bootstrap_lb')} | "
                          f"val net={half['validation'].get('net_bps_w')} "
                          f"rawLB={half['validation'].get('raw_weekly_bootstrap_lb')} "
                          f"drops={drops}", flush=True)

        for (side, rule), cell in cells.items():
            outcome = verdict(cell, anchors.get(side, {}))
            cell.update(outcome)
            if rule != "m0":
                print(f"  -> {side} {rule}: {outcome['verdict']}", flush=True)
            results.append(cell)
        print(flush=True)

    with (OUT_DIR / "results.jsonl").open("w", encoding="utf-8") as handle:
        for row in results:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    per_tape: dict[str, Any] = {}
    supported_cells: list[str] = []
    for cell in results:
        tape = f"{cell['name']}-{cell['side']}"
        entry = per_tape.setdefault(tape, {"m0_anchor": None, "rules": {}})
        if cell["rule"] == "m0":
            anchor_metrics = {"verdict": cell.get("verdict")}
            if "0.5pct" in cell:
                anchor_metrics["0.5pct"] = {
                    split: {"net_bps_w": cell["0.5pct"][split].get("net_bps_w"),
                            "raw_weekly_bootstrap_lb":
                                cell["0.5pct"][split].get("raw_weekly_bootstrap_lb"),
                            "residual_weekly_bootstrap_lb":
                                cell["0.5pct"][split].get("residual_weekly_bootstrap_lb"),
                            "validator": cell["0.5pct"][split].get("validator")}
                    for split in ("discovery", "validation")}
            entry["m0_anchor"] = anchor_metrics
        else:
            entry["rules"][cell["rule"]] = {
                "verdict": cell.get("verdict"),
                "conditions": cell.get("conditions"),
                "drops": cell["drops"]}
            if cell.get("verdict") == "SUPPORTED":
                supported_cells.append(f"{tape}-{cell['rule']}")
    summary = {
        "bar": "at 0.5% entry cost: validator pass on both splits + positive raw AND "
               "drift-neutral weekly bootstrap LBs on both splits + beats M0 on discovery "
               "net, validation net and both raw LBs",
        "cost_levels": COST_LEVELS,
        "min_sessions": MIN_SESSIONS,
        "tapes": per_tape,
        "supported_cells": sorted(supported_cells),
        "n_comparisons": sum(len(e["rules"]) for e in per_tape.values()),
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                                          encoding="utf-8")
    print("SUPPORTED cells: " + json.dumps(summary["supported_cells"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
