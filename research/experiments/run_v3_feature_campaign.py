"""Run a schema-v3 SPY options campaign whose hypotheses may read daily features.

Runs OUTSIDE the research loop (it reads local files and starts the loop):

* the real 5-minute tape (research/data/spy_5min.csv.gz, from
  download_spy_intraday.py) is materialized into the three stage stores;
* one feature export (JSONL + manifest, research/v3/features.py contract) is
  validated and pinned by manifest hash in the campaign contract;
* coverage is measured per required key (a family, or one family.metric) on
  every session of every split, and frozen into the contract: a key that is
  not READY in all three splits stops the campaign before discovery;
* the frozen hypothesis schedule is one gt and one lt atom per required
  metric; after it, each metric is paired with each intraday atom;
* discovery and validation run; the holdout stays sealed (``--consume-holdout``
  is a separate, deliberate step).

Re-running with the same arguments resumes the same campaign.

Usage::

    python research/experiments/run_v3_feature_campaign.py \\
        --dir var/campaigns/spy-vrp-1 --features path/portfolio_signal.jsonl \\
        --require portfolio_signal.cboe_vrp \\
        --describe "portfolio_signal.cboe_vrp=30-day implied vol minus trailing 21-day realized vol" \\
        --budget 20
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from research.backtest.data import SessionStore  # noqa: E402
from research.v3.campaign import (CampaignContract, CampaignCoordinator,  # noqa: E402
                                  _candidate_record, _evaluation_record)
from research.v3.evaluator import Stage  # noqa: E402
from research.v3.features import (ET, FeatureExport, FeatureManifest,  # noqa: E402
                                  coverage_report, derive_feature_snapshot, parse_feature_key,
                                  required_family_coverage_hash)
from research.v3.program import PROGRAMS, SPY_PRICE_V3, Program  # noqa: E402
from research.v3.quote_store import QuoteAccess  # noqa: E402
from research.v3.runtime import (CampaignRuntime, ModeledResearchEvaluator,  # noqa: E402
                                 QuotedResearchEvaluator)
from research.v3.stage_store import StageAccess, materialize_stage_stores  # noqa: E402

DECISION_MINUTE = 600  # coverage is measured at 10:00 ET; a daily feature is constant intraday

MISSION = """Discover a robust SPY same-day options hypothesis that conditions an intraday
entry on a daily feature known before the open (the value as of the prior close).
Options are priced by the frozen model from trailing realized volatility, so a
feature helps only if it predicts how the session's move compares with that
model's price. Prefer simple, economically motivated conditions with a clear
reason to persist."""

QUOTE_MISSION = """Discover a robust SPY same-day options hypothesis that conditions an intraday
entry on a daily feature known before the open (the value as of the prior close).
Every leg is priced from real same-day option trades at entry and settled at the
close, so a feature helps if it predicts when same-day options are mispriced
relative to the move that follows. Prefer simple, economically motivated
conditions with a clear reason to persist."""


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_export(path: Path) -> FeatureExport:
    manifest = FeatureManifest(**json.loads(Path(str(path) + ".manifest.json").read_text()))
    export = FeatureExport.from_jsonl(manifest, path.read_text())
    export.validate()
    return export


def stage_manifests(stages: Path, sessions, source_hash: str, program: Program):
    """Materialize the stage stores once; a re-run reuses them only if they were
    built from the same tape."""
    if not (stages / "discovery").exists():
        return materialize_stage_stores(stages, sessions, source_hash=source_hash,
                                        program=program)
    manifests = {stage: StageAccess(stages, stage, program).manifest()
                 for stage in ("discovery", "validation", "holdout")}
    if any(m.source_hash != source_hash for m in manifests.values()):
        raise SystemExit(f"{stages} was built from a different tape; use a new --dir")
    return manifests


def coverage(exports: dict[str, FeatureExport], keys: list[str], sessions, symbol: str,
             program: Program = SPY_PRICE_V3):
    reports = []
    for split in program.stage_dates:
        start, end = program.split_dates(split)
        days = [s.day for s in sessions
                if start <= date.fromisoformat(s.day) and (end is None or date.fromisoformat(s.day) <= end)]
        families = {parse_feature_key(k)[0] for k in keys}
        snapshots = [derive_feature_snapshot(
            {f: exports[f] for f in families}, symbol=symbol,
            decision_time=datetime.combine(date.fromisoformat(d),
                                           time(DECISION_MINUTE // 60, DECISION_MINUTE % 60),
                                           ET).isoformat(),
            spot=1.0, required_families=families) for d in days]
        for key in keys:
            family, metric = parse_feature_key(key)
            reports.append(coverage_report(family, split, snapshots, metric=metric,
                                           program=program))
    return reports


def preflight(contract: CampaignContract, evaluator, stages: Path, key: str) -> None:
    """Run a hypothesis that can never select a session through the evaluator
    and the contract's own evaluation check, outside the ledger: a harness
    mismatch (version, manifests, feature pins) fails here instead of costing
    real trials. It selects nothing, so it reveals no stage economics."""
    family, metric = parse_feature_key(key)
    never = {"schema_version": 3, "entry_minute": 600,
             "signal": {"op": "lt", "observation": {"obs": "feature", "family": family,
                                                     "metric": metric}, "value": -100},
             "structure": {"helper": "call_debit_spread", "width": 2.0, "otm_offset": 0.0}}
    from research.v3.campaign import FrozenCandidate
    candidate = FrozenCandidate(**_candidate_record(never, _sha("preflight"), contract))
    evaluation = evaluator.evaluate(
        candidate, StageAccess(stages, "discovery", contract.program), Stage.DISCOVERY)
    try:  # raises on any mismatch between what the evaluator reports and the contract
        _evaluation_record(evaluation, Stage.DISCOVERY, contract)
    except Exception as exc:
        raise SystemExit(f"preflight failed: {type(exc).__name__}: {exc}") from exc
    print("preflight ok", file=sys.stderr)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", required=True)
    p.add_argument("--features", required=True, help="feature export JSONL (manifest beside it)")
    p.add_argument("--require", action="append", required=True,
                   help="feature key the hypotheses may read, e.g. portfolio_signal.cboe_vrp")
    p.add_argument("--describe", action="append", default=[],
                   help="key=one-line description shown to the proposer")
    p.add_argument("--symbol", default="SPY")
    p.add_argument("--budget", type=int, default=20)
    p.add_argument("--family-quota", type=int, default=2)
    p.add_argument("--max-formulations", type=int, default=60)
    p.add_argument("--consume-holdout", action="store_true")
    p.add_argument("--program", choices=sorted(PROGRAMS), default=SPY_PRICE_V3.program_id)
    p.add_argument("--quotes", help="quote store root (quote-priced programs; built by "
                                    "collect_spy_0dte_quotes.py materialize)")
    args = p.parse_args()
    program = PROGRAMS[args.program]
    quoted = program.price_source == "quote"
    if quoted and not args.quotes:
        raise SystemExit(f"{program.program_id} needs --quotes")
    mission = QUOTE_MISSION if quoted else MISSION

    root = Path(args.dir)
    keys = sorted(set(args.require))
    descriptions = dict(item.split("=", 1) for item in args.describe)
    export = load_export(Path(args.features))
    exports = {export.manifest.family: export}

    # quote-priced programs trade real strikes, so their tape is unadjusted
    sessions = SessionStore.load(f"{args.symbol}-RAW" if quoted else args.symbol).sessions()
    canonical = json.dumps([[s.day, list(map(list, s.bars))] for s in sessions],
                           separators=(",", ":"))
    manifests = stage_manifests(root / "stages", sessions, _sha(canonical), program)
    reports = coverage(exports, keys, sessions, args.symbol, program)
    for r in reports:
        print(f"coverage {r.key:32} {r.split:10} {r.state.value:22} "
              f"{r.covered_sessions}/{r.eligible_sessions} sessions, {r.covered_iso_weeks} weeks",
              file=sys.stderr)

    schedule = tuple(sorted(f"{op}:feature:{key}" for key in keys for op in ("gt", "lt")))
    policy = {"program": "spy-feature-v3", "symbol": args.symbol, "required": keys,
              "decision_minute": DECISION_MINUTE, "schedule": schedule,
              "budget": args.budget, "family_quota": args.family_quota}
    if program is not SPY_PRICE_V3:  # original-program policies hash as before
        policy["research_program"] = program.program_id
    contract = CampaignContract(
        campaign_id=root.name,
        policy_hash=_sha(json.dumps(policy, sort_keys=True)),
        evaluator_version=program.evaluator_version,
        stage_manifest_hashes={k: v.manifest_hash for k, v in manifests.items()},
        mission_hash=_sha(mission),
        discovery_budget=args.budget,
        required_family_coverage_hash=required_family_coverage_hash(reports),
        required_families=tuple(keys),
        required_hypothesis_families=schedule,
        family_quota=args.family_quota,
        feature_manifest_hashes={f: e.manifest.manifest_hash for f, e in exports.items()},
        research_scope=program.scope,
        research_scope_hash=_sha(json.dumps(program.scope, sort_keys=True,
                                            separators=(",", ":"))),
    )
    if quoted:
        for stage in program.stage_dates:
            QuoteAccess(Path(args.quotes), stage, program).manifest()
        evaluator = QuotedResearchEvaluator(Path(args.quotes), program, features=exports)
    else:
        evaluator = ModeledResearchEvaluator(features=exports)
    preflight(contract, evaluator, root / "stages", keys[0])
    coordinator = CampaignCoordinator(root / "campaign", contract,
                                      lambda stage: StageAccess(root / "stages", stage, program))
    from research.v3.proposer import OpenAICompatibleDeclarativeProposer

    features = {key: descriptions.get(key, key) for key in keys}
    runtime = CampaignRuntime(
        coordinator, OpenAICompatibleDeclarativeProposer(features=features),
        evaluator, mission=mission,
        formulation_limit=args.max_formulations, feature_coverage=reports,
    )
    state = runtime.consume_holdout() if args.consume_holdout else runtime.run_until_holdout()
    summary = {k: state.get(k) for k in ("state", "scientific_trials", "failure_reason")}
    summary["admitted"] = [
        {"attempt": a["attempt_id"], "family": a["candidate"]["descriptor"]["family"],
         "passed": (a.get("evaluation") or {}).get("passed")}
        for a in state.get("discovery_attempts", []) if a.get("admitted")
    ]
    print(json.dumps(summary, indent=1, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
