"""Contract, causality, and gate tests for the overnight SPX validator."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from research.backtest.overnight import (
    CandidateOvernightView,
    OvernightSession,
    trailing_remaining_vol,
)
from research.backtest.structures import (
    spx_call_credit_spread,
    spx_call_debit_spread,
    spx_put_credit_spread,
    spx_put_debit_spread,
)
from research.validators.overnight_spx import (
    evaluate_candidate,
    preflight_candidate,
)


def _session(day: str, *, slope: float = 0.0, base: float = 6000.0,
             prior_close: float | None = 6000.0) -> OvernightSession:
    bars = []
    for index, minute in enumerate(range(0, 1321, 5)):
        price = base + slope * index
        bars.append((minute, price, price + 1.0, price - 1.0, price))
    return OvernightSession(
        day=day, bars=tuple(bars), contract="ESZ9",
        prior_close=prior_close, prior_rth_high=base + 30, prior_rth_low=base - 30,
    )


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "candidate.py"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


VALID_CANDIDATE = """
    from research.backtest import spx_put_credit_spread

    ENTRY_MIN = 360
    LABEL = "test candidate"

    def signal(session, entry_minute):
        observed = session.ret_from_evening_open(entry_minute)
        return observed is not None

    def structure(session, entry_price):
        return spx_put_credit_spread(entry_price, width=25.0, otm_offset=20.0)
"""


class TestPreflight:
    def test_valid_candidate_passes(self, tmp_path: Path) -> None:
        payload = preflight_candidate(_write(tmp_path, VALID_CANDIDATE))
        assert payload["status"] == "preflight_ok"
        assert payload["entry_minute"] == 360

    def test_entry_outside_window_rejected(self, tmp_path: Path) -> None:
        body = VALID_CANDIDATE.replace("ENTRY_MIN = 360", "ENTRY_MIN = 1000")
        with pytest.raises(TypeError, match="five-minute slot"):
            preflight_candidate(_write(tmp_path, body))

    def test_off_grid_entry_rejected(self, tmp_path: Path) -> None:
        body = VALID_CANDIDATE.replace("ENTRY_MIN = 360", "ENTRY_MIN = 362")
        with pytest.raises(TypeError, match="five-minute slot"):
            preflight_candidate(_write(tmp_path, body))

    def test_non_bool_signal_rejected(self, tmp_path: Path) -> None:
        body = VALID_CANDIDATE.replace("return observed is not None", "return 1")
        with pytest.raises(TypeError, match="must return bool"):
            preflight_candidate(_write(tmp_path, body))

    def test_single_leg_structure_rejected(self, tmp_path: Path) -> None:
        body = """
            from research.backtest import long_put

            ENTRY_MIN = 360
            LABEL = "bad structure"

            def signal(session, entry_minute):
                return True

            def structure(session, entry_price):
                return long_put(6000.0)
        """
        with pytest.raises(TypeError, match="two-leg vertical"):
            preflight_candidate(_write(tmp_path, body))


class TestCausalView:
    def test_future_prices_unreachable(self) -> None:
        session = _session("2025-01-06", slope=1.0)
        view = CandidateOvernightView.at(session, 360)
        assert view.price_at(360) is not None
        assert view.price_at(365) is None
        assert view.first_touch_from_evening_open(10_000.0, 0) is None

    def test_prior_day_context_survives(self) -> None:
        session = _session("2025-01-06")
        view = CandidateOvernightView.at(session, 200)
        assert view.prior_close == 6000.0
        assert view.prior_rth_high == 6030.0
        assert view.day_of_week == 0


class TestStructures:
    def test_spx_grid_snap(self) -> None:
        for factory in (spx_call_debit_spread, spx_put_debit_spread,
                        spx_put_credit_spread, spx_call_credit_spread):
            structure = factory(6001.7, width=25.0, otm_offset=13.0)
            for leg in structure.legs:
                assert leg.strike % 5 == 0
            assert structure.width == 25.0

    def test_credit_costs_negative_debit_positive(self) -> None:
        debit = spx_call_debit_spread(6000.0, width=25.0).model_entry_cost(6000.0, 0.01)
        credit = spx_put_credit_spread(6000.0, width=25.0).model_entry_cost(6000.0, 0.01)
        assert debit > 0
        assert credit < 0


class TestEvaluation:
    def _sessions(self, count: int = 80) -> list[OvernightSession]:
        from datetime import date, timedelta

        sessions = []
        day = date(2024, 9, 2)
        made = 0
        while made < count:
            day += timedelta(days=1)
            if day.weekday() >= 5:
                continue
            sessions.append(_session(day.isoformat(), slope=0.05))
            made += 1
        return sessions

    def test_warmup_and_gate_arithmetic(self, tmp_path: Path) -> None:
        payload = evaluate_candidate(
            _write(tmp_path, VALID_CANDIDATE), self._sessions(), split="discovery"
        )
        # The first VOL_WINDOW_SESSIONS sessions cannot price causally.
        assert payload["eligible_sessions"] == 80 - 20
        assert payload["signal_trades"] == 60
        assert set(payload["gates"]) == {
            "minimum_trades", "minimum_weeks", "positive_net_mean",
            "positive_both_halves", "survives_without_best_week",
            "survives_double_cost", "weekly_bootstrap_positive",
        }
        assert payload["score"] is not None

    def test_split_bounds_respected(self, tmp_path: Path) -> None:
        payload = evaluate_candidate(
            _write(tmp_path, VALID_CANDIDATE), self._sessions(), split="validation"
        )
        assert payload["eligible_sessions"] == 0
        assert payload["status"] == "rejected"


class TestTrailingVol:
    def test_estimate_is_causal_and_positive(self) -> None:
        sessions = [
            _session(day, slope=0.2)
            for day in ("2025-01-06", "2025-01-07", "2025-01-08")
        ]
        # Too little history: nothing may be estimated.
        assert trailing_remaining_vol(sessions, window=5) == {}
        many = [_session(f"2025-02-{index:02d}", slope=0.2) for index in range(1, 28)]
        estimates = trailing_remaining_vol(many, window=5)
        assert estimates
        first_estimated = sorted(estimates)[0]
        profile = estimates[first_estimated]
        assert profile[135] > profile[915] > 0
