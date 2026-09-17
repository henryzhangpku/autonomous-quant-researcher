from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.experiments.prepare_bars_universe import (
    FEATURE_WARMUP_SESSIONS,
    _feature_rows,
    _suggested_splits,
)
from research.validators.bars_universe import (
    evaluate_candidate,
    load_spec,
    preflight_candidate,
)


def _candidate(path: Path, body: str = "return 1") -> Path:
    path.write_text(
        "LABEL = 'bars test'\n"
        "def signal(symbol, features):\n    " + body + "\n",
        encoding="utf-8",
    )
    return path


def _spec(path: Path, **overrides) -> Path:
    data = {
        "name": "bars-test",
        "universe": ["SPY", "QQQ", "IWM"],
        "splits": {
            "discovery": ["2026-03-01", "2026-03-31"],
            "validation": ["2026-04-01", "2026-04-15"],
            "holdout": ["2026-04-16", "2026-04-30"],
        },
        "min_trades": {"discovery": 30, "validation": 10, "holdout": 10},
        "min_sessions": {"discovery": 10, "validation": 4, "holdout": 4},
        "min_symbols": 3,
        "max_concentration": 0.5,
        "cost_bps": 5.0,
        "target_sharpe": 0.75,
        "payoff": "next_open_to_close",
    }
    data.update(overrides)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _rows() -> list[dict]:
    symbols = ("SPY", "QQQ", "IWM")
    return [
        {
            "symbol": symbol, "session": f"2026-03-{day:02d}",
            "features": {"ret_1d": 0.001, "rv_5d": 0.01},
            "next_open_to_close": 0.002, "next_close_to_close": 0.0025,
        }
        for day in range(1, 25) for symbol in symbols
    ]


def test_preflight_enforces_contract(tmp_path: Path) -> None:
    spec = load_spec(_spec(tmp_path / "spec.json"))
    result = preflight_candidate(_candidate(tmp_path / "candidate.py"), spec)
    assert result["status"] == "preflight_ok"
    with pytest.raises(TypeError):
        preflight_candidate(_candidate(tmp_path / "bad.py", "return 2"), spec)


def test_positive_fixture_passes_and_reports_both_means(tmp_path: Path) -> None:
    spec = load_spec(_spec(tmp_path / "spec.json"))
    result = evaluate_candidate(_candidate(tmp_path / "candidate.py"), spec, _rows(), "discovery")
    assert result["status"] == "passed"
    assert result["trades"] == 72
    assert result["net_portfolio_mean_bps"] == 15.0
    assert result["net_trade_mean_bps"] == 15.0
    assert result["payoff"] == "next_open_to_close"


def test_short_signal_flips_payoff_sign(tmp_path: Path) -> None:
    spec = load_spec(_spec(tmp_path / "spec.json"))
    result = evaluate_candidate(_candidate(tmp_path / "candidate.py", "return -1"), spec, _rows(), "discovery")
    assert result["status"] == "rejected"
    assert result["net_portfolio_mean_bps"] == -25.0


def test_close_to_close_payoff_mode(tmp_path: Path) -> None:
    spec = load_spec(_spec(tmp_path / "spec.json", payoff="next_close_to_close"))
    result = evaluate_candidate(_candidate(tmp_path / "candidate.py"), spec, _rows(), "discovery")
    assert result["net_portfolio_mean_bps"] == 20.0


def test_single_symbol_universe_relaxes_breadth_not_performance(tmp_path: Path) -> None:
    spec = load_spec(_spec(
        tmp_path / "spec.json", universe=["SPY"], min_symbols=1,
        min_trades={"discovery": 15, "validation": 5, "holdout": 5},
    ))
    rows = [row for row in _rows() if row["symbol"] == "SPY"]
    result = evaluate_candidate(_candidate(tmp_path / "candidate.py"), spec, rows, "discovery")
    assert result["gates"]["symbol_concentration"] is True
    assert result["gates"]["positive_symbol_fraction"] is True
    assert result["status"] == "passed"
    losing = [dict(row, next_open_to_close=-0.002) for row in rows]
    assert evaluate_candidate(_candidate(tmp_path / "candidate.py"), spec, losing, "discovery")["status"] == "rejected"


