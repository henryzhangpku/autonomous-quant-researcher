"""Bounded, resumable experiment runner for on-demand quantitative research.

This adapts the small/fixed experiment contract popularized by Karpathy's
autoresearch and the state, budget, gate, and audit patterns from Loop
Engineering. It is intentionally not an agent framework: it runs one declared
experiment command at a time and never schedules or deploys anything.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import tomllib


REPO_ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = REPO_ROOT / ".research" / "loops"
BROKER_ENV_PREFIXES = (
    "ALPACA_",
    "APCA_",
    "ETORO_",
    "KALSHI_",
    "MASSIVE_",
    "POLYGON_",
    "PUBLIC_",
    "ROBINHOOD_",
    "TASTY_",
    "TASTYTRADE_",
    "WEBULL_",
)
BROKER_ENV_KEYS = {
    "QS_AGENT_TOKEN",
    "QS_BROKERAGE_AGENT_TOKEN",
    "QS_BROKERAGE_URL",
    "OPENAI_API_KEY",
    "AUTOQUANT_LAB_API_KEY",
}
FORBIDDEN_COMMAND_FRAGMENTS = (
    "archive/",
    "archive.",
    "tools/brokerage",
    "tools.brokerage",
    "trader/",
    "trader.",
    "scripts/fast.sh",
    "broker_place_order",
    "broker_cancel_order",
    "broker_close_position",
)


class ResearchLoopError(RuntimeError):
    """A loop configuration, safety, or evaluation failure."""


@dataclass(frozen=True)
class LoopConfig:
    name: str
    command: tuple[str, ...]
    metric_pattern: str
    metric_name: str
    direction: str
    timeout_seconds: int
    max_runs: int
    mutable_paths: tuple[str, ...]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_name(value: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-").lower()
    if not name:
        raise ResearchLoopError("Loop name must contain a letter or number.")
    return name


def _state_dir(name: str) -> Path:
    return STATE_ROOT / _safe_name(name)


def _load_config(path: Path) -> LoopConfig:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ResearchLoopError(f"Could not read loop config {path}: {exc}") from exc
    loop = data.get("loop", {})
    evaluator = data.get("evaluator", {})
    budget = data.get("budget", {})
    sandbox = data.get("sandbox", {})
    raw_command = loop.get("command")
    if isinstance(raw_command, str):
        command = tuple(shlex.split(raw_command))
    elif isinstance(raw_command, list) and all(isinstance(item, str) for item in raw_command):
        command = tuple(raw_command)
    else:
        raise ResearchLoopError("loop.command must be a string or list of strings.")
    config = LoopConfig(
        name=_safe_name(str(loop.get("name", path.stem))),
        command=command,
        metric_pattern=str(evaluator.get("pattern", "")),
        metric_name=str(evaluator.get("name", "metric")),
        direction=str(evaluator.get("direction", "minimize")).lower(),
        timeout_seconds=int(budget.get("timeout_seconds", 300)),
        max_runs=int(budget.get("max_runs", 20)),
        mutable_paths=tuple(str(item) for item in sandbox.get("mutable_paths", [])),
    )
    _validate_config(config)
    return config


def _validate_config(config: LoopConfig) -> None:
    if not config.command:
        raise ResearchLoopError("loop.command may not be empty.")
    rendered = " ".join(config.command).lower().replace("\\", "/")
    blocked = next((item for item in FORBIDDEN_COMMAND_FRAGMENTS if item in rendered), None)
    if blocked:
        raise ResearchLoopError(f"Research command crosses the broker boundary: {blocked}")
    if config.direction not in {"minimize", "maximize"}:
        raise ResearchLoopError("evaluator.direction must be minimize or maximize.")
    try:
        re.compile(config.metric_pattern)
    except re.error as exc:
        raise ResearchLoopError(f"Invalid evaluator.pattern: {exc}") from exc
    if not config.metric_pattern:
        raise ResearchLoopError("evaluator.pattern is required.")
    if not 1 <= config.timeout_seconds <= 86_400:
        raise ResearchLoopError("budget.timeout_seconds must be between 1 and 86400.")
    if not 1 <= config.max_runs <= 10_000:
        raise ResearchLoopError("budget.max_runs must be between 1 and 10000.")
    if not config.mutable_paths:
        raise ResearchLoopError("sandbox.mutable_paths must allow at least one research path.")
    for raw_path in config.mutable_paths:
        path = (REPO_ROOT / raw_path).resolve()
        try:
            relative = path.relative_to(REPO_ROOT)
        except ValueError as exc:
            raise ResearchLoopError(f"Mutable path escapes the repository: {raw_path}") from exc
        if not relative.parts or relative.parts[0] != "research":
            raise ResearchLoopError(f"Mutable paths must stay under research/: {raw_path}")


def _research_environment() -> dict[str, str]:
    env = dict(os.environ)
    for key in list(env):
        if key in BROKER_ENV_KEYS or key.upper().startswith(BROKER_ENV_PREFIXES):
            env.pop(key, None)
    env["AUTOQUANT_RESEARCH_ONLY"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _git_changes() -> set[str]:
    proc = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise ResearchLoopError(proc.stderr.decode(errors="replace").strip())
    changes: set[str] = set()
    entries = proc.stdout.decode(errors="replace").split("\0")
    for entry in entries:
        if not entry:
            continue
        path = entry[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        changes.add(path.replace("\\", "/"))
    return changes


def _is_allowed(path: str, mutable_paths: tuple[str, ...]) -> bool:
    candidate = (REPO_ROOT / path).resolve()
    for raw_allowed in mutable_paths:
        allowed = (REPO_ROOT / raw_allowed).resolve()
        if candidate == allowed or allowed in candidate.parents:
            return True
    return False


def _extract_metric(pattern: str, output: str) -> float:
    matches = list(re.finditer(pattern, output, flags=re.MULTILINE))
    if not matches:
        raise ResearchLoopError("Experiment output did not contain the configured metric.")
    match = matches[-1]
    raw_value = match.groupdict().get("value") if match.groupdict() else None
    if raw_value is None:
        if not match.groups():
            raise ResearchLoopError("Metric pattern needs a capture group or (?P<value>...).")
        raw_value = match.group(1)
    try:
        return float(raw_value)
    except (TypeError, ValueError) as exc:
        raise ResearchLoopError(f"Metric value is not numeric: {raw_value!r}") from exc


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def init_loop(config_path: Path) -> Path:
    config = _load_config(config_path)
    state_dir = _state_dir(config.name)
    if state_dir.exists():
        raise ResearchLoopError(f"Loop already exists: {config.name}")
    state_dir.mkdir(parents=True)
    state = {
        "name": config.name,
        "config_path": str(config_path.resolve().relative_to(REPO_ROOT)),
        "status": "ready",
        "created_at": _utc_now(),
        "run_count": 0,
        "best_metric": None,
        "best_run": None,
    }
    _write_json_atomic(state_dir / "state.json", state)
    (state_dir / "runs.jsonl").touch()
    return state_dir


def run_once(config_path: Path) -> dict[str, Any]:
    config = _load_config(config_path)
    state_dir = _state_dir(config.name)
    state_path = state_dir / "state.json"
    state = _read_json(state_path, None)
    if not isinstance(state, dict):
        raise ResearchLoopError(f"Initialize the loop first: {config.name}")
    if state.get("status") == "paused":
        raise ResearchLoopError(f"Loop is paused: {config.name}")
    if int(state.get("run_count", 0)) >= config.max_runs:
        raise ResearchLoopError(f"Run budget exhausted ({config.max_runs}).")

    before = _git_changes()
    started = time.monotonic()
    completed = subprocess.run(
        list(config.command),
        cwd=REPO_ROOT,
        env=_research_environment(),
        text=True,
        capture_output=True,
        timeout=config.timeout_seconds,
        check=False,
    )
    duration = time.monotonic() - started
    after = _git_changes()
    new_changes = after - before
    violations = sorted(path for path in new_changes if not _is_allowed(path, config.mutable_paths))
    combined_output = completed.stdout + "\n" + completed.stderr
    metric = _extract_metric(config.metric_pattern, combined_output)
    accepted = completed.returncode == 0 and not violations
    run_number = int(state.get("run_count", 0)) + 1
    previous_best = state.get("best_metric")
    improved = accepted and (
        previous_best is None
        or (config.direction == "minimize" and metric < float(previous_best))
        or (config.direction == "maximize" and metric > float(previous_best))
    )
    record = {
        "run": run_number,
        "timestamp": _utc_now(),
        "command": list(config.command),
        "returncode": completed.returncode,
        "duration_seconds": round(duration, 3),
        "metric_name": config.metric_name,
        "metric": metric,
        "accepted": accepted,
        "improved": improved,
        "boundary_violations": violations,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
    }
    with (state_dir / "runs.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    state.update(
        {
            "status": "ready",
            "updated_at": _utc_now(),
            "run_count": run_number,
            "last_run": run_number,
        }
    )
    if improved:
        state["best_metric"] = metric
        state["best_run"] = run_number
    _write_json_atomic(state_path, state)
    if completed.returncode != 0:
        raise ResearchLoopError(f"Experiment exited with status {completed.returncode}.")
    if violations:
        raise ResearchLoopError("Experiment changed files outside its mutable paths: " + ", ".join(violations))
    return record


def set_paused(name: str, paused: bool) -> dict[str, Any]:
    state_path = _state_dir(name) / "state.json"
    state = _read_json(state_path, None)
    if not isinstance(state, dict):
        raise ResearchLoopError(f"Unknown loop: {name}")
    state["status"] = "paused" if paused else "ready"
    state["updated_at"] = _utc_now()
    _write_json_atomic(state_path, state)
    return state


def show_status(name: str) -> dict[str, Any]:
    state = _read_json(_state_dir(name) / "state.json", None)
    if not isinstance(state, dict):
        raise ResearchLoopError(f"Unknown loop: {name}")
    return state


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autoresearch", description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("init", "run"):
        child = subparsers.add_parser(action)
        child.add_argument("config", type=Path)
    for action in ("status", "pause", "resume"):
        child = subparsers.add_parser(action)
        child.add_argument("name")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.action == "init":
        result: Any = {"state_dir": str(init_loop(args.config))}
    elif args.action == "run":
        result = run_once(args.config)
    elif args.action == "status":
        result = show_status(args.name)
    elif args.action == "pause":
        result = set_paused(args.name, True)
    else:
        result = set_paused(args.name, False)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ResearchLoopError, subprocess.TimeoutExpired) as exc:
        print(f"autoresearch: {exc}", file=sys.stderr)
        raise SystemExit(1)
