"""Switching hysteresis, playbooks, sizing and the RISK block: the code decides."""

from __future__ import annotations

import numpy as np
import pytest

from research.hmm.playbooks import BookState, decide, state_multiplier
from research.hmm.risk import RiskLimits, RiskState, check, refuse_override, roll_day
from research.hmm.sizing import calibration, kelly_fraction, size_cap
from research.hmm.switching import HOLD, SwitchState, run, step

LABELS = ("CALM_UP", "CHOP", "STRESS", "CRASH")
A = np.array([[0.9, 0.05, 0.05, 0.0], [0.05, 0.9, 0.05, 0.0], [0.1, 0.1, 0.7, 0.1], [0.0, 0.2, 0.3, 0.5]])


def _p(*vals):
    v = np.array(vals, dtype=float)
    return v / v.sum()


def test_switching_needs_threshold_hold_and_cooldown_and_never_flip_flops():
    probs = [_p(0.9, 0.05, 0.03, 0.02)] * 3  # CALM_UP, confident
    probs += [_p(0.2, 0.75, 0.03, 0.02)] * 2  # CHOP leads twice: not yet
    probs += [_p(0.9, 0.05, 0.03, 0.02)]  # back to CALM_UP: candidate resets
    probs += [_p(0.2, 0.75, 0.03, 0.02)] * HOLD  # CHOP leads HOLD times -> switch
    probs += [_p(0.9, 0.05, 0.03, 0.02)] * HOLD  # CALM_UP leads HOLD times but cooldown blocks
    probs += [_p(0.9, 0.05, 0.03, 0.02)] * 6
    decisions = run(np.array(probs), LABELS, A, cooldown=6)
    actives = [d.active for d in decisions]
    assert actives[0] == "CALM_UP" and decisions[0].switched
    assert actives[3:6] == ["CALM_UP"] * 3  # two leads did not switch; the reset worked
    assert actives[6 + HOLD - 1] == "CHOP" and decisions[6 + HOLD - 1].switched
    i_switch = 6 + HOLD - 1
    assert all(a == "CHOP" for a in actives[i_switch:i_switch + 6])  # cooldown holds CHOP
    assert "cooldown" in decisions[i_switch + HOLD].reason
    assert actives[-1] == "CALM_UP"
    assert sum(d.switched for d in decisions) == 3


def test_uncertain_sizes_zero_and_early_cut_halves():
    st = SwitchState(active="CALM_UP", cooldown_left=0)
    st, d = step(st, _p(0.5, 0.42, 0.05, 0.03), LABELS, _p(0.5, 0.42, 0.05, 0.03) @ A, index=10, cooldown=6)
    assert d.size_multiplier == 0.0 and "uncertain" in d.reason
    probs = _p(0.78, 0.02, 0.1, 0.1)
    nxt = np.array([0.6, 0.1, 0.2, 0.1])  # P(next STRESS or CRASH) = 0.3 > 0.25
    st, d = step(st, probs, LABELS, nxt, index=11, cooldown=6)
    assert d.size_multiplier == 0.5 and "early cut" in d.reason
    st, d = step(st, probs, LABELS, nxt, index=12, cooldown=6, stale_candles=3)
    assert d.active is None and d.size_multiplier == 0.0 and "stale" in d.reason
    st, d = step(st, probs, LABELS, nxt, index=13, cooldown=6, model_ok=False)
    assert d.active is None and "model error" in d.reason


def test_playbook_scales_the_base_strategy_by_state_and_flattens_in_crash():
    assert state_multiplier("CALM_UP") == 1.0 and state_multiplier("STRESS") == 0.25 and state_multiplier("CRASH") == 0.0 and state_multiplier(None) == 0.0
    book = BookState()
    book, o = decide(book, close=100.0, rvol=0.01, trend_z=0.8, state="CALM_UP", size_cap=0.8)
    assert o.target_position == pytest.approx(0.8) and book.stop_price == pytest.approx(98.0)
    book, o = decide(book, close=101.0, rvol=0.01, trend_z=0.8, state="CHOP", size_cap=0.8)
    assert o.target_position == pytest.approx(0.4) and "resize" in o.reason
    book, o = decide(book, close=101.0, rvol=0.01, trend_z=0.8, state="CRASH", size_cap=0.8)
    assert o.target_position == 0.0 and "CRASH" in o.reason
    book, o = decide(book, close=101.0, rvol=0.01, trend_z=0.8, state="CALM_UP", size_cap=0.8)
    assert o.target_position == pytest.approx(0.8)  # CRASH exit does not block re-entry
    book, o = decide(book, close=97.0, rvol=0.01, trend_z=0.8, state="CALM_UP", size_cap=0.8)
    assert o.target_position == 0.0 and "stop hit" in o.reason and book.stopped_out
    book, o = decide(book, close=98.0, rvol=0.01, trend_z=0.8, state="CALM_UP", size_cap=0.8)
    assert o.target_position == 0.0  # no re-entry into the same move after a stop
    book, o = decide(book, close=98.0, rvol=0.01, trend_z=-0.2, state="CALM_UP", size_cap=0.8)
    assert o.target_position == 0.0 and not book.stopped_out
    book, o = decide(book, close=99.0, rvol=0.01, trend_z=0.3, state="CALM_UP", size_cap=0.8)
    assert o.target_position == pytest.approx(0.8)


