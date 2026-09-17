"""Shared contracts for historical-only provider adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping


class CapabilityProbeError(RuntimeError):
    """Raised when a provider cannot prove a requested capability."""


def require_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    value = value.astimezone(timezone.utc)
    if value.tzinfo is not timezone.utc:
        return value.replace(tzinfo=timezone.utc)
    return value


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(value: datetime) -> str:
    return require_utc(value, "datetime").isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ProviderCapability:
    provider: str
    asset_class: str
    history: str
    endpoint: str
    feed: str
    entitlement: str | None = None
    max_page_size: int | None = None


@dataclass(frozen=True, slots=True)
class Provenance:
    provider: str
    endpoint: str
    requested_at_utc: datetime
    start_utc: datetime
    end_utc: datetime
    symbols: tuple[str, ...]
    session: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    provider_request_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "requested_at_utc", require_utc(self.requested_at_utc, "requested_at_utc"))
        object.__setattr__(self, "start_utc", require_utc(self.start_utc, "start_utc"))
        object.__setattr__(self, "end_utc", require_utc(self.end_utc, "end_utc"))
        if self.end_utc <= self.start_utc:
            raise ValueError("end_utc must be after start_utc")
        if not self.symbols:
            raise ValueError("symbols must not be empty")

    def to_json(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "endpoint": self.endpoint,
            "requested_at_utc": utc_iso(self.requested_at_utc),
            "start_utc": utc_iso(self.start_utc),
            "end_utc": utc_iso(self.end_utc),
            "symbols": list(self.symbols),
            "session": self.session,
            "parameters": dict(sorted(self.parameters.items())),
            "provider_request_id": self.provider_request_id,
        }


@dataclass(frozen=True, slots=True)
class Bar:
    timestamp_utc: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    trade_count: int | None = None
    vwap: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp_utc", require_utc(self.timestamp_utc, "timestamp_utc"))

    def to_json(self) -> dict[str, Any]:
        return {
            "timestamp_utc": utc_iso(self.timestamp_utc),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "trade_count": self.trade_count,
            "vwap": self.vwap,
        }


@dataclass(frozen=True, slots=True)
class HistoricalSnapshot:
    capability: ProviderCapability
    provenance: Provenance
    bars: tuple[Bar, ...]

    def __post_init__(self) -> None:
        if not self.bars:
            raise CapabilityProbeError(
                f"{self.provenance.provider} returned no bars for {', '.join(self.provenance.symbols)}"
            )

    def to_json(self) -> dict[str, Any]:
        return {
            "capability": {
                "provider": self.capability.provider,
                "asset_class": self.capability.asset_class,
                "history": self.capability.history,
                "endpoint": self.capability.endpoint,
                "feed": self.capability.feed,
                "entitlement": self.capability.entitlement,
                "max_page_size": self.capability.max_page_size,
            },
            "provenance": self.provenance.to_json(),
            "bars": [bar.to_json() for bar in self.bars],
        }
