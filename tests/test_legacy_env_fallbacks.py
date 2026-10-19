"""Pre-rename environment names still work for one release, behind the new names.

Each new name is read first; the old one is a silent fallback. These tests are
deleted together with the fallbacks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from research.hmm import classifier_gate
from research.hmm.data import lake_root
from research.lab.endpoints import resolve_endpoint

OLD = {
    "base": ("AUTOQUANT_DEEPSEEK_BASE_URL", "DEEPSEEK_BASE_URL"),
    "model": ("AUTOQUANT_DEEPSEEK_MODEL", "DEEPSEEK_MODEL"),
    "key": ("AUTOQUANT_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"),
}
NEW = ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "AUTOQUANT_LAB_PROVIDER", "AUTOQUANT_LAB_BASE_URL",
       "AUTOQUANT_LAB_MODEL", "AUTOQUANT_LAB_API_KEY")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in NEW + sum(OLD.values(), ()) + ("CLASSIFIER_API_KEY", "TYPESAFE_API_KEY",
                                               "RESEARCH_DATALAKE_DIR", "QS_DATALAKE_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)  # no real key file


def _down(_base_url: str, timeout: float = 0.0) -> bool:
    return False


@pytest.mark.parametrize("old", OLD["base"])
def test_old_base_url_names_are_a_fallback(monkeypatch: pytest.MonkeyPatch, old: str) -> None:
    monkeypatch.setenv(old, "https://old.example.invalid")
    assert resolve_endpoint(preference="remote", probe=_down).base_url == "https://old.example.invalid/v1"
    monkeypatch.setenv("LLM_BASE_URL", "https://new.example.invalid")
    assert resolve_endpoint(preference="remote", probe=_down).base_url == "https://new.example.invalid/v1"


@pytest.mark.parametrize("old", OLD["model"])
def test_old_model_names_are_a_fallback(monkeypatch: pytest.MonkeyPatch, old: str) -> None:
    monkeypatch.setenv(old, "old-model")
    assert resolve_endpoint(preference="remote", probe=_down).model == "old-model"
    monkeypatch.setenv("LLM_MODEL", "new-model")
    assert resolve_endpoint(preference="remote", probe=_down).model == "new-model"


@pytest.mark.parametrize("old", OLD["key"])
def test_old_api_key_names_are_a_fallback(monkeypatch: pytest.MonkeyPatch, old: str) -> None:
    monkeypatch.setenv(old, "sk-old")
    assert resolve_endpoint(preference="remote", probe=_down).api_key == "sk-old"
    monkeypatch.setenv("LLM_API_KEY", "sk-new")
    assert resolve_endpoint(preference="remote", probe=_down).api_key == "sk-new"


def test_old_api_key_name_is_a_fallback_for_an_explicit_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOQUANT_LAB_BASE_URL", "http://example.invalid/v1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-old")
    endpoint = resolve_endpoint(probe=_down)
    assert endpoint.provider == "explicit" and endpoint.api_key == "sk-old"


def test_old_provider_value_is_an_alias_of_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOQUANT_LAB_PROVIDER", "deepseek")
    assert resolve_endpoint(probe=_down).provider == "remote"
    assert resolve_endpoint(preference="deepseek", probe=lambda *_a, **_k: True).provider == "remote"


def test_old_classifier_key_name_is_a_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    assert classifier_gate.api_key() is None
    monkeypatch.setenv("TYPESAFE_API_KEY", "old-key")
    assert classifier_gate.api_key() == "old-key"
    monkeypatch.setenv("CLASSIFIER_API_KEY", "new-key")
    assert classifier_gate.api_key() == "new-key"


def test_old_classifier_key_file_is_a_fallback(tmp_path: Path) -> None:
    (tmp_path / ".qs-tools").mkdir()
    (tmp_path / ".qs-tools" / "typesafe-api-key").write_text("file-key\n", encoding="utf-8")
    assert classifier_gate.api_key() == "file-key"


def test_old_datalake_name_is_a_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        lake_root()
    monkeypatch.setenv("QS_DATALAKE_DIR", str(tmp_path / "old"))
    assert lake_root() == tmp_path / "old"
    monkeypatch.setenv("RESEARCH_DATALAKE_DIR", str(tmp_path / "new"))
    assert lake_root() == tmp_path / "new"
