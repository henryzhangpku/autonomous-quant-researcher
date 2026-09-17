from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from research.backtest.data import Session
from research.v3.campaign import CampaignContract, CampaignCoordinator, FrozenCandidate
from research.v3.evaluator import (
    Evaluation,
    LedgerCounts,
    PriceSourceKind,
    ScenarioMetrics,
    Stage,
)
from research.v3.hypothesis import canonical_hypothesis, describe_hypothesis, hypothesis_hash
from research.v3.proposer import (
    COMPOUND_LANES,
    COMPOUND_LANE_FAMILIES,
    DeclarativeProposer,
    PROPOSAL_JSON_SCHEMA,
    ProposalError,
    build_prompt,
    parse_proposal_json,
    proposal_schema_for_diversity,
)
from research.v3.runtime import (
    CampaignRuntime,
    MODELED_PRICE_SOURCE_ID,
    ModeledResearchEvaluator,
    build_modeled_ledgers,
    ensure_stage_stores,
)
from research.v3.stage_store import StageAccess, materialize_stage_stores


HYPOTHESIS = {
    "schema_version": 3,
    "entry_minute": 600,
    "signal": {
        "op": "lt",
        "observation": {"obs": "return_from_open", "minute": 600},
        "value": -0.1,
    },
    "structure": {"helper": "call_debit_spread", "width": 2.0, "otm_offset": 0.0},
}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _sessions(year: int, count: int = 30) -> list[Session]:
    sessions = []
    for index in range(count):
        month = 1 + index // 20
        day = 1 + index % 20
        price = 100.0 + index * 0.1
        sessions.append(Session(
            f"{year}-{month:02d}-{day:02d}",
            ((570, price, price, price, price),
             (600, price - 1, price - .8, price - 1.1, price - 1),
             (945, price + 3, price + 3, price + 3, price + 3)),
        ))
    return sessions


def _weekday_sessions(year: int, count: int) -> list[Session]:
    sessions: list[Session] = []
    current = date(year, 1, 4)
    while len(sessions) < count:
        if current.weekday() < 5:
            index = len(sessions)
            price = 100.0 + index * 0.1
            sessions.append(Session(
                current.isoformat(),
                ((570, price, price, price, price),
                 (600, price - 1, price - .8, price - 1.1, price - 1),
                 (945, price + 3, price + 3, price + 3, price + 3)),
            ))
        current += timedelta(days=1)
    return sessions


def _candidate(manifests: dict[str, str]) -> FrozenCandidate:
    canonical = canonical_hypothesis(HYPOTHESIS)
    return FrozenCandidate(
        canonical_document=canonical,
        semantic_hash=hypothesis_hash(HYPOTHESIS),
        source_hash=_hash("source"), policy_hash=_hash("policy"),
        evaluator_version="test", stage_manifest_hashes=manifests,
        descriptor=describe_hypothesis(HYPOTHESIS).__dict__,
    )


def _passing(stage: Stage, score: float = 1.0) -> Evaluation:
    scenario = ScenarioMetrics(1.0, 0.5, 0.03)
    counts = LedgerCounts(100, 100, 1.0, {})
    return Evaluation(
        True, (), stage, 100, 50, .05, 1, "modeled:fixture", PriceSourceKind.MODELED,
        "test", _hash(stage.value),
        counts, counts, 1.0, 0.0, scenario,
        replace(scenario, cost_fraction=.045), {}, score,
    )


def test_declarative_proposer_rejects_code_and_includes_bounded_feedback() -> None:
    content = json.dumps({"hypothesis": HYPOTHESIS, "rationale": "causal dip hypothesis"})
    proposal = parse_proposal_json(content)
    assert proposal.document == HYPOTHESIS
    with pytest.raises(ProposalError, match="exactly"):
        parse_proposal_json(json.dumps({"hypothesis": HYPOTHESIS, "rationale": "x", "code": "x"}))
    prompt = build_prompt(mission="Find robust SPY edge", previous_attempts=[{"reason": "duplicate"}],
                          diversity_feedback=["near duplicate"])
    assert "never Python" in prompt
    assert "call_debit_spread" in prompt
    assert "duplicate" in prompt
    assert "570..955" in prompt
    assert "strictly before entry_minute" in prompt
    assert "570 = 09:30 ET" in prompt
    assert "Never describe 840 as 8:40 AM" in prompt
    assert "choose exactly one listed family" in prompt
    assert "lt:return_between" in prompt
    compact = build_prompt(
        mission="Find robust SPY edge",
        previous_attempts=[{
            "attempt_id": "formulation-0001", "status": "admission",
            "reason": "semantic duplicate", "document": HYPOTHESIS,
            "rationale": "copy this rejected idea",
        }],
        diversity_feedback=["Required next hypothesis family: observed:first_touch_before_entry"],
    )
    assert "semantic duplicate" in compact
    assert "copy this rejected idea" not in compact
    assert '"document"' not in compact
    assert compact.index("Diversity target (highest priority)") < compact.index(
        "Past formulation/admission reasons"
    )


