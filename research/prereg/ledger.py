"""Hash-chained pre-registration ledger.

A pre-registration is frozen by appending one ``preregistration_frozen`` event
to an append-only JSONL ledger. The event uses the campaign coordinator's own
event format and primitives (``research.v3.campaign``): canonical JSON,
SHA-256, ``sequence`` / ``previous_event_hash`` / ``event_hash`` chaining, and
the same interprocess lock. Its payload binds, by content hash:

- the machine-readable specification (``preregistration.json``, hashed as
  canonical JSON so formatting cannot move it);
- the human-readable document (``PREREGISTRATION.md``); and
- the trusted source files that will compute the verdict.

Text files are hashed with line endings normalized to LF, so a Windows
checkout with ``core.autocrlf`` reproduces the same hashes as a Linux one.

The hash proves *what* was frozen. *When* is proved by the event timestamp
and, independently, by the commit that added the ledger line. Editing any
bound file, or any byte of the ledger, fails :func:`verify`.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.v3.campaign import _canonical, _interprocess_lock, _is_hash, _jsonable, _sha256

ROOT = Path(__file__).resolve().parents[2]
LEDGER_NAME = "ledger.jsonl"
SPEC_NAME = "preregistration.json"
DOCUMENT_NAME = "PREREGISTRATION.md"
FREEZE_EVENT = "preregistration_frozen"
OPENED_EVENT = "evaluation_opened"
VERDICT_EVENT = "verdict_recorded"
TRACK_EVENT = "track_frozen"


class PreregistrationError(RuntimeError):
    """The ledger or a bound file does not match what was frozen."""


class AlreadyFrozen(PreregistrationError):
    """A pre-registration is frozen once; a change is a new mission."""


class AlreadyOpened(PreregistrationError):
    """A campaign is judged once; its evaluation receipt cannot be reissued."""


@dataclass(frozen=True)
class FrozenPreregistration:
    mission: str
    event_hash: str
    created_at: str
    spec_sha256: str
    document_sha256: str
    code_sha256: Mapping[str, str]


def text_sha256(path: Path) -> str:
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def spec_sha256(path: Path) -> str:
    return _sha256(json.loads(path.read_text(encoding="utf-8")))


def freeze_payload(mission_dir: Path, code_paths: Sequence[str], *, spec_name: str = SPEC_NAME,
                   document_name: str = DOCUMENT_NAME) -> dict[str, Any]:
    spec = json.loads((mission_dir / spec_name).read_text(encoding="utf-8"))
    return {
        "mission": spec["mission"],
        "spec_sha256": spec_sha256(mission_dir / spec_name),
        "document_sha256": text_sha256(mission_dir / document_name),
        "code_sha256": {name: text_sha256(ROOT / name) for name in sorted(code_paths)},
    }


def read_events(ledger_path: Path) -> list[dict[str, Any]]:
    """Replay and verify the chain. Any break raises PreregistrationError."""
    events: list[dict[str, Any]] = []
    head: str | None = None
    if not ledger_path.exists():
        return events
    for number, line in enumerate(ledger_path.read_bytes().splitlines(), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PreregistrationError(f"ledger line {number} is not JSON") from exc
        if event.get("sequence") != number:
            raise PreregistrationError("ledger sequence is not contiguous")
        if event.get("previous_event_hash") != head:
            raise PreregistrationError("ledger hash chain is broken")
        claimed = event.get("event_hash")
        body = {key: value for key, value in event.items() if key != "event_hash"}
        if not isinstance(claimed, str) or not _is_hash(claimed) or claimed != _sha256(body):
            raise PreregistrationError(f"ledger line {number} integrity hash mismatch")
        if _canonical(event) != line:
            raise PreregistrationError(f"ledger line {number} is not canonical JSON")
        head = claimed
        events.append(event)
    return events


def _append(ledger_path: Path, events: list[dict[str, Any]], event_type: str, key: str,
            payload: Mapping[str, Any], created_at: datetime | None) -> dict[str, Any]:
    stamp = (created_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    event: dict[str, Any] = {
        "sequence": len(events) + 1,
        "idempotency_key": key,
        "type": event_type,
        "created_at": stamp.isoformat(),
        "payload": _jsonable(payload),
        "previous_event_hash": events[-1]["event_hash"] if events else None,
    }
    event["event_hash"] = _sha256(event)
    with ledger_path.open("ab") as handle:
        handle.write(_canonical(event) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    return event


def freeze(mission_dir: Path, code_paths: Sequence[str], *,
           created_at: datetime | None = None) -> FrozenPreregistration:
    """Append the freeze event. Refuses if the ledger already holds one."""
    ledger_path = mission_dir / LEDGER_NAME
    with _interprocess_lock(mission_dir / ".ledger.lock"):
        events = read_events(ledger_path)
        if any(event["type"] == FREEZE_EVENT for event in events):
            raise AlreadyFrozen(f"{mission_dir.name} is already frozen")
        payload = freeze_payload(mission_dir, code_paths)
        event = _append(ledger_path, events, FREEZE_EVENT,
                        f"{payload['mission']}:{FREEZE_EVENT}", payload, created_at)
    return _frozen(event)


def verdicts(mission_dir: Path) -> dict[str, dict[str, Any]]:
    """Recorded verdict payloads by campaign."""
    return {event["payload"]["campaign"]: event["payload"]
            for event in read_events(mission_dir / LEDGER_NAME) if event["type"] == VERDICT_EVENT}


def opened(mission_dir: Path) -> set[str]:
    """Campaigns whose one-use evaluation receipt has been written."""
    return {event["payload"]["campaign"]
            for event in read_events(mission_dir / LEDGER_NAME) if event["type"] == OPENED_EVENT}


def open_evaluation(mission_dir: Path, campaign: str, *, requires_pass: str | None = None,
                    track: str | None = None, inputs: Mapping[str, str] | None = None,
                    created_at: datetime | None = None) -> None:
    """Durably write the one-use receipt BEFORE any evaluation reads data.

    The receipt records the content hash of every input the evaluation will
    read. A crash after this point still consumes the evaluation: no retry.
    """
    verify(mission_dir)
    if track is not None:
        verify_track(mission_dir, track)
    ledger_path = mission_dir / LEDGER_NAME
    with _interprocess_lock(mission_dir / ".ledger.lock"):
        events = read_events(ledger_path)
        if any(e["type"] == OPENED_EVENT and e["payload"]["campaign"] == campaign for e in events):
            raise AlreadyOpened(f"{campaign} has already been opened for its one evaluation")
        if requires_pass is not None:
            prior = [e for e in events if e["type"] == VERDICT_EVENT
                     and e["payload"]["campaign"] == requires_pass]
            if not prior or prior[0]["payload"]["verdict"] != "PASS":
                raise PreregistrationError(f"{campaign} is sealed until {requires_pass} passes")
        _append(ledger_path, events, OPENED_EVENT, f"{campaign}:{OPENED_EVENT}",
                {"campaign": campaign, "inputs": dict(sorted((inputs or {}).items()))}, created_at)


def record_verdict(mission_dir: Path, campaign: str, result: Mapping[str, Any], *,
                   created_at: datetime | None = None) -> None:
    """Append a campaign's verdict, once, after its receipt."""
    ledger_path = mission_dir / LEDGER_NAME
    with _interprocess_lock(mission_dir / ".ledger.lock"):
        events = read_events(ledger_path)
        if not any(e["type"] == OPENED_EVENT and e["payload"]["campaign"] == campaign
                   for e in events):
            raise PreregistrationError(f"{campaign} has no evaluation receipt")
        if any(e["type"] == VERDICT_EVENT and e["payload"]["campaign"] == campaign for e in events):
            raise PreregistrationError(f"{campaign} already has a recorded verdict")
        _append(ledger_path, events, VERDICT_EVENT, f"{campaign}:{VERDICT_EVENT}",
                {"campaign": campaign, **result}, created_at)


