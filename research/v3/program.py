"""Research programs: the stage schedule, gates and price source a campaign runs under.

A program is frozen into a campaign through its research scope, so every
stage store, coverage report and evaluation a campaign accepts was made under
the same schedule. ``spy-price-v3`` is the original program and reproduces its
dates, floors and gates exactly; everything defaults to it.

``spy-quote-v1`` prices every leg from real SPY same-day option trades
(research/v3/quote_store.py), which exist only from 2024-02. Its stages are
therefore short: discovery is 2024-02..2024-12, validation is 2025-H1 (gated
per quarter), and the holdout opens 2025-07-01. That holdout overlaps the
``spy-price-v3`` holdout (2025+): running this program spends that period for
every SPY program, a decision taken deliberately when the program was added.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Mapping

STAGES = ("discovery", "validation", "holdout")


@dataclass(frozen=True)
class Floor:
    raw_trades: int
    effective_weeks: int
    per_period_trades: int = 0
    per_period_weeks: int = 0


@dataclass(frozen=True)
class Program:
    program_id: str
    # stage -> (ISO start, ISO end or None for an open holdout)
    stage_dates: Mapping[str, tuple[str, str | None]]
    # validation is gated per period: (label, first day, last day)
    validation_periods: tuple[tuple[str, str, str], ...]
    floors: Mapping[str, Floor]
    coverage_week_floors: Mapping[str, int]
    price_source: str             # "modeled" or "quote"
    evaluator_version: str
    # share of selected sessions that must be priced; below 1, unpriced sessions
    # (no leg trade before entry, known at entry) are excluded, not modeled
    min_price_coverage: float = 1.0

    def split_dates(self, stage: str) -> tuple[date, date | None]:
        start, end = self.stage_dates[stage]
        return date.fromisoformat(start), date.fromisoformat(end) if end else None

    def period_of(self, day: str) -> str | None:
        return next((label for label, start, end in self.validation_periods
                     if start <= day <= end), None)

    @property
    def scope(self) -> dict[str, object]:
        return {"program_id": self.program_id, "symbol": "SPY", "feature_families": []}


SPY_PRICE_V3 = Program(
    program_id="spy-price-v3",
    stage_dates=MappingProxyType({
        "discovery": ("2016-01-01", "2021-12-31"),
        "validation": ("2022-01-01", "2024-12-31"),
        "holdout": ("2025-01-01", None),
    }),
    validation_periods=(
        ("2022", "2022-01-01", "2022-12-31"),
        ("2023", "2023-01-01", "2023-12-31"),
        ("2024", "2024-01-01", "2024-12-31"),
    ),
    floors=MappingProxyType({
        "discovery": Floor(120, 40),
        "validation": Floor(60, 24, 15, 8),
        "holdout": Floor(30, 12),
    }),
    coverage_week_floors=MappingProxyType({"discovery": 80, "validation": 40, "holdout": 20}),
    price_source="modeled",
    evaluator_version="spy-options-staged-v3.2",
)

SPY_QUOTE_V1 = Program(
    program_id="spy-quote-v1",
    stage_dates=MappingProxyType({
        "discovery": ("2024-02-01", "2024-12-31"),
        "validation": ("2025-01-01", "2025-06-30"),
        "holdout": ("2025-07-01", None),
    }),
    validation_periods=(
        ("2025Q1", "2025-01-01", "2025-03-31"),
        ("2025Q2", "2025-04-01", "2025-06-30"),
    ),
    # ~230 discovery sessions (47 weeks) and ~124 validation sessions (26 weeks)
    floors=MappingProxyType({
        "discovery": Floor(80, 30),
        "validation": Floor(40, 16, 15, 6),
        "holdout": Floor(30, 12),
    }),
    coverage_week_floors=MappingProxyType({"discovery": 40, "validation": 22, "holdout": 20}),
    price_source="quote",
    evaluator_version="spy-quote-staged-v1",
    min_price_coverage=0.90,
)

PROGRAMS: Mapping[str, Program] = MappingProxyType({
    p.program_id: p for p in (SPY_PRICE_V3, SPY_QUOTE_V1)
})


def program_for_scope(scope: Mapping[str, object]) -> Program:
    program = PROGRAMS.get(str(scope.get("program_id")))
    if program is None or dict(scope) != program.scope:
        raise ValueError("research_scope must be the canonical scope of a registered program")
    return program
