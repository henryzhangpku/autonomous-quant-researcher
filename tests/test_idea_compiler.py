from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from research.lab.idea_compiler import IdeaCompileError, compile_idea
from research.lab.mission import load_mission_spec
from research.validators.bars_universe import load_spec

TODAY = date(2026, 8, 12)


def _fake_prepare(*, symbols, start, end, out, asset_class):
    out.mkdir(parents=True, exist_ok=True)
    rows = [
        {"session": f"2026-0{month}-{day:02d}", "symbol": symbol,
         "features": {"ret_1d": 0.001}, "next_open_to_close": 0.001,
         "next_close_to_close": 0.001}
        for month in (1, 2, 3) for day in range(1, 21) for symbol in symbols
    ]
    payload = "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n"
    (out / "sessions.jsonl").write_text(payload, encoding="utf-8")
    import hashlib
    sessions = sorted({row["session"] for row in rows})
    return {
        "symbols": list(symbols), "asset_class": asset_class,
        "requested_start": start, "requested_end": end,
        "session_count": len(sessions), "row_count": len(rows),
        "data_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "suggested_splits": {
            "discovery": [sessions[0], sessions[35]],
            "validation": [sessions[36], sessions[47]],
            "holdout": [sessions[48], sessions[-1]],
        },
    }


def _compile(idea: str, tmp_path: Path, **kwargs):
    return compile_idea(
        idea,
        mission_root=tmp_path / "missions",
        data_root=tmp_path / "data",
        today=TODAY,
        prepare=_fake_prepare,
        **kwargs,
    )


def test_compiles_ticker_idea_into_loadable_mission(tmp_path: Path) -> None:
    compiled = _compile("Buy SPY and QQQ after a down day of more than 1 percent", tmp_path)
    assert compiled.universe == ("SPY", "QQQ")
    assert compiled.asset_class == "equity"
    assert compiled.payoff == "next_open_to_close"
    # the emitted pair must load through the real mission loader
    spec = load_mission_spec(compiled.mission_path, compiled.policy_path)
    assert spec.policy.metric_name == "robust_portfolio_score"
    assert spec.policy.target_metric == 0.75
    assert spec.policy.min_attempts == 12
    assert "{candidate}" in " ".join(spec.policy.command)
    # and the emitted spec must load through the real validator
    validator_spec = load_spec(compiled.spec_path)
    assert validator_spec.universe == ("SPY", "QQQ")
    assert validator_spec.max_concentration == max(0.35, 0.75)
    # the human's words survive verbatim in the mission
    assert "down day of more than 1 percent" in compiled.mission_path.read_text(encoding="utf-8")


def test_alias_and_overnight_parsing(tmp_path: Path) -> None:
    compiled = _compile("Hold the mag 7 overnight when they close near the high", tmp_path)
    assert compiled.universe == ("AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA")
    assert compiled.payoff == "next_close_to_close"


def test_crypto_idea_maps_to_crypto_pairs(tmp_path: Path) -> None:
    compiled = _compile("Buy bitcoin dips of 3 percent over the last 12 months", tmp_path)
    assert compiled.universe == ("BTC/USD",)
    assert compiled.asset_class == "crypto"
    assert compiled.start == "2025-08-16"


def test_date_range_parsing(tmp_path: Path) -> None:
    assert _compile("SPY momentum since 2023", tmp_path).start == "2023-01-01"
    assert _compile("SPY momentum over the last 3 years", tmp_path).start == "2023-08-12"
    assert _compile("SPY plain momentum idea here", tmp_path).start == "2024-08-11"


def test_refuses_unsupported_surfaces(tmp_path: Path) -> None:
    with pytest.raises(IdeaCompileError, match="options-flow"):
        _compile("Trade NVDA on options-flow sweeps", tmp_path)
    with pytest.raises(IdeaCompileError, match="option structures"):
        _compile("Sell SPY 0DTE credit spreads at open", tmp_path)
    with pytest.raises(IdeaCompileError, match="intraday"):
        _compile("Buy QQQ at 10:30 intraday and exit by noon", tmp_path)
    with pytest.raises(IdeaCompileError, match="No tradeable symbols"):
        _compile("Buy the dip when everything looks bad", tmp_path)
    with pytest.raises(IdeaCompileError, match="mixes equities and crypto"):
        _compile("Rotate between SPY and bitcoin weekly", tmp_path)
    # Gamma ideas must refuse OUT LOUD: before this pattern existed,
    # "long SPY under the call wall" compiled into a bars-only mission with
    # no gamma data in it at all — a silent approximation (2026-08-15).
    with pytest.raises(IdeaCompileError, match="gamma-exposure"):
        _compile("Go long SPY when price holds under the call wall", tmp_path)
    with pytest.raises(IdeaCompileError, match="gamma-exposure"):
        _compile("Fade SPY moves when GEX is deeply positive", tmp_path)


def test_macro_event_vocabulary_compiles_but_earnings_stays_refused(
    tmp_path: Path,
) -> None:
    """The macro calendar is staged now, so FOMC/CPI/NFP ideas are expressible
    (event_today/event_next_session); earnings and news still have no data."""
    compiled = _compile("Does SPY rally the day after an FOMC day?", tmp_path)
    assert compiled.universe == ("SPY",)
    mission = compiled.mission_path.read_text(encoding="utf-8")
    assert "event_today" in mission and "event_next_session" in mission
    assert "known in advance, not a lookahead" in mission

    with pytest.raises(IdeaCompileError, match="event_today/event_next_session"):
        _compile("Does AAPL pop on earnings?", tmp_path)
    with pytest.raises(IdeaCompileError, match="news/sentiment"):
        _compile("Buy TSLA when the news sentiment turns positive", tmp_path)


def test_single_symbol_gates_relax_breadth(tmp_path: Path) -> None:
    compiled = _compile("Fade $IWM gaps up bigger than 1 percent", tmp_path)
    assert compiled.universe == ("IWM",)
    validator_spec = load_spec(compiled.spec_path)
    assert validator_spec.min_symbols == 1
    assert validator_spec.max_concentration == 1.0
    assert validator_spec.min_trades["discovery"] == 40


def test_mission_example_is_unrelated_to_the_idea_and_warns_against_copying(
    tmp_path: Path,
) -> None:
    """A weak local proposer copies the example verbatim, then dies on novelty.

    The first live run did exactly that: the example implemented the stated
    idea, so trial 1 was the example and trials 2-4 were rejected as identical.
    """
    compiled = _compile(
        "Buy SPY and QQQ when they fall more than 1 percent and close near the low",
        tmp_path,
    )
    mission = compiled.mission_path.read_text(encoding="utf-8")
    assert "do not submit it" in mission
    assert "materially different rule" in mission
    # the example must not hand back the idea's own mechanism
    example = mission.split("```python", 1)[1]
    assert "range_pos" not in example
    assert "ret_1d" not in example
