from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from research.lab import CandidateProposal, LabLoopError, MissionSpec, ResearchLoop, ValidatorPolicy
from research.lab.loop import (
    CandidateAdmissionError,
    _candidate_semantic_hash,
    _feedback,
    _is_accepted,
    _meets_target,
    _validator_diagnostics,
)


class StubProposer:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def propose(self, prompt: str) -> CandidateProposal:
        self.prompts.append(prompt)
        score = 0.20 if "score=0.8" in prompt else 0.80
        return CandidateProposal(
            code=f"score = {score:.2f}\n",
            rationale=f"offline candidate with score={score:.2f}",
        )


def _loop(tmp_path: Path, *, max_attempts: int = 2) -> tuple[ResearchLoop, StubProposer]:
    validator = tmp_path / "validator.py"
    validator.write_text(
        "\n".join(
            [
                "import os",
                "import re",
                "import sys",
                "from pathlib import Path",
                "candidate = Path(sys.argv[1]).read_text(encoding='utf-8')",
                "value = re.search(r'score\\s*=\\s*([0-9.]+)', candidate).group(1)",
                "print(f'metric={float(value):.2f}')",
                "print(f'credential_present={bool(os.getenv(\"ALPACA_API_KEY\"))}')",
            ]
        ),
        encoding="utf-8",
    )
    policy = ValidatorPolicy(
        command=(sys.executable, str(validator), "{candidate}"),
        metric_pattern=r"metric=(?P<value>[0-9.]+)",
        evaluator_version="offline-test-v1",
        metric_name="score",
        direction="minimize",
        max_attempts=max_attempts,
        min_attempts=2 if max_attempts >= 2 else 1,
        target_metric=0.20 if max_attempts >= 2 else 0.10,
    )
    proposer = StubProposer()
    loop = ResearchLoop(
        MissionSpec("Find an offline parameter candidate.", policy, "offline-test"),
        state_dir=tmp_path / "state",
        workspace_dir=tmp_path / "workspace",
        proposer=proposer,
        base_env={"ALPACA_API_KEY": "secret", "PATH": "test-path"},
    )
    return loop, proposer


