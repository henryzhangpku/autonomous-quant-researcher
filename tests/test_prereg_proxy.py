"""The proxy track: its locked arithmetic, its loaders, its refusal and its ledger entry.

Every fact, price, rate card and tape row here is a hand-written TEST FIXTURE
with invented numbers, built to make one rule bite at a time. None is SEC,
market or index data and nothing in this file is a result.
"""

from __future__ import annotations

import copy
import json
import math
import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest

from research.prereg import gpu_leads_revisions as mission
from research.prereg import ledger, proxy_campaign, xnys
from research.prereg.gpu_campaign import CampaignVerdict
from research.prereg.gpu_signal import SessionValue
from research.prereg.proxy_campaign import (
    AdjustedClose,
    FiledFact,
    MonthlyListPrice,
)
from research.prereg.requirements import Refusal, RequirementState
from research.providers import proxy_inputs

ENV = (proxy_inputs.BACKFILL_ENV, proxy_inputs.FACTS_ENV, proxy_inputs.PRICES_ENV)


@pytest.fixture(autouse=True)
def _no_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def fact(start: date, end: date, value: float, filed: date, *, symbol: str = "AAA",
         concept: str = "revenue") -> FiledFact:
    return FiledFact(symbol, concept, start, end, value, filed, f"fixture-{filed}", "FixtureTag")


# ── arithmetic ─────────────────────────────────────────────────────────────

def test_discrete_quarters_come_from_direct_or_ytd_differenced_facts() -> None:
    facts = [
        fact(date(2025, 1, 1), date(2025, 3, 31), 10, date(2025, 5, 1)),     # Q1 direct
        fact(date(2025, 1, 1), date(2025, 6, 30), 25, date(2025, 8, 1)),     # 6M YTD -> Q2 = 15
        fact(date(2025, 1, 1), date(2025, 9, 30), 45, date(2025, 11, 1)),    # 9M YTD -> Q3 = 20
        fact(date(2025, 1, 1), date(2025, 3, 31), 11, date(2025, 12, 1)),    # Q1 restated later
    ]
    assert proxy_campaign._discrete_quarters(facts, date(2025, 11, 1)) == {
        date(2025, 3, 31): 10, date(2025, 6, 30): 15, date(2025, 9, 30): 20}
    assert proxy_campaign._discrete_quarters(facts, date(2025, 12, 1))[date(2025, 3, 31)] == 11
    assert proxy_campaign.first_known(facts, date(2025, 9, 30)) == date(2025, 11, 1)


def _two_years() -> list[FiledFact]:
    out = []
    values = {2024: (10, 11, 12, 13), 2025: (12, 14, 16, 18)}
    for year, quarters in values.items():
        for q, value in enumerate(quarters):
            end = [date(year, 3, 31), date(year, 6, 30), date(year, 9, 30), date(year, 12, 31)][q]
            start = end.replace(day=1) - timedelta(days=60)
            start = start.replace(day=1)
            out.append(fact(start, end, value, end + timedelta(days=35)))
    return out


def test_acceleration_uses_only_facts_filed_before_the_decision() -> None:
    facts = _two_years()
    decision = date(2025, 9, 2)  # Q2 2025 filed 2025-08-04; Q3 2025 files 2025-11-04
    found = proxy_campaign.acceleration(facts, decision)
    assert found is not None and found.known_on == date(2025, 11, 4)
    expected = math.log(16 / 12) - math.log(14 / 11)
    assert found.value == pytest.approx(expected)
    # On the day Q3 is filed it is not yet "before" the decision; it is the next quarter.
    same_day = proxy_campaign.acceleration(facts, date(2025, 11, 4))
    assert same_day is not None and same_day.value == pytest.approx(expected)


def test_no_outcome_when_the_next_quarter_never_becomes_computable() -> None:
    facts = [f for f in _two_years() if f.period_end != date(2025, 9, 30)]
    assert proxy_campaign.acceleration(facts, date(2025, 9, 2)) is None


