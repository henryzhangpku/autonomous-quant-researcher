"""Freeze a daily-bars universe snapshot for the bars-universe validator.

Data acquisition happens here, outside candidate execution, per CLAUDE.md.
For each (session, symbol) the row carries features computed strictly from
bars up to and including that session, plus the next session's realized
returns as payoffs. Candidates never see bars, timestamps, or the future --
only the feature dict; leakage is structurally impossible at that boundary.

Output: <out>/sessions.jsonl + <out>/manifest.json (sha256, provenance,
suggested chronological splits).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

from research.providers.alpaca import AlpacaHistoricalProvider

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
# 60-session features need 60 prior sessions plus the one being described.
# Raised from 21 when the feature set was widened (2026-08-16): the loop was
# searching an 11-feature space and had collapsed to near-duplicate proposals,
# ~1,300 hypotheses landing in two score clusters. A search cannot find what
# its inputs cannot express.
FEATURE_WARMUP_SESSIONS = 61

# The staged macro event calendar (FOMC/CPI/NFP release days). Frozen input,
# hash-pinned into every snapshot manifest like the spec and data hashes: a
# changed calendar changes the computed features, hence the data hash, so a
# stale snapshot fails its hash check loudly instead of evaluating quietly
# against a different event history.
MACRO_EVENTS_PATH = REPOSITORY_ROOT / "research" / "data" / "macro_events.json"


def load_event_sessions(path: Path = MACRO_EVENTS_PATH) -> tuple[frozenset[str], str]:
    """Every release day across the three series, plus the file's sha256."""
    raw = path.read_bytes()
    payload = json.loads(raw)
    days = frozenset(
        day for series in ("fomc", "cpi", "nfp") for day in payload[series]
    )
    return days, hashlib.sha256(raw).hexdigest()


def _safe(value: float, fallback: float = 0.0) -> float:
    """Finite or the fallback. A NaN/inf in a feature poisons every candidate
    that touches it, and the failure surfaces as an unexplained trial loss."""
    return round(value, 8) if isinstance(value, (int, float)) and math.isfinite(value) else fallback


def _slope_and_fit(values: Sequence[float]) -> tuple[float, float]:
    """Least-squares slope (per session, normalised by mean) and R² of a price
    window against time — 'is this a trend or a chop', which no existing
    feature expressed."""
    n = len(values)
    if n < 3:
        return 0.0, 0.0
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    sxx = sum((i - mean_x) ** 2 for i in range(n))
    sxy = sum((i - mean_x) * (values[i] - mean_y) for i in range(n))
    if not sxx or not mean_y:
        return 0.0, 0.0
    slope = sxy / sxx
    syy = sum((v - mean_y) ** 2 for v in values)
    r2 = (sxy * sxy) / (sxx * syy) if syy else 0.0
    return slope / mean_y, r2


def _ema_series(values: Sequence[float], span: int) -> list[float]:
    """Classic EMA, seeded with the SMA of the first `span` points.

    Seeding with an SMA rather than the first value is what charting platforms
    do; starting from values[0] leaves a visible transient for tens of bars,
    which would put a different number under the same indicator name.
    """
    if len(values) < span:
        return [0.0] * len(values)
    k = 2.0 / (span + 1.0)
    out = [0.0] * len(values)
    seed = sum(values[:span]) / span
    out[span - 1] = seed
    prev = seed
    for i in range(span, len(values)):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def _rsi_series(closes: Sequence[float], period: int) -> list[float]:
    """Wilder's RSI on a 0-100 scale, the convention every platform ships.

    Wilder smoothing (alpha = 1/period), not a simple mean of the last N
    changes: RSI-2 in particular is a different indicator under the two
    definitions, and RSI-2 is the one Connors-style rules are stated in.
    """
    n = len(closes)
    out = [50.0] * n
    if n <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain, avg_loss = gains / period, losses / period
    def rsi(g: float, l: float) -> float:
        if l == 0.0:
            return 100.0 if g > 0.0 else 50.0
        return 100.0 - 100.0 / (1.0 + g / l)
    out[period] = rsi(avg_gain, avg_loss)
    for i in range(period + 1, n):
        change = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[i] = rsi(avg_gain, avg_loss)
    return out


def _macd_hist_series(closes: Sequence[float]) -> list[float]:
    """MACD(12,26,9) histogram, divided by price.

    The raw histogram is in dollars, so the same reading means different things
    on a $30 name and a $900 one and cannot be compared across a universe.
    Dividing by close makes it a fraction of price, which is what the feature
    set does everywhere else.
    """
    fast, slow, signal = _ema_series(closes, 12), _ema_series(closes, 26), None
    macd = [f - s for f, s in zip(fast, slow)]
    signal = _ema_series(macd, 9)
    return [
        _safe((m - sg) / c) if c else 0.0
        for m, sg, c in zip(macd, signal, closes)
    ]


