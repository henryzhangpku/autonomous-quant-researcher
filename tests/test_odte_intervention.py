"""Rule-evaluation tests for the 0DTE intervention sweep.

Synthetic path rows, hand-built and round-tripped through JSONL so the
sweep consumes exactly what the staging contract emits. No network, no
validator subprocess: only the pure per-session rule math.
"""

from __future__ import annotations

import json

import pytest

from research.experiments.sweep_odte_intervention import (
    EVENT_COST_FRAC,
    derive_series,
    evaluate_rules,
)


def _row(session: str = "2026-08-10", **overrides) -> dict:
    row = {
        "session": session,
        "symbol": "SPY-0DTE-PATH-NM",
        "spot": {"12": 100.0, "13": 99.0, "14": 97.0, "15": 96.0},
        "put": {
            "short_strike": 99.0, "long_strike": 94.0, "width": 5.0, "credit": 1.0,
            "short_marks": {"12": 2.0, "13": 3.2, "14": 4.5, "15": 4.8},
            "long_marks": {"12": 1.0, "13": 1.2, "14": 1.5, "15": 1.6},
        },
        "call": {
            "short_strike": 101.0, "long_strike": 106.0, "width": 5.0, "credit": 1.0,
            "short_marks": {"12": 1.6, "13": 1.4, "14": 1.2, "15": 1.0},
            "long_marks": {"12": 0.6, "13": 0.5, "14": 0.4, "15": 0.3},
        },
    }
    row.update(overrides)
    return row


def _roundtrip(tmp_path, row: dict) -> dict:
    path = tmp_path / "sessions.jsonl"
    path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")
    return json.loads(path.read_text(encoding="utf-8").splitlines()[0])


def test_put_m1_triggers_at_14_exit_pricing_and_one_event_cost(tmp_path):
    # Spread values: 12: 1.0, 13: 2.0, 14: 3.0, 15: 3.2; credit 1.0, width 5.
    # M1 (3x credit = 3.0) first breached at 14:00.
    row = _roundtrip(tmp_path, _row())
    pnl = evaluate_rules(row)["m1"]["put"]
    assert pnl == pytest.approx((1.0 - 3.0) / 5.0 - EVENT_COST_FRAC)
    # M2 (2x credit = 2.0) is already breached at 13:00 (value exactly 2.0).
    assert evaluate_rules(row)["m2"]["put"] == pytest.approx((1.0 - 2.0) / 5.0 - EVENT_COST_FRAC)
    # M0: settle 96.0 -> intrinsic max(0, 99-96) - max(0, 94-96) = 3.0.
    assert evaluate_rules(row)["m0"]["put"] == pytest.approx((1.0 - 3.0) / 5.0)


def test_strike_touch_requires_a_cross_not_a_tag(tmp_path):
    # Spot path 100 -> 99 -> 97 -> 96 with short strike 99: 13:00 tags the
    # strike but does not cross it; the first cross is 14:00 (value 3.0).
    row = _roundtrip(tmp_path, _row())
    pnl = evaluate_rules(row)["m3"]["put"]
    assert pnl == pytest.approx((1.0 - 3.0) / 5.0 - EVENT_COST_FRAC)


def test_calm_session_every_rule_equals_hold(tmp_path):
    # Nothing breaches: spread value stays under 2x credit and spot never
    # crosses either short strike, so M1..M5 are all the M0 payoff.
    row = _roundtrip(tmp_path, _row(**{
        "spot": {"12": 100.0, "13": 100.2, "14": 100.1, "15": 100.3},
        "put": {
            "short_strike": 98.0, "long_strike": 93.0, "width": 5.0, "credit": 1.0,
            "short_marks": {"12": 1.4, "13": 1.3, "14": 1.1, "15": 0.9},
            "long_marks": {"12": 0.4, "13": 0.4, "14": 0.3, "15": 0.3},
        },
        "call": {
            "short_strike": 102.0, "long_strike": 107.0, "width": 5.0, "credit": 0.9,
            "short_marks": {"12": 1.3, "13": 1.2, "14": 1.1, "15": 1.0},
            "long_marks": {"12": 0.4, "13": 0.4, "14": 0.3, "15": 0.3},
        },
    }))
    rules = evaluate_rules(row)
    for side in ("put", "call"):
        hold = rules["m0"][side]
        for rule in ("m1", "m2", "m3", "m4", "m5"):
            assert rules[rule][side] == pytest.approx(hold), (rule, side)


