"""Policy-declared candidate imports, preflight flags, and admission heat."""

from __future__ import annotations

from pathlib import Path

import pytest

from research.lab.loop import ResearchLoop, _validate_candidate_source
from research.lab.mission import MissionConfigError, load_mission_spec


def _write_mission(tmp_path: Path, policy_body: str) -> tuple[Path, Path]:
    mission = tmp_path / "mission.md"
    mission.write_text("Test mission.", encoding="utf-8")
    policy = tmp_path / "policy.toml"
    policy.write_text(policy_body, encoding="utf-8")
    return mission, policy


BASE_POLICY = """
name = "policy-extensions"

[validator]
version = "test-v1"
command = ["python", "-m", "research.validators.overnight_spx", "--candidate", "{{candidate}}"]
pattern = 'score=(?P<value>-?[0-9.]+)'
{extra}

[budget]
max_attempts = 3
"""


class TestPolicyDeclaredImports:
    def test_extension_admits_new_factory(self, tmp_path: Path) -> None:
        mission, policy = _write_mission(
            tmp_path, BASE_POLICY.format(extra='allowed_backtest_imports = ["spx_iron_condor"]')
        )
        spec = load_mission_spec(mission, policy)
        assert spec.policy.allowed_backtest_imports == ("spx_iron_condor",)
        source = "from research.backtest import spx_iron_condor\n"
        _validate_candidate_source(source, spec.policy.allowed_backtest_imports)

    def test_unextended_import_still_forbidden(self) -> None:
        with pytest.raises(Exception, match="forbidden"):
            _validate_candidate_source("from research.backtest import spx_iron_condor\n")

    def test_private_extension_rejected(self, tmp_path: Path) -> None:
        mission, policy = _write_mission(
            tmp_path, BASE_POLICY.format(extra='allowed_backtest_imports = ["_snap"]')
        )
        with pytest.raises(MissionConfigError, match="public name"):
            load_mission_spec(mission, policy)

    def test_non_list_rejected(self, tmp_path: Path) -> None:
        mission, policy = _write_mission(
            tmp_path, BASE_POLICY.format(extra='allowed_backtest_imports = "spx_iron_condor"')
        )
        with pytest.raises(MissionConfigError, match="list of strings"):
            load_mission_spec(mission, policy)


class TestPolicyDeclaredPreflight:
    def test_flag_parses(self, tmp_path: Path) -> None:
        mission, policy = _write_mission(tmp_path, BASE_POLICY.format(extra="preflight = false"))
        assert load_mission_spec(mission, policy).policy.supports_preflight is False

    def test_default_is_registry_fallback(self, tmp_path: Path) -> None:
        mission, policy = _write_mission(tmp_path, BASE_POLICY.format(extra=""))
        assert load_mission_spec(mission, policy).policy.supports_preflight is None


class TestSteeringNotes:
    def test_notes_parse_and_reach_the_prompt(self, tmp_path: Path) -> None:
        mission, policy = _write_mission(
            tmp_path,
            BASE_POLICY.format(extra="")
            + '\n[steering]\nnotes = ["discovery spans one low-vol regime", "name the regime a rule exploits"]\n',
        )
        spec = load_mission_spec(mission, policy)
        assert spec.policy.steering_notes == (
            "discovery spans one low-vol regime",
            "name the regime a rule exploits",
        )
        loop = ResearchLoop(
            spec,
            state_dir=tmp_path / "state",
            workspace_dir=tmp_path / "workspace",
            proposer=_HotProposer(),
        )
        prompt = loop.build_prompt()
        assert "low-vol regime" in prompt
        assert "steering (frozen with the policy)" in prompt

    def test_empty_note_rejected(self, tmp_path: Path) -> None:
        mission, policy = _write_mission(
            tmp_path, BASE_POLICY.format(extra="") + '\n[steering]\nnotes = [" "]\n'
        )
        with pytest.raises(MissionConfigError, match="non-empty"):
            load_mission_spec(mission, policy)


class TestLabMemory:
    def test_same_mission_conflict_found(self, tmp_path: Path) -> None:
        from research.lab.memory import CandidateMemory

        memory = CandidateMemory(tmp_path / "memory" / "candidates.jsonl")
        memory.record(
            semantic_hash="abc123", mission_hash="m1", mission_name="campaign-1",
            run=7, metric=-3.2, accepted=False,
        )
        conflict = memory.same_mission_conflict("abc123", "m1")
        assert conflict is not None and conflict["run"] == 7
        # A different mission never blocks: same code, different experiment.
        assert memory.same_mission_conflict("abc123", "m2") is None
        assert memory.same_mission_conflict("unseen", "m1") is None

    def test_missing_file_is_empty(self, tmp_path: Path) -> None:
        from research.lab.memory import CandidateMemory

        memory = CandidateMemory(tmp_path / "nowhere.jsonl")
        assert memory.lookup("anything") == []


class _HotProposer:
    temperature = 0.2

    def propose(self, prompt: str):  # pragma: no cover - never called here
        raise AssertionError("not used")


class TestAdmissionTemperatureEscalation:
    def test_escalates_and_caps(self, tmp_path: Path) -> None:
        mission, policy = _write_mission(tmp_path, BASE_POLICY.format(extra=""))
        spec = load_mission_spec(mission, policy)
        proposer = _HotProposer()
        loop = ResearchLoop(
            spec,
            state_dir=tmp_path / "state",
            workspace_dir=tmp_path / "workspace",
            proposer=proposer,
        )
        for _ in range(10):
            loop._escalate_proposer_temperature()
        assert proposer.temperature == pytest.approx(ResearchLoop.ADMISSION_TEMPERATURE_CEIL)

    def test_temperature_free_proposer_tolerated(self, tmp_path: Path) -> None:
        mission, policy = _write_mission(tmp_path, BASE_POLICY.format(extra=""))
        spec = load_mission_spec(mission, policy)

        class Bare:
            def propose(self, prompt):  # pragma: no cover
                raise AssertionError("not used")

        loop = ResearchLoop(
            spec,
            state_dir=tmp_path / "state",
            workspace_dir=tmp_path / "workspace",
            proposer=Bare(),
        )
        loop._escalate_proposer_temperature()  # must not raise
