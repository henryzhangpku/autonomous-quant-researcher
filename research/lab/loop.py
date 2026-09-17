"""Mission-driven candidate generation and deterministic validation loop."""

from __future__ import annotations

import ast
from contextlib import contextmanager
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .memory import CandidateMemory
from .mission import MissionSpec, ValidatorPolicy
from .proposer import CandidateProposal, Proposer, ProposalError


class LabLoopError(RuntimeError):
    """A lab loop configuration, safety, proposal, or validation failure."""


class CandidateAdmissionError(LabLoopError):
    """Raised when no contract-correct candidate can be formed for a run."""


BROKER_ENV_PREFIXES = (
    "ALPACA_",
    "APCA_",
    "ETORO_",
    "PUBLIC_",
    "TASTY_",
    "TASTYTRADE_",
    "WEBULL_",
    "KALSHI_",
    "MASSIVE_",
    "POLYGON_",
    "ROBINHOOD_",
)
BROKER_ENV_KEYS = {
    "OPENAI_API_KEY",
    "QS_AGENT_TOKEN",
    "QS_BROKERAGE_AGENT_TOKEN",
    "QS_BROKERAGE_URL",
    "AUTOQUANT_LAB_API_KEY",
}

ALLOWED_CANDIDATE_IMPORTS = {"math", "statistics"}
ALLOWED_BACKTEST_IMPORTS = {
    "call_debit_spread",
    "long_put",
    "put_credit_spread",
    "spx_call_credit_spread",
    "spx_call_debit_spread",
    "spx_put_credit_spread",
    "spx_put_debit_spread",
}
FORBIDDEN_CANDIDATE_CALLS = {
    "__import__",
    "breakpoint",
    "compile",
    "delattr",
    "dir",
    "eval",
    "exec",
    "getattr",
    "globals",
    "input",
    "locals",
    "open",
    "setattr",
    "vars",
}
MAX_PROPOSAL_ATTEMPTS = 3
PRIOR_CANDIDATE_LIMIT = 5
# Already carried verbatim elsewhere in the feedback line, or pure labels.
_DIAGNOSTIC_SKIP_KEYS = {"score", "split", "status", "label"}
_DIAGNOSTIC_MAX_CHARS = 900


