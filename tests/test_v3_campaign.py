from __future__ import annotations

import hashlib
import json
import multiprocessing
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from research.v3.campaign import (
    CampaignCallbackError,
    CampaignError,
    CampaignContract,
    CampaignCoordinator,
    FrozenCandidate,
    FrozenContractMismatch,
    HoldoutConsumedError,
    InvalidTransition,
)
from research.v3.evaluator import (
    Evaluation,
    LedgerCounts,
    PriceSourceKind,
    ScenarioMetrics,
    Stage,
    YearMetrics,
    bootstrap_seed,
)
from research.v3.features import (
    COVERAGE_WEEK_FLOORS,
    CoverageReport,
    CoverageState,
    required_family_coverage_hash,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def contract(*, budget: int = 1, family_quota: int = 2,
             required_families: tuple[str, ...] = (),
             required_hypothesis_families: tuple[str, ...] = (),
             feature_coverage: tuple[CoverageReport, ...] = ()) -> CampaignContract:
    return CampaignContract(
        campaign_id="fixture",
        policy_hash=digest("policy"),
        evaluator_version="spy-options-staged-v3",
        stage_manifest_hashes={stage.value: digest(stage.value) for stage in Stage},
        mission_hash=digest("test mission"),
        discovery_budget=budget,
        required_family_coverage_hash=required_family_coverage_hash(feature_coverage),
        family_quota=family_quota,
        required_families=required_families,
        required_hypothesis_families=required_hypothesis_families,
    )


class FakeAccess:
    def __init__(self, stage: str):
        self.stage = stage


class AccessFactory:
    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, stage: str):
        self.calls.append(stage)
        return FakeAccess(stage)


def _start_campaign_process(root: str, frozen_contract: CampaignContract) -> None:
    coordinator = CampaignCoordinator(
        Path(root), frozen_contract, lambda stage: FakeAccess(stage)
    )
    coordinator.start_discovery()


def document(threshold: float = -.5, *, between: bool = False) -> dict:
    observation = (
        {"obs": "return_between", "start_minute": 570, "end_minute": 595}
        if between else {"obs": "return_from_open", "minute": 595}
    )
    return {
        "schema_version": 3,
        "entry_minute": 600,
        "signal": {"op": "lte", "observation": observation, "value": threshold},
        "structure": {"helper": "call_debit_spread", "width": 2.0, "otm_offset": 0.0},
    }


def evaluation(
    candidate: FrozenCandidate,
    stage: Stage,
    score: float,
    *,
    eligible: bool = True,
    shortlist_size: int = 1,
) -> Evaluation:
    scenario = ScenarioMetrics(1.0, .5, .03)
    signal = LedgerCounts(120, 120, 1.0, {})
    control = LedgerCounts(120, 120, 1.0, {})
    manifest_hash = candidate.stage_manifest_hashes[stage.value]
    by_year = {}
    effective_weeks = 40
    if stage == Stage.VALIDATION:
        effective_weeks = 27
        by_year = {
            year: YearMetrics(
                trades=40,
                effective_weeks=9,
                absolute_full_win_edge_pp=1.0,
                control_relative_edge_pp=.25,
                price_source_kind=PriceSourceKind.MODELED,
                signal=LedgerCounts(40, 40, 1.0, {}),
                control=LedgerCounts(40, 40, 1.0, {}),
                base=scenario,
                elevated=ScenarioMetrics(.75, .25, .045),
            )
            for year in ("2022", "2023", "2024")
        }
    return Evaluation(
        eligible=eligible,
        failures=() if eligible else ("failed gate",),
        stage=stage,
        trades=120,
        effective_weeks=effective_weeks,
        alpha=.05 / shortlist_size if stage == Stage.VALIDATION else .05,
        bootstrap_seed=bootstrap_seed(
            candidate.semantic_hash, manifest_hash, "modeled", "base",
            evaluator_version=candidate.evaluator_version,
        ),
        price_source_id="modeled",
        price_source_kind=PriceSourceKind.MODELED,
        evaluator_version=candidate.evaluator_version,
        stage_manifest_hash=manifest_hash,
        signal=signal,
        control=control,
        absolute_full_win_edge_pp=1.0,
        control_relative_edge_pp=.25,
        base=scenario,
        elevated=ScenarioMetrics(.75, .25, .045),
        by_year=by_year,
        ranking_score_pp=score,
    )


