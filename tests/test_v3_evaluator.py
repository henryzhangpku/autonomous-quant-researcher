from __future__ import annotations

import hashlib
import math
from datetime import date, timedelta

import pytest

from research.v3.evaluator import (
    PriceSourceKind,
    Stage,
    StageFloor,
    bootstrap_seed,
    evaluate_ledger,
    weekly_cluster_lower_bound,
)
from research.v3.ledger import GrossTradeLedger, GrossTradeRow


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def row(day: str, *, debit: float, settlement: float, best: float, worst: float,
        width: float = 2.0, selected: bool = True, priced: bool = True,
        skip_reason: str | None = None, price_source: str = "modeled") -> GrossTradeRow:
    return GrossTradeRow(
        day, selected, priced, "vertical", width if priced else None,
        debit if priced else None, settlement if priced else None,
        best if priced else None, worst if priced else None,
        settlement - debit if priced else None, price_source, skip_reason=skip_reason,
    )


def relaxed() -> dict[Stage, StageFloor]:
    return {stage: StageFloor(1, 1, 1, 1) for stage in Stage}


def test_debit_exact_full_win_edge_oracle() -> None:
    ledger = GrossTradeLedger([
        row("2020-01-06", debit=.8, settlement=2, best=2, worst=0),
        row("2020-01-13", debit=.8, settlement=2, best=2, worst=0),
        row("2020-01-20", debit=.8, settlement=0, best=2, worst=0),
    ])
    result = evaluate_ledger(ledger, stage=Stage.DISCOVERY, candidate_hash=digest("a"),
                             stage_manifest_hash=digest("b"), price_source_id="modeled", floors=relaxed())
    assert result.absolute_full_win_edge_pp == pytest.approx(23.666666666666664)


def test_credit_exact_full_win_edge_oracle() -> None:
    ledger = GrossTradeLedger([
        row(f"2020-01-{6 + i * 7:02d}", debit=-.6, settlement=0 if i < 3 else -2,
            best=0, worst=-2) for i in range(4)
    ])
    result = evaluate_ledger(ledger, stage=Stage.DISCOVERY, candidate_hash=digest("a"),
                             stage_manifest_hash=digest("b"), price_source_id="modeled", floors=relaxed())
    assert result.absolute_full_win_edge_pp == pytest.approx(2.0)


def test_partial_settlement_is_not_a_full_win_and_bad_break_even_fails_closed() -> None:
    partial = GrossTradeLedger([row("2020-01-06", debit=.8, settlement=1.99, best=2, worst=0)])
    result = evaluate_ledger(partial, stage=Stage.DISCOVERY, candidate_hash=digest("a"),
                             stage_manifest_hash=digest("b"), price_source_id="modeled", floors=relaxed())
    assert result.absolute_full_win_edge_pp == pytest.approx(-43)
    uneconomic = GrossTradeLedger([row("2020-01-06", debit=2.1, settlement=2, best=2, worst=0)])
    failed = evaluate_ledger(uneconomic, stage=Stage.DISCOVERY, candidate_hash=digest("a"),
                             stage_manifest_hash=digest("b"), price_source_id="modeled", floors=relaxed())
    assert not failed.eligible
    assert any("outside [0, 1]" in message for message in failed.failures)


def test_costs_above_max_credit_remain_finite_and_fail_absolute_gates() -> None:
    ledger = GrossTradeLedger([
        row("2020-01-06", debit=-.01, settlement=0, best=0, worst=-2),
        row("2020-01-13", debit=-.01, settlement=-2, best=0, worst=-2),
    ])

    result = evaluate_ledger(
        ledger, stage=Stage.DISCOVERY, candidate_hash=digest("low-credit"),
        stage_manifest_hash=digest("manifest"), price_source_id="modeled",
        floors=relaxed(),
    )

    assert result.absolute_full_win_edge_pp == pytest.approx(-52.5)
    assert result.base.mean_pnl_pct == pytest.approx(-52.5)
    assert result.elevated.mean_pnl_pct == pytest.approx(-54.0)
    assert all("invalid ledger economics" not in failure for failure in result.failures)
    assert "absolute full-win edge is not positive" in result.failures
    assert "base-cost net mean P&L is not positive" in result.failures


