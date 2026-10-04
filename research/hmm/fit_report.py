"""Phase-2 review artifact: fit the HMM on the first years of a series, select
the state count, label the states, and forward-filter the rest. Writes
<mission>/fits/<SYMBOL>_<tf>/ with REPORT.md, filtered.csv.gz and a chart.
No trading logic here; this is what the reviewer reads before phase 3.

Run: uv run python -m research.hmm.fit_report --symbol BTC/USD --timeframe hour --fit-end 2024-01-01 --val-end 2024-07-01
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from research.hmm.data import load_candles
from research.hmm.features import FEATURES, build_features, feature_matrix
from research.hmm.filter import entropy, forward_filter
from research.hmm.model import select_and_fit

MISSION = Path(__file__).resolve().parents[1] / "missions" / "hmm-regime"


def md_table(frame: pd.DataFrame) -> str:
    cols = [str(c) for c in frame.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, row in frame.iterrows():
        lines.append("| " + " | ".join(f"{v:.4g}" if isinstance(v, float) else str(v) for v in row.tolist()) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTC/USD")
    ap.add_argument("--timeframe", default="hour")
    ap.add_argument("--start", default=None)
    ap.add_argument("--fit-end", required=True, help="fit window = [start, fit-end)")
    ap.add_argument("--val-end", required=True, help="validation = [fit-end, val-end); the rest is filtered only")
    args = ap.parse_args()

    candles = load_candles(args.symbol, args.timeframe, start=args.start)
    feats = build_features(candles)
    X, ts = feature_matrix(feats)
    raw_ret = np.log(candles["close"] / candles["close"].shift(1)).reindex(feats.dropna(subset=FEATURES).index).to_numpy()
    fit_end = pd.Timestamp(args.fit_end, tz="UTC")
    val_end = pd.Timestamp(args.val_end, tz="UTC")
    i_fit = int((ts < fit_end).sum())
    i_val = int((ts < val_end).sum())
    min_rows = 700 if args.timeframe == "day" else 2000  # ~3 years of candles
    if i_fit < min_rows:
        print(f"fit window too short: {i_fit} rows (< {min_rows})", file=sys.stderr)
        return 1
    fitted = select_and_fit(X[:i_fit], X[i_fit:i_val], raw_ret[:i_fit])
    probs = forward_filter(fitted.startprob, fitted.transmat, fitted.means, fitted.covars, X)
    ent = entropy(probs)
    hard = probs.argmax(axis=1)

    out_dir = MISSION / "fits" / f"{args.symbol.replace('/', '-')}_{args.timeframe}"
    out_dir.mkdir(parents=True, exist_ok=True)
    filtered = pd.DataFrame(probs, columns=[f"p_{lab}" for lab in fitted.labels])
    filtered.insert(0, "ts", ts.to_numpy())
    filtered["state"] = [fitted.labels[i] for i in hard]
    filtered["entropy"] = ent
    filtered["ret"] = raw_ret
    filtered["segment"] = np.where(np.arange(len(ts)) < i_fit, "fit", np.where(np.arange(len(ts)) < i_val, "validation", "forward"))
    filtered.to_csv(out_dir / "filtered.csv.gz", index=False, compression="gzip")

    # per-state statistics by segment, on the FILTERED state (decision-time)
    seg_rows = []
    for seg in ("fit", "validation", "forward"):
        sel = filtered[filtered["segment"] == seg]
        for lab in fitted.labels:
            s = sel[sel["state"] == lab]
            if len(s) == 0:
                continue
            seg_rows.append({"segment": seg, "state": lab, "share": len(s) / len(sel), "mean_ret_bps": s["ret"].mean() * 1e4, "vol_bps": s["ret"].std(ddof=0) * 1e4,
                             "next_ret_bps": filtered["ret"].shift(-1).loc[s.index].mean() * 1e4, "n": len(s)})
    seg = pd.DataFrame(seg_rows)
    trans = pd.DataFrame(fitted.transmat, index=fitted.labels, columns=fitted.labels).round(4).reset_index().rename(columns={"index": "from \\ to"})
    sel_tbl = pd.DataFrame(fitted.selection["candidates"])
    switches = int((filtered["state"] != filtered["state"].shift(1)).sum())

    md = [f"# HMM fit report: {args.symbol} {args.timeframe}\n",
          f"Generated {datetime.now(timezone.utc).isoformat(timespec='minutes')}. Candles {candles['ts'].iloc[0].date()} to {candles['ts'].iloc[-1].date()} ({len(candles):,}); "
          f"feature rows {len(X):,}; fit = first {i_fit:,} rows (to {fit_end.date()}), validation {i_val - i_fit:,} rows (to {val_end.date()}), forward {len(X) - i_val:,} rows. Features: {', '.join(FEATURES)} (standardized on the past only).\n",
          "## State count selection (BIC on fit, log-likelihood per observation on validation; simpler wins within 2%)\n", md_table(sel_tbl),
          f"**Chosen: {fitted.n_states} states**, labelled by their statistics on the fit window: {', '.join(fitted.labels)}.\n",
          "## Transition matrix (row = from, col = to)\n", md_table(trans),
          "## Expected duration (candles) = 1 / (1 - self-transition)\n",
          md_table(pd.DataFrame({"state": fitted.labels, "expected_duration": fitted.expected_durations.round(1), "share_fit": [s["share"] for s in fitted.state_stats]})),
          "## Per-state statistics by segment, on the forward-filtered state (what a decision would have seen)\n",
          "`next_ret_bps` = mean return of the candle AFTER the state was read: the only number that can be traded.\n", md_table(seg),
          f"Filtered-state switches over the whole series: {switches} ({switches / len(filtered) * 100:.1f}% of candles). Mean normalized entropy {ent.mean():.3f}; share of candles with entropy > 0.5: {(ent > 0.5).mean():.1%}.\n"]
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True, gridspec_kw={"height_ratios": [2, 1]})
        px = candles.set_index("ts")["close"].reindex(pd.DatetimeIndex(ts))
        axes[0].plot(ts, px.to_numpy(), color="#333", lw=0.6)
        colors = {"CALM_UP": "#2a9d8f", "CHOP": "#8d99ae", "CHOP2": "#bfc7d5", "STRESS": "#e9c46a", "CRASH": "#e76f51"}
        for lab in fitted.labels:
            mask = filtered["state"].to_numpy() == lab
            axes[0].fill_between(ts, px.min(), px.max(), where=mask, color=colors.get(lab, "#ccc"), alpha=0.18, label=lab)
        axes[0].set_yscale("log")
        axes[0].legend(loc="upper left")
        axes[0].set_title(f"{args.symbol} {args.timeframe}: filtered regime (fit to {fit_end.date()}, validation to {val_end.date()})")
        for lab in fitted.labels:
            axes[1].plot(ts, filtered[f"p_{lab}"], lw=0.5, label=lab, color=colors.get(lab, "#ccc"))
        axes[1].axvline(fit_end, color="k", ls="--", lw=0.8)
        axes[1].axvline(val_end, color="k", ls=":", lw=0.8)
        axes[1].set_ylabel("filtered P(state)")
        fig.tight_layout()
        fig.savefig(out_dir / "regimes.png", dpi=120)
        md.append("![regimes](regimes.png)\n")
    except Exception as exc:  # noqa: BLE001
        md.append(f"(chart skipped: {exc})\n")
    (out_dir / "REPORT.md").write_text("\n".join(md), encoding="utf-8")
    (out_dir / "model.json").write_text(json.dumps({"n_states": fitted.n_states, "labels": fitted.labels, "startprob": fitted.startprob.tolist(), "transmat": fitted.transmat.tolist(),
                                                     "means": fitted.means.tolist(), "covars": fitted.covars.tolist(), "state_stats": fitted.state_stats, "selection": fitted.selection,
                                                     "fit_end": args.fit_end, "val_end": args.val_end, "features": FEATURES}, indent=1), encoding="utf-8")
    print("\n".join(md[:12]))
    print("written", out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