def test_spec_rejects_overlapping_or_missing_splits(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="chronological"):
        load_spec(_spec(tmp_path / "a.json", splits={
            "discovery": ["2026-03-01", "2026-04-05"],
            "validation": ["2026-04-01", "2026-04-15"],
            "holdout": ["2026-04-16", "2026-04-30"],
        }))
    with pytest.raises(ValueError, match="exactly"):
        load_spec(_spec(tmp_path / "b.json", splits={
            "discovery": ["2026-03-01", "2026-03-31"],
            "validation": ["2026-04-01", "2026-04-15"],
        }))


def test_spec_hash_is_enforced(tmp_path: Path) -> None:
    path = _spec(tmp_path / "spec.json")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_spec(path, "0" * 64)


def _bars(count: int, *, start_close: float = 100.0) -> list[dict]:
    bars = []
    close = start_close
    for day in range(count):
        close *= 1.001
        bars.append({
            "session": f"2026-{1 + day // 28:02d}-{1 + day % 28:02d}",
            "open": close * 0.999, "high": close * 1.002,
            "low": close * 0.997, "close": close, "volume": 1_000_000.0,
        })
    return bars


def test_feature_rows_are_leakage_free_and_carry_next_session_payoff() -> None:
    bars = _bars(100)
    rows = _feature_rows("SPY", bars)
    # warmup consumes 21 sessions and the final session has no next bar
    # One row per decidable session: warmup consumed at the front, and the
    # last bar reserved as the next-session payoff.
    assert len(rows) == 100 - FEATURE_WARMUP_SESSIONS - 1
    first = rows[0]
    index = next(i for i, bar in enumerate(bars) if bar["session"] == first["session"])
    next_bar = bars[index + 1]
    assert first["next_open_to_close"] == round(next_bar["close"] / next_bar["open"] - 1.0, 8)
    assert first["features"]["ret_1d"] == round(bars[index]["close"] / bars[index - 1]["close"] - 1.0, 8)
    # no feature may reference the payoff session — with one documented
    # exception: event_next_session reads only the next session's DATE against
    # a calendar published months ahead, never its bars, so it is not leakage.
    leaked = json.dumps({k: v for k, v in first["features"].items()
                         if k != "event_next_session"})
    assert "next" not in leaked


def test_suggested_splits_are_chronological_and_exhaustive() -> None:
    sessions = [f"2026-03-{day:02d}" for day in range(1, 29)]
    splits = _suggested_splits(sessions)
    assert splits["discovery"][0] == sessions[0]
    assert splits["holdout"][1] == sessions[-1]
    assert splits["discovery"][1] < splits["validation"][0]
    assert splits["validation"][1] < splits["holdout"][0]


def test_prepared_snapshot_hash_matches_the_file_on_disk(tmp_path: Path) -> None:
    """Guards the Windows newline trap: hash the bytes actually written."""

    import hashlib

    from research.experiments import prepare_bars_universe as prep
    from research.validators.bars_universe import _load_rows

    def fake_fetch(_provider, *, asset_class, symbols, start_utc, end_utc):
        return {symbol: _bars(100) for symbol in symbols}

    monkey = pytest.MonkeyPatch()
    monkey.setattr(prep, "_resolve_provider", lambda _name: ("massive", object(), fake_fetch))
    try:
        manifest = prep.prepare(
            symbols=("SPY",), start="2026-01-01", end="2026-03-01", out=tmp_path / "snap",
        )
    finally:
        monkey.undo()

    written = (tmp_path / "snap" / "sessions.jsonl").read_bytes()
    assert hashlib.sha256(written).hexdigest() == manifest["data_sha256"]
    # the validator's own loader must accept the recorded hash
    assert _load_rows(tmp_path / "snap" / "sessions.jsonl", manifest["data_sha256"])