def _bollinger_series(closes: Sequence[float], period: int = 20,
                      mult: float = 2.0) -> tuple[list[float], list[float]]:
    """Bollinger %B and bandwidth on the conventional 20-period, 2-sigma bands.

    %B is where price sits between the bands (0 at the lower, 1 at the upper,
    outside that when price breaks out); bandwidth is band width over the
    middle band, already scale-free.
    """
    n = len(closes)
    pctb, width = [0.5] * n, [0.0] * n
    for i in range(period - 1, n):
        window = closes[i - period + 1 : i + 1]
        mid = sum(window) / period
        sd = statistics.pstdev(window)
        if sd == 0.0 or mid == 0.0:
            continue
        upper, lower = mid + mult * sd, mid - mult * sd
        pctb[i] = _safe((closes[i] - lower) / (upper - lower), 0.5)
        width[i] = _safe((upper - lower) / mid)
    return pctb, width


# ── SmartFit-style pivot-anchored regression channel ───────────────────────────
# Ported from the premise of "SmartFit Trend Channels [MarkitTick]"
# (TradingView, open-source): OLS regression anchored at the most recent
# CONFIRMED swing pivot, a signed Pearson fit score, deviation bands at a
# fixed sigma multiple, breakout detection, and re-anchoring at the most
# recent opposite-type pivot after a breakout. ta-premises P2 tested a
# fixed-window channel REVERSION and failed validation; what this family
# could not previously express is the anchoring, the fit SIGN, and the
# breakout side.
#
# Causality is the whole game here: a swing high with P bars either side is
# not KNOWN until P bars after it prints. The state machine below only ever
# admits a pivot at bar i once t >= i + P, so every emitted value is a
# function of bars <= t. tests/test_smartfit_channel_features.py asserts
# truncation invariance — the value at t is identical whether or not the
# series continues past t.
# Defaults read from the published Pine v6 source (MarkitTick, CC BY-NC-SA
# 4.0), not from the description page, so the claims are the author's numbers
# rather than a paraphrase of them:
#   pivot length   -- "Auto Pivot Lookback" is ON by default and resolves to
#                     10 for a 1-day timeframe (tfSeconds <= 86400)
#   |r| threshold  -- "Min Fit Quality (|r|)" = 0.50
#   min fit bars   -- "Min Fit Bars" = 5
#   deviation      -- "Deviation Z-Score" = 1.96
#   ADX filter     -- OFF by default; threshold 20.0, length 14 when enabled
# The band test `close > endPrice + Z * stdDev` is algebraically a z-score
# test on the residual, which is how it is expressed here.
CHANNEL_PIVOT_BARS = 10     # bars each side confirming a swing (daily auto-scale)
CHANNEL_DEV_MULT = 1.96     # "Deviation Z-Score"
CHANNEL_FIT_QUALITY = 0.50  # "Min Fit Quality (|r|)"
CHANNEL_MIN_FIT_BARS = 5    # "Min Fit Bars"
CHANNEL_Z_CLAMP = 10.0      # a 5-bar channel can have near-zero sigma; cap the z
# DEVIATION FROM THE SOURCE, recorded rather than hidden: the script lets a
# channel grow unbounded until a breakout re-anchors it, so its value at a
# session depends on how far back the chart happens to load. A frozen research
# snapshot may not have that property -- staging 2015+ and 2019+ would put
# different numbers on the same session -- so the anchor is capped. 250
# sessions is ~1 trading year and far beyond the length at which breakouts
# restart channels in practice.
CHANNEL_MAX_BARS = 250


def _fit_window(closes: Sequence[float], start: int, stop: int) -> tuple[
        float, float, float, float]:
    """OLS of close vs time over closes[start:stop+1].

    Returns (normalised slope, signed Pearson r, residual sigma, fitted value
    at ``stop``). Time runs FORWARD here, where the Pine source indexes bars
    backwards; the sign of r is identical either way (the script's own
    ``chBullish = s < 0`` is that same inversion), and positive means rising.
    Sigma divides by n-1, matching the source's ``stdDevAcc / (len - 1)``.
    """
    size = stop - start + 1
    if size < 3:
        return 0.0, 0.0, 0.0, 0.0
    window = closes[start : stop + 1]
    mean_x = (size - 1) / 2.0
    mean_y = sum(window) / size
    sxx = sum((j - mean_x) ** 2 for j in range(size))
    sxy = sum((j - mean_x) * (window[j] - mean_y) for j in range(size))
    syy = sum((value - mean_y) ** 2 for value in window)
    slope = sxy / sxx if sxx else 0.0
    pearson = sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else 0.0
    residual_sq = sum(
        (window[j] - (mean_y + slope * (j - mean_x))) ** 2 for j in range(size)
    )
    sigma = math.sqrt(residual_sq / (size - 1))
    fitted_now = mean_y + slope * ((size - 1) - mean_x)
    return (slope / mean_y if mean_y else 0.0), pearson, sigma, fitted_now


