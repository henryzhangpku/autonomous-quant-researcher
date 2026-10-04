"""The walk-forward backtest keeps honest books and rejects a tape with no edge."""

from __future__ import annotations

import gzip
from pathlib import Path

import numpy as np
import pandas as pd

from research.hmm import backtest as bt


def _write_lake(root: Path, symbol: str, timeframe: str, candles: pd.DataFrame) -> None:
    d = root / "bars" / symbol.replace("/", "-")
    d.mkdir(parents=True, exist_ok=True)
    with gzip.open(d / f"{timeframe}.csv.gz", "wt", encoding="utf-8", newline="") as fh:
        candles.to_csv(fh, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")


def _random_daily_tape(n: int = 1700, seed: int = 9) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ret = rng.normal(0.0, 0.012, n)  # zero drift: no edge to find
    close = 100 * np.exp(np.cumsum(ret))
    ts = pd.bdate_range("2016-01-04", periods=n, tz="UTC")
    return pd.DataFrame({"ts": ts, "open": close, "high": close * 1.004, "low": close * 0.996, "close": close, "volume": rng.lognormal(12, 0.3, n)})


def test_books_balance_and_a_driftless_tape_is_rejected(tmp_path: Path, monkeypatch):
    candles = _random_daily_tape()
    _write_lake(tmp_path, "TEST", "day", candles)
    monkeypatch.setenv("QS_DATALAKE_DIR", str(tmp_path))
    res = bt.run_backtest("TEST", "day", fit_end="2019-01-01", val_end="2019-07-01", fee_bps=1.0, slip_bps=2.0, refit_days=120)
    assert res["oos_candles"] > 700
    bh = res["results"]["buy_hold"]
    # buy-and-hold equity = compounded candle returns net of the single entry cost
    eq = bh["equity_curve"]
    pnl = bh["pnl_series"]
    np.testing.assert_allclose(eq[-1] / 100_000.0, np.prod(1 + pnl), rtol=1e-9)
    assert bh["costs_paid"] > 0 and bh["time_in_market"] == 1.0
    hmm = res["results"]["hmm"]
    assert hmm["costs_paid"] >= 0 and 0 <= hmm["time_in_market"] <= 1
    assert not res["all_gates_pass"]  # a driftless random walk must not clear Sharpe > 1.5 after costs
    assert set(res["gates"]) == {"sharpe", "max_drawdown", "hit_rate", "t_stat", "beats_buy_hold", "beats_best_static"}
    assert res["refits"], "walk-forward refits happened"
    out = bt.write_report(res, tmp_path / "out")
    assert out.exists() and "REJECTED" in out.read_text(encoding="utf-8")
