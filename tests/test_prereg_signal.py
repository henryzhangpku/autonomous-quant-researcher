"""The XNYS calendar, the tape reader and the pre-registered GPU signal.

Every tape in this file is a hand-written TEST FIXTURE: invented values in the
published CSV schema, used only to exercise the rules. None is index data and
none is a result.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from research.prereg import gpu_signal, xnys
from research.prereg.gpu_signal import SignalStatus, session_series, signal_at
from research.providers import gpu_index

HEADER = ("index_code,index_date,revision,status,value,provider_count,observation_count,"
          "dispersion,withheld_reason,methodology_version,published_at,superseded_at,"
          "revision_reason,snapshot")


def row(day: date, value: float | None, *, code: str = "GIX-H100", revision: int = 0,
        status: str = "published", version: str = "1.1.0", published: str | None = None,
        superseded: str = "") -> str:
    published = published or f"{day.isoformat()}T21:00:00Z"
    shown = "" if value is None else repr(value)
    return (f"{code},{day.isoformat()},{revision},{status},{shown},10,40,0.3,,{version},"
            f"{published},{superseded},,fixture.jsonl.gz")


def fixture_tape(rows: list[str]) -> tuple[gpu_signal.Fixing, ...]:
    return gpu_index.parse_tape("\n".join([HEADER, *rows]) + "\n")


def daily_rows(start: date, sessions: int, *, growth: float = 1.001, **kwargs: object) -> list[str]:
    days = xnys.sessions(start, start + timedelta(days=sessions * 2))[:sessions]
    return [row(day, 3.0 * growth ** i, **kwargs) for i, day in enumerate(days)]  # type: ignore[arg-type]


# ── calendar ────────────────────────────────────────────────────────────────

def test_xnys_holidays_match_the_published_2026_and_2027_schedules() -> None:
    assert sorted(xnys.holidays(2026)) == [date(2026, m, d) for m, d in (
        (1, 1), (1, 19), (2, 16), (4, 3), (5, 25), (6, 19), (7, 3), (9, 7), (11, 26), (12, 25))]
    assert sorted(xnys.holidays(2027)) == [date(2027, m, d) for m, d in (
        (1, 1), (1, 18), (2, 15), (3, 26), (5, 31), (6, 18), (7, 5), (9, 6), (11, 25), (12, 24))]
    # New Year's Day on a Saturday is not observed on the Friday before.
    assert date(2027, 12, 31) not in xnys.holidays(2027)
    assert date(2025, 1, 9) in xnys.holidays(2025)


def test_xnys_session_count_and_range_guard() -> None:
    assert len(xnys.sessions(date(2025, 1, 1), date(2025, 12, 31))) == 250
    with pytest.raises(xnys.CalendarRangeError):
        xnys.is_session(date(2021, 12, 31))
    assert xnys.last_session_of_each_week(
        xnys.sessions(date(2026, 9, 28), date(2026, 10, 9))) == [date(2026, 10, 2), date(2026, 10, 9)]


# ── tape reader ─────────────────────────────────────────────────────────────

def test_tape_reader_reads_path_and_hashes_bytes(tmp_path: Path) -> None:
    path = tmp_path / "index_values.csv"
    path.write_text("\n".join([HEADER, row(date(2026, 10, 1), 3.5)]) + "\n", encoding="utf-8")
    tape = gpu_index.load_tape(path)
    assert tape.source == str(path) and len(tape.sha256) == 64
    assert tape.fixings[0].value == 3.5 and tape.fixings[0].superseded_at is None


def test_tape_source_resolution_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(gpu_index.ENV_VAR, raising=False)
    assert gpu_index.resolve_source() == str(gpu_index.DEFAULT_TAPE_PATH)
    assert gpu_index.DEFAULT_TAPE_PATH.parts[-3:] == ("gpu-index", "series", "index_values.csv")
    monkeypatch.setenv(gpu_index.ENV_VAR, "https://example.invalid/tape.csv")
    assert gpu_index.resolve_source() == "https://example.invalid/tape.csv"
    assert gpu_index.resolve_source("explicit.csv") == "explicit.csv"


def test_tape_reader_fails_typed(tmp_path: Path) -> None:
    with pytest.raises(gpu_index.TapeUnavailable):
        gpu_index.load_tape(tmp_path / "absent.csv")
    with pytest.raises(gpu_index.TapeUnavailable):
        gpu_index.load_tape("http://example.invalid/plain-http.csv")
    with pytest.raises(gpu_index.TapeFormatError):
        gpu_index.parse_tape("index_code,index_date\nGIX-H100,2026-10-01\n")
    with pytest.raises(gpu_index.TapeFormatError):
        gpu_index.parse_tape("\n".join([HEADER, row(date(2026, 10, 1), None)]))


# ── signal rules ────────────────────────────────────────────────────────────

def test_signal_is_flagged_insufficient_until_63_sessions_back_exist() -> None:
    start = date(2026, 9, 17)
    tape = fixture_tape(daily_rows(start, 63))
    series = session_series(tape, "GIX-H100")
    reading = signal_at(series, "H100", series[-1].day)
    assert reading.status is SignalStatus.INSUFFICIENT_HISTORY
    assert reading.message == "insufficient history for the 3-month signal"
    assert reading.history_sessions == 63 and reading.required_sessions == 64
    assert reading.value is None
    assert gpu_signal.earliest_live_session(series) == xnys.sessions(start, date(2027, 1, 31))[63]


def test_signal_goes_live_at_64_sessions_with_the_log_change() -> None:
    tape = fixture_tape(daily_rows(date(2026, 9, 17), 64, growth=1.002))
    series = session_series(tape, "GIX-H100")
    reading = signal_at(series, "H100", series[-1].day)
    assert reading.status is SignalStatus.LIVE
    assert reading.anchor == series[0].day and reading.window_coverage == 1.0
    assert reading.value == pytest.approx(63 * math.log(1.002))


def test_a_methodology_change_restarts_the_history_clock() -> None:
    old = daily_rows(date(2026, 6, 1), 40, version="1.0.0")
    new_start = xnys.sessions(date(2026, 6, 1), date(2026, 12, 31))[40]
    new = daily_rows(new_start, 30, version="1.1.0")
    series = session_series(fixture_tape(old + new), "GIX-H100")
    reading = signal_at(series, "H100", series[-1].day)
    assert reading.status is SignalStatus.INSUFFICIENT_HISTORY
    assert reading.methodology_version == "1.1.0" and reading.history_sessions == 30


def test_withheld_weekend_and_superseded_rows_never_enter_the_series() -> None:
    friday, saturday, monday = date(2026, 10, 2), date(2026, 10, 3), date(2026, 10, 5)
    tape = fixture_tape([
        row(friday, 3.0, revision=0, superseded="2026-10-03T12:00:00Z"),
        row(friday, 3.3, revision=1, published="2026-10-03T12:00:00Z"),
        row(saturday, 9.9),
        row(monday, None, status="withheld"),
    ])
    assert [(item.day, item.value) for item in session_series(tape, "GIX-H100")] == [(friday, 3.3)]
    # Point in time: before the correction was published, revision 0 was live.
    before = datetime(2026, 10, 3, 6, 0, tzinfo=timezone.utc)
    assert [item.value for item in session_series(tape, "GIX-H100", before)] == [3.0]


def test_sparse_window_and_stale_tape_do_not_sign_a_signal() -> None:
    rows = daily_rows(date(2026, 1, 5), 70)
    sparse = [line for i, line in enumerate(rows) if i % 5 != 0 or i in (6, 69)]
    series = session_series(fixture_tape(sparse), "GIX-H100")
    assert signal_at(series, "H100", series[-1].day).status is SignalStatus.INSUFFICIENT_COVERAGE
    full = session_series(fixture_tape(rows), "GIX-H100")
    later = xnys.sessions(full[-1].day, full[-1].day + timedelta(days=14))[5]
    assert signal_at(full, "H100", later).status is SignalStatus.STALE
    assert signal_at([], "H100", later).status is SignalStatus.NO_DATA
