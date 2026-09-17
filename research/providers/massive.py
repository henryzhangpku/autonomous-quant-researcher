"""Polygon/Massive REST adapter for secondary historical market data."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .contracts import (
    Bar,
    CapabilityProbeError,
    HistoricalSnapshot,
    ProviderCapability,
    Provenance,
    require_utc,
    utc_iso,
    utc_now,
)

HttpGet = Callable[[str, Mapping[str, Any], float], Mapping[str, Any]]


class MassiveHistoricalProvider:
    """Secondary REST source for stock, crypto, and futures history.

    Polygon renamed to Massive, but existing Polygon credentials remain common;
    both POLYGON_API_KEY and MASSIVE_API_KEY are accepted explicitly.
    """

    DEFAULT_BASE_URL = "https://api.massive.com"
    MAX_LIMIT = 50_000
    DEFAULT_TIMEOUT_SECONDS = 10.0
    MAX_PAGES = 5

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        http_get: HttpGet | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_pages: int = MAX_PAGES,
        requested_at: Callable[[], datetime] = utc_now,
    ) -> None:
        if not api_key:
            raise CapabilityProbeError("POLYGON_API_KEY or MASSIVE_API_KEY is required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_pages <= 0:
            raise ValueError("max_pages must be positive")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.http_get = http_get or self._urlopen_json
        self.timeout_seconds = timeout_seconds
        self.max_pages = max_pages
        self._requested_at = requested_at

    @classmethod
    def from_env(cls) -> "MassiveHistoricalProvider":
        api_key = os.getenv("POLYGON_API_KEY", "") or os.getenv("MASSIVE_API_KEY", "")
        base_url = os.getenv("MASSIVE_BASE_URL", "") or os.getenv("POLYGON_BASE_URL", "") or cls.DEFAULT_BASE_URL
        return cls(api_key=api_key, base_url=base_url)

    @staticmethod
    def capabilities() -> tuple[ProviderCapability, ...]:
        return (
            ProviderCapability("massive", "stock", "aggregate_bars", "/v2/aggs/ticker/{ticker}/range", "utc", max_page_size=MassiveHistoricalProvider.MAX_LIMIT),
            ProviderCapability("massive", "crypto", "aggregate_bars", "/v2/aggs/ticker/{ticker}/range", "utc", max_page_size=MassiveHistoricalProvider.MAX_LIMIT),
            ProviderCapability("massive", "future", "contracts", "/futures/v1/contracts", "utc", "futures"),
            ProviderCapability("massive", "future", "aggregate_bars", "/futures/v1/aggs/{ticker}", "utc", "futures", MassiveHistoricalProvider.MAX_LIMIT),
        )

    def probe(self, capability: ProviderCapability, *, symbol: str, start_utc: datetime, end_utc: datetime) -> None:
        try:
            if capability.asset_class == "future" and capability.history == "contracts":
                contracts = self.discover_futures_contracts(root_symbol=symbol, as_of_utc=start_utc, limit=1)
                if not contracts:
                    raise CapabilityProbeError(f"Massive returned no futures contracts for {symbol}")
                return
            if capability.asset_class == "future":
                snapshot = self.futures_aggregate_bars(
                    ticker=symbol,
                    start_utc=start_utc,
                    end_utc=end_utc,
                    multiplier=1,
                    timespan="minute",
                    limit=1,
                )
            elif capability.asset_class == "crypto":
                snapshot = self.crypto_aggregate_bars(
                    ticker=symbol,
                    start_utc=start_utc,
                    end_utc=end_utc,
                    multiplier=1,
                    timespan="minute",
                    limit=1,
                )
            elif capability.asset_class == "stock":
                snapshot = self.stock_aggregate_bars(
                    ticker=symbol,
                    start_utc=start_utc,
                    end_utc=end_utc,
                    multiplier=1,
                    timespan="minute",
                    limit=1,
                )
            else:
                raise CapabilityProbeError(f"Massive does not advertise {capability.asset_class}")
        except CapabilityProbeError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CapabilityProbeError(f"Massive capability probe failed for {symbol}: {exc}") from exc
        if not snapshot.bars:
            raise CapabilityProbeError(f"Massive returned no data for {symbol}")

    def discover_futures_contracts(
        self,
        *,
        root_symbol: str,
        as_of_utc: datetime,
        limit: int = 100,
    ) -> tuple[dict[str, Any], ...]:
        as_of = require_utc(as_of_utc, "as_of_utc")
        payload = self._paged_get(
            "/futures/v1/contracts",
            {
                "product_code": root_symbol,
                "date": as_of.date().isoformat(),
                "limit": self._bounded_limit(limit),
                "sort": "ticker.asc",
            },
        )
        results = tuple(dict(item) for item in payload)
        if not results:
            raise CapabilityProbeError(f"Massive returned no futures contracts for {root_symbol} as of {utc_iso(as_of)}")
        return results

    def futures_aggregate_bars(
        self,
        *,
        ticker: str,
        start_utc: datetime,
        end_utc: datetime,
        multiplier: int,
        timespan: str,
        limit: int = MAX_LIMIT,
    ) -> HistoricalSnapshot:
        start = require_utc(start_utc, "start_utc")
        end = require_utc(end_utc, "end_utc")
        endpoint = f"/futures/v1/aggs/{ticker}"
        unit = {"second": "sec", "minute": "min"}.get(timespan, timespan)
        params = {
            "resolution": f"{multiplier}{unit}",
            "window_start.gte": int(start.timestamp() * 1_000_000_000),
            "window_start.lte": int(end.timestamp() * 1_000_000_000),
            "limit": self._bounded_limit(limit),
            "sort": "window_start.asc",
        }
        data = self._paged_get(endpoint, params)
        return self._snapshot(
            asset_class="future",
            endpoint=endpoint,
            ticker=ticker,
            start_utc=start,
            end_utc=end,
            parameters=params,
            bars=data,
        )

    def crypto_aggregate_bars(
        self,
        *,
        ticker: str,
        start_utc: datetime,
        end_utc: datetime,
        multiplier: int,
        timespan: str,
        limit: int = MAX_LIMIT,
    ) -> HistoricalSnapshot:
        start = require_utc(start_utc, "start_utc")
        end = require_utc(end_utc, "end_utc")
        endpoint = f"/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{utc_iso(start)}/{utc_iso(end)}"
        params = {"limit": self._bounded_limit(limit), "adjusted": "true"}
        data = self._paged_get(endpoint, params)
        return self._snapshot(
            asset_class="crypto",
            endpoint=endpoint,
            ticker=ticker,
            start_utc=start,
            end_utc=end,
            parameters=params,
            bars=data,
        )

    def stock_aggregate_bars(
        self,
        *,
        ticker: str,
        start_utc: datetime,
        end_utc: datetime,
        multiplier: int,
        timespan: str,
        limit: int = MAX_LIMIT,
    ) -> HistoricalSnapshot:
        """Read adjusted US stock/ETF bars through the secondary provider."""

        start = require_utc(start_utc, "start_utc")
        end = require_utc(end_utc, "end_utc")
        endpoint = (
            f"/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/"
            f"{start.date().isoformat()}/{end.date().isoformat()}"
        )
        params = {"limit": self._bounded_limit(limit), "adjusted": "true"}
        data = self._paged_get(endpoint, params)
        return self._snapshot(
            asset_class="stock",
            endpoint=endpoint,
            ticker=ticker,
            start_utc=start,
            end_utc=end,
            parameters=params,
            bars=data,
        )

    def _snapshot(
        self,
        *,
        asset_class: str,
        endpoint: str,
        ticker: str,
        start_utc: datetime,
        end_utc: datetime,
        parameters: Mapping[str, Any],
        bars: tuple[Mapping[str, Any], ...],
    ) -> HistoricalSnapshot:
        capability = ProviderCapability("massive", asset_class, "aggregate_bars", endpoint, "utc")
        provenance = Provenance(
            provider="massive",
            endpoint=endpoint,
            requested_at_utc=self._requested_at(),
            start_utc=start_utc,
            end_utc=end_utc,
            symbols=(ticker,),
            session="utc",
            parameters=parameters,
        )
        return HistoricalSnapshot(
            capability=capability,
            provenance=provenance,
            bars=tuple(_bar_from_massive(item) for item in bars),
        )

    def _paged_get(self, endpoint: str, params: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
        url = f"{self.base_url}{endpoint}"
        request_params = dict(params)
        request_params["apiKey"] = self.api_key
        results: list[Mapping[str, Any]] = []
        for _page in range(self.max_pages):
            payload = self.http_get(url, request_params, self.timeout_seconds)
            status = str(payload.get("status", "")).upper()
            if status in {"ERROR", "NOT_AUTHORIZED"}:
                message = payload.get("error") or payload.get("message") or status
                raise CapabilityProbeError(f"Massive request failed for {endpoint}: {message}")
            page_results = payload.get("results")
            if not page_results:
                raise CapabilityProbeError(f"Massive returned no data for {endpoint}")
            results.extend(page_results)
            next_url = payload.get("next_url")
            if not next_url:
                return tuple(results)
            request_params = {"apiKey": self.api_key}
            url = str(next_url)
        raise CapabilityProbeError(f"Massive pagination exceeded {self.max_pages} pages for {endpoint}")

    @staticmethod
    def _bounded_limit(limit: int) -> int:
        if limit <= 0:
            raise ValueError("limit must be positive")
        return min(limit, MassiveHistoricalProvider.MAX_LIMIT)

    @staticmethod
    def _urlopen_json(url: str, params: Mapping[str, Any], timeout_seconds: float) -> Mapping[str, Any]:
        separator = "&" if "?" in url else "?"
        full_url = f"{url}{separator}{urlencode(params)}"
        request = Request(full_url, headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - caller controls trusted provider URL
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise CapabilityProbeError(f"Massive HTTP {exc.code} for {url}") from exc
        except URLError as exc:
            raise CapabilityProbeError(f"Massive network failure for {url}: {exc.reason}") from exc


def _bar_from_massive(item: Mapping[str, Any]) -> Bar:
    raw_ts = item.get("t") or item.get("timestamp") or item.get("window_start")
    if raw_ts is None:
        raise CapabilityProbeError("Massive aggregate bar is missing timestamp")
    raw_timestamp = float(raw_ts)
    divisor = 1_000_000_000.0 if raw_timestamp >= 1e17 else 1000.0
    timestamp = datetime.fromtimestamp(raw_timestamp / divisor, tz=timezone.utc)
    volume = item.get("v", item.get("volume"))
    transactions = item.get("n", item.get("transactions"))
    vwap = item.get("vw")
    if vwap is None and volume and item.get("dollar_volume") is not None:
        vwap = float(item["dollar_volume"]) / float(volume)
    return Bar(
        timestamp_utc=timestamp,
        open=float(item.get("o", item.get("open"))),
        high=float(item.get("h", item.get("high"))),
        low=float(item.get("l", item.get("low"))),
        close=float(item.get("c", item.get("close"))),
        volume=None if volume is None else float(volume),
        trade_count=None if transactions is None else int(transactions),
        vwap=None if vwap is None else float(vwap),
    )