def _channel_series(bars: list[dict[str, Any]]) -> tuple[
        list[float], list[float], list[float], list[float], list[float]]:
    """Per-bar chan_bars, chan_slope, chan_pearson, chan_z, chan_breakout.

    Mirrors the source's state machine: pivots confirm ``pivotLen`` bars late,
    a breakout needs the band cross AND the min-bar AND the |r| gate together
    (they are inside ``isBullishBreak``, not optional filters), the signal is
    an EDGE against the previous bar's state, and on a break the channel
    re-anchors at the most recent opposite pivot only when a regression from
    that pivot clears the same |r| gate -- otherwise it restarts empty at the
    current bar.

    One deliberate translation: the source reads ``close[1]``/``high[1]``/
    ``low[1]`` because a Pine indicator evaluates on the live forming bar and
    the author wants confirmed data. The lab decides at session t's close,
    where bar t IS confirmed, so bar t is the lab's equivalent of the script's
    ``[1]``. Using the lagged bar instead would discard a session of
    information without adding any causal safety.
    """
    count = len(bars)
    highs = [bar["high"] for bar in bars]
    lows = [bar["low"] for bar in bars]
    closes = [bar["close"] for bar in bars]
    out_bars = [0.0] * count
    out_slope = [0.0] * count
    out_pearson = [0.0] * count
    out_z = [0.0] * count
    out_break = [0.0] * count
    pivot = CHANNEL_PIVOT_BARS
    last_high = last_low = -1   # most recent CONFIRMED swing high / low
    anchor = -1                 # current channel start bar
    prev_bull = prev_bear = False   # previous bar's breakout STATE
    for t in range(count):
        # The pivot at i = t - pivot is confirmed exactly now: its window
        # [i - pivot, i + pivot] ends at t. The source's f_pivotHigh rejects
        # the candidate when ANY other bar in the window ties or exceeds it,
        # so a tie is not a swing.
        i = t - pivot
        if i >= pivot:
            window_h = highs[i - pivot : i + pivot + 1]
            if highs[i] == max(window_h) and window_h.count(highs[i]) == 1:
                last_high = i
            window_l = lows[i - pivot : i + pivot + 1]
            if lows[i] == min(window_l) and window_l.count(lows[i]) == 1:
                last_low = i
        if anchor < 0:
            anchor = max(last_high, last_low)
        elif t - anchor + 1 > CHANNEL_MAX_BARS:
            anchor = t - CHANNEL_MAX_BARS + 1   # see CHANNEL_MAX_BARS above
        if anchor < 0 or t - anchor + 1 < 3:
            prev_bull = prev_bear = False
            continue
        size = t - anchor + 1
        slope, pearson, sigma, fitted_now = _fit_window(closes, anchor, t)
        z = (closes[t] - fitted_now) / sigma if sigma > 0 else 0.0
        z = max(-CHANNEL_Z_CLAMP, min(CHANNEL_Z_CLAMP, z))
        out_bars[t] = float(size)
        out_slope[t] = slope
        out_pearson[t] = pearson
        out_z[t] = z
        # The fit gates live INSIDE the breakout definition in the source.
        qualified = size >= CHANNEL_MIN_FIT_BARS and abs(pearson) >= CHANNEL_FIT_QUALITY
        is_bull = qualified and z > CHANNEL_DEV_MULT
        is_bear = qualified and z < -CHANNEL_DEV_MULT
        breakout = 0.0
        if is_bull and not prev_bull:
            breakout = 1.0
        elif is_bear and not prev_bear:
            breakout = -1.0
        out_break[t] = breakout
        # Carry THIS bar's state forward before any re-anchor, exactly as the
        # source's _prevBullish reads the prior bar's channel.
        prev_bull, prev_bear = is_bull, is_bear
        if breakout:
            # candValid: the opposite pivot must clear the same |r| gate on a
            # regression of its own, sit after the current anchor, and be in
            # the past. Otherwise the channel restarts empty at this bar.
            candidate = last_low if breakout > 0 else last_high
            fresh = t
            if candidate > anchor and candidate < t:
                _, cand_r, _, _ = _fit_window(closes, candidate, t)
                if abs(cand_r) >= CHANNEL_FIT_QUALITY:
                    fresh = candidate
            anchor = fresh
    return out_bars, out_slope, out_pearson, out_z, out_break