def test_unknown_feature_names_the_available_ones(tmp_path: Path) -> None:
    """A bare KeyError('close') tells the proposer nothing and burns a trial."""

    spec = load_spec(_spec(tmp_path / "spec.json"))
    candidate = _candidate(tmp_path / "candidate.py", "return 1 if features['close'] > 0 else 0")
    # static preflight rejects it first with the full roster
    with pytest.raises(TypeError, match="unknown features 'close'"):
        preflight_candidate(candidate, spec)
    # and if one ever reaches evaluation anyway, the lookup itself explains
    with pytest.raises(KeyError, match="available features are: ret_1d, rv_5d"):
        evaluate_candidate(candidate, spec, _rows(), "discovery")


def test_preflight_catches_unknown_features_hidden_behind_short_circuits(tmp_path: Path) -> None:
    """The live failure: ret_1d < -0.01 short-circuited, hiding features['close']."""

    spec = load_spec(_spec(tmp_path / "spec.json"))
    hidden = _candidate(
        tmp_path / "hidden.py",
        "return 1 if features['ret_1d'] < -0.01 and features['close'] == features['low'] else 0",
    )
    with pytest.raises(TypeError, match="unknown features 'close', 'low'"):
        preflight_candidate(hidden, spec)
    via_get = _candidate(tmp_path / "get.py", "return 1 if features.get('vwap', 0) > 0 else 0")
    with pytest.raises(TypeError, match="unknown features 'vwap'"):
        preflight_candidate(via_get, spec)


def test_relative_payoff_prices_out_market_drift(tmp_path: Path) -> None:
    """The bars campaign 1 defect: a long-drift window passes rules on beta."""

    spec = load_spec(_spec(tmp_path / "spec.json", payoff="next_open_to_close_rel"))
    # Every symbol returns +2% and so does the benchmark: pure drift, zero alpha.
    rows = [dict(r, next_open_to_close_rel=0.0) for r in _rows()]
    result = evaluate_candidate(_candidate(tmp_path / "c.py"), spec, rows, "discovery")
    assert result["net_portfolio_mean_bps"] == -5.0  # cost only, no drift credit
    assert result["status"] == "rejected"
    # A rule that genuinely beats the benchmark still passes.
    beating = [dict(r, next_open_to_close_rel=0.002) for r in _rows()]
    assert evaluate_candidate(_candidate(tmp_path / "c.py"), spec, beating, "discovery")["status"] == "passed"


def test_missing_relative_payoff_fails_loudly(tmp_path: Path) -> None:
    spec = load_spec(_spec(tmp_path / "spec.json", payoff="next_close_to_close_rel"))
    with pytest.raises(ValueError, match="rebuild the snapshot with a benchmark"):
        evaluate_candidate(_candidate(tmp_path / "c.py"), spec, _rows(), "discovery")


def test_correlated_firing_diagnostics_are_reported(tmp_path: Path) -> None:
    spec = load_spec(_spec(tmp_path / "spec.json"))
    result = evaluate_candidate(_candidate(tmp_path / "c.py"), spec, _rows(), "discovery")
    # 3 symbols fire every session, so the trade mean counts each session 3x.
    assert result["trades_per_session"] == 3.0
    assert "trade_minus_session_bps" in result


def test_benchmark_relative_payoffs_are_attached_at_prepare(tmp_path: Path) -> None:
    from research.experiments import prepare_bars_universe as prep

    def fake_fetch(_provider, *, asset_class, symbols, start_utc, end_utc):
        # SPY rises 0.1%/session; QQQ rises twice as fast.
        return {"SPY": _bars(100), "QQQ": _bars(100, start_close=200.0)}

    monkey = pytest.MonkeyPatch()
    monkey.setattr(prep, "_resolve_provider", lambda _n: ("massive", object(), fake_fetch))
    try:
        manifest = prep.prepare(symbols=("SPY", "QQQ"), start="2026-01-01",
                                end="2026-03-01", out=tmp_path / "snap")
    finally:
        monkey.undo()
    assert manifest["benchmark"] == "SPY"
    rows = [json.loads(line) for line in
            (tmp_path / "snap" / "sessions.jsonl").read_text(encoding="utf-8").splitlines()]
    spy = [r for r in rows if r["symbol"] == "SPY"]
    assert all(r["next_open_to_close_rel"] == 0.0 for r in spy), "benchmark is flat against itself"
    for row in rows:
        assert "next_open_to_close_rel" in row and "next_close_to_close_rel" in row


