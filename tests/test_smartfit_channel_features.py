"""The pivot-anchored channel and ADX features must be causal and conventional.

`_channel_series` is the one feature in the daily family whose value depends
on a STATE MACHINE rather than a fixed trailing window, and the construct it
ports — a swing pivot confirmed by P bars on each side — is the classic way a
chart indicator reads the future by accident. A swing high at bar `i` is not
knowable until bar `i + P`; an implementation that anchors its regression at
`i` on bar `i` produces a backtest that cannot be traded and looks excellent.

So the load-bearing test here is TRUNCATION INVARIANCE: the value emitted at
bar `t` must be identical whether or not the series continues past `t`. That
is the operational definition of "computed strictly from bars up to and
including the decision session," and it is a property the fixed-window
features get for free and this one does not.
"""

from __future__ import annotations

import math

from research.experiments.prepare_bars_universe import (
    CHANNEL_DEV_MULT,
    CHANNEL_FIT_QUALITY,
    CHANNEL_MIN_FIT_BARS,
    CHANNEL_PIVOT_BARS,
    CHANNEL_Z_CLAMP,
    _adx_series,
    _channel_series,
    _feature_rows,
)


def _wave_bars(count: int, *, amplitude: float = 8.0, period: int = 21,
               drift: float = 0.15, start: float = 100.0) -> list[dict]:
    """Bars with real swing pivots — a trending sine, so pivots genuinely fire.

    A monotone series has no interior swing highs at all, which would let a
    broken pivot detector pass by never being exercised.
    """
    bars = []
    for day in range(count):
        mid = start + drift * day + amplitude * math.sin(2 * math.pi * day / period)
        # Deterministic, non-constant intrabar range so highs/lows are distinct.
        wobble = 0.4 + 0.2 * math.sin(day * 1.7)
        bars.append({
            "session": f"2026-{1 + day // 28:02d}-{1 + day % 28:02d}",
            "open": mid - 0.1,
            "high": mid + wobble,
            "low": mid - wobble,
            "close": mid,
            "volume": 1_000_000.0 + 1_000.0 * day,
        })
    return bars


def test_channel_is_truncation_invariant() -> None:
    """The defining causality property: a prefix produces identical values.

    If any part of the state machine reached forward — an unconfirmed pivot,
    a band fitted with a later bar, a breakout detected before its close —
    then recomputing on bars[:t+1] would disagree with the full-series value
    at t. This is the test that would have caught the P-bar lookahead.
    """
    bars = _wave_bars(260)
    full = _channel_series(bars)
    # Every bar past the point where a channel can exist at all.
    for cut in range(2 * CHANNEL_PIVOT_BARS + 4, len(bars)):
        prefix = _channel_series(bars[: cut + 1])
        for name, whole, part in zip(
            ("chan_bars", "chan_slope", "chan_pearson", "chan_z", "chan_breakout"),
            full, prefix,
        ):
            assert whole[cut] == part[cut], (
                f"{name} at bar {cut} changed when later bars were revealed: "
                f"{part[cut]} (causal) vs {whole[cut]} (full series)"
            )


def test_adx_is_truncation_invariant_and_bounded() -> None:
    """ADX is Wilder-recursive; a prefix must still agree bar for bar."""
    bars = _wave_bars(260)
    full = _adx_series(bars)
    for cut in range(40, len(bars)):
        assert _adx_series(bars[: cut + 1])[cut] == full[cut], f"adx_14 drifted at {cut}"
    warm = full[2 * 14 :]
    assert warm, "the fixture must clear ADX warmup"
    assert all(0.0 <= value <= 100.0 for value in warm), "ADX left its 0-100 scale"
    assert any(value > 0.0 for value in warm), "ADX never left zero on a trending fixture"


