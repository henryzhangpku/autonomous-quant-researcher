"""Walk-forward backtest with costs, baselines and the gates (spec: backtest).

Timeline on one series of candles with past-only features:

    [ fit window >= 3 years ][ validation ][ ---- out of sample ---- ]
                               (state count chosen here)   decisions here

Every REFIT_DAYS of candles the model is refit on an EXPANDING window ending
at the refit candle (nothing after it), labels are matched to the first
fit's labels by state statistics, and the filter is re-run with the new
model over the history up to the refit candle so its state at that candle
is legitimate; decisions from then on use that model. A refit whose state
statistics drift beyond tolerance freezes new entries until the next refit.

Per candle, in order (all pure functions): switching step -> size cap from
sizing (Kelly p/b measured on the fit window's base-strategy trades; the
calibration gate reads only resolved past decisions) -> playbook decide ->
RISK check -> position. P&L = position_t x ret_{t+1}; costs = (fee +
slippage) x |position change| in notional terms, charged on the candle the
change happens.

Arms (identical data, costs, limits):
    hmm        : the system (arm 1 of the spec)
    trend_only : the base strategy with multiplier 1, no regime (best static playbook)
    buy_hold   : fully invested
Jev arms (2-4) plug in through `gate_fn`, a callable returning a multiplier
per candle from a snapshot; absent, they are reported as not run.

Gates (verbatim from the prompt, on the out-of-sample segment): Sharpe >
1.5, max drawdown < 15%, hit rate > 55%, t-stat > 2.0, beats buy-and-hold
AND the best static strategy after costs. All must pass.
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
from research.hmm.sizing import CALIBRATION_WINDOW, calibration, kelly_fraction, size_cap
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
    trades: list[float] = field(default_factory=list)  # per round-trip return
    states: list[str | None] = field(default_factory=list)

    def metrics(self, candles_per_year: float) -> dict:
        r = self.pnl
        n = len(r)
        mean, sd = float(r.mean()), float(r.std(ddof=1)) if n > 1 else 0.0
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


def _base_strategy_pb(trend_z: np.ndarray, ret_next: np.ndarray, rvol: np.ndarray, close: np.ndarray) -> tuple[float, float]:
    """Measured p (win rate) and b (avg win / avg loss) of the base TREND rule on a window."""
    book = BookState()
    trades: list[float] = []
    entry = None
    for i in range(len(trend_z)):
        prev = book.position
        book, order = decide(book, close=float(close[i]), rvol=float(rvol[i]), trend_z=float(trend_z[i]), state="CALM_UP", size_cap=1.0)
        if prev == 0 and book.position > 0:
            entry = float(close[i])
        elif prev > 0 and book.position == 0 and entry:
            trades.append(float(close[i]) / entry - 1)
            entry = None
    if len(trades) < 10:
        return 0.5, 1.0
    wins = [t for t in trades if t > 0]
    losses = [-t for t in trades if t <= 0]
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
    # transition rows, matched by label
    order_first = {lab: i for i, lab in enumerate(first.labels)}
    for i, lab in enumerate(labels_now):
        row_now = np.array([current.transmat[i, j] for j, _ in enumerate(labels_now)])
        row_first = np.array([first.transmat[order_first[lab], order_first[l2]] for l2 in labels_now])
        if np.abs(row_now - row_first).sum() > DRIFT_ROW_L1:
            return True, f"transition row {lab} moved by {np.abs(row_now - row_first).sum():.2f} L1"
    return False, "ok"


def run_backtest(symbol: str, timeframe: str, *, fit_end: str, val_end: str, fee_bps: float, slip_bps: float, refit_days: int = 30,
                 capital: float = 100_000.0, gate_fn=None, limits: RiskLimits | None = None) -> dict:
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
    kelly_p, kelly_b = _base_strategy_pb(trend_z[:i_fit], ret_next[:i_fit], rvol_raw[:i_fit], close[:i_fit])

    # arms
    arms = {name: {"equity": np.ones(n) * capital, "position": np.zeros(n), "pnl": np.zeros(n), "costs": np.zeros(n), "trades": [], "states": [None] * n}
            for name in ("hmm", "trend_only", "buy_hold")}
    books = {"hmm": BookState(), "trend_only": BookState()}
    entries = {"hmm": None, "trend_only": None}
    sw = SwitchState()
    risk = RiskState(equity_high=capital, day_start_equity=capital, day=str(ts.iloc[i_val].date()))
    model = first
    labels = first.labels
    warm = first.to_hmmlearn()
    probs_hist = forward_filter(model.startprob, model.transmat, model.means, model.covars, X[:i_val])
    prior = probs_hist[-1]
    frozen, frozen_reason = False, ""
    refit_log = []
    decisions_pred: list[float] = []
    decisions_real: list[float] = []
    last_active_idx: int | None = None
    switches = 0
    size_log = []

    for t in range(i_val, n - 1):
        # refit on schedule, expanding window ending at t (nothing after t)
        if (t - i_val) % refit_every == 0 and t > i_val:
            try:
                m = fit_k(X[:t], k, warm=warm, restarts=1)
                warm = m
                cur = describe(m, X[:t], ret[:t])
                labels = match_labels(first, cur)
                model = FittedHMM(**{**cur.__dict__, "labels": labels})
                frozen, frozen_reason = _drifted(first, model, labels)
                hist = forward_filter(model.startprob, model.transmat, model.means, model.covars, X[:t])
                prior = hist[-1]
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
        # calibration record: P(active) at t vs whether argmax persists at t+1 (resolved next candle)
        if last_active_idx is not None:
            decisions_real.append(1.0 if int(np.argmax(row)) == last_active_idx else 0.0)
        last_active_idx = int(np.argmax(row))
        decisions_pred.append(float(row[last_active_idx]))
        pred_resolved = decisions_pred[:len(decisions_real)]
        cal = calibration(np.array(pred_resolved[-CALIBRATION_WINDOW:]), np.array(decisions_real[-CALIBRATION_WINDOW:])) if len(decisions_real) >= 20 else None
        calibrated = bool(cal and cal.ok)
        gate_mult = 1.0 if gate_fn is None else float(gate_fn(t))
        cap = size_cap(p_active=float(row.max()), entropy=ent, kelly_p=kelly_p, kelly_b=kelly_b, calibrated=calibrated, switch_multiplier=d.size_multiplier * gate_mult)
        if frozen and books["hmm"].position == 0:
            cap = 0.0  # drift freeze: no NEW entries
        day = str(ts.iloc[t].date())
        eq_prev = arms["hmm"]["equity"][t - 1] if t > 0 else capital
        roll_day(risk, day, eq_prev)
        # hmm arm
        book, order = decide(books["hmm"], close=float(close[t]), rvol=float(rvol_raw[t]), trend_z=float(trend_z[t]), state=d.active, size_cap=cap)
        verdict = check(risk, limits, requested_position=order.target_position, active_state=d.active, equity=eq_prev, stale_candles=0, notional=eq_prev)
        pos = verdict.allowed_position if verdict.action in ("ok", "cap", "approval") else 0.0
        if pos != book.position:
            book = BookState(position=pos, entry_price=book.entry_price, stop_price=book.stop_price, stopped_out=book.stopped_out)
        books["hmm"] = book
        size_log.append(cap)
        # trend-only arm
        tbook, torder = decide(books["trend_only"], close=float(close[t]), rvol=float(rvol_raw[t]), trend_z=float(trend_z[t]), state="CALM_UP", size_cap=1.0)
        books["trend_only"] = tbook
        for name, position in (("hmm", pos), ("trend_only", tbook.position), ("buy_hold", 1.0)):
            a = arms[name]
            prev_pos = a["position"][t - 1] if t > i_val else 0.0
            cost = cost_rate * abs(position - prev_pos)
            pnl = position * (math.exp(ret_next[t]) - 1) - cost
            a["position"][t] = position
            a["costs"][t] = cost
            a["pnl"][t] = pnl
            a["equity"][t] = (a["equity"][t - 1] if t > i_val else capital) * (1 + pnl)
            a["states"][t] = d.active
            if name in entries:
                if prev_pos == 0 and position > 0:
                    entries[name] = float(close[t])
                elif prev_pos > 0 and position == 0 and entries[name]:
                    a["trades"].append(float(close[t]) / entries[name] - 1 - 2 * cost_rate)
                    entries[name] = None
    sl = slice(i_val, n - 1)
    results = {}
    for name, a in arms.items():
        res = ArmResult(name, a["equity"][sl], a["position"][sl], a["pnl"][sl], a["costs"][sl], a["trades"], a["states"][sl])
        results[name] = res.metrics(cpy)
        results[name]["equity_curve"] = res.equity
        results[name]["states"] = res.states
        results[name]["pnl_series"] = res.pnl
    # per-state P&L of the system
    st = np.array(arms["hmm"]["states"][sl], dtype=object)
    per_state = {}
    for lab in sorted({s for s in st if s}, key=str):
        mask = st == lab
        per_state[lab] = {"candles": int(mask.sum()), "share": float(mask.mean()), "pnl_bps_per_candle": float(arms["hmm"]["pnl"][sl][mask].mean() * 1e4), "time_in_market": float((arms["hmm"]["position"][sl][mask] > 0).mean())}
    years = pd.Series(arms["hmm"]["pnl"][sl], index=ts.iloc[sl].to_numpy()).groupby(lambda x: x.year).agg(["sum", "count"])
    gates = {}
    m = results["hmm"]
    gates["sharpe"] = m["sharpe"] > GATES["sharpe"]
    gates["max_drawdown"] = m["max_drawdown"] < GATES["max_drawdown"]
    gates["hit_rate"] = (m["hit_rate"] > GATES["hit_rate"]) if not math.isnan(m["hit_rate"]) else False
    gates["t_stat"] = m["t_stat"] > GATES["t_stat"]
    gates["beats_buy_hold"] = m["total_return"] > results["buy_hold"]["total_return"]
    gates["beats_best_static"] = m["total_return"] > results["trend_only"]["total_return"]
    return {
        "symbol": symbol, "timeframe": timeframe, "fit_end": fit_end, "val_end": val_end, "oos_start": str(ts.iloc[i_val]), "oos_end": str(ts.iloc[n - 2]),
        "oos_candles": int(n - 1 - i_val), "n_states": k, "labels": first.labels, "kelly_p": kelly_p, "kelly_b": kelly_b, "costs_bps_per_side": fee_bps + slip_bps,
        "results": results, "per_state": per_state, "by_year": {int(y): {"pnl": float(v["sum"]), "candles": int(v["count"])} for y, v in years.iterrows()},
        "switches": switches, "refits": refit_log, "frozen_refits": sum(1 for r in refit_log if r["frozen"]), "gates": gates, "all_gates_pass": all(gates.values()),
        "mean_size_cap": float(np.mean(size_log)) if size_log else 0.0, "calibration_last": cal.__dict__ if cal else None,
    }


def write_report(res: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for name in ("hmm", "trend_only", "buy_hold"):
        m = res["results"][name]
        rows.append({"arm": name, "total_return": f"{m['total_return']:+.1%}", "ann_return": f"{m['ann_return']:+.1%}", "sharpe": f"{m['sharpe']:.2f}", "max_dd": f"{m['max_drawdown']:.1%}",
                     "t_stat": f"{m['t_stat']:.2f}", "trades": m["trades"], "hit_rate": "" if math.isnan(m["hit_rate"]) else f"{m['hit_rate']:.1%}", "avg_trade_bps": "" if math.isnan(m["avg_trade_bps"]) else f"{m['avg_trade_bps']:+.0f}",
                     "costs_paid": f"{m['costs_paid']:.3f}", "time_in_mkt": f"{m['time_in_market']:.0%}"})
    def tbl(rs):
        if not rs:
            return "(none)\n"
        cols = list(rs[0].keys())
        out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
        for r in rs:
            out.append("| " + " | ".join(str(r[c]) for c in cols) + " |")
        return "\n".join(out) + "\n"
    g = res["gates"]
    gate_rows = [{"gate": k, "threshold": (f"> {GATES[k]}" if k in ("sharpe", "t_stat", "hit_rate") else (f"< {GATES[k]}" if k == "max_drawdown" else "after costs")), "passed": "PASS" if v else "FAIL"} for k, v in g.items()]
    md = [f"# Walk-forward backtest: {res['symbol']} {res['timeframe']}\n",
          f"Generated {datetime.now(timezone.utc).isoformat(timespec='minutes')}. Out of sample {res['oos_start'][:10]} to {res['oos_end'][:10]} ({res['oos_candles']:,} candles) after fit to {res['fit_end']} and validation to {res['val_end']}. "
          f"{res['n_states']} states ({', '.join(res['labels'])}); refits every 30 days on an expanding window, {len(res['refits'])} refits of which {res['frozen_refits']} froze entries for drift. "
          f"Costs {res['costs_bps_per_side']:.0f} bps per side. Base-strategy Kelly inputs measured on the fit window: p={res['kelly_p']:.2f}, b={res['kelly_b']:.2f}. Mean size cap {res['mean_size_cap']:.2f}; state switches {res['switches']}.\n",
          "## Arms (identical data, costs, limits)\n", tbl(rows),
          "## Gates (verbatim), on the HMM arm out of sample\n", tbl(gate_rows),
          f"**{'ALL GATES PASS' if res['all_gates_pass'] else 'REJECTED: the system does not clear its own gates'}.**\n",
          "## System P&L by state (out of sample)\n", tbl([{"state": k, **{kk: (f"{vv:.3f}" if isinstance(vv, float) else vv) for kk, vv in v.items()}} for k, v in res["per_state"].items()]),
          "## System P&L by year\n", tbl([{"year": y, "pnl": f"{v['pnl']:+.3f}", "candles": v["candles"]} for y, v in res["by_year"].items()]),
          "## Refits\n", tbl([{"t": r["t"][:10], "frozen": r["frozen"], "reason": r["reason"]} for r in res["refits"]][:60]),
          ]
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
    args = ap.parse_args()
    res = run_backtest(args.symbol, args.timeframe, fit_end=args.fit_end, val_end=args.val_end, fee_bps=args.fee_bps, slip_bps=args.slip_bps)
    out = write_report(res, MISSION / "fits" / f"{args.symbol.replace('/', '-')}_{args.timeframe}")
    print(open(out, encoding="utf-8").read()[:3000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
