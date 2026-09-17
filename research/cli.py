"""Command-line entry point for the autonomous research laboratory."""

from __future__ import annotations

import os

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Sequence

from research.lab import (
    LabLoopError,
    MissionConfigError,
    OpenAIProposer,
    ProposalError,
    ResearchLoop,
    gpu_diagnostic,
    load_mission_spec,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m research.cli",
        description="Run NLP missions through a fixed, append-only research validator.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("gpu", help="Report the available NVIDIA research runtime.")
    subparsers.add_parser(
        "llm",
        help="Show which proposer runtime (local GPU or DeepSeek) will be used, and why.",
    )

    for action, help_text in (
        ("run", "Run until the policy accepts a candidate or exhausts its budget."),
        ("once", "Run exactly one proposal and validation attempt."),
        ("status", "Show the durable loop state."),
        ("pause", "Pause the loop before another attempt can start."),
        ("resume", "Resume a paused loop."),
    ):
        command = subparsers.add_parser(action, help=help_text)
        command.add_argument("--mission", type=Path, required=True)
        command.add_argument("--policy", type=Path, required=True)
        command.add_argument("--run-root", type=Path, default=Path(".research/runs"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.action == "gpu":
        _print_json(gpu_diagnostic())
        return 0
    if args.action == "llm":
        # Answerable BEFORE a mission spends its budget. The first run on this
        # box burned 50 attempts against an endpoint nobody had confirmed.
        from .lab.endpoints import local_server_is_listening, resolve_endpoint

        endpoint = resolve_endpoint()
        _print_json(
            {
                "provider": endpoint.provider,
                "model": endpoint.model,
                "base_url": endpoint.base_url,
                "reason": endpoint.reason,
                "api_key_present": bool(endpoint.api_key),
                "local_server_listening": local_server_is_listening(
                    os.environ.get("AUTOQUANT_LOCAL_BASE_URL", "http://127.0.0.1:8080/v1")
                ),
                "switch_with": "AUTOQUANT_LAB_PROVIDER=auto|local|deepseek",
            }
        )
        return 0

    try:
        spec = load_mission_spec(args.mission, args.policy)
        run_dir = args.run_root.resolve() / _safe_name(spec.name)
        loop = ResearchLoop(
            spec,
            state_dir=run_dir / "state",
            workspace_dir=run_dir / "workspace",
            proposer=OpenAIProposer(),
        )
        if args.action == "run":
            result = loop.run_until_complete()
        elif args.action == "once":
            result = loop.run_once()
        elif args.action == "status":
            result = loop.status_summary()
        elif args.action == "pause":
            loop.init()
            result = loop.pause()
        else:
            loop.init()
            result = loop.resume()
    except (LabLoopError, MissionConfigError, ProposalError, OSError) as exc:
        _print_json({"error": str(exc), "status": "failed"})
        return 2

    _print_json(result)
    return 0


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-.")
    return safe or "mission"


def _print_json(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


if __name__ == "__main__":
    sys.exit(main())
