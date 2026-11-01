from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from research.v3.opra import (
    DecisionLeg,
    DecisionManifest,
    DecisionRow,
    EntitlementResult,
    EntitlementStatus,
    OpraBlock,
    OpraBlockState,
    OpraBoundaryError,
    ProviderPrice,
    collect_opra,
    evaluate_opra_block,
    replay_opra,
    validate_opra_block,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def decision_row(day: str, *, settlement: float = 2.0, selected: bool = True) -> DecisionRow:
    suffix = day.replace("-", "")[2:]
    return DecisionRow(
        day,
        True,
        selected,
        600,
        "call debit 100/102",
        2.0,
        settlement,
        2.0,
        0.0,
        "raw_unadjusted",
        (
            DecisionLeg(f"SPY{suffix}C00100000", "C", 100.0, 1),
            DecisionLeg(f"SPY{suffix}C00102000", "C", 102.0, -1),
        ) if selected else (),
    )


def manifest(rows, *, stage: str = "validation", receipt: str | None = None) -> DecisionManifest:
    return DecisionManifest.create(
        stage=stage,
        candidate_hash=digest("candidate"),
        campaign_contract_hash=digest("campaign"),
        stage_manifest_hash=digest(stage),
        receipt_id=receipt,
        requested_provider="alpaca-test",
        rows=rows,
        created_at="2026-08-11T00:00:00+00:00",
    )


class Provider:
    provider_id = "alpaca-test"

    def __init__(self, *, available: bool = True, missing: set[tuple[str, str]] | None = None,
                 feed: str = "opra", expensive: bool = False):
        self.available = available
        self.missing = missing or set()
        self.feed = feed
        self.expensive = expensive
        self.fetch_count = 0

    def check_entitlement(self, requested_feed: str) -> EntitlementResult:
        assert requested_feed == "opra"
        return EntitlementResult(
            EntitlementStatus.AVAILABLE if self.available else EntitlementStatus.UNAVAILABLE,
            "fixture",
        )

    def fetch_leg(self, contract: str, day: date, entry_minute: int,
                  requested_feed: str, tolerance_seconds: int):
        self.fetch_count += 1
        if (day.isoformat(), contract) in self.missing:
            return None
        target = datetime.combine(day, time(10, 0), ZoneInfo("America/New_York")).astimezone(
            timezone.utc
        )
        is_long = contract.endswith("100000")
        if self.expensive:
            bid, ask = ((1.98, 1.99) if is_long else (.01, .02))
        else:
            bid, ask = ((.79, .80) if is_long else (.20, .21))
        return ProviderPrice(
            self.feed, "quote", target.isoformat(),
            (target + timedelta(days=1)).isoformat(), bid=bid, ask=ask,
        )


def weekly_rows(count: int = 30, *, start: date = date(2024, 2, 5),
                weeks: int | None = None, settlement: float = 2.0) -> list[DecisionRow]:
    rows = []
    for index in range(count):
        week = index if weeks is None else index % weeks
        within = 0 if weeks is None else index // weeks
        day = start + timedelta(days=7 * week + within)
        rows.append(decision_row(day.isoformat(), settlement=settlement))
    return sorted(rows, key=lambda item: item.date)


def test_decision_manifest_is_code_free_and_integrity_tampering_fails() -> None:
    value = manifest([decision_row("2024-02-05")])
    assert "hypothesis" not in repr(value).lower()
    assert "source" not in repr(value).lower()
    with pytest.raises(OpraBoundaryError, match="integrity"):
        replace(value, candidate_hash=digest("tampered")).validate()


def test_unavailable_entitlement_never_calls_prices_and_maps_unavailable() -> None:
    decisions = manifest([decision_row("2024-02-05")])
    provider = Provider(available=False)
    artifact = collect_opra(decisions, provider)
    assert provider.fetch_count == 0
    ledger = replay_opra(decisions, artifact)
    assert ledger.selected_count == 1 and ledger.priced_selected_count == 0
    assert evaluate_opra_block(decisions, artifact).state == OpraBlockState.UNAVAILABLE


def test_unavailable_block_rejects_forged_priced_aggregate_evidence() -> None:
    decisions = manifest([decision_row("2024-02-05")])
    block = evaluate_opra_block(decisions, collect_opra(decisions, Provider(available=False)))

    with pytest.raises(OpraBoundaryError, match="zero priced coverage"):
        validate_opra_block(
            replace(block, priced_sessions=1, effective_weeks=1, coverage=1.0),
            holdout=False,
        )


def test_requested_opra_cannot_fall_back_to_indicative() -> None:
    decisions = manifest([decision_row("2024-02-05")])
    with pytest.raises(OpraBoundaryError, match="cannot fall back"):
        collect_opra(decisions, Provider(feed="indicative"))


def test_actual_quote_replay_uses_long_ask_short_bid_and_no_provider_callback() -> None:
    decisions = manifest([decision_row("2024-02-05")])
    provider = Provider()
    artifact = collect_opra(decisions, provider)
    calls_after_collection = provider.fetch_count
    ledger = replay_opra(decisions, artifact)
    row = ledger.rows[0]
    assert row.price_source == "opra"
    assert row.signed_entry_debit == pytest.approx(.60)
    assert row.gross_pnl == pytest.approx(1.40)
    assert provider.fetch_count == calls_after_collection


def test_missing_leg_retains_selected_unpriced_coverage_denominator() -> None:
    decisions = manifest([decision_row("2024-02-05"), decision_row("2024-02-12")])
    missing_leg = decisions.rows[1].legs[0].contract
    provider = Provider(missing={("2024-02-12", missing_leg)})
    ledger = replay_opra(decisions, collect_opra(decisions, provider))
    assert ledger.selected_count == 2
    assert ledger.priced_selected_count == 1
    assert ledger.coverage == .5
    assert ledger.rows[1].skip_reason == "no_opra_price"


def test_opra_block_thresholds_and_positive_actual_evidence() -> None:
    decisions = manifest(weekly_rows())
    passed = evaluate_opra_block(decisions, collect_opra(decisions, Provider()))
    assert passed.state == OpraBlockState.PASSED
    assert passed.coverage == 1
    assert passed.priced_sessions == 30
    assert passed.effective_weeks == 30
    assert passed.base_mean_pnl_pct > 0
    assert passed.base_lower_bound_pct > 0
    assert passed.elevated_mean_pnl_pct > 0

    expensive = evaluate_opra_block(decisions, collect_opra(decisions, Provider(expensive=True)))
    assert expensive.state == OpraBlockState.FAILED_EVIDENCE
    assert expensive.base_mean_pnl_pct < 0


def test_coverage_session_and_week_floors_are_independent() -> None:
    thirty = weekly_rows()
    decisions = manifest(thirty)
    missing = {
        (row.date, row.legs[0].contract)
        for row in thirty[:10]
    }
    low_coverage = evaluate_opra_block(
        decisions, collect_opra(decisions, Provider(missing=missing))
    )
    assert low_coverage.state == OpraBlockState.INSUFFICIENT_COVERAGE
    assert low_coverage.coverage < .70

    twenty_nine = manifest(weekly_rows(29))
    too_few = evaluate_opra_block(twenty_nine, collect_opra(twenty_nine, Provider()))
    assert too_few.state == OpraBlockState.INSUFFICIENT_COVERAGE
    assert too_few.priced_sessions == 29

    eleven_weeks = manifest(weekly_rows(30, weeks=11))
    clustered = evaluate_opra_block(
        eleven_weeks, collect_opra(eleven_weeks, Provider())
    )
    assert clustered.state == OpraBlockState.INSUFFICIENT_COVERAGE
    assert clustered.effective_weeks == 11

    forty = weekly_rows(40)
    exactly_seventy_five = manifest(forty)
    missing_ten = {(row.date, row.legs[0].contract) for row in forty[:10]}
    threshold_pass = evaluate_opra_block(
        exactly_seventy_five,
        collect_opra(exactly_seventy_five, Provider(missing=missing_ten)),
    )
    assert threshold_pass.coverage == .75
    assert threshold_pass.priced_sessions == 30
    assert threshold_pass.state == OpraBlockState.PASSED


def test_validation_window_and_holdout_receipt_are_strict() -> None:
    with pytest.raises(OpraBoundaryError, match="Feb-Dec 2024"):
        manifest([decision_row("2024-01-31")])
    with pytest.raises(OpraBoundaryError, match="durable receipt"):
        manifest([decision_row("2025-01-06")], stage="holdout")
    receipt = digest("receipt")
    holdout = manifest([decision_row("2025-01-06")], stage="holdout", receipt=receipt)
    artifact = collect_opra(holdout, Provider())
    with pytest.raises(OpraBoundaryError, match="provenance"):
        replace(artifact, receipt_id=digest("wrong")).validate(holdout)


def test_quote_provenance_and_content_tampering_fail_closed() -> None:
    decisions = manifest([decision_row("2024-02-05")])
    artifact = collect_opra(decisions, Provider())
    with pytest.raises(OpraBoundaryError, match="integrity"):
        replace(artifact, content_hash=digest("tampered")).validate(decisions)
    wrong = replace(artifact, stage_manifest_hash=digest("wrong"))
    with pytest.raises(OpraBoundaryError, match="provenance"):
        wrong.validate(decisions)

    # A new matching content hash does not legitimize a changed fill rule.
    long = artifact.evidence[0]
    altered_long = replace(long, chosen_value=long.bid)
    semantic_tamper = replace(
        artifact, evidence=(altered_long, *artifact.evidence[1:]), content_hash=""
    )
    semantic_tamper = replace(semantic_tamper, content_hash=semantic_tamper.expected_hash())
    with pytest.raises(OpraBoundaryError, match="executable leg side"):
        semantic_tamper.validate(decisions)


def test_forged_passed_opra_block_fails_public_validation() -> None:
    forged = OpraBlock(
        OpraBlockState.PASSED, EntitlementStatus.AVAILABLE,
        40, 20, .5, 8, 3.0, 1.0, 2.0,
        digest("decision"), digest("quotes"), None, (),
    )
    with pytest.raises(OpraBoundaryError, match="coverage floors"):
        validate_opra_block(forged, holdout=False)
    with pytest.raises(OpraBoundaryError, match="entitlement"):
        validate_opra_block(
            replace(forged, priced_sessions=35, coverage=.875, effective_weeks=14,
                    entitlement=EntitlementStatus.UNAVAILABLE),
            holdout=False,
        )
