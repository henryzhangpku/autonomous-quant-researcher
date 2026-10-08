"""Pre-registered gates and the locked campaign evaluation.

All series, records and closes here are hand-written TEST FIXTURES with
invented numbers, built to make one rule bite at a time. They are not data
and nothing in this file is a result.
"""

from __future__ import annotations

import copy
import math
from datetime import date, timedelta

import pytest

from research.prereg import gpu_campaign
from research.prereg.gates import (
    GateState,
    StageRules,
    StageVerdict,
    WeekOutcome,
    block_bootstrap_lower_bound,
    evaluate_stage,
    sign_check,
)
from research.prereg.gpu_campaign import CampaignVerdict, TotalReturnClose
from research.prereg.gpu_leads_revisions import load_spec
from research.providers.licensed import (
    ConsensusEstimateRecord,
    EstimateMeasure,
    FiscalPeriod,
)

RULES = StageRules(min_weeks=10, min_state_share=0.2, block_weeks=2, traded=True)
UNTRADED = StageRules(min_weeks=10, min_state_share=0.2, block_weeks=2, traded=False)


def weeks(values: list[float], *, elevated_cut: float = 0.0,
          positions: list[int] | None = None) -> list[WeekOutcome]:
    start = date(2025, 1, 3)
    positions = positions or [1 if i % 2 else -1 for i in range(len(values))]
    return [WeekOutcome(start + timedelta(weeks=i), positions[i], value, value - elevated_cut)
            for i, value in enumerate(values)]


# ── gates ───────────────────────────────────────────────────────────────────

def test_a_steady_positive_series_passes_every_gate() -> None:
    report = evaluate_stage(weeks([0.01, 0.02, 0.015, 0.01, 0.012, 0.02, 0.011, 0.013, 0.018, 0.01]),
                            RULES, seed=7)
    assert report.verdict is StageVerdict.PASSED
    assert {gate.name for gate in report.gates} == {
        "mean_positive", "first_half_positive", "second_half_positive",
        "best_week_removed_positive", "bootstrap_lower_bound_positive", "double_cost_positive"}


def test_one_lucky_week_fails_the_best_week_removed_gate() -> None:
    report = evaluate_stage(weeks([-0.001] * 9 + [0.5]), RULES, seed=7)
    failed = {gate.name for gate in report.gates if gate.state is GateState.FAILED}
    assert report.verdict is StageVerdict.FAILED
    assert "best_week_removed_positive" in failed and "first_half_positive" in failed


def test_a_profit_that_only_survives_base_costs_fails_double_costs() -> None:
    report = evaluate_stage(weeks([0.01] * 10, elevated_cut=0.02), RULES, seed=7)
    assert [gate.name for gate in report.gates if gate.state is GateState.FAILED] == [
        "double_cost_positive"]


def test_untraded_outcomes_report_the_cost_gate_as_not_applicable() -> None:
    report = evaluate_stage(weeks([0.01] * 10), UNTRADED, seed=7)
    cost = next(gate for gate in report.gates if gate.name == "double_cost_positive")
    assert report.verdict is StageVerdict.PASSED
    assert cost.state is GateState.NOT_APPLICABLE and cost.value is None


def test_thin_or_one_sided_samples_are_untestable_not_refuted() -> None:
    assert evaluate_stage(weeks([0.01] * 9), RULES, seed=7).verdict is StageVerdict.UNTESTABLE
    one_sided = evaluate_stage(weeks([0.01] * 10, positions=[1] * 10), RULES, seed=7)
    assert one_sided.verdict is StageVerdict.UNTESTABLE and "signal was positive" in one_sided.reason


def test_block_bootstrap_is_deterministic_and_validates_inputs() -> None:
    values = [0.01, -0.02, 0.03, 0.0, 0.01, 0.02]
    first = block_bootstrap_lower_bound(values, block=2, seed=11)
    assert first == block_bootstrap_lower_bound(values, block=2, seed=11)
    assert first < sum(values) / len(values)
    with pytest.raises(ValueError):
        block_bootstrap_lower_bound([], block=2, seed=1)
    with pytest.raises(ValueError):
        block_bootstrap_lower_bound([float("nan")], block=2, seed=1)
    assert sign_check([]).state is GateState.FAILED


# ── locked campaign construction ────────────────────────────────────────────

def record(symbol: str, as_of: date, mean: float, *, fye: date = date(2027, 1, 31),
           period: FiscalPeriod = FiscalPeriod.FY1, contributors: int = 5) -> ConsensusEstimateRecord:
    return ConsensusEstimateRecord(symbol, as_of, EstimateMeasure.REVENUE, period, fye, mean,
                                   contributors, "USD", "fixture", f"{symbol}-{as_of}")


def test_basket_revision_pins_the_fiscal_year_and_requires_fresh_names() -> None:
    start, end = date(2026, 1, 9), date(2026, 3, 6)
    records = [
        record("AAA", start, 100.0), record("AAA", end, 110.0),
        # BBB's FY+1 label rolls inside the window; the pinned year is still followed.
        record("BBB", start, 50.0), record("BBB", end, 55.0),
        record("BBB", end, 80.0, fye=date(2028, 1, 31)),
        record("CCC", start, 20.0), record("CCC", end, 22.0),
        # DDD's last observation is stale at the window end: it does not count.
        record("DDD", start, 10.0), record("DDD", end - timedelta(days=10), 30.0),
        # EEE has too few contributors.
        record("EEE", start, 10.0, contributors=2), record("EEE", end, 30.0),
    ]
    value = gpu_campaign.basket_revision(records, ["AAA", "BBB", "CCC", "DDD", "EEE"], "revenue",
                                         start, end, min_names=3, min_contributors=3)
    assert value == pytest.approx(math.log(1.1))
    assert gpu_campaign.basket_revision(records, ["AAA", "DDD", "EEE"], "revenue", start, end,
                                        min_names=3, min_contributors=3) is None


