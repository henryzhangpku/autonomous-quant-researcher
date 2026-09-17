"""Where the proposer's LLM comes from, decided once and stated out loud.

Autonomous Quant Researcher was written for a laptop with an NVIDIA GPU running llama.cpp. The
box it now runs on has neither, so every mission failed at the proposer with a
401 against api.openai.com — the placeholder key — while the README still
described a local model. The endpoint was implicit, so the failure was
mystifying rather than obvious.

Two runtimes, one switch:

    AUTOQUANT_LAB_PROVIDER = auto (default) | local | deepseek

  auto      use the local server when something is actually listening on it,
            otherwise DeepSeek. This is what a laptop with a GPU and a laptop
            without one both want, with no configuration.
  local     prefer local, but FALL BACK to DeepSeek when nothing answers
            rather than failing the mission. A research loop that cannot
            propose is worth less than one running on a remote model, and the
            fallback is recorded so the choice is never silent.
  deepseek  force remote, even if a local server is up.

An explicit AUTOQUANT_LAB_BASE_URL still overrides everything, so existing
setups keep working untouched.

Model choice never changes the fixed evaluator, so switching runtimes cannot
alter a metric — only who proposes the candidate.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from dataclasses import dataclass

LOCAL_BASE_URL = "http://127.0.0.1:8080/v1"
LOCAL_MODEL = "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF:Q4_K_M"
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-v4-flash"
PROBE_TIMEOUT_SECONDS = 1.5


@dataclass(frozen=True)
class ProposerEndpoint:
    """The resolved proposer runtime, and why it was chosen."""

    provider: str
    base_url: str
    model: str
    api_key: str
    reason: str

    def describe(self) -> str:
        """One line for logs and the CLI. Never includes the key."""
        return f"{self.provider} :: {self.model} @ {self.base_url} ({self.reason})"


def _env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _with_v1(url: str) -> str:
    """OpenAI-compatible hosts serve under /v1; DEEPSEEK_BASE_URL is often bare."""
    trimmed = url.rstrip("/")
    return trimmed if trimmed.endswith("/v1") else f"{trimmed}/v1"


def local_server_is_listening(base_url: str, timeout: float = PROBE_TIMEOUT_SECONDS) -> bool:
    """True when something answers on the local endpoint.

    ANY HTTP response counts, including 404 and 401: the question is whether a
    server is there, not whether this exact route exists. Only a transport
    failure means "no local model".
    """
    request = urllib.request.Request(f"{base_url.rstrip('/')}/models", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def _local_endpoint(reason: str) -> ProposerEndpoint:
    return ProposerEndpoint(
        provider="local",
        base_url=_env("AUTOQUANT_LOCAL_BASE_URL") or LOCAL_BASE_URL,
        model=_env("AUTOQUANT_LOCAL_MODEL", "AUTOQUANT_LAB_MODEL") or LOCAL_MODEL,
        api_key=_env("AUTOQUANT_LOCAL_API_KEY") or "local-not-required",
        reason=reason,
    )


def _deepseek_endpoint(reason: str) -> ProposerEndpoint:
    base = _env("AUTOQUANT_DEEPSEEK_BASE_URL", "DEEPSEEK_BASE_URL") or DEEPSEEK_BASE_URL
    return ProposerEndpoint(
        provider="deepseek",
        base_url=_with_v1(base),
        model=_env("AUTOQUANT_DEEPSEEK_MODEL", "DEEPSEEK_MODEL", "AUTOQUANT_LAB_MODEL") or DEEPSEEK_MODEL,
        api_key=_env("AUTOQUANT_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY", "AUTOQUANT_LAB_API_KEY") or "",
        reason=reason,
    )


def resolve_endpoint(preference: str | None = None, probe=None) -> ProposerEndpoint:
    """Pick the proposer runtime. `probe` is injectable for tests."""
    probe = probe or local_server_is_listening
    choice = (preference or _env("AUTOQUANT_LAB_PROVIDER") or "auto").strip().lower()

    explicit_base = _env("AUTOQUANT_LAB_BASE_URL")
    if explicit_base and choice == "auto":
        # Someone has already said exactly where to go; do not second-guess it.
        return ProposerEndpoint(
            provider="explicit",
            base_url=explicit_base,
            model=_env("AUTOQUANT_LAB_MODEL") or DEEPSEEK_MODEL,
            api_key=_env("AUTOQUANT_LAB_API_KEY", "DEEPSEEK_API_KEY") or "local-not-required",
            reason="AUTOQUANT_LAB_BASE_URL set explicitly",
        )

    if choice in {"deepseek", "remote"}:
        return _deepseek_endpoint("AUTOQUANT_LAB_PROVIDER=deepseek")

    local = _local_endpoint("")
    if choice == "local":
        if probe(local.base_url):
            return _local_endpoint("AUTOQUANT_LAB_PROVIDER=local, server responding")
        # Asked for local, nothing there. Falling back beats failing the run:
        # the evaluator is identical either way, so the mission still produces
        # comparable evidence.
        return _deepseek_endpoint(
            f"AUTOQUANT_LAB_PROVIDER=local but nothing answered on {local.base_url}; fell back to DeepSeek"
        )

    if probe(local.base_url):
        return _local_endpoint("auto: local server responding")
    return _deepseek_endpoint("auto: no local server, using DeepSeek")
