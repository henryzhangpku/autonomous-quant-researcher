"""Read-only adapter for the public GPU rental price index tape.

The index (github.com/henryzhangpku/gpu-price-index) publishes one append-only
CSV, ``series/index_values.csv``: one row per revision, with ``published_at``
and ``superseded_at`` so any past day can be read exactly as it was known at
the time. This adapter only reads that file and hashes what it read. The
signal arithmetic lives in ``research.prereg.gpu_signal``.

Source resolution, first match wins:

1. an explicit ``source`` argument (a filesystem path or an https URL);
2. the ``GPU_INDEX_TAPE`` environment variable (same forms);
3. the sibling checkout ``../gpu-index/series/index_values.csv``.

``PUBLISHED_TAPE_URL`` is the raw GitHub URL of the same file on the index's
``main`` branch. The index's own documentation links its board and methodology
but not this raw URL; it is the standard raw-content address of a public file.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from research.prereg.gpu_signal import Fixing

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TAPE_PATH = ROOT.parent / "gpu-index" / "series" / "index_values.csv"
PUBLISHED_TAPE_URL = (
    "https://raw.githubusercontent.com/henryzhangpku/gpu-price-index/main/series/index_values.csv"
)
ENV_VAR = "GPU_INDEX_TAPE"
REQUIRED_COLUMNS = (
    "index_code", "index_date", "revision", "status", "value",
    "methodology_version", "published_at", "superseded_at",
)


class TapeUnavailable(RuntimeError):
    """The tape could not be read from the resolved source."""


class TapeFormatError(ValueError):
    """The tape was read but does not match the published schema."""


@dataclass(frozen=True)
class Tape:
    source: str
    sha256: str
    read_at: datetime
    fixings: tuple[Fixing, ...]


def resolve_source(source: str | os.PathLike[str] | None = None) -> str:
    if source is not None:
        return os.fspath(source)
    from_env = os.environ.get(ENV_VAR, "").strip()
    if from_env:
        return from_env
    return str(DEFAULT_TAPE_PATH)


def _timestamp(raw: str, field: str) -> datetime:
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TapeFormatError(f"{field} is not ISO-8601: {raw!r}") from exc
    if value.tzinfo is None:
        raise TapeFormatError(f"{field} must carry a UTC offset: {raw!r}")
    return value.astimezone(timezone.utc)


def parse_tape(text: str) -> tuple[Fixing, ...]:
    reader = csv.DictReader(io.StringIO(text))
    missing = [name for name in REQUIRED_COLUMNS if name not in (reader.fieldnames or ())]
    if missing:
        raise TapeFormatError(f"tape is missing columns: {', '.join(missing)}")
    rows: list[Fixing] = []
    for line, raw in enumerate(reader, start=2):
        try:
            status = raw["status"].strip()
            value = float(raw["value"]) if raw["value"].strip() else None
            if status == "published" and value is None:
                raise TapeFormatError(f"line {line}: published row has no value")
            rows.append(Fixing(
                index_code=raw["index_code"].strip(),
                index_date=date.fromisoformat(raw["index_date"].strip()),
                revision=int(raw["revision"]),
                status=status,
                value=value,
                methodology_version=raw["methodology_version"].strip(),
                published_at=_timestamp(raw["published_at"].strip(), "published_at"),
                superseded_at=(_timestamp(raw["superseded_at"].strip(), "superseded_at")
                               if raw["superseded_at"].strip() else None),
            ))
        except TapeFormatError:
            raise
        except (TypeError, ValueError) as exc:
            raise TapeFormatError(f"line {line}: {exc}") from exc
    return tuple(rows)


def _read_bytes(source: str, timeout: float) -> bytes:
    if source.startswith(("http://", "https://")):
        if not source.startswith("https://"):
            raise TapeUnavailable("only https URLs are accepted for the tape")
        request = urllib.request.Request(source, headers={"User-Agent": "autonomous-quant-researcher"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - https only
                return response.read()
        except OSError as exc:
            raise TapeUnavailable(f"could not fetch {source}: {exc}") from exc
    path = Path(source)
    try:
        return path.read_bytes()
    except OSError as exc:
        raise TapeUnavailable(f"could not read {path}: {exc}") from exc


def load_tape(source: str | os.PathLike[str] | None = None, *, timeout: float = 20.0) -> Tape:
    resolved = resolve_source(source)
    payload = _read_bytes(resolved, timeout)
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TapeFormatError("tape is not UTF-8") from exc
    return Tape(
        source=resolved,
        sha256=hashlib.sha256(payload).hexdigest(),
        read_at=datetime.now(timezone.utc),
        fixings=parse_tape(text),
    )