def test_feature_spec_matches_what_prepare_actually_emits() -> None:
    """The feature contract has three consumers and they must agree exactly.

    `prepare_bars_universe` computes the features, the validator whitelists
    them (a candidate is statically rejected for naming anything outside the
    set), and `idea_compiler` lists them in the frozen mission so the model
    knows what it may read. A name present in one and missing from another
    either rejects a valid candidate or advertises a feature that is not
    there — and the failure lands mid-campaign, after a trial is spent.

    So: prove the emitted keys ARE the contract, on real computed rows.
    """
    from research.experiments.bars_feature_spec import FEATURE_FIXTURE, FEATURE_NAMES
    from research.experiments.prepare_bars_universe import (
        _add_cross_sectional_ranks,
        _feature_rows,
    )
    from research.lab.idea_compiler import FEATURES
    from research.validators.bars_universe import PREFLIGHT_FIXTURE

    # Long enough to clear the 60-session warmup, using this file's own
    # bar generator so the fixture cannot drift from the others.
    rows = _feature_rows("SPY", _bars(100)) + _feature_rows("QQQ", _bars(100, start_close=200.0))
    assert rows, "the fixture must produce decidable sessions"
    _add_cross_sectional_ranks(rows)

    emitted = set(rows[0]["features"])
    assert emitted == set(FEATURE_NAMES), {
        "missing_from_spec": sorted(emitted - set(FEATURE_NAMES)),
        "declared_but_not_emitted": sorted(set(FEATURE_NAMES) - emitted),
    }
    assert set(PREFLIGHT_FIXTURE) == emitted, "validator whitelist drifted"
    assert set(FEATURES) == emitted, "mission feature list drifted"

    # Every value must be a finite number: a NaN reaching a candidate poisons
    # every trial that touches it and reads as an unexplained loss.
    import math

    for row in rows:
        for name, value in row["features"].items():
            assert isinstance(value, (int, float)) and math.isfinite(value), (name, value)
            if name.startswith("xs_rank_"):
                assert 0.0 <= value <= 1.0, (name, value)

    # The fixture is a smoke-test vector, so it must cover the real set.
    assert set(FEATURE_FIXTURE) == emitted


def test_preflight_accepts_dataset_native_features_when_rows_given(tmp_path: Path) -> None:
    """Windowed snapshots are their own feature contract.

    Crypto overnight/weekend sessions and 0DTE spread grids carry native
    features the daily-bars fixture never names; rejecting those names made
    every windowed mission unlaunchable (2026-08-16). With the frozen rows at
    hand, the dataset — hash-pinned — is the contract.
    """
    spec = load_spec(_spec(tmp_path / "spec.json"))
    rows = [dict(r, features={"credit_frac": 0.2, "day_of_week": 4.0}) for r in _rows()]

    native = _candidate(tmp_path / "native.py", "return 1 if features['credit_frac'] > 0.1 else 0")
    with pytest.raises(TypeError, match="unknown features 'credit_frac'"):
        preflight_candidate(native, spec)
    assert preflight_candidate(native, spec, rows)["status"] == "preflight_ok"

    # a daily-fixture name the dataset does not carry is still rejected
    alien = _candidate(tmp_path / "alien.py", "return 1 if features['rv_5d'] > 0 else 0")
    with pytest.raises(TypeError, match="unknown features 'rv_5d'"):
        preflight_candidate(alien, spec, rows)