def ready_campaign(tmp_path: Path, *, budget: int = 1, family_quota: int = 2):
    factory = AccessFactory()
    coordinator = CampaignCoordinator(
        tmp_path, contract(budget=budget, family_quota=family_quota), factory
    )
    coordinator.start_discovery()
    return coordinator, factory


def finalist_campaign(tmp_path: Path):
    coordinator, factory = ready_campaign(tmp_path)
    coordinator.submit_discovery(
        document(), source_hash=digest("source"), attempt_id="one",
        evaluate=lambda candidate, access: evaluation(candidate, Stage.DISCOVERY, 3)
        if access.stage == "discovery" else (_ for _ in ()).throw(AssertionError()),
    )
    coordinator.close_discovery()
    coordinator.run_validation(
        lambda candidate, access, shortlist_size: evaluation(
            candidate, Stage.VALIDATION, 2, shortlist_size=shortlist_size
        )
        if access.stage == "validation" and shortlist_size == 1
        else (_ for _ in ()).throw(AssertionError())
    )
    return coordinator, factory


def test_rejected_admission_does_not_consume_trial_budget(tmp_path: Path) -> None:
    coordinator, factory = ready_campaign(tmp_path, budget=2)
    first = coordinator.submit_discovery(
        document(), source_hash=digest("a"), attempt_id="one",
        evaluate=lambda candidate, *_: evaluation(candidate, Stage.DISCOVERY, 2),
    )
    duplicate = coordinator.submit_discovery(
        document(), source_hash=digest("b"), attempt_id="duplicate",
        evaluate=lambda *_: (_ for _ in ()).throw(AssertionError("must not evaluate")),
    )
    near = coordinator.submit_discovery(
        document(-.55), source_hash=digest("c"), attempt_id="near",
        evaluate=lambda *_: (_ for _ in ()).throw(AssertionError("must not evaluate")),
    )
    second = coordinator.submit_discovery(
        document(-1.0), source_hash=digest("d"), attempt_id="two",
        evaluate=lambda candidate, *_: evaluation(candidate, Stage.DISCOVERY, 1),
    )
    assert first["admitted"] and second["admitted"]
    assert duplicate["reason"] == "semantic duplicate"
    assert near["reason"] == "near duplicate"
    assert coordinator.state["scientific_trials"] == 2
    assert factory.calls == ["discovery", "discovery"]


def test_proprietary_coverage_families_do_not_block_hypothesis_admission(tmp_path: Path) -> None:
    coverage = tuple(
        CoverageReport("flow", split, CoverageState.READY, floor, floor, 1.0,
                       floor, floor, True)
        for split, floor in COVERAGE_WEEK_FLOORS.items()
    )
    factory = AccessFactory()
    coordinator = CampaignCoordinator(
        tmp_path, contract(required_families=("flow",), feature_coverage=coverage), factory
    )
    coordinator.start_discovery(coverage)
    result = coordinator.submit_discovery(
        document(), source_hash=digest("flow-mission"), attempt_id="one",
        evaluate=lambda candidate, *_: evaluation(candidate, Stage.DISCOVERY, 1),
    )
    assert result["admitted"] is True
    assert coordinator.state["scientific_trials"] == 1


def test_frozen_hypothesis_family_schedule_rejects_repeat_without_trial_spend(
    tmp_path: Path,
) -> None:
    schedule = ("lte:return_between", "lte:return_from_open")
    coordinator = CampaignCoordinator(
        tmp_path,
        contract(budget=3, required_hypothesis_families=schedule),
        AccessFactory(),
    )
    coordinator.start_discovery()
    first = coordinator.submit_discovery(
        document(), source_hash=digest("first"), attempt_id="first",
        evaluate=lambda candidate, *_: evaluation(candidate, Stage.DISCOVERY, 3),
    )
    repeated = coordinator.submit_discovery(
        document(-1.0), source_hash=digest("repeat"), attempt_id="repeat",
        evaluate=lambda *_: pytest.fail("family rejection must precede evaluation"),
    )
    missing = coordinator.submit_discovery(
        document(-.5, between=True), source_hash=digest("missing"), attempt_id="missing",
        evaluate=lambda candidate, *_: evaluation(candidate, Stage.DISCOVERY, 2),
    )
    assert first["admitted"] is True
    assert repeated["reason"] == "family balance gate"
    assert missing["admitted"] is True
    assert coordinator.state["scientific_trials"] == 2