def test_revision_capture_demeans_by_matured_outcomes_only() -> None:
    grid = [date(2026, 1, 2) + timedelta(weeks=i) for i in range(20)]
    outcomes = {i: 0.01 * i for i in range(20)}
    captures = gpu_campaign.revision_captures(grid, {i: 1 for i in range(20)}, outcomes, 2)
    # With a 2-week horizon, index 14 is the first with 13 matured outcomes (0..12).
    assert min(captures) == 14
    assert captures[14].base == pytest.approx(0.14 - sum(0.01 * i for i in range(13)) / 13)
    assert captures[14].base == captures[14].elevated


def test_return_capture_charges_turnover_on_both_legs() -> None:
    grid = [date(2026, 1, 2), date(2026, 1, 9), date(2026, 1, 16)]
    out = gpu_campaign.return_captures(grid, {0: 1, 1: -1}, {0: 0.01, 1: 0.02},
                                       base_bps=5, elevated_bps=10)
    assert out[0].base == pytest.approx(0.01 - 5 * 2 / 10_000)        # entry from flat
    assert out[1].base == pytest.approx(-0.02 - 5 * 4 / 10_000)       # full reversal
    assert out[1].elevated == pytest.approx(-0.02 - 10 * 4 / 10_000)


def test_excess_returns_need_the_benchmark_and_three_names() -> None:
    grid = [date(2026, 1, 2), date(2026, 1, 9)]
    closes = [TotalReturnClose(s, d, p) for s, d, p in (
        ("A", grid[0], 10), ("A", grid[1], 11), ("B", grid[0], 10), ("B", grid[1], 11),
        ("C", grid[0], 10), ("C", grid[1], 11), ("SPY", grid[0], 100), ("SPY", grid[1], 100))]
    assert gpu_campaign.excess_returns(closes, grid, ["A", "B", "C"], "SPY")[0] == pytest.approx(
        math.log(1.1))
    assert gpu_campaign.excess_returns(closes[:4] + closes[6:], grid, ["A", "B", "C"], "SPY") == {}


def _fixture_inputs(monkeypatch: pytest.MonkeyPatch, captures_by_stage: dict[str, float],
                    check_value: float) -> gpu_campaign.CampaignInputs:
    """A fixture spec with tiny floors, and stubbed construction steps."""
    spec = copy.deepcopy(load_spec())
    spec["gates"]["sample_floor"]["min_weeks"] = {"discovery": 4, "validation": 4, "holdout": 4}
    spec["stages"]["holdout"]["closes_after_evaluable_weeks"] = 4
    grid = tuple(gpu_campaign.weekly_grid(date(2024, 12, 1), date(2025, 1, 31))
                 + gpu_campaign.weekly_grid(date(2026, 7, 1), date(2026, 7, 31))
                 + gpu_campaign.weekly_grid(date(2026, 10, 9), date(2026, 11, 6)))
    stage_of = {}
    for i, week in enumerate(grid):
        stage_of[i] = ("discovery" if week <= date(2024, 12, 31) else
                       "validation" if week <= date(2026, 7, 31) else "holdout")

    def fake_captures(grid_, positions, outcomes, horizon):
        return {i: gpu_campaign.WeekOutcome(grid_[i], positions[i], outcomes[i], outcomes[i])
                for i in positions if i in outcomes}

    def fake_outcomes(records, grid_, basket, measure, horizon, **_):
        if measure == "capex":
            return {i: check_value * (0.01 + 0.001 * (i % 3)) for i in range(len(grid_))}
        return {i: captures_by_stage[stage_of[i]] + 0.001 * (i % 3) for i in range(len(grid_))}

    monkeypatch.setattr(gpu_campaign, "revision_outcomes", fake_outcomes)
    monkeypatch.setattr(gpu_campaign, "revision_captures", fake_captures)
    monkeypatch.setattr(gpu_campaign, "position_for",
                        lambda source, code, week: 1 if grid.index(week) % 2 else -1)
    return gpu_campaign.CampaignInputs(spec=spec, spec_sha256="ab" * 32, grid=grid,
                                       primary_vendor=lambda _w: [],
                                       index_tape={code: (lambda _w: []) for code in ("H100", "H200", "B200")},
                                       estimates=())


def test_campaign_1_runs_stages_in_order_and_stops_at_the_first_failure(
        monkeypatch: pytest.MonkeyPatch) -> None:
    inputs = _fixture_inputs(monkeypatch, {"discovery": 0.01, "validation": -0.01, "holdout": 0.01}, 1)
    result = gpu_campaign.evaluate_campaign_1(inputs)
    assert result.verdict is CampaignVerdict.FAIL and result.ended_at == "validation"
    assert "holdout" not in result.stages
    assert gpu_campaign.evaluate_campaign_2(inputs, result) is None  # sealed


def test_campaign_1_passes_only_with_every_gate_and_check(monkeypatch: pytest.MonkeyPatch) -> None:
    inputs = _fixture_inputs(monkeypatch, {"discovery": 0.01, "validation": 0.01, "holdout": 0.01}, 1)
    result = gpu_campaign.evaluate_campaign_1(inputs)
    assert result.verdict is CampaignVerdict.PASS
    assert {"holdout:H200_check", "holdout:B200_check", "validation:capex_confirmation"} <= set(result.checks)
    failing_checks = _fixture_inputs(monkeypatch, {"discovery": 0.01, "validation": 0.01,
                                                   "holdout": 0.01}, -1)
    failed = gpu_campaign.evaluate_campaign_1(failing_checks)
    assert failed.verdict is CampaignVerdict.FAIL and failed.ended_at == "validation"