def test_call_m4_condor_conversion_priced_at_trigger_hour(tmp_path):
    # Call side: credit 1.0; spread value 12: 1.0, 13: 3.2 -> M1 fires at 13.
    # Conversion sells the staged put spread at its 13:00 mark (1.8 - 0.9 =
    # 0.9 credit); settle 100 puts the put spread fully OTM (intrinsic 0).
    call = {
        "short_strike": 101.0, "long_strike": 106.0, "width": 5.0, "credit": 1.0,
        "short_marks": {"12": 1.6, "13": 3.7, "14": 4.0, "15": 4.2},
        "long_marks": {"12": 0.6, "13": 0.5, "14": 0.5, "15": 0.4},
    }
    put = {
        "short_strike": 95.0, "long_strike": 90.0, "width": 5.0, "credit": 1.1,
        "short_marks": {"12": 2.0, "13": 1.8, "14": 1.6, "15": 1.4},
        "long_marks": {"12": 0.9, "13": 0.9, "14": 0.8, "15": 0.7},
    }
    row = _roundtrip(tmp_path, _row(
        spot={"12": 100.0, "13": 101.5, "14": 102.0, "15": 100.0}, put=put, call=call))
    expected = (1.0 - 3.2 + 0.9 - 0.0) / 5.0 - 2.0 * EVENT_COST_FRAC
    assert evaluate_rules(row)["m4"]["call"] == pytest.approx(expected)


def test_m4_drops_session_when_opposite_side_not_staged(tmp_path):
    # Same call-side trigger, but the row carries no put side: the M4 series
    # drops the session while M0/M5 keep it.
    call = {
        "short_strike": 101.0, "long_strike": 106.0, "width": 5.0, "credit": 1.0,
        "short_marks": {"12": 1.6, "13": 3.7, "14": 4.0, "15": 4.2},
        "long_marks": {"12": 0.6, "13": 0.5, "14": 0.5, "15": 0.4},
    }
    row = _roundtrip(tmp_path, {"session": "2026-08-10", "symbol": "SPY-0DTE-PATH-NM",
                                "spot": {"12": 100.0, "13": 101.5, "14": 102.0, "15": 100.0},
                                "call": call})
    rules = evaluate_rules(row)
    assert rules["m4"]["call"] is None
    assert rules["m0"]["call"] is not None
    assert rules["m5"]["call"] is not None
    assert "put" not in rules["m0"]


def test_put_m5_runner_finishes_itm(tmp_path):
    # M1 fires at 13:00 (spread value 3.5 - 0.4 = 3.1 >= 3.0): buy back the
    # short at 3.5, keep the 94-put; settle 93 pays intrinsic 1.0.
    row = _roundtrip(tmp_path, _row(**{
        "spot": {"12": 100.0, "13": 98.0, "14": 95.0, "15": 93.0},
        "put": {
            "short_strike": 99.0, "long_strike": 94.0, "width": 5.0, "credit": 1.0,
            "short_marks": {"12": 2.0, "13": 3.5, "14": 4.2, "15": 4.9},
            "long_marks": {"12": 1.0, "13": 0.4, "14": 0.8, "15": 1.0},
        },
    }))
    expected = (1.0 - 3.5 + 1.0) / 5.0 - EVENT_COST_FRAC
    assert evaluate_rules(row)["m5"]["put"] == pytest.approx(expected)


def test_carry_forward_missing_13_mark_uses_12_mark(tmp_path):
    # No 13:00 prints on either leg: the 13:00 trigger evaluates the 12:00
    # marks, so the 12:00 spread value of 3.0 fires M1 at 13:00, not 14:00.
    row = _roundtrip(tmp_path, _row(**{
        "put": {
            "short_strike": 99.0, "long_strike": 94.0, "width": 5.0, "credit": 1.0,
            "short_marks": {"12": 3.5, "14": 4.5, "15": 4.8},
            "long_marks": {"12": 0.5, "14": 1.5, "15": 1.6},
        },
    }))
    # Trigger at 13 on the carried value 3.5 - 0.5 = 3.0 (>= 3x credit 3.0).
    assert evaluate_rules(row)["m1"]["put"] == pytest.approx((1.0 - 3.0) / 5.0 - EVENT_COST_FRAC)


def test_derive_series_carries_validator_fields(tmp_path):
    row = _roundtrip(tmp_path, _row())
    pnl = evaluate_rules(row)["m1"]["put"]
    derived = derive_series(row, "put", "m1", pnl)
    assert derived["symbol"] == "SPY-0DTE-PATH-NM-put-m1"
    assert derived["next_open_to_close"] == derived["next_close_to_close"]
    assert derived["next_open_to_close"] == pytest.approx(pnl)
    assert derived["features"]["credit_frac"] == pytest.approx(0.2)
    assert derived["features"]["day_of_week"] == float(0)  # 2026-08-10 is a Monday
    assert derived["features"]["underlying"] == 100.0
    assert derived["spy_session_ret"] == pytest.approx(96.0 / 100.0 - 1.0)