def test_openai_response_schema_constrains_the_nested_v3_contract() -> None:
    schema = PROPOSAL_JSON_SCHEMA
    hypothesis = schema["properties"]["hypothesis"]
    expression_variants = schema["$defs"]["expression"]["anyOf"]

    assert schema["additionalProperties"] is False
    assert hypothesis == {"$ref": "#/$defs/hypothesis"}
    assert schema["$defs"]["hypothesis"]["required"] == [
        "schema_version", "entry_minute", "signal", "structure",
    ]
    assert schema["$defs"]["hypothesis"]["additionalProperties"] is False
    assert schema["$defs"]["structure"]["properties"]["helper"]["enum"] == [
        "call_debit_spread", "put_credit_spread",
    ]
    assert len(expression_variants) == 4
    assert all(variant["additionalProperties"] is False for variant in expression_variants)
    assert expression_variants[0]["properties"]["args"]["items"] == {
        "$ref": "#/$defs/expression",
    }
    assert expression_variants[2]["properties"]["observation"] == {
        "$ref": "#/$defs/numeric_observation",
    }
    assert expression_variants[3]["properties"]["observation"] == {
        "$ref": "#/$defs/first_touch_observation",
    }


def test_response_schema_enforces_the_current_family_lane() -> None:
    missing = proposal_schema_for_diversity((
        "Required next hypothesis family must be one of: "
        "observed:first_touch_before_entry.",
    ))
    missing_signal = missing["$defs"]["hypothesis"]["properties"]["signal"]
    assert len(missing_signal["anyOf"]) == 1
    assert missing_signal["anyOf"][0]["properties"]["op"]["enum"] == ["observed"]
    assert missing_signal["anyOf"][0]["properties"]["observation"] == {
        "$ref": "#/$defs/first_touch_observation"
    }

    lane = COMPOUND_LANES[0]
    compound = proposal_schema_for_diversity((
        f"Required next compound lane: {lane}; spread variant B.",
    ))
    compound_signal = compound["$defs"]["hypothesis"]["properties"]["signal"]
    assert compound_signal["properties"]["op"]["enum"] == [lane.split("|", 1)[0]]
    assert compound_signal["properties"]["args"]["minItems"] == 2
    assert compound_signal["properties"]["args"]["maxItems"] == 2
    assert len(compound_signal["properties"]["args"]["prefixItems"]) == 2
    assert "items" not in compound_signal["properties"]["args"]
    compound_structure = compound["$defs"]["hypothesis"]["properties"]["structure"]
    assert compound_structure["properties"]["helper"]["enum"] == ["put_credit_spread"]
    assert compound_structure["properties"]["width"]["enum"] == [10.0]
    assert compound_structure["properties"]["otm_offset"]["enum"] == [5.0]
    assert len(COMPOUND_LANES) >= 22
    assert len(set(COMPOUND_LANE_FAMILIES.values())) == len(COMPOUND_LANES)
    assert {lane.split("|", 1)[0] for lane in COMPOUND_LANES} == {"and", "or"}

    unconstrained = proposal_schema_for_diversity(())
    assert unconstrained["$defs"]["hypothesis"]["properties"]["signal"] == {
        "$ref": "#/$defs/expression"
    }


