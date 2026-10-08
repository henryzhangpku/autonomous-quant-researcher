"""Mission wiring for ``research/missions/gpu-leads-revisions``.

Gathers what data exists, checks it against the frozen requirements, and
refuses to evaluate until every one is met. When they are, it hands the
inputs to the locked evaluation in ``research.prereg.gpu_campaign``.

    uv run python -m research.prereg.gpu_leads_revisions status
    uv run python -m research.prereg.gpu_leads_revisions status --tape <path-or-https-url>
    uv run python -m research.prereg.gpu_leads_revisions verify
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from research.prereg import gpu_campaign, ledger, xnys
from research.prereg.gpu_signal import (
    SessionValue,
    SignalStatus,
    earliest_live_session,
    session_series,
    signal_at,
)
from research.prereg.requirements import (
    Refusal,
    Requirement,
    RequirementsReport,
    RequirementStatus,
    refuse_unless_ready,
    status,
)
from research.providers.gpu_index import Tape, TapeFormatError, TapeUnavailable, load_tape
from research.providers.licensed import (
    CAPITAL_IQ,
    ESTIMATE_PROVIDERS,
    GPU_RENTAL_HISTORY,
    LSEG_IBES,
    ConsensusEstimateRecord,
    GpuRentalRecord,
    ProviderNotAvailable,
)

ROOT = Path(__file__).resolve().parents[2]
MISSION_DIR = ROOT / "research" / "missions" / "gpu-leads-revisions"
BOUND_CODE = (
    "research/prereg/gates.py",
    "research/prereg/gpu_campaign.py",
    "research/prereg/gpu_signal.py",
    "research/prereg/xnys.py",
)
CODES = ("H100", "H200", "B200")


def load_spec(mission_dir: Path = MISSION_DIR) -> dict[str, Any]:
    return json.loads((mission_dir / ledger.SPEC_NAME).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class AvailableData:
    as_of: date                                   # last XNYS session on or before today
    tape: Tape | None
    tape_error: str | None = None
    vendor_h100: tuple[GpuRentalRecord, ...] = ()
    estimates: tuple[ConsensusEstimateRecord, ...] = ()
    closes: tuple[gpu_campaign.TotalReturnClose, ...] = ()
    provider_notes: Mapping[str, str] = field(default_factory=dict)


def last_session(on: date) -> date:
    return xnys.sessions(on - timedelta(days=10), on)[-1]


def gather(tape_source: str | None = None, *, today: date | None = None) -> AvailableData:
    """Read what exists. Licensed providers refuse; nothing is substituted."""
    as_of = last_session(today or datetime.now(timezone.utc).date())
    tape: Tape | None = None
    tape_error: str | None = None
    try:
        tape = load_tape(tape_source)
    except (TapeUnavailable, TapeFormatError) as exc:
        tape_error = str(exc)
    notes: dict[str, str] = {}
    for provider, keys in ((GPU_RENTAL_HISTORY, ("H100",)), (CAPITAL_IQ, ("NVDA",)),
                           (LSEG_IBES, ("NVDA",))):
        try:
            provider.fetch(keys, date(2023, 3, 1), as_of)
        except ProviderNotAvailable as exc:
            notes[provider.name] = exc.reason
    return AvailableData(as_of=as_of, tape=tape, tape_error=tape_error, provider_notes=notes)


def tape_series(tape: Tape | None, code: str, as_of: datetime | None = None) -> list[SessionValue]:
    if tape is None:
        return []
    return session_series(tape.fixings, f"GIX-{code}", as_of)


def _decision_grid(spec: Mapping[str, Any], as_of: date) -> list[date]:
    start = date.fromisoformat(spec["stages"]["discovery"]["start"])
    return gpu_campaign.weekly_grid(start, as_of)


def _closed_weeks(grid: list[date], as_of: date, horizon: int) -> list[int]:
    return [i for i in range(len(grid)) if i + horizon < len(grid) and grid[i + horizon] <= as_of]


def _vendor_series(records: tuple[GpuRentalRecord, ...]) -> list[SessionValue]:
    return [SessionValue(item.day, item.usd_per_gpu_hour, item.methodology)
            for item in sorted(records, key=lambda item: item.day)
            if item.gpu == "H100" and xnys.is_session(item.day)]


def check_campaign_1(data: AvailableData, spec: Mapping[str, Any]) -> RequirementsReport:
    reqs = spec["data_requirements"]
    c1 = spec["campaign_1"]
    horizon = c1["primary_outcome"]["horizon_weeks"]
    estimates_licensed = any(provider.credential_present for provider in ESTIMATE_PROVIDERS)
    items: list[RequirementStatus] = []

    hist = reqs["gpu_rental_history_h100"]
    window = xnys.sessions(date.fromisoformat(hist["from"]), date.fromisoformat(hist["to"]))
    have = {item.day for item in _vendor_series(data.vendor_h100)} & set(window)
    items.append(status(
        Requirement("gpu_rental_history_h100",
                    "Daily H100 rental price history for the discovery and validation years",
                    "licensed vendor history", "sessions",
                    math.ceil(hist["min_session_coverage"] * len(window))),
        len(have),
        data.provider_notes.get(GPU_RENTAL_HISTORY.name, f"{len(have)} sessions held"),
        licensed=bool(data.vendor_h100) or GPU_RENTAL_HISTORY.credential_present,
    ))

    grid = _decision_grid(spec, data.as_of)
    embargo = spec["stages"]["embargo"]
    lo, hi = date.fromisoformat(embargo["start"]), date.fromisoformat(embargo["end"])
    closed = [i for i in _closed_weeks(grid, data.as_of, horizon) if not lo <= grid[i] <= hi]
    for key, outcome in (("consensus_revenue_fy1", c1["primary_outcome"]),
                         ("consensus_capex_fy1", c1["confirmation_outcome"])):
        basket = spec["baskets"][outcome["basket"]]
        y = gpu_campaign.revision_outcomes(
            data.estimates, grid, basket, outcome["measure"], outcome["horizon_weeks"],
            min_names=outcome["min_names"], min_contributors=outcome["min_contributors"])
        covered = sum(1 for i in closed if i in y)
        note = "; ".join(data.provider_notes.get(p.name, "") for p in ESTIMATE_PROVIDERS).strip("; ")
        items.append(status(
            Requirement(key,
                        f"Point-in-time consensus FY+1 {outcome['measure']} for "
                        f"{', '.join(basket)}, every decision week with a closed "
                        f"{outcome['horizon_weeks']}-week window",
                        "Capital IQ or LSEG I/B/E/S", "weeks",
                        math.ceil(reqs[key]["min_week_coverage"] * len(closed))),
            covered, note or f"{covered} weeks covered",
            licensed=bool(data.estimates) or estimates_licensed,
        ))

    holdout_start = date.fromisoformat(spec["stages"]["holdout"]["start"])
    holdout_closed = [i for i in _closed_weeks(grid, data.as_of, horizon) if grid[i] >= holdout_start]
    live: dict[str, int] = {}
    for code in CODES:
        source = (lambda when, code=code: tape_series(data.tape, code, when))
        live[code] = sum(1 for i in holdout_closed
                         if gpu_campaign.position_for(source, code, grid[i]) is not None)
    needed = reqs["index_h100_holdout"]["holdout_weeks_with_live_signal_and_closed_window"]
    tape_note = data.tape_error or (
        f"{live['H100']} holdout weeks so far with a live signal and a closed window")
    items.append(status(
        Requirement("index_h100_holdout",
                    "Holdout weeks (after the freeze) with a live H100 signal from the public "
                    "index and a closed 8-week window", "public GPU rental price index", "weeks",
                    needed),
        live["H100"], tape_note,
    ))
    share = reqs["index_h200_and_b200_holdout"]["min_share_of_holdout_weeks_live"]
    for code in ("H200", "B200"):
        items.append(status(
            Requirement(f"index_{code.lower()}_holdout",
                        f"{code} live signal in the same holdout weeks (independent check)",
                        "public GPU rental price index", "weeks", math.ceil(share * needed)),
            live[code], data.tape_error or f"{live[code]} holdout weeks so far",
        ))
    return RequirementsReport("campaign_1", tuple(items))


def check_campaign_2(data: AvailableData, spec: Mapping[str, Any],
                     campaign_1: gpu_campaign.CampaignResult | None) -> RequirementsReport:
    passed = campaign_1 is not None and campaign_1.verdict is gpu_campaign.CampaignVerdict.PASS
    items = [status(
        Requirement("campaign_1_pass", "Campaign 1 verdict is PASS", "this ledger", "verdicts", 1),
        1 if passed else 0,
        "campaign 2 is sealed until campaign 1 passes" if not passed else "campaign 1 passed",
    )]
    grid = _decision_grid(spec, data.as_of)
    closed = _closed_weeks(grid, data.as_of, 1)
    baskets = spec["baskets"]
    y = gpu_campaign.excess_returns(data.closes, grid, baskets["suppliers_and_neoclouds"],
                                    baskets["returns_benchmark"])
    covered = sum(1 for i in closed if i in y)
    req = spec["data_requirements"]["campaign_2_total_return_closes"]
    items.append(status(
        Requirement("campaign_2_total_return_closes",
                    "Weekly total-return closes for the basket and SPY",
                    "Alpaca daily bars (research/providers/alpaca.py)", "weeks",
                    math.ceil(req["min_week_coverage"] * len(closed))),
        covered, "not staged; the provider exists in this repository and needs Alpaca keys"
        if not data.closes else f"{covered} weeks covered",
    ))
    return RequirementsReport("campaign_2", tuple(items))


@dataclass(frozen=True)
class Judgement:
    """Recorded verdicts. ``campaign_2`` is None while sealed by a campaign 1 non-PASS."""

    campaign_1: Mapping[str, Any]
    campaign_2: Mapping[str, Any] | Refusal | None


def result_payload(result: gpu_campaign.CampaignResult) -> dict[str, Any]:
    return {
        "verdict": result.verdict.value,
        "ended_at": result.ended_at,
        "stages": {name: asdict(report) for name, report in result.stages.items()},
        "checks": {name: asdict(check) for name, check in result.checks.items()},
    }


def _inputs(data: AvailableData, spec: Mapping[str, Any], spec_hash: str) -> gpu_campaign.CampaignInputs:
    vendor = _vendor_series(data.vendor_h100)
    return gpu_campaign.CampaignInputs(
        spec=spec, spec_sha256=spec_hash,
        grid=tuple(_decision_grid(spec, data.as_of)),
        primary_vendor=lambda _when: vendor,
        index_tape={code: (lambda when, code=code: tape_series(data.tape, code, when))
                    for code in CODES},
        estimates=data.estimates, closes=data.closes,
    )


def _consumed(campaign: str) -> Refusal:
    return Refusal(campaign, f"{campaign} was opened and recorded no verdict; its one "
                             "evaluation is consumed and it cannot be re-run")


def run(data: AvailableData, mission_dir: Path = MISSION_DIR) -> Refusal | Judgement:
    """Refuse, or judge each campaign once on the locked rules and record it.

    The one-use receipt is written to the ledger before any evaluation reads
    data, so a crash mid-evaluation consumes the campaign rather than
    allowing a second attempt. A recorded verdict is returned as recorded.
    """
    try:
        frozen = ledger.verify(mission_dir)
    except (ledger.PreregistrationError, OSError) as exc:
        return Refusal("campaign_1", f"the pre-registration does not verify: {exc}")
    spec = load_spec(mission_dir)
    recorded, opened = ledger.verdicts(mission_dir), ledger.opened(mission_dir)

    if "campaign_1" not in recorded:
        if "campaign_1" in opened:
            return _consumed("campaign_1")
        refusal = refuse_unless_ready(check_campaign_1(data, spec))
        if refusal is not None:
            return refusal
        ledger.open_evaluation(mission_dir, "campaign_1")
        first = gpu_campaign.evaluate_campaign_1(_inputs(data, spec, frozen.spec_sha256))
        ledger.record_verdict(mission_dir, "campaign_1", result_payload(first))
        recorded = ledger.verdicts(mission_dir)
    c1 = recorded["campaign_1"]
    if c1["verdict"] != gpu_campaign.CampaignVerdict.PASS.value:
        return Judgement(c1, None)

    if "campaign_2" not in recorded:
        if "campaign_2" in opened:
            return Judgement(c1, _consumed("campaign_2"))
        passed = gpu_campaign.CampaignResult("campaign_1", gpu_campaign.CampaignVerdict.PASS,
                                             c1["ended_at"], {})
        refusal = refuse_unless_ready(check_campaign_2(data, spec, passed))
        if refusal is not None:
            return Judgement(c1, refusal)
        ledger.open_evaluation(mission_dir, "campaign_2", requires_pass="campaign_1")
        second = gpu_campaign.evaluate_campaign_2(_inputs(data, spec, frozen.spec_sha256), passed)
        if second is None:  # unreachable: campaign 1 PASS was checked above
            raise RuntimeError("campaign 2 evaluation returned no result after a campaign 1 PASS")
        ledger.record_verdict(mission_dir, "campaign_2", result_payload(second))
        recorded = ledger.verdicts(mission_dir)
    return Judgement(c1, recorded["campaign_2"])


def series_snapshot(data: AvailableData) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for code in CODES:
        series = tape_series(data.tape, code)
        reading = signal_at(series, code, data.as_of)
        earliest = earliest_live_session(series)
        out[code] = {
            "points": [[item.day.isoformat(), round(item.value, 6), item.methodology_version]
                       for item in series],
            "signal": {
                "status": reading.status.value,
                "message": reading.message,
                "value": reading.value,
                "latest_fixing": reading.latest_fixing.isoformat() if reading.latest_fixing else None,
                "methodology_version": reading.methodology_version,
                "history_sessions": reading.history_sessions,
                "required_sessions": reading.required_sessions,
                "window_coverage": reading.window_coverage,
                "earliest_live_session": (earliest.isoformat() if earliest and
                                          reading.status is SignalStatus.INSUFFICIENT_HISTORY
                                          else None),
            },
        }
    return out


def status_snapshot(data: AvailableData, mission_dir: Path = MISSION_DIR) -> dict[str, Any]:
    """Everything the demo page shows, from the ledger and the live tape. No results."""
    frozen = ledger.verify(mission_dir)
    spec = load_spec(mission_dir)
    outcome = run(data, mission_dir)
    if not isinstance(outcome, Refusal):
        raise RuntimeError("a verdict is recorded; status snapshots describe the pre-data state")
    refusal = outcome.as_dict()
    c2 = check_campaign_2(data, spec, None)
    return {
        "mission": frozen.mission,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "as_of_session": data.as_of.isoformat(),
        "preregistration": {
            "ledger_event_hash": frozen.event_hash,
            "frozen_at": frozen.created_at,
            "spec_sha256": frozen.spec_sha256,
            "document_sha256": frozen.document_sha256,
            "code_sha256": dict(frozen.code_sha256),
        },
        "spec": spec,
        "tape": {
            "source": data.tape.source if data.tape else None,
            "sha256": data.tape.sha256 if data.tape else None,
            "error": data.tape_error,
        },
        "series": series_snapshot(data),
        "campaign_1": refusal,
        "campaign_2": {"campaign": "campaign_2", "refused": True,
                       "reason": "sealed until campaign 1 passes",
                       "unmet": [item.as_dict() for item in c2.unmet]},
        "providers": [provider.availability() for provider in (*ESTIMATE_PROVIDERS, GPU_RENTAL_HISTORY)],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gpu_leads_revisions")
    sub = parser.add_subparsers(dest="command", required=True)
    show = sub.add_parser("status", help="check requirements; refuse or evaluate")
    show.add_argument("--tape", help="path or https URL of series/index_values.csv")
    sub.add_parser("verify", help="recompute the pre-registration hashes against the ledger")
    args = parser.parse_args(argv)
    if args.command == "verify":
        try:
            frozen = ledger.verify(MISSION_DIR)
        except ledger.PreregistrationError as exc:
            print(f"FAILED: {exc}")
            return 1
        print(f"verified {frozen.mission}: ledger event {frozen.event_hash} ({frozen.created_at})")
        return 0
    data = gather(args.tape)
    outcome = run(data)
    if isinstance(outcome, Refusal):
        print(f"REFUSED ({outcome.campaign}): {outcome.reason}")
        for item in outcome.unmet:
            print(f"  - {item.requirement.key}: {item.available}/{item.requirement.required} "
                  f"{item.requirement.unit} [{item.state.value}] {item.detail}")
        for code, block in series_snapshot(data).items():
            signal = block["signal"]
            print(f"  {code}: {signal['message']} ({signal['history_sessions']}/"
                  f"{signal['required_sessions']} sessions)")
        return 2
    print(f"campaign_1: {outcome.campaign_1['verdict']} (ended at {outcome.campaign_1['ended_at']})")
    second = outcome.campaign_2
    if second is None:
        print("campaign_2: sealed, campaign 1 did not pass")
    elif isinstance(second, Refusal):
        print(f"campaign_2: refused, {second.reason}")
    else:
        print(f"campaign_2: {second['verdict']} (ended at {second['ended_at']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
