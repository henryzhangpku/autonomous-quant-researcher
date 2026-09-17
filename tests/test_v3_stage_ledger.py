from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.backtest.data import Session
from research.v3.ledger import GrossTradeLedger, GrossTradeRow
from research.v3.stage_store import StageAccess, materialize_stage_stores


def session(day: str) -> Session:
    return Session(day, ((570, 100.0, 101.0, 99.0, 100.5),))


def rewrite_manifest(path: Path, **updates: object) -> None:
    path.chmod(0o666)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.update(updates)
    path.write_bytes(
        (json.dumps(raw, sort_keys=True, separators=(",", ":")) + "\n").encode()
    )


def test_stage_capability_cannot_resolve_later_store(tmp_path: Path) -> None:
    manifests = materialize_stage_stores(
        tmp_path,
        [session("2016-01-04"), session("2022-01-03"), session("2025-01-02")],
        source_hash="ab" * 32,
    )
    access = StageAccess(tmp_path, "discovery")
    assert [item.day for item in access.open_sessions()] == ["2016-01-04"]
    assert manifests["validation"].sessions == 1
    with pytest.raises(PermissionError):
        access.resolve("validation")
    with pytest.raises(FileExistsError):
        materialize_stage_stores(tmp_path, [], source_hash="ab" * 32)


def test_stage_content_tampering_fails_closed(tmp_path: Path) -> None:
    materialize_stage_stores(tmp_path, [session("2022-01-03")], source_hash="ab" * 32)
    data = tmp_path / "validation" / "sessions.jsonl"
    data.chmod(0o666)
    data.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="content hash"):
        StageAccess(tmp_path, "validation").verify_integrity()


def test_stage_integrity_requires_sessions_file(tmp_path: Path) -> None:
    materialize_stage_stores(tmp_path, [session("2022-01-03")], source_hash="ab" * 32)
    data = tmp_path / "validation" / "sessions.jsonl"
    data.chmod(0o666)
    data.unlink()
    with pytest.raises(FileNotFoundError, match="sessions.jsonl"):
        StageAccess(tmp_path, "validation").verify_integrity()


def test_empty_stage_is_valid_but_not_deployment_ready(tmp_path: Path) -> None:
    materialize_stage_stores(tmp_path, [], source_hash="ab" * 32)
    access = StageAccess(tmp_path, "validation")
    assert access.verify_integrity().sessions == 0
    with pytest.raises(ValueError, match="deployment-ready"):
        access.verify_integrity(require_usable=True)


@pytest.mark.parametrize(
    "updates, message",
    [
        ({"rows": 2, "sessions": 2}, "counts"),
        ({"start_date": "2021-01-01"}, "access capability"),
    ],
)
def test_stage_integrity_rejects_forged_counts_and_dates(
    tmp_path: Path, updates: dict[str, object], message: str,
) -> None:
    materialize_stage_stores(tmp_path, [session("2022-01-03")], source_hash="ab" * 32)
    rewrite_manifest(tmp_path / "validation" / "manifest.json", **updates)
    with pytest.raises(ValueError, match=message):
        StageAccess(tmp_path, "validation").verify_integrity()


def test_stage_integrity_accepts_positive_canonical_content(tmp_path: Path) -> None:
    materialize_stage_stores(
        tmp_path,
        [session("2022-01-03"), session("2022-01-04")],
        source_hash="ab" * 32,
    )
    manifest = StageAccess(tmp_path, "validation").verify_integrity(require_usable=True)
    assert (manifest.stage, manifest.sessions, manifest.rows) == ("validation", 2, 2)


def test_gross_ledger_retains_selected_unpriced_rows_and_denominator() -> None:
    priced = GrossTradeRow("2024-01-02", True, True, "call debit", 2, .8, 2, 2, 0, 1.2, "opra")
    missing = GrossTradeRow("2024-01-03", True, False, "call debit", None, None, None,
                            None, None, None, "opra", skip_reason="no quote")
    rejected = GrossTradeRow("2024-01-04", False, False, "call debit", None, None, None,
                             None, None, None, "opra")
    ledger = GrossTradeLedger([priced, missing, rejected])
    assert ledger.selected_count == 2
    assert ledger.priced_selected_count == 1
    assert ledger.coverage == 0.5
    assert ledger.content_hash == GrossTradeLedger([priced, missing, rejected]).content_hash


def test_ledger_rejects_inconsistent_gross_pnl() -> None:
    row = GrossTradeRow("2024-01-02", True, True, "call debit", 2, .8, 2, 2, 0, 999, "modeled")
    with pytest.raises(ValueError, match="gross_pnl"):
        GrossTradeLedger([row])