def test_reanchoring_never_uses_an_unconfirmed_pivot() -> None:
    """A channel may only start where the tape could already see a start.

    The source re-anchors to the opposite pivot when that pivot clears its own
    |r| gate, and otherwise restarts EMPTY at the current bar
    (`newStartIndex = candValid ? candIndex : bar_index`). So on the bar where
    the anchor MOVES it is either the current bar — zero lag, nothing assumed
    — or a pivot, which needs CHANNEL_PIVOT_BARS of right-hand confirmation.
    An anchor landing 1..P-1 bars back is the signature of a pivot taken
    before the tape could confirm it.

    Once set, an anchor legitimately ages: a fresh restart at bar t is still
    the anchor at t+3. Only the bars where it CHANGES carry the claim.
    """
    bars = _wave_bars(260)
    chan_bars, _, _, _, _ = _channel_series(bars)
    # A channel shorter than 3 bars emits length 0, so a fresh restart is
    # first OBSERVED a couple of bars after it happened. Compare against the
    # last observed bar, not t-1, or that gap reads as a short-lag pivot.
    live = [(t, t - int(length) + 1) for t, length in enumerate(chan_bars) if length > 0]
    assert len(live) > 100, "the fixture did not exercise enough live channels"
    moves = 0
    prev_t, prev_anchor = -1, None
    for t, anchor in live:
        assert 0 <= anchor <= t, f"anchor {anchor} outside [0, {t}]"
        if anchor == prev_anchor:
            prev_t = t
            continue  # unchanged; the claim is about the bar it moved
        moves += 1
        # Legitimate origins: the series-start floor, or a fresh restart —
        # which the source places at the BREAKOUT bar, i.e. anywhere in
        # [prev_t, t] — or a pivot with its P bars of confirmation. Only an
        # anchor strictly BEFORE the last observed bar has to be a pivot.
        if anchor != 0 and anchor < prev_t:
            assert t - anchor >= CHANNEL_PIVOT_BARS, (
                f"bar {t} re-anchored to {anchor}, only {t - anchor} bars back — "
                f"a pivot needs {CHANNEL_PIVOT_BARS} bars of confirmation"
            )
        prev_t, prev_anchor = t, anchor
    assert moves > 5, "the fixture never re-anchored, so the rule went untested"


def test_breakout_fires_once_per_excursion() -> None:
    """SmartFit alerts on the breakout bar, not every bar price stays outside.

    A repeating flag would multiply one excursion into a run of trades and
    inflate any rule keyed on it.
    """
    bars = _wave_bars(600, amplitude=14.0, period=37)
    _, _, _, z_values, breakouts = _channel_series(bars)
    assert any(breakouts), "the fixture produced no breakouts to test"
    for t in range(1, len(breakouts)):
        if breakouts[t] > 0 and breakouts[t - 1] > 0:
            raise AssertionError(f"repeated upside breakout at bars {t - 1},{t}")
        if breakouts[t] < 0 and breakouts[t - 1] < 0:
            raise AssertionError(f"repeated downside breakout at bars {t - 1},{t}")
    for t, flag in enumerate(breakouts):
        if flag > 0:
            assert z_values[t] > CHANNEL_DEV_MULT, f"upside flag at {t} inside the band"
        elif flag < 0:
            assert z_values[t] < -CHANNEL_DEV_MULT, f"downside flag at {t} inside the band"


def test_pearson_sign_tracks_slope_and_values_stay_in_range() -> None:
    """chan_pearson is SIGNED — that sign is the channel's bias gauge.

    The family already carries trend_r2_20, which is squared and therefore
    cannot say which way the channel leans. If the sign did not track the
    slope, the new feature would be a duplicate of the old one.
    """
    bars = _wave_bars(320)
    _, slopes, pearsons, z_values, _ = _channel_series(bars)
    checked = 0
    for slope, r in zip(slopes, pearsons):
        assert -1.0 <= r <= 1.0, f"pearson {r} outside [-1, 1]"
        if slope != 0.0 and abs(r) > 1e-9:
            assert (slope > 0) == (r > 0), f"slope {slope} disagrees with pearson {r}"
            checked += 1
    assert checked > 50, "not enough live channels to test the sign"
    assert all(abs(z) <= CHANNEL_Z_CLAMP for z in z_values), "chan_z escaped its clamp"
    assert any(r > 0.5 for r in pearsons), "no well-fitted channel in a trending fixture"


def test_prepared_rows_carry_finite_channel_features() -> None:
    """The features must survive the real row builder, not just the helper."""
    rows = _feature_rows("SPY", _wave_bars(300))
    assert rows, "fixture produced no decidable sessions"
    names = ("chan_bars", "chan_slope", "chan_pearson", "chan_z", "chan_breakout", "adx_14")
    for row in rows:
        for name in names:
            value = row["features"][name]
            assert isinstance(value, (int, float)) and math.isfinite(value), (name, value)
    assert {row["features"]["chan_breakout"] for row in rows} <= {-1.0, 0.0, 1.0}
    # A feature that is constant across the whole fixture carries no
    # information and would silently make every rule built on it degenerate.
    for name in ("chan_bars", "chan_slope", "chan_pearson", "chan_z", "adx_14"):
        assert len({row["features"][name] for row in rows}) > 5, f"{name} is near-constant"
