"""Pure v3 cost scenarios, uncertainty estimates, and fail-closed gates."""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Iterable

from research.v3.ledger import GrossTradeLedger, GrossTradeRow

EVALUATOR_VERSION = "spy-options-staged-v3.2"
BASE_COST_FRACTION = 0.03
ELEVATED_COST_FRACTION = 0.045
BOOTSTRAP_RESAMPLES = 2_000


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str) and len(value) == 64
        and all(char in "0123456789abcdef" for char in value.lower())
    )


class Stage(StrEnum):
    DISCOVERY = "discovery"
    VALIDATION = "validation"
    HOLDOUT = "holdout"


class PriceSourceKind(StrEnum):
    MODELED = "modeled"
    INDICATIVE = "indicative"
    OPRA = "opra"


def _price_source_id_matches_kind(price_source_id: str, kind: PriceSourceKind) -> bool:
    return price_source_id == kind.value or price_source_id.startswith(f"{kind.value}:")


@dataclass(frozen=True)
class StageFloor:
    raw_trades: int
    effective_weeks: int
    per_year_trades: int = 0
    per_year_weeks: int = 0


STAGE_FLOORS = {
    Stage.DISCOVERY: StageFloor(120, 40),
    Stage.VALIDATION: StageFloor(60, 24, 15, 8),
    Stage.HOLDOUT: StageFloor(30, 12),
}


@dataclass(frozen=True)
class ScenarioMetrics:
    mean_pnl_pct: float
    lower_bound_pct: float
    cost_fraction: float


@dataclass(frozen=True)
class LedgerCounts:
    """Deterministic selection and pricing audit derived from a gross ledger."""

    selected_count: int
    priced_count: int
    coverage: float
    skip_reason_counts: dict[str, int]


@dataclass(frozen=True)
class YearMetrics:
    trades: int
    effective_weeks: int
    absolute_full_win_edge_pp: float
    control_relative_edge_pp: float | None
    price_source_kind: PriceSourceKind
    signal: LedgerCounts
    control: LedgerCounts | None
    base: ScenarioMetrics
    elevated: ScenarioMetrics


@dataclass(frozen=True)
class Evaluation:
    eligible: bool
    failures: tuple[str, ...]
    stage: Stage
    trades: int
    effective_weeks: int
    alpha: float
    bootstrap_seed: int
    price_source_id: str
    price_source_kind: PriceSourceKind
    evaluator_version: str
    stage_manifest_hash: str
    signal: LedgerCounts
    control: LedgerCounts | None
    absolute_full_win_edge_pp: float
    control_relative_edge_pp: float | None
    base: ScenarioMetrics
    elevated: ScenarioMetrics
    by_year: dict[str, YearMetrics]
    ranking_score_pp: float


def bootstrap_seed(candidate_hash: str, stage_manifest_hash: str, price_source_id: str,
                   cost_scenario_id: str, *, evaluator_version: str = EVALUATOR_VERSION) -> int:
    material = "\x1f".join((candidate_hash, evaluator_version, stage_manifest_hash,
                            price_source_id, cost_scenario_id))
    return int.from_bytes(hashlib.sha256(material.encode()).digest()[:8], "big")


def _iso_week(day: str) -> tuple[int, int]:
    parsed = date.fromisoformat(day)
    iso = parsed.isocalendar()
    return (iso.year, iso.week)


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def weekly_cluster_lower_bound(outcomes: Iterable[tuple[str, float]], *, alpha: float,
                               seed: int, resamples: int = BOOTSTRAP_RESAMPLES) -> float:
    clusters: dict[tuple[int, int], list[float]] = {}
    for day, value in outcomes:
        if not math.isfinite(value):
            raise ValueError("bootstrap outcomes must be finite")
        clusters.setdefault(_iso_week(day), []).append(value)
    ordered = [clusters[key] for key in sorted(clusters)]
    if not ordered or not 0 < alpha < 1 or resamples <= 0:
        raise ValueError("bootstrap requires clusters, a valid alpha, and resamples")
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(resamples):
        sampled = [ordered[rng.randrange(len(ordered))] for _ in ordered]
        flat = [value for cluster in sampled for value in cluster]
        means.append(sum(flat) / len(flat))
    return _quantile(means, alpha)


def _selected_priced(ledger: GrossTradeLedger) -> list[GrossTradeRow]:
    return [row for row in ledger.rows if row.selected and row.priced]


def _ledger_counts(rows: Iterable[GrossTradeRow]) -> LedgerCounts:
    values = tuple(rows)
    selected = tuple(row for row in values if row.selected)
    priced = sum(row.priced for row in selected)
    reasons: dict[str, int] = {}
    for row in selected:
        if not row.priced:
            assert row.skip_reason is not None
            reasons[row.skip_reason] = reasons.get(row.skip_reason, 0) + 1
    selected_count = len(selected)
    return LedgerCounts(
        selected_count=selected_count,
        priced_count=priced,
        coverage=priced / selected_count if selected_count else 0.0,
        skip_reason_counts=dict(sorted(reasons.items())),
    )


