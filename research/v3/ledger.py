"""Canonical gross trade ledger retained before any cost scenario is applied."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Iterable


@dataclass(frozen=True)
class GrossTradeRow:
    date: str
    selected: bool
    priced: bool
    structure: str
    width: float | None
    signed_entry_debit: float | None
    gross_settlement: float | None
    best_settlement: float | None
    worst_settlement: float | None
    gross_pnl: float | None
    price_source: str
    entry_legs: tuple[tuple[str, float, int, float], ...] = ()
    skip_reason: str | None = None

    def validate(self) -> None:
        try:
            date.fromisoformat(self.date)
        except (TypeError, ValueError) as exc:
            raise ValueError("ledger date must be ISO-8601") from exc
        if not self.date or not self.structure or not self.price_source:
            raise ValueError("ledger identity fields are required")
        if not isinstance(self.selected, bool) or not isinstance(self.priced, bool):
            raise ValueError("selection and pricing flags must be Boolean")
        if self.price_source not in {"modeled", "indicative", "opra"}:
            raise ValueError("unsupported price provenance")
        values = (self.width, self.signed_entry_debit, self.gross_settlement,
                  self.best_settlement, self.worst_settlement, self.gross_pnl)
        if self.priced:
            if any(value is None or not math.isfinite(value) for value in values):
                raise ValueError("priced rows require finite economics")
            assert self.width is not None and self.best_settlement is not None
            assert self.worst_settlement is not None and self.signed_entry_debit is not None
            assert self.gross_settlement is not None and self.gross_pnl is not None
            if self.width <= 0 or self.best_settlement <= self.worst_settlement:
                raise ValueError("priced rows require positive width and payoff range")
            if not self.worst_settlement - 1e-9 <= self.gross_settlement <= self.best_settlement + 1e-9:
                raise ValueError("settlement lies outside the structure payoff range")
            if abs(self.gross_pnl - (self.gross_settlement - self.signed_entry_debit)) > 1e-9:
                raise ValueError("gross_pnl must equal settlement minus signed debit")
            if self.skip_reason is not None:
                raise ValueError("priced rows cannot carry a skip reason")
            for leg in self.entry_legs:
                if (len(leg) != 4 or leg[0] not in {"C", "P"} or leg[2] not in {-1, 1}
                        or not all(math.isfinite(float(value)) for value in (leg[1], leg[3]))):
                    raise ValueError("entry leg observations are malformed")
        elif self.selected and not self.skip_reason:
            raise ValueError("selected unpriced rows require an explicit skip reason")
        elif any(value is not None for value in values) or self.entry_legs:
            raise ValueError("unpriced rows cannot contain economic observations")


class GrossTradeLedger:
    def __init__(self, rows: Iterable[GrossTradeRow]):
        self.rows = tuple(rows)
        if tuple(sorted(self.rows, key=lambda row: row.date)) != self.rows:
            raise ValueError("ledger rows must be chronological")
        if len({row.date for row in self.rows}) != len(self.rows):
            raise ValueError("ledger dates must be unique")
        for row in self.rows:
            row.validate()

    @property
    def selected_count(self) -> int:
        return sum(row.selected for row in self.rows)

    @property
    def priced_selected_count(self) -> int:
        return sum(row.selected and row.priced for row in self.rows)

    @property
    def coverage(self) -> float:
        return self.priced_selected_count / self.selected_count if self.selected_count else 0.0

    @property
    def content_hash(self) -> str:
        value: list[dict[str, Any]] = [asdict(row) for row in self.rows]
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(payload.encode()).hexdigest()
