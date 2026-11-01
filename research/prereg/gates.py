"""Pre-registered gates over a weekly outcome series, and the decision rule.

Pure functions. They are bound into the pre-registration by content hash, so
the arithmetic that will judge the campaign is the arithmetic committed before
any of its data existed. Changing this file breaks the ledger check; a new
rule is a new campaign.

A stage is one chronological slice of weekly observations. Each observation is
a ``WeekOutcome``: the position the signal implied (+1 / -1) and the base- and
elevated-cost capture for that week. For an outcome that is not traded (an
estimate revision) the two capture values are equal and the cost gate is
reported as not applicable rather than passed.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Sequence

from research.v3.evaluator import BOOTSTRAP_RESAMPLES, _quantile

BOOTSTRAP_ALPHA = 0.05


class GateState(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class StageVerdict(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    UNTESTABLE = "untestable"   # too few observations: our ignorance, not the market's answer


@dataclass(frozen=True)
class WeekOutcome:
    week: date                  # the decision session (last session of the ISO week)
    position: int               # +1 or -1
    base: float                 # capture after base costs (or the untraded capture)
    elevated: float             # capture after elevated (2x) costs


@dataclass(frozen=True)
class StageRules:
    min_weeks: int
    min_state_share: float      # each position state must hold this share of weeks
    block_weeks: int            # bootstrap block length, >= the outcome overlap
    traded: bool                # False: the cost gate is not applicable


@dataclass(frozen=True)
class GateResult:
    name: str
    state: GateState
    value: float | None
    detail: str


@dataclass(frozen=True)
class StageReport:
    verdict: StageVerdict
    weeks: int
    gates: tuple[GateResult, ...] = field(default_factory=tuple)
    reason: str = ""


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def bootstrap_seed(*parts: str) -> int:
    material = "\x1f".join(parts)
    return int.from_bytes(hashlib.sha256(material.encode()).digest()[:8], "big")


def block_bootstrap_lower_bound(values: Sequence[float], *, block: int, seed: int,
                                alpha: float = BOOTSTRAP_ALPHA,
                                resamples: int = BOOTSTRAP_RESAMPLES) -> float:
    """Lower ``alpha`` quantile of the mean under a consecutive-block bootstrap.

    Blocks are consecutive weeks, so overlapping multi-week outcomes are
    resampled together instead of being counted as independent evidence.
    """
    if not values or block <= 0 or not 0 < alpha < 1 or resamples <= 0:
        raise ValueError("bootstrap requires values, a positive block, a valid alpha and resamples")
    if any(not math.isfinite(value) for value in values):
        raise ValueError("bootstrap values must be finite")
    blocks = [list(values[start:start + block]) for start in range(0, len(values), block)]
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(resamples):
        drawn = [value for _ in blocks for value in blocks[rng.randrange(len(blocks))]]
        means.append(_mean(drawn))
    return _quantile(means, alpha)


def evaluate_stage(outcomes: Sequence[WeekOutcome], rules: StageRules, *, seed: int) -> StageReport:
    """Every gate for one stage. All must pass; there is no score that admits."""
    ordered = sorted(outcomes, key=lambda item: item.week)
    if len({item.week for item in ordered}) != len(ordered):
        raise ValueError("one outcome per week")
    if any(item.position not in (-1, 1) for item in ordered):
        raise ValueError("positions must be +1 or -1")
    weeks = len(ordered)
    if weeks < rules.min_weeks:
        return StageReport(StageVerdict.UNTESTABLE, weeks,
                           reason=f"{weeks} evaluable weeks, the floor is {rules.min_weeks}")
    long_share = sum(item.position == 1 for item in ordered) / weeks
    if min(long_share, 1 - long_share) < rules.min_state_share:
        return StageReport(
            StageVerdict.UNTESTABLE, weeks,
            reason=(f"signal was positive in {long_share:.0%} of weeks; each state needs "
                    f"at least {rules.min_state_share:.0%} to tell timing from drift"),
        )
    base = [item.base for item in ordered]
    elevated = [item.elevated for item in ordered]
    half = weeks // 2
    first, second = _mean(base[:half]), _mean(base[half:])
    without_best = sorted(base)[:-1]
    lower = block_bootstrap_lower_bound(base, block=rules.block_weeks, seed=seed)
    mean = _mean(base)

    def gate(name: str, value: float, detail: str) -> GateResult:
        return GateResult(name, GateState.PASSED if value > 0 else GateState.FAILED, value, detail)

    gates = [
        gate("mean_positive", mean, "mean weekly capture after base costs"),
        gate("first_half_positive", first, "mean over the first half of the stage's weeks"),
        gate("second_half_positive", second, "mean over the second half of the stage's weeks"),
        gate("best_week_removed_positive", _mean(without_best),
             "mean with the single best week removed"),
        gate("bootstrap_lower_bound_positive", lower,
             f"5% lower bound, {rules.block_weeks}-week block bootstrap, "
             f"{BOOTSTRAP_RESAMPLES} resamples"),
    ]
    if rules.traded:
        gates.append(gate("double_cost_positive", _mean(elevated),
                          "mean weekly capture at twice the base trading cost"))
    else:
        gates.append(GateResult("double_cost_positive", GateState.NOT_APPLICABLE, None,
                                "the outcome is an estimate revision, which is not traded"))
    failed = [item.name for item in gates if item.state is GateState.FAILED]
    verdict = StageVerdict.FAILED if failed else StageVerdict.PASSED
    return StageReport(verdict, weeks, tuple(gates),
                       reason=("failed: " + ", ".join(failed)) if failed else "every gate passed")


def sign_check(captures: Sequence[float]) -> GateResult:
    """A confirmation check: the mean capture must be positive. Nothing else."""
    if not captures:
        return GateResult("sign_check", GateState.FAILED, None, "no observations")
    value = _mean(captures)
    return GateResult("sign_check", GateState.PASSED if value > 0 else GateState.FAILED, value,
                      "mean capture must be positive")
