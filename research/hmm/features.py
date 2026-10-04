"""The deterministic feature pipeline (spec section 1, state_engine).

Five features per candle, every one computed from candles at or before t,
then standardized with statistics of candles STRICTLY BEFORE t:

    ret        log(close_t / close_{t-1})
    rvol       rolling std of ret over VOL_WINDOW candles (incl. t)
    range      (high_t - low_t) / close_t
    vol_ratio  volume_t / rolling mean volume over VOL_WINDOW candles ending t-1
    trend      (EMA_20(close) - EMA_100(close)) / (rvol * close), the one trend
               feature, scale-free

Standardization: z_t = (x_t - mean(x_{t-W..t-1})) / std(x_{t-W..t-1}) with
W = STD_WINDOW. Using the window ending at t-1 is what makes the test in
tests/test_hmm_features.py pass: altering candle t+1 cannot move any feature
at t, and the first STD_WINDOW+VOL_WINDOW rows are NaN by construction
rather than being filled with information from the future.

Keep it small and numeric. A feature that needs a model is not a feature.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FEATURES = ["ret", "rvol", "range", "vol_ratio", "trend"]
VOL_WINDOW = 20
EMA_FAST = 20
EMA_SLOW = 100
STD_WINDOW = 500


def raw_features(candles: pd.DataFrame) -> pd.DataFrame:
    c = candles["close"].astype(float)
    h = candles["high"].astype(float)
    lo = candles["low"].astype(float)
    v = candles["volume"].astype(float)
    ret = np.log(c / c.shift(1))
    rvol = ret.rolling(VOL_WINDOW, min_periods=VOL_WINDOW).std(ddof=0)
    rng = (h - lo) / c
    vol_mean_prev = v.shift(1).rolling(VOL_WINDOW, min_periods=VOL_WINDOW).mean()
    vol_ratio = v / vol_mean_prev.replace(0, np.nan)
    ema_fast = c.ewm(span=EMA_FAST, adjust=False, min_periods=EMA_FAST).mean()
    ema_slow = c.ewm(span=EMA_SLOW, adjust=False, min_periods=EMA_SLOW).mean()
    trend = (ema_fast - ema_slow) / (rvol * c).replace(0, np.nan)
    out = pd.DataFrame({"ts": candles["ts"], "ret": ret, "rvol": rvol, "range": rng, "vol_ratio": vol_ratio, "trend": trend})
    return out


def standardize_past_only(raw: pd.DataFrame, window: int = STD_WINDOW) -> pd.DataFrame:
    """z-scores using the trailing window ending at t-1, never t."""
    out = raw[["ts"]].copy()
    for col in FEATURES:
        x = raw[col]
        past = x.shift(1)
        mean = past.rolling(window, min_periods=window).mean()
        std = past.rolling(window, min_periods=window).std(ddof=0)
        out[col] = (x - mean) / std.replace(0, np.nan)
    return out


def build_features(candles: pd.DataFrame, window: int = STD_WINDOW) -> pd.DataFrame:
    """ts + the five standardized features; rows that cannot be computed from
    the past alone are NaN and are dropped by the caller, never filled."""
    return standardize_past_only(raw_features(candles), window)


def feature_matrix(features: pd.DataFrame) -> tuple[np.ndarray, pd.Series]:
    """(X, ts) with NaN rows removed, in time order; X is float64 (n, 5)."""
    clean = features.dropna(subset=FEATURES)
    return clean[FEATURES].to_numpy(dtype=float), clean["ts"].reset_index(drop=True)
