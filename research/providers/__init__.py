"""Read-only historical data provider contracts for research snapshots."""

from .alpaca import AlpacaHistoricalProvider
from .contracts import (
    Bar,
    CapabilityProbeError,
    HistoricalSnapshot,
    ProviderCapability,
    Provenance,
)
from .massive import MassiveHistoricalProvider
from .public_csv import PublicCsvProvider, load_bars
from .snapshots import SnapshotManifest, SnapshotWriter

__all__ = [
    "AlpacaHistoricalProvider",
    "Bar",
    "CapabilityProbeError",
    "HistoricalSnapshot",
    "MassiveHistoricalProvider",
    "ProviderCapability",
    "Provenance",
    "PublicCsvProvider",
    "SnapshotManifest",
    "SnapshotWriter",
    "load_bars",
]
