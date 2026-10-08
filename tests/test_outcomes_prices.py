"""Outcome prices: adjustment pinning, adjustment sanity, placeholder bars, cache reproducibility."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from research.outcomes import prices as P
from research.prereg import xnys
from research.providers.alpaca import AlpacaHistoricalProvider


def _sessions(start: date, count: int) -> list[date]:
    out, day = [], start
    while len(out) < count:
        if xnys.is_session(day):
            out.append(day)
        day += timedelta(days=1)
    return out


def _bar(day: date, close: float, volume: float = 1000.0, trades: int = 10):
    return SimpleNamespace(timestamp=datetime(day.year, day.month, day.day, 4, tzinfo=timezone.utc),
                           open=close, high=close, low=close, close=close, volume=volume,
                           trade_count=trades, vwap=close)


class FakeStockClient:
    """Serves a 10:1 split on the 3rd session: raw drops 10x, adjusted does not."""

    def __init__(self, days):
        self.days = days
        self.requests = []

    def get_stock_bars(self, request):
        self.requests.append(request)
        raw = [100.0, 102.0, 10.3, 10.4, 10.5]
        if request["adjustment"] == "all":
            closes = [c / 10 if i < 2 else c for i, c in enumerate(raw)]
        else:
            closes = raw
        return SimpleNamespace(data={request["symbol"]: [_bar(d, c) for d, c in zip(self.days, closes)]})


def _provider_factory(client):
    def make(adjustment):
        def request(**kwargs):
            return {"symbol": kwargs["symbols"][0], "adjustment": adjustment}
        return AlpacaHistoricalProvider(stock_client=client, request_factory=request)
    return make


def test_request_factory_pins_total_return_adjustment_and_feed():
    make = P.request_factory("all")
    start = datetime(2024, 1, 2, tzinfo=timezone.utc)
    request = make(asset_class="equity", symbols=("NVDA",), start_utc=start,
                   end_utc=start + timedelta(days=5), timeframe="1Day", limit=None)
    assert request.adjustment.value == "all"
    assert request.feed.value == "sip"
    assert request.symbol_or_symbols == "NVDA"
    with pytest.raises(ValueError):
        make(asset_class="equity", symbols=("NVDA",), start_utc=start,
             end_utc=start + timedelta(days=5), timeframe="1Min", limit=None)


def test_split_shows_as_an_adjustment_step_not_as_a_crash(tmp_path):
    days = _sessions(date(2024, 6, 5), 5)
    client = FakeStockClient(days)
    rows, audit = P.build(("NVDA",), days[0], days[-1], P.PriceCache(tmp_path), _provider_factory(client))
    a = audit["NVDA"]
    assert a["adjusted_outlier_days"] == []
    assert [s["session"] for s in a["adjustment_factor_steps"]] == [days[2].isoformat()]
    assert a["adjustment_factor_steps"][0]["raw_close_ratio"] == pytest.approx(10.3 / 102.0, abs=1e-4)
    assert a["missing_xnys_sessions"] == [] and a["bars_on_non_sessions"] == []
    assert rows[0]["adj_factor"] == pytest.approx(10.0)


def test_an_unadjusted_split_is_caught_as_an_outlier():
    days = _sessions(date(2024, 6, 5), 3)
    rows = [{"session": d, "close": c, "adj_factor": 1.0, "raw_close": c}
            for d, c in zip(days, (100.0, 102.0, 10.3))]
    assert [o["session"] for o in P.sanity(rows)["adjusted_outlier_days"]] == [days[2].isoformat()]


def test_missing_sessions_are_reported_not_filled():
    days = _sessions(date(2024, 3, 4), 5)
    rows = [{"session": d, "close": 10.0, "adj_factor": 1.0, "raw_close": 10.0}
            for d in days if d != days[2]]
    report = P.sanity(rows)
    assert report["missing_xnys_sessions"] == [days[2].isoformat()]
    assert report["sessions"] == 4


def test_untraded_placeholder_bars_are_dropped_and_counted():
    days = _sessions(date(2024, 10, 16), 4)
    snap = {"bars": [
        {"timestamp_utc": f"{d.isoformat()}T04:00:00Z", "open": 18.94, "high": 18.94, "low": 18.94,
         "close": 18.94 if i < 2 else 20.0, "volume": 0 if i < 2 else 5000,
         "trade_count": 0 if i < 2 else 50, "vwap": 0} for i, d in enumerate(days)]}
    rows, dropped = P.merge_rows("NBIS", snap, snap)
    assert dropped == [days[0].isoformat(), days[1].isoformat()]
    assert [r["session"] for r in rows] == days[2:]


def test_duplicate_daily_bars_are_refused():
    snap = {"bars": [{"timestamp_utc": "2024-03-04T05:00:00Z", "open": 1, "high": 1, "low": 1,
                      "close": 1, "volume": 1, "trade_count": 1, "vwap": 1}] * 2}
    with pytest.raises(ValueError):
        P.merge_rows("X", snap, snap)


def test_rebuild_from_cache_is_byte_identical_and_needs_no_provider(tmp_path):
    days = _sessions(date(2024, 6, 5), 5)
    client = FakeStockClient(days)
    cache = P.PriceCache(tmp_path)
    first_rows, first_audit = P.build(("NVDA",), days[0], days[-1], cache, _provider_factory(client))
    assert len(client.requests) == 2   # one adjusted, one raw
    second_rows, second_audit = P.build(("NVDA",), days[0], days[-1], cache, None)
    assert P.prices_csv(first_rows) == P.prices_csv(second_rows)
    assert first_audit["NVDA"]["cache_sha256"] == second_audit["NVDA"]["cache_sha256"]
    assert len(client.requests) == 2


def test_a_symbol_with_no_data_is_reported_not_invented(tmp_path):
    rows, audit = P.build(("NONE",), date(2024, 1, 2), date(2024, 1, 5), P.PriceCache(tmp_path), None)
    assert rows == []
    assert audit["NONE"]["sessions"] == 0 and "error" in audit["NONE"]