def _adx_series(bars: list[dict[str, Any]], period: int = 14) -> list[float]:
    """Wilder's ADX at the conventional 14, on the usual 0-100 scale.

    SmartFit gates its breakout signals on a minimum ADX, and nothing in the
    family previously measured trend STRENGTH without direction. Computed the
    Wilder way end to end — smoothed TR / +DM / -DM, the DI spread, then a
    Wilder smooth of DX — because an EMA-smoothed clone is a different number
    under the same name.
    """
    count = len(bars)
    out = [0.0] * count
    if count <= 2 * period:
        return out
    trs = [0.0] * count
    plus_dm = [0.0] * count
    minus_dm = [0.0] * count
    for i in range(1, count):
        high, low = bars[i]["high"], bars[i]["low"]
        prev_high, prev_low = bars[i - 1]["high"], bars[i - 1]["low"]
        prev_close = bars[i - 1]["close"]
        trs[i] = max(high - low, abs(high - prev_close), abs(low - prev_close))
        up_move = high - prev_high
        down_move = prev_low - low
        plus_dm[i] = up_move if up_move > down_move and up_move > 0 else 0.0
        minus_dm[i] = down_move if down_move > up_move and down_move > 0 else 0.0

    def directional_index(tr_sum: float, plus_sum: float, minus_sum: float) -> float:
        if tr_sum <= 0:
            return 0.0
        di_plus = 100.0 * plus_sum / tr_sum
        di_minus = 100.0 * minus_sum / tr_sum
        denominator = di_plus + di_minus
        return 100.0 * abs(di_plus - di_minus) / denominator if denominator else 0.0

    tr_sum = sum(trs[1 : period + 1])
    plus_sum = sum(plus_dm[1 : period + 1])
    minus_sum = sum(minus_dm[1 : period + 1])
    dx = [0.0] * count
    dx[period] = directional_index(tr_sum, plus_sum, minus_sum)
    for i in range(period + 1, count):
        tr_sum = tr_sum - tr_sum / period + trs[i]
        plus_sum = plus_sum - plus_sum / period + plus_dm[i]
        minus_sum = minus_sum - minus_sum / period + minus_dm[i]
        dx[i] = directional_index(tr_sum, plus_sum, minus_sum)
    adx = sum(dx[period : 2 * period]) / period
    out[2 * period - 1] = adx
    for i in range(2 * period, count):
        adx = (adx * (period - 1) + dx[i]) / period
        out[i] = adx
    return out


