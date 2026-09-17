"""Offline tests for the 0DTE backtest engine (synthetic sessions, no network)."""

from __future__ import annotations

import math

import pytest

from research.backtest import (
    BacktestEngine, CostModel, Session, bs_price, call_debit_spread,
    long_put, mde_two_arm, put_credit_spread, wilson_ci,
)


def make_session(day: str, opens: list[float], spread: float = 0.2) -> Session:
    """5-min bars 09:30..15:55 built from a price path (padded/truncated to 78)."""
    n = 78
    path = (opens * ((n // len(opens)) + 1))[:n]
    bars = []
    for i, px in enumerate(path):
        hm = 9 * 60 + 30 + 5 * i
        bars.append((hm, px, px + spread, px - spread, px))
    return Session(day=day, bars=tuple(bars))


def flat_universe(n_days: int, price: float = 500.0, wiggle: float = 0.0) -> list[Session]:
    out = []
    for i in range(n_days):
        px = price + (wiggle if i % 2 else -wiggle)
        out.append(make_session(f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", [px]))
    return out


# -- structures ---------------------------------------------------------------

def test_call_debit_spread_payoff_and_width():
    s = call_debit_spread(500.0, width=2.0)
    assert s.width == 2.0
    assert s.settle(499.0) == 0.0          # below long: worthless
    assert s.settle(501.0) == 1.0          # between: intrinsic on long only
    assert s.settle(505.0) == 2.0          # past short: capped at width


def test_put_credit_spread_is_a_credit_with_mirror_payoff():
    s = put_credit_spread(500.0, width=2.0)
    prices = {("P", 500.0): 1.0, ("P", 498.0): 0.4}
    assert s.entry_cost(prices) == pytest.approx(-0.6)   # credit received
    assert s.settle(505.0) == 0.0                        # OTM: keep credit
    assert s.settle(490.0) == -2.0                       # max loss = width


def test_bs_price_sane_and_parity():
    c = bs_price("C", 500.0, 500.0, 0.01)
    p = bs_price("P", 500.0, 500.0, 0.01)
    assert c > 0 and p > 0
    assert c - p == pytest.approx(0.0, abs=1e-9)         # ATM, r=0
    assert bs_price("C", 500.0, 400.0, 0.01) == pytest.approx(100.0, abs=0.01)


def test_dw_is_a_probability():
    s = call_debit_spread(500.0, width=2.0)
    debit = s.model_entry_cost(500.0, 0.008)
    assert 0.0 < debit / s.width < 1.0


# -- stats --------------------------------------------------------------------

def test_wilson_ci_brackets_the_point_estimate():
    lo, hi = wilson_ci(20, 100)
    assert lo < 0.2 < hi
    assert wilson_ci(0, 0) == (0.0, 0.0)


def test_mde_shrinks_with_n_and_diverges_at_zero():
    assert mde_two_arm(0) == float("inf")
    assert mde_two_arm(400) < mde_two_arm(50) < mde_two_arm(10)


# -- engine -------------------------------------------------------------------

def test_engine_separates_signal_from_control():
    universe = flat_universe(60, wiggle=4.0)
    engine = BacktestEngine()
    result = engine.run(
        sessions=universe, entry_hm=(12, 30),
        signal=lambda s, m: s.open < 500.0,        # exactly the wiggle-down days
        structure=lambda s, px: call_debit_spread(px, width=2.0),
        label="test",
    )
    assert 0 < len(result.rows) < len(result.control_rows)
    assert result.meta["cost_frac"] == CostModel().cost_frac


def test_generated_callbacks_receive_only_entry_time_information():
    universe = flat_universe(60, wiggle=4.0)
    observations = []

    def causal_signal(session, entry_minute):
        observations.append(
            {
                "has_close": hasattr(session, "close"),
                "has_bars": hasattr(session, "bars"),
                "future_price": session.price_at(entry_minute + 5),
                "future_touch": session.first_touch(0.0, entry_minute),
            }
        )
        return True

    result = BacktestEngine().run(
        sessions=universe,
        entry_hm=(12, 30),
        signal=causal_signal,
        structure=lambda session, price: call_debit_spread(price, width=2.0),
        label="causal view",
    )

    assert result.rows
    assert observations
    assert all(
        observation
        == {
            "has_close": False,
            "has_bars": False,
            "future_price": None,
            "future_touch": None,
        }
        for observation in observations
    )


def test_costs_always_reduce_pnl():
    universe = flat_universe(60)
    cheap = BacktestEngine(costs=CostModel(cost_frac=0.0))
    dear = BacktestEngine(costs=CostModel(cost_frac=0.10))
    kw = dict(
        sessions=universe, entry_hm=(12, 30),
        signal=lambda s, m: True,
        structure=lambda s, px: call_debit_spread(px, width=2.0),
        label="cost",
    )
    pnl_cheap = sum(r["pnl_w"] for r in cheap.run(**kw).control_rows)
    pnl_dear = sum(r["pnl_w"] for r in dear.run(**kw).control_rows)
    assert pnl_dear < pnl_cheap


def test_flat_market_spread_loses_the_debit():
    """No movement: settle at 0 intrinsic for an OTM-short spread, lose debit+cost."""
    universe = flat_universe(60)
    result = BacktestEngine().run(
        sessions=universe, entry_hm=(12, 30),
        signal=lambda s, m: True,
        structure=lambda s, px: call_debit_spread(px + 1.0, width=2.0, round_strikes=False),
        label="flat",
    )
    assert result.control_rows
    assert all(r["pnl_w"] < 0 for r in result.control_rows)
    assert all(not r["full_win"] for r in result.control_rows)


def test_hedge_fires_only_on_target_touch():
    # A day that rallies through the short strike, and a flat day.
    rally = make_session("2025-06-02", [500.0 + 0.1 * i for i in range(78)])
    flat = make_session("2025-06-03", [500.0])
    engine = BacktestEngine()
    kw = dict(
        entry_hm=(12, 30), signal=lambda s, m: True,
        structure=lambda s, px: call_debit_spread(px, width=2.0),
    )
    hedged = engine.run(sessions=[rally, flat] * 15, label="h", hedge_put_on_target=True, **kw)
    touched = [r for r in hedged.control_rows if r["hedged_at"] is not None]
    untouched = [r for r in hedged.control_rows if r["hedged_at"] is None]
    assert touched and untouched
    assert all(r["date"] == "2025-06-02" for r in touched)


def test_hedge_costs_show_up_as_pnl_drag_on_winning_days():
    rally = make_session("2025-06-02", [500.0 + 0.1 * i for i in range(78)])
    engine = BacktestEngine()
    kw = dict(
        sessions=[rally] * 25, entry_hm=(12, 30), signal=lambda s, m: True,
        structure=lambda s, px: call_debit_spread(px, width=2.0),
    )
    plain = engine.run(label="plain", **kw)
    hedged = engine.run(label="hedged", hedge_put_on_target=True, **kw)
    # The rally never comes back down, so the put expires worthless:
    # hedging must cost exactly its premium + costs, i.e. strictly less P&L.
    assert sum(r["pnl_w"] for r in hedged.control_rows) < sum(r["pnl_w"] for r in plain.control_rows)


def test_report_renders_without_error():
    universe = flat_universe(60, wiggle=4.0)
    result = BacktestEngine().run(
        sessions=universe, entry_hm=(12, 30),
        signal=lambda s, m: s.open < 500.0,
        structure=lambda s, px: call_debit_spread(px, width=2.0),
        label="render",
    )
    text = result.report()
    assert "signal" in text and "control" in text and "MDE" in text


def test_real_quotes_require_and_use_raw_underlying_strike_space():
    universe = flat_universe(30, wiggle=2.0)
    seen_legs = []

    def leg_pricer(_day, legs, _minute):
        seen_legs.append(legs)
        return {legs[0]: 1.5, legs[1]: 0.5}

    with pytest.raises(ValueError, match="raw_price_provider"):
        BacktestEngine().run(
            sessions=universe,
            entry_hm=(12, 30),
            signal=lambda s, m: True,
            structure=lambda s, px: call_debit_spread(px, width=2.0),
            label="unsafe adjusted strikes",
            leg_pricer=leg_pricer,
        )

    result = BacktestEngine().run(
        sessions=universe,
        entry_hm=(12, 30),
        signal=lambda s, m: True,
        structure=lambda s, px: call_debit_spread(px, width=2.0),
        label="raw strikes",
        leg_pricer=leg_pricer,
        raw_price_provider=lambda _day, minutes: {minutes[0]: 110.0, minutes[1]: 112.0},
    )

    assert result.control_rows
    assert seen_legs
    assert seen_legs[0] == [("C", 110.0), ("C", 112.0)]
    assert result.meta["raw_prices"] is True


def test_credit_spread_full_win_semantics():
    """A credit spread that expires OTM is a FULL WIN (settle=0), not a loss."""
    # wiggle gives realized vol > 0, so the model prices a real (nonzero) credit
    universe = flat_universe(60, wiggle=2.0)
    result = BacktestEngine(costs=CostModel(cost_frac=0.0)).run(
        sessions=universe, entry_hm=(12, 30),
        signal=lambda s, m: True,
        # short put 1 below spot; each day closes flat -> expires worthless -> keep credit
        structure=lambda s, px: put_credit_spread(px - 1.0, width=2.0, round_strikes=False),
        label="credit",
    )
    assert result.control_rows
    assert all(r["full_win"] for r in result.control_rows)
    assert all(r["pnl_w"] > 0 for r in result.control_rows)      # credit kept
    assert all(0.0 < r["dw"] < 1.0 for r in result.control_rows)  # implied prob sane
