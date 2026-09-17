"""Deterministic, research-only portable findings for downstream review.

The finding boundary deliberately accepts only trusted v3 aggregate contracts.
Raw provider rows, generated source bodies, and credentials have no field in the
schema and are rejected recursively if a caller attempts to smuggle them into a
free-text or descriptor block.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping

from research.v3.campaign import CampaignContract, FrozenCandidate
from research.v3.evaluator import (
    BASE_COST_FRACTION,
    ELEVATED_COST_FRACTION,
    Evaluation,
    PriceSourceKind,
    Stage,
    bootstrap_seed,
)
from research.v3.features import (
    CoverageReport,
    CoverageState,
    proprietary_mission_ready,
    required_family_coverage_hash,
)
from research.v3.opra import (
    EntitlementStatus,
    OpraBlock,
    OpraBlockState,
    OpraBoundaryError,
    validate_opra_block,
)

FINDING_SCHEMA_VERSION = 1
REDISTRIBUTION_CLASS = "derived_aggregate_only"
AUTHORITY = "research_only"
NEXT_ALLOWED_ACTION = "paper_shadow_review"

_REQUIRED_STAGES = {stage.value for stage in Stage}
_FORBIDDEN_KEY_PARTS = {
    "api_key", "apikey", "authorization", "cookie", "credential", "password",
    "private_key", "provider_payload", "raw_payload", "raw_record", "raw_row",
    "secret", "token",
}
_FORBIDDEN_VALUE_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)\b(?:api[_ -]?key|access[_ -]?token|client[_ -]?secret)\s*[:=]\s*['\"]?[a-z0-9._~+/=-]{8,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:sk|rk|pk|ghp|github_pat)-?[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?<![A-Za-z0-9+.-])(?:[A-Za-z]:\\|/home/|/Users/|/root/|/etc/)[^\s'\"]+"),
    re.compile(r"(?i)['\"](?:provider_payload|raw_payload|authorization|api_key)['\"]\s*:"),
)


class FindingContractError(ValueError):
    """The proposed portable artifact violates the research boundary."""


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FindingContractError("finding must contain canonical finite JSON values") from exc


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _is_hash(value: object) -> bool:
    return (
        isinstance(value, str) and len(value) == 64
        and all(char in "0123456789abcdef" for char in value.lower())
    )


def _require_hash(value: object, name: str) -> str:
    if not _is_hash(value):
        raise FindingContractError(f"{name} must be a SHA-256 hash")
    return str(value).lower()


def _created_at(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise FindingContractError("created_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise FindingContractError("created_at must be UTC")
    return parsed.isoformat().replace("+00:00", "Z")


def _required_text_list(values: Iterable[str], name: str) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise FindingContractError(f"{name} must be a collection of statements")
    result = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise FindingContractError(f"{name} statements must be non-empty strings")
        result.append(value.strip())
    if not result:
        raise FindingContractError(f"{name} must contain at least one statement")
    return sorted(set(result))


def _scan_boundary(value: Any, path: str = "finding") -> None:
    """Reject secret-like keys and raw/provider material at every nesting level."""
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key).strip().lower().replace("-", "_")
            if (key in {"rows", "records", "provider", "payload"}
                    or any(part in key for part in _FORBIDDEN_KEY_PARTS)):
                raise FindingContractError(f"portable finding contains forbidden field at {path}.{key}")
            _scan_boundary(item, f"{path}.{key}")
    elif isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            _scan_boundary(item, f"{path}[{index}]")
    elif isinstance(value, str):
        if any(pattern.search(value) for pattern in _FORBIDDEN_VALUE_PATTERNS):
            raise FindingContractError(f"portable finding contains forbidden value at {path}")


def _evaluation_block(
    evaluation: Evaluation,
    stage: Stage,
    *,
    candidate_hash: str,
    evaluator_version: str,
    stage_manifest_hash: str,
) -> dict[str, Any]:
    if not isinstance(evaluation, Evaluation) or evaluation.stage != stage:
        raise FindingContractError(f"{stage.value} evidence has the wrong evaluator stage")
    required_metrics = (
        evaluation.absolute_full_win_edge_pp,
        evaluation.base.mean_pnl_pct,
        evaluation.base.lower_bound_pct,
        evaluation.elevated.mean_pnl_pct,
    )
    if (not evaluation.eligible or evaluation.failures
            or not math.isfinite(evaluation.ranking_score_pp)
            or not all(math.isfinite(value) and value > 0 for value in required_metrics)):
        raise FindingContractError(
            f"failed or non-positive {stage.value} evidence cannot produce a survivor finding"
        )
    record = _jsonable(evaluation)
    _canonical(record)
    _serialized_positive_evaluation(
        record,
        stage.value,
        candidate_hash=candidate_hash,
        evaluator_version=evaluator_version,
        stage_manifest_hash=stage_manifest_hash,
    )
    return record


def _opra_block(block: OpraBlock, *, holdout: bool) -> dict[str, Any]:
    if not isinstance(block, OpraBlock):
        raise FindingContractError("actual-quote evidence must be a typed OPRA block")
    try:
        validate_opra_block(block, holdout=holdout)
    except OpraBoundaryError as exc:
        raise FindingContractError(str(exc)) from exc
    if not isinstance(block.entitlement, EntitlementStatus):
        raise FindingContractError("OPRA entitlement state is malformed")
    _require_hash(block.decision_manifest_hash, "OPRA decision manifest hash")
    _require_hash(block.quote_artifact_hash, "OPRA quote artifact hash")
    if holdout and not _is_hash(block.receipt_id):
        raise FindingContractError("holdout OPRA evidence requires a receipt")
    if not holdout and block.receipt_id is not None:
        raise FindingContractError("validation OPRA evidence cannot carry a holdout receipt")
    if not 0 <= block.coverage <= 1:
        raise FindingContractError("OPRA coverage must be between zero and one")
    expected_coverage = block.priced_sessions / block.selected_sessions if block.selected_sessions else 0.0
    if abs(block.coverage - expected_coverage) > 1e-12:
        raise FindingContractError("OPRA coverage is inconsistent with selected sessions")
    if block.state == OpraBlockState.PASSED:
        metrics = (
            block.base_mean_pnl_pct, block.base_lower_bound_pct,
            block.elevated_mean_pnl_pct,
        )
        if (block.failures or not all(
                isinstance(value, (int, float)) and math.isfinite(value) and value > 0
                for value in metrics
        )):
            raise FindingContractError("passed OPRA evidence must contain positive finite gates")
        if block.entitlement != EntitlementStatus.AVAILABLE:
            raise FindingContractError("passed OPRA evidence requires available entitlement")
    elif not block.failures:
        raise FindingContractError("non-passing OPRA evidence must explain its typed failure")
    if (block.state == OpraBlockState.UNAVAILABLE) != (
            block.entitlement == EntitlementStatus.UNAVAILABLE):
        raise FindingContractError("OPRA availability state contradicts entitlement")
    if block.state == OpraBlockState.UNAVAILABLE and any(value is not None for value in (
            block.base_mean_pnl_pct, block.base_lower_bound_pct, block.elevated_mean_pnl_pct)):
        raise FindingContractError("unavailable OPRA evidence cannot contain economic metrics")
    if block.state == OpraBlockState.INSUFFICIENT_COVERAGE and (
            block.coverage >= .70 and block.priced_sessions >= 30 and block.effective_weeks >= 12):
        raise FindingContractError("insufficient OPRA coverage state contradicts its evidence")
    if block.state == OpraBlockState.FAILED_EVIDENCE:
        if block.coverage < .70 or block.priced_sessions < 30 or block.effective_weeks < 12:
            raise FindingContractError("failed OPRA economics require sufficient quote coverage")
        metrics = (block.base_mean_pnl_pct, block.base_lower_bound_pct,
                   block.elevated_mean_pnl_pct)
        if (not all(isinstance(value, (int, float)) and math.isfinite(value) for value in metrics)
                or all(value > 0 for value in metrics)):
            raise FindingContractError("failed OPRA evidence must contain a non-positive economic gate")
    record = _jsonable(block)
    _canonical(record)
    return record


def _coverage_blocks(required_families: tuple[str, ...],
                     reports: Iterable[CoverageReport]) -> tuple[list[dict[str, Any]], bool]:
    values = tuple(reports)
    keys: set[tuple[str, str]] = set()
    for report in values:
        if not isinstance(report, CoverageReport):
            raise FindingContractError("feature coverage must use typed aggregate reports")
        report.validate()
        key = (report.family, report.split)
        if key in keys:
            raise FindingContractError("feature coverage reports must be unique")
        keys.add(key)
    try:
        ready = proprietary_mission_ready(required_families, values)
    except ValueError as exc:
        raise FindingContractError(str(exc)) from exc
    records = sorted((_jsonable(report) for report in values), key=lambda item: (
        item["family"], item["split"]
    ))
    return records, ready


def _semantic_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result.pop("finding_id", None)
    result.pop("created_at", None)
    result.pop("integrity_hash", None)
    return result


def _serialized_counts(block: Any, name: str) -> None:
    if not isinstance(block, Mapping) or set(block) != {
        "selected_count", "priced_count", "coverage", "skip_reason_counts",
    }:
        raise FindingContractError(f"{name} ledger counts are malformed")
    selected = block.get("selected_count")
    priced = block.get("priced_count")
    coverage = block.get("coverage")
    reasons = block.get("skip_reason_counts")
    if (not isinstance(selected, int) or isinstance(selected, bool) or selected < 0
            or not isinstance(priced, int) or isinstance(priced, bool)
            or not 0 <= priced <= selected
            or not isinstance(coverage, (int, float)) or isinstance(coverage, bool)
            or not math.isfinite(coverage) or not 0 <= coverage <= 1
            or not isinstance(reasons, Mapping)):
        raise FindingContractError(f"{name} ledger counts are malformed")
    expected_coverage = priced / selected if selected else 0.0
    if abs(float(coverage) - expected_coverage) > 1e-12:
        raise FindingContractError(f"{name} coverage is inconsistent with ledger counts")
    reason_total = 0
    for reason, count in reasons.items():
        if (not isinstance(reason, str) or not reason.strip() or reason != reason.strip()
                or not isinstance(count, int) or isinstance(count, bool) or count <= 0):
            raise FindingContractError(f"{name} skip-reason counts are malformed")
        reason_total += count
    if reason_total != selected - priced:
        raise FindingContractError(f"{name} skip-reason counts are inconsistent")


def _serialized_scenario(block: Any, name: str, expected_cost: float) -> None:
    if not isinstance(block, Mapping) or set(block) != {
        "mean_pnl_pct", "lower_bound_pct", "cost_fraction",
    } or not all(
        isinstance(block.get(field), (int, float))
        and not isinstance(block.get(field), bool)
        and math.isfinite(block[field])
        for field in ("mean_pnl_pct", "lower_bound_pct", "cost_fraction")
    ) or block["cost_fraction"] != expected_cost:
        raise FindingContractError(f"{name} scenario metrics are malformed")


def _serialized_positive_evaluation(
    block: Any,
    stage: str,
    *,
    candidate_hash: str,
    evaluator_version: str,
    stage_manifest_hash: str,
) -> None:
    if not isinstance(block, Mapping) or block.get("stage") != stage:
        raise FindingContractError(f"{stage} modeled evidence is malformed")
    if set(block) != {
        "eligible", "failures", "stage", "trades", "effective_weeks", "alpha",
        "bootstrap_seed", "price_source_id", "price_source_kind", "evaluator_version",
        "stage_manifest_hash",
        "signal", "control", "absolute_full_win_edge_pp", "control_relative_edge_pp",
        "base", "elevated", "by_year", "ranking_score_pp",
    }:
        raise FindingContractError(f"{stage} modeled evidence is malformed")
    try:
        source_kind = PriceSourceKind(block.get("price_source_kind"))
    except (TypeError, ValueError) as exc:
        raise FindingContractError(f"{stage} modeled price-source kind is malformed") from exc
    price_source_id = block.get("price_source_id")
    if (block.get("evaluator_version") != evaluator_version
            or block.get("stage_manifest_hash") != stage_manifest_hash
            or not isinstance(price_source_id, str)
            or not price_source_id.strip()
            or not (price_source_id == source_kind.value
                    or price_source_id.startswith(f"{source_kind.value}:"))):
        raise FindingContractError(f"{stage} modeled provenance does not match its contract")
    _serialized_counts(block.get("signal"), f"{stage} signal")
    control = block.get("control")
    if control is None:
        raise FindingContractError(f"{stage} survivor evidence requires typed control counts")
    _serialized_counts(control, f"{stage} control")
    _serialized_scenario(block.get("base"), f"{stage} base", BASE_COST_FRACTION)
    _serialized_scenario(
        block.get("elevated"), f"{stage} elevated", ELEVATED_COST_FRACTION
    )
    trades = block.get("trades")
    weeks = block.get("effective_weeks")
    alpha = block.get("alpha")
    seed = block.get("bootstrap_seed")
    if (not isinstance(trades, int) or isinstance(trades, bool) or trades < 0
            or trades != block["signal"]["priced_count"]
            or not isinstance(weeks, int) or isinstance(weeks, bool) or not 0 <= weeks <= trades
            or not isinstance(alpha, (int, float)) or isinstance(alpha, bool)
            or not math.isfinite(alpha) or not 0 < alpha < 1
            or not isinstance(seed, int) or isinstance(seed, bool) or seed != bootstrap_seed(
                candidate_hash, stage_manifest_hash, block["price_source_id"], "base",
                evaluator_version=evaluator_version,
            )):
        raise FindingContractError(f"{stage} modeled counts or bootstrap provenance are malformed")
    ranking = block.get("ranking_score_pp")
    metrics = (
        block.get("absolute_full_win_edge_pp"),
        block.get("base", {}).get("mean_pnl_pct"),
        block.get("base", {}).get("lower_bound_pct"),
        block.get("elevated", {}).get("mean_pnl_pct"),
    )
    if (block.get("eligible") is not True or block.get("failures")
            or not isinstance(ranking, (int, float)) or isinstance(ranking, bool)
            or not math.isfinite(ranking)
            or not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                       and math.isfinite(value) and value > 0 for value in metrics)):
        raise FindingContractError("failed modeled evidence cannot be exported as a survivor")
    relative = block.get("control_relative_edge_pp")
    if (not isinstance(relative, (int, float)) or isinstance(relative, bool)
            or not math.isfinite(relative)):
        raise FindingContractError(f"{stage} control-relative diagnostic is malformed")
    by_year = block.get("by_year")
    if not isinstance(by_year, Mapping):
        raise FindingContractError(f"{stage} yearly modeled evidence is malformed")
    selected_total = priced_total = 0
    control_selected_total = control_priced_total = 0
    for year, item in by_year.items():
        if (not isinstance(year, str) or len(year) != 4 or not year.isdigit()
                or not isinstance(item, Mapping) or set(item) != {
                    "trades", "effective_weeks", "absolute_full_win_edge_pp",
                    "control_relative_edge_pp", "price_source_kind", "signal", "control",
                    "base", "elevated",
                }):
            raise FindingContractError(f"{stage} yearly modeled evidence is malformed")
        if item.get("price_source_kind") != source_kind.value:
            raise FindingContractError(f"{stage} {year} price-source kind is inconsistent")
        _serialized_counts(item.get("signal"), f"{stage} {year} signal")
        year_control = item.get("control")
        if year_control is None:
            raise FindingContractError(f"{stage} {year} survivor evidence requires control counts")
        _serialized_counts(year_control, f"{stage} {year} control")
        year_relative = item.get("control_relative_edge_pp")
        if (not isinstance(year_relative, (int, float)) or isinstance(year_relative, bool)
                or not math.isfinite(year_relative)):
            raise FindingContractError(f"{stage} {year} control-relative diagnostic is malformed")
        year_trades = item.get("trades")
        year_weeks = item.get("effective_weeks")
        year_edge = item.get("absolute_full_win_edge_pp")
        if (not isinstance(year_trades, int) or isinstance(year_trades, bool)
                or year_trades != item["signal"]["priced_count"]
                or not isinstance(year_weeks, int) or isinstance(year_weeks, bool)
                or not 0 <= year_weeks <= year_trades
                or not isinstance(year_edge, (int, float)) or isinstance(year_edge, bool)
                or not math.isfinite(year_edge)):
            raise FindingContractError(f"{stage} {year} modeled metrics are malformed")
        _serialized_scenario(
            item.get("base"), f"{stage} {year} base", BASE_COST_FRACTION
        )
        _serialized_scenario(
            item.get("elevated"), f"{stage} {year} elevated", ELEVATED_COST_FRACTION
        )
        selected_total += item["signal"]["selected_count"]
        priced_total += item["signal"]["priced_count"]
        control_selected_total += year_control["selected_count"]
        control_priced_total += year_control["priced_count"]
    if selected_total != block["signal"]["selected_count"] or priced_total != trades:
        raise FindingContractError(f"{stage} yearly signal counts do not reconcile")
    if (control_selected_total != control["selected_count"]
            or control_priced_total != control["priced_count"]):
        raise FindingContractError(f"{stage} yearly control counts do not reconcile")


def _serialized_opra(block: Any, *, holdout: bool) -> None:
    if not isinstance(block, Mapping):
        raise FindingContractError("OPRA evidence block is malformed")
    try:
        typed = OpraBlock(
            state=OpraBlockState(block["state"]),
            entitlement=EntitlementStatus(block["entitlement"]),
            selected_sessions=block["selected_sessions"],
            priced_sessions=block["priced_sessions"],
            coverage=block["coverage"],
            effective_weeks=block["effective_weeks"],
            base_mean_pnl_pct=block["base_mean_pnl_pct"],
            base_lower_bound_pct=block["base_lower_bound_pct"],
            elevated_mean_pnl_pct=block["elevated_mean_pnl_pct"],
            decision_manifest_hash=block["decision_manifest_hash"],
            quote_artifact_hash=block["quote_artifact_hash"],
            receipt_id=block["receipt_id"],
            failures=tuple(block["failures"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise FindingContractError("OPRA evidence block is malformed") from exc
    _opra_block(typed, holdout=holdout)


@dataclass(frozen=True)
class PortableFinding:
    """Validated portable JSON and its identity-independent integrity hash."""

    payload: Mapping[str, Any]

    @property
    def integrity_hash(self) -> str:
        return str(self.payload["integrity_hash"])

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self.to_json())

    def to_json(self) -> str:
        validate_portable_finding(self.payload)
        return _canonical(self.payload).decode("utf-8")


def validate_portable_finding(payload: Mapping[str, Any]) -> None:
    """Validate a deserialized finding without trusting its construction path."""
    if not isinstance(payload, Mapping):
        raise FindingContractError("portable finding must be an object")
    required = {
        "schema_version", "finding_id", "created_at", "campaign", "candidate",
        "contracts", "hypothesis_descriptor", "evidence", "lineage", "limitations",
        "invalidation_conditions", "paper_shadow_monitoring", "authority",
        "integrity_hash",
    }
    if set(payload) != required:
        raise FindingContractError("portable finding has missing or unexpected top-level fields")
    if payload["schema_version"] != FINDING_SCHEMA_VERSION:
        raise FindingContractError("unsupported portable finding schema")
    if not isinstance(payload["finding_id"], str) or not payload["finding_id"].strip():
        raise FindingContractError("finding_id is required")
    if payload["created_at"] != _created_at(payload["created_at"]):
        raise FindingContractError("created_at must use canonical UTC notation")
    _scan_boundary(payload)

    authority = payload["authority"]
    if authority != {
        "classification": AUTHORITY,
        "execution_authorized": False,
        "next_allowed_action": NEXT_ALLOWED_ACTION,
        "paper_shadow_review_eligible": authority.get("paper_shadow_review_eligible")
        if isinstance(authority, Mapping) else None,
    } or not isinstance(authority.get("paper_shadow_review_eligible"), bool):
        raise FindingContractError("finding authority is immutable and research-only")
    if payload["lineage"].get("redistribution_class") != REDISTRIBUTION_CLASS:
        raise FindingContractError("portable lineage must be derived aggregates only")
    if payload["campaign"].get("campaign_state") != "finding_ready":
        raise FindingContractError("portable survivor finding requires finding_ready state")
    if set(payload["campaign"]) != {"campaign_id", "campaign_state", "holdout_receipt_id"}:
        raise FindingContractError("campaign finding block is malformed")

    contracts = payload["contracts"]
    if set(contracts) != {
        "campaign_contract_hash", "frozen_campaign_contract", "mission_name", "mission_hash",
        "policy_hash", "evaluator_version", "evaluator_hash", "feature_schema_hash",
        "data_manifest_hashes", "research_scope", "research_scope_hash",
    }:
        raise FindingContractError("finding contract block is malformed")
    for name in ("campaign_contract_hash", "mission_hash", "policy_hash",
                 "evaluator_hash", "feature_schema_hash", "research_scope_hash"):
        _require_hash(contracts.get(name), name)
    manifests = contracts.get("data_manifest_hashes")
    if not isinstance(manifests, Mapping) or set(manifests) != _REQUIRED_STAGES:
        raise FindingContractError("all staged data manifest hashes are required")
    for stage, value in manifests.items():
        _require_hash(value, f"{stage} data manifest hash")
    frozen_contract = contracts.get("frozen_campaign_contract")
    if not isinstance(frozen_contract, Mapping):
        raise FindingContractError("the frozen campaign contract is required")
    try:
        frozen_values = dict(frozen_contract)
        frozen_values["required_families"] = tuple(frozen_values.get("required_families", ()))
        frozen_values["required_hypothesis_families"] = tuple(
            frozen_values.get("required_hypothesis_families", ())
        )
        reconstructed = CampaignContract(**frozen_values)
        reconstructed.validate()
    except (TypeError, ValueError) as exc:
        raise FindingContractError("the frozen campaign contract is invalid") from exc
    if (reconstructed.contract_hash != contracts["campaign_contract_hash"]
            or reconstructed.campaign_id != payload["campaign"].get("campaign_id")
            or reconstructed.policy_hash != contracts["policy_hash"]
            or reconstructed.evaluator_version != contracts.get("evaluator_version")
            or reconstructed.mission_hash != contracts.get("mission_hash")
            or dict(reconstructed.research_scope) != contracts.get("research_scope")
            or reconstructed.research_scope_hash != contracts.get("research_scope_hash")
            or dict(reconstructed.stage_manifest_hashes) != dict(manifests)):
        raise FindingContractError("campaign provenance hashes do not match the frozen contract")
    candidate = payload["candidate"]
    if set(candidate) != {
        "frozen_source_reference", "semantic_hash", "source_hash", "policy_hash",
        "evaluator_version", "data_manifest_hashes", "research_scope_hash",
    }:
        raise FindingContractError("candidate finding block is malformed")
    _require_hash(candidate.get("semantic_hash"), "candidate semantic hash")
    _require_hash(candidate.get("source_hash"), "candidate source hash")
    if not isinstance(candidate.get("frozen_source_reference"), str) or not candidate["frozen_source_reference"]:
        raise FindingContractError("a frozen candidate source reference is required")
    if (candidate.get("policy_hash") != contracts["policy_hash"]
            or candidate.get("evaluator_version") != contracts.get("evaluator_version")
            or candidate.get("research_scope_hash") != contracts.get("research_scope_hash")
            or candidate.get("data_manifest_hashes") != manifests):
        raise FindingContractError("candidate provenance does not match the frozen contract")
    evidence = payload["evidence"]
    if set(evidence) != {
        "discovery", "validation", "holdout", "validation_opra", "holdout_opra",
        "feature_coverage",
    }:
        raise FindingContractError("all modeled, OPRA, and coverage evidence blocks are required")
    for stage in _REQUIRED_STAGES:
        _serialized_positive_evaluation(
            evidence[stage],
            stage,
            candidate_hash=candidate["semantic_hash"],
            evaluator_version=contracts["evaluator_version"],
            stage_manifest_hash=manifests[stage],
        )
    holdout_receipt = payload["campaign"].get("holdout_receipt_id")
    _require_hash(holdout_receipt, "holdout receipt")
    if evidence["validation_opra"].get("receipt_id") is not None:
        raise FindingContractError("validation OPRA cannot use a holdout receipt")
    if evidence["holdout_opra"].get("receipt_id") != holdout_receipt:
        raise FindingContractError("holdout OPRA receipt does not match the campaign receipt")
    _serialized_opra(evidence["validation_opra"], holdout=False)
    _serialized_opra(evidence["holdout_opra"], holdout=True)

    aggregate_hashes = payload["lineage"].get("aggregate_hashes")
    if set(payload["lineage"]) != {"aggregate_hashes", "redistribution_class"}:
        raise FindingContractError("lineage finding block is malformed")
    if not isinstance(aggregate_hashes, Mapping):
        raise FindingContractError("aggregate lineage hashes must be an object")
    for name, value in aggregate_hashes.items():
        if not isinstance(name, str) or not name:
            raise FindingContractError("aggregate lineage names are required")
        _require_hash(value, f"aggregate lineage {name}")

    required_families = set(frozen_contract.get("required_families", ()))
    reports = evidence["feature_coverage"]
    if not isinstance(reports, list):
        raise FindingContractError("feature coverage must be a list")
    typed_reports = []
    for item in reports:
        try:
            report = CoverageReport(
                family=item["family"], split=item["split"], state=CoverageState(item["state"]),
                eligible_sessions=item["eligible_sessions"],
                covered_sessions=item["covered_sessions"], coverage_ratio=item["coverage_ratio"],
                covered_iso_weeks=item["covered_iso_weeks"],
                required_iso_weeks=item["required_iso_weeks"],
                source_available=item["source_available"],
            )
            report.validate()
            typed_reports.append(report)
        except (KeyError, TypeError, ValueError) as exc:
            raise FindingContractError("feature coverage evidence is malformed") from exc
    if required_family_coverage_hash(typed_reports) != reconstructed.required_family_coverage_hash:
        raise FindingContractError("feature coverage differs from frozen campaign admission")
    ready_keys = {
        (item.get("family"), item.get("split"))
        for item in reports if item.get("state") == CoverageState.READY.value
    }
    feature_ready = all(
        (family, stage) in ready_keys
        for family in required_families for stage in _REQUIRED_STAGES
    )
    derived_eligible = (
        evidence["validation"]["eligible"] is True
        and evidence["holdout"]["eligible"] is True
        and evidence["holdout_opra"].get("state") == OpraBlockState.PASSED.value
        and feature_ready
    )
    if authority["paper_shadow_review_eligible"] != derived_eligible:
        raise FindingContractError("paper-shadow eligibility is not derived from passed evidence")
    for field in ("limitations", "invalidation_conditions", "paper_shadow_monitoring"):
        if payload[field] != _required_text_list(payload[field], field):
            raise FindingContractError(f"{field} must be sorted, unique, and canonical")
    expected_hash = _hash(_semantic_payload(payload))
    if payload["integrity_hash"] != expected_hash:
        raise FindingContractError("portable finding integrity hash mismatch")


def build_portable_finding(
    *,
    finding_id: str,
    created_at: str,
    campaign_state: str,
    contract: CampaignContract,
    candidate: FrozenCandidate,
    frozen_source_reference: str,
    mission_name: str,
    mission_hash: str,
    evaluator_hash: str,
    feature_schema_hash: str,
    discovery: Evaluation,
    validation: Evaluation,
    holdout: Evaluation,
    validation_opra: OpraBlock,
    holdout_opra: OpraBlock,
    holdout_receipt_id: str,
    feature_coverage: Iterable[CoverageReport] = (),
    aggregate_lineage_hashes: Mapping[str, str] | None = None,
    limitations: Iterable[str] = (),
    invalidation_conditions: Iterable[str] = (),
    paper_shadow_monitoring: Iterable[str] = (),
) -> PortableFinding:
    """Build a deterministic v1 finding from trusted, frozen aggregate evidence."""
    contract.validate()
    candidate.validate()
    if campaign_state != "finding_ready":
        raise FindingContractError("portable survivor finding requires finding_ready state")
    if not finding_id.strip() or not mission_name.strip() or not frozen_source_reference.strip():
        raise FindingContractError("finding, mission, and frozen source identifiers are required")
    created = _created_at(created_at)
    mission_hash = _require_hash(mission_hash, "mission_hash")
    if mission_hash != contract.mission_hash:
        raise FindingContractError("mission hash does not match the frozen campaign contract")
    evaluator_hash = _require_hash(evaluator_hash, "evaluator_hash")
    feature_schema_hash = _require_hash(feature_schema_hash, "feature_schema_hash")
    receipt = _require_hash(holdout_receipt_id, "holdout_receipt_id")
    if (candidate.policy_hash != contract.policy_hash
            or candidate.evaluator_version != contract.evaluator_version
            or candidate.research_scope_hash != contract.research_scope_hash
            or dict(candidate.stage_manifest_hashes) != dict(contract.stage_manifest_hashes)):
        raise FindingContractError("candidate provenance does not match the frozen campaign contract")
    if holdout_opra.receipt_id != receipt:
        raise FindingContractError("holdout OPRA evidence does not match the durable receipt")

    modeled = {
        "discovery": _evaluation_block(
            discovery, Stage.DISCOVERY,
            candidate_hash=candidate.semantic_hash,
            evaluator_version=contract.evaluator_version,
            stage_manifest_hash=contract.stage_manifest_hashes[Stage.DISCOVERY.value],
        ),
        "validation": _evaluation_block(
            validation, Stage.VALIDATION,
            candidate_hash=candidate.semantic_hash,
            evaluator_version=contract.evaluator_version,
            stage_manifest_hash=contract.stage_manifest_hashes[Stage.VALIDATION.value],
        ),
        "holdout": _evaluation_block(
            holdout, Stage.HOLDOUT,
            candidate_hash=candidate.semantic_hash,
            evaluator_version=contract.evaluator_version,
            stage_manifest_hash=contract.stage_manifest_hashes[Stage.HOLDOUT.value],
        ),
    }
    validation_actual = _opra_block(validation_opra, holdout=False)
    holdout_actual = _opra_block(holdout_opra, holdout=True)
    typed_coverage = tuple(feature_coverage)
    coverage, feature_ready = _coverage_blocks(contract.required_families, typed_coverage)
    if required_family_coverage_hash(typed_coverage) != contract.required_family_coverage_hash:
        raise FindingContractError("feature coverage differs from frozen campaign admission")
    lineage = {}
    for name, value in sorted((aggregate_lineage_hashes or {}).items()):
        if not name or not isinstance(name, str):
            raise FindingContractError("aggregate lineage names must be non-empty strings")
        lineage[name] = _require_hash(value, f"aggregate lineage {name}")

    eligible = (
        feature_ready
        and validation.eligible and holdout.eligible
        and holdout_actual["state"] == OpraBlockState.PASSED.value
        and holdout_actual["receipt_id"] == receipt
    )
    payload: dict[str, Any] = {
        "schema_version": FINDING_SCHEMA_VERSION,
        "finding_id": finding_id,
        "created_at": created,
        "campaign": {
            "campaign_id": contract.campaign_id,
            "campaign_state": campaign_state,
            "holdout_receipt_id": receipt,
        },
        "candidate": {
            "frozen_source_reference": frozen_source_reference,
            "semantic_hash": candidate.semantic_hash,
            "source_hash": candidate.source_hash,
            "policy_hash": candidate.policy_hash,
            "evaluator_version": candidate.evaluator_version,
            "research_scope_hash": candidate.research_scope_hash,
            "data_manifest_hashes": dict(sorted(candidate.stage_manifest_hashes.items())),
        },
        "contracts": {
            "campaign_contract_hash": contract.contract_hash,
            "frozen_campaign_contract": _jsonable(contract),
            "mission_name": mission_name,
            "mission_hash": mission_hash,
            "policy_hash": contract.policy_hash,
            "evaluator_version": contract.evaluator_version,
            "evaluator_hash": evaluator_hash,
            "feature_schema_hash": feature_schema_hash,
            "research_scope": _jsonable(contract.research_scope),
            "research_scope_hash": contract.research_scope_hash,
            "data_manifest_hashes": dict(sorted(contract.stage_manifest_hashes.items())),
        },
        "hypothesis_descriptor": _jsonable(candidate.descriptor),
        "evidence": {
            **modeled,
            "validation_opra": validation_actual,
            "holdout_opra": holdout_actual,
            "feature_coverage": coverage,
        },
        "lineage": {
            "aggregate_hashes": lineage,
            "redistribution_class": REDISTRIBUTION_CLASS,
        },
        "limitations": _required_text_list(limitations, "limitations"),
        "invalidation_conditions": _required_text_list(
            invalidation_conditions, "invalidation_conditions"
        ),
        "paper_shadow_monitoring": _required_text_list(
            paper_shadow_monitoring, "paper_shadow_monitoring"
        ),
        "authority": {
            "classification": AUTHORITY,
            "execution_authorized": False,
            "next_allowed_action": NEXT_ALLOWED_ACTION,
            "paper_shadow_review_eligible": eligible,
        },
    }
    _scan_boundary(payload)
    payload["integrity_hash"] = _hash(_semantic_payload(payload))
    validate_portable_finding(payload)
    return PortableFinding(payload)
