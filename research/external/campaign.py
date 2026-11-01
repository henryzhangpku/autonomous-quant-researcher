"""Staged campaign for declarative portfolio recipes on an external evaluator.

The same discipline as the schema-v3 coordinator, for a different hypothesis
type: an append-only, hash-chained ledger (the v3/pre-registration event
format, via ``research.prereg.ledger``) is the only state; every formulation,
admission decision, trial, crash and stage transition is an event; the
contract -- evaluator pin, vocabulary, budget, quotas, stage dates and the
hashes of the code that judges -- is bound in the first event, and a changed
contract or judging code fails closed.

Lifecycle: discovery (budgeted trials) -> shortlist (<= 3 gate-passers,
distinct families preferred) -> validation (Bonferroni-adjusted bootstrap
bound and per-year gates on top of the stage gates) -> one-use holdout,
opened only by an explicit call whose receipt is written before any data is
read; a failed holdout evaluation still consumes it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from research.external.portfolio import (PORTFOLIO_STAGES, ExternalEvaluatorError,
                                         ExternalEvaluatorSpec, Refusal,
                                         canonical_recipe_hash, evaluate_portfolio_stage)
from research.external.recipes import RecipeDescriptor, RecipeShapeError, admission_reason, describe
from research.prereg.gates import (BOOTSTRAP_ALPHA, GateState, StageVerdict,
                                   block_bootstrap_lower_bound, bootstrap_seed)
from research.prereg.ledger import _append, read_events, text_sha256
from research.v3.campaign import _interprocess_lock, _sha256

ROOT = Path(__file__).resolve().parents[2]
JUDGING_CODE = ("research/prereg/gates.py", "research/external/portfolio.py",
                "research/external/recipes.py", "research/external/campaign.py")
VALIDATION_YEARS = (2022, 2023, 2024)
MIN_WEEKS_PER_YEAR = 8


class CampaignError(RuntimeError):
    pass


class ContractMismatch(CampaignError):
    pass


class BudgetExhausted(CampaignError):
    """Running out of trials pauses discovery; it is never a positive verdict."""


@dataclass(frozen=True)
class PortfolioCampaignContract:
    campaign_id: str
    mission: str
    evaluator: ExternalEvaluatorSpec
    vocabulary_hash: str
    discovery_budget: int
    family_quota: int = 2
    shortlist_max: int = 3
    stages: Mapping[str, tuple[str, str | None]] = field(
        default_factory=lambda: dict(PORTFOLIO_STAGES))

    def validate(self) -> None:
        if not self.campaign_id or not self.mission.strip():
            raise ValueError("campaign id and mission are required")
        if self.discovery_budget <= 0 or self.family_quota <= 0 or self.shortlist_max <= 0:
            raise ValueError("budget, family quota and shortlist size must be positive")
        if len(self.vocabulary_hash) != 64:
            raise ValueError("vocabulary_hash must be a SHA-256 hash")

    def contract_hash(self) -> str:
        self.validate()
        return _sha256(asdict(self))


def judging_code_hashes() -> dict[str, str]:
    return {path: text_sha256(ROOT / path) for path in JUDGING_CODE}


def _excerpt(value: Any, limit: int = 400) -> str:
    """A bounded, diagnostic copy of a rejected proposal (never evaluated)."""
    try:
        text = json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = repr(value)
    return text[:limit]


def _weakest(gates: Sequence[Mapping[str, Any]]) -> float:
    values = [g["value"] for g in gates if g.get("value") is not None]
    return min(values) if values else float("-inf")


class PortfolioCampaign:
    def __init__(self, root: Path, contract: PortfolioCampaignContract,
                 evaluate: Callable[..., Any] = evaluate_portfolio_stage,
                 vocabulary: Mapping[str, Any] | None = None):
        self.root = Path(root)
        self.contract = contract
        self._evaluate = evaluate
        # The frozen vocabulary supplies operator semantics for families; it must
        # be the one whose hash the contract pins.
        if vocabulary is not None and _sha256(vocabulary) != contract.vocabulary_hash:
            raise ContractMismatch("vocabulary does not match the frozen vocabulary hash")
        self.vocabulary = vocabulary
        self.ledger_path = self.root / "ledger.jsonl"
        self.lock_path = self.root / ".lock"
        with _interprocess_lock(self.lock_path):
            events = read_events(self.ledger_path)
            if not events:
                _append(self.ledger_path, events, "campaign_initialized", "init",
                        {"contract": asdict(contract), "contract_hash": contract.contract_hash(),
                         "judging_code": judging_code_hashes()}, None)
            else:
                first = events[0]["payload"]
                if first["contract_hash"] != contract.contract_hash():
                    raise ContractMismatch("the frozen campaign contract has changed")
                if first["judging_code"] != judging_code_hashes():
                    raise ContractMismatch("the code that judges this campaign has changed")

    # ------------------------------------------------------------------ state
    def events(self) -> list[dict[str, Any]]:
        return read_events(self.ledger_path)

    def state(self) -> dict[str, Any]:
        s: dict[str, Any] = {"phase": "discovery", "trials": 0, "formulations": 0,
                             "admitted": [], "results": {}, "shortlist": [],
                             "validation": {}, "finalist": None, "holdout": None}
        for event in self.events():
            t, p = event["type"], event["payload"]
            if t in ("formulation_rejected", "admission_rejected", "discovery_attempted"):
                s["formulations"] += 1
            if t == "discovery_attempted":
                s["admitted"].append(p)
            elif t == "discovery_result":
                s["results"][p["semantic_hash"]] = p
                if p["status"] != "formulation_refused":
                    s["trials"] += 1
            elif t == "shortlist_frozen":
                s["phase"], s["shortlist"] = "shortlist_frozen", p["candidates"]
            elif t == "no_discovery_survivor":
                s["phase"] = "closed"
            elif t == "validation_result":
                s["validation"][p["semantic_hash"]] = p
            elif t == "validation_completed":
                s["phase"], s["finalist"] = "holdout_ready", p["finalist"]
            elif t == "no_validation_survivor":
                s["phase"] = "closed"
            elif t == "holdout_receipt":
                s["phase"] = "holdout_consumed"
            elif t in ("holdout_result", "holdout_failed"):
                s["holdout"] = p
        # an attempt without a result is a crashed trial: it still counts
        unresolved = {a["semantic_hash"] for a in s["admitted"]} - set(s["results"])
        s["trials"] += len(unresolved)
        return s

    def _descriptors(self, state: Mapping[str, Any]) -> list[RecipeDescriptor]:
        return [RecipeDescriptor(**a["descriptor"]) for a in state["admitted"]
                if state["results"].get(a["semantic_hash"], {}).get("status")
                != "formulation_refused"]

    # -------------------------------------------------------------- discovery
    def submit(self, proposal: Mapping[str, Any], *, source: str = "proposer") -> dict[str, Any]:
        """Record and, if admitted, evaluate one proposal in discovery."""
        with _interprocess_lock(self.lock_path):
            state = self.state()
            if state["phase"] != "discovery":
                raise CampaignError(f"discovery is closed (phase {state['phase']})")
            events = self.events()
            seq = len(events) + 1
            recipe = proposal.get("recipe") if isinstance(proposal, Mapping) else None
            rationale = str(proposal.get("rationale", ""))[:2000] if isinstance(proposal, Mapping) else ""
            try:
                if not isinstance(proposal, Mapping) or set(proposal) != {"recipe", "rationale"}:
                    raise RecipeShapeError("a proposal has exactly 'recipe' and 'rationale'")
                descriptor = describe(recipe, self.vocabulary)
            except RecipeShapeError as exc:
                return _append(self.ledger_path, events, "formulation_rejected", f"f{seq}",
                               {"reason": str(exc), "source": source,
                                "excerpt": _excerpt(proposal)}, None)
            reason = admission_reason(descriptor, self._descriptors(state),
                                      self.contract.family_quota)
            if reason:
                return _append(self.ledger_path, events, "admission_rejected", f"a{seq}",
                               {"reason": reason, "descriptor": asdict(descriptor),
                                "source": source}, None)
            if state["trials"] >= self.contract.discovery_budget:
                raise BudgetExhausted(f"{state['trials']} of {self.contract.discovery_budget} "
                                      "trials used; discovery is paused, not passed")
            _append(self.ledger_path, events, "discovery_attempted", f"t{seq}",
                    {"semantic_hash": descriptor.semantic_hash, "recipe": recipe,
                     "rationale": rationale, "descriptor": asdict(descriptor),
                     "source": source}, None)
            payload = self._run_stage(recipe, "discovery")
            payload["semantic_hash"] = descriptor.semantic_hash
            return _append(self.ledger_path, self.events(), "discovery_result", f"r{seq}",
                           payload, None)

    def _run_stage(self, recipe: Mapping[str, Any], stage: str,
                   stage_end: str | None = None) -> dict[str, Any]:
        try:
            report, evidence = self._evaluate(self.contract.evaluator, recipe, stage,
                                              stage_end=stage_end,
                                              campaign=self.contract.campaign_id)
        except Refusal as exc:
            status = "formulation_refused" if exc.reason == "recipe_invalid" else "refused"
            return {"status": status, "reason": exc.reason, "detail": exc.detail}
        except (ExternalEvaluatorError, OSError, ValueError) as exc:
            return {"status": "crashed", "error": f"{type(exc).__name__}: {exc}"}
        return {"status": "evaluated", "verdict": report.verdict.value, "reason": report.reason,
                "weeks": report.weeks,
                "gates": [{"name": g.name, "state": g.state.value, "value": g.value}
                          for g in report.gates],
                "summary": evidence.get("summary", {}),
                "evidence": {"code_hash": evidence["evaluator"]["code_hash"],
                             "data_hash": evidence.get("data_hash"),
                             "recipe_hash": evidence.get("recipe_hash")},
                "weekly": evidence.get("weeks", [])}

    def close_discovery(self) -> dict[str, Any]:
        with _interprocess_lock(self.lock_path):
            state = self.state()
            if state["phase"] != "discovery":
                raise CampaignError("discovery is already closed")
            passers = []
            for attempt in state["admitted"]:
                result = state["results"].get(attempt["semantic_hash"], {})
                if result.get("verdict") == StageVerdict.PASSED.value:
                    passers.append((_weakest(result["gates"]), attempt))
            passers.sort(key=lambda item: (-item[0], item[1]["semantic_hash"]))
            chosen, families = [], set()
            for strict in (True, False):  # distinct families first, then fill
                for score, attempt in passers:
                    if len(chosen) == self.contract.shortlist_max:
                        break
                    family = attempt["descriptor"]["family"]
                    if attempt in chosen or (strict and family in families):
                        continue
                    chosen.append(attempt)
                    families.add(family)
            events = self.events()
            if not chosen:
                return _append(self.ledger_path, events, "no_discovery_survivor", "close",
                               {"trials": state["trials"]}, None)
            candidates = [{"semantic_hash": a["semantic_hash"], "recipe": a["recipe"],
                           "family": a["descriptor"]["family"]} for a in chosen]
            return _append(self.ledger_path, events, "shortlist_frozen", "close",
                           {"candidates": candidates,
                            "shortlist_hash": _sha256(candidates)}, None)

    # ------------------------------------------------------------- validation
    def run_validation(self) -> dict[str, Any]:
        with _interprocess_lock(self.lock_path):
            state = self.state()
            if state["phase"] != "shortlist_frozen":
                raise CampaignError(f"validation needs a frozen shortlist (phase {state['phase']})")
            shortlist = state["shortlist"]
            alpha = BOOTSTRAP_ALPHA / len(shortlist)
            best = None
            for item in shortlist:
                if item["semantic_hash"] in state["validation"]:
                    payload = state["validation"][item["semantic_hash"]]
                else:
                    payload = self._run_stage(item["recipe"], "validation")
                    payload["semantic_hash"] = item["semantic_hash"]
                    if payload["status"] == "evaluated":
                        payload["gates"] += self._validation_extra_gates(payload, alpha, item)
                        failed = any(g["state"] == GateState.FAILED.value
                                     for g in payload["gates"])
                        if payload["verdict"] == StageVerdict.PASSED.value and failed:
                            payload["verdict"] = StageVerdict.FAILED.value
                    _append(self.ledger_path, self.events(), "validation_result",
                            "v" + item["semantic_hash"][:16], payload, None)
                if payload.get("verdict") == StageVerdict.PASSED.value:
                    score = _weakest(payload["gates"])
                    if best is None or score > best[0]:
                        best = (score, item)
            events = self.events()
            if best is None:
                return _append(self.ledger_path, events, "no_validation_survivor", "validated",
                               {"shortlist": [i["semantic_hash"] for i in shortlist]}, None)
            return _append(self.ledger_path, events, "validation_completed", "validated",
                           {"finalist": best[1]}, None)

    def _validation_extra_gates(self, payload: Mapping[str, Any], alpha: float,
                                item: Mapping[str, Any]) -> list[dict[str, Any]]:
        weekly = payload.get("weekly", [])
        base = [w["base"] for w in weekly]
        gates = []
        if base:
            seed = bootstrap_seed(self.contract.campaign_id, "validation-bonferroni",
                                  item["semantic_hash"])
            lower = block_bootstrap_lower_bound(base, block=4, seed=seed, alpha=alpha)
            gates.append({"name": "bootstrap_lower_bound_bonferroni",
                          "state": (GateState.PASSED if lower > 0 else GateState.FAILED).value,
                          "value": lower})
        for year in VALIDATION_YEARS:
            values = [w["base"] for w in weekly if date.fromisoformat(w["week"]).year == year]
            ok = len(values) >= MIN_WEEKS_PER_YEAR and sum(values) / len(values) > 0
            gates.append({"name": f"year_{year}_positive",
                          "state": (GateState.PASSED if ok else GateState.FAILED).value,
                          "value": sum(values) / len(values) if values else None})
        return gates

    # ---------------------------------------------------------------- holdout
    def consume_holdout(self, *, end: str, confirm: bool = False) -> dict[str, Any]:
        """Open the final holdout exactly once. The receipt is written first."""
        if not confirm:
            raise CampaignError("the holdout is one-use; pass confirm=True deliberately")
        date.fromisoformat(end)
        with _interprocess_lock(self.lock_path):
            state = self.state()
            if state["phase"] != "holdout_ready":
                raise CampaignError(f"no finalist is holdout-ready (phase {state['phase']})")
            finalist = state["finalist"]
            _append(self.ledger_path, self.events(), "holdout_receipt", "holdout",
                    {"finalist": finalist["semantic_hash"], "end": end,
                     "recipe_hash": canonical_recipe_hash(finalist["recipe"])}, None)
            payload = self._run_stage(finalist["recipe"], "holdout", stage_end=end)
            payload["semantic_hash"] = finalist["semantic_hash"]
            kind = "holdout_result" if payload["status"] == "evaluated" else "holdout_failed"
            return _append(self.ledger_path, self.events(), kind, "holdout-result", payload, None)

    def rejection_feedback(self, limit: int = 8) -> list[dict[str, Any]]:
        """Recent formulation/admission rejections, so the proposer can correct itself."""
        out = []
        recipes = {}
        for event in self.events():
            p = event["payload"]
            if event["type"] == "discovery_attempted":
                recipes[p["semantic_hash"]] = p["recipe"]
            elif event["type"] == "discovery_result" and p.get("status") == "formulation_refused":
                out.append({"kind": "vocabulary", "reason": p.get("detail", p.get("reason")),
                            "excerpt": _excerpt(recipes.get(p["semantic_hash"]))[:200]})
            elif event["type"] == "formulation_rejected":
                out.append({"kind": "formulation", "reason": p["reason"],
                            "excerpt": p.get("excerpt", "")[:200]})
            elif event["type"] == "admission_rejected":
                out.append({"kind": "admission", "reason": p["reason"],
                            "family": p["descriptor"]["family"]})
        return out[-limit:]

    def attempts_summary(self) -> list[dict[str, Any]]:
        """Discovery evidence only, for the proposer. Validation stays sealed."""
        state = self.state()
        out = []
        for attempt in state["admitted"]:
            r = state["results"].get(attempt["semantic_hash"], {"status": "crashed"})
            out.append({"recipe": attempt["recipe"], "family": attempt["descriptor"]["family"],
                        "status": r.get("status"), "verdict": r.get("verdict"),
                        "failed_gates": [g["name"] for g in r.get("gates", [])
                                         if g["state"] == GateState.FAILED.value],
                        "ir_base": (r.get("summary") or {}).get("ir_base"),
                        "detail": r.get("detail") or r.get("error")})
        return out
