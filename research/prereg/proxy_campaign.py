"""The locked evaluation for the gpu-leads-revisions PROXY track.

The primary track needs licensed data. The proxy track asks a weaker version
of the same question from free inputs:

- signal: the 3-month change in a monthly H100 rental LIST-PRICE median
  reconstructed from archived public rate cards (discovery and validation),
  and the monthly mean of the public index's published fixings (holdout);
- campaign 1-proxy outcome: the acceleration in year-on-year growth of the
  next reported quarter, from SEC XBRL facts stamped at their filing date;
- campaign 2-proxy outcome: forward 1-month basket returns against SMH.

Pure functions, bound into the ledger by content hash with ``gates.py``,
``gpu_signal.py`` and ``xnys.py``. Nothing here reads a file or a network and
nothing runs until the proxy requirements are met. No data is bundled.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

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
from research.prereg.gpu_campaign import CampaignResult, CampaignVerdict
from research.prereg.gpu_signal import SessionValue

DECISION_READ_TIME = time(14, 30, tzinfo=timezone.utc)
STAGES = ("discovery", "validation", "holdout")
MIN_RATE_CARDS = 3
MIN_MONTH_SESSION_COVERAGE = 0.9
WARMUP_MATURED_OUTCOMES = 3
QUARTER_DAYS = (80, 100)
YEAR_MATCH_DAYS = 10

SeriesAsOf = Callable[[datetime], Sequence[SessionValue]]


@dataclass(frozen=True)
class MonthlyListPrice:
    gpu: str
    month: date                 # first day of the month
    median_usd_per_gpu_hour: float
    rate_card_count: int
    price_type: str             # must be "list_price"


@dataclass(frozen=True)
class FiledFact:
    symbol: str
    concept: str                # "revenue" or "capex"
    period_start: date
    period_end: date
    value: float
    filed: date                 # the SEC filing date: the first day it was knowable
    accession: str
    xbrl_tag: str               # the us-gaap element the value was read from, for audit


@dataclass(frozen=True)
class AdjustedClose:
    symbol: str
    day: date
    close: float


# ── decision grid and signal ────────────────────────────────────────────────

def month_start(day: date) -> date:
    return day.replace(day=1)


def add_months(month: date, n: int) -> date:
    index = month.year * 12 + month.month - 1 + n
    return date(index // 12, index % 12 + 1, 1)


def monthly_decisions(start: date, end: date) -> list[date]:
    """The first XNYS session of each month in [start, end]."""
    firsts: dict[date, date] = {}
    for day in xnys.sessions(start, end):
        firsts.setdefault(month_start(day), day)
    return sorted(firsts.values())


def backfill_value(series: Sequence[MonthlyListPrice], gpu: str, month: date) -> float | None:
    for row in series:
        if (row.gpu == gpu and row.month == month and row.price_type == "list_price"
                and row.rate_card_count >= MIN_RATE_CARDS and row.median_usd_per_gpu_hour > 0):
            return row.median_usd_per_gpu_hour
    return None


def tape_month_value(series: Sequence[SessionValue], month: date) -> tuple[float, str] | None:
    """Mean of a month's published session fixings, if 90% of its sessions have one."""
    end = add_months(month, 1) - timedelta(days=1)
    calendar = xnys.sessions(month, end)
    rows = [item for item in series if month <= item.day <= end]
    versions = {item.methodology_version for item in rows}
    if not calendar or len(rows) < MIN_MONTH_SESSION_COVERAGE * len(calendar) or len(versions) != 1:
        return None
    return sum(item.value for item in rows) / len(rows), versions.pop()


def position_from_backfill(series: Sequence[MonthlyListPrice], gpu: str, decision: date) -> int | None:
    month = add_months(month_start(decision), -1)
    now, then = backfill_value(series, gpu, month), backfill_value(series, gpu, add_months(month, -3))
    return _sign(now, then)


def position_from_tape(series_as_of: SeriesAsOf, decision: date) -> int | None:
    series = series_as_of(datetime.combine(decision, DECISION_READ_TIME))
    month = add_months(month_start(decision), -1)
    now, then = tape_month_value(series, month), tape_month_value(series, add_months(month, -3))
    if now is None or then is None or now[1] != then[1]:
        return None
    window = [item for item in series if add_months(month, -3) <= item.day < add_months(month, 1)]
    if len({item.methodology_version for item in window}) != 1:
        return None
    return _sign(now[0], then[0])