def _frozen(event: Mapping[str, Any]) -> FrozenPreregistration:
    payload = event["payload"]
    return FrozenPreregistration(
        mission=payload["mission"], event_hash=event["event_hash"],
        created_at=event["created_at"], spec_sha256=payload["spec_sha256"],
        document_sha256=payload["document_sha256"], code_sha256=dict(payload["code_sha256"]),
    )


def verify(mission_dir: Path) -> FrozenPreregistration:
    """Recompute every bound hash and compare with the frozen ledger entry."""
    events = read_events(mission_dir / LEDGER_NAME)
    frozen = [event for event in events if event["type"] == FREEZE_EVENT]
    if len(frozen) != 1:
        raise PreregistrationError(f"expected exactly one freeze event, found {len(frozen)}")
    entry = _frozen(frozen[0])
    current = freeze_payload(mission_dir, list(entry.code_sha256))
    if current["mission"] != entry.mission:
        raise PreregistrationError("mission name differs from the frozen entry")
    if current["spec_sha256"] != entry.spec_sha256:
        raise PreregistrationError(f"{SPEC_NAME} differs from the frozen pre-registration")
    if current["document_sha256"] != entry.document_sha256:
        raise PreregistrationError(f"{DOCUMENT_NAME} differs from the frozen pre-registration")
    for name, digest in entry.code_sha256.items():
        if current["code_sha256"][name] != digest:
            raise PreregistrationError(f"{name} differs from the frozen pre-registration")
    return entry


