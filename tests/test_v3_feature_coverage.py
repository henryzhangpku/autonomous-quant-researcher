"""Coverage per feature metric, admission of feature reads, and feature proposals."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from research.v3.campaign import CampaignContract, CampaignCoordinator
from research.v3.features import (CoverageState, FeatureContractError, FeatureExport,
                                  ObservationWindow, PortfolioSignalRecord, coverage_report,
                                  derive_feature_snapshot, parse_feature_key,
                                  proprietary_mission_ready, required_family_coverage_hash)
from research.v3.proposer import (COMPOUND_LANES, DeclarativeProposer, build_prompt,
                                  compound_lanes_for, lane_family, parse_proposal_json,
                                  proposal_schema, proposal_schema_for_diversity)
from research.v3.stage_store import StageAccess, materialize_stage_stores

ET = ZoneInfo("America/New_York")
VRP = "portfolio_signal.cboe_vrp"
FEATURES = {VRP: "30-day implied minus trailing 21-day realized vol (vol points)",
            "portfolio_signal.mom": "trend score"}


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _common(key: str, when: str) -> dict:
    return {"schema_version": 1, "family": "portfolio_signal", "event_key": key, "symbol": "SPY",
            "snapshot_time": when, "available_at": when, "ingested_at": when,
            "exchange_timezone": "America/New_York", "exchange_calendar": "XNYS",
            "redistribution_class": "private_derived_only",
            "source_contract_version": "evaluator-export-v1"}


def _export(days: list[date], vrp_from: date) -> FeatureExport:
    """`mom` on every close; `cboe_vrp` only from `vrp_from` (a short history)."""
    records, windows = [], []
    for day in days:
        when = datetime.combine(day, time(16, 0), ET).isoformat()
        metrics = {"mom": 0.1} | ({"cboe_vrp": 0.02} if day >= vrp_from else {})
        for metric, value in sorted(metrics.items()):
            records.append(PortfolioSignalRecord(**_common(f"{day}:{metric}", when),
                                                 event_time=when, metric=metric, value=value))
        windows.append(ObservationWindow(**_common(f"{day}:w", when), observation_time=when,
                                         observation_kind="daily_close",
                                         event_count=len(metrics)))
    return FeatureExport.create(family="portfolio_signal", source_hash=_h("x"), records=records,
                                windows=windows, created_at="2026-10-14T00:00:00+00:00")


def _weekdays(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)
            if (start + timedelta(days=i)).weekday() < 5]


def _discovery_reports(exp: FeatureExport, metric: str = ""):
    days = _weekdays(date(2016, 1, 5), date(2017, 12, 29))
    snaps = [derive_feature_snapshot({"portfolio_signal": exp}, symbol="SPY",
                                     decision_time=datetime.combine(d, time(10), ET).isoformat(),
                                     spot=100.0, required_families={"portfolio_signal"})
             for d in days]
    return coverage_report("portfolio_signal", "discovery", snaps, metric=metric)


def test_a_short_history_metric_cannot_ride_on_its_family_coverage():
    exp = _export(_weekdays(date(2016, 1, 1), date(2017, 12, 31)), vrp_from=date(2017, 9, 1))
    family = _discovery_reports(exp)
    vrp = _discovery_reports(exp, "cboe_vrp")
    assert family.state == CoverageState.READY and family.key == "portfolio_signal"
    assert vrp.state == CoverageState.INSUFFICIENT_COVERAGE and vrp.key == VRP
    assert vrp.covered_sessions < family.covered_sessions


def test_family_level_reports_hash_as_before():
    exp = _export(_weekdays(date(2016, 1, 1), date(2017, 12, 31)), vrp_from=date(2016, 1, 1))
    report = _discovery_reports(exp)
    legacy = asdict(report)
    legacy.pop("metric")
    legacy.pop("program_id")
    expected = hashlib.sha256(json.dumps([legacy], sort_keys=True, separators=(",", ":"),
                                         default=str).encode()).hexdigest()
    assert required_family_coverage_hash([report]) == expected
    assert required_family_coverage_hash([_discovery_reports(exp, "cboe_vrp")]) != expected


def test_mission_readiness_is_checked_per_required_key():
    exp = _export(_weekdays(date(2016, 1, 1), date(2017, 12, 31)), vrp_from=date(2017, 9, 1))
    family, vrp = _discovery_reports(exp), _discovery_reports(exp, "cboe_vrp")
    # discovery alone is never enough: every split needs a READY report
    assert not proprietary_mission_ready({"portfolio_signal"}, [family])
    assert not proprietary_mission_ready({VRP}, [family])
    assert not proprietary_mission_ready({VRP}, [vrp])
    assert parse_feature_key(VRP) == ("portfolio_signal", "cboe_vrp")
    for bad in ("nope", "portfolio_signal.Bad", "nope.metric"):
        with pytest.raises(FeatureContractError):
            parse_feature_key(bad)


def _contract(manifests, required=()):
    return CampaignContract("c", _h("policy"), "test",
                            {k: v.manifest_hash for k, v in manifests.items()},
                            _h("test"), 3, required_families=tuple(required))


def test_contract_accepts_metric_keys_and_rejects_unknown_ones(tmp_path: Path):
    manifests = materialize_stage_stores(tmp_path / "s", [], source_hash=_h("bars"))
    _contract(manifests, [VRP]).validate()
    with pytest.raises(ValueError):
        _contract(manifests, ["portfolio_signal.Not-OK"]).validate()


def _feature_hypothesis(metric: str) -> dict:
    return {"schema_version": 3, "entry_minute": 600,
            "signal": {"op": "gt", "observation": {"obs": "feature", "family": "portfolio_signal",
                                                    "metric": metric}, "value": 0.0},
            "structure": {"helper": "call_debit_spread", "width": 2.0, "otm_offset": 0.0}}


def test_reading_an_uncovered_metric_is_rejected_before_any_trial(tmp_path: Path):
    manifests = materialize_stage_stores(tmp_path / "s", [], source_hash=_h("bars"))
    coordinator = CampaignCoordinator(tmp_path / "c", _contract(manifests, [VRP]),
                                      lambda stage: StageAccess(tmp_path / "s", stage))
    state = coordinator.state
    coordinator._append_event(state, "discovery_started", {}, "discovery-started",
                              allowed={"ready"})
    called = []
    result = coordinator.submit_discovery(
        _feature_hypothesis("mom"), source_hash=_h("a"), attempt_id="a1",
        evaluate=lambda *args: called.append(args))
    assert not result["admitted"] and "portfolio_signal.mom" in result["reason"]
    assert called == [] and coordinator.state["scientific_trials"] == 0


def test_proposal_schema_names_exactly_the_contract_features():
    schema = proposal_schema(FEATURES)
    feature = schema["$defs"]["feature_observation"]["anyOf"][0]
    assert feature["properties"]["family"]["enum"] == ["portfolio_signal"]
    assert feature["properties"]["metric"]["enum"] == ["cboe_vrp", "mom"]
    assert "feature_observation" not in proposal_schema()["$defs"]
    prompt = build_prompt(mission="m", features=FEATURES)
    assert VRP in prompt and "PRIOR close" in prompt
    assert VRP not in build_prompt(mission="m")


def test_feature_atoms_join_the_schedule_and_the_compound_lanes():
    atom = f"gt:feature:{VRP}"
    schema = proposal_schema_for_diversity(
        (f"Required next hypothesis family must be one of: {atom}, lt:return_from_open. "
         "Repeated families are rejected before scientific evaluation.",), FEATURES)
    options = schema["$defs"]["hypothesis"]["properties"]["signal"]["anyOf"]
    assert options[0]["properties"]["observation"]["properties"]["metric"]["enum"] == ["cboe_vrp"]
    assert len(options) == 2

    lanes = compound_lanes_for(sorted([atom, "lt:return_from_open"]))
    assert lanes and all(lane.startswith(f"and|{atom}|") for lane in lanes)
    assert len({lane_family(lane) for lane in lanes}) == len(lanes)
    assert compound_lanes_for(["lt:return_from_open"]) == COMPOUND_LANES

    lane = lanes[0]
    compound = proposal_schema_for_diversity(
        (f"Required next compound lane: {lane}; spread variant A.",), FEATURES)
    items = compound["$defs"]["hypothesis"]["properties"]["signal"]["properties"]["args"]
    assert items["prefixItems"][0]["properties"]["observation"]["properties"]["obs"]["enum"] \
        == ["feature"]


def test_a_feature_proposal_round_trips_through_the_proposer():
    document = {"hypothesis": _feature_hypothesis("cboe_vrp"), "rationale": "rich premium"}
    proposer = DeclarativeProposer(lambda prompt: json.dumps(document), features=FEATURES)
    proposal = proposer.propose(mission="m")
    assert proposal.document["signal"]["observation"]["metric"] == "cboe_vrp"
    assert parse_proposal_json(json.dumps(document)).hypothesis == proposal.hypothesis