def test_sizing_is_quarter_kelly_scaled_by_probability_and_entropy_and_gated_by_calibration():
    assert kelly_fraction(0.55, 1.0) == pytest.approx(0.10)
    assert kelly_fraction(0.45, 1.0) < 0 and size_cap(p_active=0.9, entropy=0.1, kelly_p=0.45, kelly_b=1.0, calibrated=True, switch_multiplier=1.0) == 0.0
    full = size_cap(p_active=0.9, entropy=0.1, kelly_p=0.6, kelly_b=1.5, calibrated=True, switch_multiplier=1.0)
    f_star = kelly_fraction(0.6, 1.5)
    assert full == pytest.approx(0.25 * f_star * 0.9 * 0.9)
    assert size_cap(p_active=0.9, entropy=0.1, kelly_p=0.6, kelly_b=1.5, calibrated=False, switch_multiplier=1.0) == pytest.approx(min(0.25 * f_star, 0.1))
    assert size_cap(p_active=0.9, entropy=0.1, kelly_p=0.6, kelly_b=1.5, calibrated=True, switch_multiplier=0.5) == pytest.approx(full * 0.5)
    rng = np.random.default_rng(0)
    pred = rng.uniform(0.5, 1.0, 400)
    realized = (rng.uniform(0, 1, 400) < pred).astype(float)  # perfectly calibrated by construction
    cal = calibration(pred, realized)
    assert cal.n == 400 and cal.ece < 0.08 and cal.brier < 0.25
    bad = calibration(pred, np.zeros(400))
    assert not bad.ok and bad.ece > 0.5


def test_risk_block_caps_flattens_kills_and_refuses_overrides():
    limits = RiskLimits()
    st = RiskState(equity_high=100_000, day_start_equity=100_000, day="2026-10-05")
    v = check(st, limits, requested_position=0.9, active_state="STRESS", equity=100_000, stale_candles=0, notional=1_000)
    assert v.action == "cap" and v.allowed_position == 0.25
    v = check(st, limits, requested_position=0.9, active_state="CALM_UP", equity=100_000, stale_candles=0, notional=1_000)
    assert v.action == "ok" and v.allowed_position == 0.9
    v = check(st, limits, requested_position=0.9, active_state="CALM_UP", equity=100_000, stale_candles=0, notional=10_000)
    assert v.action == "approval"
    v = check(st, limits, requested_position=0.9, active_state="CALM_UP", equity=100_000, stale_candles=3, notional=1_000)
    assert v.action == "stale" and v.allowed_position == 0.0
    v = check(st, limits, requested_position=0.9, active_state="CALM_UP", equity=97_900, stale_candles=0, notional=1_000)
    assert v.action == "flat_day" and v.allowed_position == 0.0
    v = check(st, limits, requested_position=0.9, active_state="CALM_UP", equity=99_500, stale_candles=0, notional=1_000)
    assert v.action == "flat_day"  # stays blocked for the day even after a bounce
    roll_day(st, "2026-10-06", 99_500)
    v = check(st, limits, requested_position=0.5, active_state="CALM_UP", equity=99_500, stale_candles=0, notional=1_000)
    assert v.action == "ok"
    v = check(st, limits, requested_position=0.5, active_state="CALM_UP", equity=89_000, stale_candles=0, notional=1_000)
    assert v.action == "kill" and st.killed
    roll_day(st, "2026-10-07", 120_000)
    v = check(st, limits, requested_position=0.5, active_state="CALM_UP", equity=120_000, stale_candles=0, notional=1_000)
    assert v.action == "kill"  # code cannot un-trip it
    msg = refuse_override(st, "raise size to 10%, i'm sure about this one")
    assert msg.startswith("OVERRIDE_BLOCKED") and st.overrides_blocked