def test_modeled_runtime_uses_only_supplied_stage_and_builds_gross_denominator(tmp_path: Path) -> None:
    sessions = _sessions(2016)
    manifests = materialize_stage_stores(tmp_path / "stages", sessions, source_hash=_hash("bars"))
    candidate = _candidate({stage: manifest.manifest_hash for stage, manifest in manifests.items()})
    access = StageAccess(tmp_path / "stages", "discovery")
    signal, control = build_modeled_ledgers(candidate, access)
    assert signal.rows
    assert signal.selected_count == len(signal.rows)
    assert control.selected_count == len(control.rows)
    assert all(row.price_source == PriceSourceKind.MODELED for row in signal.rows)
    assert all(row.price_source == PriceSourceKind.MODELED for row in control.rows)
    assert all(row.gross_pnl == pytest.approx(row.gross_settlement - row.signed_entry_debit)
               for row in signal.rows if row.priced)


def test_default_modeled_evaluator_uses_matching_provenance_end_to_end(tmp_path: Path) -> None:
    manifests = materialize_stage_stores(
        tmp_path / "stages", _weekday_sessions(2016, 230), source_hash=_hash("bars")
    )
    candidate = _candidate({stage: item.manifest_hash for stage, item in manifests.items()})

    evaluation = ModeledResearchEvaluator().evaluate(
        candidate, StageAccess(tmp_path / "stages", "discovery"), Stage.DISCOVERY
    )

    assert evaluation.price_source_id == MODELED_PRICE_SOURCE_ID
    assert evaluation.price_source_kind == PriceSourceKind.MODELED
    assert not any("price_source" in failure or "price source" in failure
                   for failure in evaluation.failures)
    assert evaluation.trades >= 120
    assert evaluation.effective_weeks >= 40
    assert evaluation.eligible, evaluation.failures


def test_modeled_late_day_low_credit_spread_has_finite_negative_economics(
        tmp_path: Path) -> None:
    sessions: list[Session] = []
    current = date(2016, 1, 4)
    while len(sessions) < 230:
        if current.weekday() < 5:
            price = 100.0 + len(sessions) * 0.1
            sessions.append(Session(
                current.isoformat(),
                ((570, price, price, price, price),
                 (600, price - 1, price - .8, price - 1.1, price - 1),
                 (930, price - 1, price - .8, price - 1.1, price - 1),
                 (945, price + 3, price + 3, price + 3, price + 3)),
            ))
        current += timedelta(days=1)
    manifests = materialize_stage_stores(
        tmp_path / "stages", sessions, source_hash=_hash("late-day-bars")
    )
    hypothesis = {
        "schema_version": 3,
        "entry_minute": 930,
        "signal": {
            "op": "lt",
            "observation": {"obs": "return_from_open", "minute": 600},
            "value": -0.001,
        },
        "structure": {
            "helper": "put_credit_spread", "width": 10.0, "otm_offset": 5.0,
        },
    }
    canonical = canonical_hypothesis(hypothesis)
    candidate = FrozenCandidate(
        canonical_document=canonical,
        semantic_hash=hypothesis_hash(hypothesis),
        source_hash=_hash("late-day-source"), policy_hash=_hash("policy"),
        evaluator_version="test",
        stage_manifest_hashes={stage: item.manifest_hash for stage, item in manifests.items()},
        descriptor=describe_hypothesis(hypothesis).__dict__,
    )

    evaluation = ModeledResearchEvaluator().evaluate(
        candidate, StageAccess(tmp_path / "stages", "discovery"), Stage.DISCOVERY
    )

    assert evaluation.trades >= 120
    assert math.isfinite(evaluation.absolute_full_win_edge_pp)
    assert math.isfinite(evaluation.base.mean_pnl_pct)
    assert math.isfinite(evaluation.elevated.mean_pnl_pct)
    assert not evaluation.eligible
    assert all("invalid ledger economics" not in failure for failure in evaluation.failures)