def _sign(now: float | None, then: float | None) -> int | None:
    if now is None or then is None or now == then:
        return None
    return 1 if math.log(now / then) > 0 else -1


# ── campaign 1-proxy: next reported quarter's growth acceleration ──────────

def _discrete_quarters(facts: Sequence[FiledFact], known_on: date) -> dict[date, float]:
    """Discrete quarterly values by period end, from facts filed on or before ``known_on``.

    A directly reported quarter (80-100 days) is used as is; otherwise the
    quarter is the fiscal-YTD fact minus the prior YTD fact with the same start.
    For each period the latest filing on or before ``known_on`` wins.
    """
    latest: dict[tuple[date, date], FiledFact] = {}
    for fact in facts:
        if fact.filed > known_on:
            continue
        key = (fact.period_start, fact.period_end)
        if key not in latest or fact.filed >= latest[key].filed:
            latest[key] = fact
    out: dict[date, float] = {}
    for (start, end), fact in latest.items():
        days = (end - start).days
        if QUARTER_DAYS[0] <= days <= QUARTER_DAYS[1]:
            out[end] = fact.value
    for (start, end), fact in latest.items():
        if end in out or (end - start).days <= QUARTER_DAYS[1]:
            continue
        prior = [e for (s, e) in latest if s == start and QUARTER_DAYS[0] <= (end - e).days <= QUARTER_DAYS[1]]
        if prior:
            out[end] = fact.value - latest[(start, max(prior))].value
    return out


def _year_ago(values: Mapping[date, float], end: date) -> float | None:
    target = end - timedelta(days=365)
    near = [e for e in values if abs((e - target).days) <= YEAR_MATCH_DAYS]
    return values[min(near, key=lambda e: abs((e - target).days))] if near else None


def _yoy(values: Mapping[date, float], end: date) -> float | None:
    base = _year_ago(values, end)
    if base is None or base <= 0 or values[end] <= 0:
        return None
    return math.log(values[end] / base)


def first_known(facts: Sequence[FiledFact], end: date) -> date | None:
    """The first filing date on which the quarter ending ``end`` is computable."""
    for filed in sorted({fact.filed for fact in facts}):
        if end in _discrete_quarters(facts, filed):
            return filed
    return None


@dataclass(frozen=True)
class Acceleration:
    value: float
    known_on: date              # filing date of the next quarter: when the outcome matured


def acceleration(facts: Sequence[FiledFact], decision: date) -> Acceleration | None:
    """YoY growth of the next quarter first filed after ``decision`` minus that of the last filed before."""
    before = _discrete_quarters(facts, decision - timedelta(days=1))
    known = sorted(end for end in before if _yoy(before, end) is not None)
    if not known:
        return None
    last = known[-1]
    later = sorted({fact.period_end for fact in facts if fact.period_end > last})
    previous = last
    for end in later:
        if not QUARTER_DAYS[0] <= (end - previous).days <= QUARTER_DAYS[1]:
            return None         # a quarter is missing: no outcome, never skipping ahead
        filed = first_known(facts, end)
        if filed is None:
            return None         # the next quarter is not computable: no outcome
        if filed < decision:
            previous = end      # known before the decision but without a year-ago base
            continue
        after = _discrete_quarters(facts, filed)
        base = _year_ago(before, end)  # the year-ago quarter must be known at the decision
        if base is None or base <= 0 or after.get(end, 0) <= 0:
            return None
        return Acceleration(math.log(after[end] / base) - _yoy(before, last), filed)  # type: ignore[operator]
    return None


def basket_acceleration(facts: Sequence[FiledFact], basket: Sequence[str], concept: str,
                        decision: date, *, min_names: int) -> Acceleration | None:
    values: list[Acceleration] = []
    for symbol in basket:
        own = [fact for fact in facts if fact.symbol == symbol and fact.concept == concept]
        found = acceleration(own, decision)
        if found is not None:
            values.append(found)
    if len(values) < min_names:
        return None
    return Acceleration(sum(item.value for item in values) / len(values),
                        max(item.known_on for item in values))


def acceleration_captures(grid: Sequence[date], positions: Mapping[int, int],
                          outcomes: Mapping[int, Acceleration]) -> dict[int, WeekOutcome]:
    """position * (A - mean of outcomes already matured at the decision)."""
    captures: dict[int, WeekOutcome] = {}
    for index, position in positions.items():
        if index not in outcomes:
            continue
        matured = [item.value for item in outcomes.values() if item.known_on <= grid[index]]
        if len(matured) < WARMUP_MATURED_OUTCOMES:
            continue
        value = position * (outcomes[index].value - sum(matured) / len(matured))
        captures[index] = WeekOutcome(grid[index], position, value, value)
    return captures


