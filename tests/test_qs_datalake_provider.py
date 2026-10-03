"""The qs-research lake export loads as a validated FeatureExport, and bars as Bar."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from research.providers.qs_datalake import describe, load_bars, load_flow_export
from research.v3.features import FeatureContractError


def _record(key: str, event_time: str, option_type: str = "C", side: str = "buy") -> dict:
    return {
        "schema_version": 1, "family": "flow", "event_key": key, "symbol": "IBIT",
        "event_time": event_time, "snapshot_time": "2026-09-05T00:00:00Z",
        "available_at": event_time, "ingested_at": "2026-09-05T00:00:00Z",
        "exchange_timezone": "America/New_York", "exchange_calendar": "XNYS",
        "redistribution_class": "private_derived_only",
        "source_contract_version": "cheddarflow-optionsFeed/qs-research-flow-history/1",
        "option_type": option_type, "inferred_side": side, "premium": 100000.0, "dte": 42,
        "is_sweep": True, "is_block": False,
    }


def _window(day_key: str, observation_time: str, count: int) -> dict:
    return {
        "schema_version": 1, "family": "flow", "event_key": day_key, "symbol": "IBIT",
        "observation_time": observation_time, "snapshot_time": "2026-09-05T00:00:00Z",
        "available_at": observation_time, "ingested_at": "2026-09-05T00:00:00Z",
        "exchange_timezone": "America/New_York", "exchange_calendar": "XNYS",
        "redistribution_class": "private_derived_only",
        "source_contract_version": "cheddarflow-optionsFeed/qs-research-flow-history/1",
        "observation_kind": "event_window", "event_count": count,
    }


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _write_export(tmp_path: Path, lines: list[str]) -> Path:
    payload = "\n".join(lines) + "\n"
    out = tmp_path / "IBIT.flow.jsonl"
    out.write_text(payload, encoding="utf-8", newline="\n")
    manifest = {
        "schema_version": 1, "family": "flow", "source_hash": "0" * 64,
        "created_at": "2026-09-05T00:00:00+00:00", "record_count": sum(1 for l in lines if '"record"' in l),
        "window_count": sum(1 for l in lines if '"window"' in l),
        "content_hash": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }
    Path(str(out) + ".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return out


def test_a_lake_export_loads_as_a_validated_feature_export(tmp_path: Path):
    out = _write_export(tmp_path, [
        _canonical({"kind": "record", "payload": _record("cf:IBIT:a", "2026-09-04T13:31:00Z")}),
        _canonical({"kind": "record", "payload": _record("cf:IBIT:b", "2026-09-04T14:00:00Z", "P", "neutral")}),
        _canonical({"kind": "window", "payload": _window("cf:IBIT:day:2026-09-04", "2026-09-04T20:00:00Z", 2)}),
    ])
    export = load_flow_export(out)
    assert export.manifest.record_count == 2 and export.manifest.window_count == 1
    assert export.records[0].event_key == "cf:IBIT:a" and export.records[1].option_type == "P"
    summary = describe(out)
    assert summary["first_event"] == "2026-09-04T13:31:00Z" and summary["content_hash"] == export.manifest.content_hash


def test_a_tampered_export_is_refused(tmp_path: Path):
    out = _write_export(tmp_path, [
        _canonical({"kind": "record", "payload": _record("cf:IBIT:a", "2026-09-04T13:31:00Z")}),
    ])
    text = out.read_text(encoding="utf-8").replace("100000.0", "900000.0")
    out.write_text(text, encoding="utf-8", newline="\n")
    with pytest.raises(FeatureContractError):
        load_flow_export(out)


def test_bars_load_in_time_order_as_the_provider_bar_type(tmp_path: Path):
    path = tmp_path / "hour.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        handle.write("ts,open,high,low,close,volume\n")
        handle.write("2026-09-05T01:00:00Z,101,102,100,101.5,3.5\n")
        handle.write("2026-09-05T00:00:00Z,100,101,99,100.5,2.0\n")
    bars = load_bars(path)
    assert [b.close for b in bars] == [100.5, 101.5]
    assert bars[0].timestamp_utc.isoformat() == "2026-09-05T00:00:00+00:00" and bars[0].volume == 2.0