def test_campaign_failure_is_durable_hash_chained_and_idempotent(tmp_path: Path) -> None:
    coordinator, _ = ready_campaign(tmp_path)
    failed = coordinator.fail("worker stopped")
    assert failed["state"] == "failed"
    assert failed["campaign_failure"]["reason"] == "worker stopped"
    assert coordinator.fail("worker stopped")["last_sequence"] == failed["last_sequence"]
    reopened = CampaignCoordinator(tmp_path, contract(), AccessFactory()).state
    assert reopened["event_head_hash"] == failed["event_head_hash"]


def test_proprietary_discovery_fails_closed_without_frozen_ready_coverage(tmp_path: Path) -> None:
    coordinator = CampaignCoordinator(
        tmp_path, contract(required_families=("flow",)), AccessFactory()
    )
    with pytest.raises(InvalidTransition, match="coverage is not ready"):
        coordinator.start_discovery()
    assert coordinator.state["state"] == "ready"


def test_eligible_finite_ranking_survives_even_when_nonpositive(tmp_path: Path) -> None:
    coordinator, _ = ready_campaign(tmp_path)
    coordinator.submit_discovery(
        document(), source_hash=digest("negative-ranking"), attempt_id="one",
        evaluate=lambda candidate, *_: evaluation(
            candidate, Stage.DISCOVERY, -1, eligible=True
        ),
    )
    assert coordinator.close_discovery()["state"] == "shortlist_frozen"
    state = coordinator.run_validation(
        lambda candidate, _access, shortlist_size: evaluation(
            candidate, Stage.VALIDATION, 0, eligible=True,
            shortlist_size=shortlist_size,
        )
    )
    assert state["state"] == "holdout_ready"


@pytest.mark.parametrize("missing", ["aggregate", "year"])
def test_eligible_evaluation_without_typed_control_is_rejected(
    tmp_path: Path, missing: str,
) -> None:
    coordinator, _ = ready_campaign(tmp_path)

    def injected(candidate: FrozenCandidate, *_args) -> Evaluation:
        result = evaluation(candidate, Stage.DISCOVERY, 2)
        if missing == "aggregate":
            return replace(result, control=None, control_relative_edge_pp=None)
        yearly = YearMetrics(
            trades=120, effective_weeks=40, absolute_full_win_edge_pp=1.0,
            control_relative_edge_pp=float("nan"),
            price_source_kind=PriceSourceKind.MODELED,
            signal=result.signal, control=None, base=result.base, elevated=result.elevated,
        )
        return replace(result, by_year={"2016": yearly})

    attempt = coordinator.submit_discovery(
        document(), source_hash=digest(missing), attempt_id="one", evaluate=injected,
    )
    assert attempt["evaluation"]["eligible"] is False
    assert "typed" in attempt["evaluation"]["failures"][0]


def test_no_discovery_survivor_is_terminal_before_validation(tmp_path: Path) -> None:
    coordinator, factory = ready_campaign(tmp_path)
    coordinator.submit_discovery(
        document(), source_hash=digest("a"), attempt_id="one",
        evaluate=lambda candidate, *_: evaluation(
            candidate, Stage.DISCOVERY, -1, eligible=False
        ),
    )
    assert coordinator.close_discovery()["state"] == "no_discovery_survivor"
    with pytest.raises(InvalidTransition):
        coordinator.run_validation(
            lambda candidate, _access, shortlist_size: evaluation(
                candidate, Stage.VALIDATION, 1, shortlist_size=shortlist_size
            )
        )
    assert "validation" not in factory.calls


