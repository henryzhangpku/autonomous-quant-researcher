"""Mission wiring for ``research/missions/gpu-leads-revisions``.

Gathers what data exists, checks it against the frozen requirements, and
refuses to evaluate until every one is met. When they are, it hands the
inputs to the locked evaluation in ``research.prereg.gpu_campaign``.

    uv run python -m research.prereg.gpu_leads_revisions status
    uv run python -m research.prereg.gpu_leads_revisions status --tape <path-or-https-url>
    uv run python -m research.prereg.gpu_leads_revisions verify
    uv run python -m research.prereg.gpu_leads_revisions proxy-status \
        [--backfill CSV] [--facts CSV] [--prices CSV] [--tape PATH-OR-URL]

Two tracks share one ledger. The primary track (licensed consensus revisions)
was frozen first; the proxy track (list-price back-series, SEC XBRL growth
acceleration, forward returns) is a weaker, separately judged test chained
after it.
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

from research.prereg import gpu_campaign, ledger, proxy_campaign, xnys
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
from research.providers import proxy_inputs
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
PROXY_TRACK = "proxy"
PROXY_SPEC_NAME = "proxy-preregistration.json"
PROXY_DOC_NAME = "PROXY-PREREGISTRATION.md"
PROXY_BOUND_CODE = (
    "research/prereg/gates.py",
    "research/prereg/gpu_campaign.py",
    "research/prereg/gpu_signal.py",
    "research/prereg/proxy_campaign.py",
    "research/prereg/xnys.py",
)


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
        note = "; ".join(f"{p.name}: {data.provider_notes[p.name]}" for p in ESTIMATE_PROVIDERS
                         if p.name in data.provider_notes)
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


# ── the proxy track ─────────────────────────────────────────────────────────

def load_proxy_spec(mission_dir: Path = MISSION_DIR) -> dict[str, Any]:
    return json.loads((mission_dir / PROXY_SPEC_NAME).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class ProxyData:
    as_of: date
    tape: Tape | None
    backfill: proxy_inputs.Loaded[proxy_campaign.MonthlyListPrice] | None
    facts: proxy_inputs.Loaded[proxy_campaign.FiledFact] | None
    closes: proxy_inputs.Loaded[proxy_campaign.AdjustedClose] | None
    errors: Mapping[str, str] = field(default_factory=dict)


def gather_proxy(*, tape_source: str | None = None, backfill: str | None = None,
                 facts: str | None = None, prices: str | None = None,
                 today: date | None = None) -> ProxyData:
    """Read the proxy inputs that exist. A missing or malformed file is recorded, never filled."""
    as_of = last_session(today or datetime.now(timezone.utc).date())
    errors: dict[str, str] = {}
    tape: Tape | None = None
    try:
        tape = load_tape(tape_source)
    except (TapeUnavailable, TapeFormatError) as exc:
        errors["tape"] = str(exc)
    loaded: dict[str, Any] = {}
    for key, loader, source in (("backfill", proxy_inputs.load_backfill, backfill),
                                ("facts", proxy_inputs.load_facts, facts),
                                ("closes", proxy_inputs.load_closes, prices)):
        try:
            loaded[key] = loader(source)
        except (proxy_inputs.InputUnavailable, proxy_inputs.InputSchemaError) as exc:
            loaded[key] = None
            errors[key] = str(exc)
    return ProxyData(as_of, tape, loaded["backfill"], loaded["facts"], loaded["closes"], errors)


def _proxy_grid(spec: Mapping[str, Any], as_of: date) -> list[date]:
    # A spec may start the decision grid before discovery so the outcome warm-up
    # (matured outcomes before the first scored month) uses earlier filings.
    start = spec.get("decision_grid_start") or spec["stages"]["discovery"]["start"]
    return proxy_campaign.monthly_decisions(date.fromisoformat(start), as_of)


def _proxy_inputs(data: ProxyData, spec: Mapping[str, Any], spec_hash: str) -> proxy_campaign.ProxyInputs:
    return proxy_campaign.ProxyInputs(
        spec=spec, spec_sha256=spec_hash, grid=tuple(_proxy_grid(spec, data.as_of)),
        backfill=data.backfill.rows if data.backfill else (),
        index_tape={code: (lambda when, code=code: tape_series(data.tape, code, when)) for code in CODES},
        facts=data.facts.rows if data.facts else (),
        closes=data.closes.rows if data.closes else (),
    )


def check_proxy_1(data: ProxyData, spec: Mapping[str, Any]) -> RequirementsReport:
    reqs = spec["data_requirements"]
    items: list[RequirementStatus] = []
    months_cfg = reqs["list_price_backfill"]["h100_months"]
    first = date.fromisoformat(months_cfg["from"] + "-01")
    last = date.fromisoformat(months_cfg["to"] + "-01")
    months: list[date] = []
    while first <= last:
        months.append(first)
        first = proxy_campaign.add_months(first, 1)
    rows = data.backfill.rows if data.backfill else ()
    have = sum(1 for month in months if proxy_campaign.backfill_value(rows, "H100", month) is not None)
    items.append(status(
        Requirement("list_price_backfill",
                    f"Monthly H100 rental list-price medians, {months_cfg['from']} to {months_cfg['to']}, "
                    "at least 3 rate cards each", "gpu-index list-price back-series (GPU_INDEX_BACKFILL)",
                    "months", math.ceil(reqs["list_price_backfill"]["min_month_coverage"] * len(months))),
        have, data.errors.get("backfill", f"{have} months usable")))

    grid = _proxy_grid(spec, data.as_of)
    disc = date.fromisoformat(spec["stages"]["discovery"]["start"])
    val_end = date.fromisoformat(spec["stages"]["validation"]["end"])
    pre_freeze = [i for i, d in enumerate(grid) if disc <= d <= val_end]
    facts = data.facts.rows if data.facts else ()
    share = reqs["xbrl_facts"]["min_decision_coverage"]
    for name, outcome in spec["proxy_campaign_1"]["outcomes"].items():
        covered = sum(1 for i in pre_freeze if proxy_campaign.basket_acceleration(
            facts, outcome["basket"], outcome["concept"], grid[i], min_names=outcome["min_names"]) is not None)
        items.append(status(
            Requirement(f"xbrl_{name}",
                        f"Filed-date-stamped XBRL {outcome['concept']} for {', '.join(outcome['basket'])}: "
                        "next-quarter acceleration computable at each discovery and validation decision",
                        "SEC XBRL facts (PROXY_XBRL_FACTS)", "decisions", math.ceil(share * len(pre_freeze))),
            covered, data.errors.get("facts", f"{covered} decisions covered")))

    holdout_start = date.fromisoformat(spec["stages"]["holdout"]["start"])
    holdout = [i for i, d in enumerate(grid) if d >= holdout_start]
    needed = reqs["index_holdout"]["holdout_months_with_live_signal_and_matured_outcome"]
    live: dict[str, int] = {}
    matured: dict[str, int] = {}
    outcomes = spec["proxy_campaign_1"]["outcomes"]
    for code in CODES:
        source = (lambda when, code=code: tape_series(data.tape, code, when))
        live_idx = [i for i in holdout if proxy_campaign.position_from_tape(source, grid[i]) is not None]
        live[code] = len(live_idx)
        matured[code] = sum(1 for i in live_idx if all(
            (found := proxy_campaign.basket_acceleration(facts, o["basket"], o["concept"], grid[i],
                                                         min_names=o["min_names"])) is not None
            and found.known_on <= data.as_of for o in outcomes.values()))
    tape_note = data.errors.get("tape")
    items.append(status(
        Requirement("proxy_index_h100_holdout",
                    "Holdout months (after the freeze) with a live monthly H100 signal from the public "
                    "index and a matured next-quarter outcome", "public GPU rental price index", "months",
                    needed),
        matured["H100"], tape_note or f"{live['H100']} holdout months so far with a live monthly signal"))
    check_share = reqs["index_holdout"]["checks_min_share"]
    for code in ("H200", "B200"):
        items.append(status(
            Requirement(f"proxy_index_{code.lower()}_holdout", f"{code} monthly signal live in the same "
                        "holdout months (independent check)", "public GPU rental price index", "months",
                        math.ceil(check_share * needed)),
            matured[code], tape_note or f"{live[code]} holdout months so far with a live monthly signal"))
    return RequirementsReport("proxy_campaign_1", tuple(items))


def check_proxy_2(data: ProxyData, spec: Mapping[str, Any], passed: bool) -> RequirementsReport:
    items = [status(Requirement("proxy_campaign_1_pass", "Proxy campaign 1 verdict is PASS", "this ledger",
                                "verdicts", 1), 1 if passed else 0,
                    "proxy campaign 2 is sealed until proxy campaign 1 passes" if not passed
                    else "proxy campaign 1 passed")]
    c2 = spec["proxy_campaign_2"]
    grid = _proxy_grid(spec, data.as_of)
    closes = data.closes.rows if data.closes else ()
    excess = proxy_campaign.forward_excess(closes, grid, c2["basket"], c2["primary"]["benchmark"],
                                           c2["primary"]["months"], min_names=c2["min_names"])
    closed = [i for i in range(len(grid) - c2["primary"]["months"])]
    covered = sum(1 for i in closed if i in excess)
    share = spec["data_requirements"]["adjusted_closes"]["min_decision_coverage"]
    items.append(status(
        Requirement("proxy_adjusted_closes", f"Daily adjusted closes for the basket and "
                    f"{c2['primary']['benchmark']}, every monthly decision with a closed window",
                    "adjusted closes (PROXY_PRICES)", "decisions", math.ceil(share * len(closed))),
        covered, data.errors.get("closes", f"{covered} decisions covered")))
    return RequirementsReport("proxy_campaign_2", tuple(items))


def _input_hashes(data: ProxyData) -> dict[str, str]:
    out: dict[str, str] = {}
    if data.tape is not None:
        out["index_tape"] = data.tape.sha256
    for key, loaded in (("backfill", data.backfill), ("xbrl_facts", data.facts), ("closes", data.closes)):
        if loaded is not None:
            out[key] = loaded.sha256
    return out


def run_proxy(data: ProxyData, mission_dir: Path = MISSION_DIR) -> Refusal | Judgement:
    """Refuse, or judge each proxy campaign once on its locked rules and record it."""
    try:
        ledger.verify(mission_dir)
        frozen = ledger.verify_track(mission_dir, PROXY_TRACK)
    except (ledger.PreregistrationError, OSError) as exc:
        return Refusal("proxy_campaign_1", f"the proxy pre-registration does not verify: {exc}")
    spec = load_proxy_spec(mission_dir)
    recorded, opened = ledger.verdicts(mission_dir), ledger.opened(mission_dir)
    if "proxy_campaign_1" not in recorded:
        if "proxy_campaign_1" in opened:
            return _consumed("proxy_campaign_1")
        refusal = refuse_unless_ready(check_proxy_1(data, spec))
        if refusal is not None:
            return refusal
        ledger.open_evaluation(mission_dir, "proxy_campaign_1", track=PROXY_TRACK,
                               inputs=_input_hashes(data))
        first = proxy_campaign.evaluate_proxy_1(_proxy_inputs(data, spec, frozen.spec_sha256))
        ledger.record_verdict(mission_dir, "proxy_campaign_1", result_payload(first))
        recorded = ledger.verdicts(mission_dir)
    c1 = recorded["proxy_campaign_1"]
    if c1["verdict"] != gpu_campaign.CampaignVerdict.PASS.value:
        return Judgement(c1, None)
    if "proxy_campaign_2" not in recorded:
        if "proxy_campaign_2" in opened:
            return Judgement(c1, _consumed("proxy_campaign_2"))
        refusal = refuse_unless_ready(check_proxy_2(data, spec, True))
        if refusal is not None:
            return Judgement(c1, refusal)
        ledger.open_evaluation(mission_dir, "proxy_campaign_2", requires_pass="proxy_campaign_1",
                               track=PROXY_TRACK, inputs=_input_hashes(data))
        passed = gpu_campaign.CampaignResult("proxy_campaign_1", gpu_campaign.CampaignVerdict.PASS,
                                             c1["ended_at"], {})
        second = proxy_campaign.evaluate_proxy_2(_proxy_inputs(data, spec, frozen.spec_sha256), passed)
        if second is None:  # unreachable: proxy campaign 1 PASS was checked above
            raise RuntimeError("proxy campaign 2 returned no result after a proxy campaign 1 PASS")
        ledger.record_verdict(mission_dir, "proxy_campaign_2", result_payload(second))
        recorded = ledger.verdicts(mission_dir)
    return Judgement(c1, recorded["proxy_campaign_2"])


def proxy_snapshot(data: ProxyData, mission_dir: Path = MISSION_DIR) -> dict[str, Any]:
    frozen = ledger.verify_track(mission_dir, PROXY_TRACK)
    spec = load_proxy_spec(mission_dir)
    outcome = run_proxy(data, mission_dir)
    if not isinstance(outcome, Refusal):
        raise RuntimeError("a proxy verdict is recorded; snapshots describe the pre-data state")
    return {
        "track": {"ledger_event_hash": frozen.event_hash, "frozen_at": frozen.created_at,
                  "spec_sha256": frozen.spec_sha256, "document_sha256": frozen.document_sha256,
                  "code_sha256": dict(frozen.code_sha256)},
        "spec": spec,
        "campaign_1": outcome.as_dict(),
        "campaign_2": {"campaign": "proxy_campaign_2", "refused": True,
                       "reason": "sealed until proxy campaign 1 passes",
                       "unmet": [item.as_dict() for item in check_proxy_2(data, spec, False).unmet]},
    }


def _print_refusal(outcome: Refusal) -> None:
    print(f"REFUSED ({outcome.campaign}): {outcome.reason}")
    for item in outcome.unmet:
        print(f"  - {item.requirement.key}: {item.available}/{item.requirement.required} "
              f"{item.requirement.unit} [{item.state.value}] {item.detail}")


def _print_judgement(outcome: Judgement) -> None:
    print(f"{outcome.campaign_1['campaign']}: {outcome.campaign_1['verdict']} "
          f"(ended at {outcome.campaign_1['ended_at']})")
    second = outcome.campaign_2
    if second is None:
        print("second campaign: sealed, the first did not pass")
    elif isinstance(second, Refusal):
        print(f"second campaign: refused, {second.reason}")
    else:
        print(f"{second['campaign']}: {second['verdict']} (ended at {second['ended_at']})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gpu_leads_revisions")
    sub = parser.add_subparsers(dest="command", required=True)
    show = sub.add_parser("status", help="primary track: check requirements; refuse or evaluate")
    show.add_argument("--tape", help="path or https URL of series/index_values.csv")
    proxy = sub.add_parser("proxy-status", help="proxy track: check requirements; refuse or evaluate")
    proxy.add_argument("--tape", help="path or https URL of series/index_values.csv")
    proxy.add_argument("--backfill", help=f"list-price back-series CSV (or ${proxy_inputs.BACKFILL_ENV})")
    proxy.add_argument("--facts", help=f"XBRL facts CSV (or ${proxy_inputs.FACTS_ENV})")
    proxy.add_argument("--prices", help=f"adjusted closes CSV (or ${proxy_inputs.PRICES_ENV})")
    sub.add_parser("verify", help="recompute both tracks' hashes against the ledger")
    args = parser.parse_args(argv)
    if args.command == "verify":
        try:
            frozen = ledger.verify(MISSION_DIR)
            track = ledger.verify_track(MISSION_DIR, PROXY_TRACK)
        except ledger.PreregistrationError as exc:
            print(f"FAILED: {exc}")
            return 1
        print(f"verified {frozen.mission} primary: ledger event {frozen.event_hash} ({frozen.created_at})")
        print(f"verified {frozen.mission} proxy:   ledger event {track.event_hash} ({track.created_at})")
        return 0
    if args.command == "proxy-status":
        result = run_proxy(gather_proxy(tape_source=args.tape, backfill=args.backfill,
                                        facts=args.facts, prices=args.prices))
        if isinstance(result, Refusal):
            _print_refusal(result)
            return 2
        _print_judgement(result)
        return 0
    data = gather(args.tape)
    outcome = run(data)
    if isinstance(outcome, Refusal):
        _print_refusal(outcome)
        for code, block in series_snapshot(data).items():
            signal = block["signal"]
            print(f"  {code}: {signal['message']} ({signal['history_sessions']}/"
                  f"{signal['required_sessions']} sessions)")
        return 2
    _print_judgement(outcome)
    return 0


if __name__ == "__main__":
    sys.exit(main())