def _feature_rows(symbol: str, bars: list[dict[str, Any]],
                  event_sessions: frozenset[str] | None = None) -> list[dict[str, Any]]:
    """Bars are chronological daily bars; emit one row per decidable session."""

    # None means "the staged calendar"; tests pass their own tiny set.
    if event_sessions is None:
        event_sessions = load_event_sessions()[0]
    rows: list[dict[str, Any]] = []
    closes = [bar["close"] for bar in bars]
    volumes = [bar["volume"] or 0.0 for bar in bars]
    returns = [
        closes[index] / closes[index - 1] - 1.0 if closes[index - 1] else 0.0
        for index in range(1, len(bars))
    ]
    rsi_2_series = _rsi_series(closes, 2)
    rsi_14_series = _rsi_series(closes, 14)
    macd_hist_series = _macd_hist_series(closes)
    bb_pctb_series, bb_width_series = _bollinger_series(closes)
    adx_14_series = _adx_series(bars)
    (chan_bars_series, chan_slope_series, chan_pearson_series,
     chan_z_series, chan_breakout_series) = _channel_series(bars)
    for index in range(FEATURE_WARMUP_SESSIONS, len(bars) - 1):
        bar = bars[index]
        next_bar = bars[index + 1]
        if not (bar["close"] and bar["high"] >= bar["low"] and next_bar["open"] and next_bar["close"]):
            continue
        window_20 = closes[index - 19 : index + 1]
        window_60 = closes[index - 59 : index + 1]
        highs_20 = [b["high"] for b in bars[index - 19 : index + 1]]
        lows_20 = [b["low"] for b in bars[index - 19 : index + 1]]
        recent_returns = returns[index - 20 : index]  # returns index i is close[i]/close[i-1]-1
        returns_60 = returns[index - 60 : index]
        rv_5 = statistics.stdev(recent_returns[-5:]) if len(recent_returns) >= 5 else 0.0
        rv_20 = statistics.stdev(recent_returns) if len(recent_returns) >= 2 else 0.0
        rv_60 = statistics.stdev(returns_60) if len(returns_60) >= 2 else 0.0
        vol_5 = statistics.mean(volumes[index - 4 : index + 1])
        vol_20 = statistics.mean(volumes[index - 19 : index + 1])
        vol_20_window = volumes[index - 19 : index + 1]
        close = closes[index]
        day_range = bar["high"] - bar["low"]
        ma_5 = statistics.mean(closes[index - 4 : index + 1])
        ma_20 = statistics.mean(window_20)
        ma_60 = statistics.mean(window_60)
        slope_20, r2_20 = _slope_and_fit(window_20)
        slope_60, r2_60 = _slope_and_fit(window_60)
        span_20 = max(highs_20) - min(lows_20)
        gains = [r for r in recent_returns if r > 0]
        losses = [-r for r in recent_returns if r < 0]
        churn = sum(gains) + sum(losses)

        # Signed run length of same-direction closes, capped: a 12-day streak
        # and a 30-day streak are the same regime to a next-day decision.
        streak = 0
        for prior in range(index, max(index - 10, 0), -1):
            step = closes[prior] - closes[prior - 1]
            if step == 0 or (streak and (step > 0) != (streak > 0)):
                break
            streak += 1 if step > 0 else -1

        rows.append({
            "session": bar["session"],
            "symbol": symbol,
            "features": {
                # ── original eleven, unchanged ────────────────────────────
                "ret_1d": _safe(close / closes[index - 1] - 1.0),
                "ret_5d": _safe(close / closes[index - 5] - 1.0),
                "ret_20d": _safe(close / closes[index - 20] - 1.0),
                "gap_open": _safe(bar["open"] / closes[index - 1] - 1.0),
                "range_pos": _safe((close - bar["low"]) / day_range, 0.5) if day_range else 0.5,
                "vol_ratio_5_20": _safe(vol_5 / vol_20, 1.0) if vol_20 else 1.0,
                "rv_5d": _safe(rv_5),
                "rv_20d": _safe(rv_20),
                "dist_high_20": _safe(close / max(window_20) - 1.0),
                "dist_low_20": _safe(close / min(window_20) - 1.0),
                "day_of_week": float(datetime.strptime(bar["session"], "%Y-%m-%d").weekday()),
                # Macro release days are calendar facts published months to
                # years ahead, so flagging the NEXT session is not lookahead:
                # at the decision close the calendar for tomorrow is already
                # known. This is the one feature family allowed to reference a
                # future session, and only its date — never its bars.
                "event_today": 1.0 if bar["session"] in event_sessions else 0.0,
                "event_next_session": 1.0 if next_bar["session"] in event_sessions else 0.0,
                # ── horizons the old set could not express ────────────────
                "ret_10d": _safe(close / closes[index - 10] - 1.0),
                "ret_60d": _safe(close / closes[index - 60] - 1.0),
                "oc_ret": _safe(close / bar["open"] - 1.0),
                # ── candle shape (Qlib KBAR): where in the bar the fight
                #    happened, which a close-to-close return cannot say ────
                "k_body": _safe((close - bar["open"]) / day_range) if day_range else 0.0,
                "k_upper": _safe((bar["high"] - max(bar["open"], close)) / day_range) if day_range else 0.0,
                "k_lower": _safe((min(bar["open"], close) - bar["low"]) / day_range) if day_range else 0.0,
                # ── moving-average geometry ───────────────────────────────
                "dist_ma_5": _safe(close / ma_5 - 1.0) if ma_5 else 0.0,
                "dist_ma_20": _safe(close / ma_20 - 1.0) if ma_20 else 0.0,
                "dist_ma_60": _safe(close / ma_60 - 1.0) if ma_60 else 0.0,
                "ma_5_20_spread": _safe(ma_5 / ma_20 - 1.0) if ma_20 else 0.0,
                # ── volatility term structure and level ───────────────────
                "rv_60d": _safe(rv_60),
                "rv_ratio_5_20": _safe(rv_5 / rv_20, 1.0) if rv_20 else 1.0,
                # ── the retail canon, computed the conventional way so a
                #    result can be quoted under the indicator's own name ────
                "rsi_2": _safe(rsi_2_series[index], 50.0),
                "rsi_14": _safe(rsi_14_series[index], 50.0),
                "macd_hist": _safe(macd_hist_series[index]),
                "bb_pctb": _safe(bb_pctb_series[index], 0.5),
                "bb_bandwidth": _safe(bb_width_series[index]),
                # ── SmartFit-style pivot-anchored channel + ADX (see
                #    _channel_series / _adx_series above). chan_pearson is
                #    the SIGNED correlation — its sign is the channel bias;
                #    chan_z is close minus the regression midline in
                #    residual sigmas; chan_breakout fires +1/-1 once, on
                #    the bar the close first leaves the two-sigma band. ──
                "chan_bars": _safe(chan_bars_series[index]),
                "chan_slope": _safe(chan_slope_series[index]),
                "chan_pearson": _safe(chan_pearson_series[index]),
                "chan_z": _safe(chan_z_series[index]),
                "chan_breakout": _safe(chan_breakout_series[index]),
                "adx_14": _safe(adx_14_series[index]),
                # ── trend vs chop: slope AND how well a line fits it ──────
                "trend_slope_20": _safe(slope_20),
                "trend_r2_20": _safe(r2_20),
                "trend_slope_60": _safe(slope_60),
                "trend_r2_60": _safe(r2_60),
                # ── position in range, and how RECENT the extremes are
                #    (Qlib RSV / IMAX / IMIN) ──────────────────────────────
                "rsv_20": _safe((close - min(lows_20)) / span_20, 0.5) if span_20 else 0.5,
                "high_recency_20": _safe(highs_20.index(max(highs_20)) / 19.0),
                "low_recency_20": _safe(lows_20.index(min(lows_20)) / 19.0),
                # ── breadth of the move within the window ─────────────────
                "up_frac_20": _safe(sum(1 for r in recent_returns if r > 0) / len(recent_returns))
                              if recent_returns else 0.5,
                "gain_share_20": _safe(sum(gains) / churn, 0.5) if churn else 0.5,
                "streak": float(streak),
                "ret_skew_20": _safe(_skew(recent_returns)),
                # ── volume behaviour, not just its 5/20 ratio ─────────────
                "vol_cv_20": _safe(statistics.stdev(vol_20_window) / vol_20, 0.0)
                             if vol_20 and len(vol_20_window) >= 2 else 0.0,
                "corr_ret_vol_20": _safe(_corr(recent_returns, volumes[index - 19 : index + 1][1:])),
            },
            "next_open_to_close": _safe(next_bar["close"] / next_bar["open"] - 1.0),
            "next_close_to_close": _safe(next_bar["close"] / close - 1.0),
        })
    return rows


