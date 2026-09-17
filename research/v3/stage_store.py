"""Immutable, one-stage-at-a-time materialization for v3 research data."""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Literal

from research.backtest.data import Session

StageName = Literal["discovery", "validation", "holdout"]
STAGE_DATES: dict[StageName, tuple[str, str | None]] = {
    "discovery": ("2016-01-01", "2021-12-31"),
    "validation": ("2022-01-01", "2024-12-31"),
    "holdout": ("2025-01-01", None),
}

def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


@dataclass(frozen=True)
class StageManifest:
    schema_version: int
    stage: StageName
    start_date: str
    end_date: str | None
    source_hash: str
    rows: int
    sessions: int
    created_at: str
    content_hash: str

    @property
    def manifest_hash(self) -> str:
        return hashlib.sha256(_canonical(asdict(self))).hexdigest()


_MANIFEST_FIELDS = frozenset(StageManifest.__annotations__)

# Content hashes whose bytes already passed the full line-by-line validation
# walk. The sha256 check itself is never skipped, so a cache hit still proves
# byte-identity on every call; this only avoids re-walking millions of bars
# that are provably the bars that already validated. Bounded as a hygiene
# measure — a host holds a handful of staged stores, not thousands.
_VERIFIED_CONTENT: set[tuple[str, str, bool]] = set()
_VERIFIED_CONTENT_LIMIT = 64


def _session_record(session: Session) -> dict[str, object]:
    return {"day": session.day, "bars": [list(bar) for bar in session.bars]}


def materialize_stage_stores(root: Path, sessions: Iterable[Session],
                             *, source_hash: str) -> dict[StageName, StageManifest]:
    """Create all three immutable stores; refuse to replace existing evidence."""
    if (len(source_hash) != 64
            or any(c not in "0123456789abcdef" for c in source_hash.lower())):
        raise ValueError("source_hash must be a 64-character hexadecimal content hash")
    root.mkdir(parents=True, exist_ok=True)
    existing = [stage for stage in STAGE_DATES if (root / stage).exists()]
    if existing:
        raise FileExistsError(f"immutable stage stores already exist: {', '.join(existing)}")
    all_sessions = sorted(sessions, key=lambda item: item.day)
    if len({item.day for item in all_sessions}) != len(all_sessions):
        raise ValueError("session dates must be unique")
    for item in all_sessions:
        try:
            date.fromisoformat(item.day)
            _canonical(_session_record(item))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid canonical session: {item.day!r}") from exc
    manifests: dict[StageName, StageManifest] = {}
    for stage, (start, end) in STAGE_DATES.items():
        selected = [item for item in all_sessions if item.day >= start and (end is None or item.day <= end)]
        records = [_session_record(item) for item in selected]
        payload = b"".join(_canonical(record) + b"\n" for record in records)
        content_hash = hashlib.sha256(payload).hexdigest()
        stage_dir = root / stage
        stage_dir.mkdir()
        data_path = stage_dir / "sessions.jsonl"
        manifest_path = stage_dir / "manifest.json"
        data_path.write_bytes(payload)
        manifest = StageManifest(
            schema_version=1,
            stage=stage,
            start_date=start,
            end_date=end,
            source_hash=source_hash.lower(),
            rows=sum(len(item.bars) for item in selected),
            sessions=len(selected),
            created_at=datetime.now(timezone.utc).isoformat(),
            content_hash=content_hash,
        )
        manifest_path.write_bytes(_canonical(asdict(manifest)) + b"\n")
        os.chmod(data_path, 0o444)
        os.chmod(manifest_path, 0o444)
        manifests[stage] = manifest
    return manifests


