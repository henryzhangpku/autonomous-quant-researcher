"""Trusted, offline runtime for schema-v3 modeled research."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import os
from dataclasses import dataclass, replace
from datetime import date, datetime, time
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
    DeclarativeProposer,
    ProposalError,
    compound_lanes_for,
    lane_family,
)
from research.v3.program import Program
from research.v3.quote_store import DayQuotes, QuoteAccess
from research.v3.stage_store import StageAccess, StageManifest, materialize_stage_stores

ET = ZoneInfo("America/New_York")
MODELED_PRICE_SOURCE_ID = "modeled:prior-day-rv-black-scholes-v1"
# Alpaca 1-minute option trade bars: real prints, not executable quotes.
QUOTE_PRICE_SOURCE_ID = "indicative:alpaca-option-trade-bars-1m-v1"
from research.v3.features import ET, FeatureExport, derive_feature_snapshot


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


def _feature_families(node: Mapping[str, Any]) -> set[str]:
    observation = node.get("observation")
    found = {observation["family"]} if isinstance(observation, Mapping) \
        and observation.get("obs") == "feature" else set()
    for child in [*(node.get("args") or []), node.get("arg")]:
        if isinstance(child, Mapping):
            found |= _feature_families(child)
    return found


def build_modeled_ledgers(candidate: FrozenCandidate, access: StageAccess,
                          *, vrp_multiple: float = 1.10,
                          features: Mapping[str, FeatureExport] | None = None,
                          symbol: str = "SPY",
                          quotes: Mapping[str, DayQuotes] | None = None,
                          ) -> tuple[GrossTradeLedger, GrossTradeLedger]:
    """Interpret a frozen hypothesis over one capability and retain denominators.

    With ``quotes`` (one stage's real same-day option prices) every leg is
    priced from the last trade before entry; a leg with none is unpriced,
    never modeled. Without them, legs are priced by the frozen model."""
    candidate.validate()
    hypothesis = parse_hypothesis(json.loads(candidate.canonical_document))
    families = _feature_families(hypothesis.signal)
    missing = families - set(features or {})
    if missing:
        raise ValueError(f"hypothesis reads feature families with no export: {sorted(missing)}")
    sessions = list(access.open_sessions())
    vol = realized_vol(sessions)
    signal_rows: list[GrossTradeRow] = []
    control_rows: list[GrossTradeRow] = []
    previous_day: str | None = None
    for session in sessions:
        sigma_daily = vol.get(previous_day) if previous_day else None
        previous_day = session.day
        # only model pricing needs trailing volatility; quote pricing starts on day one
        if (sigma_daily is None and quotes is None) or not session.is_full_day():
            continue
        entry_price = session.price_at(hypothesis.entry_minute)
        if entry_price is None:
            continue
        snapshot = None
        if families:
            decision = datetime.combine(
                date.fromisoformat(session.day),
                time(hypothesis.entry_minute // 60, hypothesis.entry_minute % 60), tzinfo=ET,
            )
            snapshot = derive_feature_snapshot(
                {family: features[family] for family in families}, symbol=symbol,
                decision_time=decision.isoformat(), spot=entry_price,
                required_families=families,
            )
        view = CandidateSessionView.at(session, hypothesis.entry_minute, snapshot)
        selected = interpret_signal(hypothesis, view)
        structure = make_structure(hypothesis, entry_price)
        remaining = max(1, 960 - hypothesis.entry_minute) / (252.0 * 390.0)
        source = PriceSourceKind.MODELED.value
        skip_reason = "modeled economics unavailable"
        if quotes is None:
            sigma_t = sigma_daily * math.sqrt(252.0) * vrp_multiple * math.sqrt(remaining)
            signed_debit = structure.model_entry_cost(entry_price, sigma_t)
            prices = tuple(
                (leg.cp, leg.strike, leg.qty, leg.model_price(entry_price, sigma_t))
                for leg in structure.legs
            )
        else:
            source = PriceSourceKind.INDICATIVE.value
            skip_reason = "no leg trade within the quote freshness window before entry"
            book = quotes.get(session.day)
            leg_prices = [book.price(leg.cp, leg.strike, hypothesis.entry_minute)
                          if book is not None else None for leg in structure.legs]
            if any(price is None for price in leg_prices):
                signed_debit, prices = math.nan, ()
            else:
                signed_debit = sum(leg.qty * price
                                   for leg, price in zip(structure.legs, leg_prices))
                prices = tuple((leg.cp, leg.strike, leg.qty, price)
                               for leg, price in zip(structure.legs, leg_prices))
        settlement = structure.settle(session.close)
        worst, best = _payoff_bounds(structure)
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
                price_source=source,
                entry_legs=prices,
            )
        else:
            control_row = GrossTradeRow(
                date=session.day, selected=True, priced=False,
                structure=structure.label, width=None, signed_entry_debit=None,
                gross_settlement=None, best_settlement=None, worst_settlement=None,
                gross_pnl=None, price_source=source,
                skip_reason=skip_reason,
            )
        signal_rows.append(replace(
            control_row,
            selected=selected,
            skip_reason=control_row.skip_reason if selected else None,
        ))
        control_rows.append(control_row)
    return GrossTradeLedger(signal_rows), GrossTradeLedger(control_rows)


class QuotedResearchEvaluator:
    """Scores a frozen hypothesis on one stage's real same-day option prices.

    The quote store is opened through the same stage the evaluation was
    granted, so a discovery evaluation cannot read validation or holdout
    prices."""

    def __init__(self, quote_root: Path, program: Program,
                 features: Mapping[str, FeatureExport] | None = None):
        if program.price_source != "quote":
            raise ValueError(f"{program.program_id} is not a quote-priced program")
        self._quote_root = Path(quote_root)
        self._program = program
        self._features = dict(features or {})

    def evaluate(self, candidate: FrozenCandidate, access: StageAccess, stage: Stage,
                 shortlist_size: int = 1) -> Evaluation:
        stage = Stage(stage)
        if access.stage != stage.value or access.program != self._program:
            raise PermissionError("stage capability does not match requested evaluation")
        quotes = QuoteAccess(self._quote_root, stage.value, self._program)
        signal, control = build_modeled_ledgers(candidate, access, features=self._features,
                                                quotes=quotes.open_days())
        evaluation = evaluate_ledger(
            signal, stage=stage, candidate_hash=candidate.semantic_hash,
            stage_manifest_hash=access.manifest().manifest_hash,
            price_source_id=QUOTE_PRICE_SOURCE_ID,
            price_source_kind=PriceSourceKind.INDICATIVE,
            shortlist_size=shortlist_size, control_ledger=control, program=self._program,
        )
        return replace(evaluation, feature_manifest_hashes={
            family: export.manifest.manifest_hash for family, export in sorted(self._features.items())
        })


class ModeledResearchEvaluator:
    """Pure scoring facade; an injected evaluator is trusted test infrastructure."""

    def __init__(self, override: Callable[[FrozenCandidate, StageAccess, Stage, int], Evaluation]
                 | None = None, features: Mapping[str, FeatureExport] | None = None):
        self._override = override
        self._features = dict(features or {})

    def evaluate(self, candidate: FrozenCandidate, access: StageAccess, stage: Stage,
                 shortlist_size: int = 1) -> Evaluation:
        stage = Stage(stage)
        if access.stage != stage.value:
            raise PermissionError("stage capability does not match requested evaluation")
        if self._override is not None:
            return self._override(candidate, access, stage, shortlist_size)
        signal, control = build_modeled_ledgers(candidate, access, features=self._features)
        evaluation = evaluate_ledger(
            signal, stage=stage, candidate_hash=candidate.semantic_hash,
            stage_manifest_hash=access.manifest().manifest_hash,
            price_source_id=MODELED_PRICE_SOURCE_ID,
            price_source_kind=PriceSourceKind.MODELED,
            shortlist_size=shortlist_size, control_ledger=control,
        )
        return replace(evaluation, feature_manifest_hashes={
            family: export.manifest.manifest_hash for family, export in sorted(self._features.items())
        })


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
            for lane in compound_lanes_for(scheduled):
                family = lane_family(lane)
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
