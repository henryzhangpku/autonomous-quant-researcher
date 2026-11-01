"""Immutable, one-stage-at-a-time store of SPY same-day option prices.

Collected outside the research loop (research/experiments/collect_spy_0dte_quotes.py)
and read inside it only through a stage capability, exactly like the session
stores: a QuoteAccess for one stage cannot open another stage's prices.

One line per session:

    {"day": "2024-03-04", "C": {"512": [p570, p575, ..., p955]}, "P": {...}}

Each list holds, for every minute on the 5-minute decision grid 570..955, the
close of the latest 1-minute trade bar that *ended* at or before that minute
(a bar stamped t covers [t, t+1)), or null when there was none within
MAX_AGE_MINUTES. A price read at entry minute m is therefore observable at m:
no part of the bar it comes from lies after the decision. Strikes are whole
dollars in raw (unadjusted) SPY price space; the expiry is the session day.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from research.v3.program import Program

GRID = tuple(range(570, 960, 5))   # 09:30 .. 15:55 ET
MAX_AGE_MINUTES = 15
QUOTE_SCHEMA_VERSION = 1


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def grid_prices(bars: Iterable[tuple[int, float]]) -> list[float | None]:
    """[(bar start minute, close)] of one contract's 1-minute bars -> its price on
    every grid minute: the latest bar that ended at or before it, if fresh."""
    ordered = sorted(bars)
    out: list[float | None] = []
    i, last = 0, None
    for minute in GRID:
        while i < len(ordered) and ordered[i][0] + 1 <= minute:
            last = ordered[i]
            i += 1
        fresh = last is not None and minute - (last[0] + 1) <= MAX_AGE_MINUTES
        out.append(round(float(last[1]), 4) if fresh else None)
    return out


@dataclass(frozen=True)
class QuoteManifest:
    schema_version: int
    program_id: str
    stage: str
    start_date: str
    end_date: str | None
    source_hash: str
    sessions: int
    contracts: int
    created_at: str
    content_hash: str

    @property
    def manifest_hash(self) -> str:
        return hashlib.sha256(_canonical(asdict(self))).hexdigest()


def _validate_day(record: Mapping) -> None:
    if set(record) != {"day", "C", "P"}:
        raise ValueError("quote record must have exactly day, C and P")
    date.fromisoformat(record["day"])
    for right in ("C", "P"):
        for strike, prices in record[right].items():
            if not strike.isdigit() or len(prices) != len(GRID):
                raise ValueError(f"malformed {right} {strike} on {record['day']}")
            if any(p is not None and (not math.isfinite(p) or p < 0) for p in prices):
                raise ValueError(f"invalid price for {right} {strike} on {record['day']}")


def materialize_quote_stores(root: Path, days: Iterable[Mapping], *, source_hash: str,
                             program: Program) -> dict[str, QuoteManifest]:
    """Write all three stage stores once; refuse to replace existing evidence."""
    existing = [stage for stage in program.stage_dates if (root / stage).exists()]
    if existing:
        raise FileExistsError(f"immutable quote stores already exist: {', '.join(existing)}")
    records = sorted(days, key=lambda r: r["day"])
    if len({r["day"] for r in records}) != len(records):
        raise ValueError("quote days must be unique")
    for record in records:
        _validate_day(record)
    root.mkdir(parents=True, exist_ok=True)
    manifests = {}
    for stage, (start, end) in program.stage_dates.items():
        selected = [r for r in records if r["day"] >= start and (end is None or r["day"] <= end)]
        payload = b"".join(_canonical(r) + b"\n" for r in selected)
        stage_dir = root / stage
        stage_dir.mkdir()
        manifest = QuoteManifest(
            schema_version=QUOTE_SCHEMA_VERSION, program_id=program.program_id, stage=stage,
            start_date=start, end_date=end, source_hash=source_hash.lower(),
            sessions=len(selected), contracts=sum(len(r["C"]) + len(r["P"]) for r in selected),
            created_at=datetime.now(timezone.utc).isoformat(),
            content_hash=hashlib.sha256(payload).hexdigest(),
        )
        (stage_dir / "quotes.jsonl").write_bytes(payload)
        (stage_dir / "manifest.json").write_bytes(_canonical(asdict(manifest)) + b"\n")
        os.chmod(stage_dir / "quotes.jsonl", 0o444)
        os.chmod(stage_dir / "manifest.json", 0o444)
        manifests[stage] = manifest
    return manifests


class DayQuotes:
    """One session's prices: price(cp, strike, minute) or None."""

    def __init__(self, record: Mapping):
        self._books = {right: record[right] for right in ("C", "P")}

    def price(self, cp: str, strike: float, minute: int) -> float | None:
        if strike != int(strike) or minute not in _GRID_INDEX:
            return None
        prices = self._books[cp].get(str(int(strike)))
        return None if prices is None else prices[_GRID_INDEX[minute]]


_GRID_INDEX = {minute: i for i, minute in enumerate(GRID)}


class QuoteAccess:
    """A capability that can read exactly one stage's quotes."""

    def __init__(self, root: Path, stage: str, program: Program):
        if stage not in program.stage_dates:
            raise ValueError("unknown stage")
        self._root, self.stage, self.program = root, stage, program

    def manifest(self) -> QuoteManifest:
        raw = json.loads((self._root / self.stage / "manifest.json").read_bytes())
        manifest = QuoteManifest(**raw)
        if (manifest.stage != self.stage or manifest.program_id != self.program.program_id
                or (manifest.start_date, manifest.end_date)
                != tuple(self.program.stage_dates[self.stage])):
            raise ValueError("quote manifest does not match access capability")
        return manifest

    def open_days(self) -> dict[str, DayQuotes]:
        manifest = self.manifest()
        payload = (self._root / self.stage / "quotes.jsonl").read_bytes()
        if hashlib.sha256(payload).hexdigest() != manifest.content_hash:
            raise ValueError("quote content hash mismatch")
        out = {}
        for line in payload.splitlines():
            record = json.loads(line)
            out[record["day"]] = DayQuotes(record)
        return out