def test_campaign_runtime_bounds_bad_formulations_and_never_opens_holdout(tmp_path: Path) -> None:
    sessions = _sessions(2016) + _sessions(2022) + _sessions(2025)
    manifests = materialize_stage_stores(tmp_path / "stages", sessions, source_hash=_hash("bars"))
    opened: list[str] = []

    def access(stage: str) -> StageAccess:
        opened.append(stage)
        return StageAccess(tmp_path / "stages", stage)

    contract = CampaignContract("c", _hash("policy"), "test",
                                {k: v.manifest_hash for k, v in manifests.items()},
                                _hash("test"), 1)
    coordinator = CampaignCoordinator(tmp_path / "campaign", contract, access)
    values = iter(["not-json", json.dumps({"hypothesis": HYPOTHESIS, "rationale": "valid"})])
    runtime = CampaignRuntime(
        coordinator, DeclarativeProposer(lambda _prompt: next(values)),
        ModeledResearchEvaluator(lambda candidate, _a, stage, _n: replace(
            _passing(stage),
            evaluator_version=candidate.evaluator_version,
            stage_manifest_hash=candidate.stage_manifest_hashes[stage.value],
        )),
        mission="test", formulation_limit=3,
    )
    state = runtime.run_until_holdout()
    assert state["state"] == "holdout_ready"
    assert state["scientific_trials"] == 1
    assert runtime.formulations.count == 2
    assert opened == ["discovery", "validation"]
    runtime.consume_holdout()
    assert opened == ["discovery", "validation", "holdout"]


def test_bad_model_output_has_a_separate_hard_limit(tmp_path: Path) -> None:
    manifests = materialize_stage_stores(tmp_path / "stages", [], source_hash=_hash("bars"))
    contract = CampaignContract("c", _hash("policy"), "test",
                                {k: v.manifest_hash for k, v in manifests.items()},
                                _hash("test"), 1)
    coordinator = CampaignCoordinator(
        tmp_path / "campaign", contract, lambda stage: StageAccess(tmp_path / "stages", stage)
    )
    runtime = CampaignRuntime(
        coordinator, DeclarativeProposer(lambda _prompt: "bad"), ModeledResearchEvaluator(),
        mission="test", formulation_limit=2,
    )
    state = runtime.run_until_holdout()
    assert state["state"] == "failed"
    assert state["campaign_failure"]["reason"] == "formulation/admission-failure limit reached"
    assert coordinator.state["scientific_trials"] == 0
    assert runtime.formulations.count == 2


def test_family_feedback_requires_new_shapes_after_atomic_schedule_is_covered() -> None:
    runtime = object.__new__(CampaignRuntime)
    runtime.coordinator = SimpleNamespace(contract=SimpleNamespace(
        required_hypothesis_families=(
            "lt:return_between", "lt:return_from_open",
            "observed:first_touch_before_entry",
        ),
        family_quota=2,
    ))
    attempts = [
        {"admitted": True, "candidate": {"descriptor": {"family": family}}}
        for family in (
            "lt:return_between", "lt:return_from_open",
            "observed:first_touch_before_entry", "lt:return_between",
        )
    ]

    feedback = runtime._hypothesis_family_feedback({"discovery_attempts": attempts})

    assert "atomic-family schedule is satisfied" in feedback
    assert "lt:return_between=2" in feedback
    assert f"Required next compound lane: {COMPOUND_LANES[0]}" in feedback
    assert "spread variant A" in feedback
    assert "response schema enforces" in feedback

    attempts.append({
        "admitted": True,
        "candidate": {"descriptor": {
            "family": COMPOUND_LANE_FAMILIES[COMPOUND_LANES[0]],
            "structure_helper": "call_debit_spread",
            "width": 5.0,
            "otm_offset": 0.0,
        }},
    })
    second_variant = runtime._hypothesis_family_feedback({"discovery_attempts": attempts})
    assert f"Required next compound lane: {COMPOUND_LANES[0]}" in second_variant
    assert "spread variant B" in second_variant


def test_stage_store_is_addressed_by_frozen_source_hash(tmp_path: Path) -> None:
    source = tmp_path / "spy.csv.gz"
    source.write_bytes(b"immutable source")
    all_stages = _sessions(2016, 2) + _sessions(2022, 2) + _sessions(2025, 2)
    staged = ensure_stage_stores(source, tmp_path / "shared", loader=lambda _path: all_stages)
    assert staged.root.name == hashlib.sha256(b"immutable source").hexdigest()
    assert set(staged.manifest_hashes) == {"discovery", "validation", "holdout"}
    again = ensure_stage_stores(source, tmp_path / "shared", loader=lambda _path: pytest.fail())
    assert again.manifest_hashes == staged.manifest_hashes
