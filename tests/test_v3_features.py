from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from research.v3.features import (
    CoverageReport,
    CoverageState,
    DarkPoolRecord,
    FeatureContractError,
    FeatureExport,
    FeatureManifest,
    FlowRecord,
    GexRecord,
    ObservationWindow,
    coverage_report,
    derive_feature_snapshot,
    proprietary_mission_ready,
    required_family_coverage_hash,
)

ET = ZoneInfo("America/New_York")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def ts(day: str, hm: int) -> str:
    return datetime.combine(
        date.fromisoformat(day), time(hm // 60, hm % 60), ET
    ).isoformat()


def common(family: str, key: str, event_time: str, *, available_at: str | None = None,
           ingested_at: str | None = None) -> dict:
    available = available_at or event_time
    return {
        "schema_version": 1,
        "family": family,
        "event_key": key,
        "symbol": "SPY",
        "snapshot_time": event_time,
        "available_at": available,
        "ingested_at": ingested_at or available,
        "exchange_timezone": "America/New_York",
        "exchange_calendar": "XNYS",
        "redistribution_class": "private_derived_only",
        "source_contract_version": "qs-cf-v1",
    }


def flow(day: str, hm: int, key: str, *, premium: float = 100,
         side: str = "buy", available_at: str | None = None,
         ingested_at: str | None = None) -> FlowRecord:
    event = ts(day, hm)
    return FlowRecord(
        **common("flow", key, event, available_at=available_at, ingested_at=ingested_at),
        event_time=event, option_type="C", inferred_side=side, premium=premium,
        dte=0, is_sweep=True, is_block=False,
    )


def window(family: str, day: str, hm: int, key: str, *, kind: str,
           event_count: int, available_at: str | None = None) -> ObservationWindow:
    observed = ts(day, hm)
    provenance = common(family, key, observed, available_at=available_at)
    if family == "gex":
        provenance["ingested_at"] = (
            datetime.fromisoformat(observed) + timedelta(seconds=60)
        ).isoformat()
    return ObservationWindow(
        **provenance,
        observation_time=observed, observation_kind=kind, event_count=event_count,
    )


def export(family: str, records=(), windows=()) -> FeatureExport:
    return FeatureExport.create(
        family=family, source_hash=digest(family), records=records, windows=windows,
        created_at="2026-08-11T12:00:00+00:00",
    )


def test_jsonl_contract_rejects_schema_provenance_sort_duplicate_finite_and_hash_failures() -> None:
    first = flow("2024-02-05", 575, "a")
    second = flow("2024-02-05", 580, "b")
    observed = window("flow", "2024-02-05", 600, "w", kind="event_window", event_count=2)
    value = export("flow", [first, second], [observed])
    assert FeatureExport.from_jsonl(value.manifest, value.to_jsonl()) == value
    malformed = value.to_jsonl().replace('"available_at":"2024-02-05T09:35:00-05:00",', "", 1)
    with pytest.raises(FeatureContractError, match="malformed"):
        FeatureExport.from_jsonl(value.manifest, malformed)

    with pytest.raises(FeatureContractError, match="schema"):
        export("flow", [replace(first, schema_version=2)], [observed])
    with pytest.raises(FeatureContractError, match="timezone/calendar"):
        export("flow", [replace(first, exchange_timezone="UTC")], [observed])
    with pytest.raises(FeatureContractError, match="sorted"):
        export("flow", [second, first], [observed])
    with pytest.raises(FeatureContractError, match="unique"):
        export("flow", [first, replace(second, event_key="a")], [observed])
    with pytest.raises(FeatureContractError, match="finite"):
        export("flow", [replace(first, premium=float("nan"))], [observed])
    with pytest.raises(FeatureContractError, match="content hash"):
        replace(value, manifest=replace(value.manifest, content_hash=digest("bad"))).validate()


def test_flow_is_causal_and_late_or_post_entry_events_have_no_effect() -> None:
    day = "2024-02-05"
    causal = flow(day, 575, "causal", premium=100)
    post_entry = flow(day, 605, "future", premium=10_000)
    late = flow(day, 580, "late", premium=20_000, available_at=ts(day, 610))
    observed = window("flow", day, 600, "window", kind="event_window", event_count=3)
    snapshot = derive_feature_snapshot(
        {"flow": export("flow", [causal, late, post_entry], [observed])},
        symbol="SPY", decision_time=ts(day, 600), spot=100, required_families=["flow"],
    )
    family = snapshot.family("flow")
    assert family.covered
    assert family.metric("trade_count") == 1
    assert family.metric("call_premium") == 100
    assert family.metric("directional_imbalance") == 1


def test_late_ingested_flow_and_window_do_not_enter_decision_snapshot() -> None:
    day = "2024-02-05"
    late = ts(day, 610)
    record = flow(day, 575, "late-record", ingested_at=late)
    observed = replace(
        window("flow", day, 600, "late-window", kind="event_window", event_count=1),
        ingested_at=late,
    )
    record_late = derive_feature_snapshot(
        {"flow": export("flow", [record], [window(
            "flow", day, 600, "ready-window", kind="event_window", event_count=1
        )])}, symbol="SPY", decision_time=ts(day, 600), spot=100,
        required_families=["flow"],
    ).family("flow")
    window_late = derive_feature_snapshot(
        {"flow": export("flow", [flow(day, 575, "ready-record")], [observed])},
        symbol="SPY", decision_time=ts(day, 600), spot=100,
        required_families=["flow"],
    ).family("flow")
    assert record_late.covered and record_late.metric("trade_count") == 0
    assert not window_late.covered


def test_zero_event_observed_window_is_covered_but_missing_window_is_not() -> None:
    day = "2024-02-05"
    zero = export(
        "flow", [], [window("flow", day, 600, "zero", kind="event_window", event_count=0)]
    )
    covered = derive_feature_snapshot(
        {"flow": zero}, symbol="SPY", decision_time=ts(day, 600), spot=100,
        required_families=["flow"],
    ).family("flow")
    missing = derive_feature_snapshot(
        {"flow": export("flow")}, symbol="SPY", decision_time=ts(day, 600), spot=100,
        required_families=["flow"],
    ).family("flow")
    assert covered.covered and covered.metric("trade_count") == 0
    assert not missing.covered


def gex(day: str, hm: int, key: str) -> GexRecord:
    event = ts(day, hm)
    provenance = common("gex", key, event)
    provenance["ingested_at"] = (datetime.fromisoformat(event) + timedelta(seconds=60)).isoformat()
    return GexRecord(
        **provenance, event_time=event,
        net_gex=10, call_gex=15, put_gex=-5, spot=100, call_wall=102,
        put_wall=98, expected_move_up=103, expected_move_down=97,
    )


def test_gex_never_projects_current_snapshot_backward_and_preserves_last_good_asof() -> None:
    stored = gex("2025-01-06", 590, "gex")
    observed = window("gex", "2025-01-06", 590, "gw", kind="snapshot", event_count=1)
    value = export("gex", [stored], [observed])
    historical = derive_feature_snapshot(
        {"gex": value}, symbol="SPY", decision_time=ts("2024-01-05", 600), spot=100,
        required_families=["gex"],
    ).family("gex")
    last_good = derive_feature_snapshot(
        {"gex": value}, symbol="SPY", decision_time=ts("2025-01-06", 600), spot=100,
        required_families=["gex"],
    ).family("gex")
    assert not historical.covered
    assert last_good.covered
    assert last_good.as_of_time == datetime.fromisoformat(ts("2025-01-06", 590)).astimezone(
        ZoneInfo("UTC")
    ).isoformat()
    assert last_good.staleness_seconds == 600
    assert last_good.metric("staleness_seconds") == 600


def test_gex_requires_a_tight_forward_capture_clock() -> None:
    value = gex("2025-01-06", 590, "backfilled-current")
    historical_ingestion = replace(value, ingested_at="2026-08-11T12:00:00+00:00")
    observed = window("gex", "2025-01-06", 590, "gw", kind="snapshot", event_count=1)
    with pytest.raises(FeatureContractError, match="forward live ledger"):
        export("gex", [historical_ingestion], [observed])


def test_gex_ingested_after_decision_is_not_visible() -> None:
    record = gex("2025-01-06", 590, "late-gex")
    decision = ts("2025-01-06", 590)  # record ingestion is one minute later
    family = derive_feature_snapshot(
        {"gex": export("gex", [record], [window(
            "gex", "2025-01-06", 590, "gw", kind="snapshot", event_count=1
        )])}, symbol="SPY", decision_time=decision, spot=100, required_families=["gex"],
    ).family("gex")
    assert not family.covered


def test_stale_same_day_flow_window_is_uncovered() -> None:
    day = "2024-02-05"
    stale = export(
        "flow", [flow(day, 570, "early")],
        [window("flow", day, 570, "old-window", kind="event_window", event_count=1)],
    )
    family = derive_feature_snapshot(
        {"flow": stale}, symbol="SPY", decision_time=ts(day, 600), spot=100,
        required_families=["flow"],
    ).family("flow")
    assert not family.covered


def dark(day: str, hm: int, key: str, price: float, premium: float,
         *, kind: str = "closed_aggregation") -> DarkPoolRecord:
    event = ts(day, hm)
    return DarkPoolRecord(
        **common("dark_pool", key, event), event_time=event,
        aggregation_kind=kind, price_level=price, premium=premium,
    )


def test_dark_pool_rejects_same_day_final_and_prior_closed_features_are_direction_neutral() -> None:
    current_day = "2025-01-07"
    same_day = export(
        "dark_pool", [dark(current_day, 960, "same", 101, 1000)],
        [window("dark_pool", current_day, 960, "sw", kind="closed_aggregation", event_count=1)],
    )
    rejected = derive_feature_snapshot(
        {"dark_pool": same_day}, symbol="SPY", decision_time=ts(current_day, 600), spot=100,
        required_families=["dark_pool"],
    ).family("dark_pool")
    assert not rejected.covered

    prior_day = "2025-01-06"
    prior = export(
        "dark_pool",
        [dark(prior_day, 960, "one", 99, 1000), dark(prior_day, 960, "two", 102, 500)],
        [window("dark_pool", prior_day, 960, "pw", kind="closed_aggregation", event_count=2)],
    )
    derived = derive_feature_snapshot(
        {"dark_pool": prior}, symbol="SPY", decision_time=ts(current_day, 600), spot=100,
        required_families=["dark_pool"],
    ).family("dark_pool")
    assert derived.covered
    assert derived.metric("nearest_level_distance_pct") == pytest.approx(1)
    names = {name for name, _ in derived.metrics}
    assert not any(token in name for name in names for token in ("buy", "sell", "bull", "bear"))


def test_dark_pool_ingested_after_decision_is_not_visible() -> None:
    prior = "2025-01-06"
    decision_day = "2025-01-07"
    late = ts(decision_day, 610)
    record = replace(dark(prior, 960, "late-dark", 99, 1000), ingested_at=late)
    observed = replace(window(
        "dark_pool", prior, 960, "late-window", kind="closed_aggregation", event_count=1
    ), ingested_at=late)
    family = derive_feature_snapshot(
        {"dark_pool": export("dark_pool", [record], [observed])}, symbol="SPY",
        decision_time=ts(decision_day, 600), spot=100, required_families=["dark_pool"],
    ).family("dark_pool")
    assert not family.covered


def test_zero_event_windows_cannot_contradict_matching_raw_rows() -> None:
    day = "2024-02-05"
    with pytest.raises(FeatureContractError, match="zero-event"):
        export(
            "flow", [flow(day, 575, "flow-row")],
            [window("flow", day, 600, "flow-zero", kind="event_window", event_count=0)],
        )
    with pytest.raises(FeatureContractError, match="zero-event"):
        export(
            "dark_pool", [dark(day, 960, "dark-row", 100, 50)],
            [window(
                "dark_pool", day, 960, "dark-zero",
                kind="closed_aggregation", event_count=0,
            )],
        )


def synthetic_snapshot(day: date, family: str, covered: bool):
    derived = derive_feature_snapshot(
        {}, symbol="SPY", decision_time=ts(day.isoformat(), 600), spot=100,
        required_families=[family],
    )
    if not covered:
        return derived
    item = replace(
        derived.family(family), covered=True, as_of_time=ts(day.isoformat(), 595),
        staleness_seconds=300, source_hash=digest("source"),
        export_manifest_hash=digest("manifest"), metrics=(("observed", 1.0),),
    )
    from research.v3.features import FeatureSnapshot
    return FeatureSnapshot.create("SPY", derived.decision_time, [item])


def snapshots(start: date, weeks: int, family: str, *, missing: int = 0):
    return [synthetic_snapshot(start + timedelta(days=7 * index), family, index >= missing)
            for index in range(weeks)]


def test_coverage_states_week_ratio_missing_split_and_price_only_bypass() -> None:
    unavailable = coverage_report(
        "flow", "discovery", [], source_available=False
    )
    insufficient = coverage_report(
        "flow", "discovery", snapshots(date(2016, 1, 4), 79, "flow")
    )
    ratio_fail = coverage_report(
        "flow", "discovery", snapshots(date(2016, 1, 4), 100, "flow", missing=11)
    )
    ready = coverage_report(
        "flow", "discovery", snapshots(date(2016, 1, 4), 80, "flow")
    )
    assert unavailable.state == CoverageState.UNAVAILABLE
    assert insufficient.state == CoverageState.INSUFFICIENT_COVERAGE
    assert ratio_fail.coverage_ratio == .89 and ratio_fail.state == CoverageState.INSUFFICIENT_COVERAGE
    assert ready.state == CoverageState.READY

    reports = [
        ready,
        CoverageReport("flow", "validation", CoverageState.READY, 40, 40, 1, 40, 40, True),
    ]
    assert not proprietary_mission_ready(["flow"], reports)
    reports.append(CoverageReport(
        "flow", "holdout", CoverageState.READY, 20, 20, 1, 20, 20, True
    ))
    assert proprietary_mission_ready(["flow"], reports)
    assert proprietary_mission_ready([], [])


def test_snapshot_portable_surface_contains_only_derived_aggregates_and_hashes() -> None:
    day = "2024-02-05"
    value = export(
        "flow", [flow(day, 575, "secret-raw-event")],
        [window("flow", day, 600, "window", kind="event_window", event_count=1)],
    )
    snapshot = derive_feature_snapshot(
        {"flow": value}, symbol="SPY", decision_time=ts(day, 600), spot=100,
        required_families=["flow"],
    )
    portable = json.dumps(snapshot.to_portable_dict(), sort_keys=True)
    assert "secret-raw-event" not in portable
    for forbidden in ("event_key", "path", "provider", "credential", "api_key", "cookie"):
        assert forbidden not in portable.lower()
    assert not hasattr(snapshot, "records")
    assert not hasattr(snapshot, "network")
