"""The frozen news-gap-fade arithmetic does what the preregistration says."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from research.prereg import news_gap_fade as ngf


def test_news_window_is_prev_close_to_0925_et() -> None:
    start, end = ngf.news_window_utc(date(2024, 3, 8), date(2024, 3, 11))  # DST starts 2024-03-10
    assert start == datetime(2024, 3, 8, 21, 0, tzinfo=timezone.utc)
    assert end == datetime(2024, 3, 11, 13, 25, tzinfo=timezone.utc)


def test_price_uses_bar_ending_at_time_else_latest_earlier_in_window() -> None:
    bars = [(time(9, 30), 10.0), (time(9, 33), 10.3), (time(9, 34), 10.4), (time(9, 35), 99.0)]
    assert ngf.entry_price(bars, "09:35") == (10.4, time(9, 34))
    assert ngf.entry_price(bars[:2], "09:35") == (10.3, time(9, 33))
    assert ngf.entry_price([(time(9, 29), 5.0)], "09:35") is None
    assert ngf.exit_price([(time(15, 29), 1.0)]) is None
    assert ngf.exit_price([(time(15, 40), 2.0), (time(15, 54), 3.0), (time(15, 55), 4.0)]) == (3.0, time(15, 54))


def test_fade_sign_and_market_adjustment() -> None:
    raw, adj = ngf.fade_returns(0.03, 100.0, 98.0, 400.0, 404.0)   # gap up -> short
    assert raw == pytest.approx(0.02)
    assert adj == pytest.approx(0.02 + 0.01)
    raw, adj = ngf.fade_returns(-0.03, 100.0, 101.0, 400.0, 400.0)  # gap down -> long
    assert raw == pytest.approx(0.01) and adj == pytest.approx(0.01)


def test_ticker_map_respects_dates() -> None:
    changes = [{"old_symbol": "FB", "new_symbol": "META", "process_date": "2022-06-09"},
               {"old_symbol": "123456789", "new_symbol": "ZZZ", "process_date": "2020-01-01"}]
    tm = ngf.ticker_map(changes, ["META", "AAPL"])
    early = datetime(2016, 1, 7, 15, 0, tzinfo=timezone.utc)
    late = datetime(2023, 1, 5, 15, 0, tzinfo=timezone.utc)
    assert ngf.tagged_universe_symbols(["FB"], early, {"META", "AAPL"}, tm) == {"META"}
    assert ngf.tagged_universe_symbols(["FB"], late, {"META", "AAPL"}, tm) == set()
    assert ngf.tagged_universe_symbols(["META"], early, {"META", "AAPL"}, tm) == set()
    assert ngf.tagged_universe_symbols(["META", "AAPL"], late, {"META", "AAPL"}, tm) == {"META", "AAPL"}
    assert ngf.qualifies(["A", "B", "C"]) and not ngf.qualifies(["A", "B", "C", "D"]) and not ngf.qualifies([])


def test_daily_portfolio_counts_a_day_once() -> None:
    d = date(2020, 1, 2)
    assert ngf.daily_portfolio([(d, 0.01), (d, 0.03), (date(2020, 1, 3), -0.01)]) == {d: 0.02, date(2020, 1, 3): -0.01}


def _series(mean: float, n: int = 300, noise: float = 0.01, seed: int = 1) -> dict[date, float]:
    import numpy as np
    rng = np.random.default_rng(seed)
    start = date(2016, 1, 4)
    return {start + timedelta(days=i): float(mean + noise * rng.standard_normal()) for i in range(n)}


def test_bootstrap_is_deterministic_and_sensible() -> None:
    s = _series(0.005)
    assert ngf.week_bootstrap_lower(s, "x") == ngf.week_bootstrap_lower(s, "x")
    assert ngf.week_bootstrap_lower(s, "x") > 0
    assert ngf.week_bootstrap_lower(_series(-0.005), "x") < 0
    lb, discarded = ngf.week_bootstrap_difference_lower(_series(0.006, seed=2), _series(0.0, seed=3), "d")
    assert lb > 0 and discarded == 0


def test_discovery_gates_pass_strong_and_flag_plain_fade() -> None:
    strong = ngf.discovery_gates("c", "all", _series(0.006, seed=4), _series(0.0, seed=5), 900)
    assert strong.passed, strong.failed
    plain = ngf.discovery_gates("c", "all", _series(0.006, seed=4), _series(0.006, seed=6), 900)
    assert plain.failed == ["news_beats_control_lb_positive"]
    thin = ngf.discovery_gates("c", "top_tercile", _series(0.0025, seed=7, noise=0.001), {}, 50)
    assert "stress_40bps_positive" in thin.failed
    few = ngf.discovery_gates("c", "all", _series(0.01, n=50), _series(0.0, seed=8), 50)
    assert "min_event_days" in few.failed


def test_part_b_sample_is_stratified_fixed_and_capped() -> None:
    pool = {2016: [f"a{i}" for i in range(300)], 2017: [f"b{i}" for i in range(100)]}
    out = ngf.part_b_sample(pool, cap=100, label="t")
    assert len(out) == 100 and out == ngf.part_b_sample(pool, cap=100, label="t")
    assert sum(k.startswith("a") for k in out) == 75
    assert ngf.part_b_sample(pool, cap=1000) == sorted(pool[2016] + pool[2017])


def test_prompt_has_no_date_and_states_direction() -> None:
    msgs = ngf.part_b_messages("XYZ", ["Beats estimates"], -0.0234)
    user = msgs[1]["content"]
    assert "gapped DOWN 2.3%" in user and "- Beats estimates" in user and "XYZ" in user
    assert not any(str(y) in user for y in range(2015, 2027))


def test_recycled_ticker_history_is_not_inherited() -> None:
    # Barrick: GOLD -> B on 2025-05-09. The GOLD ticker had been A-Mark's (AMRK -> GOLD)
    # only from that same day, so A-Mark's AMRK news must not become Barrick's.
    changes = [{"old_symbol": "GOLD", "new_symbol": "B", "process_date": "2025-05-09"},
               {"old_symbol": "AMRK", "new_symbol": "GOLD", "process_date": "2025-05-09"}]
    tm = ngf.ticker_map(changes, ["B"])
    news_2018 = datetime(2018, 6, 1, 15, 0, tzinfo=timezone.utc)
    assert ngf.tagged_universe_symbols(["AMRK"], news_2018, {"B"}, tm) == set()
    assert ngf.tagged_universe_symbols(["GOLD"], news_2018, {"B"}, tm) == {"B"}
    assert ngf.tagged_universe_symbols(["B"], news_2018, {"B"}, tm) == set()
    # A strictly earlier rename in the chain is followed.
    chain = [{"old_symbol": "ABX", "new_symbol": "GOLD", "process_date": "2019-01-02"}] + changes[:1]
    tm2 = ngf.ticker_map(chain, ["B"])
    assert ngf.tagged_universe_symbols(["ABX"], news_2018, {"B"}, tm2) == {"B"}


def test_same_day_ticker_swap() -> None:
    # 2020-03-02: Ingersoll-Rand IR -> TT and Gardner Denver GDI -> IR.
    changes = [{"old_symbol": "IR", "new_symbol": "TT", "process_date": "2020-03-02"},
               {"old_symbol": "GDI", "new_symbol": "IR", "process_date": "2020-03-02"}]
    tm = ngf.ticker_map(changes, ["IR", "TT"])
    before = datetime(2019, 6, 3, 15, 0, tzinfo=timezone.utc)
    after = datetime(2021, 6, 3, 15, 0, tzinfo=timezone.utc)
    assert ngf.tagged_universe_symbols(["IR"], before, {"IR", "TT"}, tm) == {"TT"}
    assert ngf.tagged_universe_symbols(["GDI"], before, {"IR", "TT"}, tm) == {"IR"}
    assert ngf.tagged_universe_symbols(["IR"], after, {"IR", "TT"}, tm) == {"IR"}
    assert ngf.same_security_duplicates(tm, ["IR", "TT"], date(2025, 6, 30)) == set()


def test_same_security_listed_twice_is_deduplicated() -> None:
    changes = [{"old_symbol": "SATS", "new_symbol": "ECHO", "process_date": "2026-06-24"},
               {"old_symbol": "METCV", "new_symbol": "METC", "process_date": "2023-06-22"},
               {"old_symbol": "METCV", "new_symbol": "METC", "process_date": "2023-06-26"}]
    tm = ngf.ticker_map(changes, ["SATS", "ECHO", "METC"])
    assert ngf.same_security_duplicates(tm, ["SATS", "ECHO", "METC"], date(2025, 6, 30)) == {"SATS"}
    assert tm.since["METC"] == date(2023, 6, 22)


def test_early_closes_are_listed() -> None:
    assert date(2019, 11, 29) in ngf.EARLY_CLOSES and date(2024, 12, 24) in ngf.EARLY_CLOSES
    assert all(d.weekday() < 5 for d in ngf.EARLY_CLOSES)


def test_control_cells_skip_the_comparison_gate() -> None:
    rep = ngf.discovery_gates("c", "all", _series(0.006, seed=4), None, 900)
    assert rep.passed and all(g.name != "news_beats_control_lb_positive" for g in rep.gates)
