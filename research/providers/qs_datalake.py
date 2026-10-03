"""Read-only adapter for the qs-research data lake.

qs-research keeps the raw vendor history on disk (CheddarFlow options prints
per symbol per trading day, Alpaca bars per symbol per timeframe) and exports
it in THIS repository's feature contract:

    python -m qs_research_web.datalake export-flow IBIT --out exports/IBIT.flow.jsonl

writes ``IBIT.flow.jsonl`` (canonical ``{"kind","payload"}`` envelopes, the
``FeatureExport`` JSONL) next to ``IBIT.flow.jsonl.manifest.json`` (the
``FeatureManifest``). Bars are gzip CSV with columns ts, open, high, low,
close, volume.

This module only reads those files and hands back the typed objects the loop
already validates. It holds no credentials and opens no network connection:
the lake is synced on the qs-research side, and the researcher sees an
immutable, hashed export of it.
"""

from __future__ import annotations

import csv
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

from research.providers.contracts import Bar, utc_iso
from research.v3.features import FeatureExport, FeatureManifest


def load_flow_export(jsonl_path: Path | str) -> FeatureExport:
    """A validated FeatureExport from a lake export and its manifest.

    Every contract check the loop applies (sorted rows, unique keys, provenance
    clocks, canonical JSON, content hash) runs here, so a tampered or
    truncated export fails at load time rather than inside a trial.
    """
    path = Path(jsonl_path)
    manifest_path = Path(str(path) + ".manifest.json")
    manifest = FeatureManifest(**json.loads(manifest_path.read_text(encoding="utf-8")))
    payload = path.read_text(encoding="utf-8")
    return FeatureExport.from_jsonl(manifest, payload)


def load_bars(csv_gz_path: Path | str) -> tuple[Bar, ...]:
    """Bars from a lake series file, in time order, as the provider Bar type."""
    bars: list[Bar] = []
    with gzip.open(Path(csv_gz_path), "rt", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            stamp = datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            volume = row.get("volume")
            bars.append(
                Bar(
                    timestamp_utc=stamp,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(volume) if volume not in (None, "") else None,
                )
            )
    bars.sort(key=lambda bar: bar.timestamp_utc)
    return tuple(bars)


def describe(jsonl_path: Path | str) -> dict[str, object]:
    """A one-line provenance summary for a mission's data manifest."""
    export = load_flow_export(jsonl_path)
    first = export.records[0].event_time if export.records else None
    last = export.records[-1].event_time if export.records else None
    return {
        "source": "qs-research data lake (CheddarFlow via flow-history)",
        "records": export.manifest.record_count,
        "windows": export.manifest.window_count,
        "first_event": first,
        "last_event": last,
        "content_hash": export.manifest.content_hash,
        "manifest_hash": export.manifest.manifest_hash,
        "loaded_at": utc_iso(datetime.now(timezone.utc)),
    }