@dataclass(frozen=True)
class FrozenTrack:
    mission: str
    track: str
    event_hash: str
    created_at: str
    spec_name: str
    document_name: str
    spec_sha256: str
    document_sha256: str
    code_sha256: Mapping[str, str]


def _track(event: Mapping[str, Any]) -> FrozenTrack:
    p = event["payload"]
    return FrozenTrack(p["mission"], p["track"], event["event_hash"], event["created_at"],
                       p["spec_name"], p["document_name"], p["spec_sha256"], p["document_sha256"],
                       dict(p["code_sha256"]))


def freeze_track(mission_dir: Path, track: str, *, spec_name: str, document_name: str,
                 code_paths: Sequence[str], created_at: datetime | None = None) -> FrozenTrack:
    """Chain a further, separately judged pre-registration after the primary freeze."""
    verify(mission_dir)
    ledger_path = mission_dir / LEDGER_NAME
    with _interprocess_lock(mission_dir / ".ledger.lock"):
        events = read_events(ledger_path)
        if any(e["type"] == TRACK_EVENT and e["payload"]["track"] == track for e in events):
            raise AlreadyFrozen(f"track {track} is already frozen")
        if any(e["type"] in {OPENED_EVENT, VERDICT_EVENT} for e in events):
            raise PreregistrationError("a track cannot be added after any evaluation was opened")
        payload = {"track": track, "spec_name": spec_name, "document_name": document_name,
                   **freeze_payload(mission_dir, code_paths, spec_name=spec_name,
                                    document_name=document_name)}
        event = _append(ledger_path, events, TRACK_EVENT, f"{payload['mission']}:{TRACK_EVENT}:{track}",
                        payload, created_at)
    return _track(event)


def verify_track(mission_dir: Path, track: str) -> FrozenTrack:
    """Recompute a chained track's bound hashes against its ledger entry."""
    events = read_events(mission_dir / LEDGER_NAME)
    found = [e for e in events if e["type"] == TRACK_EVENT and e["payload"]["track"] == track]
    if len(found) != 1:
        raise PreregistrationError(f"expected exactly one {track} track event, found {len(found)}")
    entry = _track(found[0])
    current = freeze_payload(mission_dir, list(entry.code_sha256), spec_name=entry.spec_name,
                             document_name=entry.document_name)
    if current["spec_sha256"] != entry.spec_sha256:
        raise PreregistrationError(f"{entry.spec_name} differs from the frozen {track} track")
    if current["document_sha256"] != entry.document_sha256:
        raise PreregistrationError(f"{entry.document_name} differs from the frozen {track} track")
    for name, digest in entry.code_sha256.items():
        if current["code_sha256"][name] != digest:
            raise PreregistrationError(f"{name} differs from the frozen {track} track")
    return entry