# ── campaign 2-proxy: forward returns ──────────────────────────────────────

def forward_excess(closes: Sequence[AdjustedClose], grid: Sequence[date], basket: Sequence[str],
                   benchmark: str, months: int, *, min_names: int) -> dict[int, tuple[float, date]]:
    """(basket minus benchmark log return, window end) from each decision to ``months`` later."""
    price = {(item.symbol, item.day): item.close for item in closes}
    out: dict[int, tuple[float, date]] = {}
    for index in range(len(grid) - months):
        start, end = grid[index], grid[index + months]
        legs = [math.log(price[(s, end)] / price[(s, start)]) for s in basket
                if (s, start) in price and (s, end) in price]
        if len(legs) < min_names or (benchmark, start) not in price or (benchmark, end) not in price:
            continue
        out[index] = (sum(legs) / len(legs) - math.log(price[(benchmark, end)] / price[(benchmark, start)]),
                      end)
    return out


def return_captures(grid: Sequence[date], positions: Mapping[int, int],
                    outcomes: Mapping[int, tuple[float, date]], *, base_bps: float,
                    elevated_bps: float) -> dict[int, WeekOutcome]:
    captures: dict[int, WeekOutcome] = {}
    previous = 0
    for index in range(len(grid)):
        if index not in positions or index not in outcomes:
            previous = 0
            continue
        position = positions[index]
        turnover = abs(position - previous) * 2
        gross = position * outcomes[index][0]
        captures[index] = WeekOutcome(grid[index], position, gross - base_bps * turnover / 10_000,
                                      gross - elevated_bps * turnover / 10_000)
        previous = position
    return captures


# ── stages and verdicts ────────────────────────────────────────────────────

@dataclass(frozen=True)
class ProxyInputs:
    spec: Mapping[str, object]
    spec_sha256: str
    grid: tuple[date, ...]
    backfill: tuple[MonthlyListPrice, ...]
    index_tape: Mapping[str, SeriesAsOf]
    facts: tuple[FiledFact, ...] = ()
    closes: tuple[AdjustedClose, ...] = ()


def _positions(inputs: ProxyInputs, gpu: str, *, holdout_only: bool) -> dict[int, int]:
    holdout_start = date.fromisoformat(inputs.spec["stages"]["holdout"]["start"])  # type: ignore[index]
    out: dict[int, int] = {}
    for index, decision in enumerate(inputs.grid):
        in_holdout = decision >= holdout_start
        if holdout_only and not in_holdout:
            continue
        position = (position_from_tape(inputs.index_tape[gpu], decision) if in_holdout
                    else position_from_backfill(inputs.backfill, gpu, decision))
        if position is not None:
            out[index] = position
    return out


def stage_indices(grid: Sequence[date], spec: Mapping[str, object],
                  evaluable: Mapping[int, object], window_end: Mapping[int, date]) -> dict[str, list[int]]:
    stages = spec["stages"]  # type: ignore[index]
    cutoff = date.fromisoformat(stages["embargo"]["end"])  # type: ignore[index]
    out: dict[str, list[int]] = {}
    for name in ("discovery", "validation"):
        lo = date.fromisoformat(stages[name]["start"])  # type: ignore[index]
        hi = date.fromisoformat(stages[name]["end"])  # type: ignore[index]
        out[name] = [i for i in sorted(evaluable) if lo <= grid[i] <= hi and window_end[i] <= cutoff]
    holdout = stages["holdout"]  # type: ignore[index]
    start = date.fromisoformat(holdout["start"])
    out["holdout"] = [i for i in sorted(evaluable) if grid[i] >= start][
        : holdout["closes_after_evaluable_months"]]
    return out


def _rules(spec: Mapping[str, object], stage: str, campaign: str) -> StageRules:
    floor = spec["gates"]["sample_floor"]  # type: ignore[index]
    block = spec[campaign]["bootstrap_block_months"]  # type: ignore[index]
    traded = spec[campaign]["traded"]  # type: ignore[index]
    return StageRules(floor["min_months"][stage], floor["min_state_share"], block, traded)