def _costed_outcomes(rows: list[GrossTradeRow], cost_fraction: float) -> list[tuple[str, float]]:
    outcomes = []
    for row in rows:
        assert row.gross_pnl is not None and row.width is not None
        outcomes.append((row.date, (row.gross_pnl - cost_fraction * row.width) / row.width))
    return outcomes


def _edge(rows: list[GrossTradeRow], cost_fraction: float) -> float:
    margins = []
    for row in rows:
        assert row.width is not None and row.signed_entry_debit is not None
        assert row.best_settlement is not None and row.worst_settlement is not None
        assert row.gross_settlement is not None
        payoff_range = row.best_settlement - row.worst_settlement
        gross_q = ((row.signed_entry_debit - row.worst_settlement) / payoff_range)
        if (not math.isfinite(gross_q)
                or gross_q < -1e-9 or gross_q > 1 + 1e-9):
            raise ValueError("gross break-even full-win probability is outside [0, 1]")
        gross_q = min(1.0, max(0.0, gross_q))
        # Costs can legitimately push the required win rate above 100%. That is
        # a finite, strongly negative result (the trade cannot cover costs even
        # at maximum settlement), not malformed gross pricing evidence.
        q = gross_q + (cost_fraction * row.width / payoff_range)
        if not math.isfinite(q):
            raise ValueError("costed break-even full-win requirement is not finite")
        full_win = abs(row.gross_settlement - row.best_settlement) <= 1e-9
        margins.append(float(full_win) - q)
    if not margins:
        raise ValueError("edge requires priced selected rows")
    return 100.0 * sum(margins) / len(margins)


def _scenario(rows: list[GrossTradeRow], cost_fraction: float, *, alpha: float,
              seed: int) -> ScenarioMetrics:
    outcomes = _costed_outcomes(rows, cost_fraction)
    mean = sum(value for _, value in outcomes) / len(outcomes) * 100.0
    lower = weekly_cluster_lower_bound(outcomes, alpha=alpha, seed=seed) * 100.0
    return ScenarioMetrics(mean, lower, cost_fraction)


