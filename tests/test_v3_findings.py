from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace

import pytest

from research.v3.campaign import CampaignContract, FrozenCandidate
from research.v3.evaluator import (
    EVALUATOR_VERSION,
    Evaluation,
    LedgerCounts,
    PriceSourceKind,
    ScenarioMetrics,
    Stage,
    YearMetrics,
    bootstrap_seed,
)
from research.v3.features import (
    CoverageReport,
    CoverageState,
    required_family_coverage_hash,
)
from research.v3.findings import (
    FindingContractError,
    build_portable_finding,
    validate_portable_finding,
)
from research.v3.hypothesis import canonical_hypothesis, describe_hypothesis, hypothesis_hash
from research.v3.opra import EntitlementStatus, OpraBlock, OpraBlockState


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _refresh_integrity(payload: dict) -> None:
    semantic = copy.deepcopy(payload)
    for key in ("finding_id", "created_at", "integrity_hash"):
        semantic.pop(key, None)
    encoded = json.dumps(
        semantic, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode()
    payload["integrity_hash"] = hashlib.sha256(encoded).hexdigest()


def _document() -> dict:
    return {
        "schema_version": 3,
        "entry_minute": 600,
        "signal": {
            "op": "lt",
            "observation": {"obs": "return_from_open", "minute": 590},
            "value": -0.5,
        },
        "structure": {"helper": "call_debit_spread", "width": 2.0, "otm_offset": 0.0},
    }


def _contract(*, proprietary: bool = False) -> CampaignContract:
    coverage = tuple(
        CoverageReport("flow", stage.value, CoverageState.READY, 100, 95, .95,
                       {Stage.DISCOVERY: 80, Stage.VALIDATION: 40, Stage.HOLDOUT: 20}[stage],
                       {Stage.DISCOVERY: 80, Stage.VALIDATION: 40, Stage.HOLDOUT: 20}[stage], True)
        for stage in Stage
    ) if proprietary else ()
    return CampaignContract(
        campaign_id="fixture-campaign",
        policy_hash=_h("policy"),
        evaluator_version=EVALUATOR_VERSION,
        stage_manifest_hashes={stage.value: _h(stage.value) for stage in Stage},
        mission_hash=_h("mission"),
        discovery_budget=50,
        required_family_coverage_hash=required_family_coverage_hash(coverage),
        required_families=("flow",) if proprietary else (),
    )


def _candidate(contract: CampaignContract, *, descriptor: dict | None = None) -> FrozenCandidate:
    document = _document()
    return FrozenCandidate(
        canonical_document=canonical_hypothesis(document),
        semantic_hash=hypothesis_hash(document),
        source_hash=_h("proposer-artifact"),
        policy_hash=contract.policy_hash,
        evaluator_version=contract.evaluator_version,
        stage_manifest_hashes=dict(contract.stage_manifest_hashes),
        descriptor=descriptor or vars(describe_hypothesis(document)),
    )


def _evaluation(stage: Stage, *, eligible: bool = True,
                ranking_score: float | None = None) -> Evaluation:
    base = ScenarioMetrics(4.0, 1.0, 0.03)
    elevated = ScenarioMetrics(2.0, 0.5, 0.045)
    signal = LedgerCounts(120, 120, 1.0, {})
    control = LedgerCounts(120, 120, 1.0, {})
    by_year = {
        "2020": YearMetrics(
            120, 40, 3.0, .25, PriceSourceKind.MODELED,
            signal, control, base, elevated,
        ),
    }
    if stage == Stage.VALIDATION:
        by_year = {
            year: YearMetrics(
                40, 9, 3.0, .25, PriceSourceKind.MODELED,
                LedgerCounts(40, 40, 1.0, {}), LedgerCounts(40, 40, 1.0, {}),
                base, elevated,
            )
            for year in ("2022", "2023", "2024")
        }
    return Evaluation(
        eligible=eligible,
        failures=() if eligible else ("negative gate",),
        stage=stage,
        trades=120,
        effective_weeks=40,
        alpha=.05,
        bootstrap_seed=bootstrap_seed(
            hypothesis_hash(_document()), _h(stage.value), "modeled", "base",
        ),
        price_source_id="modeled",
        price_source_kind=PriceSourceKind.MODELED,
        evaluator_version=EVALUATOR_VERSION,
        stage_manifest_hash=_h(stage.value),
        signal=signal,
        control=control,
        absolute_full_win_edge_pp=3.0 if eligible else -1.0,
        control_relative_edge_pp=.25,
        base=base,
        elevated=elevated,
        by_year=by_year,
        ranking_score_pp=(ranking_score if ranking_score is not None
                          else (1.0 if eligible else -1.0)),
    )


def _opra(*, holdout: bool, state: OpraBlockState = OpraBlockState.PASSED) -> OpraBlock:
    passed = state == OpraBlockState.PASSED
    return OpraBlock(
        state=state,
        entitlement=(EntitlementStatus.AVAILABLE if passed else EntitlementStatus.UNAVAILABLE),
        selected_sessions=40,
        priced_sessions=35 if passed else 0,
        coverage=.875 if passed else 0.0,
        effective_weeks=14 if passed else 0,
        base_mean_pnl_pct=3.0 if passed else None,
        base_lower_bound_pct=.5 if passed else None,
        elevated_mean_pnl_pct=1.5 if passed else None,
        decision_manifest_hash=_h("decision-holdout" if holdout else "decision-validation"),
        quote_artifact_hash=_h("quotes-holdout" if holdout else "quotes-validation"),
        receipt_id=_h("receipt") if holdout else None,
        failures=() if passed else ("OPRA entitlement unavailable",),
    )


def _coverage() -> tuple[CoverageReport, ...]:
    return tuple(
        CoverageReport("flow", stage.value, CoverageState.READY, 100, 95, .95,
                       {Stage.DISCOVERY: 80, Stage.VALIDATION: 40, Stage.HOLDOUT: 20}[stage],
                       {Stage.DISCOVERY: 80, Stage.VALIDATION: 40, Stage.HOLDOUT: 20}[stage], True)
        for stage in Stage
    )


def _build(*, contract: CampaignContract | None = None,
           candidate: FrozenCandidate | None = None,
           validation_opra: OpraBlock | None = None,
           holdout_opra: OpraBlock | None = None,
           holdout: Evaluation | None = None,
           finding_id: str = "finding-a", created_at: str = "2026-08-11T09:00:00Z"):
    contract = contract or _contract()
    candidate = candidate or _candidate(contract)
    return build_portable_finding(
        finding_id=finding_id,
        created_at=created_at,
        campaign_state="finding_ready",
        contract=contract,
        candidate=candidate,
        frozen_source_reference="artifact://campaign/candidates/finalist.json",
        mission_name="spy-price-only",
        mission_hash=_h("mission"),
        evaluator_hash=_h("evaluator-source"),
        feature_schema_hash=_h("feature-schema"),
        discovery=_evaluation(Stage.DISCOVERY),
        validation=_evaluation(Stage.VALIDATION),
        holdout=holdout or _evaluation(Stage.HOLDOUT),
        validation_opra=validation_opra or _opra(holdout=False),
        holdout_opra=holdout_opra or _opra(holdout=True),
        holdout_receipt_id=_h("receipt"),
        feature_coverage=_coverage() if contract.required_families else (),
        aggregate_lineage_hashes={"modeled-ledger": _h("ledger")},
        limitations=("Historical fills are not live fills.",),
        invalidation_conditions=("Net edge lower bound becomes non-positive.",),
        paper_shadow_monitoring=("Reconcile every modeled and observed fill.",),
    )


def test_complete_finding_is_portable_research_only_and_schema_valid() -> None:
    finding = _build()
    payload = finding.to_dict()

    assert payload["authority"] == {
        "classification": "research_only",
        "execution_authorized": False,
        "next_allowed_action": "paper_shadow_review",
        "paper_shadow_review_eligible": True,
    }
    assert payload["lineage"]["redistribution_class"] == "derived_aggregate_only"
    assert set(payload["evidence"]) == {
        "discovery", "validation", "holdout", "validation_opra", "holdout_opra",
        "feature_coverage",
    }
    assert "canonical_document" not in json.dumps(payload)
    validate_portable_finding(payload)


@pytest.mark.parametrize("state", [
    OpraBlockState.UNAVAILABLE,
    OpraBlockState.INSUFFICIENT_COVERAGE,
    OpraBlockState.FAILED_EVIDENCE,
])
def test_typed_nonpassing_holdout_opra_is_exported_but_never_eligible(state) -> None:
    block = _opra(holdout=True, state=state)
    if state != OpraBlockState.UNAVAILABLE:
        metrics = ({
            "base_mean_pnl_pct": -1.0,
            "base_lower_bound_pct": -2.0,
            "elevated_mean_pnl_pct": -3.0,
        } if state == OpraBlockState.FAILED_EVIDENCE else {})
        coverage = ({
            "priced_sessions": 35,
            "coverage": .875,
            "effective_weeks": 14,
        } if state == OpraBlockState.FAILED_EVIDENCE else {})
        block = OpraBlock(
            **{**vars(block), "entitlement": EntitlementStatus.AVAILABLE,
               "failures": ("typed OPRA gate failed",), **metrics, **coverage}
        )
    payload = _build(holdout_opra=block).to_dict()

    assert payload["evidence"]["holdout_opra"]["state"] == state.value
    assert payload["authority"]["paper_shadow_review_eligible"] is False


def test_failed_modeled_gate_cannot_produce_survivor_finding() -> None:
    with pytest.raises(FindingContractError, match="cannot produce a survivor"):
        _build(holdout=_evaluation(Stage.HOLDOUT, eligible=False))


def test_finite_nonpositive_ranking_does_not_veto_passed_hard_gates() -> None:
    finding = _build(holdout=_evaluation(Stage.HOLDOUT, ranking_score=-2.0))
    assert finding.to_dict()["evidence"]["holdout"]["ranking_score_pp"] == -2.0


def test_finding_semantics_are_deterministic_except_id_and_creation_time() -> None:
    first = _build(finding_id="one", created_at="2026-08-11T09:00:00Z")
    second = _build(finding_id="two", created_at="2026-08-12T10:00:00Z")

    assert first.integrity_hash == second.integrity_hash
    left, right = first.to_dict(), second.to_dict()
    left.pop("finding_id"); right.pop("finding_id")
    left.pop("created_at"); right.pop("created_at")
    assert left == right
    assert first.to_json() == json.dumps(first.to_dict(), sort_keys=True, separators=(",", ":"))


def test_proprietary_mission_requires_every_ready_split() -> None:
    contract = _contract(proprietary=True)
    passed = _build(contract=contract).to_dict()
    assert passed["authority"]["paper_shadow_review_eligible"] is True

    reports = list(_coverage())
    reports[-1] = CoverageReport(
        "flow", "holdout", CoverageState.INSUFFICIENT_COVERAGE,
        100, 89, .89, 20, 20, True,
    )
    with pytest.raises(FindingContractError, match="frozen campaign admission"):
        build_portable_finding(
            finding_id="feature-gap", created_at="2026-08-11T09:00:00Z",
            campaign_state="finding_ready", contract=contract, candidate=_candidate(contract),
            frozen_source_reference="artifact://finalist", mission_name="flow-mission",
            mission_hash=_h("mission"), evaluator_hash=_h("evaluator"),
            feature_schema_hash=_h("features"), discovery=_evaluation(Stage.DISCOVERY),
            validation=_evaluation(Stage.VALIDATION), holdout=_evaluation(Stage.HOLDOUT),
            validation_opra=_opra(holdout=False), holdout_opra=_opra(holdout=True),
            holdout_receipt_id=_h("receipt"), feature_coverage=reports,
            limitations=("Research evidence only.",),
            invalidation_conditions=("Any gate becomes non-positive.",),
            paper_shadow_monitoring=("Monitor realized costs.",),
        )


@pytest.mark.parametrize("descriptor", [
    {"raw_rows": [{"premium": 1_000}]},
    {"provider_payload": {"trade": "private"}},
    {"api_key": "do-not-export"},
])
def test_raw_provider_and_secret_like_fields_are_rejected(descriptor) -> None:
    contract = _contract()
    with pytest.raises(FindingContractError, match="forbidden field"):
        _build(contract=contract, candidate=_candidate(contract, descriptor=descriptor))


@pytest.mark.parametrize("smuggled", [
    "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.secret.signature",
    "api_key=sk-thisisarealisticlongsecretvalue12345",
    "-----BEGIN PRIVATE KEY----- ABCDEF",
    r"Loaded evidence from C:\\Users\\analyst\\provider-dump.json",
    "/home/analyst/private/provider.json was inspected",
    '{"provider_payload":{"trade":"private"}}',
])
def test_free_text_rejects_secret_raw_and_local_path_value_smuggling(smuggled: str) -> None:
    payload = _build().to_dict()
    payload["limitations"] = [smuggled]
    with pytest.raises(FindingContractError, match="forbidden value"):
        validate_portable_finding(payload)


def test_deserialized_artifact_rejects_missing_hash_tampering_and_redistribution() -> None:
    payload = _build().to_dict()
    missing = copy.deepcopy(payload)
    del missing["contracts"]["feature_schema_hash"]
    with pytest.raises(FindingContractError, match="contract block"):
        validate_portable_finding(missing)

    redistributed = copy.deepcopy(payload)
    redistributed["lineage"]["redistribution_class"] = "raw_provider_data"
    with pytest.raises(FindingContractError, match="derived aggregates"):
        validate_portable_finding(redistributed)

    authority = copy.deepcopy(payload)
    authority["authority"]["execution_authorized"] = True
    with pytest.raises(FindingContractError, match="research-only"):
        validate_portable_finding(authority)


def test_receipts_and_frozen_provenance_must_match() -> None:
    wrong_receipt = OpraBlock(**{**vars(_opra(holdout=True)), "receipt_id": _h("wrong")})
    with pytest.raises(FindingContractError, match="durable receipt"):
        _build(holdout_opra=wrong_receipt)

    contract = _contract()
    candidate = _candidate(contract)
    changed = FrozenCandidate(**{**vars(candidate), "policy_hash": _h("changed")})
    with pytest.raises(FindingContractError, match="frozen campaign contract"):
        _build(contract=contract, candidate=changed)


@pytest.mark.parametrize(("field", "value", "message"), [
    ("evaluator_version", "tampered", "provenance"),
    ("stage_manifest_hash", _h("tampered"), "provenance"),
])
def test_deserialized_modeled_provenance_tampering_fails_even_with_rehashed_integrity(
        field: str, value, message: str) -> None:
    payload = _build().to_dict()
    payload["evidence"]["holdout"][field] = value
    _refresh_integrity(payload)

    with pytest.raises(FindingContractError, match=message):
        validate_portable_finding(payload)


def test_deserialized_count_and_coverage_tampering_fails_even_with_rehashed_integrity() -> None:
    payload = _build().to_dict()
    payload["evidence"]["holdout"]["signal"]["coverage"] = .5
    _refresh_integrity(payload)

    with pytest.raises(FindingContractError, match="coverage is inconsistent"):
        validate_portable_finding(payload)


def test_deserialized_bootstrap_seed_tampering_fails_even_with_rehashed_integrity() -> None:
    payload = _build().to_dict()
    payload["evidence"]["validation"]["bootstrap_seed"] += 1
    _refresh_integrity(payload)

    with pytest.raises(FindingContractError, match="bootstrap provenance"):
        validate_portable_finding(payload)


def test_portable_evidence_round_trips_exactly_with_typed_control() -> None:
    finding = _build()
    payload = finding.to_dict()
    restored = json.loads(finding.to_json())

    assert restored == payload
    for stage in ("discovery", "validation", "holdout"):
        assert restored["evidence"][stage]["control"]["priced_count"] == 120
        assert restored["evidence"][stage]["control_relative_edge_pp"] == .25
        assert restored["evidence"][stage]["price_source_id"] == "modeled"
        assert restored["evidence"][stage]["price_source_kind"] == "modeled"


def test_finding_only_consumer_can_prove_canonical_research_scope() -> None:
    payload = _build().to_dict()
    contract = _contract()
    assert payload["contracts"]["research_scope"] == {
        "program_id": "spy-price-v3", "symbol": "SPY", "feature_families": [],
    }
    assert payload["contracts"]["research_scope_hash"] == contract.research_scope_hash
    assert payload["candidate"]["research_scope_hash"] == contract.research_scope_hash

    wrong_scope = copy.deepcopy(payload)
    wrong_scope["contracts"]["research_scope"]["symbol"] = "PLTR"
    _refresh_integrity(wrong_scope)
    with pytest.raises(FindingContractError, match="frozen contract"):
        validate_portable_finding(wrong_scope)

    wrong_candidate = copy.deepcopy(payload)
    wrong_candidate["candidate"]["research_scope_hash"] = _h("wrong scope")
    _refresh_integrity(wrong_candidate)
    with pytest.raises(FindingContractError, match="candidate provenance"):
        validate_portable_finding(wrong_candidate)


def test_survivor_finding_rejects_absent_control() -> None:
    holdout = replace(
        _evaluation(Stage.HOLDOUT), control=None, control_relative_edge_pp=None,
    )
    with pytest.raises(FindingContractError, match="requires typed control counts"):
        _build(holdout=holdout)


def test_survivor_finding_rejects_missing_control_relative_diagnostic() -> None:
    holdout = replace(_evaluation(Stage.HOLDOUT), control_relative_edge_pp=None)
    with pytest.raises(FindingContractError, match="control-relative diagnostic"):
        _build(holdout=holdout)


def test_negative_control_relative_diagnostic_does_not_veto_survivor() -> None:
    holdout = _evaluation(Stage.HOLDOUT)
    holdout = replace(
        holdout,
        control_relative_edge_pp=-99.0,
        by_year={
            year: replace(item, control_relative_edge_pp=-99.0)
            for year, item in holdout.by_year.items()
        },
    )

    payload = _build(holdout=holdout).to_dict()
    assert payload["authority"]["paper_shadow_review_eligible"] is True
    assert payload["evidence"]["holdout"]["control_relative_edge_pp"] == -99.0


def test_survivor_finding_rejects_price_source_kind_id_mismatch() -> None:
    holdout = replace(_evaluation(Stage.HOLDOUT), price_source_id="indicative:vendor")
    with pytest.raises(FindingContractError, match="provenance"):
        _build(holdout=holdout)


def test_deserialized_tampered_year_control_counts_fail_after_rehash() -> None:
    payload = _build().to_dict()
    year_control = payload["evidence"]["holdout"]["by_year"]["2020"]["control"]
    year_control["selected_count"] += 1
    year_control["priced_count"] += 1
    _refresh_integrity(payload)

    with pytest.raises(FindingContractError, match="yearly control counts do not reconcile"):
        validate_portable_finding(payload)
