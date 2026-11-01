"""Daily portfolio-signal features as hypothesis conditions."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from research.backtest.data import Session
from research.v3.campaign import FrozenCandidate
from research.v3.features import (FeatureContractError, FeatureExport, ObservationWindow,
                                  PortfolioSignalRecord, derive_feature_snapshot)
from research.v3.hypothesis import (HypothesisError, canonical_hypothesis, describe_hypothesis,
                                    hypothesis_hash, parse_hypothesis)
from research.v3.runtime import ModeledResearchEvaluator, build_modeled_ledgers
from research.v3.stage_store import StageAccess, materialize_stage_stores

ET = ZoneInfo("America/New_York")
FEATURE = {"obs": "feature", "family": "portfolio_signal", "metric": "trend"}
HYPOTHESIS = {
    "schema_version": 3, "entry_minute": 600,
    "signal": {"op": "gt", "observation": FEATURE, "value": 0},
    "structure": {"helper": "call_debit_spread", "width": 2.0, "otm_offset": 0.0},
}


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _close(day: date) -> str:
    return datetime.combine(day, time(16, 0), ET).isoformat()


def _common(key: str, when: str, *, available: str | None = None) -> dict:
    return {"schema_version": 1, "family": "portfolio_signal", "event_key": key, "symbol": "SPY",
            "snapshot_time": when, "available_at": available or when,
            "ingested_at": available or when, "exchange_timezone": "America/New_York",
            "exchange_calendar": "XNYS", "redistribution_class": "private_derived_only",
            "source_contract_version": "evaluator-export-v1"}


def export(scores: dict[date, float], *, available_delay: timedelta = timedelta(minutes=30),
           stamp: time = time(16, 0)) -> FeatureExport:
    records, windows = [], []
    for day, value in sorted(scores.items()):
        when = datetime.combine(day, stamp, ET).isoformat()
        published = (datetime.fromisoformat(when) + available_delay).isoformat()
        records.append(PortfolioSignalRecord(**_common(f"r{day}", when, available=published),
                                             event_time=when, metric="trend", value=value))
        windows.append(ObservationWindow(**_common(f"w{day}", when, available=published),
                                         observation_time=when, observation_kind="daily_close",
                                         event_count=1))
    return FeatureExport.create(family="portfolio_signal", source_hash=_h("scores"),
                                records=records, windows=windows,
                                created_at="2026-10-14T00:00:00+00:00")


def _snapshot(exp: FeatureExport, day: date, hm: int = 600):
    decision = datetime.combine(day, time(hm // 60, hm % 60), ET).isoformat()
    return derive_feature_snapshot({"portfolio_signal": exp}, symbol="SPY", decision_time=decision,
                                   spot=100.0, required_families={"portfolio_signal"})


def _metric(snapshot):
    family = next(f for f in snapshot.families if f.family == "portfolio_signal")
    return family.metric("trend") if family.covered else None


def test_a_score_is_visible_only_on_a_later_session():
    monday, tuesday = date(2021, 3, 1), date(2021, 3, 2)
    exp = export({monday: 1.5})
    assert _metric(_snapshot(exp, monday, hm=15 * 60 + 55)) is None   # same day: never
    assert _metric(_snapshot(exp, tuesday)) == 1.5                   # next session
    assert _metric(_snapshot(exp, monday + timedelta(days=6))) is None  # stale after 4 days


def test_an_intraday_stamped_score_is_still_only_a_next_session_input():
    monday, tuesday = date(2021, 3, 1), date(2021, 3, 2)
    early = export({monday: 2.0}, stamp=time(9, 0), available_delay=timedelta(minutes=5))
    assert _metric(_snapshot(early, monday, hm=10 * 60)) is None  # same session: never
    assert _metric(_snapshot(early, tuesday)) == 2.0


def test_a_score_published_after_the_decision_is_invisible():
    monday, tuesday = date(2021, 3, 1), date(2021, 3, 2)
    late = export({monday: 1.5}, available_delay=timedelta(hours=18))  # Tue 10:00 publication
    assert _metric(_snapshot(late, tuesday, hm=9 * 60 + 35)) is None
    assert _metric(_snapshot(late, tuesday, hm=10 * 60 + 30)) == 1.5


def test_record_bounds_and_names_are_enforced():
    when = _close(date(2021, 3, 1))
    for metric, value in (("Trend", 1.0), ("trend", 1e6), ("trend", float("nan"))):
        with pytest.raises(FeatureContractError):
            PortfolioSignalRecord(**_common("x", when), event_time=when, metric=metric,
                                  value=value).validate()


def test_hypothesis_feature_observation_rules():
    parse_hypothesis(HYPOTHESIS)
    assert "feature:portfolio_signal.trend" in describe_hypothesis(HYPOTHESIS).family
    bad = [
        {**HYPOTHESIS, "signal": {"op": "gt", "observation": {**FEATURE, "family": "flow"}, "value": 0}},
        {**HYPOTHESIS, "signal": {"op": "gt", "observation": {**FEATURE, "metric": "Bad-Name"}, "value": 0}},
        {**HYPOTHESIS, "signal": {"op": "observed", "observation": FEATURE}},
        {**HYPOTHESIS, "signal": {"op": "not", "arg": {"op": "gt", "observation": FEATURE, "value": 0}}},
    ]
    for document in bad:
        with pytest.raises(HypothesisError):
            parse_hypothesis(document)


def _sessions(start: date, count: int) -> list[Session]:
    sessions, day = [], start
    while len(sessions) < count:
        if day.weekday() < 5:
            price = 100.0 + len(sessions) * 0.1
            sessions.append(Session(day.isoformat(), (
                (570, price, price, price, price),
                (600, price - 1, price - .8, price - 1.1, price - 1),
                (945, price + 3, price + 3, price + 3, price + 3))))
        day += timedelta(days=1)
    return sessions


def _candidate(manifests: dict[str, str]) -> FrozenCandidate:
    return FrozenCandidate(canonical_document=canonical_hypothesis(HYPOTHESIS),
                           semantic_hash=hypothesis_hash(HYPOTHESIS), source_hash=_h("s"),
                           policy_hash=_h("p"), evaluator_version="test",
                           stage_manifest_hashes=manifests,
                           descriptor=describe_hypothesis(HYPOTHESIS).__dict__)


def test_runtime_selects_sessions_by_the_prior_close_score(tmp_path: Path):
    sessions = _sessions(date(2016, 1, 4), 60)
    days = [date.fromisoformat(s.day) for s in sessions]
    # alternate positive / negative scores by close
    exp = export({d: (1.0 if i % 2 == 0 else -1.0) for i, d in enumerate(days)})
    manifests = materialize_stage_stores(tmp_path / "stages", sessions, source_hash=_h("bars"))
    candidate = _candidate({k: v.manifest_hash for k, v in manifests.items()})
    access = StageAccess(tmp_path / "stages", "discovery")
    with pytest.raises(ValueError, match="no export"):
        build_modeled_ledgers(candidate, access)
    signal, control = build_modeled_ledgers(candidate, access, features={"portfolio_signal": exp})
    by_day = {row.date: row.selected for row in signal.rows}
    previous = {days[i + 1].isoformat(): i % 2 == 0 for i in range(len(days) - 1)}
    checked = [day for day in by_day if day in previous]
    assert checked and all(by_day[day] == previous[day] for day in checked)
    assert all(row.selected for row in control.rows)
    evaluation = ModeledResearchEvaluator(features={"portfolio_signal": exp}).evaluate(
        candidate, access, "discovery")
    assert evaluation.feature_manifest_hashes == {"portfolio_signal": exp.manifest.manifest_hash}