def test_basket_acceleration_needs_its_minimum_names() -> None:
    a = _two_years()
    b = [FiledFact("BBB", *(f.concept, f.period_start, f.period_end, f.value, f.filed,
                            f.accession, f.xbrl_tag)) for f in a]
    both = proxy_campaign.basket_acceleration(a + b, ["AAA", "BBB"], "revenue", date(2025, 9, 2),
                                              min_names=2)
    assert both is not None
    assert proxy_campaign.basket_acceleration(a, ["AAA", "BBB"], "revenue", date(2025, 9, 2),
                                              min_names=2) is None


def test_backfill_signal_requires_list_prices_and_three_rate_cards() -> None:
    rows = [MonthlyListPrice("H100", date(2025, 3, 1), 3.0, 5, "list_price"),
            MonthlyListPrice("H100", date(2025, 6, 1), 3.3, 5, "list_price")]
    assert proxy_campaign.position_from_backfill(rows, "H100", date(2025, 7, 1)) == 1
    thin = [rows[0], MonthlyListPrice("H100", date(2025, 6, 1), 3.3, 2, "list_price")]
    assert proxy_campaign.position_from_backfill(thin, "H100", date(2025, 7, 1)) is None
    labelled = [rows[0], MonthlyListPrice("H100", date(2025, 6, 1), 3.3, 5, "transaction")]
    assert proxy_campaign.position_from_backfill(labelled, "H100", date(2025, 7, 1)) is None


def test_tape_month_needs_coverage_and_one_methodology() -> None:
    october = xnys.sessions(date(2026, 10, 1), date(2026, 10, 31))
    full = [SessionValue(d, 3.0, "1.1.0") for d in october]
    assert proxy_campaign.tape_month_value(full, date(2026, 10, 1)) == (3.0, "1.1.0")
    assert proxy_campaign.tape_month_value(full[:15], date(2026, 10, 1)) is None
    mixed = full[:-1] + [SessionValue(october[-1], 3.0, "1.2.0")]
    assert proxy_campaign.tape_month_value(mixed, date(2026, 10, 1)) is None


def test_first_possible_holdout_signal_is_february_2027() -> None:
    days = xnys.sessions(date(2026, 9, 17), date(2027, 2, 26))
    series = [SessionValue(d, 3.0 + 0.001 * i, "1.1.0") for i, d in enumerate(days)]
    source = lambda _when: series  # noqa: E731 - fixture
    assert proxy_campaign.position_from_tape(source, date(2027, 1, 4)) is None
    assert proxy_campaign.position_from_tape(source, date(2027, 2, 1)) == 1


def test_forward_returns_and_costs() -> None:
    grid = [date(2025, 1, 2), date(2025, 2, 3), date(2025, 3, 3)]
    closes = [AdjustedClose(s, d, p) for s in ("A", "B") for d, p in zip(grid, (10, 11, 11))]
    closes += [AdjustedClose("SMH", d, p) for d, p in zip(grid, (100, 100, 100))]
    out = proxy_campaign.forward_excess(closes, grid, ["A", "B"], "SMH", 1, min_names=2)
    assert out[0] == (pytest.approx(math.log(1.1)), grid[1])
    assert proxy_campaign.forward_excess(closes, grid, ["A", "B", "C"], "SMH", 1, min_names=3) == {}
    captures = proxy_campaign.return_captures(grid, {0: 1, 1: -1}, out, base_bps=5, elevated_bps=10)
    assert captures[0].base == pytest.approx(math.log(1.1) - 10 / 10_000)
    assert captures[1].elevated == pytest.approx(-0.0 - 40 / 10_000)


