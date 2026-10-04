"""Walk-forward backtest with costs, baselines and the gates (spec: backtest).

Timeline on one series of candles with past-only features:

    [ fit window >= 3 years ][ validation ][ ---- out of sample ---- ]
                               (state count chosen here)   decisions here

Every REFIT_DAYS of candles the model is refit on an EXPANDING window ending
at the refit candle (nothing after it), warm-started from the previous
model, labels matched to the first fit's labels by state statistics, and
the filter re-run with the new model over the history up to the refit
candle so its state at that candle is legitimate; decisions from then on
use that model. A refit whose state statistics drift beyond tolerance
freezes new entries until the next refit.

Per candle, in order (all pure functions): switching step -> size cap from
sizing (Kelly p/b measured on the fit window's base-strategy trades; the
calibration gate reads only resolved past decisions) -> playbook decide ->
RISK check -> position. P&L = position_t x ret_{t+1}; costs = (fee +
slippage) x |position change| in notional terms, charged on the candle the
change happens.

Arms (identical data, costs, limits), all driven by the same filtered
probabilities and switching decisions:
    hmm         the system (spec arm 1): regime x Kelly sizing
    hmm_fixed   diagnostic: regime multipliers on a fixed cap of 1, no Kelly,
                so the regime layer is visible even when Kelly says f* <= 0
    trend_only  the base rule with no regime (best static playbook)
    buy_hold    fully invested
    + any gated arms passed in (Jev arms 2-4): {"name": {"gate": fn(t)->mult, "regime": bool, "kelly": bool}}

Gates (verbatim from the prompt, on the out-of-sample segment): Sharpe >
1.5, max drawdown < 15%, hit rate > 55%, t-stat > 2.0, beats buy-and-hold
AND the best static strategy after costs. All must pass. Evaluated on the
system arm for the verdict and reported for every other arm.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from research.hmm.data import load_candles
from research.hmm.features import FEATURES, VOL_WINDOW, build_features, feature_matrix
from research.hmm.filter import entropy, forward_filter
from research.hmm.model import FittedHMM, describe, fit_k, match_labels, select_and_fit
from research.hmm.playbooks import BookState, decide
from research.hmm.risk import RiskLimits, RiskState, check, roll_day
from research.hmm.sizing import CALIBRATION_WINDOW, calibration, size_cap
from research.hmm.switching import SwitchState, step

MISSION = Path(__file__).resolve().parents[1] / "missions" / "hmm-regime"
GATES = {"sharpe": 1.5, "max_drawdown": 0.15, "hit_rate": 0.55, "t_stat": 2.0}
DRIFT_SIGMA = 1.0
DRIFT_ROW_L1 = 0.30


@dataclass
class ArmResult:
    name: str
    equity: np.ndarray
    position: np.ndarray
    pnl: np.ndarray
    costs: np.ndarray
    trades: list[float] = field(default_factory=list)
    states: list[str | None] = field(default_factory=list)

    def metrics(self, candles_per_year: float) -> dict:
        r = self.pnl
        n = len(r)
        mean = float(r.mean())
        sd = float(r.std(ddof=1)) if n > 1 else 0.0
        sharpe = (mean / sd) * math.sqrt(candles_per_year) if sd > 0 else 0.0
        t = (mean / sd) * math.sqrt(n) if sd > 0 else 0.0
        eq = self.equity
        dd = float((1 - eq / np.maximum.accumulate(eq)).max()) if len(eq) else 0.0
        wins = sum(1 for x in self.trades if x > 0)
        return {
            "arm": self.name, "candles": n, "total_return": float(eq[-1] / eq[0] - 1) if len(eq) else 0.0,
            "ann_return": float((eq[-1] / eq[0]) ** (candles_per_year / max(n, 1)) - 1) if len(eq) else 0.0,
            "sharpe": sharpe, "max_drawdown": dd, "t_stat": t, "trades": len(self.trades),
            "hit_rate": (wins / len(self.trades)) if self.trades else float("nan"),
            "avg_trade_bps": float(np.mean(self.trades) * 1e4) if self.trades else float("nan"),
            "costs_paid": float(self.costs.sum()), "time_in_market": float((self.position > 0).mean()),
        }


def candles_per_year(timeframe: str) -> float:
    return {"hour": 24 * 365.0, "day": 252.0}[timeframe]


def cooldown_for(timeframe: str) -> int:
    return {"hour": 6, "day": 3}[timeframe]


def _base_strategy_pb(trend_z: np.ndarray, rvol: np.ndarray, close: np.ndarray) -> tuple[float, float]:
    """Measured p (win rate) and b (avg win / avg loss) of the base TREND rule on a window."""
    book = BookState()
    trades: list[float] = []
    entry = None
    for i in range(len(trend_z)):
        prev = book.position
        book, _order = decide(book, close=float(close[i]), rvol=float(rvol[i]), trend_z=float(trend_z[i]), state="CALM_UP", size_cap=1.0)
        if prev == 0 and book.position > 0:
            entry = float(close[i])
        elif prev > 0 and book.position == 0 and entry:
            trades.append(float(close[i]) / entry - 1)
            entry = None
    if len(trades) < 10:
        return 0.5, 1.0
    wins = [x for x in trades if x > 0]
    losses = [-x for x in trades if x <= 0]
    p = len(wins) / len(trades)
    b = (np.mean(wins) / np.mean(losses)) if wins and losses and np.mean(losses) > 0 else 1.0
    return float(p), float(b)


def _drifted(first: FittedHMM, current: FittedHMM, labels_now: tuple[str, ...]) -> tuple[bool, str]:
    by_label_first = {s["label"]: s for s in first.state_stats}
    pooled_sigma = float(np.mean([s["vol"] for s in first.state_stats])) or 1e-9
    for i, lab in enumerate(labels_now):
        ref = by_label_first.get(lab)
        if ref is None:
            return True, f"state {lab} has no counterpart in the first fit"
        cur = current.state_stats[i]
        if abs(cur["mean_ret"] - ref["mean_ret"]) > DRIFT_SIGMA * pooled_sigma or abs(cur["vol"] - ref["vol"]) > DRIFT_SIGMA * pooled_sigma:
            return True, f"state {lab} statistics moved beyond {DRIFT_SIGMA} sigma"
    order_first = {lab: i for i, lab in enumerate(first.labels)}
    for i, lab in enumerate(labels_now):
        row_now = np.array([current.transmat[i, j] for j, _ in enumerate(labels_now)])
        row_first = np.array([first.transmat[order_first[lab], order_first[l2]] for l2 in labels_now])
        if np.abs(row_now - row_first).sum() > DRIFT_ROW_L1:
            return True, f"transition row {lab} moved by {np.abs(row_now - row_first).sum():.2f} L1"
    return False, "ok"


def run_backtest(symbol: str, timeframe: str, *, fit_end: str, val_end: str, fee_bps: float, slip_bps: float, refit_days: int = 30,
                 capital: float = 100_000.0, gates: dict[str, dict] | None = None, limits: RiskLimits | None = None) -> dict:
    candles = load_candles(symbol, timeframe)
    feats = build_features(candles)
    clean = feats.dropna(subset=FEATURES)
    X, ts = feature_matrix(feats)
    idx = clean.index.to_numpy()
    close = candles["close"].to_numpy(dtype=float)[idx]
    ret = np.log(candles["close"] / candles["close"].shift(1)).to_numpy()[idx]
    ret_next = np.append(ret[1:], np.nan)
    rvol_raw = np.log(candles["close"] / candles["close"].shift(1)).rolling(VOL_WINDOW).std(ddof=0).to_numpy()[idx]
    trend_z = clean["trend"].to_numpy()
    cpy = candles_per_year(timeframe)
    per_day = 24 if timeframe == "hour" else 1
    refit_every = refit_days * per_day
    limits = limits or RiskLimits()
    cost_rate = (fee_bps + slip_bps) / 1e4

    i_fit = int((ts < pd.Timestamp(fit_end, tz="UTC")).sum())
    i_val = int((ts < pd.Timestamp(val_end, tz="UTC")).sum())
    n = len(X)
    first = select_and_fit(X[:i_fit], X[i_fit:i_val], ret[:i_fit])
    k = first.n_states
    kelly_p, kelly_b = _base_strategy_pb(trend_z[:i_fit], rvol_raw[:i_fit], close[:i_fit])

    arm_cfg: dict[str, tuple] = {
        "hmm": (True, True, None),
        "hmm_fixed": (True, False, None),
        "trend_only": (False, False, None),
        "buy_hold": (None, None, None),
    }
    for name, g in (gates or {}).items():
        arm_cfg[name] = (g.get("regime", True), g.get("kelly", True), g["gate"])
    first_day = str(ts.iloc[i_val].date())
    arms = {name: {"equity": np.ones(n) * capital, "position": np.zeros(n), "pnl": np.zeros(n), "costs": np.zeros(n), "trades": [], "states": [None] * n,
                   "book": BookState(), "entry": None, "risk": RiskState(equity_high=capital, day_start_equity=capital, day=first_day), "gate_log": []}
            for name in arm_cfg}
    sw = SwitchState()
    model = first
    labels = first.labels
    warm = first.to_hmmlearn()
    prior = forward_filter(model.startprob, model.transmat, model.means, model.covars, X[:i_val])[-1]
    frozen, frozen_reason = False, ""
    refit_log: list[dict] = []
    decisions_pred: list[float] = []
    decisions_real: list[float] = []
    last_active_idx: int | None = None
    switches = 0
    size_log: list[float] = []
    cal = None

    for t in range(i_val, n - 1):
        if (t - i_val) % refit_every == 0 and t > i_val:
            try:
                m = fit_k(X[:t], k, warm=warm, restarts=1)
                warm = m
                cur = describe(m, X[:t], ret[:t])
                labels = match_labels(first, cur)
                model = FittedHMM(**{**cur.__dict__, "labels": labels})
                frozen, frozen_reason = _drifted(first, model, labels)
                prior = forward_filter(model.startprob, model.transmat, model.means, model.covars, X[:t])[-1]
                refit_log.append({"t": str(ts.iloc[t]), "frozen": frozen, "reason": frozen_reason, "labels": labels})
            except Exception as exc:  # noqa: BLE001
                frozen, frozen_reason = True, f"refit failed: {exc}"
                refit_log.append({"t": str(ts.iloc[t]), "frozen": True, "reason": frozen_reason})
        row = forward_filter(model.startprob, model.transmat, model.means, model.covars, X[t:t + 1], prior=prior)[0]
        prior = row
        nxt = row @ model.transmat
        ent = float(entropy(row[None, :])[0])
        sw, d = step(sw, row, labels, nxt, index=t, cooldown=cooldown_for(timeframe))
        if d.switched:
            switches += 1
        if last_active_idx is not None:
            decisions_real.append(1.0 if int(np.argmax(row)) == last_active_idx else 0.0)
        last_active_idx = int(np.argmax(row))
        decisions_pred.append(float(row[last_active_idx]))
        pred_resolved = decisions_pred[:len(decisions_real)]
        cal = calibration(np.array(pred_resolved[-CALIBRATION_WINDOW:]), np.array(decisions_real[-CALIBRATION_WINDOW:])) if len(decisions_real) >= 20 else None
        calibrated = bool(cal and cal.ok)
        day = str(ts.iloc[t].date())
        for name, (use_regime, use_kelly, gate) in arm_cfg.items():
            a = arms[name]
            prev_pos = a["position"][t - 1] if t > i_val else 0.0
            eq_prev = a["equity"][t - 1] if t > i_val else capital
            if use_regime is None:
                position = 1.0
                active = d.active
            else:
                gate_mult = 1.0 if gate is None else float(gate(t))
                if gate is not None:
                    a["gate_log"].append(gate_mult)
                if use_regime:
                    active, switch_mult = d.active, d.size_multiplier
                else:
                    active, switch_mult = "CALM_UP", 1.0
                if use_kelly:
                    cap = size_cap(p_active=float(row.max()), entropy=ent, kelly_p=kelly_p, kelly_b=kelly_b, calibrated=calibrated, switch_multiplier=switch_mult * gate_mult)
                else:
                    cap = 1.0 * switch_mult * gate_mult
                if use_regime and frozen and a["book"].position == 0:
                    cap = 0.0
                if name == "hmm":
                    size_log.append(cap)
                roll_day(a["risk"], day, eq_prev)
                book, order = decide(a["book"], close=float(close[t]), rvol=float(rvol_raw[t]), trend_z=float(trend_z[t]), state=active, size_cap=cap)
                verdict = check(a["risk"], limits, requested_position=order.target_position, active_state=active, equity=eq_prev, stale_candles=0, notional=eq_prev)
                position = verdict.allowed_position if verdict.action in ("ok", "cap", "approval") else 0.0
                if position != book.position:
                    book = BookState(position=position, entry_price=book.entry_price, stop_price=book.stop_price, stopped_out=book.stopped_out)
                a["book"] = book
            cost = cost_rate * abs(position - prev_pos)
            pnl = position * (math.exp(ret_next[t]) - 1) - cost
            a["position"][t] = position
            a["costs"][t] = cost
            a["pnl"][t] = pnl
            a["equity"][t] = eq_prev * (1 + pnl)
            a["states"][t] = active
            if use_regime is not None:
                if prev_pos == 0 and position > 0:
                    a["entry"] = float(close[t])
                elif prev_pos > 0 and position == 0 and a["entry"]:
                    a["trades"].append(float(close[t]) / a["entry"] - 1 - 2 * cost_rate)
                    a["entry"] = None

    sl = slice(i_val, n - 1)
    results: dict[str, dict] = {}
    for name, a in arms.items():
        res = ArmResult(name, a["equity"][sl], a["position"][sl], a["pnl"][sl], a["costs"][sl], a["trades"], a["states"][sl])
        results[name] = res.metrics(cpy)
        results[name]["equity_curve"] = res.equity
        results[name]["states"] = res.states
        results[name]["pnl_series"] = res.pnl
        if a["gate_log"]:
            results[name]["gate_mean"] = float(np.mean(a["gate_log"]))
            results[name]["gate_zero_share"] = float(np.mean(np.array(a["gate_log"]) == 0.0))
    st = np.array(arms["hmm"]["states"][sl], dtype=object)
    per_state = {}
    for lab in sorted({s for s in st if s}, key=str):
        mask = st == lab
        per_state[lab] = {"candles": int(mask.sum()), "share": float(mask.mean()), "pnl_bps_per_candle": float(arms["hmm_fixed"]["pnl"][sl][mask].mean() * 1e4),
                          "time_in_market_fixed": float((arms["hmm_fixed"]["position"][sl][mask] > 0).mean()), "next_ret_bps": float(np.nanmean(ret_next[sl][mask]) * 1e4)}
    years = pd.Series(arms["hmm"]["pnl"][sl], index=ts.iloc[sl].to_numpy()).groupby(lambda x: x.year).agg(["sum", "count"])

    def gate_eval(m: dict) -> dict:
        return {"sharpe": m["sharpe"] > GATES["sharpe"], "max_drawdown": m["max_drawdown"] < GATES["max_drawdown"],
                "hit_rate": (m["hit_rate"] > GATES["hit_rate"]) if not math.isnan(m["hit_rate"]) else False, "t_stat": m["t_stat"] > GATES["t_stat"],
                "beats_buy_hold": m["total_return"] > results["buy_hold"]["total_return"], "beats_best_static": m["total_return"] > results["trend_only"]["total_return"]}

    gate_results = {name: gate_eval(results[name]) for name in results if name not in ("buy_hold", "trend_only")}
    verdict_gates = gate_results["hmm"]
    return {
        "symbol": symbol, "timeframe": timeframe, "fit_end": fit_end, "val_end": val_end, "oos_start": str(ts.iloc[i_val]), "oos_end": str(ts.iloc[n - 2]),
        "oos_candles": int(n - 1 - i_val), "n_states": k, "labels": first.labels, "kelly_p": kelly_p, "kelly_b": kelly_b, "costs_bps_per_side": fee_bps + slip_bps,
        "results": results, "per_state": per_state, "by_year": {int(y): {"pnl": float(v["sum"]), "candles": int(v["count"])} for y, v in years.iterrows()},
        "switches": switches, "refits": refit_log, "frozen_refits": sum(1 for r in refit_log if r["frozen"]), "gates": verdict_gates,
        "all_gates_pass": all(verdict_gates.values()), "gates_by_arm": {name: {"gates": g, "all_pass": all(g.values())} for name, g in gate_results.items()},
        "mean_size_cap": float(np.mean(size_log)) if size_log else 0.0, "calibration_last": cal.__dict__ if cal else None,
    }


def _tbl(rows: list[dict]) -> str:
    if not rows:
        return "(none)\n"
    cols = list(rows[0].keys())
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    return "\n".join(out) + "\n"


def write_report(res: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, m in res["results"].items():
        rows.append({"arm": name, "total_return": f"{m['total_return']:+.1%}", "ann_return": f"{m['ann_return']:+.1%}", "sharpe": f"{m['sharpe']:.2f}", "max_dd": f"{m['max_drawdown']:.1%}",
                     "t_stat": f"{m['t_stat']:.2f}", "trades": m["trades"], "hit_rate": "" if math.isnan(m["hit_rate"]) else f"{m['hit_rate']:.1%}",
                     "avg_trade_bps": "" if math.isnan(m["avg_trade_bps"]) else f"{m['avg_trade_bps']:+.0f}", "costs_paid": f"{m['costs_paid']:.3f}", "time_in_mkt": f"{m['time_in_market']:.0%}",
                     "gate_mean": f"{m['gate_mean']:.2f}" if "gate_mean" in m else ""})
    g = res["gates"]
    thresholds = {"sharpe": f"> {GATES['sharpe']}", "max_drawdown": f"< {GATES['max_drawdown']}", "hit_rate": f"> {GATES['hit_rate']}", "t_stat": f"> {GATES['t_stat']}", "beats_buy_hold": "after costs", "beats_best_static": "after costs"}
    gate_rows = [{"gate": k, "threshold": thresholds[k], "passed": "PASS" if v else "FAIL"} for k, v in g.items()]
    by_arm = [{"arm": k, **{gg: ("PASS" if vv else "FAIL") for gg, vv in val["gates"].items()}, "all": "PASS" if val["all_pass"] else "FAIL"} for k, val in res.get("gates_by_arm", {}).items()]
    md = [f"# Walk-forward backtest: {res['symbol']} {res['timeframe']}\n",
          f"Generated {datetime.now(timezone.utc).isoformat(timespec='minutes')}. Out of sample {res['oos_start'][:10]} to {res['oos_end'][:10]} ({res['oos_candles']:,} candles) after fit to {res['fit_end']} and validation to {res['val_end']}. "
          f"{res['n_states']} states ({', '.join(res['labels'])}); refits every 30 days on an expanding window, {len(res['refits'])} refits of which {res['frozen_refits']} froze entries for drift. "
          f"Costs {res['costs_bps_per_side']:.0f} bps per side. Base-strategy Kelly inputs measured on the fit window: p={res['kelly_p']:.2f}, b={res['kelly_b']:.2f} (f* = {(res['kelly_b'] * res['kelly_p'] - (1 - res['kelly_p'])) / max(res['kelly_b'], 1e-9):+.3f}). "
          f"Mean size cap of the system {res['mean_size_cap']:.2f}; state switches {res['switches']}.\n",
          "## Arms (identical data, costs, limits)\n", _tbl(rows),
          "## Gates (verbatim), on the system arm out of sample\n", _tbl(gate_rows),
          f"**{'ALL GATES PASS' if res['all_gates_pass'] else 'REJECTED: the system does not clear its own gates'}.**\n",
          "## Gates by arm\n", _tbl(by_arm),
          "## By state (out of sample): share, the fixed-cap arm's P&L while in the state, and the raw next-candle return\n",
          _tbl([{"state": k, **{kk: (f"{vv:.3f}" if isinstance(vv, float) else vv) for kk, vv in v.items()}} for k, v in res["per_state"].items()]),
          "## System P&L by year\n", _tbl([{"year": y, "pnl": f"{v['pnl']:+.3f}", "candles": v["candles"]} for y, v in res["by_year"].items()]),
          "## Refits\n", _tbl([{"t": r["t"][:10], "frozen": r["frozen"], "reason": r["reason"]} for r in res["refits"]][:60])]
    if res.get("calibration_last"):
        c = res["calibration_last"]
        md.append(f"Last rolling calibration of P(active state) vs persistence: Brier {c['brier']:.3f}, ECE {c['ece']:.3f}, n {c['n']} ({'calibrated' if c['ece'] <= 0.05 and c['n'] >= 200 else 'NOT calibrated: sizing stayed at the floor'}).\n")
    (out_dir / "BACKTEST.md").write_text("\n".join(md), encoding="utf-8")
    slim = {k: v for k, v in res.items() if k != "results"}
    slim["results"] = {k: {kk: vv for kk, vv in v.items() if kk not in ("equity_curve", "states", "pnl_series")} for k, v in res["results"].items()}
    (out_dir / "backtest.json").write_text(json.dumps(slim, indent=1, default=str), encoding="utf-8")
    return out_dir / "BACKTEST.md"


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTC/USD")
    ap.add_argument("--timeframe", default="hour")
    ap.add_argument("--fit-end", required=True)
    ap.add_argument("--val-end", required=True)
    ap.add_argument("--fee-bps", type=float, default=10.0)
    ap.add_argument("--slip-bps", type=float, default=5.0)
    ap.add_argument("--jev", action="store_true", help="add the three Jev arms (calls the API; cached)")
    args = ap.parse_args()
    gates = None
    if args.jev:
        from research.hmm.jev_gate import JevGate

        candles = load_candles(args.symbol, args.timeframe)
        feats = build_features(candles)
        clean = feats.dropna(subset=FEATURES)
        close = candles["close"].to_numpy(dtype=float)[clean.index.to_numpy()]
        cache = MISSION / "fits" / f"{args.symbol.replace('/', '-')}_{args.timeframe}"
        plain = JevGate(args.symbol, args.timeframe, clean, close, cache_dir=cache, confidence_gate=False)
        conf = JevGate(args.symbol, args.timeframe, clean, close, cache_dir=cache, confidence_gate=True).share_cache_with(plain)
        gates = {"jev_only": {"gate": plain, "regime": False, "kelly": False},
                 "hmm_jev": {"gate": plain, "regime": True, "kelly": False},
                 "hmm_jev_conf": {"gate": conf, "regime": True, "kelly": False}}
    res = run_backtest(args.symbol, args.timeframe, fit_end=args.fit_end, val_end=args.val_end, fee_bps=args.fee_bps, slip_bps=args.slip_bps, gates=gates)
    if gates:
        res["jev"] = {"calls": gates["jev_only"]["gate"].calls, "unavailable": gates["jev_only"]["gate"].unavailable, "model_versions": sorted(gates["jev_only"]["gate"].model_versions)}
    out = write_report(res, MISSION / "fits" / f"{args.symbol.replace('/', '-')}_{args.timeframe}")
    print(open(out, encoding="utf-8").read()[:3500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