def _skew(values: Sequence[float]) -> float:
    """Return asymmetry — a fat left tail and a fat right tail are different
    regimes that identical mean and stdev cannot distinguish."""
    n = len(values)
    if n < 3:
        return 0.0
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    if var <= 0:
        return 0.0
    return (sum((v - mean) ** 3 for v in values) / n) / (var ** 1.5)


def _corr(left: Sequence[float], right: Sequence[float]) -> float:
    """Pearson correlation over the overlapping tail. Price-volume agreement
    is one of the most-used inputs in the formulaic-alpha literature and the
    old feature set had no way to express it."""
    size = min(len(left), len(right))
    if size < 3:
        return 0.0
    a, b = list(left[-size:]), list(right[-size:])
    mean_a, mean_b = sum(a) / size, sum(b) / size
    saa = sum((x - mean_a) ** 2 for x in a)
    sbb = sum((y - mean_b) ** 2 for y in b)
    if saa <= 0 or sbb <= 0:
        return 0.0
    sab = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(size))
    return sab / math.sqrt(saa * sbb)


# Features that also get a CROSS-SECTIONAL rank. This is a whole axis the old
# set lacked: every feature was per-symbol time-series, so a candidate could
# never ask "which name is the strongest today" — and cross-sectional rank is
# what most of the formulaic-alpha literature is built on (Kakushadze 2016,
# "101 Formulaic Alphas"; holding periods of 0.6-6.4 days, which is exactly
# this family's next-session payoff).
CROSS_SECTIONAL_SOURCES = (
    "ret_1d", "ret_5d", "ret_20d", "rv_20d",
    "vol_ratio_5_20", "dist_high_20", "range_pos", "dist_ma_20",
)


def _add_cross_sectional_ranks(rows: list[dict[str, Any]]) -> None:
    """Add xs_rank_* in [0,1] per session, computed across the symbols present
    in THAT session only — no lookahead and no survivorship: a symbol absent
    on a day simply is not ranked on that day.

    With a single-symbol universe every rank is 0.5 by construction. That is
    honest rather than useful; a candidate leaning on it in a one-name mission
    is reading a constant, and the mission text says so.
    """
    by_session: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_session.setdefault(row["session"], []).append(row)
    for session_rows in by_session.values():
        count = len(session_rows)
        for name in CROSS_SECTIONAL_SOURCES:
            ordered = sorted(session_rows, key=lambda r: r["features"].get(name, 0.0))
            for position, row in enumerate(ordered):
                row["features"][f"xs_rank_{name}"] = (
                    0.5 if count < 2 else round(position / (count - 1), 8)
                )


def _collect(bars: Any) -> list[dict[str, Any]]:
    return [{
        "session": bar.timestamp_utc.date().isoformat(),
        "open": bar.open, "high": bar.high, "low": bar.low,
        "close": bar.close, "volume": bar.volume,
    } for bar in bars]


def _alpaca_daily_bars(provider: AlpacaHistoricalProvider, *, asset_class: str,
                       symbols: tuple[str, ...], start_utc: datetime,
                       end_utc: datetime) -> dict[str, list[dict[str, Any]]]:
    # One request per symbol: the provider's Bar contract carries no symbol
    # field, so a combined multi-symbol response cannot be attributed safely.
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for symbol in symbols:
        snapshot = provider.bars(
            asset_class=asset_class, symbols=(symbol,),
            start_utc=start_utc, end_utc=end_utc, timeframe="1Day",
        )
        by_symbol[symbol] = _collect(snapshot.bars)
    return by_symbol