def test_proxy_campaign_1_requires_both_outcomes(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = copy.deepcopy(mission.load_proxy_spec())
    spec["gates"]["sample_floor"]["min_months"] = {"discovery": 4, "validation": 4, "holdout": 4}
    spec["stages"]["holdout"]["closes_after_evaluable_months"] = 4
    grid = tuple(proxy_campaign.monthly_decisions(date(2024, 1, 1), date(2024, 12, 31))
                 + proxy_campaign.monthly_decisions(date(2025, 1, 1), date(2025, 12, 31))
                 + proxy_campaign.monthly_decisions(date(2026, 11, 1), date(2027, 6, 30)))
    early = date(2023, 1, 1)

    def fake_acc(facts, basket, concept, decision, *, min_names):
        sign = 1 if grid.index(decision) % 2 else -1
        drift = 0.02 if concept == "capex" else -0.05
        return proxy_campaign.Acceleration(sign * 0.01 + drift, early)

    monkeypatch.setattr(proxy_campaign, "basket_acceleration", fake_acc)
    monkeypatch.setattr(proxy_campaign, "_positions",
                        lambda inputs, gpu, holdout_only: {i: (1 if i % 2 else -1)
                                                           for i in range(len(grid))})
    inputs = proxy_campaign.ProxyInputs(spec=spec, spec_sha256="cd" * 32, grid=grid, backfill=(),
                                        index_tape={})
    result = proxy_campaign.evaluate_proxy_1(inputs)
    assert result.verdict is CampaignVerdict.PASS  # +-1 positions times +-0.01 deviations
    assert {"discovery:capex_alone", "discovery:revenue_alone", "holdout:H200_check"} <= set(result.checks)
    assert proxy_campaign.evaluate_proxy_2(inputs, result.__class__(
        "proxy_campaign_1", CampaignVerdict.FAIL, "discovery", {})) is None


# ── loaders ────────────────────────────────────────────────────────────────

def test_loaders_validate_schema_and_hash_the_bytes(tmp_path: Path) -> None:
    path = tmp_path / "backfill.csv"
    path.write_text("gpu,month,median_usd_per_gpu_hour,rate_card_count,price_type\n"
                    "H100,2025-03,3.0,5,list_price\n", encoding="utf-8")
    loaded = proxy_inputs.load_backfill(path)
    assert loaded.rows[0].month == date(2025, 3, 1) and len(loaded.sha256) == 64
    path.write_text("gpu,month,median_usd_per_gpu_hour,rate_card_count,price_type\n"
                    "H100,2025-03,3.0,5,transaction\n", encoding="utf-8")
    with pytest.raises(proxy_inputs.InputSchemaError, match="list_price"):
        proxy_inputs.load_backfill(path)
    facts = tmp_path / "facts.csv"
    facts.write_text("symbol,concept,period_start\nAAA,revenue,2025-01-01\n", encoding="utf-8")
    with pytest.raises(proxy_inputs.InputSchemaError, match="missing columns"):
        proxy_inputs.load_facts(facts)
    with pytest.raises(proxy_inputs.InputUnavailable, match=proxy_inputs.PRICES_ENV):
        proxy_inputs.load_closes()


# ── refusal ────────────────────────────────────────────────────────────────

def test_today_the_proxy_track_refuses_and_names_every_missing_input(tmp_path: Path) -> None:
    data = mission.gather_proxy(tape_source=str(tmp_path / "absent.csv"), today=date(2026, 10, 8))
    outcome = mission.run_proxy(data)
    assert isinstance(outcome, Refusal)
    assert set(outcome.missing_keys) == {
        "list_price_backfill", "xbrl_hyperscaler_capex", "xbrl_supplier_revenue",
        "proxy_index_h100_holdout", "proxy_index_h200_holdout", "proxy_index_b200_holdout"}
    backfill = next(i for i in outcome.unmet if i.requirement.key == "list_price_backfill")
    assert backfill.state is RequirementState.MISSING and backfill.requirement.required == 36
    assert proxy_inputs.BACKFILL_ENV in backfill.detail
    sealed = mission.check_proxy_2(data, mission.load_proxy_spec(), False)
    assert [i.requirement.key for i in sealed.unmet] == ["proxy_campaign_1_pass", "proxy_adjusted_closes"]


# ── ledger ─────────────────────────────────────────────────────────────────

def test_the_proxy_track_is_chained_after_the_primary_freeze() -> None:
    events = ledger.read_events(mission.MISSION_DIR / ledger.LEDGER_NAME)
    assert [e["type"] for e in events] == [ledger.FREEZE_EVENT, ledger.TRACK_EVENT]
    assert events[1]["previous_event_hash"] == events[0]["event_hash"]
    track = ledger.verify_track(mission.MISSION_DIR, mission.PROXY_TRACK)
    assert sorted(track.code_sha256) == sorted(mission.PROXY_BOUND_CODE)
    assert track.created_at.startswith("2026-10-08")


@pytest.fixture()
def proxy_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    target = root / "research" / "missions" / mission.MISSION_DIR.name
    shutil.copytree(mission.MISSION_DIR, target)
    for name in set(mission.BOUND_CODE) | set(mission.PROXY_BOUND_CODE):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ledger.ROOT / name, root / name)
    monkeypatch.setattr(ledger, "ROOT", root)
    return target


