"""One switch between a local GPU model and a remote LLM, with a stated reason.

Autonomous Quant Researcher assumed a laptop running llama.cpp on an NVIDIA GPU. Moved to a box
with neither, every mission failed at the proposer with a 401 against
api.openai.com while the docs still described a local model. The runtime was
implicit, so the failure was mystifying instead of obvious.

These tests pin the switch positions and, just as importantly, that a fallback
is never silent.
"""

from __future__ import annotations

import pytest

from research.lab.endpoints import ProposerEndpoint, resolve_endpoint

LAB_VARS = (
    "AUTOQUANT_LAB_PROVIDER",
    "AUTOQUANT_LAB_BASE_URL",
    "AUTOQUANT_LAB_MODEL",
    "AUTOQUANT_LAB_API_KEY",
    "AUTOQUANT_LOCAL_BASE_URL",
    "AUTOQUANT_LOCAL_MODEL",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    # pre-rename names, cleared so a developer's shell cannot leak into a test
    "AUTOQUANT_DEEPSEEK_BASE_URL",
    "AUTOQUANT_DEEPSEEK_MODEL",
    "AUTOQUANT_DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
    "DEEPSEEK_API_KEY",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in LAB_VARS:
        monkeypatch.delenv(name, raising=False)


def _up(_base_url: str, timeout: float = 0.0) -> bool:
    return True


def _down(_base_url: str, timeout: float = 0.0) -> bool:
    return False


def test_auto_prefers_a_local_server_when_one_is_listening() -> None:
    endpoint = resolve_endpoint(probe=_up)
    assert endpoint.provider == "local"
    assert "127.0.0.1" in endpoint.base_url


def test_auto_uses_remote_when_no_local_server_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    # The box this runs on today: no NVIDIA GPU, no llama-server.
    monkeypatch.setenv("LLM_MODEL", "remote-model")
    endpoint = resolve_endpoint(probe=_down)
    assert endpoint.provider == "remote"
    assert endpoint.model == "remote-model"


def test_remote_has_no_built_in_endpoint() -> None:
    endpoint = resolve_endpoint(preference="remote", probe=_down)
    assert endpoint.base_url == ""
    assert "LLM_BASE_URL is not set" in endpoint.reason


def test_explicit_local_is_honoured_when_available() -> None:
    endpoint = resolve_endpoint(preference="local", probe=_up)
    assert endpoint.provider == "local"


def test_explicit_local_falls_back_to_remote_and_says_so() -> None:
    # Asking for local and getting nothing must not fail the mission — the
    # evaluator is identical either way, so the run still produces comparable
    # evidence. But the substitution has to be visible in the reason.
    endpoint = resolve_endpoint(preference="local", probe=_down)
    assert endpoint.provider == "remote"
    assert "fell back" in endpoint.reason
    assert "127.0.0.1" in endpoint.reason


def test_explicit_remote_wins_even_with_a_local_server_up() -> None:
    endpoint = resolve_endpoint(preference="remote", probe=_up)
    assert endpoint.provider == "remote"


def test_an_explicit_base_url_still_overrides_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    # Backwards compatibility: existing setups that pointed the lab somewhere
    # specific keep working untouched.
    monkeypatch.setenv("AUTOQUANT_LAB_BASE_URL", "http://example.invalid/v1")
    endpoint = resolve_endpoint(probe=_up)
    assert endpoint.provider == "explicit"
    assert endpoint.base_url == "http://example.invalid/v1"


def test_a_bare_remote_host_gets_the_v1_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    # Hosting platforms often store LLM_BASE_URL as a bare host with no path.
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.invalid")
    endpoint = resolve_endpoint(preference="remote", probe=_down)
    assert endpoint.base_url == "https://llm.example.invalid/v1"


def test_remote_credentials_come_from_the_usual_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    endpoint = resolve_endpoint(preference="remote", probe=_down)
    assert endpoint.api_key == "sk-test"


def test_describe_never_leaks_the_key() -> None:
    endpoint = ProposerEndpoint(
        provider="remote",
        base_url="https://llm.example.invalid/v1",
        model="remote-model",
        api_key="sk-super-secret",
        reason="test",
    )
    assert "sk-super-secret" not in endpoint.describe()