def _massive_daily_bars(provider: Any, *, asset_class: str, symbols: tuple[str, ...],
                        start_utc: datetime, end_utc: datetime) -> dict[str, list[dict[str, Any]]]:
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for symbol in symbols:
        if asset_class == "crypto":
            snapshot = provider.crypto_aggregate_bars(
                ticker=symbol.replace("/", "").upper() if not symbol.startswith("X:") else symbol,
                start_utc=start_utc, end_utc=end_utc, multiplier=1, timespan="day",
            )
        else:
            snapshot = provider.stock_aggregate_bars(
                ticker=symbol, start_utc=start_utc, end_utc=end_utc,
                multiplier=1, timespan="day",
            )
        by_symbol[symbol] = _collect(snapshot.bars)
    return by_symbol


def _resolve_provider(name: str) -> tuple[str, Any, Any]:
    """Pick the bar source explicitly; never silently fall back between them.

    'auto' prefers Alpaca (the stated primary) and uses Polygon/Massive only
    when Alpaca has no credentials -- and the choice is recorded in the
    manifest either way, so a snapshot always names the source it came from.
    """
    from research.providers.contracts import CapabilityProbeError
    from research.providers.massive import MassiveHistoricalProvider

    # Provider credentials live in the repo .env; the web service is started by
    # a scheduled task that does not inherit them.
    try:
        from dotenv import load_dotenv  # noqa: PLC0415

        load_dotenv(REPOSITORY_ROOT / ".env")
    except ImportError:
        pass

    if name in {"alpaca", "auto"}:
        try:
            return "alpaca", AlpacaHistoricalProvider.from_env(), _alpaca_daily_bars
        except CapabilityProbeError:
            if name == "alpaca":
                raise
    return "massive", MassiveHistoricalProvider.from_env(), _massive_daily_bars


PAYOFF_FIELDS = ("next_open_to_close", "next_close_to_close")
DEFAULT_BENCHMARK = "SPY"


def _attach_relative_payoffs(rows: list[dict[str, Any]], benchmark: str | None, *,
                             closed_zero: bool = False) -> int:
    """Add benchmark-relative payoffs alongside the raw ones.

    A discovery window with a strong market drift lets a rule pass on beta
    alone: bars campaign 1 promoted `ret_20d < -5%` AND `ret_20d > +5%`, both
    long, because holding anything paid over 2021-2024. Subtracting the
    benchmark's same-session return prices that drift out, so a rule has to
    beat the market on the sessions it chooses rather than merely be in it.

    Same-session and same-payoff-window by construction, so this introduces no
    lookahead: it is the identical forward interval, differenced.

    With ``closed_zero``, sessions where the benchmark did not trade (an
    equity benchmark against 24/7 crypto sessions) take a zero benchmark
    return — the benchmark market genuinely did not move — so the relative
    payoff equals the raw one instead of being left absent.
    """
    if not benchmark:
        return 0
    bench_by_session: dict[str, dict[str, float]] = {
        row["session"]: {field: row[field] for field in PAYOFF_FIELDS}
        for row in rows
        if row["symbol"] == benchmark
    }
    if not bench_by_session:
        raise ValueError(
            f"benchmark {benchmark} is not in the universe; add it or pass benchmark=None"
        )
    for row in rows:
        bench = bench_by_session.get(row["session"])
        if bench is None:
            if closed_zero:
                # Benchmark market closed that session (equity benchmark vs a
                # 24/7 crypto session): its return is genuinely zero, so the
                # relative payoff equals the raw one.
                for field in PAYOFF_FIELDS:
                    row[f"{field}_rel"] = row[field]
            # Otherwise leave the relative fields absent rather than silently
            # differencing against zero.
            continue
        for field in PAYOFF_FIELDS:
            if bench is not None:
                row[f"{field}_rel"] = round(row[field] - bench[field], 8)
    return len(bench_by_session)


def _suggested_splits(sessions: list[str]) -> dict[str, tuple[str, str]]:
    """Chronological 60/20/20 over observed sessions; compiler may override."""

    if len(sessions) < 15:
        raise ValueError(f"only {len(sessions)} decidable sessions; need at least 15")
    discovery_end = sessions[int(len(sessions) * 0.6) - 1]
    validation_end = sessions[int(len(sessions) * 0.8) - 1]
    later = [session for session in sessions if session > discovery_end]
    holdout_start_pool = [session for session in later if session > validation_end]
    return {
        "discovery": (sessions[0], discovery_end),
        "validation": (later[0], validation_end),
        "holdout": (holdout_start_pool[0], sessions[-1]),
    }


