"""The quote-priced program: schedule, quote store, quoted ledgers and gates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from research.backtest.data import Session
from research.v3.campaign import CampaignContract, FrozenCandidate
from research.v3.evaluator import STAGE_FLOORS, Stage, evaluate_ledger
from research.v3.features import COVERAGE_WEEK_FLOORS
from research.v3.hypothesis import canonical_hypothesis, describe_hypothesis, hypothesis_hash
from research.v3.ledger import GrossTradeLedger, GrossTradeRow
from research.v3.program import SPY_PRICE_V3, SPY_QUOTE_V1
from research.v3.quote_store import (GRID, QuoteAccess, grid_prices,
                                     materialize_quote_stores)
from research.v3.runtime import QuotedResearchEvaluator, build_modeled_ledgers
from research.v3.stage_store import STAGE_DATES, StageAccess, materialize_stage_stores

HYPOTHESIS = {
    "schema_version": 3, "entry_minute": 600,
    "signal": {"op": "gt", "observation": {"obs": "return_from_open", "minute": 600},
               "value": -100},
    "structure": {"helper": "call_debit_spread", "width": 2.0, "otm_offset": 0.0},
}


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_the_original_program_keeps_its_schedule_and_gates():
    assert dict(SPY_PRICE_V3.stage_dates) == STAGE_DATES
    assert dict(SPY_PRICE_V3.coverage_week_floors) == COVERAGE_WEEK_FLOORS
    for stage, floor in STAGE_FLOORS.items():
        f = SPY_PRICE_V3.floors[stage.value]
        assert (f.raw_trades, f.effective_weeks, f.per_period_trades, f.per_period_weeks) == (
            floor.raw_trades, floor.effective_weeks, floor.per_year_trades, floor.per_year_weeks)
    assert SPY_PRICE_V3.evaluator_version == "spy-options-staged-v3.2"


def test_a_grid_price_never_comes_from_a_bar_that_ends_after_the_decision():
    # bar stamped 599 covers [599, 600): usable at 600; bar stamped 600 is not
    prices = dict(zip(GRID, grid_prices([(599, 1.0), (600, 9.0)])))
    assert prices[600] == 1.0 and prices[605] == 9.0
    stale = dict(zip(GRID, grid_prices([(570, 2.0)])))
    assert stale[585] == 2.0 and stale[590] is None     # older than 15 minutes
    assert stale[570] is None                            # nothing has ended by the open


def _record(day: str, calls: dict[int, float], puts: dict[int, float] | None = None) -> dict:
    return {"day": day,
            "C": {str(k): [v] * len(GRID) for k, v in calls.items()},
            "P": {str(k): [v] * len(GRID) for k, v in (puts or {}).items()}}


def test_quote_stores_are_per_stage_and_tamper_evident(tmp_path: Path):
    records = [_record("2024-03-04", {500: 1.0}), _record("2025-02-03", {600: 2.0}),
               _record("2025-08-04", {650: 3.0})]
    manifests = materialize_quote_stores(tmp_path, records, source_hash=_h("q"),
                                         program=SPY_QUOTE_V1)
    assert {k: m.sessions for k, m in manifests.items()} == {
        "discovery": 1, "validation": 1, "holdout": 1}
    days = QuoteAccess(tmp_path, "discovery", SPY_QUOTE_V1).open_days()
    assert list(days) == ["2024-03-04"]
    assert days["2024-03-04"].price("C", 500.0, 600) == 1.0
    assert days["2024-03-04"].price("C", 500.5, 600) is None
    with pytest.raises(ValueError):
        QuoteAccess(tmp_path, "discovery", SPY_PRICE_V3).manifest()
    with pytest.raises(FileExistsError):
        materialize_quote_stores(tmp_path, records, source_hash=_h("q"), program=SPY_QUOTE_V1)
    path = tmp_path / "validation" / "quotes.jsonl"
    path.chmod(0o644)
    path.write_bytes(path.read_bytes().replace(b"2.0", b"2.5"))
    with pytest.raises(ValueError, match="hash"):
        QuoteAccess(tmp_path, "validation", SPY_QUOTE_V1).open_days()


def _sessions(start: date, count: int, price: float = 500.0) -> list[Session]:
    out, day = [], start
    while len(out) < count:
        if day.weekday() < 5:
            out.append(Session(day.isoformat(), (
                (570, price, price, price, price), (600, price, price, price, price),
                (955, price + 3, price + 3, price + 3, price + 3))))
        day += timedelta(days=1)
    return out


def _candidate(manifests) -> FrozenCandidate:
    return FrozenCandidate(canonical_document=canonical_hypothesis(HYPOTHESIS),
                           semantic_hash=hypothesis_hash(HYPOTHESIS), source_hash=_h("s"),
                           policy_hash=_h("p"), evaluator_version="test",
                           stage_manifest_hashes=manifests,
                           descriptor=describe_hypothesis(HYPOTHESIS).__dict__)


def test_legs_are_priced_from_quotes_and_unquoted_legs_are_never_modeled(tmp_path: Path):
    sessions = _sessions(date(2024, 2, 5), 12)
    manifests = materialize_stage_stores(tmp_path / "stages", sessions, source_hash=_h("bars"),
                                         program=SPY_QUOTE_V1)
    # every day but the last quotes both legs (500 long call, 502 short call)
    records = [_record(s.day, {500: 1.2, 502: 0.4}) for s in sessions[:-1]]
    records.append(_record(sessions[-1].day, {500: 1.2}))
    materialize_quote_stores(tmp_path / "quotes", records, source_hash=_h("q"),
                             program=SPY_QUOTE_V1)
    candidate = _candidate({k: v.manifest_hash for k, v in manifests.items()})
    access = StageAccess(tmp_path / "stages", "discovery", SPY_QUOTE_V1)
    quotes = QuoteAccess(tmp_path / "quotes", "discovery", SPY_QUOTE_V1).open_days()
    signal, _ = build_modeled_ledgers(candidate, access, quotes=quotes)
    priced = [r for r in signal.rows if r.priced]
    assert priced and all(r.price_source == "indicative" for r in signal.rows)
    assert all(r.signed_entry_debit == pytest.approx(0.8) for r in priced)
    assert all(r.gross_settlement == pytest.approx(2.0) for r in priced)   # close 503
    last = next(r for r in signal.rows if r.date == sessions[-1].day)
    assert not last.priced and "freshness" in last.skip_reason

    evaluation = QuotedResearchEvaluator(tmp_path / "quotes", SPY_QUOTE_V1).evaluate(
        candidate, access, Stage.DISCOVERY)
    assert evaluation.evaluator_version == "spy-quote-staged-v1"
    assert evaluation.price_source_id.startswith("indicative:")
    with pytest.raises(PermissionError):
        QuotedResearchEvaluator(tmp_path / "quotes", SPY_QUOTE_V1).evaluate(
            candidate, StageAccess(tmp_path / "stages", "discovery"), Stage.DISCOVERY)
    with pytest.raises(ValueError):
        QuotedResearchEvaluator(tmp_path / "quotes", SPY_PRICE_V3)


def _row(day: str, pnl: float, priced: bool = True) -> GrossTradeRow:
    if not priced:
        return GrossTradeRow(day, True, False, "s", None, None, None, None, None, None,
                             "indicative", skip_reason="no quote")
    return GrossTradeRow(day, True, True, "s", 2.0, 1.0, 1.0 + pnl, 2.0, 0.0, pnl,
                         "indicative", (("C", 1.0, 1, 1.0),))


def _validation(rows):
    return evaluate_ledger(GrossTradeLedger(rows), stage=Stage.VALIDATION, candidate_hash=_h("c"),
                           stage_manifest_hash=_h("m"), price_source_id="indicative:x",
                           price_source_kind="indicative",
                           program=SPY_QUOTE_V1)


def test_quote_program_validation_is_gated_per_quarter_and_tolerates_unpriced_sessions():
    days = [date(2025, 1, 2) + timedelta(days=i) for i in range(180)]
    days = [d.isoformat() for d in days if d.weekday() < 5]
    q1 = [d for d in days if d < "2025-04-01"]
    rows = [_row(d, 1.0) for d in days]
    evaluation = _validation(rows)
    assert not any("2025Q" in f for f in evaluation.failures)
    assert not any("priced" in f for f in evaluation.failures)
    only_q1 = _validation([_row(d, 1.0) for d in q1])
    assert "validation must contain evidence for 2025Q1 and 2025Q2" in only_q1.failures
    q2_loses = _validation([_row(d, 1.0 if d in q1 else -1.0) for d in days])
    assert "2025Q2 base-cost net mean P&L is negative" in q2_loses.failures
    sparse = _validation([_row(d, 1.0, priced=i % 5 != 0) for i, d in enumerate(days)])
    assert any("of selected sessions are priced" in f for f in sparse.failures)


def test_contracts_bind_a_registered_program():
    contract = CampaignContract("c", _h("p"), SPY_QUOTE_V1.evaluator_version,
                                {s: _h(s) for s in ("discovery", "validation", "holdout")},
                                _h("m"), 5, research_scope=SPY_QUOTE_V1.scope,
                                research_scope_hash=hashlib.sha256(json.dumps(
                                    SPY_QUOTE_V1.scope, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest())
    contract.validate()
    assert contract.program is SPY_QUOTE_V1
    with pytest.raises(ValueError):
        replace(contract, research_scope={**SPY_QUOTE_V1.scope, "symbol": "QQQ"}).validate()


def test_leg_prints_outside_the_spread_payoff_range_are_unpriced(tmp_path: Path):
    sessions = _sessions(date(2024, 2, 5), 3)
    manifests = materialize_stage_stores(tmp_path / "stages", sessions, source_hash=_h("bars"),
                                         program=SPY_QUOTE_V1)
    # a 2-wide call debit spread cannot cost more than 2: stale prints say 3.1
    records = [_record(s.day, {500: 3.5, 502: 0.4}) for s in sessions]
    materialize_quote_stores(tmp_path / "quotes", records, source_hash=_h("q"),
                             program=SPY_QUOTE_V1)
    candidate = _candidate({k: v.manifest_hash for k, v in manifests.items()})
    access = StageAccess(tmp_path / "stages", "discovery", SPY_QUOTE_V1)
    quotes = QuoteAccess(tmp_path / "quotes", "discovery", SPY_QUOTE_V1).open_days()
    signal, _ = build_modeled_ledgers(candidate, access, quotes=quotes)
    assert signal.rows and not any(r.priced for r in signal.rows)
    assert all("no-arbitrage" in r.skip_reason for r in signal.rows)