def test_editing_the_proxy_spec_breaks_only_the_proxy_check(proxy_copy: Path) -> None:
    path = proxy_copy / mission.PROXY_SPEC_NAME
    spec = json.loads(path.read_text(encoding="utf-8"))
    spec["gates"]["sample_floor"]["min_months"]["holdout"] = 6
    path.write_text(json.dumps(spec), encoding="utf-8")
    ledger.verify(proxy_copy)
    with pytest.raises(ledger.PreregistrationError, match="proxy-preregistration.json"):
        ledger.verify_track(proxy_copy, mission.PROXY_TRACK)
    outcome = mission.run_proxy(mission.gather_proxy(tape_source="absent.csv", today=date(2026, 10, 8)),
                                proxy_copy)
    assert isinstance(outcome, Refusal) and "does not verify" in outcome.reason


def test_the_receipt_records_input_hashes_and_tracks_close_after_opening(
        proxy_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from research.prereg.requirements import RequirementsReport

    monkeypatch.setattr(mission, "check_proxy_1", lambda data, spec: RequirementsReport("p", ()))
    monkeypatch.setattr(proxy_campaign, "evaluate_proxy_1", lambda inputs: proxy_campaign.CampaignResult(
        "proxy_campaign_1", CampaignVerdict.UNTESTABLE, "discovery", {}))
    data = mission.ProxyData(as_of=date(2026, 10, 8), tape=None, backfill=proxy_inputs.Loaded(
        "fixture.csv", "ef" * 32, ()), facts=None, closes=None)
    judged = mission.run_proxy(data, proxy_copy)
    assert isinstance(judged, mission.Judgement) and judged.campaign_1["verdict"] == "UNTESTABLE"
    receipt = [e for e in ledger.read_events(proxy_copy / ledger.LEDGER_NAME)
               if e["type"] == ledger.OPENED_EVENT][0]
    assert receipt["payload"] == {"campaign": "proxy_campaign_1", "inputs": {"backfill": "ef" * 32}}
    with pytest.raises(ledger.PreregistrationError, match="after any evaluation"):
        ledger.freeze_track(proxy_copy, "another", spec_name=mission.PROXY_SPEC_NAME,
                            document_name=mission.PROXY_DOC_NAME, code_paths=mission.PROXY_BOUND_CODE)


def test_the_demo_snapshot_carries_the_proxy_track_and_no_result() -> None:
    snapshot = json.loads((ledger.ROOT / "demo" / "data" / "gpu-leads-revisions.json")
                          .read_text(encoding="utf-8"))
    track = ledger.verify_track(mission.MISSION_DIR, mission.PROXY_TRACK)
    assert snapshot["proxy"]["track"]["ledger_event_hash"] == track.event_hash
    assert snapshot["proxy"]["spec"] == mission.load_proxy_spec()
    assert snapshot["proxy"]["campaign_1"]["refused"] is True