def test_loop_generates_content_addressed_candidates_and_feeds_back(tmp_path: Path):
    loop, proposer = _loop(tmp_path)

    summary = loop.run_until_complete()
    records = [
        json.loads(line)
        for line in (tmp_path / "state" / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert summary["status"] == "complete"
    assert summary["best_metric"] == 0.20
    assert summary["accepted_run"] == 2
    assert len(records) == 2
    assert records[0]["metric"] == 0.80
    assert records[1]["metric"] == 0.20
    assert "score=0.8" in proposer.prompts[1]
    assert "credential_present=False" in records[0]["validator"]["stdout_tail"]
    candidate = tmp_path / "workspace" / records[1]["candidate_path"]
    assert candidate.parent.name == records[1]["candidate_hash"]
    assert candidate.is_file()
    result = json.loads((tmp_path / "state" / "result.json").read_text(encoding="utf-8"))
    assert result["metric"] == 0.20
    report = (tmp_path / "state" / "shutdown-report.md").read_text(encoding="utf-8")
    assert "Attempts: 2 / 2" in report
    assert "Accepted run: 2" in report


def test_loop_rejects_a_second_host_before_it_can_duplicate_attempt_numbers(
    tmp_path: Path,
) -> None:
    loop, _ = _loop(tmp_path)
    loop.init()

    with loop._run_lock():
        with pytest.raises(LabLoopError, match="already running"):
            loop.run_once()

    assert loop.status_summary()["run_count"] == 0


def test_contract_preflight_repairs_candidate_before_spending_run_budget(
    tmp_path: Path, monkeypatch
) -> None:
    loop, _ = _loop(tmp_path)

    class RepairingProposer:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def propose(self, prompt: str) -> CandidateProposal:
            self.prompts.append(prompt)
            score = 0.80 if len(self.prompts) == 1 else 0.20
            return CandidateProposal(
                code=f"score = {score:.2f}\n",
                rationale=f"candidate {len(self.prompts)}",
            )

    proposer = RepairingProposer()
    loop.proposer = proposer

    def fake_preflight(candidate_path: Path) -> dict[str, object]:
        failed = "score = 0.80" in candidate_path.read_text(encoding="utf-8")
        output = (
            '{"error": "candidate signal failed preflight: float and NoneType", '
            '"status": "failed"}'
            if failed
            else '{"label": "repaired", "status": "preflight_ok"}'
        )
        return {
            "command": ["preflight"],
            "returncode": 2 if failed else 0,
            "duration_seconds": 0.01,
            "stdout_tail": output,
            "stderr_tail": "",
            "combined_output": output,
        }

    monkeypatch.setattr(loop, "_run_candidate_preflight", fake_preflight)

    record = loop.run_once()

    assert record["run"] == 1
    assert record["metric"] == 0.20
    assert len(record["proposal_attempts"]) == 2
    assert record["proposal_attempts"][0]["error"].startswith(
        "Candidate contract preflight failed"
    )
    assert record["proposal_attempts"][1]["error"] is None
    assert "float and NoneType" in proposer.prompts[1]
    assert loop.status_summary()["run_count"] == 1


def test_bare_python_validator_uses_active_project_interpreter(tmp_path: Path) -> None:
    policy = ValidatorPolicy(
        command=("python", "-m", "research.validators.options", "--candidate", "{candidate}"),
        metric_pattern=r"metric=(?P<value>[0-9.]+)",
        evaluator_version="interpreter-test-v1",
    )
    loop = ResearchLoop(
        MissionSpec("Use the active project interpreter.", policy, "interpreter-test"),
        state_dir=tmp_path / "state",
        workspace_dir=tmp_path / "workspace",
        proposer=StubProposer(),
    )

    command = loop._validator_command(tmp_path / "candidate.py")

    assert command[0] == sys.executable
    assert command[-1] == str(tmp_path / "candidate.py")


def test_loop_pause_and_budget_are_durable(tmp_path: Path):
    loop, _ = _loop(tmp_path, max_attempts=1)
    loop.init()
    loop.pause()
    with pytest.raises(LabLoopError, match="paused"):
        loop.run_once()

    loop.resume()
    summary = loop.run_until_complete()
    assert summary["status"] == "paused"
    assert summary["run_count"] == 1
    assert not (tmp_path / "state" / "result.json").exists()
    assert (tmp_path / "state" / "shutdown-report.md").is_file()


def test_initialized_trial_family_rejects_mission_or_policy_drift(tmp_path: Path):
    loop, proposer = _loop(tmp_path)
    loop.init()
    drifted = ResearchLoop(
        MissionSpec("A changed mission.", loop.spec.policy, loop.spec.name),
        state_dir=tmp_path / "state",
        workspace_dir=tmp_path / "workspace",
        proposer=proposer,
    )

    with pytest.raises(LabLoopError, match="drifted"):
        drifted.init()


def test_initialized_trial_family_rejects_evaluator_version_drift(tmp_path: Path):
    loop, _ = _loop(tmp_path)
    state = loop.init()
    state["evaluator_version"] = "older-evaluator-v0"
    loop.state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(LabLoopError, match="Evaluator contract drifted"):
        loop.init()


def test_semantic_duplicate_is_repaired_without_spending_a_trial(tmp_path: Path) -> None:
    loop, _ = _loop(tmp_path, max_attempts=3)

    class SequencedProposer:
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.codes = iter(
                (
                    'LABEL = "first"\nscore = 0.80\n',
                    '# renamed only\nLABEL = "second"\nscore = 0.8\n',
                    'LABEL = "materially-new"\nscore = 0.20\n',
                )
            )

        def propose(self, prompt: str) -> CandidateProposal:
            self.prompts.append(prompt)
            return CandidateProposal(next(self.codes), "candidate formulation")

    proposer = SequencedProposer()
    loop.proposer = proposer

    first = loop.run_once()
    second = loop.run_once()
    records = loop._read_runs()

    assert first["metric"] == 0.80
    assert second["metric"] == 0.20
    assert loop.status_summary()["run_count"] == 2
    assert len(records) == 2
    assert len(second["proposal_attempts"]) == 2
    duplicate, admitted = second["proposal_attempts"]
    assert duplicate["semantic_hash"] == first["semantic_hash"]
    assert duplicate["candidate_hash"] != first["candidate_hash"]
    assert duplicate["candidate_path"] is None
    assert "semantically identical" in duplicate["error"]
    assert admitted["semantic_hash"] == second["semantic_hash"]
    assert admitted["error"] is None
    assert "Previously evaluated candidate logic" in proposer.prompts[1]
    assert "Candidate novelty check failed" in proposer.prompts[2]


def test_options_semantic_hash_ignores_metadata_but_tracks_research_logic() -> None:
    baseline = """
from research.backtest import call_debit_spread
ENTRY_HM = (12, 30)
LABEL = "baseline"
def signal(session, entry_minute):
    return session.ret_from_open(entry_minute) <= -0.5
def structure(session, entry_price):
    return call_debit_spread(entry_price, width=2.0)
"""
    renamed = baseline.replace('LABEL = "baseline"', 'LABEL = "renamed"')
    renamed = '"presentation docs"\n# presentation comment\n' + renamed
    changed_signal = baseline.replace("<= -0.5", "<= -0.75")
    changed_structure = baseline.replace("width=2.0", "width=3.0")

    fingerprint = _candidate_semantic_hash(baseline)

    assert _candidate_semantic_hash(renamed) == fingerprint
    assert _candidate_semantic_hash(changed_signal) != fingerprint
    assert _candidate_semantic_hash(changed_structure) != fingerprint


def test_three_semantic_duplicates_stop_admission_without_spending_budget(
    tmp_path: Path,
) -> None:
    loop, _ = _loop(tmp_path, max_attempts=3)

    class DuplicateProposer:
        def __init__(self) -> None:
            self.codes = iter(
                (
                    'LABEL = "baseline"\nscore = 0.80\n',
                    'LABEL = "rename"\nscore = 0.8\n',
                    '"module docs"\nLABEL = "docs"\nscore = 0.800\n',
                    '# formatting only\nLABEL: str = "typed"\nscore=0.80\n',
                )
            )

        def propose(self, _prompt: str) -> CandidateProposal:
            return CandidateProposal(next(self.codes), "changed rationale only")

    loop.proposer = DuplicateProposer()
    first = loop.run_once()

    with pytest.raises(LabLoopError, match="Candidate admission failed"):
        loop.run_once()

    summary = loop.status_summary()
    admissions = loop._read_admissions()
    assert first["run"] == 1
    assert summary["run_count"] == 1
    assert summary["status"] == "failed"
    assert summary["admission_failure_count"] == 1
    assert len(loop._read_runs()) == 1
    assert len(admissions) == 1
    assert len(admissions[0]["attempts"]) == 3
    assert all(
        attempt["semantic_hash"] == first["semantic_hash"]
        for attempt in admissions[0]["attempts"]
    )
    assert all(
        "semantically identical" in attempt["error"]
        for attempt in admissions[0]["attempts"]
    )


@pytest.mark.parametrize(
    "source,blocked",
    [
        ("import subprocess\nscore = 1\n", "import is forbidden"),
        ("from alpaca.trading import TradingClient\nscore = 1\n", "import is forbidden"),
        (
            "from research.backtest import SessionStore\nscore = 1\n",
            "research.backtest import is forbidden",
        ),
        (
            "from research.backtest import AlpacaOptionData as call_debit_spread\nscore = 1\n",
            "research.backtest import is forbidden",
        ),
        (
            "from research.backtest import Structure\nscore = 1\n",
            "research.backtest import is forbidden",
        ),
        ("import research.backtest\nscore = 1\n", "import is forbidden"),
        ("score = open('secret').read()\n", "call is forbidden"),
        ("reader = open\nscore = reader('secret').read()\n", "name is forbidden: open"),
        (
            "read_attr = getattr\nscore = read_attr(session, '_bars')\n",
            "name is forbidden: getattr",
        ),
        (
            "from statistics import __builtins__ as b\nscore = b['open']('secret')\n",
            "private import is forbidden",
        ),
        ("score = session._bars\n", "private attribute access is forbidden"),
    ],
)
def test_generated_candidate_escape_capabilities_are_rejected(
    tmp_path: Path, source: str, blocked: str
):
    class UnsafeProposer:
        def propose(self, _prompt: str) -> CandidateProposal:
            return CandidateProposal(source, "unsafe")

    loop, _ = _loop(tmp_path, max_attempts=1)
    loop.proposer = UnsafeProposer()

    with pytest.raises(LabLoopError, match=blocked):
        loop.run_once()

    summary = loop.status_summary()
    assert summary["status"] == "failed"
    assert summary["run_count"] == 0
    assert summary["admission_failure_count"] == 1
    admissions = [
        json.loads(line)
        for line in (tmp_path / "state" / "admissions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert len(admissions) == 1
    assert len(admissions[0]["attempts"]) == 3
    assert all(blocked in attempt["error"] for attempt in admissions[0]["attempts"])


def _diagnostic_policy(**overrides):
    defaults = dict(
        command=("python", "-c", "pass"),
        metric_pattern=r"score=(?P<value>[-0-9.]+)",
        evaluator_version="diagnostics-test-v1",
        metric_name="robust_portfolio_score",
        direction="maximize",
        max_attempts=24,
        min_attempts=12,
        target_metric=0.75,
    )
    defaults.update(overrides)
    return ValidatorPolicy(**defaults)


def _validation(payload: dict, *, stderr: str = "") -> dict:
    return {
        "returncode": 0,
        "stderr_tail": stderr,
        "combined_output": json.dumps(payload, sort_keys=True) + "\nscore=-0.250001\n",
    }


def test_validator_diagnostics_forwards_numeric_payload():
    rendered = _validator_diagnostics(
        json.dumps(
            {
                "status": "rejected",
                "split": "discovery",
                "label": "cross-market selective imbalance",
                "score": -0.250001,
                "net_sharpe": 1.689151,
                "net_trade_mean_bps": -0.887988,
                "gates": {"symbol_concentration": False, "minimum_trades": True},
                "symbol_counts": {"NVDA": 50, "AMZN": 22},
            },
            sort_keys=True,
        )
    )
    assert "net_sharpe=1.689151" in rendered
    assert "net_trade_mean_bps=-0.887988" in rendered
    assert "symbol_counts={AMZN:22,NVDA:50}" in rendered
    # Already carried elsewhere in the feedback line, or a pure label.
    for skipped in ("score=", "split=", "status=", "label="):
        assert skipped not in rendered
    # Boolean gate maps are reported by name through stderr, not duplicated here.
    assert "gates=" not in rendered


def test_validator_diagnostics_tolerates_absent_payload():
    assert _validator_diagnostics("score=1.0\nnot json\n") == ""
    assert _validator_diagnostics("") == ""


def test_feedback_carries_evidence_and_flags_held_qualifier():
    policy = _diagnostic_policy()
    validation = _validation(
        {"net_sharpe": 1.689151, "max_symbol_concentration": 0.367647},
        stderr="robustness_gates_failed=symbol_concentration",
    )
    feedback = _feedback(policy, 0.9, validation, run_number=7, accepted=False)
    # The underlying measurements must reach the proposer, not just the scalar.
    assert "net_sharpe=1.689151" in feedback
    assert "max_symbol_concentration=0.367647" in feedback
    # A qualifying candidate held only by attempt ordering must say so, or the
    # proposer reads a rejection and abandons a mechanism that already passed.
    assert "already meets the robust_portfolio_score target" in feedback
    assert "do not abandon it" in feedback
    assert "robustness_gates_failed=symbol_concentration" in feedback


def test_feedback_omits_qualifier_note_when_target_missed():
    policy = _diagnostic_policy()
    feedback = _feedback(policy, -0.250001, _validation({"net_sharpe": 0.1}), 7, False)
    assert "waiting for min_attempts 12" in feedback
    assert "already meets" not in feedback


def test_meets_target_is_independent_of_attempt_ordering():
    maximize = _diagnostic_policy()
    assert _meets_target(maximize, 0.75) is True
    assert _meets_target(maximize, 0.74) is False
    minimize = _diagnostic_policy(direction="minimize", target_metric=0.2)
    assert _meets_target(minimize, 0.1) is True
    assert _meets_target(minimize, 0.3) is False
    assert _meets_target(_diagnostic_policy(target_metric=None), -99.0) is True
    assert _meets_target(maximize, None) is False


def test_is_accepted_still_defers_to_min_attempts():
    policy = _diagnostic_policy()
    # Meeting the target early must not accept; only the note in feedback changes.
    assert _is_accepted(policy, 0.9, 0, run_number=7) is False
    assert _is_accepted(policy, 0.9, 0, run_number=12) is True
    assert _is_accepted(policy, 0.9, 1, run_number=12) is False


def test_validator_crash_is_retried_without_spending_budget(tmp_path: Path) -> None:
    """A candidate that crashes the validator was never evaluated, so it costs
    feedback, not a trial — same class as a novelty rejection.

    Pins the 2026-08-16 budget leak: three-consecutive-down-days burned 17 of
    18 trials on invented ret_Nd feature names, each rejected by the validator
    at full trial price.
    """
    validator = tmp_path / "validator.py"
    validator.write_text(
        "\n".join(
            [
                "import sys",
                "from pathlib import Path",
                "candidate = Path(sys.argv[1]).read_text(encoding='utf-8')",
                "if 'ret_99d' in candidate:",
                "    print('{\"status\": \"failed\", \"error\": \"unknown feature ret_99d; available features are: ret_1d\"}')",
                "    sys.exit(2)",
                "print('score=0.5')",
            ]
        ),
        encoding="utf-8",
    )
    policy = ValidatorPolicy(
        command=(sys.executable, str(validator), "{candidate}"),
        metric_pattern=r"score=(?P<value>[0-9.]+)",
        evaluator_version="crash-test-v1",
        max_attempts=2,
        min_attempts=1,
    )
    codes = iter(
        (
            "LABEL = 'a'\n"
            "def signal(symbol, features):\n"
            "    return 1 if features['ret_99d'] > 0 else 0\n",
            "LABEL = 'b'\n"
            "def signal(symbol, features):\n"
            "    return 1 if features['ret_1d'] > 0 else 0\n",
        )
    )
    prompts: list[str] = []

    class CrashThenRepairProposer:
        def propose(self, prompt: str) -> CandidateProposal:
            prompts.append(prompt)
            return CandidateProposal(code=next(codes), rationale="scripted")

    loop = ResearchLoop(
        MissionSpec("Crash once, then repair.", policy, "crash-test"),
        state_dir=tmp_path / "state",
        workspace_dir=tmp_path / "workspace",
        proposer=CrashThenRepairProposer(),
    )
    loop.init()
    record = loop.run_once()

    assert record["run"] == 1
    assert record["metric"] == 0.5
    assert len(record["proposal_attempts"]) == 2
    assert record["proposal_attempts"][0]["error"].startswith(
        "Validator rejected candidate"
    )
    assert record["proposal_attempts"][1]["error"] is None
    assert loop.status_summary()["run_count"] == 1
    # The retry sees the validator's reason AND the code it just wrote.
    assert "unknown feature ret_99d" in prompts[1]
    assert "The rejected proposal was:" in prompts[1]


def test_persistent_validator_crash_fails_admission_not_budget(tmp_path: Path) -> None:
    """Unrepairable crashes hit the admission cap with zero trials spent."""
    validator = tmp_path / "validator.py"
    validator.write_text(
        "\n".join(
            [
                "import sys",
                "print('{\"status\": \"failed\", \"error\": \"candidate signal raised\"}')",
                "sys.exit(2)",
            ]
        ),
        encoding="utf-8",
    )
    policy = ValidatorPolicy(
        command=(sys.executable, str(validator), "{candidate}"),
        metric_pattern=r"score=(?P<value>[0-9.]+)",
        evaluator_version="crash-bound-test-v1",
        max_attempts=2,
        min_attempts=1,
    )

    class AlwaysCrashProposer:
        def __init__(self) -> None:
            self.calls = 0

        def propose(self, _prompt: str) -> CandidateProposal:
            self.calls += 1
            # Distinct thresholds keep each formulation novel, so every attempt
            # reaches the validator and crashes there.
            code = (
                "LABEL = 'x'\n"
                "def signal(symbol, features):\n"
                f"    return 1 if features['ret_1d'] > 0.0{self.calls} else 0\n"
            )
            return CandidateProposal(code=code, rationale=f"try {self.calls}")

    loop = ResearchLoop(
        MissionSpec("Crash forever.", policy, "crash-bound-test"),
        state_dir=tmp_path / "state",
        workspace_dir=tmp_path / "workspace",
        proposer=AlwaysCrashProposer(),
    )
    loop.init()

    with pytest.raises(LabLoopError, match="Candidate admission failed"):
        loop.run_once()

    summary = loop.status_summary()
    assert summary["status"] == "failed"
    assert summary["run_count"] == 0
    assert summary["admission_failure_count"] == 1
    assert loop._read_runs() == []
    admissions = loop._read_admissions()
    assert len(admissions) == 1
    assert len(admissions[0]["attempts"]) == 3
    assert all(
        "Validator rejected candidate" in attempt["error"]
        for attempt in admissions[0]["attempts"]
    )


def test_retry_prompt_shows_the_proposal_it_just_rejected(tmp_path: Path, monkeypatch) -> None:
    """Novelty retries must see their own rejected code, not just the error."""

    validator = tmp_path / "validator.py"
    validator.write_text("print('score=1.0')\n", encoding="utf-8")
    policy = ValidatorPolicy(
        command=(sys.executable, str(validator), "{candidate}"),
        metric_pattern=r"score=(?P<value>[0-9.]+)",
        evaluator_version="retry-test-v1",
        max_attempts=2,
        min_attempts=1,
        # Unreachable, so run 1 is evaluated but never accepted and run 2 can
        # exercise the novelty retry path.
        target_metric=5.0,
    )
    identical = "LABEL = 'x'\ndef signal(symbol, features):\n    return 1\n"
    prompts: list[str] = []

    class RepeatingProposer:
        def propose(self, prompt: str) -> CandidateProposal:
            prompts.append(prompt)
            return CandidateProposal(code=identical, rationale="same each time")

    loop = ResearchLoop(
        MissionSpec("Repeat the same logic.", policy, "retry-test"),
        state_dir=tmp_path / "state",
        workspace_dir=tmp_path / "workspace",
        proposer=RepeatingProposer(),
    )
    loop.init()
    loop.run_once()
    with pytest.raises(LabLoopError):
        loop.run_once()

    retries = [prompt for prompt in prompts if "did not pass candidate admission" in prompt]
    assert retries, "expected at least one admission retry prompt"
    assert "The rejected proposal was:" in retries[0]
    assert "def signal(symbol, features)" in retries[0].split("The rejected proposal was:", 1)[1]


def test_admission_failure_resumes_within_bound(tmp_path: Path) -> None:
    """One mode-collapsed formulation round must not kill a whole campaign."""

    validator = tmp_path / "validator.py"
    validator.write_text("print('score=1.0')\n", encoding="utf-8")
    policy = ValidatorPolicy(
        command=(sys.executable, str(validator), "{candidate}"),
        metric_pattern=r"score=(?P<value>[0-9.]+)",
        evaluator_version="resume-test-v1",
        max_attempts=3,
        min_attempts=1,
    )
    template = "LABEL = 'x'\ndef signal(symbol, features):\n    return {body}\n"
    # proposals: run1 novel; then 3 duplicates (admission failure); then novel again
    codes = [template.format(body="1")] + [template.format(body="1")] * 3 + [
        template.format(body="-1")
    ]

    class ScriptedProposer:
        def __init__(self) -> None:
            self.calls = 0

        def propose(self, prompt: str) -> CandidateProposal:
            code = codes[min(self.calls, len(codes) - 1)]
            self.calls += 1
            return CandidateProposal(code=code, rationale=f"call {self.calls}")

    loop = ResearchLoop(
        MissionSpec("Recover from one bad round.", policy, "resume-test"),
        state_dir=tmp_path / "state",
        workspace_dir=tmp_path / "workspace",
        proposer=ScriptedProposer(),
    )
    loop.init()
    summary = loop.run_until_complete()
    # run 1 accepted immediately (min_attempts=1, no target): complete after 1
    assert summary["status"] == "complete"


def test_admission_failures_beyond_bound_still_fail(tmp_path: Path) -> None:
    validator = tmp_path / "validator.py"
    validator.write_text("print('score=1.0')\n", encoding="utf-8")
    policy = ValidatorPolicy(
        command=(sys.executable, str(validator), "{candidate}"),
        metric_pattern=r"score=(?P<value>[0-9.]+)",
        evaluator_version="resume-bound-test-v1",
        max_attempts=4,
        min_attempts=4,
    )
    same = "LABEL = 'x'\ndef signal(symbol, features):\n    return 1\n"

    class StuckProposer:
        def propose(self, prompt: str) -> CandidateProposal:
            return CandidateProposal(code=same, rationale="stuck")

    loop = ResearchLoop(
        MissionSpec("Stay stuck forever.", policy, "resume-bound-test"),
        state_dir=tmp_path / "state",
        workspace_dir=tmp_path / "workspace",
        proposer=StuckProposer(),
    )
    loop.init()
    with pytest.raises(CandidateAdmissionError):
        loop.run_until_complete()
    state = json.loads((tmp_path / "state" / "state.json").read_text(encoding="utf-8"))
    assert state["admission_failure_count"] == ResearchLoop.MAX_ADMISSION_FAILURES


def test_prompt_steers_toward_unexplored_features(tmp_path: Path) -> None:
    validator = tmp_path / "validator.py"
    validator.write_text("print('score=0.1')\n", encoding="utf-8")
    policy = ValidatorPolicy(
        command=(sys.executable, str(validator), "{candidate}"),
        metric_pattern=r"score=(?P<value>[0-9.]+)",
        evaluator_version="steering-test-v1",
        max_attempts=5,
        min_attempts=5,
        target_metric=99.0,
    )
    first = (
        "LABEL = 'a'\n"
        "def signal(symbol, features):\n"
        "    return 1 if features['ret_1d'] < -0.01 and features['range_pos'] < 0.2 else 0\n"
    )

    class OneShotProposer:
        def propose(self, prompt: str) -> CandidateProposal:
            return CandidateProposal(code=first, rationale="first")

    loop = ResearchLoop(
        MissionSpec("Steer later proposals.", policy, "steering-test"),
        state_dir=tmp_path / "state",
        workspace_dir=tmp_path / "workspace",
        proposer=OneShotProposer(),
    )
    loop.init()
    loop.run_once()
    prompt = loop.build_prompt()
    assert "already explored by earlier candidates: range_pos, ret_1d" in prompt
    assert "NOT in that list" in prompt


class SequenceProposer:
    """Emits a fixed sequence of scores, each a semantically distinct candidate."""

    def __init__(self, scores: list[float]) -> None:
        self.scores = list(scores)
        self.calls = 0

    def propose(self, prompt: str) -> CandidateProposal:
        score = self.scores[min(self.calls, len(self.scores) - 1)]
        self.calls += 1
        return CandidateProposal(
            code=f"score = {score:.2f}\n",
            rationale=f"sequence candidate {self.calls} score={score:.2f}",
        )


def test_held_qualifier_is_accepted_once_min_attempts_is_reached(tmp_path: Path):
    # A qualifying candidate found before min_attempts is scored, held by
    # ordering, and the novelty check forbids re-proposing its logic -- so it
    # could never be accepted and the campaign lost it (2026-08-24: AAPL run 1
    # at 1.731, msft-0dte-call-credit-v1 run 3 at 1.054). Once min_attempts is
    # satisfied, the held evaluation is accepted as it stands.
    validator = tmp_path / "validator.py"
    validator.write_text(
        "\n".join(
            [
                "import re",
                "import sys",
                "from pathlib import Path",
                "candidate = Path(sys.argv[1]).read_text(encoding='utf-8')",
                r"value = re.search(r'score\s*=\s*([0-9.]+)', candidate).group(1)",
                "print(f'metric={float(value):.2f}')",
            ]
        ),
        encoding="utf-8",
    )
    policy = ValidatorPolicy(
        command=(sys.executable, str(validator), "{candidate}"),
        metric_pattern=r"metric=(?P<value>[0-9.]+)",
        evaluator_version="offline-test-v1",
        metric_name="score",
        direction="minimize",
        max_attempts=5,
        min_attempts=3,
        target_metric=0.20,
    )
    # Run 1 meets the target (0.20 <= 0.20) but lands before min_attempts;
    # runs 2 and 3 miss. At run 3 the ordering constraint is satisfied and
    # the held run-1 evaluation must be accepted without spending runs 4-5.
    proposer = SequenceProposer([0.20, 0.80, 0.70])
    loop = ResearchLoop(
        MissionSpec("Find an offline parameter candidate.", policy, "held-test"),
        state_dir=tmp_path / "state",
        workspace_dir=tmp_path / "workspace",
        proposer=proposer,
        base_env={"PATH": "test-path"},
    )

    summary = loop.run_until_complete()
    records = [
        json.loads(line)
        for line in (tmp_path / "state" / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert summary["status"] == "complete"
    assert summary["accepted_run"] == 1
    assert len(records) == 3  # runs 4-5 were never spent
    state = json.loads((tmp_path / "state" / "state.json").read_text(encoding="utf-8"))
    assert state["accepted_from_hold"] is True
    assert "held only by min_attempts ordering" in state["last_feedback"]
    result = json.loads((tmp_path / "state" / "result.json").read_text(encoding="utf-8"))
    assert result["accepted_run"] == 1
    assert result["metric"] == 0.20
    report = (tmp_path / "state" / "shutdown-report.md").read_text(encoding="utf-8")
    assert "held qualifier from run 1" in report


def test_no_retro_acceptance_when_nothing_ever_met_the_target(tmp_path: Path):
    validator = tmp_path / "validator.py"
    validator.write_text(
        "\n".join(
            [
                "import re",
                "import sys",
                "from pathlib import Path",
                "candidate = Path(sys.argv[1]).read_text(encoding='utf-8')",
                r"value = re.search(r'score\s*=\s*([0-9.]+)', candidate).group(1)",
                "print(f'metric={float(value):.2f}')",
            ]
        ),
        encoding="utf-8",
    )
    policy = ValidatorPolicy(
        command=(sys.executable, str(validator), "{candidate}"),
        metric_pattern=r"metric=(?P<value>[0-9.]+)",
        evaluator_version="offline-test-v1",
        metric_name="score",
        direction="minimize",
        max_attempts=3,
        min_attempts=2,
        target_metric=0.10,
    )
    proposer = SequenceProposer([0.80, 0.70, 0.60])
    loop = ResearchLoop(
        MissionSpec("Find an offline parameter candidate.", policy, "no-hold-test"),
        state_dir=tmp_path / "state",
        workspace_dir=tmp_path / "workspace",
        proposer=proposer,
        base_env={"PATH": "test-path"},
    )

    summary = loop.run_until_complete()
    assert summary["status"] == "paused"
    assert summary["accepted_run"] is None
    state = json.loads((tmp_path / "state" / "state.json").read_text(encoding="utf-8"))
    assert state["paused_reason"] == "budget_exhausted"
    assert "accepted_from_hold" not in state
