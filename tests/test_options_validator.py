from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from research.backtest import Session
from research.lab import load_mission_spec
from research.validators.options import evaluate_candidate, main, preflight_candidate


def _sessions(n: int = 90) -> list[Session]:
    sessions = []
    day = date(2022, 1, 3)
    for index in range(n):
        price = 500.0 + (2.0 if index % 2 else -2.0)
        bars = tuple(
            (9 * 60 + 30 + 5 * offset, price, price + 0.2, price - 0.2, price)
            for offset in range(78)
        )
        sessions.append(Session(day=day.isoformat(), bars=bars))
        day += timedelta(days=1)
    return sessions


def test_fixed_options_validator_runs_generated_contract(tmp_path: Path, capsys):
    candidate = tmp_path / "candidate.py"
    candidate.write_text(
        "\n".join(
            [
                "from research.backtest import call_debit_spread",
                "ENTRY_HM = (12, 30)",
                "LABEL = 'generated candidate'",
                "def signal(session, entry_minute):",
                "    return True",
                "def structure(session, entry_price):",
                "    return call_debit_spread(entry_price, width=2.0)",
            ]
        ),
        encoding="utf-8",
    )

    payload = evaluate_candidate(candidate, _sessions(), split="validation")

    assert payload["status"] == "ok"
    assert payload["signal_trades"] >= 50
    assert payload["signal_edge_pp"] == payload["control_edge_pp"]
    assert payload["score"] == 0.0

    preflight = preflight_candidate(candidate)
    assert preflight == {"status": "preflight_ok", "label": "generated candidate"}
    assert main(["--candidate", str(candidate), "--preflight-only"]) == 0
    assert '"status": "preflight_ok"' in capsys.readouterr().out


@pytest.mark.parametrize(
    "signal_body, structure_body, error",
    [
        (
            "return session.price_at(entry_minute - 5) < session.open",
            "return call_debit_spread(entry_price, width=2.0)",
            "candidate signal failed preflight",
        ),
        (
            "return session.first_touch(session.open + 1, after_minute=entry_minute) is not None",
            "return call_debit_spread(entry_price, width=2.0)",
            "unexpected keyword argument 'after_minute'",
        ),
        (
            "return True",
            "return call_debit_spread(session + entry_price, width=2.0)",
            "candidate structure failed preflight",
        ),
        (
            "return session.close > session.open",
            "return call_debit_spread(entry_price, width=2.0)",
            "has no attribute 'close'",
        ),
    ],
)
def test_candidate_preflight_rejects_runtime_contract_errors(
    tmp_path: Path, signal_body: str, structure_body: str, error: str
) -> None:
    candidate = tmp_path / "candidate.py"
    candidate.write_text(
        "\n".join(
            [
                "from research.backtest import call_debit_spread",
                "ENTRY_HM = (12, 30)",
                "LABEL = 'broken generated candidate'",
                "def signal(session, entry_minute):",
                f"    {signal_body}",
                "def structure(session, entry_price):",
                f"    {structure_body}",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(TypeError, match=error):
        preflight_candidate(candidate)


def test_included_nlp_mission_and_policy_load_as_frozen_contract():
    root = Path(__file__).resolve().parents[1]
    mission_dir = root / "research" / "missions" / "spy-0dte"

    spec = load_mission_spec(mission_dir / "mission.md", mission_dir / "policy.toml")

    assert spec.name == "spy-0dte-autoresearch"
    assert spec.policy.evaluator_version == "spy-options-causal-v2"
    assert spec.policy.metric_name == "excess_edge_pp"
    assert spec.policy.min_attempts == 5
    assert "research.validators.options" in spec.policy.command
