"""Typed data requirements and the refusal a campaign returns until they are met.

A pre-registered campaign does not evaluate on whatever data happens to be on
hand. Each input it needs is declared as a :class:`Requirement`; a check turns
the inputs actually available into one :class:`RequirementStatus` per
requirement, with how much history exists against how much is required. If
any is unmet the campaign returns a :class:`Refusal` value — not an exception
— naming exactly what is missing. A refusal is not a verdict and opens
nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Sequence


class RequirementState(StrEnum):
    MET = "met"
    PARTIAL = "partial"         # some history exists, less than required
    MISSING = "missing"         # nothing usable exists
    NOT_LICENSED = "not_licensed"


@dataclass(frozen=True)
class Requirement:
    key: str
    description: str
    source: str
    unit: str                   # what ``required`` and ``available`` count
    required: int


@dataclass(frozen=True)
class RequirementStatus:
    requirement: Requirement
    state: RequirementState
    available: int
    detail: str

    @property
    def met(self) -> bool:
        return self.state is RequirementState.MET

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.requirement.key,
            "description": self.requirement.description,
            "source": self.requirement.source,
            "unit": self.requirement.unit,
            "required": self.requirement.required,
            "available": self.available,
            "state": self.state.value,
            "detail": self.detail,
        }


def status(requirement: Requirement, available: int, detail: str, *,
           licensed: bool = True) -> RequirementStatus:
    if not licensed:
        state = RequirementState.NOT_LICENSED
    elif available >= requirement.required:
        state = RequirementState.MET
    elif available > 0:
        state = RequirementState.PARTIAL
    else:
        state = RequirementState.MISSING
    return RequirementStatus(requirement, state, max(0, available), detail)


@dataclass(frozen=True)
class RequirementsReport:
    campaign: str
    items: tuple[RequirementStatus, ...]

    @property
    def ready(self) -> bool:
        return all(item.met for item in self.items)

    @property
    def unmet(self) -> tuple[RequirementStatus, ...]:
        return tuple(item for item in self.items if not item.met)


@dataclass(frozen=True)
class Refusal:
    """The campaign declined to evaluate. Carries every reason, never a metric."""

    campaign: str
    reason: str
    unmet: tuple[RequirementStatus, ...] = field(default_factory=tuple)

    @property
    def missing_keys(self) -> tuple[str, ...]:
        return tuple(item.requirement.key for item in self.unmet)

    def as_dict(self) -> dict[str, object]:
        return {"campaign": self.campaign, "refused": True, "reason": self.reason,
                "unmet": [item.as_dict() for item in self.unmet]}


def refuse_unless_ready(report: RequirementsReport) -> Refusal | None:
    if report.ready:
        return None
    return Refusal(report.campaign, "data requirements are not met", report.unmet)


def summarize(items: Sequence[RequirementStatus]) -> str:
    return "; ".join(f"{item.requirement.key}: {item.available}/{item.requirement.required} "
                     f"{item.requirement.unit} ({item.state.value})" for item in items)