def test_balanced_shortlist_and_one_validation_finalist(tmp_path: Path) -> None:
    coordinator, _ = ready_campaign(tmp_path, budget=4, family_quota=3)
    trials = [
        (document(-.5), "a", 9),
        (document(-1.0), "b", 8),
        (document(-1.5), "c", 7),
        (document(-.5, between=True), "d", 6),
    ]
    for index, (doc, source, score) in enumerate(trials):
        coordinator.submit_discovery(
            doc, source_hash=digest(source), attempt_id=str(index),
            evaluate=lambda candidate, *_args, score=score: evaluation(
                candidate, Stage.DISCOVERY, score
            ),
        )
    state = coordinator.close_discovery()
    assert len(state["shortlist"]) == 3
    # The first shortlist pass takes the best member of each distinct cluster.
    families = [item["descriptor"]["family"] for item in state["shortlist"][:2]]
    assert len(set(families)) == 2
    calls: list[tuple[str, int]] = []

    def validate(candidate, access, shortlist_size):
        calls.append((candidate.semantic_hash, shortlist_size))
        return evaluation(
            candidate,
            Stage.VALIDATION,
            3 if candidate.source_hash == digest("b") else 1,
            shortlist_size=shortlist_size,
        )

    final = coordinator.run_validation(validate)
    assert final["state"] == "holdout_ready"
    assert final["finalist"]["source_hash"] == digest("b")
    assert all(size == 3 for _, size in calls)


def test_validation_is_idempotent_and_concurrent_calls_do_not_repeat(tmp_path: Path) -> None:
    coordinator, _ = ready_campaign(tmp_path)
    coordinator.submit_discovery(
        document(), source_hash=digest("a"), attempt_id="one",
        evaluate=lambda candidate, *_: evaluation(candidate, Stage.DISCOVERY, 2),
    )
    coordinator.close_discovery()
    count = 0
    guard = threading.Lock()

    def validate(*_args):
        nonlocal count
        with guard:
            count += 1
        time.sleep(.05)
        candidate, _access, shortlist_size = _args
        return evaluation(
            candidate, Stage.VALIDATION, 2, shortlist_size=shortlist_size
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: coordinator.run_validation(validate), range(2)))
    assert count == 1
    assert {item["state"] for item in results} == {"holdout_ready"}


def test_cross_process_initialization_and_transition_serialize(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=_start_campaign_process, args=(str(tmp_path), contract()))
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(15)
        assert process.exitcode == 0
    coordinator = CampaignCoordinator(tmp_path, contract(), AccessFactory())
    assert coordinator.state["state"] == "discovery"
    assert coordinator.state["last_sequence"] == 2


def test_no_validation_survivor_is_terminal_before_holdout(tmp_path: Path) -> None:
    coordinator, factory = ready_campaign(tmp_path)
    coordinator.submit_discovery(
        document(), source_hash=digest("a"), attempt_id="one",
        evaluate=lambda candidate, *_: evaluation(candidate, Stage.DISCOVERY, 2),
    )
    coordinator.close_discovery()
    state = coordinator.run_validation(
        lambda candidate, _access, shortlist_size: evaluation(
            candidate, Stage.VALIDATION, -1, eligible=False,
            shortlist_size=shortlist_size,
        )
    )
    assert state["state"] == "no_validation_survivor"
    assert state["finalist"] is None
    assert "holdout" not in factory.calls


def test_changed_campaign_contract_is_rejected_on_reopen(tmp_path: Path) -> None:
    CampaignCoordinator(tmp_path, contract(), AccessFactory())
    with pytest.raises(FrozenContractMismatch):
        CampaignCoordinator(
            tmp_path, replace(contract(), policy_hash=digest("changed")), AccessFactory()
        )


def test_event_replay_recovers_append_before_state_replace(tmp_path: Path, monkeypatch) -> None:
    coordinator = CampaignCoordinator(tmp_path, contract(), AccessFactory())
    original = coordinator._write_materialized
    calls = 0

    def crash_once(state):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("simulated replace crash")
        original(state)

    monkeypatch.setattr(coordinator, "_write_materialized", crash_once)
    with pytest.raises(OSError, match="replace crash"):
        coordinator.start_discovery()
    recovered = CampaignCoordinator(tmp_path, contract(), AccessFactory())
    assert recovered.state["state"] == "discovery"
    assert recovered.state["last_sequence"] == 2


def test_interrupted_discovery_attempt_is_durable_before_callback(tmp_path: Path) -> None:
    coordinator, _ = ready_campaign(tmp_path)

    def inspect_then_crash(*_args):
        events = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
        assert "discovery-admission:one" in events
        raise RuntimeError("interrupted evaluator")

    result = coordinator.submit_discovery(
        document(), source_hash=digest("a"), attempt_id="one", evaluate=inspect_then_crash,
    )
    assert result["admitted"] is True
    assert result["evaluation"]["eligible"] is False
    assert "interrupted evaluator" in result["evaluation"]["failures"][0]


