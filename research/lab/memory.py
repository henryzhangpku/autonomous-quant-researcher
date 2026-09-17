"""Lab-wide candidate memory: what has already been tried, across campaigns.

The novelty gate deduplicates within one campaign, so a NEW campaign of the
same mission happily re-spends scientific trials on logic an earlier run
already evaluated (observed 2026-08-13: overnight-spx campaign 1 and its
resumes shared no memory). This module is the append-only, host-local index
of every evaluated candidate across the whole lab.

Scope rules, deliberately narrow:

- Same mission (matching mission hash): a remembered semantic hash is a HARD
  admission rejection — same logic, same frozen mission, already answered.
- Different mission: never blocks. The same code on different data or under a
  different evaluator is a new experiment, and pretending otherwise would
  quietly couple unrelated campaigns. Cross-mission records are informational.

The file lives under the ignored .research/ tree next to run state; records
are one JSON object per line and are never rewritten.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MEMORY_PATH = REPOSITORY_ROOT / ".research" / "lab-memory" / "candidates.jsonl"


class CandidateMemory:
    """Append-only ledger of evaluated candidate semantics across campaigns."""

    def __init__(self, path: Path | None = None) -> None:
        # Resolved at call time, not definition time, so tests can redirect
        # DEFAULT_MEMORY_PATH and never touch the host-wide ledger.
        self.path = path if path is not None else DEFAULT_MEMORY_PATH

    def record(
        self,
        *,
        semantic_hash: str,
        mission_hash: str,
        mission_name: str,
        run: int,
        metric: float | None,
        accepted: bool,
        label: str = "",
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "semantic_hash": semantic_hash,
            "mission_hash": mission_hash,
            "mission_name": mission_name,
            "run": run,
            "metric": metric,
            "accepted": accepted,
            "label": label,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")

    def lookup(self, semantic_hash: str) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        matches: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("semantic_hash") == semantic_hash:
                    matches.append(entry)
        return matches

    def same_mission_conflict(
        self, semantic_hash: str, mission_hash: str
    ) -> dict[str, Any] | None:
        """The earlier same-mission evaluation of this logic, if one exists."""
        for entry in self.lookup(semantic_hash):
            if entry.get("mission_hash") == mission_hash:
                return entry
        return None
