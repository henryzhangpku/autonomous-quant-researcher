from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from research.providers import (
    AlpacaHistoricalProvider,
    Bar,
    CapabilityProbeError,
    HistoricalSnapshot,
    MassiveHistoricalProvider,
    ProviderCapability,
    Provenance,
    SnapshotWriter,
)


FIXED_NOW = datetime(2026, 8, 10, 18, 0, tzinfo=timezone.utc)
START = datetime(2026, 8, 10, 13, 30, tzinfo=timezone.utc)
END = datetime(2026, 8, 10, 14, 30, tzinfo=timezone.utc)


class FakeAlpacaClient:
    def __init__(self, method_name: str, data):
        self.method_name = method_name
        self.data = data
        self.calls = []

    def __getattr__(self, name: str):
        if name != self.method_name:
            raise AttributeError(name)

        def call(request):
            self.calls.append(request)
            return SimpleNamespace(data=self.data)

        return call


def fake_bar(ts: datetime = START):
    return SimpleNamespace(
        timestamp=ts,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1234,
        trade_count=12,
        vwap=100.25,
    )


def test_contracts_are_immutable_and_require_utc_metadata():
    capability = ProviderCapability("alpaca", "equity", "bars", "endpoint", "SIP")
    with pytest.raises(FrozenInstanceError):
        capability.provider = "other"  # type: ignore[misc]

    with pytest.raises(ValueError, match="start_utc"):
        Provenance(
            provider="alpaca",
            endpoint="endpoint",
            requested_at_utc=FIXED_NOW,
            start_utc=datetime(2026, 8, 10, 13, 30),
            end_utc=END,
            symbols=("SPY",),
            session="utc",
        )


def test_alpaca_provider_uses_historical_clients_without_trading_imports():
    source = Path("research/providers/alpaca.py").read_text(encoding="utf-8")
    assert "alpaca.trading" not in source
    assert "alpaca.data.historical" in source

    stock = FakeAlpacaClient("get_stock_bars", {"SPY": [fake_bar()]})
    provider = AlpacaHistoricalProvider(
        stock_client=stock,
        requested_at=lambda: FIXED_NOW,
        request_factory=lambda **kwargs: kwargs,
    )

    snapshot = provider.bars(
        asset_class="equity",
        symbols=("SPY",),
        start_utc=START,
        end_utc=END,
        timeframe="1Min",
        limit=1,
    )

    assert stock.calls[0]["asset_class"] == "equity"
    assert snapshot.capability.provider == "alpaca"
    assert snapshot.provenance.session == "utc"
    assert snapshot.provenance.requested_at_utc == FIXED_NOW
    assert snapshot.bars[0].timestamp_utc == START