def test_nonfinite_failed_evaluation_is_recorded_as_json_null(tmp_path: Path) -> None:
    coordinator, _ = ready_campaign(tmp_path)

    def invalid_evaluation(candidate, *_args):
        return replace(
            evaluation(candidate, Stage.DISCOVERY, 1, eligible=False),
            absolute_full_win_edge_pp=float("nan"),
            ranking_score_pp=float("nan"),
        )

    result = coordinator.submit_discovery(
        document(), source_hash=digest("a"), attempt_id="one", evaluate=invalid_evaluation,
    )
    assert result["evaluation"]["absolute_full_win_edge_pp"] is None
    assert result["evaluation"]["ranking_score_pp"] is None
    assert coordinator.close_discovery()["state"] == "no_discovery_survivor"


@pytest.mark.parametrize(("field", "value", "message"), [
    ("evaluator_version", "changed-evaluator", "evaluation version differs"),
    ("stage_manifest_hash", digest("changed-manifest"), "evaluation manifest differs"),
])
def test_discovery_evaluation_must_match_frozen_evaluator_contract(
        tmp_path: Path, field: str, value, message: str) -> None:
    coordinator, _ = ready_campaign(tmp_path)

    def mismatched(candidate, *_args):
        return replace(
            evaluation(candidate, Stage.DISCOVERY, 1),
            **{field: value},
        )

    result = coordinator.submit_discovery(
        document(), source_hash=digest(field), attempt_id="one", evaluate=mismatched,
    )

    assert result["evaluation"]["eligible"] is False
    assert any(message in failure for failure in result["evaluation"]["failures"])
    assert coordinator.state["scientific_trials"] == 1


@pytest.mark.parametrize("field,value", [
    ("source_hash", digest("changed-source")),
    ("semantic_hash", digest("changed-semantic")),
    ("policy_hash", digest("changed-policy")),
    ("evaluator_version", "changed-evaluator"),
    ("research_scope_hash", digest("changed-scope")),
    ("stage_manifest_hashes", {stage.value: digest("changed-" + stage.value) for stage in Stage}),
])
def test_holdout_rejects_any_changed_frozen_contract(tmp_path: Path, field: str, value) -> None:
    coordinator, factory = finalist_campaign(tmp_path)
    changed = replace(coordinator.finalist_contract(), **{field: value})
    with pytest.raises(FrozenContractMismatch):
        coordinator.run_holdout(
            changed,
            lambda candidate, *_: evaluation(candidate, Stage.HOLDOUT, 1),
        )
    assert "holdout" not in factory.calls
    assert coordinator.state["holdout_receipt"] is None


def test_holdout_receipt_precedes_access_and_callback_crash_consumes_forever(tmp_path: Path) -> None:
    coordinator, factory = finalist_campaign(tmp_path)
    finalist = coordinator.finalist_contract()

    def guarded_access(stage: str):
        factory.calls.append(stage)
        if stage == "holdout":
            durable = CampaignCoordinator(tmp_path, contract(), AccessFactory()).state
            assert durable["state"] == "holdout_consumed"
            assert durable["holdout_receipt"] is not None
        return FakeAccess(stage)

    coordinator._stage_access_factory = guarded_access

    def crash(_candidate, access):
        assert access.stage == "holdout"
        raise RuntimeError("validator crashed")

    with pytest.raises(CampaignCallbackError):
        coordinator.run_holdout(finalist, crash)
    state = coordinator.state
    assert state["state"] == "holdout_consumed"
    assert state["holdout_receipt"] is not None
    assert "validator crashed" in state["holdout_result"]["error"]
    with pytest.raises(HoldoutConsumedError):
        coordinator.run_holdout(
            finalist,
            lambda candidate, *_: evaluation(candidate, Stage.HOLDOUT, 4),
        )
    assert factory.calls.count("holdout") == 1


def test_successful_holdout_is_also_one_use(tmp_path: Path) -> None:
    coordinator, factory = finalist_campaign(tmp_path)
    finalist = coordinator.finalist_contract()
    state = coordinator.run_holdout(
        finalist,
        lambda candidate, access: evaluation(candidate, Stage.HOLDOUT, 1)
        if access.stage == "holdout" else (_ for _ in ()).throw(AssertionError()),
    )
    assert state["holdout_result"]["evaluation"]["eligible"] is True
    with pytest.raises(HoldoutConsumedError):
        coordinator.run_holdout(
            finalist,
            lambda candidate, *_: evaluation(candidate, Stage.HOLDOUT, 1),
        )
    assert factory.calls.count("holdout") == 1


