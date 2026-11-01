"""Tests for research/experiments/prepare_intraday_sessions.py.

Provider fetches are faked/monkeypatched exactly as in
tests/test_bars_universe_validator.py: no network, no credentials.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from research.experiments import prepare_intraday_sessions as prep

ET = ZoneInfo("America/New_York")
DECISION_HM = 10 * 60 + 30  # module default 10:30


def _bar(day: date, hm: int, o: float, h: float, l: float, c: float, v: float = 1000.0) -> dict:
    moment = datetime(day.year, day.month, day.day, hm // 60, hm % 60, tzinfo=ET)
    return {"ts": moment.astimezone(UTC), "open": o, "high": h, "low": l, "close": c, "volume": v}


def _weekdays(count: int, start: date = date(2026, 1, 5)) -> list[date]:
    days = []
    cursor = start
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _day_bars(day: date, *, pre_close: float = 100.0, post_close: float = 100.0,
              volume: float = 1000.0) -> list[dict]:
    """A full 78-bar RTH day: flat at pre_close through the 10:25 bar, flat at
    post_close from the 10:30 bar on. The 10:30 bar OPENS at pre_close (the
    tradable decision price) and closes at post_close."""
    bars = []
    for hm in range(prep.RTH_OPEN_HM, prep.RTH_CLOSE_HM, 5):
        price = pre_close if hm < DECISION_HM else post_close
        open_ = pre_close if hm <= DECISION_HM else post_close
        bars.append(_bar(day, hm, open_, max(open_, price), min(open_, price), price, volume))
    return bars


def _series(days: list[date], **kwargs) -> list[dict]:
    bars = []
    for day in days:
        bars.extend(_day_bars(day, **kwargs))
    return bars


def _sessions(count: int = 40, **kwargs):
    return prep._sessions_from_bars(_series(_weekdays(count), **kwargs))


def test_sessions_group_rth_bars_only() -> None:
    day = date(2026, 1, 5)
    bars = _day_bars(day)
    bars.append(_bar(day, 4 * 60, 99.0, 99.0, 99.0, 99.0))   # pre-market: excluded
    bars.append(_bar(day, 18 * 60, 99.0, 99.0, 99.0, 99.0))  # post-market: excluded
    sessions = prep._sessions_from_bars(bars)
    assert len(sessions) == 1
    day_out, session_bars = sessions[0]
    assert day_out == "2026-01-05"
    assert len(session_bars) == 78
    assert session_bars[0][0] == 570 and session_bars[-1][0] == 955


def test_features_never_read_post_decision_bars() -> None:
    """Post-decision prices exploding must not move any feature of THAT session.

    Day K's post-decision tape is set to 10,000 while every other day stays at
    100. The row for day K must be byte-identical to the all-calm series; only
    the NEXT session's priors (gap_open vs day K's close) may register it.
    """
    days = _weekdays(40)
    exploded_day = days[25]
    calm = prep._feature_rows("SPY", _sessions(post_close=100.0), DECISION_HM)

    bars = []
    for day in days:
        bars.extend(_day_bars(day, post_close=10_000.0 if day == exploded_day else 100.0))
    exploded = prep._feature_rows("SPY", prep._sessions_from_bars(bars), DECISION_HM)

    calm_by_day = {row["session"]: row for row in calm}
    exploded_by_day = {row["session"]: row for row in exploded}
    assert set(calm_by_day) == set(exploded_by_day)
    for session, calm_row in calm_by_day.items():
        if session <= exploded_day.isoformat():
            assert calm_row["features"] == exploded_by_day[session]["features"], session
    # the explosion is real and lands exactly where causality allows it:
    # the next session's gap against day K's close
    next_session = days[26].isoformat()
    assert exploded_by_day[next_session]["features"]["gap_open"] != 0.0

    first = calm[0]["features"]
    # flat pre-decision tape: no move yet, decision price sits mid-opening-range
    assert first["session_ret_so_far"] == 0.0
    assert first["vwap_dist"] == 0.0
    assert first["or_ret"] == 0.0
    assert first["or_break"] == 0.5
    assert first["gap_open"] == 0.0
    assert first["prior_ret_1d"] == 0.0
    assert first["vol_ratio_open"] == 1.0
    assert first["day_of_week"] == float(date(2026, 1, 26).weekday())


def test_payoffs_are_decision_to_close_and_close_to_next_close() -> None:
    days = _weekdays(40)
    bars = []
    for i, day in enumerate(days):
        # pre-decision flat at 100; post-decision flat at a per-day close
        close = 100.0 + (i + 1)  # 101, 102, ... deterministic
        bars.extend(_day_bars(day, pre_close=100.0, post_close=close))
    rows = prep._feature_rows("SPY", prep._sessions_from_bars(bars), DECISION_HM)
    assert rows
    first = rows[0]
    day_index = next(i for i, d in enumerate(days) if d.isoformat() == first["session"])
    own_close = 100.0 + (day_index + 1)
    next_close = 100.0 + (day_index + 2)
    assert first["next_open_to_close"] == round(own_close / 100.0 - 1.0, 8)
    assert first["next_close_to_close"] == round(next_close / own_close - 1.0, 8)
    # last session is reserved for the close-to-close payoff, never a row
    assert rows[-1]["session"] == days[-2].isoformat()


def test_or_break_is_clamped_to_unit_interval() -> None:
    day_count = 40
    days = _weekdays(day_count)
    bars = []
    for day in days:
        # opening range 100..101, then the tape rallies hard before 10:30
        day_bars = []
        for hm in range(prep.RTH_OPEN_HM, prep.RTH_CLOSE_HM, 5):
            if hm < prep.OPENING_RANGE_END_HM:
                o, h, l, c = 100.0, 101.0, 100.0, 100.5
            else:
                o, h, l, c = 105.0, 105.0, 105.0, 105.0
            if hm == DECISION_HM:
                o = 105.0  # decision price beyond the opening-range high
            day_bars.append(_bar(day, hm, o, h, l, c))
        bars.extend(day_bars)
    rows = prep._feature_rows("SPY", prep._sessions_from_bars(bars), DECISION_HM)
    assert rows
    for row in rows:
        assert row["features"]["or_break"] == 1.0  # clamped from 4.5
        assert row["features"]["or_ret"] == round(100.5 / 100.0 - 1.0, 8)


def test_prior_features_use_prior_closes() -> None:
    days = _weekdays(40)
    bars = []
    for i, day in enumerate(days):
        level = 100.0 * (1.001 ** i)  # gentle drift: each close 0.1% above the prior
        bars.extend(_day_bars(day, pre_close=level, post_close=level))
    rows = prep._feature_rows("SPY", prep._sessions_from_bars(bars), DECISION_HM)
    first = rows[0]
    index = next(i for i, d in enumerate(days) if d.isoformat() == first["session"])
    closes = [100.0 * (1.001 ** i) for i in range(len(days))]
    assert first["features"]["prior_ret_1d"] == round(closes[index - 1] / closes[index - 2] - 1.0, 8)
    assert first["features"]["prior_ret_5d"] == round(closes[index - 1] / closes[index - 6] - 1.0, 8)
    assert first["features"]["rv_5d"] == 0.0  # constant 0.1% returns


def test_prepare_writes_hash_pinned_snapshot_and_manifest(tmp_path: Path) -> None:
    def fake_fetch(symbol: str, start_utc: datetime, end_utc: datetime) -> list[dict]:
        return _series(_weekdays(40))

    monkey = pytest.MonkeyPatch()
    monkey.setattr(prep, "_resolve_provider", lambda _name: ("massive", fake_fetch))
    monkey.setattr(prep, "_load_staged_spy", lambda *_a, **_k: None)  # force the fetch path
    try:
        manifest = prep.prepare(symbols=("SPY", "QQQ"), start="2026-01-01",
                                end="2026-03-31", out=tmp_path / "snap")
    finally:
        monkey.undo()

    written = (tmp_path / "snap" / "sessions.jsonl").read_bytes()
    assert hashlib.sha256(written).hexdigest() == manifest["data_sha256"]

    # the frozen validator's own loader must accept the recorded hash
    from research.validators.bars_universe import _load_rows

    rows = _load_rows(tmp_path / "snap" / "sessions.jsonl", manifest["data_sha256"])
    assert len(rows) == manifest["row_count"]
    assert {row["symbol"] for row in rows} == {"SPY", "QQQ"}

    for key in ("provider", "symbols", "row_count", "session_count", "first_session",
                "last_session", "data_sha256", "suggested_splits", "prepared_at_utc",
                "decision_hhmm"):
        assert key in manifest, key
    assert manifest["provider"] == "massive"
    assert manifest["decision_hhmm"] == "10:30"
    splits = manifest["suggested_splits"]
    assert set(splits) == {"discovery", "validation", "holdout"}
    assert splits["discovery"][0] == manifest["first_session"]
    assert splits["holdout"][1] == manifest["last_session"]
    assert splits["discovery"][1] < splits["validation"][0]
    assert splits["validation"][1] < splits["holdout"][0]

    # the manifest on disk matches the returned one
    on_disk = json.loads((tmp_path / "snap" / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk == manifest


def test_staged_spy_file_is_used_when_it_covers_the_request(tmp_path: Path) -> None:
    staged_path = tmp_path / "spy_5min.csv.gz"
    days = _weekdays(40)
    import gzip

    with gzip.open(staged_path, "wt", encoding="utf-8", newline="") as fh:
        fh.write("timestamp_utc,open,high,low,close,volume\n")
        for bar in _series(days):
            fh.write(f"{bar['ts'].isoformat()},{bar['open']},{bar['high']},"
                     f"{bar['low']},{bar['close']},{bar['volume']}\n")

    def forbidden_fetch(symbol: str, start_utc: datetime, end_utc: datetime) -> list[dict]:
        raise AssertionError("staged file covers the request; fetch must not run")

    monkey = pytest.MonkeyPatch()
    monkey.setattr(prep, "SPY_STAGED_PATH", staged_path)
    monkey.setattr(prep, "_resolve_provider", lambda _name: ("alpaca", forbidden_fetch))
    try:
        manifest = prep.prepare(symbols=("SPY",), start=days[0].isoformat(),
                                end=days[-1].isoformat(), out=tmp_path / "snap")
    finally:
        monkey.undo()
    assert manifest["row_count"] > 0
    assert manifest["staged_sources"] == {"SPY": str(staged_path)}


def test_decision_hhmm_validation() -> None:
    assert prep._parse_decision_hhmm("10:30") == 630
    for bad in ("09:45", "16:00", "10:37", "not-a-time"):
        with pytest.raises(ValueError):
            prep._parse_decision_hhmm(bad)