class StageAccess:
    """A capability object that can resolve exactly one declared stage."""

    def __init__(self, root: Path, allowed_stage: StageName):
        if allowed_stage not in STAGE_DATES:
            raise ValueError("unknown stage")
        self._root = root
        self.stage = allowed_stage

    def manifest(self) -> StageManifest:
        manifest_path = self._root / self.stage / "manifest.json"
        payload = manifest_path.read_bytes()
        try:
            raw = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("stage manifest is not valid JSON") from exc
        if not isinstance(raw, dict) or set(raw) != _MANIFEST_FIELDS:
            raise ValueError("stage manifest has an invalid object shape")
        if payload != _canonical(raw) + b"\n":
            raise ValueError("stage manifest is not canonical JSON")
        if (type(raw["schema_version"]) is not int
                or type(raw["rows"]) is not int
                or type(raw["sessions"]) is not int
                or raw["rows"] < 0 or raw["sessions"] < 0
                or not isinstance(raw["stage"], str)
                or not isinstance(raw["start_date"], str)
                or raw["end_date"] is not None and not isinstance(raw["end_date"], str)
                or not isinstance(raw["source_hash"], str)
                or not isinstance(raw["created_at"], str)
                or not isinstance(raw["content_hash"], str)):
            raise ValueError("stage manifest contains invalid field types")
        manifest = StageManifest(**raw)
        expected_dates = STAGE_DATES[self.stage]
        if (manifest.schema_version != 1 or manifest.stage != self.stage
                or (manifest.start_date, manifest.end_date) != expected_dates):
            raise ValueError("stage manifest does not match access capability")
        for value in (manifest.source_hash, manifest.content_hash):
            if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError("stage manifest contains an invalid content hash")
        try:
            created_at = datetime.fromisoformat(manifest.created_at)
        except ValueError as exc:
            raise ValueError("stage manifest contains an invalid creation timestamp") from exc
        if created_at.tzinfo is None:
            raise ValueError("stage manifest creation timestamp must include a timezone")
        return manifest

    def verify_integrity(self, *, require_usable: bool = False) -> StageManifest:
        """Verify this store without exposing its sessions to campaign code.

        Startup and health checks should set ``require_usable=True`` for a
        deployment gate. Empty materialized stores remain valid fixtures when
        that deployment requirement is not requested.

        Every call re-reads the file and re-checks its sha256 against the
        manifest — tampering is always caught. Only the line-by-line shape
        walk is memoized, keyed by the verified content hash itself: bytes
        that already passed full validation cannot fail it on a re-read.
        (The health poll and session listing re-validated millions of bars
        per request and starved the web service on a busy research host,
        2026-08-15.)
        """
        manifest = self.manifest()
        data_path = self._root / self.stage / "sessions.jsonl"
        if not data_path.is_file():
            raise FileNotFoundError(f"stage data file does not exist: {data_path}")
        payload = data_path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != manifest.content_hash:
            raise ValueError("stage content hash mismatch")
        cache_key = (self.stage, manifest.content_hash, bool(require_usable))
        if cache_key in _VERIFIED_CONTENT:
            return manifest

        session_count = 0
        row_count = 0
        empty_session_count = 0
        previous_day: str | None = None
        start, end = STAGE_DATES[self.stage]
        for line_number, line in enumerate(payload.splitlines(keepends=True), start=1):
            if not line.endswith(b"\n") or line == b"\n":
                raise ValueError(f"stage session line {line_number} is not canonical JSONL")
            body = line[:-1]
            try:
                raw = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"stage session line {line_number} is not valid JSON") from exc
            if not isinstance(raw, dict) or set(raw) != {"day", "bars"}:
                raise ValueError(f"stage session line {line_number} has an invalid object shape")
            try:
                canonical = _canonical(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"stage session line {line_number} is not canonical JSON") from exc
            if body != canonical:
                raise ValueError(f"stage session line {line_number} is not canonical JSON")

            day = raw["day"]
            bars = raw["bars"]
            if not isinstance(day, str) or not isinstance(bars, list):
                raise ValueError(f"stage session line {line_number} has an invalid session shape")
            try:
                parsed_day = date.fromisoformat(day)
            except ValueError as exc:
                raise ValueError(f"stage session line {line_number} has an invalid date") from exc
            if parsed_day.isoformat() != day:
                raise ValueError(f"stage session line {line_number} has a non-canonical date")
            if day < start or (end is not None and day > end):
                raise ValueError("session outside the authorized stage")
            if previous_day is not None and day <= previous_day:
                raise ValueError("stage session dates must be unique and chronological")
            previous_day = day

            for bar in bars:
                if (not isinstance(bar, list) or len(bar) != 5
                        or type(bar[0]) is not int
                        or any(type(value) not in (int, float)
                               or type(value) is float and not math.isfinite(value)
                               for value in bar[1:])):
                    raise ValueError(f"stage session line {line_number} has an invalid bar shape")
            session_count += 1
            row_count += len(bars)
            if not bars:
                empty_session_count += 1

        if session_count != manifest.sessions or row_count != manifest.rows:
            raise ValueError("stage row/session counts do not match the manifest")
        if require_usable and (session_count == 0 or row_count == 0 or empty_session_count):
            raise ValueError("stage is not deployment-ready: usable sessions and rows are required")
        _VERIFIED_CONTENT.add(cache_key)
        if len(_VERIFIED_CONTENT) > _VERIFIED_CONTENT_LIMIT:
            _VERIFIED_CONTENT.pop()
        return manifest

    def open_sessions(self) -> tuple[Session, ...]:
        self.verify_integrity()
        payload = (self._root / self.stage / "sessions.jsonl").read_bytes()
        sessions = []
        for line in payload.splitlines():
            raw = json.loads(line)
            sessions.append(Session(raw["day"], tuple(tuple(bar) for bar in raw["bars"])))
        return tuple(sessions)

    def resolve(self, stage: StageName) -> Path:
        if stage != self.stage:
            raise PermissionError(f"this capability cannot resolve {stage}")
        return self._root / self.stage
