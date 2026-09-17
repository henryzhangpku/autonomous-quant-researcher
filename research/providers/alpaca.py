"""Alpaca historical-data adapter.

This module intentionally uses only ``alpaca.data.historical`` clients. The
research pivot forbids importing Alpaca trading clients or request types here.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Callable, Mapping

from .contracts import (
    Bar,
    CapabilityProbeError,
    HistoricalSnapshot,
    ProviderCapability,
    Provenance,
    require_utc,
    utc_now,
)


class AlpacaHistoricalProvider:
    """Primary historical source for equities, options, and crypto."""

    PROVIDER = "alpaca"

    def __init__(
        self,
        *,
        stock_client: Any | None = None,
        option_client: Any | None = None,
        crypto_client: Any | None = None,
        request_factory: Callable[..., Any] | None = None,
        timeframe_factory: Callable[[str], Any] | None = None,
        requested_at: Callable[[], datetime] = utc_now,
    ) -> None:
        self._stock_client = stock_client
        self._option_client = option_client
        self._crypto_client = crypto_client
        self._request_factory = request_factory
        self._timeframe_factory = timeframe_factory
        self._requested_at = requested_at

    @classmethod
    def from_env(cls) -> "AlpacaHistoricalProvider":
        key = os.getenv("ALPACA_API_KEY", "")
        secret = os.getenv("ALPACA_API_SECRET", "") or os.getenv("ALPACA_SECRET_KEY", "")
        if not key or not secret:
            raise CapabilityProbeError("ALPACA_API_KEY and ALPACA_API_SECRET are required for Alpaca history")

        from alpaca.data.historical import (  # noqa: PLC0415
            CryptoHistoricalDataClient,
            OptionHistoricalDataClient,
            StockHistoricalDataClient,
        )

        return cls(
            stock_client=StockHistoricalDataClient(key, secret),
            option_client=OptionHistoricalDataClient(key, secret),
            crypto_client=CryptoHistoricalDataClient(key, secret),
        )

    @staticmethod
    def capabilities() -> tuple[ProviderCapability, ...]:
        return (
            ProviderCapability("alpaca", "equity", "bars", "StockHistoricalDataClient.get_stock_bars", "SIP"),
            ProviderCapability("alpaca", "option", "bars", "OptionHistoricalDataClient.get_option_bars", "OPRA", "options"),
            ProviderCapability("alpaca", "crypto", "bars", "CryptoHistoricalDataClient.get_crypto_bars", "US", "crypto"),
        )

    def probe(self, capability: ProviderCapability, *, symbol: str, start_utc: datetime, end_utc: datetime) -> None:
        try:
            snapshot = self.bars(
                asset_class=capability.asset_class,
                symbols=(symbol,),
                start_utc=start_utc,
                end_utc=end_utc,
                timeframe="1Min",
                limit=1,
            )
        except CapabilityProbeError:
            raise
        except Exception as exc:  # noqa: BLE001 - provider SDKs raise heterogeneous entitlement errors
            raise CapabilityProbeError(
                f"Alpaca capability probe failed for {capability.asset_class}/{symbol}: {exc}"
            ) from exc
        if not snapshot.bars:
            raise CapabilityProbeError(f"Alpaca returned no data for {capability.asset_class}/{symbol}")

    def bars(
        self,
        *,
        asset_class: str,
        symbols: tuple[str, ...],
        start_utc: datetime,
        end_utc: datetime,
        timeframe: str,
        limit: int | None = None,
    ) -> HistoricalSnapshot:
        start = require_utc(start_utc, "start_utc")
        end = require_utc(end_utc, "end_utc")
        client, method_name, endpoint, feed = self._client_for(asset_class)
        request = self._make_request(
            asset_class=asset_class,
            symbols=symbols,
            start_utc=start,
            end_utc=end,
            timeframe=timeframe,
            limit=limit,
        )
        try:
            response = getattr(client, method_name)(request)
        except Exception as exc:  # noqa: BLE001
            raise CapabilityProbeError(f"Alpaca {asset_class} history request failed: {exc}") from exc

        bars = tuple(self._iter_bars(response, symbols))
        capability = ProviderCapability("alpaca", asset_class, "bars", endpoint, feed)
        provenance = Provenance(
            provider="alpaca",
            endpoint=endpoint,
            requested_at_utc=self._requested_at(),
            start_utc=start,
            end_utc=end,
            symbols=tuple(symbols),
            session="utc",
            parameters={"timeframe": timeframe, "limit": limit,
                        "adjustment": "split" if asset_class == "equity" else None},
        )
        return HistoricalSnapshot(capability=capability, provenance=provenance, bars=bars)

    def _client_for(self, asset_class: str) -> tuple[Any, str, str, str]:
        if asset_class == "equity":
            return self._require_client(self._stock_client, asset_class), "get_stock_bars", "StockHistoricalDataClient.get_stock_bars", "SIP"
        if asset_class == "option":
            return self._require_client(self._option_client, asset_class), "get_option_bars", "OptionHistoricalDataClient.get_option_bars", "OPRA"
        if asset_class == "crypto":
            return self._require_client(self._crypto_client, asset_class), "get_crypto_bars", "CryptoHistoricalDataClient.get_crypto_bars", "US"
        raise ValueError(f"unsupported Alpaca asset_class: {asset_class}")

    @staticmethod
    def _require_client(client: Any | None, asset_class: str) -> Any:
        if client is None:
            raise CapabilityProbeError(f"Alpaca {asset_class} historical client is not configured")
        return client

    def _make_request(
        self,
        *,
        asset_class: str,
        symbols: tuple[str, ...],
        start_utc: datetime,
        end_utc: datetime,
        timeframe: str,
        limit: int | None,
    ) -> Any:
        if self._request_factory is not None:
            return self._request_factory(
                asset_class=asset_class,
                symbols=symbols,
                start_utc=start_utc,
                end_utc=end_utc,
                timeframe=timeframe,
                limit=limit,
            )

        from alpaca.data.requests import (  # noqa: PLC0415
            CryptoBarsRequest,
            OptionBarsRequest,
            StockBarsRequest,
        )
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit  # noqa: PLC0415

        if self._timeframe_factory is not None:
            timeframe_value = self._timeframe_factory(timeframe)
        else:
            # The string must actually be honored: the old default ignored it
            # and always requested minute bars, so a "1Day" caller silently
            # received minute data.
            match = re.fullmatch(r"(\d+)(Min|Hour|Day|Week|Month)", timeframe)
            if match is None:
                raise CapabilityProbeError(f"Unsupported Alpaca timeframe: {timeframe!r}")
            timeframe_value = TimeFrame(int(match.group(1)), TimeFrameUnit(match.group(2)))
        request_cls = {
            "equity": StockBarsRequest,
            "option": OptionBarsRequest,
            "crypto": CryptoBarsRequest,
        }[asset_class]
        kwargs: dict[str, Any] = {
            "symbol_or_symbols": list(symbols) if len(symbols) > 1 else symbols[0],
            "timeframe": timeframe_value,
            "start": start_utc,
            "end": end_utc,
        }
        if limit is not None:
            kwargs["limit"] = limit
        if asset_class == "equity":
            # Alpaca defaults to adjustment="raw". Unadjusted daily bars render
            # every stock split as a catastrophic one-day crash: staged
            # 2026-08-23, GOOGL's 20:1 printed -95.1%, AMZN's 20:1 -94.9%,
            # NVDA's 10:1 -89.9%, and each one dragged its RSI to a maximally
            # oversold reading produced by a corporate action rather than by
            # the market. Every moving-average, range and momentum feature is
            # wrong for weeks after each split. Options and crypto do not
            # split, and OptionBarsRequest/CryptoBarsRequest take no such
            # parameter, so this is equity-only.
            kwargs["adjustment"] = "split"
        return request_cls(**kwargs)

    @staticmethod
    def _iter_bars(response: Any, symbols: tuple[str, ...]) -> list[Bar]:
        data = getattr(response, "data", response)
        if isinstance(data, Mapping):
            raw_bars = []
            for symbol in symbols:
                raw_bars.extend(data.get(symbol, ()))
        else:
            raw_bars = list(data or ())

        return [
            Bar(
                timestamp_utc=getattr(raw, "timestamp"),
                open=float(getattr(raw, "open")),
                high=float(getattr(raw, "high")),
                low=float(getattr(raw, "low")),
                close=float(getattr(raw, "close")),
                volume=_optional_float(getattr(raw, "volume", None)),
                trade_count=_optional_int(getattr(raw, "trade_count", None)),
                vwap=_optional_float(getattr(raw, "vwap", None)),
            )
            for raw in raw_bars
        ]


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)