def test_one_low_credit_row_does_not_discard_other_finite_evidence() -> None:
    ledger = GrossTradeLedger([
        row("2020-01-06", debit=-.01, settlement=0, best=0, worst=-2),
        row("2020-01-13", debit=-.60, settlement=0, best=0, worst=-2),
    ])

    result = evaluate_ledger(
        ledger, stage=Stage.DISCOVERY, candidate_hash=digest("mixed-credit"),
        stage_manifest_hash=digest("manifest"), price_source_id="modeled",
        floors=relaxed(),
    )

    assert math.isfinite(result.absolute_full_win_edge_pp)
    assert math.isfinite(result.base.mean_pnl_pct)
    assert math.isfinite(result.base.lower_bound_pct)
    assert all("invalid ledger economics" not in failure for failure in result.failures)


def test_roundoff_at_gross_payoff_boundary_is_clamped_not_rejected() -> None:
    ledger = GrossTradeLedger([
        row("2020-01-06", debit=2.0 + 5e-12, settlement=2, best=2, worst=0),
    ])

    result = evaluate_ledger(
        ledger, stage=Stage.DISCOVERY, candidate_hash=digest("roundoff"),
        stage_manifest_hash=digest("manifest"), price_source_id="modeled",
        floors=relaxed(),
    )

    assert result.absolute_full_win_edge_pp == pytest.approx(-3.0)
    assert math.isfinite(result.base.mean_pnl_pct)
    assert all("invalid ledger economics" not in failure for failure in result.failures)


def test_missing_selected_price_fails_closed_but_remains_in_ledger() -> None:
    ledger = GrossTradeLedger([
        row("2020-01-06", debit=.1, settlement=2, best=2, worst=0),
        row("2020-01-07", debit=0, settlement=0, best=0, worst=0,
            priced=False, skip_reason="no price"),
    ])
    result = evaluate_ledger(ledger, stage=Stage.DISCOVERY, candidate_hash=digest("a"),
                             stage_manifest_hash=digest("b"), price_source_id="modeled", floors=relaxed())
    assert not result.eligible
    assert "selected rows are missing required prices" in result.failures
    assert result.signal.selected_count == 2
    assert result.signal.priced_count == 1
    assert result.signal.coverage == pytest.approx(.5)
    assert result.signal.skip_reason_counts == {"no price": 1}
    assert result.control is None
    assert result.price_source_id == "modeled"
    assert result.price_source_kind == PriceSourceKind.MODELED
    assert result.stage_manifest_hash == digest("b")


def test_weekly_cluster_bootstrap_and_seed_are_deterministic() -> None:
    outcomes = [("2020-01-06", .1), ("2020-01-07", .2), ("2020-01-13", .3)]
    seed = bootstrap_seed("candidate", "manifest", "modeled", "base")
    assert seed == bootstrap_seed("candidate", "manifest", "modeled", "base")
    first = weekly_cluster_lower_bound(outcomes, alpha=.05, seed=seed)
    second = weekly_cluster_lower_bound(outcomes, alpha=.05, seed=seed)
    assert first == second


def test_cost_stress_reuses_gross_ledger_and_can_veto_candidate() -> None:
    # Gross return 4% of width: positive after 3%, negative after 4.5%.
    ledger = GrossTradeLedger([
        row("2020-01-06", debit=1.92, settlement=2, best=2, worst=0),
        row("2020-01-13", debit=1.92, settlement=2, best=2, worst=0),
    ])
    result = evaluate_ledger(ledger, stage=Stage.DISCOVERY, candidate_hash=digest("a"),
                             stage_manifest_hash=digest("b"), price_source_id="modeled", floors=relaxed())
    assert result.base.mean_pnl_pct == pytest.approx(1.0)
    assert result.elevated.mean_pnl_pct == pytest.approx(-.5)
    assert not result.eligible


def test_control_relative_is_diagnostic_and_cannot_override_absolute_loss() -> None:
    signal = GrossTradeLedger([row("2020-01-06", debit=.9, settlement=0, best=2, worst=0)])
    control = GrossTradeLedger([row("2020-01-06", debit=1.5, settlement=0, best=2, worst=0)])
    result = evaluate_ledger(signal, control_ledger=control, stage=Stage.DISCOVERY,
                             candidate_hash=digest("a"), stage_manifest_hash=digest("b"),
                             price_source_id="modeled", floors=relaxed())
    assert result.control_relative_edge_pp is not None and result.control_relative_edge_pp > 0
    assert result.control is not None
    assert result.control.selected_count == result.control.priced_count == 1
    assert result.by_year["2020"].control is not None
    assert result.by_year["2020"].control_relative_edge_pp == pytest.approx(
        result.control_relative_edge_pp
    )
    assert result.absolute_full_win_edge_pp < 0
    assert not result.eligible


