"""Immutable proprietary-feature exports and causal derived snapshots.

Raw vendor-derived rows stop at this module. Candidate-facing snapshots
contain only aggregate metrics and integrity hashes; they expose no paths,
provider clients, credentials, network handles, or serializable raw records.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

FEATURE_EXPORT_SCHEMA_VERSION = 1
FEATURE_SNAPSHOT_SCHEMA_VERSION = 1
EXCHANGE_TIMEZONE = "America/New_York"
EXCHANGE_CALENDAR = "XNYS"
ET = ZoneInfo(EXCHANGE_TIMEZONE)
FAMILIES = {"flow", "gex", "dark_pool"}
REDISTRIBUTION_CLASSES = {"private_derived_only", "private_internal_only"}
FLOW_MAX_STALENESS_SECONDS = 15 * 60
GEX_MAX_CAPTURE_LAG_SECONDS = 10 * 60
GEX_MAX_STALENESS_SECONDS = 30 * 60
DARK_POOL_MAX_STALENESS_SECONDS = 4 * 24 * 60 * 60
COVERAGE_WEEK_FLOORS = {"discovery": 80, "validation": 40, "holdout": 20}
SPLIT_DATES = {
    "discovery": (date(2016, 1, 1), date(2021, 12, 31)),
    "validation": (date(2022, 1, 1), date(2024, 12, 31)),
    "holdout": (date(2025, 1, 1), None),
}


class FeatureContractError(ValueError):
    pass


class CoverageState(StrEnum):
    UNAVAILABLE = "unavailable"
    INSUFFICIENT_COVERAGE = "insufficient_coverage"
    READY = "ready"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _is_hash(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value.lower())


def _timestamp(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise FeatureContractError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise FeatureContractError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _finite(value: Any, name: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FeatureContractError(f"{name} must be finite")
    result = float(value)
    if not math.isfinite(result) or (nonnegative and result < 0):
        raise FeatureContractError(f"{name} must be finite")
    return result


def _validate_common(record: Any, *, time_field: str) -> tuple[datetime, datetime, datetime, datetime]:
    if record.schema_version != FEATURE_EXPORT_SCHEMA_VERSION or record.family not in FAMILIES:
        raise FeatureContractError("unsupported feature schema or source family")
    if not record.event_key or record.symbol != record.symbol.upper() or not record.symbol:
        raise FeatureContractError("event key and uppercase symbol are required")
    if (record.exchange_timezone != EXCHANGE_TIMEZONE
            or record.exchange_calendar != EXCHANGE_CALENDAR):
        raise FeatureContractError("exchange timezone/calendar must be America/New_York/XNYS")
    if record.redistribution_class not in REDISTRIBUTION_CLASSES:
        raise FeatureContractError("unsupported redistribution class")
    if not record.source_contract_version:
        raise FeatureContractError("source contract version is required")
    observed = _timestamp(getattr(record, time_field), time_field)
    snapshot = _timestamp(record.snapshot_time, "snapshot_time")
    available = _timestamp(record.available_at, "available_at")
    ingested = _timestamp(record.ingested_at, "ingested_at")
    if observed > available or available > ingested or snapshot > ingested:
        raise FeatureContractError("feature provenance clocks are inconsistent")
    return observed, snapshot, available, ingested


@dataclass(frozen=True)
class FlowRecord:
    schema_version: int
    family: str
    event_key: str
    symbol: str
    event_time: str
    snapshot_time: str
    available_at: str
    ingested_at: str
    exchange_timezone: str
    exchange_calendar: str
    redistribution_class: str
    source_contract_version: str
    option_type: str
    inferred_side: str
    premium: float
    dte: int
    is_sweep: bool
    is_block: bool

    def validate(self) -> None:
        _validate_common(self, time_field="event_time")
        if self.family != "flow" or self.option_type not in {"C", "P"}:
            raise FeatureContractError("flow option type is invalid")
        if self.inferred_side not in {"buy", "sell", "neutral"}:
            raise FeatureContractError("flow inferred side is invalid")
        _finite(self.premium, "flow premium", nonnegative=True)
        if isinstance(self.dte, bool) or not isinstance(self.dte, int) or self.dte < 0:
            raise FeatureContractError("flow DTE must be a non-negative integer")
        if not isinstance(self.is_sweep, bool) or not isinstance(self.is_block, bool):
            raise FeatureContractError("flow flags must be Boolean")


@dataclass(frozen=True)
class GexRecord:
    schema_version: int
    family: str
    event_key: str
    symbol: str
    event_time: str
    snapshot_time: str
    available_at: str
    ingested_at: str
    exchange_timezone: str
    exchange_calendar: str
    redistribution_class: str
    source_contract_version: str
    net_gex: float
    call_gex: float
    put_gex: float
    spot: float
    call_wall: float
    put_wall: float
    expected_move_up: float
    expected_move_down: float

    def validate(self) -> None:
        event, snapshot, available, ingested = _validate_common(self, time_field="event_time")
        if self.family != "gex" or event != snapshot:
            raise FeatureContractError("GEX must be a real point-in-time stored snapshot")
        capture_lag = (ingested - max(snapshot, available)).total_seconds()
        if not 0 <= capture_lag <= GEX_MAX_CAPTURE_LAG_SECONDS:
            raise FeatureContractError("GEX snapshot was not captured by the forward live ledger")
        for name in ("net_gex", "call_gex", "put_gex"):
            _finite(getattr(self, name), name)
        for name in ("spot", "call_wall", "put_wall", "expected_move_up", "expected_move_down"):
            if _finite(getattr(self, name), name, nonnegative=True) <= 0:
                raise FeatureContractError(f"{name} must be positive")


@dataclass(frozen=True)
class DarkPoolRecord:
    schema_version: int
    family: str
    event_key: str
    symbol: str
    event_time: str
    snapshot_time: str
    available_at: str
    ingested_at: str
    exchange_timezone: str
    exchange_calendar: str
    redistribution_class: str
    source_contract_version: str
    aggregation_kind: str
    price_level: float
    premium: float

    def validate(self) -> None:
        _validate_common(self, time_field="event_time")
        if self.family != "dark_pool" or self.aggregation_kind not in {
            "closed_aggregation", "forward_snapshot"
        }:
            raise FeatureContractError("dark-pool aggregation kind is invalid")
        if _finite(self.price_level, "dark-pool price", nonnegative=True) <= 0:
            raise FeatureContractError("dark-pool price must be positive")
        _finite(self.premium, "dark-pool premium", nonnegative=True)


SourceRecord = FlowRecord | GexRecord | DarkPoolRecord


@dataclass(frozen=True)
class ObservationWindow:
    schema_version: int
    family: str
    event_key: str
    symbol: str
    observation_time: str
    snapshot_time: str
    available_at: str
    ingested_at: str
    exchange_timezone: str
    exchange_calendar: str
    redistribution_class: str
    source_contract_version: str
    observation_kind: str
    event_count: int

    def validate(self) -> None:
        _, snapshot, available, ingested = _validate_common(
            self, time_field="observation_time"
        )
        allowed = {
            "flow": {"event_window"},
            "gex": {"snapshot"},
            "dark_pool": {"closed_aggregation", "forward_snapshot"},
        }
        if self.observation_kind not in allowed[self.family]:
            raise FeatureContractError("observation kind does not match its family")
        if self.family == "gex":
            capture_lag = (ingested - max(snapshot, available)).total_seconds()
            if not 0 <= capture_lag <= GEX_MAX_CAPTURE_LAG_SECONDS:
                raise FeatureContractError(
                    "GEX observation was not captured by the forward live ledger"
                )
        if isinstance(self.event_count, bool) or not isinstance(self.event_count, int) or self.event_count < 0:
            raise FeatureContractError("event_count must be a non-negative integer")


@dataclass(frozen=True)
class FeatureManifest:
    schema_version: int
    family: str
    source_hash: str
    created_at: str
    record_count: int
    window_count: int
    content_hash: str

    @property
    def manifest_hash(self) -> str:
        return _hash(asdict(self))

    def validate(self) -> None:
        if self.schema_version != FEATURE_EXPORT_SCHEMA_VERSION or self.family not in FAMILIES:
            raise FeatureContractError("unsupported feature manifest")
        if not _is_hash(self.source_hash) or not _is_hash(self.content_hash):
            raise FeatureContractError("feature manifest hashes are malformed")
        _timestamp(self.created_at, "created_at")
        if self.record_count < 0 or self.window_count < 0:
            raise FeatureContractError("feature manifest counts cannot be negative")


def _jsonl(records: tuple[SourceRecord, ...], windows: tuple[ObservationWindow, ...]) -> str:
    lines = []
    for record in records:
        lines.append(_canonical({"kind": "record", "payload": asdict(record)}).decode())
    for window in windows:
        lines.append(_canonical({"kind": "window", "payload": asdict(window)}).decode())
    return "\n".join(lines) + ("\n" if lines else "")


@dataclass(frozen=True)
class FeatureExport:
    manifest: FeatureManifest
    records: tuple[SourceRecord, ...]
    windows: tuple[ObservationWindow, ...]

    @classmethod
    def create(
        cls,
        *,
        family: str,
        source_hash: str,
        records: Iterable[SourceRecord],
        windows: Iterable[ObservationWindow],
        created_at: str | None = None,
    ) -> "FeatureExport":
        frozen_records = tuple(records)
        frozen_windows = tuple(windows)
        for item in (*frozen_records, *frozen_windows):
            item.validate()
            if item.family != family:
                raise FeatureContractError("feature row family does not match its export")
        payload = _jsonl(frozen_records, frozen_windows)
        value = cls(
            FeatureManifest(
                FEATURE_EXPORT_SCHEMA_VERSION,
                family,
                source_hash,
                created_at or datetime.now(timezone.utc).isoformat(),
                len(frozen_records),
                len(frozen_windows),
                hashlib.sha256(payload.encode()).hexdigest(),
            ),
            frozen_records,
            frozen_windows,
        )
        value.validate()
        return value

    @classmethod
    def from_jsonl(cls, manifest: FeatureManifest, payload: str) -> "FeatureExport":
        record_type = {"flow": FlowRecord, "gex": GexRecord, "dark_pool": DarkPoolRecord}
        records: list[SourceRecord] = []
        windows: list[ObservationWindow] = []
        try:
            for line in payload.splitlines():
                raw = json.loads(line)
                if set(raw) != {"kind", "payload"}:
                    raise FeatureContractError("feature JSONL envelope is malformed")
                if raw["kind"] == "record":
                    records.append(record_type[manifest.family](**raw["payload"]))
                elif raw["kind"] == "window":
                    windows.append(ObservationWindow(**raw["payload"]))
                else:
                    raise FeatureContractError("unknown feature JSONL row kind")
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise FeatureContractError("feature JSONL is malformed") from exc
        value = cls(manifest, tuple(records), tuple(windows))
        value.validate()
        if value.to_jsonl() != payload:
            raise FeatureContractError("feature JSONL is not canonical")
        return value

    def to_jsonl(self) -> str:
        return _jsonl(self.records, self.windows)

    def validate(self) -> None:
        self.manifest.validate()
        if (len(self.records) != self.manifest.record_count
                or len(self.windows) != self.manifest.window_count):
            raise FeatureContractError("feature manifest counts do not match the export")
        record_order = tuple(sorted(self.records, key=lambda item: (item.event_time, item.event_key)))
        window_order = tuple(sorted(
            self.windows, key=lambda item: (item.observation_time, item.event_key)
        ))
        if self.records != record_order or self.windows != window_order:
            raise FeatureContractError("feature export rows must be sorted")
        keys = [item.event_key for item in (*self.records, *self.windows)]
        if len(set(keys)) != len(keys):
            raise FeatureContractError("feature event keys must be unique")
        for item in (*self.records, *self.windows):
            item.validate()
            if item.family != self.manifest.family:
                raise FeatureContractError("feature row family does not match its manifest")
        self._validate_zero_event_windows()
        payload_hash = hashlib.sha256(self.to_jsonl().encode()).hexdigest()
        if payload_hash != self.manifest.content_hash:
            raise FeatureContractError("feature export content hash mismatch")

    def _validate_zero_event_windows(self) -> None:
        for window in self.windows:
            if window.event_count != 0:
                continue
            observed = _timestamp(window.observation_time, "observation_time")
            matching = False
            if window.family == "flow":
                matching = any(
                    isinstance(record, FlowRecord) and record.symbol == window.symbol
                    and _timestamp(record.event_time, "event_time").astimezone(ET).date()
                    == observed.astimezone(ET).date()
                    and _timestamp(record.event_time, "event_time") <= observed
                    for record in self.records
                )
            elif window.family == "gex":
                matching = any(
                    isinstance(record, GexRecord) and record.symbol == window.symbol
                    and _timestamp(record.snapshot_time, "snapshot_time") == observed
                    for record in self.records
                )
            else:
                matching = any(
                    isinstance(record, DarkPoolRecord) and record.symbol == window.symbol
                    and record.aggregation_kind == window.observation_kind
                    and _timestamp(record.event_time, "event_time") == observed
                    for record in self.records
                )
            if matching:
                raise FeatureContractError(
                    "zero-event observation window contradicts matching source rows"
                )


@dataclass(frozen=True)
class DerivedFamily:
    family: str
    covered: bool
    as_of_time: str | None
    staleness_seconds: float | None
    metrics: tuple[tuple[str, float], ...]
    source_hash: str | None
    export_manifest_hash: str | None

    def metric(self, name: str) -> float | None:
        return dict(self.metrics).get(name)


@dataclass(frozen=True)
class FeatureSnapshot:
    schema_version: int
    symbol: str
    decision_time: str
    redistribution_class: str
    families: tuple[DerivedFamily, ...]
    snapshot_hash: str

    @classmethod
    def create(cls, symbol: str, decision_time: str,
               families: Iterable[DerivedFamily]) -> "FeatureSnapshot":
        value = cls(
            FEATURE_SNAPSHOT_SCHEMA_VERSION,
            symbol,
            decision_time,
            "derived_aggregate_only",
            tuple(sorted(families, key=lambda item: item.family)),
            "",
        )
        value = replace(value, snapshot_hash=value.expected_hash())
        value.validate()
        return value

    def expected_hash(self) -> str:
        payload = asdict(self)
        payload.pop("snapshot_hash")
        return _hash(payload)

    def validate(self) -> None:
        if self.schema_version != FEATURE_SNAPSHOT_SCHEMA_VERSION:
            raise FeatureContractError("unsupported feature snapshot schema")
        if self.symbol != self.symbol.upper() or not self.symbol:
            raise FeatureContractError("feature snapshot symbol is invalid")
        _timestamp(self.decision_time, "decision_time")
        if self.redistribution_class != "derived_aggregate_only":
            raise FeatureContractError("candidate snapshots may contain derived aggregates only")
        if tuple(sorted(self.families, key=lambda item: item.family)) != self.families:
            raise FeatureContractError("derived feature families must be sorted")
        if len({item.family for item in self.families}) != len(self.families):
            raise FeatureContractError("derived feature families must be unique")
        for item in self.families:
            if item.family not in FAMILIES:
                raise FeatureContractError("unknown derived feature family")
            if tuple(sorted(item.metrics)) != item.metrics:
                raise FeatureContractError("derived metrics must be sorted")
            if len({name for name, _ in item.metrics}) != len(item.metrics):
                raise FeatureContractError("derived metric names must be unique")
            if any(not math.isfinite(value) for _, value in item.metrics):
                raise FeatureContractError("derived metrics must be finite")
            if item.covered:
                if (item.as_of_time is None or item.source_hash is None
                        or item.export_manifest_hash is None):
                    raise FeatureContractError("covered features require aggregate provenance")
                _timestamp(item.as_of_time, "feature as_of_time")
                if (item.staleness_seconds is None
                        or not math.isfinite(item.staleness_seconds)
                        or item.staleness_seconds < 0):
                    raise FeatureContractError("covered feature staleness must be finite")
            elif item.as_of_time is not None or item.staleness_seconds is not None or item.metrics:
                raise FeatureContractError("uncovered families cannot expose feature values")
            if item.source_hash is not None and not _is_hash(item.source_hash):
                raise FeatureContractError("derived source hash is malformed")
            if item.export_manifest_hash is not None and not _is_hash(item.export_manifest_hash):
                raise FeatureContractError("derived manifest hash is malformed")
        if self.snapshot_hash != self.expected_hash():
            raise FeatureContractError("feature snapshot integrity hash mismatch")

    def family(self, name: str) -> DerivedFamily | None:
        return next((item for item in self.families if item.family == name), None)

    def to_portable_dict(self) -> dict[str, Any]:
        """Return only derived aggregates and hashes; raw rows are unreachable."""
        self.validate()
        return asdict(self)


def _window_candidates(export: FeatureExport, symbol: str, decision: datetime,
                       kind: set[str], *, same_day: bool) -> list[ObservationWindow]:
    result = []
    decision_day = decision.astimezone(ET).date()
    for window in export.windows:
        observed = _timestamp(window.observation_time, "observation_time")
        available = _timestamp(window.available_at, "available_at")
        ingested = _timestamp(window.ingested_at, "ingested_at")
        if (window.symbol == symbol and window.observation_kind in kind
                and observed <= decision and available <= decision and ingested <= decision
                and (not same_day or observed.astimezone(ET).date() == decision_day)):
            result.append(window)
    return result


def _uncovered(family: str, export: FeatureExport | None) -> DerivedFamily:
    return DerivedFamily(
        family, False, None, None, (),
        export.manifest.source_hash if export else None,
        export.manifest.manifest_hash if export else None,
    )


def _flow_features(export: FeatureExport, symbol: str, decision: datetime) -> DerivedFamily:
    windows = _window_candidates(export, symbol, decision, {"event_window"}, same_day=True)
    if not windows:
        return _uncovered("flow", export)
    window = max(windows, key=lambda item: _timestamp(item.observation_time, "observation_time"))
    window_as_of = _timestamp(window.observation_time, "observation_time")
    staleness = (decision - window_as_of).total_seconds()
    if staleness < 0 or staleness > FLOW_MAX_STALENESS_SECONDS:
        return _uncovered("flow", export)
    day = decision.astimezone(ET).date()
    records = [
        item for item in export.records
        if isinstance(item, FlowRecord) and item.symbol == symbol
        and _timestamp(item.event_time, "event_time").astimezone(ET).date() == day
        and _timestamp(item.event_time, "event_time") <= window_as_of
        and _timestamp(item.available_at, "available_at") <= decision
        and _timestamp(item.ingested_at, "ingested_at") <= decision
    ]
    premium = sum(item.premium for item in records)
    buy = sum(item.premium for item in records if item.inferred_side == "buy")
    sell = sum(item.premium for item in records if item.inferred_side == "sell")
    metrics = {
        "block_count": float(sum(item.is_block for item in records)),
        "buy_premium": buy,
        "call_premium": sum(item.premium for item in records if item.option_type == "C"),
        "directional_imbalance": (buy - sell) / premium if premium else 0.0,
        "dte_0_premium": sum(item.premium for item in records if item.dte == 0),
        "dte_1_7_premium": sum(item.premium for item in records if 1 <= item.dte <= 7),
        "dte_8_plus_premium": sum(item.premium for item in records if item.dte >= 8),
        "neutral_premium": sum(item.premium for item in records if item.inferred_side == "neutral"),
        "put_premium": sum(item.premium for item in records if item.option_type == "P"),
        "sell_premium": sell,
        "sweep_count": float(sum(item.is_sweep for item in records)),
        "top_premium_concentration": max((item.premium for item in records), default=0.0)
        / premium if premium else 0.0,
        "trade_count": float(len(records)),
    }
    as_of = window_as_of
    return DerivedFamily(
        "flow", True, as_of.isoformat(), staleness,
        tuple(sorted(metrics.items())), export.manifest.source_hash, export.manifest.manifest_hash,
    )


def _gex_features(export: FeatureExport, symbol: str, decision: datetime) -> DerivedFamily:
    windows = _window_candidates(export, symbol, decision, {"snapshot"}, same_day=True)
    observed_snapshots = {
        _timestamp(window.observation_time, "observation_time")
        for window in windows if window.event_count > 0
    }
    candidates = [
        item for item in export.records
        if isinstance(item, GexRecord) and item.symbol == symbol
        and _timestamp(item.snapshot_time, "snapshot_time") in observed_snapshots
        and _timestamp(item.snapshot_time, "snapshot_time") <= decision
        and _timestamp(item.available_at, "available_at") <= decision
        and _timestamp(item.ingested_at, "ingested_at") <= decision
        and _timestamp(item.snapshot_time, "snapshot_time").astimezone(ET).date()
        == decision.astimezone(ET).date()
    ]
    if not windows or not candidates:
        return _uncovered("gex", export)
    record = max(candidates, key=lambda item: _timestamp(item.snapshot_time, "snapshot_time"))
    as_of = _timestamp(record.snapshot_time, "snapshot_time")
    staleness = (decision - as_of).total_seconds()
    if staleness < 0 or staleness > GEX_MAX_STALENESS_SECONDS:
        return _uncovered("gex", export)
    metrics = {
        "call_gex": record.call_gex,
        "call_wall_distance_pct": (record.call_wall / record.spot - 1) * 100,
        "expected_move_down_distance_pct": (record.expected_move_down / record.spot - 1) * 100,
        "expected_move_up_distance_pct": (record.expected_move_up / record.spot - 1) * 100,
        "net_gex": record.net_gex,
        "put_gex": record.put_gex,
        "put_wall_distance_pct": (record.put_wall / record.spot - 1) * 100,
        "staleness_seconds": staleness,
    }
    return DerivedFamily(
        "gex", True, as_of.isoformat(), staleness, tuple(sorted(metrics.items())),
        export.manifest.source_hash, export.manifest.manifest_hash,
    )


def _dark_pool_features(export: FeatureExport, symbol: str, decision: datetime,
                        spot: float) -> DerivedFamily:
    windows = _window_candidates(
        export, symbol, decision, {"closed_aggregation", "forward_snapshot"}, same_day=False
    )
    causal_windows = []
    decision_day = decision.astimezone(ET).date()
    for window in windows:
        observed = _timestamp(window.observation_time, "observation_time")
        if window.observation_kind == "closed_aggregation":
            if observed.astimezone(ET).date() >= decision_day:
                continue
        elif (_timestamp(window.snapshot_time, "snapshot_time") > decision
              or observed.astimezone(ET).date() != decision_day):
            continue
        causal_windows.append(window)
    if not causal_windows:
        return _uncovered("dark_pool", export)
    window = max(causal_windows, key=lambda item: _timestamp(item.observation_time, "observation_time"))
    as_of = _timestamp(window.observation_time, "observation_time")
    staleness = (decision - as_of).total_seconds()
    if staleness < 0 or staleness > DARK_POOL_MAX_STALENESS_SECONDS:
        return _uncovered("dark_pool", export)
    records = [
        item for item in export.records
        if isinstance(item, DarkPoolRecord) and item.symbol == symbol
        and item.aggregation_kind == window.observation_kind
        and _timestamp(item.event_time, "event_time") == as_of
        and _timestamp(item.available_at, "available_at") <= decision
        and _timestamp(item.ingested_at, "ingested_at") <= decision
        and (window.observation_kind != "forward_snapshot"
             or _timestamp(item.snapshot_time, "snapshot_time") <= decision)
    ]
    total = sum(item.premium for item in records)
    absolute_distances = [abs(item.price_level / spot - 1) * 100 for item in records]
    metrics = {
        "level_count": float(len(records)),
        "nearest_level_distance_pct": min(absolute_distances, default=0.0),
        "premium_weighted_abs_distance_pct": (
            sum(distance * item.premium for distance, item in zip(absolute_distances, records))
            / total if total else 0.0
        ),
        "staleness_seconds": staleness,
        "top_level_concentration": max((item.premium for item in records), default=0.0)
        / total if total else 0.0,
    }
    return DerivedFamily(
        "dark_pool", True, as_of.isoformat(), staleness, tuple(sorted(metrics.items())),
        export.manifest.source_hash, export.manifest.manifest_hash,
    )


def derive_feature_snapshot(
    exports: Mapping[str, FeatureExport],
    *,
    symbol: str,
    decision_time: str,
    spot: float,
    required_families: Iterable[str],
) -> FeatureSnapshot:
    decision = _timestamp(decision_time, "decision_time")
    symbol = symbol.upper()
    spot = _finite(spot, "spot", nonnegative=True)
    if spot <= 0:
        raise FeatureContractError("spot must be positive")
    requested = tuple(sorted(set(required_families)))
    if any(family not in FAMILIES for family in requested):
        raise FeatureContractError("unknown requested proprietary feature family")
    derived = []
    for family in requested:
        export = exports.get(family)
        if export is None:
            derived.append(_uncovered(family, None))
            continue
        export.validate()
        if export.manifest.family != family:
            raise FeatureContractError("export key does not match its family")
        if family == "flow":
            derived.append(_flow_features(export, symbol, decision))
        elif family == "gex":
            derived.append(_gex_features(export, symbol, decision))
        else:
            derived.append(_dark_pool_features(export, symbol, decision, spot))
    return FeatureSnapshot.create(symbol, decision.isoformat(), derived)


@dataclass(frozen=True)
class CoverageReport:
    family: str
    split: str
    state: CoverageState
    eligible_sessions: int
    covered_sessions: int
    coverage_ratio: float
    covered_iso_weeks: int
    required_iso_weeks: int
    source_available: bool

    def validate(self) -> None:
        if self.family not in FAMILIES or self.split not in COVERAGE_WEEK_FLOORS:
            raise FeatureContractError("unknown coverage family or split")
        if (self.eligible_sessions < 0 or not 0 <= self.covered_sessions <= self.eligible_sessions
                or self.covered_iso_weeks < 0
                or self.required_iso_weeks != COVERAGE_WEEK_FLOORS[self.split]):
            raise FeatureContractError("coverage counts are inconsistent")
        expected_ratio = self.covered_sessions / self.eligible_sessions if self.eligible_sessions else 0.0
        if abs(self.coverage_ratio - expected_ratio) > 1e-12:
            raise FeatureContractError("coverage ratio is inconsistent")
        expected_state = (
            CoverageState.UNAVAILABLE if not self.source_available
            else CoverageState.READY
            if self.coverage_ratio >= .90 and self.covered_iso_weeks >= self.required_iso_weeks
            else CoverageState.INSUFFICIENT_COVERAGE
        )
        if self.state != expected_state:
            raise FeatureContractError("coverage state does not match its evidence")


def coverage_report(
    family: str,
    split: str,
    snapshots: Iterable[FeatureSnapshot],
    *,
    source_available: bool = True,
) -> CoverageReport:
    if family not in FAMILIES or split not in COVERAGE_WEEK_FLOORS:
        raise FeatureContractError("unknown coverage family or split")
    values = tuple(snapshots)
    split_start, split_end = SPLIT_DATES[split]
    for snapshot in values:
        snapshot.validate()
        day = _timestamp(snapshot.decision_time, "decision_time").astimezone(ET).date()
        if day < split_start or (split_end is not None and day > split_end):
            raise FeatureContractError("feature snapshot is outside the declared split")
    covered = [snapshot for snapshot in values
               if snapshot.family(family) is not None and snapshot.family(family).covered]
    ratio = len(covered) / len(values) if values else 0.0
    weeks = len({
        _timestamp(snapshot.decision_time, "decision_time").astimezone(ET).date().isocalendar()[:2]
        for snapshot in covered
    })
    floor = COVERAGE_WEEK_FLOORS[split]
    if not source_available:
        state = CoverageState.UNAVAILABLE
    elif ratio < .90 or weeks < floor:
        state = CoverageState.INSUFFICIENT_COVERAGE
    else:
        state = CoverageState.READY
    report = CoverageReport(
        family, split, state, len(values), len(covered), ratio, weeks, floor, source_available
    )
    report.validate()
    return report


def proprietary_mission_ready(required_families: Iterable[str],
                              reports: Iterable[CoverageReport]) -> bool:
    required = set(required_families)
    if not required:
        return True
    if not required <= FAMILIES:
        raise FeatureContractError("unknown requested proprietary feature family")
    indexed = {(report.family, report.split): report for report in reports}
    for report in indexed.values():
        report.validate()
    return all(
        indexed.get((family, split)) is not None
        and indexed[(family, split)].state == CoverageState.READY
        for family in required for split in COVERAGE_WEEK_FLOORS
    )


def required_family_coverage_hash(reports: Iterable[CoverageReport]) -> str:
    """Hash a canonical, validated admission bundle for a frozen campaign."""
    values = tuple(reports)
    keys: set[tuple[str, str]] = set()
    for report in values:
        if not isinstance(report, CoverageReport):
            raise FeatureContractError("coverage admission requires typed reports")
        report.validate()
        key = (report.family, report.split)
        if key in keys:
            raise FeatureContractError("coverage admission reports must be unique")
        keys.add(key)
    payload = [
        asdict(report)
        for report in sorted(values, key=lambda item: (item.family, item.split))
    ]
    return _hash(payload)