def test_crypto_benchmark_fetched_via_equity_path_and_closed_sessions_zero_filled(tmp_path: Path) -> None:
    """Crypto passes must not silently drop the drift benchmark.

    The live failure: btc-streak-momentum-v2's accepted "RSI" candidate was
    always-long BTC through a bull window (2026-08-16). With no benchmark on
    crypto snapshots, beta passed discovery gates as alpha.
    """
    from datetime import date, timedelta

    from research.experiments import prepare_bars_universe as prep

    def dated_bars(days: list[date], start_close: float) -> list[dict]:
        close = start_close
        bars = []
        for session in days:
            close *= 1.001
            bars.append({
                "session": session.isoformat(),
                "open": close * 0.999, "high": close * 1.002,
                "low": close * 0.997, "close": close, "volume": 1_000_000.0,
            })
        return bars

    all_days = [date(2026, 1, 1) + timedelta(days=i) for i in range(100)]
    weekdays = [day for day in all_days if day.weekday() < 5]

    def fake_fetch(_provider, *, asset_class, symbols, start_utc, end_utc):
        if asset_class == "crypto":
            return {symbol: dated_bars(all_days, 50.0) for symbol in symbols}
        return {symbol: dated_bars(weekdays, 200.0) for symbol in symbols}

    monkey = pytest.MonkeyPatch()
    monkey.setattr(prep, "_resolve_provider", lambda _n: ("massive", object(), fake_fetch))
    try:
        manifest = prep.prepare(
            symbols=("BTC/USD",), start="2026-01-01", end="2026-03-31",
            out=tmp_path / "snap", asset_class="crypto", benchmark="IBIT",
        )
    finally:
        monkey.undo()

    assert manifest["benchmark"] == "IBIT"
    rows = [json.loads(line) for line in
            (tmp_path / "snap" / "sessions.jsonl").read_text(encoding="utf-8").splitlines()]
    btc = [row for row in rows if row["symbol"] == "BTC/USD"]
    assert btc, "crypto rows present"
    weekend_rows = [row for row in btc if date.fromisoformat(row["session"]).weekday() >= 5]
    assert weekend_rows, "crypto snapshot must include weekend sessions"
    for row in btc:
        assert "next_close_to_close_rel" in row
    for row in weekend_rows:
        # benchmark market closed: its return is genuinely zero, rel == raw
        assert row["next_close_to_close_rel"] == row["next_close_to_close"]
    weekday_rows = [row for row in btc if date.fromisoformat(row["session"]).weekday() < 5]
    assert any(
        row["next_close_to_close_rel"] != row["next_close_to_close"] for row in weekday_rows
    ), "weekday rows must be differenced against the benchmark"


def test_preflight_rejects_zero_fire_candidate_with_feature_ranges(tmp_path: Path) -> None:
    # A rule whose condition lies outside the data's feature ranges never
    # fires, teaches nothing, and used to burn a full trial per attempt
    # (2026-08-24: 20 of 24 IBIT 0DTE candidates). Preflight now rejects it
    # and reports the discovery-split feature ranges so the retry can aim.
    spec = load_spec(_spec(tmp_path / "spec.json"))
    candidate = _candidate(
        tmp_path / "never.py",
        "return 1 if features['ret_1d'] > 999.0 else 0",
    )
    with pytest.raises(TypeError) as excinfo:
        preflight_candidate(candidate, spec, _rows())
    message = str(excinfo.value)
    assert "never fires on the discovery split" in message
    assert "ret_1d" in message and "p50=" in message


def test_preflight_reports_discovery_fires_for_a_firing_candidate(tmp_path: Path) -> None:
    spec = load_spec(_spec(tmp_path / "spec.json"))
    candidate = _candidate(tmp_path / "fires.py", "return 1")
    result = preflight_candidate(candidate, spec, _rows())
    assert result["status"] == "preflight_ok"
    assert result["discovery_fires"] > 0


def test_preflight_without_rows_skips_the_zero_fire_check(tmp_path: Path) -> None:
    # The daily-bars fixture path has no dataset rows to measure against.
    spec = load_spec(_spec(tmp_path / "spec.json"))
    candidate = _candidate(
        tmp_path / "never.py",
        "return 1 if features['ret_1d'] > 999.0 else 0",
    )
    result = preflight_candidate(candidate, spec)
    assert result["status"] == "preflight_ok"
    assert "discovery_fires" not in result
