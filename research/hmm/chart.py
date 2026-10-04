"""Chart a saved fit (fits/<name>/filtered.csv.gz + model.json) without refitting."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from research.hmm.data import load_candles  # noqa: E402

COLORS = {"CALM_UP": "#2a9d8f", "CHOP": "#8d99ae", "CHOP2": "#bfc7d5", "STRESS": "#e9c46a", "CRASH": "#e76f51"}


def main(fit_dir: str, symbol: str, timeframe: str) -> int:
    d = Path(fit_dir)
    model = json.loads((d / "model.json").read_text(encoding="utf-8"))
    f = pd.read_csv(d / "filtered.csv.gz")
    f["ts"] = pd.to_datetime(f["ts"], utc=True)
    px = load_candles(symbol, timeframe).set_index("ts")["close"].reindex(pd.DatetimeIndex(f["ts"]))
    fig, axes = plt.subplots(2, 1, figsize=(13, 7), sharex=True, gridspec_kw={"height_ratios": [2, 1]})
    axes[0].plot(f["ts"], px.to_numpy(), color="#222", lw=0.6)
    for lab in model["labels"]:
        mask = (f["state"] == lab).to_numpy()
        axes[0].fill_between(f["ts"], px.min(), px.max(), where=mask, color=COLORS.get(lab, "#ccc"), alpha=0.2, label=lab)
    axes[0].set_yscale("log"); axes[0].legend(loc="upper left", ncol=5)
    axes[0].set_title(f"{symbol} {timeframe}: forward-filtered regime; fit to {model['fit_end']}, validation to {model['val_end']}")
    for lab in model["labels"]:
        axes[1].plot(f["ts"], f[f"p_{lab}"], lw=0.4, color=COLORS.get(lab, "#ccc"), label=lab)
    axes[1].axvline(pd.Timestamp(model["fit_end"], tz="UTC"), color="k", ls="--", lw=0.8)
    axes[1].axvline(pd.Timestamp(model["val_end"], tz="UTC"), color="k", ls=":", lw=0.8)
    axes[1].set_ylabel("filtered P(state)")
    fig.tight_layout(); fig.savefig(d / "regimes.png", dpi=120)
    print("written", d / "regimes.png")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
