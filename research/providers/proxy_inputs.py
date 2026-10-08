"""Read-only loaders for the gpu-leads-revisions proxy track's free inputs.

Three files, each produced elsewhere and handed in by path:

- ``GPU_INDEX_BACKFILL``: the monthly rental LIST-PRICE back-series built from
  archived public rate cards (a separate file in gpu-index);
- ``PROXY_XBRL_FACTS``: SEC XBRL facts stamped with their filing date; and
- ``PROXY_PRICES``: daily adjusted closes.

Each loader validates the declared schema and returns the rows with the
SHA-256 of the bytes it read, so an evaluation receipt can record exactly
which inputs it consumed. A missing file is a typed ``InputUnavailable``; a
schema mismatch is a typed ``InputSchemaError``. Nothing is ever substituted.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Generic, TypeVar

from research.prereg.proxy_campaign import AdjustedClose, FiledFact, MonthlyListPrice

T = TypeVar("T")

BACKFILL_ENV = "GPU_INDEX_BACKFILL"
FACTS_ENV = "PROXY_XBRL_FACTS"
PRICES_ENV = "PROXY_PRICES"

BACKFILL_COLUMNS = ("gpu", "month", "median_usd_per_gpu_hour", "rate_card_count", "price_type")
FACTS_COLUMNS = ("symbol", "concept", "period_start", "period_end", "value", "filed", "accession",
                 "xbrl_tag")
PRICES_COLUMNS = ("symbol", "day", "adj_close")


class InputUnavailable(RuntimeError):
    """The input file is not configured or cannot be read."""


class InputSchemaError(ValueError):
    """The input file was read but does not match the declared schema."""


@dataclass(frozen=True)
class Loaded(Generic[T]):
    source: str
    sha256: str
    rows: tuple[T, ...]


def _month(raw: str) -> date:
    text = raw.strip()
    return date.fromisoformat(text + "-01" if len(text) == 7 else text).replace(day=1)


def _backfill(row: dict[str, str]) -> MonthlyListPrice:
    return MonthlyListPrice(row["gpu"].strip(), _month(row["month"]),
                            float(row["median_usd_per_gpu_hour"]), int(row["rate_card_count"]),
                            row["price_type"].strip())


def _fact(row: dict[str, str]) -> FiledFact:
    concept = row["concept"].strip()
    if concept not in {"revenue", "capex"}:
        raise ValueError(f"concept must be revenue or capex, not {concept!r}")
    return FiledFact(row["symbol"].strip(), concept, date.fromisoformat(row["period_start"].strip()),
                     date.fromisoformat(row["period_end"].strip()), float(row["value"]),
                     date.fromisoformat(row["filed"].strip()), row["accession"].strip(),
                     row["xbrl_tag"].strip())


def _close(row: dict[str, str]) -> AdjustedClose:
    return AdjustedClose(row["symbol"].strip(), date.fromisoformat(row["day"].strip()),
                         float(row["adj_close"]))


def _load(source: str | os.PathLike[str] | None, env: str, columns: tuple[str, ...],
          parse: Callable[[dict[str, str]], T]) -> Loaded[T]:
    resolved = os.fspath(source) if source is not None else os.environ.get(env, "").strip()
    if not resolved:
        raise InputUnavailable(f"not configured: set {env} or pass a path")
    try:
        payload = Path(resolved).read_bytes()
    except OSError as exc:
        raise InputUnavailable(f"could not read {resolved}: {exc}") from exc
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    missing = [name for name in columns if name not in (reader.fieldnames or ())]
    if missing:
        raise InputSchemaError(f"{Path(resolved).name} is missing columns: {', '.join(missing)}")
    rows: list[T] = []
    for line, raw in enumerate(reader, start=2):
        try:
            rows.append(parse(raw))
        except (TypeError, ValueError, KeyError) as exc:
            raise InputSchemaError(f"{Path(resolved).name} line {line}: {exc}") from exc
    return Loaded(resolved, hashlib.sha256(payload).hexdigest(), tuple(rows))


def load_backfill(source: str | os.PathLike[str] | None = None) -> Loaded[MonthlyListPrice]:
    loaded = _load(source, BACKFILL_ENV, BACKFILL_COLUMNS, _backfill)
    wrong = {row.price_type for row in loaded.rows} - {"list_price"}
    if wrong:
        raise InputSchemaError(f"the back-series must be labelled list_price, found {sorted(wrong)}")
    keys = [(row.gpu, row.month) for row in loaded.rows]
    if len(set(keys)) != len(keys):
        raise InputSchemaError("the back-series has more than one row for a GPU and month")
    return loaded


def load_facts(source: str | os.PathLike[str] | None = None) -> Loaded[FiledFact]:
    return _load(source, FACTS_ENV, FACTS_COLUMNS, _fact)


def load_closes(source: str | os.PathLike[str] | None = None) -> Loaded[AdjustedClose]:
    return _load(source, PRICES_ENV, PRICES_COLUMNS, _close)
