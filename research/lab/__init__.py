"""Mission-driven research loop primitives.

The lab package keeps natural-language strategy ideation separate from
deterministic evaluation. Generated candidates are isolated under a supplied
workspace and every attempt is recorded in an append-only ledger.
"""

from .gpu import gpu_diagnostic
from .loop import LabLoopError, ResearchLoop, run_once, status_summary
from .mission import MissionConfigError, MissionSpec, ValidatorPolicy, load_mission_spec
from .proposer import (
    CandidateProposal,
    OpenAIProposer,
    ProposalError,
    Proposer,
    TransientProposalError,
    parse_proposal_json,
)

__all__ = [
    "CandidateProposal",
    "LabLoopError",
    "MissionSpec",
    "MissionConfigError",
    "OpenAIProposer",
    "ProposalError",
    "Proposer",
    "ResearchLoop",
    "TransientProposalError",
    "ValidatorPolicy",
    "gpu_diagnostic",
    "load_mission_spec",
    "parse_proposal_json",
    "run_once",
    "status_summary",
]