def prepare(*, symbols: tuple[str, ...], start: str, end: str, out: Path,
            asset_class: str = "equity", source: str = "auto",
            benchmark: str | None = DEFAULT_BENCHMARK) -> dict[str, Any]:
    provider_name, provider, fetch = _resolve_provider(source)
    # A universe without the benchmark used to fail here outright, which
    # killed every single-name equity idea ("benchmark SPY is not in the
    # universe", autopilot backlog, 2026-08-15). The benchmark prices drift
    # out of the payoff — the discipline those ideas need MOST — so fetch it
    # alongside the universe instead of dropping it. The evaluator filters
    # candidate rows to the spec universe, so benchmark rows can never be
    # traded; they exist only to compute the _rel fields. Crypto passes used
    # to drop the benchmark, which let BTC bull-window beta pass as alpha
    # (btc-streak-momentum-v2's accepted "RSI" candidate was always-long,
    # 2026-08-16): the drift benchmark (IBIT, SPY) is an equity, so fetch it
    # through the equity bar path and treat its closed sessions as zero
    # return — they genuinely did not move.
    fetch_symbols = symbols
    benchmark_via_equity = False
    if benchmark and benchmark not in symbols:
        if asset_class == "equity":
            fetch_symbols = tuple(symbols) + (benchmark,)
        else:
            benchmark_via_equity = True
    start_utc = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=UTC)
    end_utc = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=UTC) + timedelta(days=1)
    by_symbol = fetch(
        provider, asset_class=asset_class, symbols=fetch_symbols,
        start_utc=start_utc, end_utc=end_utc,
    )
    if benchmark_via_equity:
        _, bench_provider, bench_fetch = _resolve_provider(source)
        bench_bars = bench_fetch(
            bench_provider, asset_class="equity", symbols=(benchmark,),
            start_utc=start_utc, end_utc=end_utc,
        )
        if not bench_bars.get(benchmark):
            raise ValueError(
                f"no equity bars returned for benchmark {benchmark}; "
                "pass benchmark=None to keep absolute payoffs"
            )
        by_symbol[benchmark] = bench_bars[benchmark]
        fetch_symbols = tuple(fetch_symbols) + (benchmark,)
    for symbol, series in by_symbol.items():
        series.sort(key=lambda item: item["session"])
        sessions = [item["session"] for item in series]
        if len(sessions) != len(set(sessions)):
            # More than one bar per session means the provider did not return
            # daily bars; a snapshot built from that would be silently wrong.
            raise ValueError(
                f"{symbol}: provider returned multiple bars for one session; "
                "expected exactly one daily bar per session"
            )
    missing = [symbol for symbol in symbols if not by_symbol.get(symbol)]
    if missing:
        raise ValueError(f"no bars returned for: {', '.join(missing)}; refusing partial universe")

    rows: list[dict[str, Any]] = []
    # fetch_symbols, not symbols: a benchmark fetched alongside the universe
    # must become rows or _attach_relative_payoffs has nothing to difference
    # against. The evaluator filters candidate rows to the spec universe, so
    # benchmark rows are reference data, never tradeable.
    event_sessions, events_hash = load_event_sessions()
    for symbol in fetch_symbols:
        rows.extend(_feature_rows(symbol, by_symbol[symbol], event_sessions))
    rows.sort(key=lambda row: (row["session"], row["symbol"]))
    if not rows:
        raise ValueError("no decidable sessions after warmup; widen the date range")
    # Cross-sectional ranks need every symbol's row for a session, so this runs
    # after the per-symbol pass and before anything hashes or freezes the data.
    _add_cross_sectional_ranks(rows)
    benchmark_sessions = _attach_relative_payoffs(
        rows, benchmark, closed_zero=benchmark_via_equity
    )

    out.mkdir(parents=True, exist_ok=True)
    data_path = out / "sessions.jsonl"
    # Write bytes, not text: on Windows write_text translates \n to \r\n, so a
    # hash taken over the pre-write string never matches the file the validator
    # reads back, and every candidate dies on a frozen-hash mismatch.
    payload = ("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n").encode("utf-8")
    data_path.write_bytes(payload)
    data_hash = hashlib.sha256(payload).hexdigest()

    sessions = sorted({row["session"] for row in rows})
    manifest = {
        "provider": provider_name, "asset_class": asset_class,
        "symbols": list(symbols), "requested_start": start, "requested_end": end,
        "first_session": sessions[0], "last_session": sessions[-1],
        "session_count": len(sessions), "row_count": len(rows),
        "benchmark": benchmark, "benchmark_sessions": benchmark_sessions,
        "data_sha256": data_hash,
        # Provenance for the calendar the event features were computed from;
        # a changed calendar also changes data_sha256 above, so old snapshots
        # fail their hash check rather than silently evaluating a different
        # event history.
        "macro_events_sha256": events_hash,
        "suggested_splits": {name: list(window) for name, window in _suggested_splits(sessions).items()},
        "prepared_at_utc": datetime.now(UTC).isoformat(),
        "feature_warmup_sessions": FEATURE_WARMUP_SESSIONS,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", required=True, help="comma-separated, e.g. SPY,QQQ,IWM")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--asset-class", default="equity", choices=("equity", "crypto"))
    parser.add_argument("--source", default="auto", choices=("auto", "alpaca", "massive"))
    args = parser.parse_args(argv)
    symbols = tuple(symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip())
    manifest = prepare(symbols=symbols, start=args.start, end=args.end,
                       out=args.out, asset_class=args.asset_class, source=args.source)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