class ResearchLoop:
    """Append-only NLP-to-candidate-to-validator loop."""

    def __init__(
        self,
        spec: MissionSpec,
        *,
        state_dir: Path,
        workspace_dir: Path,
        proposer: Proposer,
        base_env: dict[str, str] | None = None,
        memory: CandidateMemory | None = None,
    ) -> None:
        self.spec = spec
        self.state_dir = state_dir.resolve()
        self.workspace_dir = workspace_dir.resolve()
        self.proposer = proposer
        self.base_env = dict(os.environ if base_env is None else base_env)
        self.memory = memory if memory is not None else CandidateMemory()
        self.state_path = self.state_dir / "state.json"
        self.runs_path = self.state_dir / "runs.jsonl"
        self.admissions_path = self.state_dir / "admissions.jsonl"
        self.result_path = self.state_dir / "result.json"
        self.shutdown_report_path = self.state_dir / "shutdown-report.md"
        self.run_lock_path = self.state_dir / "run.lock"

    def init(self) -> dict[str, Any]:
        self._ensure_workspace_boundary()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        if self.state_path.exists():
            state = self._read_state()
            expected_policy = _json_hash(asdict(self.spec.policy))
            expected_mission = _text_hash(self.spec.mission)
            if state.get("evaluator_version") != self.spec.policy.evaluator_version:
                raise LabLoopError(
                    "Evaluator contract drifted after initialization; start a new "
                    "research session instead of mixing incompatible trials."
                )
            if state.get("policy_hash") != expected_policy or state.get("mission_hash") != expected_mission:
                raise LabLoopError(
                    "Mission or validator policy drifted after initialization; "
                    "start a new named run instead of rewriting trial history."
                )
            return state
        state = {
            "name": self.spec.name,
            "status": "ready",
            "created_at": _utc_now(),
            "run_count": 0,
            "best_metric": None,
            "best_candidate_hash": None,
            "best_run": None,
            "accepted_run": None,
            "admission_failure_count": 0,
            "evaluator_version": self.spec.policy.evaluator_version,
            "policy_hash": _json_hash(asdict(self.spec.policy)),
            "mission_hash": _text_hash(self.spec.mission),
        }
        _write_json_atomic(self.state_path, state)
        self.runs_path.touch()
        self.admissions_path.touch()
        return state

    def pause(self) -> dict[str, Any]:
        state = self._read_state()
        if state.get("status") == "complete":
            raise LabLoopError(f"Completed loop cannot be paused: {self.spec.name}")
        state["status"] = "paused"
        state["paused_reason"] = "user"
        state["updated_at"] = _utc_now()
        _write_json_atomic(self.state_path, state)
        self._write_shutdown_report(state, reason="user_pause")
        return state

    def resume(self) -> dict[str, Any]:
        state = self._read_state()
        if state.get("status") == "complete":
            raise LabLoopError(f"Completed loop cannot be resumed: {self.spec.name}")
        state["status"] = "ready"
        state["updated_at"] = _utc_now()
        _write_json_atomic(self.state_path, state)
        return state

    def run_once(self) -> dict[str, Any]:
        with self._run_lock():
            return self._run_once_unlocked()

    def _run_once_unlocked(self) -> dict[str, Any]:
        self.init()
        state = self._read_state()
        if state.get("status") == "complete":
            raise LabLoopError(f"Loop is complete: {self.spec.name}")
        if state.get("status") == "paused":
            raise LabLoopError(f"Loop is paused: {self.spec.name}")
        if state.get("status") == "failed":
            raise LabLoopError(f"Loop must be resumed after a candidate admission failure: {self.spec.name}")
        run_number = int(state.get("run_count", 0)) + 1
        if run_number > self.spec.policy.max_attempts:
            self._mark_budget_exhausted(state)
            raise LabLoopError(f"Attempt budget exhausted ({self.spec.policy.max_attempts}).")

        prompt = self.build_prompt()
        proposal: CandidateProposal | None = None
        candidate_hash: str | None = None
        semantic_hash: str | None = None
        candidate_path: Path | None = None
        rationale_path: Path | None = None
        preflight: dict[str, Any] | None = None
        proposal_attempts: list[dict[str, Any]] = []
        validation: dict[str, Any] | None = None
        metric: float | None = None
        evaluated_semantics = self._evaluated_semantic_hashes()
        attempted_semantics: set[str] = set()
        started = time.monotonic()
        try:
            for proposal_number in range(1, MAX_PROPOSAL_ATTEMPTS + 1):
                proposal = None
                candidate_hash = None
                semantic_hash = None
                candidate_path = None
                rationale_path = None
                preflight = None
                try:
                    proposal = self.proposer.propose(prompt)
                    _validate_candidate_source(
                        proposal.code, self.spec.policy.allowed_backtest_imports
                    )
                    candidate_hash = _text_hash(proposal.code)
                    semantic_hash = _candidate_semantic_hash(proposal.code)
                    if (
                        semantic_hash in evaluated_semantics
                        or semantic_hash in attempted_semantics
                    ):
                        raise LabLoopError(
                            "Candidate novelty check failed: the trading logic is "
                            "semantically identical to an earlier formulation. "
                            "Materially change ENTRY_HM, signal logic, helper logic, "
                            "or options structure; labels, comments, docstrings, "
                            "formatting, and rationale do not count as novelty."
                        )
                    remembered = self.memory.same_mission_conflict(
                        semantic_hash, _text_hash(self.spec.mission)
                    )
                    if remembered is not None:
                        raise LabLoopError(
                            "Lab memory: this exact trading logic was already "
                            f"evaluated for this mission in campaign "
                            f"'{remembered.get('mission_name')}' run "
                            f"{remembered.get('run')} (metric "
                            f"{remembered.get('metric')}, accepted="
                            f"{remembered.get('accepted')}). Propose materially "
                            "different logic instead of re-testing it."
                        )
                    attempted_semantics.add(semantic_hash)
                    candidate_path, rationale_path = self._write_candidate(
                        candidate_hash, proposal
                    )
                    preflight = self._run_candidate_preflight(candidate_path)
                    if preflight is not None and preflight["returncode"] != 0:
                        raise LabLoopError(
                            "Candidate contract preflight failed: "
                            + _validation_failure_detail(preflight)
                        )
                    # A trial is spent only when the candidate was actually
                    # evaluated. The validator exits 0 whenever it scored the
                    # candidate (failed robustness gates still print a metric);
                    # non-zero means the candidate crashed or broke the
                    # contract, so NOTHING was learned about the market. That
                    # is the same class as a novelty rejection — feedback, not
                    # budget. (three-consecutive-down-days, 2026-08-16: 17 of
                    # 18 trials burned on invented ret_Nd feature names.)
                    validation = self._run_validator(candidate_path)
                    if validation["returncode"] != 0:
                        raise LabLoopError(
                            "Validator rejected candidate: "
                            + _validation_failure_detail(validation)
                        )
                    metric = _extract_metric(
                        self.spec.policy, validation["combined_output"]
                    )
                except (LabLoopError, ProposalError, subprocess.TimeoutExpired) as exc:
                    proposal_attempts.append(
                        _proposal_attempt_record(
                            proposal_number,
                            prompt,
                            proposal,
                            candidate_hash,
                            semantic_hash,
                            candidate_path,
                            self.workspace_dir,
                            preflight,
                            str(exc),
                        )
                    )
                    if proposal_number >= MAX_PROPOSAL_ATTEMPTS:
                        self._record_candidate_admission_failure(
                            state,
                            prospective_run=run_number,
                            attempts=proposal_attempts,
                            error=str(exc),
                        )
                        raise CandidateAdmissionError(
                            "Candidate admission failed after "
                            f"{MAX_PROPOSAL_ATTEMPTS} recorded formulations: {exc}"
                        ) from exc
                    # Show the retry what it just wrote. Prior-candidate logic
                    # only covers evaluated runs, so without this the model is
                    # asked for something "materially distinct" from code it
                    # cannot see, and a weak proposer simply rewrites it until
                    # admission fails the run.
                    rejected = getattr(proposal, "code", None)
                    detail = str(exc)
                    if rejected:
                        detail = f"{detail}\n\nThe rejected proposal was:\n{rejected}"
                    prompt = self.build_prompt(proposal_feedback=detail)
                    continue
                proposal_attempts.append(
                    _proposal_attempt_record(
                        proposal_number,
                        prompt,
                        proposal,
                        candidate_hash,
                        semantic_hash,
                        candidate_path,
                        self.workspace_dir,
                        preflight,
                        None,
                    )
                )
                break

            if candidate_path is None or validation is None or metric is None:
                raise LabLoopError("Candidate proposal did not produce a validation file.")
            accepted = _is_accepted(self.spec.policy, metric, validation["returncode"], run_number)
            feedback = _feedback(self.spec.policy, metric, validation, run_number, accepted)
            error: str | None = None
        except CandidateAdmissionError:
            raise
        except (LabLoopError, ProposalError, subprocess.TimeoutExpired) as exc:
            if validation is None:
                validation = _empty_validation(exc)
            metric = None
            accepted = False
            feedback = str(exc)
            error = str(exc)

        duration = time.monotonic() - started
        previous_best = state.get("best_metric")
        validator_success = validation["returncode"] == 0 and metric is not None
        improved = validator_success and (
            previous_best is None
            or (
                self.spec.policy.direction == "minimize"
                and metric < float(previous_best)
            )
            or (
                self.spec.policy.direction == "maximize"
                and metric > float(previous_best)
            )
        )
        record = {
            "run": run_number,
            "timestamp": _utc_now(),
            "duration_seconds": round(duration, 3),
            "candidate_hash": candidate_hash,
            "semantic_hash": semantic_hash,
            "rationale_hash": _text_hash(proposal.rationale) if proposal is not None else None,
            "proposal_hash": _candidate_hash(proposal) if proposal is not None else None,
            "proposal_attempts": proposal_attempts,
            "candidate_path": _relative_to(candidate_path, self.workspace_dir)
            if candidate_path is not None
            else None,
            "rationale_path": _relative_to(rationale_path, self.workspace_dir)
            if rationale_path is not None
            else None,
            "prompt_hash": _text_hash(prompt),
            "metric_name": self.spec.policy.metric_name,
            "metric": metric,
            "accepted": accepted,
            "improved": improved,
            "feedback": feedback,
            "error": error,
            "validator": validation,
        }
        self._append_record(record)
        if semantic_hash is not None:
            # Feed the lab-wide memory so a future campaign of this mission
            # never re-spends a scientific trial on this exact logic.
            try:
                self.memory.record(
                    semantic_hash=semantic_hash,
                    mission_hash=_text_hash(self.spec.mission),
                    mission_name=self.spec.name,
                    run=run_number,
                    metric=metric,
                    accepted=accepted,
                    label=self.spec.policy.metric_name,
                )
            except OSError:
                pass  # memory is an accelerator, never a reason to lose a run

        state.update(
            {
                "status": "ready",
                "updated_at": _utc_now(),
                "run_count": run_number,
                "last_run": run_number,
                "last_feedback": feedback,
                "last_metric": metric,
                "last_accepted": accepted,
            }
        )
        if improved:
            state["best_metric"] = metric
            state["best_candidate_hash"] = candidate_hash
            state["best_run"] = run_number
        held: dict[str, Any] | None = None
        if not accepted and run_number >= self.spec.policy.min_attempts:
            held = self._best_held_qualifier()
        if accepted:
            state["status"] = "complete"
            state["accepted_run"] = run_number
            self._write_result(record, state)
        elif held is not None:
            # A qualifying candidate found before min_attempts was scored,
            # held by ordering, and -- because the novelty check forbids
            # re-proposing its logic -- could never be re-submitted. Two
            # campaigns lost target-passing formulations exactly this way
            # (2026-08-24: does-aapl-lag run 1 at 1.731 died on admission
            # collapse; msft-0dte-call-credit-v1 run 3 at 1.054 exhausted its
            # budget). The held evaluation was made by the frozen validator
            # and is accepted as it stands once the ordering constraint is
            # satisfied; nothing is re-run and no new trial is spent.
            state["status"] = "complete"
            state["accepted_run"] = held["run"]
            state["accepted_from_hold"] = True
            state["last_feedback"] = (
                f"accepted from hold: run {held['run']} already met the "
                f"{self.spec.policy.metric_name} target "
                f"({held.get('metric')}) and was held only by min_attempts "
                "ordering"
            )
            self._write_result(held, state)
        _write_json_atomic(self.state_path, state)
        if accepted:
            self._write_shutdown_report(state, reason="accepted")
        elif held is not None:
            self._write_shutdown_report(
                state,
                reason=f"accepted (held qualifier from run {held['run']})",
            )
        return record

    def _best_held_qualifier(self) -> dict[str, Any] | None:
        """The best earlier trial that met the target but was held by ordering.

        Eligible records were scored by the frozen validator (returncode 0),
        met the target metric, and were not accepted -- which, given
        `_is_accepted`, can only mean they landed before min_attempts.
        """

        policy = self.spec.policy
        best: dict[str, Any] | None = None
        for record in self._read_runs():
            metric = record.get("metric")
            if record.get("accepted") or not isinstance(metric, (int, float)):
                continue
            validator = record.get("validator") or {}
            if validator.get("returncode") != 0:
                continue
            if not _meets_target(policy, float(metric)):
                continue
            if (
                best is None
                or (policy.direction == "minimize" and float(metric) < float(best["metric"]))
                or (policy.direction == "maximize" and float(metric) > float(best["metric"]))
            ):
                best = record
        return best

    # A weak proposer occasionally exhausts its formulation retries; every such
    # failure is already preserved in the admissions ledger. Ending the whole
    # campaign on the first one converts a bad five minutes into a dead run.
    # Bound them instead: repeated failures still stop the loop.
    MAX_ADMISSION_FAILURES = 8
    # Sampling heat added per admission failure, and its ceiling. Novelty
    # collapse is a low-temperature failure mode: a 7B proposer at 0.2
    # rewrites one formulation forever (overnight-spx, 2026-08-13, 9 admission
    # failures with the same semantic hash). Retrying at the SAME temperature
    # converts the resume budget into the same failure repeated; escalation
    # makes each resume a materially different draw. Deterministic policy,
    # not strategy authorship: it changes sampling, never content.
    ADMISSION_TEMPERATURE_STEP = 0.25
    ADMISSION_TEMPERATURE_CEIL = 1.2

    def run_until_complete(self) -> dict[str, Any]:
        """Run attempts until the policy accepts a candidate or budget pauses the loop."""

        with self._run_lock():
            self.init()
            while True:
                state = self._read_state()
                if state.get("status") == "complete":
                    return self.status_summary()
                if int(state.get("run_count", 0)) >= self.spec.policy.max_attempts:
                    self._mark_budget_exhausted(state)
                    return self.status_summary()
                try:
                    last_record = self._run_once_unlocked()
                except CandidateAdmissionError:
                    state = self._read_state()
                    failures = int(state.get("admission_failure_count", 0))
                    if failures >= self.MAX_ADMISSION_FAILURES:
                        raise
                    self._escalate_proposer_temperature()
                    self.resume()
                    continue
                if last_record["accepted"]:
                    return self.status_summary()

    def _escalate_proposer_temperature(self) -> None:
        """Raise proposer sampling heat after an admission failure, if it has any."""

        temperature = getattr(self.proposer, "temperature", None)
        if not isinstance(temperature, (int, float)):
            return
        self.proposer.temperature = min(
            self.ADMISSION_TEMPERATURE_CEIL,
            float(temperature) + self.ADMISSION_TEMPERATURE_STEP,
        )

    @contextmanager
    def _run_lock(self):
        """Prevent two host processes from assigning the same attempt number."""

        self.state_dir.mkdir(parents=True, exist_ok=True)
        descriptor: int | None = None
        for retry in range(2):
            try:
                descriptor = os.open(
                    self.run_lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                )
                os.write(descriptor, str(os.getpid()).encode("ascii"))
                break
            except FileExistsError as exc:
                owner = _read_lock_owner(self.run_lock_path)
                if retry == 0 and owner is not None and not _process_exists(owner):
                    try:
                        self.run_lock_path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                raise LabLoopError(
                    f"Research loop is already running for {self.spec.name}"
                    + (f" (pid {owner})" if owner is not None else "")
                ) from exc
        if descriptor is None:  # pragma: no cover - defensive guard
            raise LabLoopError(f"Could not acquire research lock for {self.spec.name}")
        try:
            yield
        finally:
            os.close(descriptor)
            try:
                if _read_lock_owner(self.run_lock_path) == os.getpid():
                    self.run_lock_path.unlink()
            except FileNotFoundError:
                pass

    def _feature_steering(self) -> str | None:
        """Point the proposer at observation inputs it has not explored yet.

        Mirrors v3's diversity policy at the prompt level: a weak proposer
        anchors on one formulation and re-proposes it until novelty ends the
        run. Naming the concrete inputs its past candidates used -- and the
        ones they never touched -- is deterministic steering, not strategy
        authorship: every rule is still the model's own.
        """
        used: set[str] = set()
        sources = 0
        for record in self._read_runs():
            source = self._candidate_source_from_record(record)
            if source is None:
                continue
            sources += 1
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Subscript)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "features"
                    and isinstance(node.slice, ast.Constant)
                    and isinstance(node.slice.value, str)
                ):
                    used.add(node.slice.value)
        if not sources or not used:
            return None
        return (
            "Feature inputs already explored by earlier candidates: "
            + ", ".join(sorted(used))
            + ". Build the next rule around at least one feature NOT in that "
            "list, or a materially different combination or direction. Do not "
            "re-propose earlier logic with new labels."
        )

    def build_prompt(self, *, proposal_feedback: str | None = None) -> str:
        feedback = [record.get("feedback", "") for record in self._read_runs() if record.get("feedback")]
        prior_feedback = "\n".join(f"- {item}" for item in feedback[-5:]) or "- none"
        prior_candidates = self._prior_candidate_logic()
        steering = self._feature_steering()
        lines = [
            "Mission:",
            self.spec.mission,
            "",
            "Return strict JSON with exactly these keys: code, rationale.",
            "The code must be one Python candidate file and must not trade, place orders,",
            "cancel orders, close positions, flatten positions, or use broker credentials.",
            "The validator is fixed by policy and cannot be changed by the candidate.",
            f"Acceptance target: {_target_description(self.spec.policy)}.",
            "",
            "Prior validator feedback:",
            prior_feedback,
        ]
        if self.spec.policy.steering_notes:
            lines.extend(["", "Regime and mission steering (frozen with the policy):"])
            lines.extend(f"- {note}" for note in self.spec.policy.steering_notes)
        if steering:
            lines.extend(["", steering])
        if prior_candidates:
            lines.extend(
                [
                    "",
                    "Previously evaluated candidate logic (do not repeat semantically):",
                ]
            )
            for semantic_hash, logic in prior_candidates:
                lines.extend([f"--- {semantic_hash[:12]} ---", logic])
        if proposal_feedback:
            lines.extend(
                [
                    "",
                    "The previous proposal did not pass candidate admission:",
                    proposal_feedback,
                    "Return a corrected, materially distinct complete candidate and do not "
                    "repeat that admission error.",
                ]
            )
        return "\n".join(lines)

    def status_summary(self) -> dict[str, Any]:
        """Return compact machine-readable state for orchestration callers."""

        state = self.init()
        return {
            "name": state.get("name"),
            "status": state.get("status"),
            "run_count": int(state.get("run_count", 0)),
            "max_attempts": self.spec.policy.max_attempts,
            "min_attempts": self.spec.policy.min_attempts,
            "target_metric": self.spec.policy.target_metric,
            "direction": self.spec.policy.direction,
            "best_metric": state.get("best_metric"),
            "best_run": state.get("best_run"),
            "accepted_run": state.get("accepted_run"),
            "result_path": str(self.result_path) if self.result_path.exists() else None,
            "shutdown_report_path": str(self.shutdown_report_path)
            if self.shutdown_report_path.exists()
            else None,
            "admission_failure_count": int(state.get("admission_failure_count", 0)),
            "admissions_path": str(self.admissions_path) if self.admissions_path.exists() else None,
            "evaluator_version": state.get("evaluator_version"),
        }

    def _run_validator(self, candidate_path: Path) -> dict[str, Any]:
        command = self._validator_command(candidate_path)
        started = time.monotonic()
        completed = subprocess.run(
            command,
            cwd=self.workspace_dir,
            env=self._candidate_env(candidate_path),
            text=True,
            capture_output=True,
            timeout=self.spec.policy.timeout_seconds,
            check=False,
        )
        return {
            "command": command,
            "returncode": completed.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": completed.stdout[-4000:],
            "stderr_tail": completed.stderr[-4000:],
            "combined_output": completed.stdout + "\n" + completed.stderr,
        }

    def _run_candidate_preflight(self, candidate_path: Path) -> dict[str, Any] | None:
        """Run a cheap contract probe when the fixed validator exposes one."""

        supported = {
            "research.validators.options",
            "research.validators.bars_universe",
            "research.validators.overnight_spx",
            "research.validators.overnight_spx_v2",
        }
        declared = self.spec.policy.supports_preflight
        if declared is False:
            return None
        if declared is None and not supported.intersection(self.spec.policy.command):
            return None
        command = self._validator_command(candidate_path) + ["--preflight-only"]
        started = time.monotonic()
        completed = subprocess.run(
            command,
            cwd=self.workspace_dir,
            env=self._candidate_env(candidate_path),
            text=True,
            capture_output=True,
            timeout=min(self.spec.policy.timeout_seconds, 30),
            check=False,
        )
        return {
            "command": command,
            "returncode": completed.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": completed.stdout[-4000:],
            "stderr_tail": completed.stderr[-4000:],
            "combined_output": completed.stdout + "\n" + completed.stderr,
        }

    def _validator_command(self, candidate_path: Path) -> list[str]:
        """Resolve a bare Python command to the active project interpreter."""

        command = [
            part.format(candidate=str(candidate_path))
            for part in self.spec.policy.command
        ]
        if command and command[0].casefold() in {
            "python",
            "python.exe",
            "python3",
            "python3.exe",
        }:
            command[0] = sys.executable
        return command

    def _candidate_env(self, candidate_path: Path) -> dict[str, str]:
        env = dict(self.base_env)
        for key in list(env):
            if key in BROKER_ENV_KEYS or key.upper().startswith(BROKER_ENV_PREFIXES):
                env.pop(key, None)
        env["AUTOQUANT_RESEARCH_ONLY"] = "1"
        env["PYTHONNOUSERSITE"] = "1"
        env["CANDIDATE_PATH"] = str(candidate_path)
        env["CANDIDATE_WORKSPACE"] = str(self.workspace_dir)
        repository_root = str(Path(__file__).resolve().parents[2])
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            repository_root
            if not existing_pythonpath
            else os.pathsep.join((repository_root, existing_pythonpath))
        )
        return env

    def _write_candidate(
        self, candidate_hash: str, proposal: CandidateProposal
    ) -> tuple[Path, Path]:
        candidate_dir = self.workspace_dir / "candidates" / candidate_hash
        candidate_dir.mkdir(parents=True, exist_ok=True)
        candidate_path = candidate_dir / "candidate.py"
        rationale_path = candidate_dir / "rationale.md"
        candidate_path.write_text(proposal.code.rstrip() + "\n", encoding="utf-8")
        rationale_path.write_text(proposal.rationale.rstrip() + "\n", encoding="utf-8")
        self._assert_in_workspace(candidate_path)
        self._assert_in_workspace(rationale_path)
        return candidate_path, rationale_path

    def _ensure_workspace_boundary(self) -> None:
        if self.workspace_dir == self.state_dir or self.workspace_dir in self.state_dir.parents:
            raise LabLoopError("Workspace may not contain the state directory.")

    def _assert_in_workspace(self, path: Path) -> None:
        resolved = path.resolve()
        if resolved != self.workspace_dir and self.workspace_dir not in resolved.parents:
            raise LabLoopError(f"Candidate write escaped workspace: {path}")

    def _read_state(self) -> dict[str, Any]:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise LabLoopError(f"Loop is not initialized: {self.spec.name}") from exc
        if not isinstance(state, dict):
            raise LabLoopError("Loop state must be an object.")
        return state

    def _read_runs(self) -> list[dict[str, Any]]:
        try:
            lines = self.runs_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        records: list[dict[str, Any]] = []
        for line in lines:
            if line.strip():
                records.append(json.loads(line))
        return records

    def _read_admissions(self) -> list[dict[str, Any]]:
        try:
            lines = self.admissions_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        return [json.loads(line) for line in lines if line.strip()]

    def _evaluated_semantic_hashes(self) -> set[str]:
        """Return logic fingerprints for scientific trials, including legacy records."""

        fingerprints: set[str] = set()
        for record in self._read_runs():
            fingerprint = record.get("semantic_hash")
            if isinstance(fingerprint, str) and fingerprint:
                fingerprints.add(fingerprint)
                continue
            source = self._candidate_source_from_record(record)
            if source is not None:
                fingerprints.add(_candidate_semantic_hash(source))
        return fingerprints

    def _prior_candidate_logic(self) -> list[tuple[str, str]]:
        """Give the proposer compact, readable examples of already-tested logic."""

        candidates: list[tuple[str, str]] = []
        seen: set[str] = set()
        for record in reversed(self._read_runs()):
            source = self._candidate_source_from_record(record)
            if source is None:
                continue
            fingerprint = record.get("semantic_hash") or _candidate_semantic_hash(source)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            candidates.append((fingerprint, _candidate_logic_excerpt(source)))
            if len(candidates) >= PRIOR_CANDIDATE_LIMIT:
                break
        candidates.reverse()
        return candidates

    def _candidate_source_from_record(self, record: dict[str, Any]) -> str | None:
        relative_path = record.get("candidate_path")
        if not isinstance(relative_path, str) or not relative_path:
            return None
        candidate_path = (self.workspace_dir / relative_path).resolve()
        self._assert_in_workspace(candidate_path)
        try:
            return candidate_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def _append_record(self, record: dict[str, Any]) -> None:
        self.runs_path.parent.mkdir(parents=True, exist_ok=True)
        with self.runs_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    def _record_candidate_admission_failure(
        self,
        state: dict[str, Any],
        *,
        prospective_run: int,
        attempts: list[dict[str, Any]],
        error: str,
    ) -> None:
        record = {
            "timestamp": _utc_now(),
            "prospective_run": prospective_run,
            "attempts": attempts,
            "error": error,
        }
        self.admissions_path.parent.mkdir(parents=True, exist_ok=True)
        with self.admissions_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        state.update(
            {
                "status": "failed",
                "updated_at": _utc_now(),
                "last_feedback": error,
                "admission_failure_count": int(state.get("admission_failure_count", 0)) + 1,
            }
        )
        _write_json_atomic(self.state_path, state)
        self._write_shutdown_report(state, reason="candidate_admission_failed")

    def _write_result(self, record: dict[str, Any], state: dict[str, Any]) -> None:
        result = {
            "name": self.spec.name,
            "completed_at": _utc_now(),
            "mission_hash": state.get("mission_hash"),
            "policy_hash": state.get("policy_hash"),
            "evaluator_version": state.get("evaluator_version"),
            "accepted_run": record["run"],
            "candidate_hash": record["candidate_hash"],
            "semantic_hash": record["semantic_hash"],
            "candidate_path": record["candidate_path"],
            "metric_name": record["metric_name"],
            "metric": record["metric"],
            "best_metric": state.get("best_metric"),
            "best_run": state.get("best_run"),
        }
        _write_json_atomic(self.result_path, result)

    def _mark_budget_exhausted(self, state: dict[str, Any]) -> None:
        state["status"] = "paused"
        state["paused_reason"] = "budget_exhausted"
        state["updated_at"] = _utc_now()
        _write_json_atomic(self.state_path, state)
        self._write_shutdown_report(state, reason="budget_exhausted")

    def _write_shutdown_report(self, state: dict[str, Any], *, reason: str) -> None:
        """Write mandatory human-readable evidence whenever a loop stops."""

        records = self._read_runs()
        admissions = self._read_admissions()
        failures = sum(record.get("metric") is None for record in records)
        lines = [
            f"# Research shutdown report: {self.spec.name}",
            "",
            f"- Status: `{state.get('status')}`",
            f"- Reason: `{reason}`",
            f"- Attempts: {len(records)} / {self.spec.policy.max_attempts}",
            f"- Failed attempts: {failures}",
            f"- Candidate admission failures: {len(admissions)}",
            f"- Best {self.spec.policy.metric_name}: {state.get('best_metric')}",
            f"- Best run: {state.get('best_run')}",
            f"- Accepted run: {state.get('accepted_run')}",
            "",
            "## Experiments",
            "",
            "| Run | Candidate | Metric | Accepted | Evidence |",
            "|---:|---|---:|:---:|---|",
        ]
        for record in records:
            evidence = str(record.get("feedback") or record.get("error") or "").replace("|", "\\|")
            if len(evidence) > 240:
                evidence = evidence[:237] + "..."
            lines.append(
                "| {run} | `{candidate}` | {metric} | {accepted} | {evidence} |".format(
                    run=record.get("run"),
                    candidate=record.get("candidate_hash") or "none",
                    metric=record.get("metric"),
                    accepted="yes" if record.get("accepted") else "no",
                    evidence=evidence,
                )
            )
        lines.extend(
            [
                "",
                "## Integrity",
                "",
                f"- Mission hash: `{state.get('mission_hash')}`",
            f"- Policy hash: `{state.get('policy_hash')}`",
            f"- Evaluator version: `{state.get('evaluator_version')}`",
                "- Failed attempts remain in the append-only ledger.",
                "- This report makes no trading or deployment claim.",
            ]
        )
        self.shutdown_report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_once(
    spec: MissionSpec,
    *,
    state_dir: Path,
    workspace_dir: Path,
    proposer: Proposer,
    base_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Convenience wrapper for one proposal/evaluation attempt."""

    return ResearchLoop(
        spec,
        state_dir=state_dir,
        workspace_dir=workspace_dir,
        proposer=proposer,
        base_env=base_env,
    ).run_once()


def status_summary(
    spec: MissionSpec,
    *,
    state_dir: Path,
    workspace_dir: Path,
    proposer: Proposer,
    base_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Convenience wrapper for a compact loop state summary."""

    return ResearchLoop(
        spec,
        state_dir=state_dir,
        workspace_dir=workspace_dir,
        proposer=proposer,
        base_env=base_env,
    ).status_summary()


def _extract_metric(policy: ValidatorPolicy, output: str) -> float:
    matches = list(re.finditer(policy.metric_pattern, output, flags=re.MULTILINE))
    if not matches:
        raise LabLoopError("Validator output did not contain the configured metric.")
    match = matches[-1]
    raw_value = match.groupdict().get("value") if match.groupdict() else None
    if raw_value is None:
        if not match.groups():
            raise LabLoopError("Metric pattern needs a capture group or (?P<value>...).")
        raw_value = match.group(1)
    try:
        return float(raw_value)
    except (TypeError, ValueError) as exc:
        raise LabLoopError(f"Metric value is not numeric: {raw_value!r}") from exc


def _validate_candidate_source(source: str, extra_backtest_imports: tuple[str, ...] = ()) -> None:
    """Reject candidate capabilities that can escape deterministic validation.

    ``extra_backtest_imports`` lets a mission's POLICY extend the admissible
    `research.backtest` names (pure structure factories only by construction
    of that module) without editing this file for every new program family.
    The policy is frozen for the campaign, so the extension is part of the
    audited contract, not a runtime knob.
    """

    allowed_backtest = ALLOWED_BACKTEST_IMPORTS | set(extra_backtest_imports)

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise LabLoopError(f"Candidate is not valid Python: {exc}") from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in ALLOWED_CANDIDATE_IMPORTS:
                    raise LabLoopError(f"Candidate import is forbidden: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            private_names = [
                name
                for alias in node.names
                for name in (alias.name, alias.asname or "")
                if name.startswith("_")
            ]
            if private_names:
                raise LabLoopError(
                    "Candidate private import is forbidden: " + ", ".join(private_names)
                )
            if module in ALLOWED_CANDIDATE_IMPORTS:
                pass
            elif module == "research.backtest":
                blocked = [
                    alias.name
                    for alias in node.names
                    if alias.name not in allowed_backtest
                ]
                if blocked:
                    raise LabLoopError(
                        "Candidate research.backtest import is forbidden: "
                        + ", ".join(blocked)
                    )
            else:
                raise LabLoopError(f"Candidate import is forbidden: {module}")

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_CANDIDATE_CALLS:
                raise LabLoopError(f"Candidate call is forbidden: {node.func.id}")
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in FORBIDDEN_CANDIDATE_CALLS or node.id.startswith("_"):
                raise LabLoopError(f"Candidate name is forbidden: {node.id}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise LabLoopError(f"Candidate private attribute access is forbidden: {node.attr}")


def _meets_target(policy: ValidatorPolicy, metric: float | None) -> bool:
    """Whether the metric clears the policy target, ignoring min_attempts."""
    if metric is None:
        return False
    if policy.target_metric is None:
        return True
    if policy.direction == "minimize":
        return metric <= policy.target_metric
    return metric >= policy.target_metric


def _is_accepted(policy: ValidatorPolicy, metric: float, returncode: int | None, run_number: int) -> bool:
    if returncode != 0 or run_number < policy.min_attempts:
        return False
    return _meets_target(policy, metric)


def _validator_diagnostics(combined_output: str) -> str:
    """Render the validator's own JSON payload as proposer-visible evidence.

    Validators emit a full diagnostic payload on stdout, but the loop parses a
    single scalar out of it and discards the rest. That leaves the proposer
    unable to tell a near miss from a total miss, or to see which quantity a
    failing gate was measuring, so it cannot steer. Everything numeric the
    validator chose to publish is forwarded here.
    """
    payload: dict[str, Any] | None = None
    for line in combined_output.splitlines():
        line = line.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            payload = parsed
    if not payload:
        return ""
    parts: list[str] = []
    for key, value in sorted(payload.items()):
        if key in _DIAGNOSTIC_SKIP_KEYS or isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            parts.append(f"{key}={value}")
        elif isinstance(value, dict) and value and all(
            isinstance(item, (int, float)) and not isinstance(item, bool)
            for item in value.values()
        ):
            inner = ",".join(f"{name}:{count}" for name, count in sorted(value.items()))
            parts.append(f"{key}={{{inner}}}")
    return "; ".join(parts)[:_DIAGNOSTIC_MAX_CHARS]


def _feedback(
    policy: ValidatorPolicy,
    metric: float,
    validation: dict[str, Any],
    run_number: int,
    accepted: bool,
) -> str:
    if accepted:
        status = "accepted"
    elif validation["returncode"] != 0:
        status = f"exit {validation['returncode']}"
    elif run_number < policy.min_attempts:
        status = f"waiting for min_attempts {policy.min_attempts}"
        if _meets_target(policy, metric):
            # A qualifying candidate found before min_attempts is rejected on
            # ordering alone, and the novelty check then forbids re-proposing
            # the same logic. Without saying so, the proposer reads a negative
            # score and abandons a mechanism that already cleared the target.
            status += (
                f" -- NOTE: this formulation already meets the {policy.metric_name} "
                f"target ({policy.target_metric}); it is held only by attempt "
                "ordering. Keep its mechanism and refine it; do not abandon it"
            )
    else:
        status = f"target miss ({_target_description(policy)})"
    stderr = validation.get("stderr_tail", "").strip()
    suffix = f"; stderr: {stderr[-500:]}" if stderr else ""
    diagnostics = _validator_diagnostics(validation.get("combined_output", "") or "")
    evidence = f"; evidence: {diagnostics}" if diagnostics else ""
    return f"{status}; {policy.metric_name}={metric}{evidence}{suffix}"


def _target_description(policy: ValidatorPolicy) -> str:
    if policy.target_metric is None:
        return f"validator success after at least {policy.min_attempts} attempt(s)"
    operator = "<=" if policy.direction == "minimize" else ">="
    return (
        f"{policy.metric_name} {operator} {policy.target_metric} "
        f"after at least {policy.min_attempts} attempt(s)"
    )


def _empty_validation(exc: BaseException) -> dict[str, Any]:
    return {
        "command": None,
        "returncode": None,
        "duration_seconds": 0,
        "stdout_tail": "",
        "stderr_tail": str(exc),
        "combined_output": str(exc),
    }


def _validation_failure_detail(validation: dict[str, Any]) -> str:
    output = str(validation.get("combined_output") or "").strip()
    for line in reversed(output.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("error"):
            return str(payload["error"])
    stderr = str(validation.get("stderr_tail") or "").strip()
    if stderr:
        return stderr[-1000:]
    return output[-1000:] or f"validator exited with {validation.get('returncode')}"


def _proposal_attempt_record(
    attempt: int,
    prompt: str,
    proposal: CandidateProposal | None,
    candidate_hash: str | None,
    semantic_hash: str | None,
    candidate_path: Path | None,
    workspace_dir: Path,
    preflight: dict[str, Any] | None,
    error: str | None,
) -> dict[str, Any]:
    return {
        "attempt": attempt,
        "prompt_hash": _text_hash(prompt),
        "proposal_hash": _candidate_hash(proposal) if proposal is not None else None,
        "candidate_hash": candidate_hash,
        "semantic_hash": semantic_hash,
        "candidate_path": _relative_to(candidate_path, workspace_dir),
        "preflight": preflight,
        "error": error,
    }


def _candidate_hash(proposal: CandidateProposal) -> str:
    return _json_hash({"code": proposal.code, "rationale": proposal.rationale})


def _candidate_semantic_hash(source: str) -> str:
    """Fingerprint executable candidate logic while ignoring presentation-only edits."""

    tree = _normalized_candidate_tree(source)
    canonical = ast.dump(tree, annotate_fields=True, include_attributes=False)
    return _text_hash(canonical)


def _candidate_logic_excerpt(source: str) -> str:
    """Return normalized candidate code suitable for a compact novelty prompt."""

    normalized = ast.unparse(_normalized_candidate_tree(source)).strip()
    if len(normalized) <= 2400:
        return normalized
    return normalized[:2397] + "..."


def _normalized_candidate_tree(source: str) -> ast.AST:
    tree = ast.parse(source)
    return _CandidateSemanticNormalizer().visit(tree)


class _CandidateSemanticNormalizer(ast.NodeTransformer):
    """Remove metadata that cannot alter a candidate's trading behavior."""

    def visit_Module(self, node: ast.Module) -> ast.AST:  # noqa: N802
        self.generic_visit(node)
        node.body = self._without_docstring(node.body)
        node.body = [statement for statement in node.body if not _is_label_assignment(statement)]
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:  # noqa: N802
        self.generic_visit(node)
        node.body = self._without_docstring(node.body)
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:  # noqa: N802
        self.generic_visit(node)
        node.body = self._without_docstring(node.body)
        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:  # noqa: N802
        self.generic_visit(node)
        node.body = self._without_docstring(node.body)
        return node

    @staticmethod
    def _without_docstring(statements: list[ast.stmt]) -> list[ast.stmt]:
        if (
            statements
            and isinstance(statements[0], ast.Expr)
            and isinstance(statements[0].value, ast.Constant)
            and isinstance(statements[0].value.value, str)
        ):
            return statements[1:]
        return statements


def _is_label_assignment(statement: ast.stmt) -> bool:
    if isinstance(statement, ast.Assign):
        return len(statement.targets) == 1 and isinstance(
            statement.targets[0], ast.Name
        ) and statement.targets[0].id == "LABEL"
    return (
        isinstance(statement, ast.AnnAssign)
        and isinstance(statement.target, ast.Name)
        and statement.target.id == "LABEL"
    )


def _json_hash(value: Any) -> str:
    return _text_hash(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _read_lock_owner(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (FileNotFoundError, OSError, ValueError):
        return None


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        # OpenProcess alone is NOT a liveness test. Windows keeps the process
        # OBJECT openable for as long as any handle to it survives, and Task
        # Scheduler holds one for every task it launched. A dead pid therefore
        # reported as alive, which deadlocked stale-lock reclaim permanently
        # (observed 2026-08-21: the web server refused to start for 8 days
        # against a lock owned by an exited process). WaitForSingleObject
        # separates the two: a running process never signals, an exited one
        # signals immediately.
        synchronize = 0x00100000
        process_query_limited_information = 0x1000
        wait_timeout = 0x00000102
        still_active = 259

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

        handle = kernel32.OpenProcess(
            synchronize | process_query_limited_information, False, pid
        )
        if handle:
            try:
                return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
            finally:
                kernel32.CloseHandle(handle)

        # SYNCHRONIZE can be refused where a bare query handle is still granted
        # (a process owned by another user). Fall back to the exit code rather
        # than reporting a live owner as dead and stealing its lock.
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    except OverflowError:
        # A pid too large for the platform's C int cannot be a live process;
        # macOS raises here where Linux raises ESRCH (stale-lock test, 2026-09-04).
        return False
    return True


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _relative_to(path: Path | None, root: Path) -> str | None:
    if path is None:
        return None
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)
