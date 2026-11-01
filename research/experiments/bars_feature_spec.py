"""The bars-universe feature contract — one definition, three consumers.

The feature set is coupled across three files that must agree exactly or a
candidate dies on a name the fixture does not know:

  * ``prepare_bars_universe`` COMPUTES the features into each row;
  * ``research.validators.bars_universe`` WHITELISTS them (a candidate is
    statically rejected for referencing anything outside this set);
  * ``research.lab.idea_compiler`` NAMES them in the frozen mission text so
    the proposing model knows what it may read.

Keeping three hand-written copies in sync is a drift bug waiting to happen —
so the names live here, in a leaf module with no imports, and every consumer
derives from it. ``test_bars_feature_spec_matches_prepared_rows`` asserts the
prepared data really does carry exactly these keys.

The values are a REPRESENTATIVE VECTOR, not data: the validator executes a
candidate against them once, before spending a scientific trial, to prove it
runs at all. They are deliberately unremarkable — a fixture that looks like a
signal invites a candidate that fits the fixture.
"""

from __future__ import annotations

# name -> representative value. Ordered by family, matching the computation.
FEATURE_FIXTURE: dict[str, float] = {
    # Returns over the horizons the loop may reason about.
    "ret_1d": 0.004,
    "ret_5d": -0.012,
    "ret_10d": 0.008,
    "ret_20d": 0.031,
    "ret_60d": 0.055,
    "oc_ret": 0.003,
    "gap_open": -0.002,
    # Where inside the bar the session resolved (candle geometry).
    "range_pos": 0.62,
    "k_body": 0.21,
    "k_upper": 0.18,
    "k_lower": 0.33,
    # Distance from moving averages, and their spread.
    "dist_ma_5": 0.006,
    "dist_ma_20": 0.014,
    "dist_ma_60": 0.027,
    "ma_5_20_spread": 0.008,
    # Realized volatility level and term structure.
    "rv_5d": 0.011,
    "rv_20d": 0.014,
    "rv_60d": 0.017,
    "rv_ratio_5_20": 0.79,
    # Trend versus chop: slope and how well a line explains the window.
    "trend_slope_20": 0.0012,
    "trend_r2_20": 0.44,
    "trend_slope_60": 0.0008,
    "trend_r2_60": 0.31,
    # Position in the recent range, and how recent the extremes are.
    "dist_high_20": -0.018,
    "dist_low_20": 0.042,
    "rsv_20": 0.58,
    "high_recency_20": 0.74,
    "low_recency_20": 0.11,
    # Breadth and shape of the move.
    "up_frac_20": 0.55,
    "gain_share_20": 0.57,
    "streak": 2.0,
    "ret_skew_20": -0.35,
    # The retail canon, on its conventional definitions, so a finding can be
    # quoted under the indicator's own name rather than a proxy. RSI is
    # Wilder-smoothed on the usual 0-100 scale; the MACD(12,26,9) histogram is
    # divided by price so one reading means the same thing on a $30 name and a
    # $900 one; Bollinger is the 20-period, 2-sigma band, %B being where price
    # sits between the bands and bandwidth their width over the middle band.
    "rsi_2": 38.0,
    "rsi_14": 47.0,
    "macd_hist": 0.002,
    "bb_pctb": 0.44,
    "bb_bandwidth": 0.09,
    # SmartFit-style pivot-anchored regression channel (see _channel_series
    # in prepare_bars_universe) plus Wilder's ADX — trend strength without
    # direction. chan_pearson is the SIGNED correlation of close vs time
    # over the channel window, so its sign is the channel bias; chan_z is
    # close minus the regression midline in residual sigmas; chan_breakout
    # fires +1/-1 once, on the bar the close first leaves the 2-sigma band.
    "chan_bars": 24.0,
    "chan_slope": 0.0011,
    "chan_pearson": 0.63,
    "chan_z": -0.85,
    "chan_breakout": 0.0,
    "adx_14": 21.0,
    # Volume behaviour.
    "vol_ratio_5_20": 1.15,
    "vol_cv_20": 0.38,
    "corr_ret_vol_20": 0.12,
    # Calendar.
    "day_of_week": 2.0,
    # Macro event calendar (FOMC/CPI/NFP release days, research/data/
    # macro_events.json). Both are known-in-advance calendar facts — the
    # schedules publish months to years ahead — so event_next_session looking
    # one session ahead is NOT lookahead: the decision session's close already
    # knows tomorrow is a release day. Both are 0.0 on most sessions, which is
    # also why the fixture keeps them there.
    "event_today": 0.0,
    "event_next_session": 0.0,
    # Cross-sectional rank within the session's universe, in [0, 1]. In a
    # single-symbol mission every one of these is 0.5 by construction.
    "xs_rank_ret_1d": 0.7,
    "xs_rank_ret_5d": 0.4,
    "xs_rank_ret_20d": 0.6,
    "xs_rank_rv_20d": 0.3,
    "xs_rank_vol_ratio_5_20": 0.8,
    "xs_rank_dist_high_20": 0.45,
    "xs_rank_range_pos": 0.65,
    "xs_rank_dist_ma_20": 0.55,
}

FEATURE_NAMES: tuple[str, ...] = tuple(FEATURE_FIXTURE)