def _run(campaign: str, spec: Mapping[str, object], spec_hash: str,
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


def evaluate_proxy_1(inputs: ProxyInputs) -> CampaignResult:
    spec = inputs.spec
    c1 = spec["proxy_campaign_1"]  # type: ignore[index]
    grid = list(inputs.grid)
    outcomes: dict[str, dict[int, Acceleration]] = {}
    for name, outcome in c1["outcomes"].items():
        outcomes[name] = {}
        for index, decision in enumerate(grid):
            found = basket_acceleration(inputs.facts, outcome["basket"], outcome["concept"], decision,
                                        min_names=outcome["min_names"])
            if found is not None:
                outcomes[name][index] = found
    positions = _positions(inputs, "H100", holdout_only=False)
    capex = acceleration_captures(grid, positions, outcomes["hyperscaler_capex"])
    revenue = acceleration_captures(grid, positions, outcomes["supplier_revenue"])
    # Both outcomes are required; the stage series is their mean on months where both exist.
    joint = {i: WeekOutcome(grid[i], positions[i], (capex[i].base + revenue[i].base) / 2,
                            (capex[i].base + revenue[i].base) / 2)
             for i in capex if i in revenue}
    window_end = {i: max(outcomes["hyperscaler_capex"][i].known_on,
                         outcomes["supplier_revenue"][i].known_on) for i in joint}
    checks_by_gpu = {gpu: _positions(inputs, gpu, holdout_only=True) for gpu in ("H200", "B200")}
    indices = stage_indices(grid, spec, joint, window_end)

    def extra(stage: str, chosen: list[int]) -> dict[str, GateResult]:
        out = {"capex_alone": sign_check([capex[i].base for i in chosen]),
               "revenue_alone": sign_check([revenue[i].base for i in chosen])}
        if stage == "holdout":
            for gpu, pos in checks_by_gpu.items():
                out[f"{gpu}_check"] = sign_check([joint[i].base * pos[i] * joint[i].position
                                                  for i in chosen if i in pos])
        return out

    return _run("proxy_campaign_1", spec, inputs.spec_sha256, joint, indices, extra)


def evaluate_proxy_2(inputs: ProxyInputs, proxy_1: CampaignResult) -> CampaignResult | None:
    """None while sealed: never evaluated without a proxy campaign 1 PASS."""
    if proxy_1.verdict is not CampaignVerdict.PASS:
        return None
    spec = inputs.spec
    c2 = spec["proxy_campaign_2"]  # type: ignore[index]
    grid = list(inputs.grid)
    basket, min_names = c2["basket"], c2["min_names"]
    primary = forward_excess(inputs.closes, grid, basket, c2["primary"]["benchmark"],
                             c2["primary"]["months"], min_names=min_names)
    confirmations = {f"{item['months']}m_vs_{item['benchmark']}": forward_excess(
        inputs.closes, grid, basket, item["benchmark"], item["months"], min_names=min_names)
        for item in c2["confirmations"]}
    positions = _positions(inputs, "H100", holdout_only=False)
    costs = {"base_bps": c2["base_cost_bps_per_side"], "elevated_bps": c2["elevated_cost_bps_per_side"]}
    captures = return_captures(grid, positions, primary, **costs)
    checks_by_gpu = {gpu: _positions(inputs, gpu, holdout_only=True) for gpu in ("H200", "B200")}
    indices = stage_indices(grid, spec, captures, {i: primary[i][1] for i in captures})

    def extra(stage: str, chosen: list[int]) -> dict[str, GateResult]:
        out: dict[str, GateResult] = {}
        if chosen:
            margin = (sum(captures[i].base for i in chosen) - sum(primary[i][0] for i in chosen)) / len(chosen)
            out["beats_always_long_baseline"] = GateResult(
                "beats_always_long_baseline", GateState.PASSED if margin > 0 else GateState.FAILED,
                margin, "mean capture minus the mean of always holding the basket against the benchmark")
        else:
            out["beats_always_long_baseline"] = sign_check([])
        for name, series in confirmations.items():
            out[name] = sign_check([positions[i] * series[i][0] for i in chosen if i in series])
        if stage == "holdout":
            for gpu, pos in checks_by_gpu.items():
                out[f"{gpu}_check"] = sign_check([pos[i] * primary[i][0] for i in chosen if i in pos])
        return out

    return _run("proxy_campaign_2", spec, inputs.spec_sha256, captures, indices, extra)