def test_invalid_control_provenance_remains_diagnostic_only() -> None:
    signal = GrossTradeLedger([
        row("2020-01-06", debit=.1, settlement=2, best=2, worst=0),
    ])
    control = GrossTradeLedger([
        row("2020-01-06", debit=.1, settlement=2, best=2, worst=0,
            price_source="indicative"),
    ])

    result = evaluate_ledger(
        signal, control_ledger=control, stage=Stage.DISCOVERY,
        candidate_hash=digest("candidate"), stage_manifest_hash=digest("manifest"),
        price_source_id="modeled", floors=relaxed(),
    )

    assert result.eligible
    assert result.control is not None
    assert result.control_relative_edge_pp is None
    assert result.by_year["2020"].control_relative_edge_pp is None


def test_absent_control_is_explicit_in_all_diagnostics() -> None:
    result = evaluate_ledger(
        GrossTradeLedger([row("2020-01-06", debit=.1, settlement=2, best=2, worst=0)]),
        stage=Stage.DISCOVERY, candidate_hash=digest("a"), stage_manifest_hash=digest("b"),
        price_source_id="modeled", floors=relaxed(),
    )

    assert result.control is None
    assert result.control_relative_edge_pp is None
    assert result.by_year["2020"].control is None
    assert result.by_year["2020"].control_relative_edge_pp is None


def test_selected_unpriced_row_must_match_declared_price_source() -> None:
    ledger = GrossTradeLedger([
        row("2020-01-06", debit=.1, settlement=2, best=2, worst=0),
        row("2020-01-07", debit=0, settlement=0, best=0, worst=0,
            priced=False, skip_reason="no quote", price_source="indicative"),
    ])

    result = evaluate_ledger(
        ledger, stage=Stage.DISCOVERY, candidate_hash=digest("candidate"),
        stage_manifest_hash=digest("manifest"), price_source_id="modeled", floors=relaxed(),
    )

    assert "selected signal rows do not match price_source_kind" in result.failures
    assert result.signal.skip_reason_counts == {"no quote": 1}


@pytest.mark.parametrize(("candidate_hash", "manifest_hash"), [
    ("not-a-hash", digest("manifest")),
    (digest("candidate"), "not-a-hash"),
])
def test_evaluator_rejects_malformed_provenance_hashes(
        candidate_hash: str, manifest_hash: str) -> None:
    ledger = GrossTradeLedger([
        row("2020-01-06", debit=.1, settlement=2, best=2, worst=0),
    ])

    with pytest.raises(ValueError, match="SHA-256"):
        evaluate_ledger(
            ledger, stage=Stage.DISCOVERY, candidate_hash=candidate_hash,
            stage_manifest_hash=manifest_hash, price_source_id="modeled", floors=relaxed(),
        )


def test_evaluator_rejects_incompatible_price_source_kind_and_id() -> None:
    ledger = GrossTradeLedger([
        row("2020-01-06", debit=.1, settlement=2, best=2, worst=0),
    ])

    with pytest.raises(ValueError, match="incompatible"):
        evaluate_ledger(
            ledger, stage=Stage.DISCOVERY, candidate_hash=digest("candidate"),
            stage_manifest_hash=digest("manifest"), price_source_id="indicative:vendor",
            price_source_kind=PriceSourceKind.MODELED, floors=relaxed(),
        )


def test_validation_uses_bonferroni_and_rejects_negative_year() -> None:
    rows = []
    for year, pnl in ((2022, 2), (2023, 0), (2024, 2)):
        # 0 settlement with a credit is a win. 2023 uses a debit and loses.
        rows.append(row(f"{year}-01-03", debit=-.2 if pnl else .2,
                        settlement=0, best=0 if pnl else 2, worst=-2 if pnl else 0))
    result = evaluate_ledger(GrossTradeLedger(rows), stage=Stage.VALIDATION,
                             candidate_hash=digest("a"), stage_manifest_hash=digest("b"),
                             price_source_id="modeled", shortlist_size=3, floors=relaxed())
    assert result.alpha == pytest.approx(.05 / 3)
    assert any("2023" in message and "negative" in message for message in result.failures)


def test_effective_week_floor_applies_even_with_many_trades() -> None:
    start = date(2020, 1, 6)
    rows = [row((start + timedelta(days=i)).isoformat(), debit=.1, settlement=2, best=2, worst=0)
            for i in range(5)]
    floors = relaxed()
    floors[Stage.DISCOVERY] = StageFloor(5, 2)
    result = evaluate_ledger(GrossTradeLedger(rows), stage=Stage.DISCOVERY,
                             candidate_hash=digest("a"), stage_manifest_hash=digest("b"),
                             price_source_id="modeled", floors=floors)
    assert not result.eligible
    assert any("effective weeks" in message for message in result.failures)
