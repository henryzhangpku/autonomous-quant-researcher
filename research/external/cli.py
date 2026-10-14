"""Command line for portfolio-recipe campaigns on an external evaluator.

  python -m research.external.cli init --dir D --campaign-id ID --mission mission.md \\
      --evaluator-cwd PATH --evaluator-cmd "python research/evaluate.py" \\
      --universe eq_mini --base-bps 5 --elevated-bps 10 --budget 20
  python -m research.external.cli run --dir D [--scripted proposals.json] [--max-formulations 40]
  python -m research.external.cli close --dir D        # freeze the discovery shortlist
  python -m research.external.cli validate --dir D     # evaluate the frozen shortlist
  python -m research.external.cli holdout --dir D --end 2026-09-30 --confirm   # one use
  python -m research.external.cli status --dir D

`init` asks the evaluator to describe itself, pins its identity (code hash)
and vocabulary, and freezes the contract in the campaign's first ledger event.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from research.autoresearch import _research_environment
from research.external.campaign import BudgetExhausted, PortfolioCampaign, PortfolioCampaignContract
from research.external.portfolio import ExternalEvaluatorSpec
from research.external.proposer import ProposalError, RecipeProposer, openai_completion, scripted
from research.v3.campaign import _canonical


def describe_evaluator(command: tuple[str, ...], cwd: str, universe: str) -> dict[str, Any]:
    """Ask the evaluator for its identity and the vocabulary available to `universe`."""
    proc = subprocess.run([*command, "--describe", "--config", universe], text=True,
                          capture_output=True, cwd=cwd,
                          env=_research_environment(), timeout=300)
    if proc.returncode != 0:
        raise SystemExit(f"evaluator --describe failed: {proc.stderr.strip()[-500:]}")
    return json.loads(proc.stdout)


def load(directory: Path) -> tuple[PortfolioCampaign, dict[str, Any]]:
    raw = json.loads((directory / "contract.json").read_text())
    spec = ExternalEvaluatorSpec(**{**raw["evaluator"], "command": tuple(raw["evaluator"]["command"])})
    stages = {k: tuple(v) for k, v in raw["stages"].items()}
    contract = PortfolioCampaignContract(**{**raw, "evaluator": spec, "stages": stages})
    vocabulary = json.loads((directory / "vocabulary.json").read_text())
    if hashlib.sha256(_canonical(vocabulary)).hexdigest() != contract.vocabulary_hash:
        raise SystemExit("vocabulary.json does not match the frozen vocabulary hash")
    return PortfolioCampaign(directory, contract), vocabulary


def run_discovery(campaign: PortfolioCampaign, proposer: RecipeProposer,
                  vocabulary: dict[str, Any], max_formulations: int) -> dict[str, Any]:
    """Propose and submit until the trial budget or the formulation cap is reached."""
    for _ in range(max_formulations):
        try:
            proposal = proposer.propose(mission=campaign.contract.mission, vocabulary=vocabulary,
                                        attempts=campaign.attempts_summary(),
                                        rejections=campaign.rejection_feedback())
        except ProposalError as exc:
            proposal = {"invalid": str(exc)}  # recorded as a formulation failure
        try:
            campaign.submit(proposal)
        except BudgetExhausted:
            break
        if campaign.state()["trials"] >= campaign.contract.discovery_budget:
            break
    return campaign.state()


def _status(campaign: PortfolioCampaign) -> dict[str, Any]:
    s = campaign.state()
    return {"phase": s["phase"], "trials": s["trials"],
            "budget": campaign.contract.discovery_budget, "formulations": s["formulations"],
            "discovery_passed": sum(r.get("verdict") == "passed" for r in s["results"].values()),
            "shortlist": [c["semantic_hash"][:12] for c in s["shortlist"]],
            "validation": {k[:12]: v.get("verdict") or v.get("status")
                           for k, v in s["validation"].items()},
            "finalist": (s["finalist"] or {}).get("semantic_hash", "")[:12] or None,
            "holdout": (s["holdout"] or {}).get("verdict") or (s["holdout"] or {}).get("status"),
            "ledger_events": len(campaign.events())}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    for name in ("dir", "campaign-id", "mission", "evaluator-cwd", "evaluator-cmd", "universe"):
        init.add_argument("--" + name, required=True)
    init.add_argument("--base-bps", type=float, required=True)
    init.add_argument("--elevated-bps", type=float, required=True)
    init.add_argument("--risk-target", type=float, default=0.05)
    init.add_argument("--budget", type=int, required=True)
    init.add_argument("--family-quota", type=int, default=2)
    run = sub.add_parser("run")
    run.add_argument("--dir", required=True)
    run.add_argument("--scripted", help="JSON list of proposals instead of a model")
    run.add_argument("--max-formulations", type=int, default=40)
    for name in ("close", "validate", "status"):
        sub.add_parser(name).add_argument("--dir", required=True)
    hold = sub.add_parser("holdout")
    hold.add_argument("--dir", required=True)
    hold.add_argument("--end", required=True)
    hold.add_argument("--confirm", action="store_true")
    args = p.parse_args(argv)
    directory = Path(args.dir)

    if args.command == "init":
        if (directory / "ledger.jsonl").exists():
            raise SystemExit(f"{directory} already holds a campaign")
        command = tuple(shlex.split(args.evaluator_cmd))
        described = describe_evaluator(command, args.evaluator_cwd, args.universe)
        identity, vocabulary = described["evaluator"], described["vocabulary"]
        spec = ExternalEvaluatorSpec(name=identity["name"], version=identity["version"],
                                     code_hash=identity["code_hash"], command=command,
                                     cwd=args.evaluator_cwd, universe=args.universe,
                                     base_bps=args.base_bps, elevated_bps=args.elevated_bps,
                                     risk_target=args.risk_target)
        contract = PortfolioCampaignContract(
            campaign_id=args.campaign_id, mission=Path(args.mission).read_text(),
            evaluator=spec, vocabulary_hash=hashlib.sha256(_canonical(vocabulary)).hexdigest(),
            discovery_budget=args.budget, family_quota=args.family_quota)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "vocabulary.json").write_text(json.dumps(vocabulary, sort_keys=True, indent=1))
        (directory / "contract.json").write_text(json.dumps(asdict(contract), sort_keys=True, indent=1))
        campaign = PortfolioCampaign(directory, contract)
        print(json.dumps({"contract_hash": contract.contract_hash(),
                          "evaluator": identity, "status": _status(campaign)}, indent=1))
        return 0

    campaign, vocabulary = load(directory)
    if args.command == "run":
        complete = (scripted(json.loads(Path(args.scripted).read_text())) if args.scripted
                    else openai_completion())
        run_discovery(campaign, RecipeProposer(complete), vocabulary, args.max_formulations)
    elif args.command == "close":
        campaign.close_discovery()
    elif args.command == "validate":
        campaign.run_validation()
    elif args.command == "holdout":
        campaign.consume_holdout(end=args.end, confirm=args.confirm)
    print(json.dumps(_status(campaign), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
