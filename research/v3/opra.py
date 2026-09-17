"""Privileged OPRA collection and deterministic actual-price replay.

Decision manifests contain only frozen trade decisions and option legs. The
collector is the sole surface that sees a provider; replay and scoring consume
only immutable, hash-verified artifacts and cannot fall back to another feed.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timezone
from enum import StrEnum
from typing import Iterable, Protocol
from zoneinfo import ZoneInfo

from research.v3.evaluator import (
    BASE_COST_FRACTION,
    ELEVATED_COST_FRACTION,
    bootstrap_seed,
    weekly_cluster_lower_bound,
)
from research.v3.ledger import GrossTradeLedger, GrossTradeRow

OPRA_SCHEMA_VERSION = 1
REQUESTED_FEED = "opra"
VALIDATION_OPRA_START = date(2024, 2, 1)
VALIDATION_OPRA_END = date(2024, 12, 31)
HOLDOUT_START = date(2025, 1, 1)
ET = ZoneInfo("America/New_York")


class OpraBoundaryError(ValueError):
    pass


class EntitlementStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class OpraBlockState(StrEnum):
    UNAVAILABLE = "unavailable"
    INSUFFICIENT_COVERAGE = "insufficient_coverage"
    FAILED_EVIDENCE = "failed_evidence"
    PASSED = "passed"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _is_hash(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value.lower())


def _utc_timestamp(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise OpraBoundaryError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise OpraBoundaryError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _finite(value: float | None, name: str, *, nonnegative: bool = False) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OpraBoundaryError(f"{name} must be finite")
    result = float(value)
    if not math.isfinite(result) or (nonnegative and result < 0):
        raise OpraBoundaryError(f"{name} must be finite")
    return result


@dataclass(frozen=True)
class DecisionLeg:
    contract: str
    cp: str
    strike: float
    qty: int

    def validate(self) -> None:
        if not self.contract or self.cp not in {"C", "P"} or self.qty not in {-1, 1}:
            raise OpraBoundaryError("decision leg is malformed")
        if _finite(self.strike, "strike", nonnegative=True) <= 0:
            raise OpraBoundaryError("strike must be positive")

    def validate_for_day(self, day: str) -> None:
        self.validate()
        match = re.fullmatch(r"[A-Z]{1,6}(\d{6})([CP])(\d{8})", self.contract)
        if match is None:
            raise OpraBoundaryError("option contract is not an OCC symbol")
        if match.group(1) != date.fromisoformat(day).strftime("%y%m%d"):
            raise OpraBoundaryError("option contract expiration does not match the decision day")
        if match.group(2) != self.cp or int(match.group(3)) != round(self.strike * 1000):
            raise OpraBoundaryError("option contract terms do not match the decision leg")


@dataclass(frozen=True)
class DecisionRow:
    date: str
    eligible: bool
    selected: bool
    entry_minute: int
    structure: str
    width: float
    gross_settlement: float
    best_settlement: float
    worst_settlement: float
    underlying_adjustment_rule: str
    legs: tuple[DecisionLeg, ...]

    def validate(self) -> None:
        try:
            date.fromisoformat(self.date)
        except (TypeError, ValueError) as exc:
            raise OpraBoundaryError("decision date must be ISO-8601") from exc
        if not isinstance(self.eligible, bool) or not isinstance(self.selected, bool):
            raise OpraBoundaryError("decision flags must be Boolean")
        if self.selected and not self.eligible:
            raise OpraBoundaryError("an ineligible session cannot be selected")
        if not 9 * 60 + 30 <= self.entry_minute < 16 * 60:
            raise OpraBoundaryError("entry minute is outside regular trading hours")
        if not self.structure or self.underlying_adjustment_rule != "raw_unadjusted":
            raise OpraBoundaryError("actual option decisions require raw_unadjusted prices")
        width = _finite(self.width, "width", nonnegative=True)
        settlement = _finite(self.gross_settlement, "gross settlement")
        best = _finite(self.best_settlement, "best settlement")
        worst = _finite(self.worst_settlement, "worst settlement")
        if (width <= 0 or best <= worst or abs(width - (best - worst)) > 1e-9
                or not worst - 1e-9 <= settlement <= best + 1e-9):
            raise OpraBoundaryError("decision payoff range is invalid")
        if self.selected and not self.legs:
            raise OpraBoundaryError("selected decisions require option legs")
        if len({leg.contract for leg in self.legs}) != len(self.legs):
            raise OpraBoundaryError("decision contracts must be unique within a session")
        for leg in self.legs:
            leg.validate_for_day(self.date)


@dataclass(frozen=True)
class DecisionManifest:
    schema_version: int
    stage: str
    candidate_hash: str
    campaign_contract_hash: str
    stage_manifest_hash: str
    receipt_id: str | None
    requested_provider: str
    requested_feed: str
    created_at: str
    rows: tuple[DecisionRow, ...]
    content_hash: str

    @classmethod
    def create(
        cls,
        *,
        stage: str,
        candidate_hash: str,
        campaign_contract_hash: str,
        stage_manifest_hash: str,
        receipt_id: str | None,
        requested_provider: str,
        rows: Iterable[DecisionRow],
        created_at: str | None = None,
    ) -> "DecisionManifest":
        value = cls(
            OPRA_SCHEMA_VERSION,
            stage,
            candidate_hash,
            campaign_contract_hash,
            stage_manifest_hash,
            receipt_id,
            requested_provider,
            REQUESTED_FEED,
            created_at or datetime.now(timezone.utc).isoformat(),
            tuple(rows),
            "",
        )
        value = replace(value, content_hash=value.expected_hash())
        value.validate()
        return value

    def expected_hash(self) -> str:
        payload = asdict(self)
        payload.pop("content_hash")
        return _hash(payload)

    def validate(self) -> None:
        if self.schema_version != OPRA_SCHEMA_VERSION or self.requested_feed != REQUESTED_FEED:
            raise OpraBoundaryError("only schema-v1 requested OPRA manifests are supported")
        if not all(_is_hash(value) for value in (
            self.candidate_hash, self.campaign_contract_hash, self.stage_manifest_hash
        )):
            raise OpraBoundaryError("decision provenance hashes are malformed")
        if not self.requested_provider or not self.rows:
            raise OpraBoundaryError("decision provider and rows are required")
        _utc_timestamp(self.created_at, "created_at")
        if tuple(sorted(self.rows, key=lambda row: row.date)) != self.rows:
            raise OpraBoundaryError("decision rows must be chronological")
        if len({row.date for row in self.rows}) != len(self.rows):
            raise OpraBoundaryError("decision dates must be unique")
        for row in self.rows:
            row.validate()
            day = date.fromisoformat(row.date)
            if self.stage == "validation":
                if not VALIDATION_OPRA_START <= day <= VALIDATION_OPRA_END:
                    raise OpraBoundaryError("validation OPRA evidence is limited to Feb-Dec 2024")
            elif self.stage == "holdout":
                if day < HOLDOUT_START:
                    raise OpraBoundaryError("holdout OPRA evidence must be 2025 or later")
            else:
                raise OpraBoundaryError("OPRA evidence is only valid for validation or holdout")
        if self.stage == "validation" and self.receipt_id is not None:
            raise OpraBoundaryError("validation OPRA evidence cannot carry a holdout receipt")
        if self.stage == "holdout" and (self.receipt_id is None or not _is_hash(self.receipt_id)):
            raise OpraBoundaryError("holdout OPRA evidence requires its durable receipt")
        if self.content_hash != self.expected_hash():
            raise OpraBoundaryError("decision manifest integrity hash mismatch")


@dataclass(frozen=True)
class EntitlementResult:
    status: EntitlementStatus
    detail: str


@dataclass(frozen=True)
class ProviderPrice:
    feed: str
    kind: str
    event_time: str
    as_of_time: str
    bid: float | None = None
    ask: float | None = None
    value: float | None = None


class TrustedOpraProvider(Protocol):
    provider_id: str

    def check_entitlement(self, requested_feed: str) -> EntitlementResult: ...

    def fetch_leg(
        self, contract: str, day: date, entry_minute: int,
        requested_feed: str, tolerance_seconds: int,
    ) -> ProviderPrice | None: ...


@dataclass(frozen=True)
class LegEvidence:
    date: str
    provider: str
    feed: str
    contract: str
    quote_or_bar: str | None
    event_time: str | None
    as_of_time: str
    bid: float | None
    ask: float | None
    chosen_value: float | None
    tolerance_seconds: int
    underlying_adjustment_rule: str
    skip_reason: str | None

    def validate(self) -> None:
        date.fromisoformat(self.date)
        if not self.provider or self.feed != REQUESTED_FEED or not self.contract:
            raise OpraBoundaryError("leg evidence provenance is invalid")
        as_of = _utc_timestamp(self.as_of_time, "as_of_time")
        if not 0 <= self.tolerance_seconds <= 3600:
            raise OpraBoundaryError("selection tolerance is invalid")
        if self.underlying_adjustment_rule != "raw_unadjusted":
            raise OpraBoundaryError("leg evidence adjustment rule is invalid")
        if self.skip_reason is not None:
            if (self.chosen_value is not None or self.bid is not None or self.ask is not None
                    or self.quote_or_bar is not None or self.event_time is not None):
                raise OpraBoundaryError("skipped evidence cannot contain a price")
            return
        if self.quote_or_bar not in {"quote", "bar"} or self.event_time is None:
            raise OpraBoundaryError("priced evidence requires a quote or bar timestamp")
        event = _utc_timestamp(self.event_time, "event_time")
        if as_of < event:
            raise OpraBoundaryError("as-of time cannot precede the market event")
        chosen = _finite(self.chosen_value, "chosen value", nonnegative=True)
        if self.quote_or_bar == "quote":
            bid = _finite(self.bid, "bid", nonnegative=True)
            ask = _finite(self.ask, "ask", nonnegative=True)
            if bid > ask or not bid - 1e-12 <= chosen <= ask + 1e-12:
                raise OpraBoundaryError("quote price is outside the bid/ask")
        elif self.bid is not None or self.ask is not None:
            raise OpraBoundaryError("bar evidence cannot contain bid/ask")


@dataclass(frozen=True)
class QuoteArtifact:
    schema_version: int
    decision_manifest_hash: str
    candidate_hash: str
    campaign_contract_hash: str
    stage: str
    stage_manifest_hash: str
    receipt_id: str | None
    provider: str
    requested_feed: str
    entitlement: EntitlementResult
    created_at: str
    evidence: tuple[LegEvidence, ...]
    content_hash: str

    def expected_hash(self) -> str:
        payload = asdict(self)
        payload.pop("content_hash")
        return _hash(payload)

    def validate(self, decision: DecisionManifest) -> None:
        decision.validate()
        if self.schema_version != OPRA_SCHEMA_VERSION or self.requested_feed != REQUESTED_FEED:
            raise OpraBoundaryError("quote artifact is not requested OPRA evidence")
        relationship = (
            self.decision_manifest_hash == decision.content_hash
            and self.candidate_hash == decision.candidate_hash
            and self.campaign_contract_hash == decision.campaign_contract_hash
            and self.stage == decision.stage
            and self.stage_manifest_hash == decision.stage_manifest_hash
            and self.receipt_id == decision.receipt_id
            and self.provider == decision.requested_provider
        )
        if not relationship:
            raise OpraBoundaryError("quote artifact provenance does not match its decision manifest")
        _utc_timestamp(self.created_at, "created_at")
        required = {
            (row.date, leg.contract)
            for row in decision.rows if row.eligible and row.selected for leg in row.legs
        }
        actual = {(item.date, item.contract) for item in self.evidence}
        if len(actual) != len(self.evidence) or actual != required:
            raise OpraBoundaryError("quote artifact must contain exactly one item per selected leg")
        decision_legs = {
            (row.date, leg.contract): (row, leg)
            for row in decision.rows if row.eligible and row.selected for leg in row.legs
        }
        for item in self.evidence:
            item.validate()
            if item.provider != self.provider:
                raise OpraBoundaryError("leg provider does not match quote artifact")
            row, leg = decision_legs[(item.date, item.contract)]
            if item.underlying_adjustment_rule != row.underlying_adjustment_rule:
                raise OpraBoundaryError("leg adjustment rule changed after the decision")
            if item.skip_reason is None:
                event = _utc_timestamp(str(item.event_time), "event_time")
                target = datetime.combine(
                    date.fromisoformat(row.date),
                    time(row.entry_minute // 60, row.entry_minute % 60), ET,
                ).astimezone(timezone.utc)
                age = (target - event).total_seconds()
                if not 0 <= age <= item.tolerance_seconds:
                    raise OpraBoundaryError("leg evidence is outside the declared entry tolerance")
                if item.quote_or_bar == "quote":
                    executable = item.ask if leg.qty > 0 else item.bid
                    if item.chosen_value is None or executable is None:
                        raise OpraBoundaryError("quote evidence is missing an executable price")
                    if abs(item.chosen_value - executable) > 1e-12:
                        raise OpraBoundaryError("quote did not use the executable leg side")
        if self.entitlement.status == EntitlementStatus.UNAVAILABLE:
            if any(item.skip_reason is None for item in self.evidence):
                raise OpraBoundaryError("unavailable entitlement cannot contain OPRA prices")
        elif self.entitlement.status != EntitlementStatus.AVAILABLE:
            raise OpraBoundaryError("unknown entitlement status")
        if self.content_hash != self.expected_hash():
            raise OpraBoundaryError("quote artifact integrity hash mismatch")


def _skip_evidence(decision: DecisionManifest, row: DecisionRow, leg: DecisionLeg,
                   provider: str, tolerance_seconds: int, reason: str) -> LegEvidence:
    return LegEvidence(
        row.date, provider, REQUESTED_FEED, leg.contract, None, None,
        datetime.now(timezone.utc).isoformat(), None, None, None,
        tolerance_seconds, row.underlying_adjustment_rule, reason,
    )


def collect_opra(
    decision: DecisionManifest,
    provider: TrustedOpraProvider,
    *,
    tolerance_seconds: int = 15 * 60,
    created_at: str | None = None,
) -> QuoteArtifact:
    """Privileged collection: accepts frozen decisions, never hypothesis code."""
    decision.validate()
    if provider.provider_id != decision.requested_provider:
        raise OpraBoundaryError("provider does not match the requested provenance")
    if not 0 <= tolerance_seconds <= 3600:
        raise OpraBoundaryError("selection tolerance is invalid")
    entitlement = provider.check_entitlement(REQUESTED_FEED)
    if (not isinstance(entitlement, EntitlementResult)
            or not isinstance(entitlement.status, EntitlementStatus)):
        raise OpraBoundaryError("provider returned an invalid entitlement result")
    evidence: list[LegEvidence] = []
    for row in decision.rows:
        if not row.eligible or not row.selected:
            continue
        for leg in row.legs:
            if entitlement.status == EntitlementStatus.UNAVAILABLE:
                evidence.append(_skip_evidence(
                    decision, row, leg, provider.provider_id, tolerance_seconds,
                    f"entitlement_unavailable:{entitlement.detail}",
                ))
                continue
            try:
                raw = provider.fetch_leg(
                    leg.contract, date.fromisoformat(row.date), row.entry_minute,
                    REQUESTED_FEED, tolerance_seconds,
                )
            except Exception as exc:
                evidence.append(_skip_evidence(
                    decision, row, leg, provider.provider_id, tolerance_seconds,
                    f"provider_error:{type(exc).__name__}",
                ))
                continue
            if raw is None:
                evidence.append(_skip_evidence(
                    decision, row, leg, provider.provider_id, tolerance_seconds, "no_opra_price",
                ))
                continue
            if raw.feed != REQUESTED_FEED:
                raise OpraBoundaryError("requested OPRA cannot fall back to another feed")
            if raw.kind not in {"quote", "bar"}:
                raise OpraBoundaryError("provider returned an unsupported price type")
            event = _utc_timestamp(raw.event_time, "event_time")
            target = datetime.combine(
                date.fromisoformat(row.date), time(row.entry_minute // 60, row.entry_minute % 60), ET
            ).astimezone(timezone.utc)
            age = (target - event).total_seconds()
            if not 0 <= age <= tolerance_seconds:
                evidence.append(_skip_evidence(
                    decision, row, leg, provider.provider_id, tolerance_seconds,
                    "outside_selection_tolerance",
                ))
                continue
            if raw.kind == "quote":
                if raw.value is not None:
                    raise OpraBoundaryError("quote provider result cannot contain a bar value")
                bid = _finite(raw.bid, "bid", nonnegative=True)
                ask = _finite(raw.ask, "ask", nonnegative=True)
                if bid > ask:
                    raise OpraBoundaryError("provider returned a crossed quote")
                chosen = ask if leg.qty > 0 else bid
            else:
                if raw.bid is not None or raw.ask is not None:
                    raise OpraBoundaryError("bar provider result cannot contain bid/ask")
                bid = ask = None
                chosen = _finite(raw.value, "bar value", nonnegative=True)
            item = LegEvidence(
                row.date, provider.provider_id, REQUESTED_FEED, leg.contract, raw.kind,
                raw.event_time, raw.as_of_time, bid, ask, chosen, tolerance_seconds,
                row.underlying_adjustment_rule, None,
            )
            item.validate()
            evidence.append(item)
    artifact = QuoteArtifact(
        OPRA_SCHEMA_VERSION,
        decision.content_hash,
        decision.candidate_hash,
        decision.campaign_contract_hash,
        decision.stage,
        decision.stage_manifest_hash,
        decision.receipt_id,
        provider.provider_id,
        REQUESTED_FEED,
        entitlement,
        created_at or datetime.now(timezone.utc).isoformat(),
        tuple(evidence),
        "",
    )
    artifact = replace(artifact, content_hash=artifact.expected_hash())
    artifact.validate(decision)
    return artifact


def replay_opra(decision: DecisionManifest, artifact: QuoteArtifact) -> GrossTradeLedger:
    """Replay actual evidence without a provider or any fallback price source."""
    artifact.validate(decision)
    by_key = {(item.date, item.contract): item for item in artifact.evidence}
    ledger_rows: list[GrossTradeRow] = []
    for row in decision.rows:
        selected = row.eligible and row.selected
        if not selected:
            ledger_rows.append(GrossTradeRow(
                row.date, False, False, row.structure, None, None, None, None, None,
                None, "opra",
            ))
            continue
        items = [by_key[(row.date, leg.contract)] for leg in row.legs]
        missing = [item.skip_reason for item in items if item.skip_reason is not None]
        if missing:
            ledger_rows.append(GrossTradeRow(
                row.date, True, False, row.structure, None, None, None, None, None,
                None, "opra", skip_reason=";".join(sorted(set(missing))),
            ))
            continue
        chosen_prices: list[float] = []
        for item in items:
            if item.chosen_value is None:
                raise OpraBoundaryError("priced OPRA evidence is missing a chosen value")
            chosen_prices.append(item.chosen_value)
        debit = sum(leg.qty * price
                    for leg, price in zip(row.legs, chosen_prices, strict=True))
        if not -row.width < debit < row.width:
            ledger_rows.append(GrossTradeRow(
                row.date, True, False, row.structure, None, None, None, None, None,
                None, "opra", skip_reason="degenerate_opra_entry",
            ))
            continue
        ledger_rows.append(GrossTradeRow(
            row.date,
            True,
            True,
            row.structure,
            row.width,
            debit,
            row.gross_settlement,
            row.best_settlement,
            row.worst_settlement,
            row.gross_settlement - debit,
            "opra",
            tuple((leg.cp, leg.strike, leg.qty, price)
                  for leg, price in zip(row.legs, chosen_prices, strict=True)),
        ))
    return GrossTradeLedger(ledger_rows)


@dataclass(frozen=True)
class OpraBlock:
    state: OpraBlockState
    entitlement: EntitlementStatus
    selected_sessions: int
    priced_sessions: int
    coverage: float
    effective_weeks: int
    base_mean_pnl_pct: float | None
    base_lower_bound_pct: float | None
    elevated_mean_pnl_pct: float | None
    decision_manifest_hash: str
    quote_artifact_hash: str
    receipt_id: str | None
    failures: tuple[str, ...]


def validate_opra_block(block: OpraBlock, *, holdout: bool) -> None:
    """Fail closed on forged aggregate OPRA evidence before it crosses layers."""
    if not isinstance(block, OpraBlock):
        raise OpraBoundaryError("OPRA evidence must be a typed block")
    if not isinstance(block.state, OpraBlockState) or not isinstance(
        block.entitlement, EntitlementStatus
    ):
        raise OpraBoundaryError("OPRA block state or entitlement is malformed")
    for name, value in (("decision manifest", block.decision_manifest_hash),
                        ("quote artifact", block.quote_artifact_hash)):
        if not isinstance(value, str) or not _is_hash(value):
            raise OpraBoundaryError(f"{name} hash is malformed")
    if holdout:
        if not isinstance(block.receipt_id, str) or not _is_hash(block.receipt_id):
            raise OpraBoundaryError("holdout OPRA block requires a receipt hash")
    elif block.receipt_id is not None:
        raise OpraBoundaryError("validation OPRA block cannot carry a holdout receipt")
    counts = (block.selected_sessions, block.priced_sessions, block.effective_weeks)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts):
        raise OpraBoundaryError("OPRA block counts must be nonnegative integers")
    if block.priced_sessions > block.selected_sessions:
        raise OpraBoundaryError("priced OPRA sessions exceed selected sessions")
    if block.effective_weeks > block.priced_sessions:
        raise OpraBoundaryError("effective OPRA weeks exceed priced sessions")
    coverage = _finite(block.coverage, "OPRA coverage", nonnegative=True)
    expected = block.priced_sessions / block.selected_sessions if block.selected_sessions else 0.0
    if coverage > 1 or abs(coverage - expected) > 1e-12:
        raise OpraBoundaryError("OPRA coverage is inconsistent with session counts")
    if (not isinstance(block.failures, tuple)
            or any(not isinstance(item, str) or not item.strip() for item in block.failures)):
        raise OpraBoundaryError("OPRA failures must be non-empty statements")
    floors_pass = coverage >= .70 and block.priced_sessions >= 30 and block.effective_weeks >= 12
    metrics = (block.base_mean_pnl_pct, block.base_lower_bound_pct,
               block.elevated_mean_pnl_pct)
    if block.state == OpraBlockState.UNAVAILABLE:
        if (block.entitlement != EntitlementStatus.UNAVAILABLE or not block.failures
                or block.priced_sessions != 0 or block.effective_weeks != 0 or coverage != 0
                or any(value is not None for value in metrics)):
            raise OpraBoundaryError(
                "unavailable OPRA block requires zero priced coverage and no economic metrics"
            )
        return
    if block.entitlement != EntitlementStatus.AVAILABLE:
        raise OpraBoundaryError("non-unavailable OPRA state requires entitlement")
    if block.state == OpraBlockState.INSUFFICIENT_COVERAGE:
        if floors_pass or not block.failures or any(value is not None for value in metrics):
            raise OpraBoundaryError("insufficient-coverage OPRA block is inconsistent")
        return
    finite_metrics = tuple(_finite(value, "OPRA economic metric") for value in metrics)
    if not floors_pass:
        raise OpraBoundaryError("OPRA economic state does not meet coverage floors")
    if block.state == OpraBlockState.PASSED:
        if block.failures or not all(value > 0 for value in finite_metrics):
            raise OpraBoundaryError("passed OPRA block requires positive economics")
        return
    if block.state == OpraBlockState.FAILED_EVIDENCE:
        if not block.failures or all(value > 0 for value in finite_metrics):
            raise OpraBoundaryError("failed-evidence OPRA block is inconsistent")
        return
    raise OpraBoundaryError("unsupported OPRA block state")


def evaluate_opra_block(decision: DecisionManifest, artifact: QuoteArtifact) -> OpraBlock:
    ledger = replay_opra(decision, artifact)
    selected = ledger.selected_count
    priced_rows = [row for row in ledger.rows if row.selected and row.priced]
    priced = len(priced_rows)
    coverage = priced / selected if selected else 0.0
    weeks = len({date.fromisoformat(row.date).isocalendar()[:2] for row in priced_rows})
    block = OpraBlock(
        state=OpraBlockState.INSUFFICIENT_COVERAGE,
        entitlement=artifact.entitlement.status,
        selected_sessions=selected,
        priced_sessions=priced,
        coverage=coverage,
        effective_weeks=weeks,
        base_mean_pnl_pct=None,
        base_lower_bound_pct=None,
        elevated_mean_pnl_pct=None,
        decision_manifest_hash=decision.content_hash,
        quote_artifact_hash=artifact.content_hash,
        receipt_id=decision.receipt_id,
        failures=(),
    )
    if artifact.entitlement.status == EntitlementStatus.UNAVAILABLE:
        return replace(
            block,
            state=OpraBlockState.UNAVAILABLE,
            failures=("OPRA entitlement unavailable",),
        )
    coverage_failures: list[str] = []
    if coverage < .70:
        coverage_failures.append("OPRA coverage is below 70%")
    if priced < 30:
        coverage_failures.append("fewer than 30 selected sessions have OPRA prices")
    if weeks < 12:
        coverage_failures.append("fewer than 12 ISO weeks have OPRA prices")
    if coverage_failures:
        return replace(block, failures=tuple(coverage_failures))
    priced_outcomes: list[tuple[str, float, float]] = []
    for row in priced_rows:
        if row.gross_pnl is None or row.width is None:
            raise OpraBoundaryError("priced OPRA ledger row is missing economics")
        priced_outcomes.append((row.date, row.gross_pnl, row.width))
    base_outcomes = [
        (day, (gross_pnl - BASE_COST_FRACTION * width) / width)
        for day, gross_pnl, width in priced_outcomes
    ]
    elevated_outcomes = [
        (day, (gross_pnl - ELEVATED_COST_FRACTION * width) / width)
        for day, gross_pnl, width in priced_outcomes
    ]
    base_mean = 100 * sum(value for _, value in base_outcomes) / priced
    elevated_mean = 100 * sum(value for _, value in elevated_outcomes) / priced
    seed = bootstrap_seed(
        decision.candidate_hash, decision.stage_manifest_hash, "opra", "base"
    )
    lower = 100 * weekly_cluster_lower_bound(base_outcomes, alpha=.05, seed=seed)
    evidence_failures: list[str] = []
    if base_mean <= 0:
        evidence_failures.append("actual-price base-cost mean P&L is not positive")
    if lower <= 0:
        evidence_failures.append("actual-price 95% weekly-cluster LCB is not positive")
    if elevated_mean <= 0:
        evidence_failures.append("actual-price elevated-cost mean P&L is not positive")
    return replace(
        block,
        state=(OpraBlockState.FAILED_EVIDENCE if evidence_failures else OpraBlockState.PASSED),
        base_mean_pnl_pct=base_mean,
        base_lower_bound_pct=lower,
        elevated_mean_pnl_pct=elevated_mean,
        failures=tuple(evidence_failures),
    )
