"""The feature pipeline uses the past only, and says so with NaN rather than guessing."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.hmm.features import FEATURES, STD_WINDOW, VOL_WINDOW, EMA_SLOW, build_features, feature_matrix, raw_features


def _candles(n: int = 900, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ret = rng.normal(0.0002, 0.01, n)
    close = 100 * np.exp(np.cumsum(ret))
    high = close * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.004, n)))
    vol = rng.lognormal(10, 0.3, n)
    ts = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"ts": ts, "open": close, "high": high, "low": low, "close": close, "volume": vol})


def test_no_feature_at_t_depends_on_candle_t_plus_1():
    candles = _candles()
    base = build_features(candles)
    altered = candles.copy()
    t = 700
    altered.loc[t + 1, ["open", "high", "low", "close"]] *= 1.5  # a wild candle AFTER t
    altered.loc[t + 1, "volume"] *= 10
    after = build_features(altered)
    pd.testing.assert_frame_equal(base.iloc[: t + 1].reset_index(drop=True), after.iloc[: t + 1].reset_index(drop=True))
    assert not after.iloc[t + 1][FEATURES].equals(base.iloc[t + 1][FEATURES])  # and it does move t+1 itself


def test_features_on_a_truncated_series_equal_the_full_series_up_to_the_cut():
    candles = _candles()
    full = build_features(candles)
    cut = build_features(candles.iloc[:750].reset_index(drop=True))
    pd.testing.assert_frame_equal(full.iloc[:750].reset_index(drop=True), cut)


def test_standardization_uses_statistics_strictly_before_t():
    candles = _candles(n=STD_WINDOW + 200)
    raw = raw_features(candles)
    feats = build_features(candles)
    t = STD_WINDOW + 150
    past = raw["ret"].iloc[t - STD_WINDOW:t]  # rows t-W .. t-1, never t
    expected = (raw["ret"].iloc[t] - past.mean()) / past.std(ddof=0)
    assert feats["ret"].iloc[t] == pytest.approx(expected)


def test_warmup_rows_are_nan_not_filled():
    candles = _candles(n=STD_WINDOW + 50)
    feats = build_features(candles)
    assert feats[FEATURES].iloc[: max(VOL_WINDOW, EMA_SLOW)].isna().all().all()
    assert pd.notna(feats["ret"].iloc[STD_WINDOW]) or pd.notna(feats["ret"].iloc[STD_WINDOW + 1])
    X, ts = feature_matrix(feats)
    assert X.shape[1] == len(FEATURES) and len(ts) == len(X) and not np.isnan(X).any()


def test_features_are_scale_free():
    candles = _candles()
    scaled = candles.copy()
    for col in ("open", "high", "low", "close"):
        scaled[col] *= 1000.0  # same market in a different unit
    a = build_features(candles)
    b = build_features(scaled)
    pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-9)
