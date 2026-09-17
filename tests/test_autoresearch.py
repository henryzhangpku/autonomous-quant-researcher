from __future__ import annotations

from pathlib import Path

import pytest

from research import autoresearch


def test_research_environment_removes_broker_credentials(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("QS_BROKERAGE_AGENT_TOKEN", "secret")
    monkeypatch.setenv("ETORO_API_KEY", "secret")
    monkeypatch.setenv("ALPACA_API_KEY", "secret")
    monkeypatch.setenv("ALPACA_API_SECRET", "secret")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret")
    monkeypatch.setenv("ROBINHOOD_USERNAME", "secret")
    monkeypatch.setenv("WEBULL_REFRESH_TOKEN", "secret")
    monkeypatch.setenv("KALSHI_API_KEY", "secret")
    monkeypatch.setenv("SAFE_VALUE", "kept")

    env = autoresearch._research_environment()

    assert "QS_BROKERAGE_AGENT_TOKEN" not in env
    assert "ETORO_API_KEY" not in env
    assert "ALPACA_API_KEY" not in env
    assert "ALPACA_API_SECRET" not in env
    assert "ALPACA_SECRET_KEY" not in env
    assert "ROBINHOOD_USERNAME" not in env
    assert "WEBULL_REFRESH_TOKEN" not in env
    assert "KALSHI_API_KEY" not in env
    assert env["SAFE_VALUE"] == "kept"
    assert env["AUTOQUANT_RESEARCH_ONLY"] == "1"


def test_config_rejects_broker_execution_command(tmp_path: Path):
    config = tmp_path / "bad.toml"
    config.write_text(
        """
[loop]
name = "bad"
command = ["python", "trader/rh/buyOPT.py", "SPY"]
[evaluator]
pattern = "metric=(?P<value>[0-9]+)"
[sandbox]
mutable_paths = ["research/example.py"]
""",
        encoding="utf-8",
    )

    with pytest.raises(autoresearch.ResearchLoopError, match="broker boundary"):
        autoresearch._load_config(config)


@pytest.mark.parametrize(
    "module",
    [
        "trader.rh.buyOPT",
        "tools.brokerage.qs_brokerage",
        "archive.2026_07_cleanup.trader.rh.buyOPT",
    ],
)
def test_config_rejects_dotted_brokerage_modules(tmp_path: Path, module: str):
    config = tmp_path / "bad-module.toml"
    config.write_text(
        f"""
[loop]
name = "bad-module"
command = ["python", "-m", "{module}"]
[evaluator]
pattern = "metric=(?P<value>[0-9]+)"
[sandbox]
mutable_paths = ["research/example.py"]
""",
        encoding="utf-8",
    )

    with pytest.raises(autoresearch.ResearchLoopError, match="broker boundary"):
        autoresearch._load_config(config)


def test_config_rejects_mutable_path_outside_research(tmp_path: Path):
    config = tmp_path / "bad-path.toml"
    config.write_text(
        """
[loop]
name = "bad-path"
command = ["python", "research/example.py"]
[evaluator]
pattern = "metric=(?P<value>[0-9]+)"
[sandbox]
mutable_paths = ["trader/example.py"]
""",
        encoding="utf-8",
    )

    with pytest.raises(autoresearch.ResearchLoopError, match="under research"):
        autoresearch._load_config(config)


def test_metric_parser_uses_last_named_value():
    assert autoresearch._extract_metric(
        r"score=(?P<value>[0-9.]+)", "score=1.0\nscore=1.5\n"
    ) == 1.5


def test_allowed_path_requires_declared_research_subtree():
    assert autoresearch._is_allowed(
        "research/experiments/model.py", ("research/experiments",)
    )
    assert not autoresearch._is_allowed("trader/order.py", ("research/experiments",))


def test_generic_broker_client_is_not_an_active_module():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "tools" / "brokerage" / "qs_brokerage.py").exists()