def test_receipt_only_crash_can_fail_durably_without_reopening_holdout(
    tmp_path: Path, monkeypatch,
) -> None:
    coordinator, factory = finalist_campaign(tmp_path)
    finalist = coordinator.finalist_contract()
    original = coordinator._write_materialized
    crashed = False

    def crash_after_receipt(state):
        nonlocal crashed
        if (not crashed and state["state"] == "holdout_consumed"
                and state["holdout_result"] is None):
            crashed = True
            raise OSError("simulated receipt materialization crash")
        original(state)

    monkeypatch.setattr(coordinator, "_write_materialized", crash_after_receipt)
    with pytest.raises(OSError, match="receipt materialization"):
        coordinator.run_holdout(
            finalist, lambda *_args: pytest.fail("holdout access must remain sealed")
        )
    interrupted = coordinator.state
    assert interrupted["state"] == "holdout_consumed"
    assert interrupted["holdout_result"] is None
    assert factory.calls.count("holdout") == 0

    failed = coordinator.fail_post_holdout(
        "receipt-only interruption; holdout remains consumed"
    )
    assert failed["state"] == "failed"
    assert failed["campaign_failure"]["receipt_id"] == interrupted["holdout_receipt"]["receipt_id"]
    assert factory.calls.count("holdout") == 0
    with pytest.raises(HoldoutConsumedError):
        coordinator.run_holdout(finalist, lambda *_args: pytest.fail("must not reopen"))


def test_modeled_result_without_post_holdout_bundle_fails_without_second_open(
    tmp_path: Path,
) -> None:
    coordinator, factory = finalist_campaign(tmp_path)
    finalist = coordinator.finalist_contract()
    consumed = coordinator.run_holdout(
        finalist, lambda candidate, *_args: evaluation(candidate, Stage.HOLDOUT, 1)
    )
    assert consumed["state"] == "holdout_consumed"
    assert consumed["holdout_result"]["evaluation"]["eligible"] is True
    failed = coordinator.fail_post_holdout(
        "modeled result persisted before atomic post-holdout completion"
    )
    assert failed["state"] == "failed"
    assert factory.calls.count("holdout") == 1
    with pytest.raises(HoldoutConsumedError):
        coordinator.run_holdout(finalist, lambda *_args: pytest.fail("must not reopen"))
    assert factory.calls.count("holdout") == 1


def test_campaign_contract_hash_binds_canonical_research_scope() -> None:
    frozen = contract()
    frozen.validate()
    assert frozen.research_scope == {
        "program_id": "spy-price-v3", "symbol": "SPY", "feature_families": [],
    }
    assert len(frozen.research_scope_hash) == 64
    with pytest.raises(ValueError, match="canonical spy-price-v3 scope"):
        replace(frozen, research_scope={
            "program_id": "palantir", "symbol": "PLTR", "feature_families": [],
        }).validate()
    with pytest.raises(ValueError, match="research_scope_hash"):
        replace(frozen, research_scope_hash=digest("wrong-scope")).validate()


def test_campaign_initialized_event_replays_scope_and_scope_hash(tmp_path: Path) -> None:
    frozen = contract()
    CampaignCoordinator(tmp_path, frozen, AccessFactory())
    initialized = json.loads(
        (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    durable = initialized["payload"]["contract"]
    assert durable["research_scope"] == frozen.research_scope
    assert durable["research_scope_hash"] == frozen.research_scope_hash
    reopened = CampaignCoordinator(tmp_path, frozen, AccessFactory()).state
    assert reopened["contract"]["research_scope_hash"] == frozen.research_scope_hash


def test_ledger_rejects_structurally_valid_earlier_event_tampering(tmp_path: Path) -> None:
    coordinator, _ = ready_campaign(tmp_path)
    ledger = tmp_path / "events.jsonl"
    events = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    events[0]["payload"]["contract"]["campaign_id"] = "tampered"
    ledger.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
    with pytest.raises(CampaignError, match="integrity hash"):
        CampaignCoordinator(tmp_path, contract(), AccessFactory())