def evaluate_ledger(
    ledger: GrossTradeLedger,
    *,
    stage: Stage,
    candidate_hash: str,
    stage_manifest_hash: str,
    price_source_id: str,
    price_source_kind: PriceSourceKind | str | None = None,
    shortlist_size: int = 1,
    control_ledger: GrossTradeLedger | None = None,
    floors: dict[Stage, StageFloor] | None = None,
) -> Evaluation:
    """Evaluate once from the canonical gross ledger; provider callbacks are impossible."""
    failures: list[str] = []
    stage = Stage(stage)
    if not _is_sha256(candidate_hash):
        raise ValueError("candidate_hash must be a SHA-256 hash")
    if not isinstance(price_source_id, str) or not price_source_id.strip():
        raise ValueError("price_source_id is required")
    try:
        source_kind = PriceSourceKind(price_source_kind or price_source_id)
    except ValueError as exc:
        raise ValueError("price_source_kind must be modeled, indicative, or opra") from exc
    if not _price_source_id_matches_kind(price_source_id, source_kind):
        raise ValueError("price_source_id is incompatible with price_source_kind")
    if not _is_sha256(stage_manifest_hash):
        raise ValueError("stage_manifest_hash must be a SHA-256 hash")
    floor = (floors or STAGE_FLOORS)[stage]
    alpha = 0.05 / shortlist_size if stage == Stage.VALIDATION else 0.05
    if shortlist_size < 1 or (stage == Stage.VALIDATION and shortlist_size > 3):
        raise ValueError("shortlist_size must be between one and three")
    rows = _selected_priced(ledger)
    signal_counts = _ledger_counts(ledger.rows)
    control_counts = _ledger_counts(control_ledger.rows) if control_ledger is not None else None
    if any(row.price_source != source_kind.value for row in ledger.rows if row.selected):
        failures.append("selected signal rows do not match price_source_kind")
    if ledger.selected_count != len(rows):
        failures.append("selected rows are missing required prices")
    weeks = len({_iso_week(row.date) for row in rows})
    if len(rows) < floor.raw_trades:
        failures.append(f"requires at least {floor.raw_trades} priced trades")
    if weeks < floor.effective_weeks:
        failures.append(f"requires at least {floor.effective_weeks} effective weeks")

    seed = bootstrap_seed(candidate_hash, stage_manifest_hash, price_source_id, "base")
    nan_scenario = ScenarioMetrics(float("nan"), float("nan"), BASE_COST_FRACTION)
    absolute_edge = float("nan")
    base = nan_scenario
    elevated = ScenarioMetrics(float("nan"), float("nan"), ELEVATED_COST_FRACTION)
    control_relative: float | None = None
    by_year: dict[str, YearMetrics] = {}
    try:
        absolute_edge = _edge(rows, BASE_COST_FRACTION)
        base = _scenario(rows, BASE_COST_FRACTION, alpha=alpha, seed=seed)
        elevated_seed = bootstrap_seed(candidate_hash, stage_manifest_hash,
                                       price_source_id, "elevated")
        elevated = _scenario(rows, ELEVATED_COST_FRACTION, alpha=alpha, seed=elevated_seed)
        grouped: dict[str, list[GrossTradeRow]] = {}
        for row in rows:
            grouped.setdefault(row.date[:4], []).append(row)
        for year, year_rows in sorted(grouped.items()):
            year_base_seed = bootstrap_seed(
                candidate_hash, stage_manifest_hash, price_source_id, f"base:{year}"
            )
            year_elevated_seed = bootstrap_seed(
                candidate_hash, stage_manifest_hash, price_source_id, f"elevated:{year}"
            )
            signal_year_counts = _ledger_counts(
                row for row in ledger.rows if row.date[:4] == year
            )
            control_year_rows = (
                [row for row in control_ledger.rows if row.date[:4] == year]
                if control_ledger is not None else None
            )
            control_year_counts = (
                _ledger_counts(control_year_rows) if control_year_rows is not None else None
            )
            year_edge = _edge(year_rows, BASE_COST_FRACTION)
            year_control_relative: float | None = None
            if control_year_rows is not None:
                try:
                    priced_control_year_rows = [
                        row for row in control_year_rows if row.selected and row.priced
                    ]
                    if any(
                        row.price_source != source_kind.value
                        for row in control_year_rows if row.selected
                    ):
                        raise ValueError("control price source mismatch")
                    year_control_relative = year_edge - _edge(
                        priced_control_year_rows,
                        BASE_COST_FRACTION,
                    )
                except (ArithmeticError, ValueError, OverflowError):
                    year_control_relative = None
            by_year[year] = YearMetrics(
                trades=len(year_rows),
                effective_weeks=len({_iso_week(row.date) for row in year_rows}),
                absolute_full_win_edge_pp=year_edge,
                control_relative_edge_pp=year_control_relative,
                price_source_kind=source_kind,
                signal=signal_year_counts,
                control=control_year_counts,
                base=_scenario(year_rows, BASE_COST_FRACTION, alpha=alpha, seed=year_base_seed),
                elevated=_scenario(
                    year_rows, ELEVATED_COST_FRACTION, alpha=alpha, seed=year_elevated_seed
                ),
            )
    except (ArithmeticError, ValueError, OverflowError) as exc:
        failures.append(f"invalid ledger economics: {exc}")

    # The control is deliberately diagnostic. Bad or absent control evidence
    # cannot rescue a signal and also cannot veto valid absolute evidence.
    if control_ledger is not None and math.isfinite(absolute_edge):
        try:
            control_rows = _selected_priced(control_ledger)
            if any(
                row.price_source != source_kind.value
                for row in control_ledger.rows if row.selected
            ):
                raise ValueError("control price source mismatch")
            control_relative = absolute_edge - _edge(
                control_rows, BASE_COST_FRACTION
            )
        except (ArithmeticError, ValueError, OverflowError):
            control_relative = None

    metrics = (absolute_edge, base.mean_pnl_pct, base.lower_bound_pct,
               elevated.mean_pnl_pct)
    if not all(math.isfinite(value) for value in metrics):
        failures.append("all metrics must be finite")
    else:
        if absolute_edge <= 0:
            failures.append("absolute full-win edge is not positive")
        if base.mean_pnl_pct <= 0:
            failures.append("base-cost net mean P&L is not positive")
        if base.lower_bound_pct <= 0:
            failures.append("uncertainty-adjusted P&L lower bound is not positive")
        if elevated.mean_pnl_pct <= 0:
            failures.append("elevated-cost net mean P&L is not positive")

    if stage == Stage.VALIDATION:
        expected_years = {"2022", "2023", "2024"}
        if set(by_year) != expected_years:
            failures.append("validation must contain evidence for 2022, 2023, and 2024")
        for year in sorted(expected_years):
            item = by_year.get(year)
            if item is None:
                continue
            if item.trades < floor.per_year_trades or item.effective_weeks < floor.per_year_weeks:
                failures.append(f"{year} does not meet validation sample floors")
            if item.base.mean_pnl_pct < 0:
                failures.append(f"{year} base-cost net mean P&L is negative")

    worst_year = min((item.base.mean_pnl_pct for item in by_year.values()), default=float("nan"))
    ranking_components = [absolute_edge, base.lower_bound_pct, elevated.mean_pnl_pct]
    if math.isfinite(worst_year):
        ranking_components.append(worst_year)
    ranking = min(ranking_components) if all(math.isfinite(v) for v in ranking_components) else float("nan")
    return Evaluation(
        eligible=not failures,
        failures=tuple(dict.fromkeys(failures)),
        stage=stage,
        trades=len(rows),
        effective_weeks=weeks,
        alpha=alpha,
        bootstrap_seed=seed,
        price_source_id=price_source_id,
        price_source_kind=source_kind,
        evaluator_version=EVALUATOR_VERSION,
        stage_manifest_hash=stage_manifest_hash,
        signal=signal_counts,
        control=control_counts,
        absolute_full_win_edge_pp=absolute_edge,
        control_relative_edge_pp=control_relative,
        base=base,
        elevated=elevated,
        by_year=by_year,
        ranking_score_pp=ranking,
    )
