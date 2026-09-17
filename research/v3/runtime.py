"""Trusted, offline runtime for schema-v3 modeled research."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import os
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

from research.backtest.data import CandidateSessionView, Session
from research.backtest.engine import realized_vol
from research.v3.campaign import (
    CampaignCoordinator,
    FrozenCandidate,
    _interprocess_lock,
)
from research.v3.evaluator import Evaluation, PriceSourceKind, Stage, evaluate_ledger
from research.v3.hypothesis import interpret_signal, make_structure, parse_hypothesis
from research.v3.ledger import GrossTradeLedger, GrossTradeRow
from research.v3.proposer import (
    COMPOUND_LANES,
    COMPOUND_LANE_FAMILIES,
    DeclarativeProposer,
    ProposalError,
)
from research.v3.stage_store import StageAccess, StageManifest, materialize_stage_stores

ET = ZoneInfo("America/New_York")
MODELED_PRICE_SOURCE_ID = "modeled:prior-day-rv-black-scholes-v1"


@dataclass(frozen=True)
class StagedResearchData:
    root: Path
    source_hash: str
    manifests: Mapping[str, StageManifest]

    @property
    def manifest_hashes(self) -> dict[str, str]:
        return {name: manifest.manifest_hash for name, manifest in self.manifests.items()}


def load_sessions_file(path: Path) -> list[Session]:
    """Read the canonical Alpaca bar export from an explicit local file."""
    by_day: dict[str, list[tuple[int, float, float, float, float]]] = {}
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        next(reader)
        for row in reader:
            timestamp = datetime.fromisoformat(row[0]).astimezone(ET)
            by_day.setdefault(timestamp.date().isoformat(), []).append((
                timestamp.hour * 60 + timestamp.minute,
                float(row[1]), float(row[2]), float(row[3]), float(row[4]),
            ))
    return [Session(day, tuple(sorted(bars))) for day, bars in sorted(by_day.items())]


def ensure_stage_stores(source_path: Path, shared_root: Path, *,
                        loader: Callable[[Path], Iterable[Session]] = load_sessions_file
                        ) -> StagedResearchData:
    """Create or verify immutable source-hash-addressed stage stores."""
    source_path = Path(source_path).resolve(strict=True)
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    root = Path(shared_root) / source_hash
    with _interprocess_lock(root / ".materialize.lock"):
        if not all((root / stage / "manifest.json").is_file()
                   for stage in ("discovery", "validation", "holdout")):
            existing = [stage for stage in ("discovery", "validation", "holdout")
                        if (root / stage).exists()]
            if existing:
                raise ValueError("staged data store is incomplete and cannot be repaired in place")
            materialize_stage_stores(root, loader(source_path), source_hash=source_hash)
    manifests: dict[str, StageManifest] = {}
    for stage in ("discovery", "validation", "holdout"):
        access = StageAccess(root, stage)
        manifest = access.verify_integrity(require_usable=True)
        if manifest.source_hash != source_hash:
            raise ValueError("staged data source hash mismatch")
        manifests[stage] = manifest
    return StagedResearchData(root, source_hash, manifests)


def _payoff_bounds(structure: Any) -> tuple[float, float]:
    strikes = [leg.strike for leg in structure.legs]
    probes = [0.0, *strikes, max(strikes) + structure.width + 1.0]
    values = [structure.settle(spot) for spot in probes]
    return min(values), max(values)


def build_modeled_ledgers(candidate: FrozenCandidate, access: StageAccess,
                          *, vrp_multiple: float = 1.10
                          ) -> tuple[GrossTradeLedger, GrossTradeLedger]:
    """Interpret a frozen hypothesis over one capability and retain denominators."""
    candidate.validate()
    hypothesis = parse_hypothesis(json.loads(candidate.canonical_document))
    sessions = list(access.open_sessions())
    vol = realized_vol(sessions)
    signal_rows: list[GrossTradeRow] = []
    control_rows: list[GrossTradeRow] = []
    previous_day: str | None = None
    for session in sessions:
        sigma_daily = vol.get(previous_day) if previous_day else None
        previous_day = session.day
        if sigma_daily is None or not session.is_full_day():
            continue
        entry_price = session.price_at(hypothesis.entry_minute)
        if entry_price is None:
            continue
        view = CandidateSessionView.at(session, hypothesis.entry_minute)
        selected = interpret_signal(hypothesis, view)
        structure = make_structure(hypothesis, entry_price)
        remaining = max(1, 960 - hypothesis.entry_minute) / (252.0 * 390.0)
        sigma_t = sigma_daily * math.sqrt(252.0) * vrp_multiple * math.sqrt(remaining)
        signed_debit = structure.model_entry_cost(entry_price, sigma_t)
        settlement = structure.settle(session.close)
        worst, best = _payoff_bounds(structure)
        prices = tuple(
            (leg.cp, leg.strike, leg.qty, leg.model_price(entry_price, sigma_t))
            for leg in structure.legs
        )
        priced = (
            math.isfinite(signed_debit) and best > worst
            and worst - 1e-9 <= settlement <= best + 1e-9
        )

        if priced:
            control_row = GrossTradeRow(
                date=session.day, selected=True, priced=True,
                structure=structure.label, width=structure.width,
                signed_entry_debit=signed_debit, gross_settlement=settlement,
                best_settlement=best, worst_settlement=worst,
                gross_pnl=settlement - signed_debit,
                price_source=PriceSourceKind.MODELED.value,
                entry_legs=prices,
            )
        else:
            control_row = GrossTradeRow(
                date=session.day, selected=True, priced=False,
                structure=structure.label, width=None, signed_entry_debit=None,
                gross_settlement=None, best_settlement=None, worst_settlement=None,
                gross_pnl=None, price_source=PriceSourceKind.MODELED.value,
                skip_reason="modeled economics unavailable",
            )
        signal_rows.append(replace(
            control_row,
            selected=selected,
            skip_reason=control_row.skip_reason if selected else None,
        ))
        control_rows.append(control_row)
    return GrossTradeLedger(signal_rows), GrossTradeLedger(control_rows)


class ModeledResearchEvaluator:
    """Pure scoring facade; an injected evaluator is trusted test infrastructure."""

    def __init__(self, override: Callable[[FrozenCandidate, StageAccess, Stage, int], Evaluation]
                 | None = None):
        self._override = override

    def evaluate(self, candidate: FrozenCandidate, access: StageAccess, stage: Stage,
                 shortlist_size: int = 1) -> Evaluation:
        stage = Stage(stage)
        if access.stage != stage.value:
            raise PermissionError("stage capability does not match requested evaluation")
        if self._override is not None:
            return self._override(candidate, access, stage, shortlist_size)
        signal, control = build_modeled_ledgers(candidate, access)
        return evaluate_ledger(
            signal, stage=stage, candidate_hash=candidate.semantic_hash,
            stage_manifest_hash=access.manifest().manifest_hash,
            price_source_id=MODELED_PRICE_SOURCE_ID,
            price_source_kind=PriceSourceKind.MODELED,
            shortlist_size=shortlist_size, control_ledger=control,
        )


class FormulationLedger:
    """Durable record of all model formulations, including invalid JSON."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]

    @property
    def count(self) -> int:
        return len({record.get("attempt_id") for record in self.records()
                    if record.get("attempt_id")})

    def append(self, record: Mapping[str, Any]) -> None:
        data = json.dumps(dict(record), sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(data + "\n")
            handle.flush()
            os.fsync(handle.fileno())


class CampaignRuntime:
    """Drive discovery and validation, deliberately stopping before holdout."""

    def __init__(self, coordinator: CampaignCoordinator, proposer: DeclarativeProposer,
                 evaluator: ModeledResearchEvaluator, *, mission: str,
                 formulation_limit: int, feature_coverage: Iterable[object] = ()):
        if formulation_limit <= 0:
            raise ValueError("formulation_limit must be positive")
        self.coordinator = coordinator
        self.proposer = proposer
        self.evaluator = evaluator
        self.mission = mission
        if hashlib.sha256(mission.encode("utf-8")).hexdigest() != coordinator.contract.mission_hash:
            raise ValueError("runtime mission differs from the frozen campaign contract")
        self.feature_coverage = tuple(feature_coverage)
        self.formulation_limit = formulation_limit
        self.formulations = FormulationLedger(coordinator.root / "formulations.jsonl")

    def run_until_holdout(self, *, should_pause: Callable[[], bool] = lambda: False
                          ) -> dict[str, Any]:
        state = self.coordinator.state
        if state["state"] == "ready":
            state = self.coordinator.start_discovery(self.feature_coverage)
        while state["state"] == "discovery" and (
            state["scientific_trials"] < self.coordinator.contract.discovery_budget
        ):
            if should_pause():
                return state
            records = self.formulations.records()
            by_attempt: dict[str, list[dict[str, Any]]] = {}
            for record in records:
                by_attempt.setdefault(str(record.get("attempt_id")), []).append(record)
            pending = next((values[-1] for values in by_attempt.values()
                            if values[-1].get("status") == "proposed"), None)
            if pending is None and self.formulations.count >= self.formulation_limit:
                return self.coordinator.fail("formulation/admission-failure limit reached")
            if pending is not None:
                attempt_id = str(pending["attempt_id"])
                document = pending["document"]
                source_hash = str(pending["source_hash"])
                rationale = str(pending["rationale"])
            else:
                attempt_id = f"formulation-{self.formulations.count + 1:04d}"
                try:
                    proposal = self.proposer.propose(
                        mission=self.mission,
                        previous_attempts=records,
                        diversity_feedback=(
                            [self._hypothesis_family_feedback(state)]
                            + [
                                item.get("reason", "")
                                for item in state["discovery_attempts"]
                                if not item.get("admitted")
                            ]
                        ),
                    )
                except ProposalError as exc:
                    self.formulations.append({"attempt_id": attempt_id, "status": "invalid",
                                              "admitted": False,
                                              "reason": f"invalid formulation: {exc}"})
                    state = self.coordinator.state
                    continue
                document = proposal.document
                source_hash = proposal.source_hash
                rationale = proposal.rationale
                self.formulations.append({
                    "attempt_id": attempt_id, "status": "proposed", "document": document,
                    "source_hash": source_hash, "rationale": rationale,
                })
            result = self.coordinator.submit_discovery(
                document, source_hash=source_hash, attempt_id=attempt_id,
                evaluate=lambda candidate, access: self.evaluator.evaluate(
                    candidate, access, Stage.DISCOVERY
                ),
            )
            self.formulations.append({
                "attempt_id": attempt_id, "status": "admission",
                "admitted": bool(result["admitted"]),
                "reason": result.get("reason"), "semantic_hash": (
                    result.get("semantic_hash") or result.get("candidate", {}).get("semantic_hash")
                ), "rationale": rationale,
            })
            state = self.coordinator.state
        if state["state"] == "discovery":
            state = self.coordinator.close_discovery()
        if state["state"] == "shortlist_frozen":
            state = self.coordinator.run_validation(
                lambda candidate, access, size: self.evaluator.evaluate(
                    candidate, access, Stage.VALIDATION, size
                )
            )
        return state

    def _hypothesis_family_feedback(self, state: Mapping[str, Any]) -> str:
        scheduled = self.coordinator.contract.required_hypothesis_families
        if not scheduled:
            return ""
        family_counts: dict[str, int] = {}
        for item in state["discovery_attempts"]:
            if not item.get("admitted"):
                continue
            family = str(item["candidate"]["descriptor"]["family"])
            family_counts[family] = family_counts.get(family, 0) + 1
        missing = [family for family in scheduled if family not in family_counts]
        if not missing:
            counts = ", ".join(
                f"{family}={count}" for family, count in sorted(family_counts.items())
            )
            for lane in COMPOUND_LANES:
                family = COMPOUND_LANE_FAMILIES[lane]
                if family_counts.get(family, 0) < self.coordinator.contract.family_quota:
                    family_descriptors = [
                        item["candidate"]["descriptor"]
                        for item in state["discovery_attempts"]
                        if item.get("admitted")
                        and item["candidate"]["descriptor"]["family"] == family
                    ]
                    variant_a_used = any(
                        descriptor.get("structure_helper") == "call_debit_spread"
                        and float(descriptor.get("width", math.nan)) == 5.0
                        and float(descriptor.get("otm_offset", math.nan)) == 0.0
                        for descriptor in family_descriptors
                    )
                    variant = "B" if variant_a_used else "A"
                    return (
                        "Frozen atomic-family schedule is satisfied. Admitted family counts: "
                        + counts
                        + f". Required next compound lane: {lane}; spread variant {variant}. "
                          "The response schema "
                          "enforces this materially distinct two-observation family; vary its "
                          "times, thresholds, entry, and spread configuration."
                    )
            return (
                "Frozen compound-family lane catalog is exhausted; no further hypothesis "
                "family is admissible."
            )
        return (
            "Required next hypothesis family must be one of: " + ", ".join(missing)
            + ". Repeated families are rejected before scientific evaluation."
        )

    def consume_holdout(self) -> dict[str, Any]:
        finalist = self.coordinator.finalist_contract()
        return self.coordinator.run_holdout(
            finalist,
            lambda candidate, access: self.evaluator.evaluate(
                candidate, access, Stage.HOLDOUT
            ),
        )