def test_alpaca_probe_fails_clearly_when_key_or_data_missing(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    with pytest.raises(CapabilityProbeError, match="ALPACA_API_KEY"):
        AlpacaHistoricalProvider.from_env()

    empty = FakeAlpacaClient("get_stock_bars", {"SPY": []})
    provider = AlpacaHistoricalProvider(
        stock_client=empty,
        requested_at=lambda: FIXED_NOW,
        request_factory=lambda **kwargs: kwargs,
    )
    capability = ProviderCapability("alpaca", "equity", "bars", "endpoint", "SIP")
    with pytest.raises(CapabilityProbeError, match="no bars|no data"):
        provider.probe(capability, symbol="SPY", start_utc=START, end_utc=END)


def test_massive_accepts_polygon_or_massive_keys_and_configured_base_url(monkeypatch):
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    with pytest.raises(CapabilityProbeError, match="POLYGON_API_KEY or MASSIVE_API_KEY"):
        MassiveHistoricalProvider.from_env()

    monkeypatch.setenv("POLYGON_API_KEY", "polygon-key")
    monkeypatch.setenv("POLYGON_BASE_URL", "https://example.test")
    provider = MassiveHistoricalProvider.from_env()

    assert provider.api_key == "polygon-key"
    assert provider.base_url == "https://example.test"


def test_massive_stock_bars_use_adjusted_aggregate_endpoint():
    calls = []

    def http_get(url, params, timeout_seconds):
        calls.append((url, dict(params), timeout_seconds))
        return {"status": "OK", "results": [
            {"t": 1786368600000, "o": 100, "h": 101, "l": 99, "c": 100.5, "v": 20}
        ]}

    provider = MassiveHistoricalProvider(api_key="key", base_url="https://massive.test", http_get=http_get)
    snapshot = provider.stock_aggregate_bars(
        ticker="QQQ", start_utc=START, end_utc=END, multiplier=5, timespan="minute"
    )

    assert "/v2/aggs/ticker/QQQ/range/5/minute/" in calls[0][0]
    assert calls[0][1]["adjusted"] == "true"
    assert snapshot.capability.asset_class == "stock"
    assert snapshot.bars[0].close == 100.5


def test_massive_futures_contract_discovery_and_aggregate_endpoint_are_bounded():
    calls = []

    def http_get(url, params, timeout_seconds):
        calls.append((url, dict(params), timeout_seconds))
        if url.endswith("/futures/v1/contracts"):
            return {"status": "OK", "results": [{"ticker": "ESU6", "last_trade_date": "2026-09-18"}]}
        if url.endswith("/futures/v1/aggs/ESU6"):
            return {"status": "OK", "results": [{"window_start": 1786368600000000000, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}]}
        raise AssertionError(url)

    provider = MassiveHistoricalProvider(
        api_key="key",
        base_url="https://massive.test",
        http_get=http_get,
        timeout_seconds=3.5,
        requested_at=lambda: FIXED_NOW,
    )

    contracts = provider.discover_futures_contracts(root_symbol="ES", as_of_utc=START, limit=999_999)
    snapshot = provider.futures_aggregate_bars(
        ticker=contracts[0]["ticker"],
        start_utc=START,
        end_utc=END,
        multiplier=1,
        timespan="minute",
    )

    assert calls[0][0] == "https://massive.test/futures/v1/contracts"
    assert calls[0][1]["limit"] == MassiveHistoricalProvider.MAX_LIMIT
    assert calls[0][1]["product_code"] == "ES"
    assert calls[0][1]["date"] == START.date().isoformat()
    assert calls[0][1]["apiKey"] == "key"
    assert calls[0][2] == 3.5
    assert calls[1][0] == "https://massive.test/futures/v1/aggs/ESU6"
    assert calls[1][1]["resolution"] == "1min"
    assert "window_start.gte" in calls[1][1]
    assert snapshot.provenance.endpoint == "/futures/v1/aggs/ESU6"
    assert snapshot.provenance.session == "utc"
    assert snapshot.bars[0].close == 1.5


def test_massive_probe_fails_without_silent_fallback_when_entitlement_or_data_missing():
    def http_get(_url, _params, _timeout_seconds):
        return {"status": "NOT_AUTHORIZED", "message": "futures entitlement required"}

    provider = MassiveHistoricalProvider(api_key="key", http_get=http_get)
    capability = ProviderCapability("massive", "future", "aggregate_bars", "/futures/v1/aggs/{ticker}", "utc")

    with pytest.raises(CapabilityProbeError, match="entitlement required"):
        provider.probe(capability, symbol="ESU6", start_utc=START, end_utc=END)


def test_snapshot_writer_is_deterministic_immutable_and_atomic(tmp_path):
    snapshot = HistoricalSnapshot(
        capability=ProviderCapability("alpaca", "equity", "bars", "endpoint", "SIP"),
        provenance=Provenance(
            provider="alpaca",
            endpoint="endpoint",
            requested_at_utc=FIXED_NOW,
            start_utc=START,
            end_utc=END,
            symbols=("SPY",),
            session="utc",
            parameters={"limit": 1, "timeframe": "1Min"},
        ),
        bars=(Bar(timestamp_utc=START, open=1.0, high=2.0, low=0.5, close=1.5),),
    )
    writer = SnapshotWriter(tmp_path / "research-data", clock=lambda: FIXED_NOW)

    manifest = writer.write("alpaca/spy/2026-08-10", snapshot)
    payload_bytes = manifest.payload_path.read_bytes()
    disk_manifest = json.loads(manifest.manifest_path.read_text(encoding="utf-8"))

    assert manifest.payload_sha256 == hashlib.sha256(payload_bytes).hexdigest()
    assert disk_manifest["payload_sha256"] == manifest.payload_sha256
    assert disk_manifest["created_at_utc"] == "2026-08-10T18:00:00Z"
    with pytest.raises(FileExistsError):
        writer.write("alpaca/spy/2026-08-10", snapshot)
    with pytest.raises(ValueError):
        writer.write("../escape", snapshot)
