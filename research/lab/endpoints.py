"""Where the proposer's LLM comes from, decided once and stated out loud.

Autonomous Quant Researcher was written for a laptop with an NVIDIA GPU running llama.cpp. The
box it now runs on has neither, so every mission failed at the proposer with a
401 against api.openai.com — the placeholder key — while the README still
described a local model. The endpoint was implicit, so the failure was
mystifying rather than obvious.

Two runtimes, one switch:

    AUTOQUANT_LAB_PROVIDER = auto (default) | local | remote

  auto      use the local server when something is actually listening on it,
            otherwise the remote LLM (configurable). This is what a laptop with a GPU and a laptop
            without one both want, with no configuration.
  local     prefer local, but FALL BACK to the remote LLM when nothing answers
            rather than failing the mission. A research loop that cannot
            propose is worth less than one running on a remote model, and the
            fallback is recorded so the choice is never silent.
  remote    force remote, even if a local server is up.

The remote LLM is configured only through LLM_BASE_URL, LLM_MODEL and
LLM_API_KEY; there is no built-in remote endpoint.

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
    """OpenAI-compatible hosts serve under /v1; LLM_BASE_URL is often bare."""
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


# Pre-rename variable names, read only when the new name is unset.
# Removed after one release.
_LEGACY_BASE_URL = ("AUTOQUANT_DEEPSEEK_BASE_URL", "DEEPSEEK_BASE_URL")
_LEGACY_MODEL = ("AUTOQUANT_DEEPSEEK_MODEL", "DEEPSEEK_MODEL")
_LEGACY_API_KEY = ("AUTOQUANT_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY")
_LEGACY_REMOTE_CHOICE = "deepseek"


def _remote_endpoint(reason: str) -> ProposerEndpoint:
    base = _env("LLM_BASE_URL", *_LEGACY_BASE_URL)
    if not base:
        reason = f"{reason}; LLM_BASE_URL is not set"
    return ProposerEndpoint(
        provider="remote",
        base_url=_with_v1(base) if base else "",
        model=_env("LLM_MODEL", "AUTOQUANT_LAB_MODEL", *_LEGACY_MODEL) or "",
        api_key=_env("LLM_API_KEY", "AUTOQUANT_LAB_API_KEY", *_LEGACY_API_KEY) or "",
        reason=reason,
    )


def resolve_endpoint(preference: str | None = None, probe=None) -> ProposerEndpoint:
    """Pick the proposer runtime. `probe` is injectable for tests."""
    probe = probe or local_server_is_listening
    choice = (preference or _env("AUTOQUANT_LAB_PROVIDER") or "auto").strip().lower()
    if choice == _LEGACY_REMOTE_CHOICE:
        choice = "remote"

    explicit_base = _env("AUTOQUANT_LAB_BASE_URL")
    if explicit_base and choice == "auto":
        # Someone has already said exactly where to go; do not second-guess it.
        return ProposerEndpoint(
            provider="explicit",
            base_url=explicit_base,
            model=_env("AUTOQUANT_LAB_MODEL", "LLM_MODEL", *_LEGACY_MODEL) or "",
            api_key=_env("AUTOQUANT_LAB_API_KEY", "LLM_API_KEY", *_LEGACY_API_KEY) or "local-not-required",
            reason="AUTOQUANT_LAB_BASE_URL set explicitly",
        )

    if choice == "remote":
        return _remote_endpoint("AUTOQUANT_LAB_PROVIDER=remote")

    local = _local_endpoint("")
    if choice == "local":
        if probe(local.base_url):
            return _local_endpoint("AUTOQUANT_LAB_PROVIDER=local, server responding")
        # Asked for local, nothing there. Falling back beats failing the run:
        # the evaluator is identical either way, so the mission still produces
        # comparable evidence.
        return _remote_endpoint(
            f"AUTOQUANT_LAB_PROVIDER=local but nothing answered on {local.base_url}; fell back to the remote LLM"
        )

    if probe(local.base_url):
        return _local_endpoint("auto: local server responding")
    return _remote_endpoint("auto: no local server, using the remote LLM")
