"""Crash-safe staged campaign coordinator for schema-v3 research.

The append-only event ledger is authoritative. ``state.json`` is only a
materialized view and is rebuilt from the ledger on every open. Trusted
evaluation callbacks receive one stage capability and never a root path.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research.v3.evaluator import Evaluation, Stage
from research.v3.hypothesis import (
    DiversityDescriptor,
    canonical_hypothesis,
    describe_hypothesis,
    family_is_admissible,
    hypothesis_hash,
    is_near_duplicate,
    parse_hypothesis,
)
from research.v3.opra import (
    DecisionManifest,
    OpraBlock,
    OpraBoundaryError,
    QuoteArtifact,
    evaluate_opra_block,
)
from research.v3.stage_store import StageAccess, StageName

CAMPAIGN_SCHEMA_VERSION = 3
CANONICAL_RESEARCH_SCOPE: dict[str, object] = {
    "program_id": "spy-price-v3",
    "symbol": "SPY",
    "feature_families": [],
}

EVENT_PREDECESSORS: dict[str, set[str | None]] = {
    "campaign_initialized": {None},
    "discovery_started": {"ready"},
    "discovery_attempted": {"discovery"},
    "discovery_result": {"discovery"},
    "shortlist_frozen": {"discovery"},
    "no_discovery_survivor": {"discovery"},
    "validation_started": {"shortlist_frozen"},
    "validation_result": {"validation"},
    "validation_completed": {"validation"},
    "no_validation_survivor": {"validation"},
    "holdout_prepared": {"validation_complete"},
    "holdout_receipt": {"holdout_ready"},
    "holdout_result": {"holdout_consumed"},
    "holdout_failed": {"holdout_consumed"},
    "post_holdout_recorded": {"holdout_consumed"},
    "post_holdout_failed": {"holdout_consumed"},
    "campaign_failed": {
        "ready", "discovery", "shortlist_frozen", "validation", "validation_complete",
        "holdout_ready",
    },
}


class CampaignError(RuntimeError):
    pass


class InvalidTransition(CampaignError):
    pass


class FrozenContractMismatch(CampaignError):
    pass


class HoldoutConsumedError(CampaignError):
    pass


class CampaignCallbackError(CampaignError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _is_hash(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value.lower())


@dataclass(frozen=True)
class CampaignContract:
    campaign_id: str
    policy_hash: str
    evaluator_version: str
    stage_manifest_hashes: Mapping[str, str]
    mission_hash: str
    discovery_budget: int
    required_family_coverage_hash: str = hashlib.sha256(b"[]").hexdigest()
    required_families: tuple[str, ...] = ()
    required_hypothesis_families: tuple[str, ...] = ()
    family_quota: int = 2
    research_scope: Mapping[str, object] = field(
        default_factory=lambda: dict(CANONICAL_RESEARCH_SCOPE)
    )
    research_scope_hash: str = hashlib.sha256(
        json.dumps(
            CANONICAL_RESEARCH_SCOPE, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()

    def validate(self) -> None:
        if not self.campaign_id or not self.evaluator_version:
            raise ValueError("campaign and evaluator identifiers are required")
        if not _is_hash(self.policy_hash):
            raise ValueError("policy_hash must be a SHA-256 hash")
        if not _is_hash(self.mission_hash):
            raise ValueError("mission_hash must be a SHA-256 hash")
        if not _is_hash(self.required_family_coverage_hash):
            raise ValueError("required_family_coverage_hash must be a SHA-256 hash")
        if dict(self.research_scope) != CANONICAL_RESEARCH_SCOPE:
            raise ValueError("research_scope must be the canonical spy-price-v3 scope")
        if (not _is_hash(self.research_scope_hash)
                or self.research_scope_hash != _sha256(self.research_scope)):
            raise ValueError("research_scope_hash must bind the canonical research scope")
        if set(self.stage_manifest_hashes) != {stage.value for stage in Stage}:
            raise ValueError("all three frozen stage manifest hashes are required")
        if not all(_is_hash(value) for value in self.stage_manifest_hashes.values()):
            raise ValueError("stage manifest hashes must be SHA-256 hashes")
        if self.discovery_budget <= 0 or self.family_quota <= 0:
            raise ValueError("discovery budget and family quota must be positive")
        if tuple(sorted(set(self.required_families))) != self.required_families:
            raise ValueError("required_families must be sorted and unique")
        if (tuple(sorted(set(self.required_hypothesis_families)))
                != self.required_hypothesis_families
                or any(not family.strip() for family in self.required_hypothesis_families)):
            raise ValueError("required_hypothesis_families must be sorted, unique, and non-empty")

    @property
    def contract_hash(self) -> str:
        self.validate()
        return _sha256(asdict(self))


@dataclass(frozen=True)
class FrozenCandidate:
    canonical_document: str
    semantic_hash: str
    source_hash: str
    policy_hash: str
    evaluator_version: str
    stage_manifest_hashes: Mapping[str, str]
    descriptor: Mapping[str, Any]
    research_scope_hash: str = hashlib.sha256(
        json.dumps(
            CANONICAL_RESEARCH_SCOPE, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()

    def validate(self) -> None:
        if not _is_hash(self.semantic_hash) or not _is_hash(self.source_hash):
            raise FrozenContractMismatch("candidate hashes are malformed")
        if not _is_hash(self.research_scope_hash):
            raise FrozenContractMismatch("candidate research scope hash is malformed")
        try:
            document = json.loads(self.canonical_document)
        except (TypeError, json.JSONDecodeError) as exc:
            raise FrozenContractMismatch("candidate source is not canonical JSON") from exc
        if canonical_hypothesis(parse_hypothesis(document)) != self.canonical_document:
            raise FrozenContractMismatch("candidate source is not canonical")
        if hypothesis_hash(document) != self.semantic_hash:
            raise FrozenContractMismatch("candidate semantic hash changed")


_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _thread_lock(key: str) -> threading.RLock:
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _interprocess_lock(path: Path) -> Iterator[None]:
    """Serialize threads and processes using native Windows/POSIX file locks."""
    with _thread_lock(str(path.resolve())):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                while True:
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        time.sleep(0.025)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _evaluation_record(
    evaluation: Evaluation, expected_stage: Stage, contract: CampaignContract,
) -> dict[str, Any]:
    if not isinstance(evaluation, Evaluation) or evaluation.stage != expected_stage:
        raise ValueError(f"callback must return a {expected_stage.value} Evaluation")
    if evaluation.evaluator_version != contract.evaluator_version:
        raise FrozenContractMismatch("evaluation version differs from the frozen contract")
    if (evaluation.stage_manifest_hash
            != contract.stage_manifest_hashes[expected_stage.value]):
        raise FrozenContractMismatch(
            f"{expected_stage.value} evaluation manifest differs from the frozen contract"
        )
    if evaluation.eligible and not math.isfinite(evaluation.ranking_score_pp):
        raise ValueError("eligible evaluations require a finite ranking score")
    if evaluation.eligible:
        if (evaluation.control is None
                or not math.isfinite(evaluation.control_relative_edge_pp)):
            raise ValueError(
                "eligible evaluations require typed aggregate control evidence"
            )
        for year, metrics in evaluation.by_year.items():
            if metrics.control is None or not math.isfinite(metrics.control_relative_edge_pp):
                raise ValueError(
                    f"eligible evaluations require typed control evidence for {year}"
                )
    record = _jsonable(evaluation)
    return record


def _failed_evaluation(stage: Stage, exc: BaseException) -> dict[str, Any]:
    return {
        "eligible": False,
        "stage": stage.value,
        "ranking_score_pp": None,
        "failures": [f"{type(exc).__name__}: {exc}"],
    }


def _candidate_record(document: Mapping[str, Any], source_hash: str,
                      contract: CampaignContract) -> dict[str, Any]:
    if not _is_hash(source_hash):
        raise ValueError("source_hash must be a SHA-256 hash")
    hypothesis = parse_hypothesis(document)
    descriptor = describe_hypothesis(hypothesis)
    candidate = FrozenCandidate(
        canonical_document=canonical_hypothesis(hypothesis),
        semantic_hash=hypothesis_hash(hypothesis),
        source_hash=source_hash.lower(),
        policy_hash=contract.policy_hash,
        evaluator_version=contract.evaluator_version,
        stage_manifest_hashes=dict(contract.stage_manifest_hashes),
        descriptor=_jsonable(descriptor),
        research_scope_hash=contract.research_scope_hash,
    )
    candidate.validate()
    return _jsonable(candidate)


def _descriptor(candidate: Mapping[str, Any]) -> DiversityDescriptor:
    raw = dict(candidate["descriptor"])
    raw["observation_minutes"] = tuple(raw["observation_minutes"])
    raw["thresholds"] = tuple(raw["thresholds"])
    return DiversityDescriptor(**raw)


def _cluster_id(candidate: Mapping[str, Any]) -> str:
    descriptor = candidate["descriptor"]
    return _sha256({
        "family": descriptor["family"],
        "entry_bucket": descriptor["entry_bucket"],
        "observation_minutes": descriptor["observation_minutes"],
        "structure_helper": descriptor["structure_helper"],
    })


def _shortlist_hash(contract_hash: str, candidates: Sequence[Mapping[str, Any]]) -> str:
    return _sha256({"contract_hash": contract_hash, "candidates": candidates})


def _initial_state() -> dict[str, Any]:
    return {
        "campaign_schema_version": CAMPAIGN_SCHEMA_VERSION,
        "state": None,
        "contract": None,
        "contract_hash": None,
        "discovery_attempts": [],
        "scientific_trials": 0,
        "shortlist": [],
        "shortlist_hash": None,
        "validation_results": {},
        "finalist": None,
        "holdout_receipt": None,
        "holdout_result": None,
        "opra_result": None,
        "finding": None,
        "campaign_failure": None,
        "event_head_hash": None,
        "last_sequence": 0,
        "applied_idempotency_keys": [],
    }


def _apply_event(state: dict[str, Any], event: Mapping[str, Any]) -> None:
    event_type = event["type"]
    payload = event["payload"]
    if event_type not in EVENT_PREDECESSORS:
        raise CampaignError(f"unknown campaign event: {event_type}")
    if state["state"] not in EVENT_PREDECESSORS[event_type]:
        raise CampaignError(
            f"event {event_type} is invalid after durable state {state['state']}"
        )
    if event_type == "campaign_initialized":
        state["state"] = "ready"
        state["contract"] = payload["contract"]
        state["contract_hash"] = payload["contract_hash"]
    elif event_type == "discovery_started":
        state["state"] = "discovery"
    elif event_type == "discovery_attempted":
        state["discovery_attempts"].append(payload)
        if payload["admitted"]:
            state["scientific_trials"] += 1
    elif event_type == "discovery_result":
        attempt = next(
            (item for item in state["discovery_attempts"]
             if item["attempt_id"] == payload["attempt_id"]),
            None,
        )
        if attempt is None or not attempt["admitted"] or attempt.get("evaluation") is not None:
            raise CampaignError("discovery result has no unique pending admission")
        attempt["evaluation"] = payload["evaluation"]
    elif event_type == "shortlist_frozen":
        state["state"] = "shortlist_frozen"
        state["shortlist"] = payload["shortlist"]
        state["shortlist_hash"] = payload["shortlist_hash"]
    elif event_type == "no_discovery_survivor":
        state["state"] = "no_discovery_survivor"
    elif event_type == "validation_started":
        if (payload["shortlist_hash"] != state["shortlist_hash"]
                or payload["shortlist_size"] != len(state["shortlist"])):
            raise CampaignError("validation start does not match the frozen shortlist")
        state["state"] = "validation"
    elif event_type == "validation_result":
        shortlisted = {item["semantic_hash"] for item in state["shortlist"]}
        if (payload["shortlist_hash"] != state["shortlist_hash"]
                or payload["shortlist_size"] != len(state["shortlist"])
                or payload["semantic_hash"] not in shortlisted
                or payload["semantic_hash"] in state["validation_results"]):
            raise CampaignError("validation result violates the frozen shortlist")
        state["validation_results"][payload["semantic_hash"]] = payload
    elif event_type == "validation_completed":
        if payload["finalist"] not in state["shortlist"]:
            raise CampaignError("validation finalist was not shortlisted")
        state["state"] = "validation_complete"
        state["finalist"] = payload["finalist"]
    elif event_type == "holdout_prepared":
        if payload["semantic_hash"] != state["finalist"]["semantic_hash"]:
            raise CampaignError("holdout preparation does not match the finalist")
        state["state"] = "holdout_ready"
    elif event_type == "no_validation_survivor":
        state["state"] = "no_validation_survivor"
    elif event_type == "holdout_receipt":
        if (payload["semantic_hash"] != state["finalist"]["semantic_hash"]
                or payload["contract_hash"] != state["contract_hash"]):
            raise CampaignError("holdout receipt violates the frozen contract")
        state["state"] = "holdout_consumed"
        state["holdout_receipt"] = payload
    elif event_type in {"holdout_result", "holdout_failed"}:
        if payload["receipt_id"] != state["holdout_receipt"]["receipt_id"]:
            raise CampaignError("holdout result does not match its receipt")
        if state["holdout_result"] is not None:
            raise CampaignError("holdout already has a terminal result")
        state["holdout_result"] = payload
    elif event_type == "post_holdout_recorded":
        if state["holdout_result"] is None:
            raise CampaignError("post-holdout lifecycle cannot precede modeled holdout evidence")
        if payload["receipt_id"] != state["holdout_receipt"]["receipt_id"]:
            raise CampaignError("post-holdout bundle does not match the holdout receipt")
        state["state"] = "finding_ready"
        state["opra_result"] = {
            "receipt_id": payload["receipt_id"],
            "validation": payload["validation"],
            "holdout": payload["holdout"],
            "scientific_outcome": payload["scientific_outcome"],
        }
        state["finding"] = {
            "receipt_id": payload["receipt_id"], "finding": payload["finding"]
        }
    elif event_type == "post_holdout_failed":
        if (payload["receipt_id"] != state["holdout_receipt"]["receipt_id"]
                or not isinstance(payload.get("reason"), str)
                or not payload["reason"].strip()):
            raise CampaignError("post-holdout failure requires its receipt and a reason")
        state["state"] = "failed"
        state["campaign_failure"] = payload
    elif event_type == "campaign_failed":
        if not isinstance(payload.get("reason"), str) or not payload["reason"].strip():
            raise CampaignError("campaign failure requires a durable reason")
        state["state"] = "failed"
        state["campaign_failure"] = payload
    state["last_sequence"] = event["sequence"]
    state["event_head_hash"] = event["event_hash"]
    state["applied_idempotency_keys"].append(event["idempotency_key"])


class CampaignCoordinator:
    """Own one v3 campaign event ledger and its trusted stage callbacks."""

    def __init__(self, root: Path, contract: CampaignContract,
                 stage_access_factory: Callable[[StageName], StageAccess]):
        contract.validate()
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.contract = contract
        self._stage_access_factory = stage_access_factory
        self._ledger_path = self.root / "events.jsonl"
        self._state_path = self.root / "state.json"
        self._lock_path = self.root / "campaign.lock"
        with _interprocess_lock(self._lock_path):
            state, events = self._load_replayed()
            if not events:
                self._append_event(
                    state,
                    "campaign_initialized",
                    {"contract": _jsonable(contract), "contract_hash": contract.contract_hash},
                    "campaign-initialized",
                    allowed={None},
                )
            elif (state["contract_hash"] != contract.contract_hash
                  or state["contract"] != _jsonable(contract)):
                raise FrozenContractMismatch("campaign contract differs from the durable contract")
            else:
                self._write_materialized(state)

    @property
    def state(self) -> dict[str, Any]:
        with _interprocess_lock(self._lock_path):
            state, _ = self._load_replayed()
            self._write_materialized(state)
            return json.loads(json.dumps(state))

    def _load_replayed(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        state = _initial_state()
        events: list[dict[str, Any]] = []
        if self._ledger_path.exists():
            for line_number, line in enumerate(self._ledger_path.read_bytes().splitlines(), 1):
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise CampaignError(f"invalid event ledger line {line_number}") from exc
                if event.get("sequence") != line_number:
                    raise CampaignError("event ledger sequence is not contiguous")
                if event.get("previous_event_hash") != state["event_head_hash"]:
                    raise CampaignError("event ledger hash chain is broken")
                claimed_hash = event.get("event_hash")
                hash_payload = dict(event)
                hash_payload.pop("event_hash", None)
                if not _is_hash(claimed_hash) or claimed_hash != _sha256(hash_payload):
                    raise CampaignError("event ledger integrity hash mismatch")
                if event.get("idempotency_key") in state["applied_idempotency_keys"]:
                    raise CampaignError("duplicate idempotency key in event ledger")
                _apply_event(state, event)
                events.append(event)
        return state, events

    def _write_materialized(self, state: Mapping[str, Any]) -> None:
        payload = _canonical(state) + b"\n"
        # `state` is a READ property: it replays the ledger and materializes
        # on every access, including those /api/health makes via
        # ResearchSessionStore.list(). Replaying a settled campaign reproduces
        # bytes identical to what is already on disk, so the fsync +
        # os.replace is pure cost -- and it is paid while holding the campaign
        # interprocess lock, which is precisely what listing queues behind
        # (measured 2026-08-22: health latency 84-170s, dominated by lock
        # waits). Comparing first is observably equivalent; the file ends up
        # with the same bytes either way.
        try:
            if self._state_path.read_bytes() == payload:
                return
        except OSError:
            pass  # missing or unreadable: fall through and write it properly
        temp = self.root / (
            f"state.json.tmp.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}"
        )
        try:
            with temp.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self._state_path)
        finally:
            temp.unlink(missing_ok=True)
        if os.name != "nt":
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)

    def _append_event(self, state: dict[str, Any], event_type: str,
                      payload: Mapping[str, Any], idempotency_key: str,
                      *, allowed: set[str | None]) -> dict[str, Any]:
        if idempotency_key in state["applied_idempotency_keys"]:
            return state
        if state["state"] not in allowed:
            raise InvalidTransition(f"{event_type} is not allowed from {state['state']}")
        event = {
            "sequence": state["last_sequence"] + 1,
            "idempotency_key": idempotency_key,
            "type": event_type,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "payload": _jsonable(payload),
            "previous_event_hash": state["event_head_hash"],
        }
        event["event_hash"] = _sha256(event)
        encoded = _canonical(event) + b"\n"
        with self._ledger_path.open("ab", buffering=0) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        _apply_event(state, event)
        self._write_materialized(state)
        return state

    def start_discovery(self, feature_coverage: Sequence[object] = ()) -> dict[str, Any]:
        from research.v3.features import (
            CoverageReport,
            proprietary_mission_ready,
            required_family_coverage_hash,
        )

        reports = tuple(feature_coverage)
        if any(not isinstance(report, CoverageReport) for report in reports):
            raise FrozenContractMismatch("feature coverage admission requires typed reports")
        if required_family_coverage_hash(reports) != self.contract.required_family_coverage_hash:
            raise FrozenContractMismatch("feature coverage differs from the frozen campaign contract")
        if not proprietary_mission_ready(self.contract.required_families, reports):
            raise InvalidTransition("required proprietary feature coverage is not ready")
        with _interprocess_lock(self._lock_path):
            state, _ = self._load_replayed()
            if state["state"] == "discovery":
                return state
            return self._append_event(
                state, "discovery_started", {}, "discovery-started", allowed={"ready"}
            )

    def submit_discovery(
        self,
        document: Mapping[str, Any],
        *,
        source_hash: str,
        attempt_id: str,
        evaluate: Callable[[FrozenCandidate, StageAccess], Evaluation],
    ) -> dict[str, Any]:
        if not attempt_id:
            raise ValueError("attempt_id is required")
        candidate = _candidate_record(document, source_hash, self.contract)
        descriptor = _descriptor(candidate)
        admission_key = f"discovery-admission:{attempt_id}"
        result_key = f"discovery-result:{attempt_id}"
        with _interprocess_lock(self._lock_path):
            state, _ = self._load_replayed()
            if state["state"] != "discovery":
                raise InvalidTransition(f"discovery attempt is not allowed from {state['state']}")
            prior = next(
                (item for item in state["discovery_attempts"] if item["attempt_id"] == attempt_id),
                None,
            )
            if prior is not None and (not prior["admitted"] or prior.get("evaluation") is not None):
                prior_semantic = prior.get(
                    "semantic_hash", prior.get("candidate", {}).get("semantic_hash")
                )
                prior_source = prior.get("source_hash", prior.get("candidate", {}).get("source_hash"))
                if (prior_semantic != candidate["semantic_hash"]
                        or prior_source != candidate["source_hash"]):
                    raise FrozenContractMismatch("attempt_id was reused for another hypothesis")
                return prior
            attempts = state["discovery_attempts"]
            admitted_candidates = [item["candidate"] for item in attempts if item["admitted"]]
            if prior is None:
                reason: str | None = None
                if state["scientific_trials"] >= self.contract.discovery_budget:
                    reason = "discovery budget is closed"
                elif any(item["semantic_hash"] == candidate["semantic_hash"] for item in admitted_candidates):
                    reason = "semantic duplicate"
                elif any(is_near_duplicate(descriptor, _descriptor(item)) for item in admitted_candidates):
                    reason = "near duplicate"
                elif not family_is_admissible(
                    descriptor,
                    [_descriptor(item) for item in admitted_candidates],
                    quota=self.contract.family_quota,
                    # This schedule is intentionally distinct from proprietary
                    # source coverage families (flow/GEX/dark-pool).
                    required_families=frozenset(
                        self.contract.required_hypothesis_families
                    ),
                ):
                    reason = "family balance gate"
            else:
                if (prior["candidate"]["semantic_hash"] != candidate["semantic_hash"]
                        or prior["candidate"]["source_hash"] != candidate["source_hash"]):
                    raise FrozenContractMismatch("attempt_id was reused for another hypothesis")
                reason = None
            if prior is None and reason is not None:
                payload = {
                    "attempt_id": attempt_id,
                    "admitted": False,
                    "reason": reason,
                    "semantic_hash": candidate["semantic_hash"],
                    "source_hash": candidate["source_hash"],
                }
                self._append_event(state, "discovery_attempted", payload, admission_key,
                                   allowed={"discovery"})
                return payload

            if prior is None:
                admission = {
                    "attempt_id": attempt_id,
                    "admitted": True,
                    "reason": None,
                    "candidate": candidate,
                    "evaluation": None,
                    "cluster_id": _cluster_id(candidate),
                }
                self._append_event(
                    state, "discovery_attempted", admission, admission_key,
                    allowed={"discovery"},
                )
            else:
                candidate = prior["candidate"]
        # The admission decision is durable at this point; run the multi-second
        # evaluation outside the interprocess lock so concurrent ledger readers
        # are not blocked, then append the result under a fresh locked replay.
        frozen = FrozenCandidate(**candidate)
        try:
            result = _evaluation_record(
                evaluate(frozen, self._stage_access_factory("discovery")),
                Stage.DISCOVERY,
                self.contract,
            )
        except Exception as exc:  # callback failures are durable scientific attempts
            result = _failed_evaluation(Stage.DISCOVERY, exc)
        with _interprocess_lock(self._lock_path):
            state, _ = self._load_replayed()
            state = self._append_event(
                state, "discovery_result",
                {"attempt_id": attempt_id, "evaluation": result}, result_key,
                allowed={"discovery"},
            )
            return next(
                item for item in state["discovery_attempts"] if item["attempt_id"] == attempt_id
            )

    def close_discovery(self) -> dict[str, Any]:
        with _interprocess_lock(self._lock_path):
            state, _ = self._load_replayed()
            if state["state"] in {"shortlist_frozen", "no_discovery_survivor"}:
                return state
            if state["state"] != "discovery":
                raise InvalidTransition(f"discovery cannot close from {state['state']}")
            if state["scientific_trials"] < self.contract.discovery_budget:
                raise InvalidTransition("discovery budget has not closed")
            if any(item["admitted"] and item.get("evaluation") is None
                   for item in state["discovery_attempts"]):
                raise InvalidTransition("discovery has an interrupted pending evaluation")
            survivors = [
                item for item in state["discovery_attempts"]
                if item["admitted"] and item["evaluation"].get("eligible")
                and isinstance(item["evaluation"].get("ranking_score_pp"), (int, float))
                and math.isfinite(item["evaluation"]["ranking_score_pp"])
            ]
            if not survivors:
                return self._append_event(
                    state, "no_discovery_survivor", {}, "discovery-no-survivor",
                    allowed={"discovery"},
                )
            ordered = sorted(
                survivors,
                key=lambda item: (-item["evaluation"]["ranking_score_pp"],
                                  item["candidate"]["semantic_hash"]),
            )
            chosen: list[dict[str, Any]] = []
            seen_clusters: set[str] = set()
            for item in ordered:
                if item["cluster_id"] not in seen_clusters:
                    chosen.append(item["candidate"])
                    seen_clusters.add(item["cluster_id"])
                if len(chosen) == 3:
                    break
            if len(chosen) < 3:
                chosen_hashes = {item["semantic_hash"] for item in chosen}
                for item in ordered:
                    candidate = item["candidate"]
                    if candidate["semantic_hash"] not in chosen_hashes:
                        chosen.append(candidate)
                        chosen_hashes.add(candidate["semantic_hash"])
                    if len(chosen) == 3:
                        break
            shortlist_hash = _shortlist_hash(self.contract.contract_hash, chosen)
            return self._append_event(
                state,
                "shortlist_frozen",
                {"shortlist": chosen, "shortlist_hash": shortlist_hash},
                f"shortlist-frozen:{shortlist_hash}",
                allowed={"discovery"},
            )

    def run_validation(
        self,
        evaluate: Callable[[FrozenCandidate, StageAccess, int], Evaluation],
    ) -> dict[str, Any]:
        with _interprocess_lock(self._lock_path):
            state, _ = self._load_replayed()
            if state["state"] in {"holdout_ready", "no_validation_survivor"}:
                return state
            if state["state"] in {"shortlist_frozen", "validation", "validation_complete"}:
                self._verify_shortlist(state)
            if state["state"] == "shortlist_frozen":
                state = self._append_event(
                    state,
                    "validation_started",
                    {"shortlist_hash": state["shortlist_hash"],
                     "shortlist_size": len(state["shortlist"])},
                    f"validation-started:{state['shortlist_hash']}",
                    allowed={"shortlist_frozen"},
                )
            if state["state"] not in {"validation", "validation_complete"}:
                raise InvalidTransition(f"validation is not allowed from {state['state']}")
        # Each evaluation runs outside the interprocess lock so concurrent
        # ledger readers are not blocked; the per-campaign evaluation lock
        # keeps concurrent validation calls from repeating an evaluation.
        evaluation_lock = _thread_lock(f"{self._lock_path.resolve()}:validation-evaluation")
        access: StageAccess | None = None
        while True:
            with evaluation_lock:
                with _interprocess_lock(self._lock_path):
                    state, _ = self._load_replayed()
                    if state["state"] != "validation":
                        break
                    candidate = next(
                        (item for item in state["shortlist"]
                         if item["semantic_hash"] not in state["validation_results"]),
                        None,
                    )
                    if candidate is None:
                        break
                    shortlist_hash = state["shortlist_hash"]
                    shortlist_size = len(state["shortlist"])
                if access is None:
                    access = self._stage_access_factory("validation")
                frozen = FrozenCandidate(**candidate)
                frozen.validate()
                try:
                    result = _evaluation_record(
                        evaluate(frozen, access, shortlist_size),
                        Stage.VALIDATION,
                        self.contract,
                    )
                except Exception as exc:
                    result = _failed_evaluation(Stage.VALIDATION, exc)
                with _interprocess_lock(self._lock_path):
                    state, _ = self._load_replayed()
                    if state["state"] == "validation":
                        self._append_event(
                            state,
                            "validation_result",
                            {"semantic_hash": candidate["semantic_hash"],
                             "evaluation": result,
                             "shortlist_hash": shortlist_hash,
                             "shortlist_size": shortlist_size},
                            f"validation-result:{shortlist_hash}:{candidate['semantic_hash']}",
                            allowed={"validation"},
                        )
        with _interprocess_lock(self._lock_path):
            state, _ = self._load_replayed()
            if state["state"] == "validation":
                survivors = []
                for candidate in state["shortlist"]:
                    result = state["validation_results"][candidate["semantic_hash"]]["evaluation"]
                    if (result.get("eligible")
                            and isinstance(result.get("ranking_score_pp"), (int, float))
                            and math.isfinite(result["ranking_score_pp"])):
                        survivors.append((candidate, result))
                if not survivors:
                    return self._append_event(
                        state, "no_validation_survivor", {},
                        f"validation-no-survivor:{state['shortlist_hash']}",
                        allowed={"validation"},
                    )
                finalist = min(
                    survivors,
                    key=lambda item: (-item[1]["ranking_score_pp"], item[0]["semantic_hash"]),
                )[0]
                state = self._append_event(
                    state, "validation_completed", {"finalist": finalist},
                    f"validation-complete:{state['shortlist_hash']}",
                    allowed={"validation"},
                )
            return self._append_event(
                state, "holdout_prepared", {"semantic_hash": state["finalist"]["semantic_hash"]},
                f"holdout-ready:{state['shortlist_hash']}", allowed={"validation_complete"},
            )

    def _verify_shortlist(self, state: Mapping[str, Any]) -> None:
        shortlist = state["shortlist"]
        if not 1 <= len(shortlist) <= 3:
            raise FrozenContractMismatch("frozen shortlist size is invalid")
        if state["shortlist_hash"] != _shortlist_hash(self.contract.contract_hash, shortlist):
            raise FrozenContractMismatch("frozen shortlist hash changed")
        semantic_hashes: set[str] = set()
        for raw in shortlist:
            candidate = FrozenCandidate(**raw)
            candidate.validate()
            if candidate.semantic_hash in semantic_hashes:
                raise FrozenContractMismatch("frozen shortlist contains a duplicate")
            semantic_hashes.add(candidate.semantic_hash)
            if (candidate.policy_hash != self.contract.policy_hash
                    or candidate.evaluator_version != self.contract.evaluator_version
                    or candidate.research_scope_hash != self.contract.research_scope_hash
                    or dict(candidate.stage_manifest_hashes)
                    != dict(self.contract.stage_manifest_hashes)):
                raise FrozenContractMismatch("frozen shortlist contract changed")

    def finalist_contract(self) -> FrozenCandidate:
        state = self.state
        if state["finalist"] is None:
            raise InvalidTransition("no frozen validation finalist exists")
        return FrozenCandidate(**state["finalist"])

    def _verify_finalist(self, supplied: FrozenCandidate, state: Mapping[str, Any]) -> None:
        supplied.validate()
        expected = FrozenCandidate(**state["finalist"])
        expected.validate()
        if _jsonable(supplied) != _jsonable(expected):
            raise FrozenContractMismatch("candidate or frozen research contract changed")
        if (supplied.policy_hash != self.contract.policy_hash
                or supplied.evaluator_version != self.contract.evaluator_version
                or supplied.research_scope_hash != self.contract.research_scope_hash
                or dict(supplied.stage_manifest_hashes) != dict(self.contract.stage_manifest_hashes)):
            raise FrozenContractMismatch("policy, evaluator, or data manifest hash changed")

    def run_holdout(
        self,
        supplied_finalist: FrozenCandidate,
        evaluate: Callable[[FrozenCandidate, StageAccess], Evaluation],
    ) -> dict[str, Any]:
        with _interprocess_lock(self._lock_path):
            state, _ = self._load_replayed()
            if state["holdout_receipt"] is not None or state["state"] == "holdout_consumed":
                raise HoldoutConsumedError("the final holdout has already been consumed")
            if state["state"] != "holdout_ready":
                raise InvalidTransition(f"holdout is not allowed from {state['state']}")
            self._verify_finalist(supplied_finalist, state)
            receipt_id = _sha256({
                "campaign": self.contract.contract_hash,
                "shortlist": state["shortlist_hash"],
                "candidate": supplied_finalist.semantic_hash,
            })
            state = self._append_event(
                state,
                "holdout_receipt",
                {"receipt_id": receipt_id, "semantic_hash": supplied_finalist.semantic_hash,
                 "contract_hash": self.contract.contract_hash,
                 "holdout_manifest_hash": self.contract.stage_manifest_hashes["holdout"]},
                f"holdout-receipt:{receipt_id}",
                allowed={"holdout_ready"},
            )

        # Resolve the sensitive holdout capability only after the receipt and
        # materialized consumed state have both reached durable storage.
        try:
            access = self._stage_access_factory("holdout")
            result = _evaluation_record(
                evaluate(supplied_finalist, access), Stage.HOLDOUT, self.contract
            )
        except Exception as exc:
            with _interprocess_lock(self._lock_path):
                state, _ = self._load_replayed()
                self._append_event(
                    state, "holdout_failed",
                    {"receipt_id": receipt_id, "error": f"{type(exc).__name__}: {exc}"},
                    f"holdout-failed:{receipt_id}", allowed={"holdout_consumed"},
                )
            raise CampaignCallbackError("holdout evaluation failed after durable consumption") from exc

        with _interprocess_lock(self._lock_path):
            state, _ = self._load_replayed()
            return self._append_event(
                state, "holdout_result", {"receipt_id": receipt_id, "evaluation": result},
                f"holdout-result:{receipt_id}", allowed={"holdout_consumed"},
            )

    def record_post_holdout(
        self,
        validation_decision: DecisionManifest,
        validation_quotes: QuoteArtifact,
        validation: OpraBlock,
        holdout_decision: DecisionManifest,
        holdout_quotes: QuoteArtifact,
        holdout: OpraBlock,
        finding: object,
    ) -> dict[str, Any]:
        """Validate and atomically record the complete typed post-holdout bundle."""
        from research.v3.findings import PortableFinding, validate_portable_finding

        if not isinstance(finding, PortableFinding):
            raise ValueError("post-holdout lifecycle requires a PortableFinding")
        with _interprocess_lock(self._lock_path):
            state, _ = self._load_replayed()
            if state["state"] not in {"holdout_consumed", "finding_ready"}:
                raise InvalidTransition(
                    f"post-holdout lifecycle is not allowed from {state['state']}"
                )
            if state["holdout_result"] is None or "evaluation" not in state["holdout_result"]:
                raise InvalidTransition(
                    "post-holdout lifecycle requires successful modeled holdout evidence"
                )
            receipt_id = state["holdout_receipt"]["receipt_id"]
            finalist = FrozenCandidate(**state["finalist"])
            finalist.validate()
            bundles = (
                ("validation", validation_decision, validation_quotes, validation, None),
                ("holdout", holdout_decision, holdout_quotes, holdout, receipt_id),
            )
            replayed: dict[str, OpraBlock] = {}
            try:
                for stage, decision, quotes, claimed, receipt in bundles:
                    if (not isinstance(decision, DecisionManifest)
                            or not isinstance(quotes, QuoteArtifact)
                            or not isinstance(claimed, OpraBlock)):
                        raise OpraBoundaryError(
                            "post-holdout evidence must use typed decision, quote, and OPRA artifacts"
                        )
                    if (decision.stage != stage
                            or decision.campaign_contract_hash != self.contract.contract_hash
                            or decision.candidate_hash != finalist.semantic_hash
                            or decision.stage_manifest_hash
                            != self.contract.stage_manifest_hashes[stage]
                            or decision.receipt_id != receipt):
                        raise OpraBoundaryError(
                            "OPRA decision provenance violates the frozen campaign"
                        )
                    actual = evaluate_opra_block(decision, quotes)
                    if actual != claimed:
                        raise OpraBoundaryError(
                            "OPRA aggregate claim differs from replayed evidence"
                        )
                    replayed[stage] = actual
            except OpraBoundaryError as exc:
                raise FrozenContractMismatch(f"invalid typed OPRA evidence: {exc}") from exc

            finding_payload = finding.to_dict()
            validate_portable_finding(finding_payload)
            discovery = next((
                attempt["evaluation"] for attempt in state["discovery_attempts"]
                if attempt.get("admitted") and attempt.get("candidate") == state["finalist"]
            ), None)
            validation_evaluation = (
                state["validation_results"].get(finalist.semantic_hash) or {}
            ).get("evaluation")
            expected_reference = (
                f"campaign://{self.contract.campaign_id}/candidates/{finalist.semantic_hash}"
            )
            expected_candidate = {
                "frozen_source_reference": expected_reference,
                "semantic_hash": finalist.semantic_hash,
                "source_hash": finalist.source_hash,
                "policy_hash": finalist.policy_hash,
                "evaluator_version": finalist.evaluator_version,
                "research_scope_hash": finalist.research_scope_hash,
                "data_manifest_hashes": dict(sorted(finalist.stage_manifest_hashes.items())),
            }
            evidence = finding_payload.get("evidence", {})
            if (finding_payload.get("candidate") != expected_candidate
                    or finding_payload.get("hypothesis_descriptor")
                    != _jsonable(finalist.descriptor)
                    or evidence.get("discovery") != discovery
                    or evidence.get("validation") != validation_evaluation
                    or evidence.get("holdout") != state["holdout_result"]["evaluation"]
                    or evidence.get("validation_opra") != _jsonable(replayed["validation"])
                    or evidence.get("holdout_opra") != _jsonable(replayed["holdout"])
                    or finding_payload["campaign"].get("campaign_id")
                    != self.contract.campaign_id
                    or finding_payload["campaign"].get("holdout_receipt_id") != receipt_id
                    or finding_payload["contracts"].get("campaign_contract_hash")
                    != self.contract.contract_hash):
                raise FrozenContractMismatch(
                    "portable finding differs from the exact durable campaign evidence"
                )
            payload = {
                "receipt_id": receipt_id,
                "validation": _jsonable(replayed["validation"]),
                "holdout": _jsonable(replayed["holdout"]),
                "scientific_outcome": replayed["holdout"].state.value,
                "finding": finding_payload,
            }
            digest = _sha256(payload)
            if state["state"] == "finding_ready":
                durable = {**state["opra_result"], "finding": state["finding"]["finding"]}
                if durable != payload:
                    raise FrozenContractMismatch(
                        "a different post-holdout bundle is already durable"
                    )
                return state
            return self._append_event(
                state, "post_holdout_recorded", payload,
                f"post-holdout-recorded:{receipt_id}:{digest}",
                allowed={"holdout_consumed"},
            )

    def fail_post_holdout(self, reason: str) -> dict[str, Any]:
        """Durably record integration failure after one-use holdout consumption."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("post-holdout failure reason is required")
        clean_reason = reason.strip()
        with _interprocess_lock(self._lock_path):
            state, _ = self._load_replayed()
            if state["state"] in {"finding_ready", "failed"}:
                return state
            if state["state"] != "holdout_consumed":
                raise InvalidTransition(
                    "post-holdout failure requires a durably consumed holdout receipt"
                )
            receipt_id = state["holdout_receipt"]["receipt_id"]
            payload = {"receipt_id": receipt_id, "reason": clean_reason}
            digest = _sha256(payload)
            return self._append_event(
                state, "post_holdout_failed", payload,
                f"post-holdout-failed:{receipt_id}:{digest}",
                allowed={"holdout_consumed"},
            )

    def fail(self, reason: str) -> dict[str, Any]:
        """Durably terminate an unconsumed campaign after an operational failure."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("campaign failure reason is required")
        clean_reason = reason.strip()
        with _interprocess_lock(self._lock_path):
            state, _ = self._load_replayed()
            if state["state"] == "failed":
                return state
            digest = _sha256({"reason": clean_reason})
            return self._append_event(
                state, "campaign_failed", {"reason": clean_reason},
                f"campaign-failed:{digest}",
                allowed={
                    "ready", "discovery", "shortlist_frozen", "validation",
                    "validation_complete", "holdout_ready",
                },
            )
