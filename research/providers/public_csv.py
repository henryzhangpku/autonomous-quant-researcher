"""Free/public CSV bars: a runnable example of the provider boundary.

Licensed products live behind :mod:`research.providers.licensed` and refuse
until a deployment injects an adapter. This module is the opposite example:
a small, dependency-free loader for a CSV that anyone can produce from a free
source (for example a yfinance download or an exchange's public daily file).

The schema is deliberately boring: ``date,open,high,low,close,volume``. A
date is treated as a UTC session timestamp; a full ISO timestamp is accepted
as-is. The repository ships one tiny synthetic fixture in this schema so the
example and its test run with no network and no key.

This is an example, not a licensed-data adapter: it does not claim to be
survivorship-bias-free or point-in-time by itself.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from .contracts import Bar

CSV_COLUMNS = ("date", "open", "high", "low", "close", "volume")
EXAMPLE_CSV = Path(__file__).resolve().parents[1] / "data" / "public" / "example_daily.csv"


class PublicCsvError(ValueError):
    """The CSV does not match the public bar schema."""


def _timestamp(value: str) -> datetime:
    text = value.strip()
    try:
        if len(text) == 10:
            parsed = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PublicCsvError(f"bad date/timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class PublicCsvProvider:
    """Read one public CSV as a chronological, validated tuple of bars."""

    path: Path = EXAMPLE_CSV
    name: str = "public_csv"

    def fetch(self, *, start: date | None = None, end: date | None = None) -> tuple[Bar, ...]:
        source = Path(self.path)
        try:
            handle = source.open(newline="", encoding="utf-8")
        except OSError as exc:
            raise PublicCsvError(f"could not read {source}: {exc}") from exc
        with handle:
            reader = csv.DictReader(handle)
            missing = [name for name in CSV_COLUMNS if name not in (reader.fieldnames or ())]
            if missing:
                raise PublicCsvError(f"{source} is missing columns: {', '.join(missing)}")
            bars: list[Bar] = []
            seen: set[datetime] = set()
            for number, row in enumerate(reader, 2):
                try:
                    when = _timestamp(row["date"])
                    bar = Bar(
                        timestamp_utc=when,
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]) if (row.get("volume") or "").strip() else None,
                    )
                except (TypeError, ValueError) as exc:
                    raise PublicCsvError(f"{source} line {number}: {exc}") from exc
                if min(bar.open, bar.high, bar.low, bar.close) <= 0:
                    raise PublicCsvError(f"{source} line {number}: prices must be positive")
                if when in seen:
                    raise PublicCsvError(f"{source} line {number}: duplicate timestamp {when.isoformat()}")
                seen.add(when)
                if start is not None and when.date() < start:
                    continue
                if end is not None and when.date() > end:
                    continue
                bars.append(bar)
        bars.sort(key=lambda item: item.timestamp_utc)
        return tuple(bars)


def load_bars(path: str | Path = EXAMPLE_CSV, *, start: date | None = None,
              end: date | None = None) -> tuple[Bar, ...]:
    """Convenience wrapper around :class:`PublicCsvProvider`."""
    return PublicCsvProvider(Path(path)).fetch(start=start, end=end)
