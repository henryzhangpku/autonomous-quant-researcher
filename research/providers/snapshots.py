"""Deterministic immutable snapshot writer for research data captures."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Mapping

from .contracts import HistoricalSnapshot, utc_iso, utc_now


@dataclass(frozen=True, slots=True)
class SnapshotManifest:
    snapshot_name: str
    payload_path: Path
    manifest_path: Path
    payload_sha256: str
    created_at_utc: datetime

    def to_json(self) -> dict[str, Any]:
        return {
            "snapshot_name": self.snapshot_name,
            "payload_path": self.payload_path.name,
            "manifest_path": self.manifest_path.name,
            "payload_sha256": self.payload_sha256,
            "created_at_utc": utc_iso(self.created_at_utc),
        }


class SnapshotWriter:
    """Writes immutable canonical JSON snapshots below a caller-selected root."""

    def __init__(self, root: Path, *, clock=utc_now) -> None:
        self.root = root
        self.clock = clock

    def write(self, snapshot_name: str, snapshot: HistoricalSnapshot) -> SnapshotManifest:
        snapshot_dir = self._snapshot_dir(snapshot_name)
        snapshot_dir.mkdir(parents=True, exist_ok=False)

        payload_path = snapshot_dir / "payload.json"
        manifest_path = snapshot_dir / "manifest.json"
        payload_bytes = _canonical_bytes(snapshot.to_json())
        payload_sha = hashlib.sha256(payload_bytes).hexdigest()
        created_at = self.clock()
        manifest_seed = {
            "snapshot_name": snapshot_name,
            "payload_path": payload_path.name,
            "payload_sha256": payload_sha,
            "created_at_utc": utc_iso(created_at),
            "provenance": snapshot.provenance.to_json(),
        }
        manifest = SnapshotManifest(
            snapshot_name=snapshot_name,
            payload_path=payload_path,
            manifest_path=manifest_path,
            payload_sha256=payload_sha,
            created_at_utc=created_at,
        )

        _atomic_write(payload_path, payload_bytes)
        _atomic_write(manifest_path, _canonical_bytes(manifest_seed))
        return manifest

    def _snapshot_dir(self, snapshot_name: str) -> Path:
        if not snapshot_name or snapshot_name in {".", ".."}:
            raise ValueError("snapshot_name must be a relative directory name")
        path = Path(snapshot_name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("snapshot_name must stay below the configured root")
        return self.root / path


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _atomic_write(path: Path, data: bytes) -> None:
    if path.exists():
        raise FileExistsError(f"snapshot file already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("wb", delete=False, dir=path.parent, prefix=f".{path.name}.", suffix=".tmp") as temporary:
        temporary.write(data)
        temp_path = Path(temporary.name)
    try:
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
