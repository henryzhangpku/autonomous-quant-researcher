"""External portfolio evaluator boundary.

Daily portfolio signals (long/short equity, futures) are evaluated by an
external, separately versioned evaluator process; this repository never
imports its code. The boundary is:

* the campaign pins the evaluator -- command, name, version, the sha256 of its
  source (``code_hash``), the universe, costs and risk target -- in an
  ``ExternalEvaluatorSpec`` whose own hash goes into the contract;
* the evaluator runs with the same credential-stripped environment as every
  research child process and receives only a declarative recipe and one
  stage window;
* a result is accepted only if it echoes the pinned evaluator identity, the
  requested stage and the recipe's canonical hash -- anything else fails
  closed;
* the evaluator reports weekly outcomes; the gates here (``research.prereg.gates``)
  decide. A dollar-neutral book is always on, so outcomes carry position +1
  and the timing-share check is disabled (``min_state_share=0``); every other
  gate is unchanged.

The recipe vocabulary is the evaluator's (strict JSON, no executable fields);
an invalid recipe comes back as a typed refusal, which is a formulation
failure, not a scientific trial.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from research.autoresearch import _research_environment
from research.prereg.gates import StageReport, StageRules, WeekOutcome, bootstrap_seed, evaluate_stage

PORTFOLIO_STAGES: dict[str, tuple[str, str | None]] = {
    "discovery": ("2016-01-01", "2021-12-31"),
    "validation": ("2022-01-01", "2024-12-31"),
    "holdout": ("2025-01-01", None),  # end is fixed by the holdout receipt
}

# Weekly outcomes of a daily-rebalanced book overlap little; 4-week blocks
# still keep a month of autocorrelation together in the bootstrap.
PORTFOLIO_RULES: dict[str, StageRules] = {
    "discovery": StageRules(min_weeks=104, min_state_share=0.0, block_weeks=4, traded=True),
    "validation": StageRules(min_weeks=52, min_state_share=0.0, block_weeks=4, traded=True),
    "holdout": StageRules(min_weeks=26, min_state_share=0.0, block_weeks=4, traded=True),
}


class ExternalEvaluatorError(RuntimeError):
    """The evaluator's output cannot be trusted; the trial fails closed."""


class Refusal(Exception):
    """The evaluator declined with a typed reason (no economics were produced)."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class ExternalEvaluatorSpec:
    name: str
    version: str
    code_hash: str
    command: tuple[str, ...]      # argv prefix, e.g. ("python", "research/evaluate.py")
    cwd: str
    universe: str                 # the evaluator's universe config name
    base_bps: float
    elevated_bps: float
    risk_target: float = 0.05
    timeout_seconds: int = 900

    def contract_hash(self) -> str:
        material = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(material.encode()).hexdigest()


def canonical_recipe_hash(recipe: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def run_external(spec: ExternalEvaluatorSpec, recipe: Mapping[str, Any],
                 stage_start: str, stage_end: str) -> dict[str, Any]:
    """Run the pinned evaluator on one stage window and verify what it returns."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "result.json"
        argv = [*spec.command, "--recipe", "-", "--config", spec.universe,
                "--stage-start", stage_start, "--stage-end", stage_end,
                "--base-bps", repr(spec.base_bps), "--elevated-bps", repr(spec.elevated_bps),
                "--risk-target", repr(spec.risk_target), "--out", str(out)]
        proc = subprocess.run(argv, input=json.dumps(recipe), text=True, capture_output=True,
                              cwd=spec.cwd, env=_research_environment(),
                              timeout=spec.timeout_seconds)
        if proc.returncode not in (0, 2) or not out.is_file():
            raise ExternalEvaluatorError(
                f"evaluator exited {proc.returncode}: {proc.stderr.strip()[-500:]}")
        result = json.loads(out.read_text())

    identity = result.get("evaluator") or {}
    if (identity.get("name"), identity.get("version"), identity.get("code_hash")) != \
            (spec.name, spec.version, spec.code_hash):
        raise ExternalEvaluatorError(
            f"evaluator identity {identity} does not match the pinned "
            f"{spec.name} v{spec.version} {spec.code_hash[:12]}")
    if result.get("stage") != {"start": stage_start, "end": stage_end}:
        raise ExternalEvaluatorError(f"evaluator reported stage {result.get('stage')}")
    if "refusal" in result:
        refusal = result["refusal"]
        raise Refusal(str(refusal.get("reason")), str(refusal.get("detail")))
    if result.get("recipe_hash") != canonical_recipe_hash(recipe):
        raise ExternalEvaluatorError("evaluator evaluated a different recipe")
    return result


def week_outcomes(result: Mapping[str, Any], stage_start: str, stage_end: str) -> list[WeekOutcome]:
    """Weekly evidence -> gate inputs. Every value must be finite and in the stage."""
    start, end = date.fromisoformat(stage_start), date.fromisoformat(stage_end)
    outcomes = []
    for row in result.get("weeks", []):
        week = date.fromisoformat(row["week"])
        if not start <= week <= end:
            raise ExternalEvaluatorError(f"week {week} is outside the stage")
        base, elevated = row.get("base"), row.get("elevated")
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (base, elevated)):
            raise ExternalEvaluatorError(f"non-finite outcome for week {week}")
        if elevated > base + 1e-12:
            raise ExternalEvaluatorError(f"elevated-cost outcome exceeds base cost in week {week}")
        outcomes.append(WeekOutcome(week=week, position=1, base=float(base),
                                    elevated=float(elevated)))
    return outcomes


def evaluate_portfolio_stage(spec: ExternalEvaluatorSpec, recipe: Mapping[str, Any],
                             stage: str, *, stage_end: str | None = None,
                             campaign: str = "") -> tuple[StageReport, dict[str, Any]]:
    """Run, verify and gate one stage. Returns the gate report and the evidence."""
    if stage not in PORTFOLIO_STAGES:
        raise ValueError(f"unknown stage {stage}")
    start, default_end = PORTFOLIO_STAGES[stage]
    end = stage_end or default_end
    if end is None:
        raise ValueError("the holdout end date comes from its receipt")
    result = run_external(spec, recipe, start, end)
    outcomes = week_outcomes(result, start, end)
    seed = bootstrap_seed(campaign, stage, spec.contract_hash(), canonical_recipe_hash(recipe))
    report = evaluate_stage(outcomes, PORTFOLIO_RULES[stage], seed=seed)
    return report, result
