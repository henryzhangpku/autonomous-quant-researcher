"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

import research.lab.memory as lab_memory


@pytest.fixture(autouse=True)
def _isolated_lab_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Every test gets its own candidate-memory ledger.

    Without this, loop tests append to the host-wide .research/lab-memory
    file — and because test missions hash identically across runs, the SECOND
    pytest invocation would hit same-mission conflicts and admission-fail
    candidates that passed the first time.
    """
    monkeypatch.setattr(
        lab_memory, "DEFAULT_MEMORY_PATH", tmp_path / "lab-memory" / "candidates.jsonl"
    )
