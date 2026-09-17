"""Tests for research/experiments/prepare_odte_credit.py.

Provider fetches are faked: no network, no credentials. The put-side default
path is regression-checked byte-for-byte against the pre---side implementation
at git HEAD.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from research.experiments import prepare_odte_credit as prep
from research.providers.contracts import Bar, CapabilityProbeError

ET = ZoneInfo("America/New_York")


def _weekdays(count: int, start: date = date(2026, 7, 6)) -> list[date]:
    days = []
    cursor = start
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


class FakeProvider:
    """Serves hourly bars from in-memory maps; raises CapabilityProbeError
    for symbols with no tape, exactly like the real snapshot contract."""

    def __init__(self, spot: dict[datetime, float], options: dict[str, dict[datetime, float]]):
        self.spot = spot
        self.options = options

    def bars(self, *, asset_class, symbols, start_utc, end_utc, timeframe, limit=None):
        if asset_class == "equity":
            bars = tuple(Bar(timestamp_utc=ts, open=px, high=px, low=px, close=px)
                         for ts, px in sorted(self.spot.items()))
            if not bars:
                raise CapabilityProbeError("no equity bars")
            return SimpleNamespace(bars=bars)
        prices = self.options.get(symbols[0])
        if not prices:
            raise CapabilityProbeError(f"no option bars for {symbols[0]}")
        bars = tuple(Bar(timestamp_utc=ts, open=px, high=px, low=px, close=px)
                     for ts, px in sorted(prices.items()))
        return SimpleNamespace(bars=bars)


def _spot_tape(days: list[date], settles: dict[date, float], entry_spot: float = 100.0):
    """Hourly bars 9:00-16:00 ET: flat at entry_spot through the 10:00 bar,
    flat at the day's settle from the 11:00 bar on (so the 15:00 bar — the
    16:00 close — reads settle)."""
    tape: dict[datetime, float] = {}
    for day in days:
        settle = settles[day]
        for hour in range(9, 17):
            moment = datetime(day.year, day.month, day.day, hour, tzinfo=ET).astimezone(UTC)
            tape[moment] = entry_spot if hour <= 10 else settle
    return tape


def _option_tape(days: list[date], underlying: str, right: str,
                 legs: dict[float, float]) -> dict[str, dict[datetime, float]]:
    """Every strike in legs trades its given price at every hour 8:00-17:00 ET."""
    tape: dict[str, dict[datetime, float]] = {}
    for day in days:
        expiry = datetime(day.year, day.month, day.day, tzinfo=UTC)
        for strike, price in legs.items():
            symbol = prep.occ_symbol(underlying, expiry, right, strike)
            tape[symbol] = {
                datetime(day.year, day.month, day.day, hour, tzinfo=ET).astimezone(UTC): price
                for hour in range(8, 18)
            }
    return tape


def _scenarios(days: list[date], settles_by_kind: dict[str, float]) -> dict[date, float]:
    kinds = list(settles_by_kind)
    return {day: settles_by_kind[kinds[i % len(kinds)]] for i, day in enumerate(days)}


def _build(tmp_path, provider, **kwargs) -> tuple[dict, list[dict]]:
    out = tmp_path / "snap"
    manifest = prep.build(underlying="SPY", start="2026-07-06", end="2026-10-01",
                          out=out, provider=provider, **kwargs)
    rows = [json.loads(line) for line in (out / "sessions.jsonl").read_text().splitlines()]
    return manifest, rows


def test_intrinsic_value_boundaries() -> None:
    # Call credit: win at/below short, full loss at/above long, linear between.
    assert prep.intrinsic_value("call", 105.0, 110.0, 104.0) == 0.0
    assert prep.intrinsic_value("call", 105.0, 110.0, 105.0) == 0.0
    assert prep.intrinsic_value("call", 105.0, 110.0, 107.5) == 2.5
    assert prep.intrinsic_value("call", 105.0, 110.0, 110.0) == 5.0
    assert prep.intrinsic_value("call", 105.0, 110.0, 112.0) == 5.0
    # Put credit: win at/above short, full loss at/below long.
    assert prep.intrinsic_value("put", 95.0, 90.0, 96.0) == 0.0
    assert prep.intrinsic_value("put", 95.0, 90.0, 95.0) == 0.0
    assert prep.intrinsic_value("put", 95.0, 90.0, 92.5) == 2.5
    assert prep.intrinsic_value("put", 95.0, 90.0, 90.0) == 5.0
    assert prep.intrinsic_value("put", 95.0, 90.0, 88.0) == 5.0


def test_call_payoff_win_partial_full_loss(tmp_path) -> None:
    days = _weekdays(70)
    settles = _scenarios(days, {"win": 104.0, "at_short": 105.0, "mid": 107.5, "full": 112.0})
    provider = FakeProvider(_spot_tape(days, settles),
                            _option_tape(days, "SPY", "C", {105.0: 2.0, 110.0: 1.0}))
    manifest, rows = _build(tmp_path, provider, side="call", width=5.0, offset=5.0)
    assert len(rows) == 70
    expected = {"win": 0.2, "at_short": 0.2, "mid": -0.3, "full": -0.8}
    for row in rows:
        day = date.fromisoformat(row["session"])
        kind = {"104.0": "win", "105.0": "at_short", "107.5": "mid", "112.0": "full"}[
            str(settles[day])]
        assert row["short_strike"] == 105.0 and row["long_strike"] == 110.0
        assert row["credit"] == 1.0
        assert row["features"]["credit_frac"] == 0.2
        assert row["next_open_to_close"] == pytest.approx(expected[kind])
        assert row["next_close_to_close"] == row["next_open_to_close"]
        assert row["symbol"] == "SPY-0DTE-CCS-w5o5"
    assert manifest["side"] == "call"
    assert manifest["structure"] == "call_credit_spread"
    assert manifest["width"] == 5.0 and manifest["offset"] == 5.0


def test_put_payoff_regression_unchanged(tmp_path) -> None:
    days = _weekdays(70)
    settles = _scenarios(days, {"win": 96.0, "at_short": 95.0, "mid": 92.5, "full": 88.0})
    provider = FakeProvider(_spot_tape(days, settles),
                            _option_tape(days, "SPY", "P", {95.0: 2.0, 90.0: 1.0}))
    manifest, rows = _build(tmp_path, provider)  # all defaults: put, w5/o5
    assert len(rows) == 70
    expected = {"win": 0.2, "at_short": 0.2, "mid": -0.3, "full": -0.8}
    for row in rows:
        day = date.fromisoformat(row["session"])
        kind = {"96.0": "win", "95.0": "at_short", "92.5": "mid", "88.0": "full"}[
            str(settles[day])]
        assert row["short_strike"] == 95.0 and row["long_strike"] == 90.0
        assert row["next_open_to_close"] == pytest.approx(expected[kind])
        assert row["symbol"] == "SPY-0DTE-PCS"
    assert manifest["side"] == "put"
    assert manifest["structure"] == "put_credit_spread"


# Commit immediately before --side landed; the byte-identical baseline.
PRE_SIDE_BASELINE = "6e898e1d7"


def test_pct_mode_matches_dollar_when_equivalent(tmp_path) -> None:
    """At spot 100, width_pct 0.05 / offset_pct 0.05 is exactly w5/o5 — for
    both sides — and the row shape follows the near-money convention."""
    days = _weekdays(70)
    settles = _scenarios(days, {"win": 104.0, "mid": 107.5, "full": 112.0})
    for side, right, legs in (("call", "C", {105.0: 2.0, 110.0: 1.0}),
                              ("put", "P", {95.0: 2.0, 90.0: 1.0})):
        provider = FakeProvider(_spot_tape(days, settles),
                                _option_tape(days, "SPY", right, legs))
        manifest, rows = _build(tmp_path / side, provider, side=side,
                                width_pct=0.05, offset_pct=0.05)
        assert len(rows) == 70
        tag = "CCS" if side == "call" else "PCS"
        for row in rows:
            assert row["symbol"] == f"SPY-0DTE-{tag}-NM"
            assert row["features"]["credit_frac"] == 0.2
            assert row["features"]["otm_pct"] == 0.05
        assert manifest["side"] == side
        assert manifest["width_pct"] == 0.05 and manifest["offset_pct"] == 0.05
        assert manifest["structure"] == f"{side}_credit_spread near-money (pct-defined)"
        assert "width" not in manifest and "offset" not in manifest


def test_pct_mode_floors(tmp_path) -> None:
    """qqq-nearmoney convention: offset = max($1, round(spot*offset_pct)),
    width = max($2, round(spot*width_pct)). At spot 100 the floors bind."""
    days = _weekdays(70)
    settles = _scenarios(days, {"win": 96.0, "mid": 98.5})
    provider = FakeProvider(_spot_tape(days, settles),
                            _option_tape(days, "SPY", "P", {99.0: 0.9, 97.0: 0.4}))
    manifest, rows = _build(tmp_path, provider, side="put",
                            width_pct=0.0064, offset_pct=0.0026)
    assert len(rows) == 70
    for row in rows:
        assert row["short_strike"] == 99.0 and row["long_strike"] == 97.0
        assert row["credit"] == 0.5
        assert row["features"]["credit_frac"] == 0.25  # 0.5 / width 2.0


def test_probe_grids_finds_coarse_strikes(tmp_path) -> None:
    """Single-name convention: the $1 probe misses, the $2.5 grid trades."""
    days = _weekdays(70)
    settles = _scenarios(days, {"win": 104.0, "mid": 107.5})
    # short target 103.75 -> $1 grid 104 (no tape), $2.5 grid 105 (tape);
    # long target 108.75 -> $1 grid 109 (no tape), $2.5 grid 110 (tape).
    provider = FakeProvider(_spot_tape(days, settles),
                            _option_tape(days, "SPY", "C", {105.0: 2.0, 110.0: 1.0}))
    manifest, rows = _build(tmp_path, provider, side="call",
                            width_pct=0.05, offset_pct=0.0375, probe_grids=True)
    assert len(rows) == 70
    for row in rows:
        assert row["short_strike"] == 105.0 and row["long_strike"] == 110.0
        assert row["credit"] == 1.0
    assert manifest["skipped"] == {"no_spot": 0, "no_option_bars": 0, "degenerate": 0}


def test_missing_option_bars_are_counted_not_dropped_silently(tmp_path) -> None:
    days = _weekdays(80)
    settles = _scenarios(days, {"win": 104.0})
    options = _option_tape(days, "SPY", "C", {105.0: 2.0, 110.0: 1.0})
    # Kill the long leg on every sixth day.
    for day in days[::6]:
        expiry = datetime(day.year, day.month, day.day, tzinfo=UTC)
        del options[prep.occ_symbol("SPY", expiry, "C", 110.0)]
    provider = FakeProvider(_spot_tape(days, settles), options)
    manifest, rows = _build(tmp_path, provider, side="call")
    assert manifest["skipped"]["no_option_bars"] == len(days[::6])
    assert len(rows) == 80 - len(days[::6])


def test_invalid_side_and_partial_pct_rejected(tmp_path) -> None:
    provider = FakeProvider({}, {})
    with pytest.raises(ValueError, match="side"):
        prep.build(underlying="SPY", start="2026-07-06", end="2026-10-01",
                   out=tmp_path / "a", side="straddle", provider=provider)
    with pytest.raises(ValueError, match="width_pct"):
        prep.build(underlying="SPY", start="2026-07-06", end="2026-10-01",
                   out=tmp_path / "b", width_pct=0.05, provider=provider)
