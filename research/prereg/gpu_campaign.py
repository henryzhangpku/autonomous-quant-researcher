"""The locked evaluation for ``research/missions/gpu-leads-revisions``.

Pure functions from (signal series, consensus records, closes) to a verdict,
following ``preregistration.json`` exactly. Bound into the pre-registration by
content hash, with ``gates.py``, ``gpu_signal.py`` and ``xnys.py``.

Nothing here reads a file or a network, and nothing here is called until the
mission's data requirements are met (``research.prereg.gpu_leads_revisions``
refuses first). No data is bundled for it to run on.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from enum import StrEnum

from research.prereg import xnys
from research.prereg.gates import (
    GateResult,
    GateState,
    StageReport,
    StageRules,
    StageVerdict,
    WeekOutcome,
    bootstrap_seed,
    evaluate_stage,
    sign_check,
)
from research.prereg.gpu_signal import SessionValue, SignalStatus, signal_at
from research.providers.licensed import ConsensusEstimateRecord

DECISION_READ_TIME = time(14, 30, tzinfo=timezone.utc)
STAGES = ("discovery", "validation", "holdout")
WARMUP_MATURED_OUTCOMES = 13

SeriesAsOf = Callable[[datetime], Sequence[SessionValue]]


class CampaignVerdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNTESTABLE = "UNTESTABLE"


@dataclass(frozen=True)
class CampaignResult:
    campaign: str
    verdict: CampaignVerdict
    ended_at: str
    stages: Mapping[str, StageReport]
    checks: Mapping[str, GateResult] = field(default_factory=dict)


@dataclass(frozen=True)
class TotalReturnClose:
    symbol: str
    day: date
    close: float                # total-return (dividend-adjusted) close


def weekly_grid(start: date, end: date) -> list[date]:
    """Decision sessions: the last XNYS session of each ISO week in [start, end]."""
    return xnys.last_session_of_each_week(xnys.sessions(start, end))


def decision_time(week: date) -> datetime:
    return datetime.combine(week, DECISION_READ_TIME)


def position_for(series_as_of: SeriesAsOf, code: str, week: date) -> int | None:
    """+1 / -1 from the signal computable at the session before ``week``."""
    previous = xnys.sessions(week - timedelta(days=10), week)[-2]
    reading = signal_at(series_as_of(decision_time(week)), code, previous)
    if reading.status is not SignalStatus.LIVE or reading.value is None or reading.value == 0:
        return None
    return 1 if reading.value > 0 else -1


CONSENSUS_FRESHNESS_DAYS = 7


def _consensus(records: Sequence[ConsensusEstimateRecord], on: date, *,
               fiscal_year_end: date | None) -> ConsensusEstimateRecord | None:
    """The consensus known on ``on``, dated within the freshness window, or None."""
    oldest = on - timedelta(days=CONSENSUS_FRESHNESS_DAYS - 1)
    known = [item for item in records if oldest <= item.as_of <= on and (
        item.fiscal_period.value == "FY+1" if fiscal_year_end is None
        else item.fiscal_year_end == fiscal_year_end)]
    return max(known, key=lambda item: item.as_of) if known else None


def basket_revision(records: Sequence[ConsensusEstimateRecord], basket: Sequence[str],
                    measure: str, start: date, end: date, *,
                    min_names: int, min_contributors: int) -> float | None:
    """Equal-weight mean log change in pinned-year consensus between two dates."""
    changes: list[float] = []
    for symbol in basket:
        own = [item for item in records if item.symbol == symbol and item.measure.value == measure]
        first = _consensus(own, start, fiscal_year_end=None)
        if first is None or first.contributor_count < min_contributors or first.consensus_mean <= 0:
            continue
        last = _consensus(own, end, fiscal_year_end=first.fiscal_year_end)
        if (last is None or last.contributor_count < min_contributors
                or last.consensus_mean <= 0):
            continue
        changes.append(math.log(last.consensus_mean / first.consensus_mean))
    if len(changes) < min_names:
        return None
    return sum(changes) / len(changes)


def revision_outcomes(records: Sequence[ConsensusEstimateRecord], grid: Sequence[date],
                      basket: Sequence[str], measure: str, horizon_weeks: int, *,
                      min_names: int, min_contributors: int) -> dict[int, float]:
    """Y by grid index: the basket revision from week i to week i + horizon."""
    out: dict[int, float] = {}
    for index in range(len(grid) - horizon_weeks):
        value = basket_revision(records, basket, measure, grid[index], grid[index + horizon_weeks],
                                min_names=min_names, min_contributors=min_contributors)
        if value is not None:
            out[index] = value
    return out


def revision_captures(grid: Sequence[date], positions: Mapping[int, int],
                      outcomes: Mapping[int, float], horizon_weeks: int) -> dict[int, WeekOutcome]:
    """position * (Y - running mean of matured Y). Untraded: base == elevated."""
    captures: dict[int, WeekOutcome] = {}
    for index, position in positions.items():
        if index not in outcomes:
            continue
        matured = [value for prior, value in outcomes.items() if prior + horizon_weeks <= index]
        if len(matured) < WARMUP_MATURED_OUTCOMES:
            continue
        value = position * (outcomes[index] - sum(matured) / len(matured))
        captures[index] = WeekOutcome(grid[index], position, value, value)
    return captures


def excess_returns(closes: Sequence[TotalReturnClose], grid: Sequence[date],
                   basket: Sequence[str], benchmark: str, *, min_names: int = 3) -> dict[int, float]:
    """Y by grid index: basket minus benchmark log return over the next week."""
    price = {(item.symbol, item.day): item.close for item in closes}
    out: dict[int, float] = {}
    for index in range(len(grid) - 1):
        start, end = grid[index], grid[index + 1]
        legs = [math.log(price[(s, end)] / price[(s, start)]) for s in basket
                if (s, start) in price and (s, end) in price]
        if len(legs) < min_names or (benchmark, start) not in price or (benchmark, end) not in price:
            continue
        out[index] = sum(legs) / len(legs) - math.log(price[(benchmark, end)] / price[(benchmark, start)])
    return out


def return_captures(grid: Sequence[date], positions: Mapping[int, int],
                    outcomes: Mapping[int, float], *, base_bps: float,
                    elevated_bps: float) -> dict[int, WeekOutcome]:
    captures: dict[int, WeekOutcome] = {}
    previous = 0
    for index in range(len(grid)):
        if index not in positions or index not in outcomes:
            previous = 0  # no signal or no outcome: flat that week
            continue
        position = positions[index]
        turnover = abs(position - previous) * 2  # basket leg and benchmark leg
        gross = position * outcomes[index]
        captures[index] = WeekOutcome(grid[index], position,
                                      gross - base_bps * turnover / 10_000,
                                      gross - elevated_bps * turnover / 10_000)
        previous = position
    return captures


def stage_indices(grid: Sequence[date], spec: Mapping[str, object],
                  evaluable: Mapping[int, object]) -> dict[str, list[int]]:
    stages = spec["stages"]  # type: ignore[index]
    out: dict[str, list[int]] = {}
    for name in ("discovery", "validation"):
        bounds = stages[name]  # type: ignore[index]
        lo, hi = date.fromisoformat(bounds["start"]), date.fromisoformat(bounds["end"])
        out[name] = [i for i in sorted(evaluable) if lo <= grid[i] <= hi]
    holdout = stages["holdout"]  # type: ignore[index]
    start = date.fromisoformat(holdout["start"])
    out["holdout"] = [i for i in sorted(evaluable) if grid[i] >= start][
        : holdout["closes_after_evaluable_weeks"]]
    return out


def _rules(spec: Mapping[str, object], stage: str, campaign: str) -> StageRules:
    gates = spec["gates"]["sample_floor"]  # type: ignore[index]
    block = spec[campaign]["bootstrap_block_weeks"]  # type: ignore[index]
    traded = spec[campaign]["traded"]  # type: ignore[index]
    return StageRules(gates["min_weeks"][stage], gates["min_state_share"], block, traded)


def _run_stages(campaign: str, spec: Mapping[str, object], spec_hash: str,
                captures: Mapping[int, WeekOutcome], indices: Mapping[str, list[int]],
                extra: Callable[[str, list[int]], dict[str, GateResult]]) -> CampaignResult:
    reports: dict[str, StageReport] = {}
    checks: dict[str, GateResult] = {}
    for stage in STAGES:
        chosen = indices[stage]
        report = evaluate_stage([captures[i] for i in chosen], _rules(spec, stage, campaign),
                                seed=bootstrap_seed(spec_hash, campaign, stage))
        reports[stage] = report
        if report.verdict is StageVerdict.UNTESTABLE:
            return CampaignResult(campaign, CampaignVerdict.UNTESTABLE, stage, reports, checks)
        stage_checks = extra(stage, chosen)
        checks.update({f"{stage}:{name}": result for name, result in stage_checks.items()})
        if report.verdict is StageVerdict.FAILED or any(
                result.state is GateState.FAILED for result in stage_checks.values()):
            return CampaignResult(campaign, CampaignVerdict.FAIL, stage, reports, checks)
    return CampaignResult(campaign, CampaignVerdict.PASS, "holdout", reports, checks)


@dataclass(frozen=True)
class CampaignInputs:
    spec: Mapping[str, object]
    spec_sha256: str
    grid: tuple[date, ...]
    primary_vendor: SeriesAsOf            # licensed H100 history, discovery and validation
    index_tape: Mapping[str, SeriesAsOf]  # "H100" / "H200" / "B200" -> point-in-time tape
    estimates: tuple[ConsensusEstimateRecord, ...]
    closes: tuple[TotalReturnClose, ...] = ()


def _positions(inputs: CampaignInputs, code: str, *, holdout_only: bool) -> dict[int, int]:
    holdout_start = date.fromisoformat(inputs.spec["stages"]["holdout"]["start"])  # type: ignore[index]
    out: dict[int, int] = {}
    for index, week in enumerate(inputs.grid):
        in_holdout = week >= holdout_start
        if holdout_only and not in_holdout:
            continue
        source = inputs.index_tape[code] if in_holdout else inputs.primary_vendor
        position = position_for(source, code, week)
        if position is not None:
            out[index] = position
    return out


def evaluate_campaign_1(inputs: CampaignInputs) -> CampaignResult:
    spec = inputs.spec
    c1 = spec["campaign_1"]  # type: ignore[index]
    baskets = spec["baskets"]  # type: ignore[index]
    primary, confirm = c1["primary_outcome"], c1["confirmation_outcome"]
    horizon = primary["horizon_weeks"]
    grid = list(inputs.grid)
    y = revision_outcomes(inputs.estimates, grid, baskets[primary["basket"]], primary["measure"],
                          horizon, min_names=primary["min_names"],
                          min_contributors=primary["min_contributors"])
    y_capex = revision_outcomes(inputs.estimates, grid, baskets[confirm["basket"]],
                                confirm["measure"], confirm["horizon_weeks"],
                                min_names=confirm["min_names"],
                                min_contributors=confirm["min_contributors"])
    positions = _positions(inputs, "H100", holdout_only=False)
    captures = revision_captures(grid, positions, y, horizon)
    capex = revision_captures(grid, positions, y_capex, confirm["horizon_weeks"])
    check_captures = {code: revision_captures(grid, _positions(inputs, code, holdout_only=True), y, horizon)
                      for code in ("H200", "B200")}
    indices = stage_indices(grid, spec, captures)

    def extra(stage: str, chosen: list[int]) -> dict[str, GateResult]:
        out: dict[str, GateResult] = {}
        if stage in ("validation", "holdout"):
            out["capex_confirmation"] = sign_check([capex[i].base for i in chosen if i in capex])
        if stage == "holdout":
            for code, series in check_captures.items():
                out[f"{code}_check"] = sign_check([series[i].base for i in chosen if i in series])
        return out

    return _run_stages("campaign_1", spec, inputs.spec_sha256, captures, indices, extra)


def evaluate_campaign_2(inputs: CampaignInputs, campaign_1: CampaignResult) -> CampaignResult | None:
    """None while campaign 2 is sealed: it is never evaluated without a campaign 1 PASS."""
    if campaign_1.verdict is not CampaignVerdict.PASS:
        return None
    spec = inputs.spec
    c2 = spec["campaign_2"]  # type: ignore[index]
    baskets = spec["baskets"]  # type: ignore[index]
    grid = list(inputs.grid)
    y = excess_returns(inputs.closes, grid, baskets["suppliers_and_neoclouds"],
                       baskets["returns_benchmark"])
    costs = {"base_bps": c2["base_cost_bps_per_side"], "elevated_bps": c2["elevated_cost_bps_per_side"]}
    captures = return_captures(grid, _positions(inputs, "H100", holdout_only=False), y, **costs)
    check_captures = {code: return_captures(grid, _positions(inputs, code, holdout_only=True), y, **costs)
                      for code in ("H200", "B200")}
    indices = stage_indices(grid, spec, captures)

    def extra(stage: str, chosen: list[int]) -> dict[str, GateResult]:
        if not chosen:
            return {"beats_always_long_baseline": sign_check([])}
        timing = sum(captures[i].base for i in chosen) / len(chosen)
        baseline = sum(y[i] for i in chosen) / len(chosen)
        margin = timing - baseline
        out = {"beats_always_long_baseline": GateResult(
            "beats_always_long_baseline", GateState.PASSED if margin > 0 else GateState.FAILED,
            margin, "mean capture minus the mean of always holding the basket against SPY")}
        if stage == "holdout":
            for code, series in check_captures.items():
                out[f"{code}_check"] = sign_check([series[i].base for i in chosen if i in series])
        return out

    return _run_stages("campaign_2", spec, inputs.spec_sha256, captures, indices, extra)
