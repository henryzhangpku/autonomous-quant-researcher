from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from research.providers.public_csv import EXAMPLE_CSV, PublicCsvError, PublicCsvProvider, load_bars


def test_shipped_public_fixture_loads_in_chronological_order() -> None:
    bars = load_bars()
    assert len(bars) == 5
    assert [bar.timestamp_utc.date() for bar in bars] == [
        date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5), date(2024, 1, 8)
    ]
    assert bars[0].timestamp_utc == datetime(2024, 1, 2, tzinfo=timezone.utc)
    assert bars[0].close == 100.90 and bars[-1].volume == 1500000


def test_free_csv_provider_slices_a_date_range() -> None:
    provider = PublicCsvProvider(EXAMPLE_CSV)
    bars = provider.fetch(start=date(2024, 1, 3), end=date(2024, 1, 4))
    assert [bar.timestamp_utc.date() for bar in bars] == [date(2024, 1, 3), date(2024, 1, 4)]


def test_missing_columns_are_a_typed_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("date,open,high,low,close\n2024-01-02,1,2,0.5,1.5\n", encoding="utf-8")
    with pytest.raises(PublicCsvError, match="missing columns: volume"):
        load_bars(path)


def test_non_positive_prices_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("date,open,high,low,close,volume\n2024-01-02,1,2,0,1.5,10\n", encoding="utf-8")
    with pytest.raises(PublicCsvError, match="prices must be positive"):
        load_bars(path)
