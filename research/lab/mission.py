"""Immutable mission and validator policy loading."""

from __future__ import annotations

import json
import re
import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class MissionConfigError(ValueError):
    """Raised when mission or policy input is invalid."""


FORBIDDEN_VALIDATOR_FRAGMENTS = (
    "tools/brokerage",
    "tools.brokerage",
    "trader/",
    "trader.",
    "archive/",
    "archive.",
    "alpaca.trading",
    "scripts/fast.sh",
    "broker_place_order",
    "broker_cancel_order",
    "broker_close_position",
    "flatten",
    "place_order",
    "cancel_order",
)


@dataclass(frozen=True)
class ValidatorPolicy:
    """Fixed evaluator contract supplied by policy, never by generated code."""

    command: tuple[str, ...]
    metric_pattern: str
    evaluator_version: str
    metric_name: str = "metric"
    direction: str = "maximize"
    timeout_seconds: int = 120
    max_attempts: int = 5
    target_metric: float | None = None
    min_attempts: int = 1
    #: Extra `research.backtest` names candidates may import, declared by the
    #: frozen policy so new program families never edit the loop's core
    #: allow-list constant (overnight-spx needed exactly that on 2026-08-13).
    allowed_backtest_imports: tuple[str, ...] = ()
    #: Whether the validator supports `--preflight-only`. None falls back to
    #: the loop's built-in registry of known validators.
    supports_preflight: bool | None = None
    #: Frozen, human-authored proposer steering (e.g., the data's volatility
    #: regimes). Part of the hashed contract: deterministic context for the
    #: proposer, never a per-run knob and never strategy authorship.
    steering_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class MissionSpec:
    """Natural-language mission paired with an immutable validator policy."""

    mission: str
    policy: ValidatorPolicy
    name: str = "mission"


def load_mission_spec(mission_path: Path, policy_path: Path) -> MissionSpec:
    """Load a frozen mission spec from plain text plus JSON or TOML policy."""

    mission = mission_path.read_text(encoding="utf-8").strip()
    if not mission:
        raise MissionConfigError("Mission text may not be empty.")
    raw_policy = _read_policy(policy_path)
    name = str(raw_policy.get("name") or mission_path.stem)
    return MissionSpec(mission=mission, policy=_parse_policy(raw_policy), name=name)


def _read_policy(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MissionConfigError(f"Could not read policy {path}: {exc}") from exc
    try:
        if path.suffix.lower() == ".json":
            data = json.loads(raw)
        else:
            data = tomllib.loads(raw)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        raise MissionConfigError(f"Could not parse policy {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise MissionConfigError("Policy config must be an object.")
    return data


def _parse_policy(data: dict[str, Any]) -> ValidatorPolicy:
    validator = data.get("validator", data)
    budget = data.get("budget", {})
    if not isinstance(validator, dict) or not isinstance(budget, dict):
        raise MissionConfigError("Policy validator and budget sections must be objects.")

    raw_command = validator.get("command")
    if isinstance(raw_command, str):
        command = tuple(shlex.split(raw_command))
    elif isinstance(raw_command, list) and all(isinstance(item, str) for item in raw_command):
        command = tuple(raw_command)
    else:
        raise MissionConfigError("validator.command must be a string or list of strings.")

    raw_imports = validator.get("allowed_backtest_imports", [])
    if not isinstance(raw_imports, list) or not all(isinstance(item, str) for item in raw_imports):
        raise MissionConfigError("validator.allowed_backtest_imports must be a list of strings.")
    for name in raw_imports:
        if not name.isidentifier() or name.startswith("_"):
            raise MissionConfigError(f"validator.allowed_backtest_imports entry is not a public name: {name!r}")
    raw_preflight = validator.get("preflight")
    if raw_preflight is not None and not isinstance(raw_preflight, bool):
        raise MissionConfigError("validator.preflight must be a boolean when present.")
    steering = data.get("steering", {})
    if not isinstance(steering, dict):
        raise MissionConfigError("Policy steering section must be an object.")
    raw_notes = steering.get("notes", [])
    if not isinstance(raw_notes, list) or not all(
        isinstance(item, str) and item.strip() for item in raw_notes
    ):
        raise MissionConfigError("steering.notes must be a list of non-empty strings.")

    policy = ValidatorPolicy(
        command=command,
        metric_pattern=str(validator.get("pattern", "")),
        evaluator_version=str(validator.get("version", "")).strip(),
        metric_name=str(validator.get("name", "metric")),
        direction=str(validator.get("direction", "maximize")).lower(),
        timeout_seconds=int(budget.get("timeout_seconds", validator.get("timeout_seconds", 120))),
        max_attempts=int(budget.get("max_attempts", budget.get("max_runs", 5))),
        target_metric=_optional_float(validator.get("target_metric", budget.get("target_metric"))),
        min_attempts=int(budget.get("min_attempts", validator.get("min_attempts", 1))),
        allowed_backtest_imports=tuple(raw_imports),
        supports_preflight=raw_preflight,
        steering_notes=tuple(note.strip() for note in raw_notes),
    )
    _validate_policy(policy)
    return policy


def _validate_policy(policy: ValidatorPolicy) -> None:
    if not policy.command:
        raise MissionConfigError("validator.command may not be empty.")
    if not policy.evaluator_version:
        raise MissionConfigError("validator.version is required.")
    rendered = " ".join(policy.command).lower().replace("\\", "/")
    blocked = next((fragment for fragment in FORBIDDEN_VALIDATOR_FRAGMENTS if fragment in rendered), None)
    if blocked:
        raise MissionConfigError(f"Validator command crosses the broker boundary: {blocked}")
    if policy.direction not in {"minimize", "maximize"}:
        raise MissionConfigError("validator.direction must be minimize or maximize.")
    if not policy.metric_pattern:
        raise MissionConfigError("validator.pattern is required.")
    try:
        re.compile(policy.metric_pattern)
    except re.error as exc:
        raise MissionConfigError(f"Invalid validator.pattern: {exc}") from exc
    if not 1 <= policy.timeout_seconds <= 86_400:
        raise MissionConfigError("timeout_seconds must be between 1 and 86400.")
    if not 1 <= policy.max_attempts <= 10_000:
        raise MissionConfigError("max_attempts must be between 1 and 10000.")
    if not 1 <= policy.min_attempts <= policy.max_attempts:
        raise MissionConfigError("min_attempts must be between 1 and max_attempts.")


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
