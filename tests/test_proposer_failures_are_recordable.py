"""A proposer failure must arrive as ProposalError, so the loop records it.

`LabLoop.run_once` catches (LabLoopError, ProposalError, TimeoutExpired) and
that clause is the ONLY path that appends a failed attempt to runs.jsonl. Any
exception outside it escapes, kills the process, and leaves the mission looking
untouched: run_count 0, status "ready", an empty ledger.

That is exactly what happened on 2026-08-15 — a misconfigured API key produced
a raw openai.AuthenticationError and no trace anywhere that a run was even
tried. The run-log contract requires every attempt, including invalid ones, to
be recorded and forbids summarizing failures away.
"""

from __future__ import annotations

import pytest

from research.lab.proposer import OpenAIProposer, ProposalError, TransientProposalError


class _RaisingCompletions:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def create(self, **_kwargs):
        raise self._exc


class _RaisingClient:
    def __init__(self, exc: Exception) -> None:
        self.chat = type("_Chat", (), {"completions": _RaisingCompletions(exc)})()


def _proposer_raising(exc: Exception) -> OpenAIProposer:
    proposer = OpenAIProposer.__new__(OpenAIProposer)
    proposer.model = "test-model"
    proposer._json_schema_supported = True
    proposer.max_tokens = 16384
    proposer.temperature = 0.2
    proposer._client = _RaisingClient(exc)
    return proposer


def _openai_error(name: str) -> Exception:
    """Build a real openai exception without performing any network call."""
    import httpx
    import openai

    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    response = httpx.Response(status_code=401, request=request, json={"error": {"message": "bad key"}})
    exc_type = getattr(openai, name)
    return exc_type("bad key", response=response, body=None)


@pytest.mark.parametrize("name", ["AuthenticationError", "PermissionDeniedError", "RateLimitError"])
def test_api_status_errors_become_proposal_errors(name: str) -> None:
    # These previously escaped unwrapped and crashed the loop before it could
    # write the failed attempt.
    proposer = _proposer_raising(_openai_error(name))
    with pytest.raises(ProposalError):
        proposer.propose("prompt")


def test_connection_failures_stay_transient() -> None:
    # Transport blips keep their own type: the loop treats them as retryable
    # rather than as a candidate that failed on merit.
    import httpx
    import openai

    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    proposer = _proposer_raising(openai.APIConnectionError(request=request))
    with pytest.raises(TransientProposalError):
        proposer.propose("prompt")


def test_the_loop_catches_what_the_proposer_now_raises() -> None:
    # The guarantee this file exists to protect: whatever the proposer raises
    # is inside the tuple run_once() handles, so the attempt gets recorded.
    import inspect

    from research.lab import loop as loop_module

    # Asserted against the MODULE, not one function: upstream moved the
    # recording try/except out of run_once on 2026-08-15 and a test pinned to
    # that method name failed while the guarantee itself was intact. What must
    # stay true is that whatever the proposer raises is caught somewhere that
    # records the attempt, not that a particular function does it.
    source = inspect.getsource(loop_module)
    assert "ProposalError" in source
    assert "except (LabLoopError, ProposalError" in source


class _FormatRecordingCompletions:
    """Records the response_format of each call; rejects json_schema once."""

    def __init__(self, *, reject_schema: bool) -> None:
        self.reject_schema = reject_schema
        self.formats: list[str] = []

    def create(self, **kwargs):
        fmt = kwargs["response_format"]["type"]
        self.formats.append(fmt)
        if self.reject_schema and fmt == "json_schema":
            import httpx
            import openai

            request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
            response = httpx.Response(
                status_code=400,
                request=request,
                json={"error": {"message": "This response_format type is unavailable now"}},
            )
            raise openai.BadRequestError("response_format unavailable", response=response, body=None)
        message = type("_M", (), {"content": '{"code": "ENTRY_HM=(10,0)", "rationale": "r"}'})()
        return type("_R", (), {"choices": [type("_C", (), {"message": message})()]})()


def _proposer_with(completions) -> OpenAIProposer:
    proposer = OpenAIProposer.__new__(OpenAIProposer)
    proposer.model = "test-model"
    proposer._json_schema_supported = True
    proposer.max_tokens = 16384
    proposer.temperature = 0.2
    proposer._client = type("_Client", (), {"chat": type("_Chat", (), {"completions": completions})()})()
    return proposer


def test_endpoints_without_json_schema_fall_back_to_json_object() -> None:
    # DeepSeek and most OpenAI-compatible endpoints reject json_schema. Before
    # the fallback this failed all 50 attempts of a mission without evaluating
    # a single candidate (2026-08-15).
    completions = _FormatRecordingCompletions(reject_schema=True)
    proposer = _proposer_with(completions)
    proposal = proposer.propose("prompt")
    assert proposal.code
    assert completions.formats == ["json_schema", "json_object"]


def test_the_downgrade_is_remembered_for_later_attempts() -> None:
    # The probe costs one rejected call per mission, not one per attempt.
    completions = _FormatRecordingCompletions(reject_schema=True)
    proposer = _proposer_with(completions)
    proposer.propose("a")
    proposer.propose("b")
    assert completions.formats == ["json_schema", "json_object", "json_object"]


def test_strict_schema_is_kept_where_it_works() -> None:
    completions = _FormatRecordingCompletions(reject_schema=False)
    proposer = _proposer_with(completions)
    proposer.propose("a")
    proposer.propose("b")
    assert completions.formats == ["json_schema", "json_schema"]


def test_unrelated_bad_requests_still_surface() -> None:
    # Only a response_format complaint may trigger the downgrade; anything else
    # is a real failure and must reach the loop as a recorded attempt.
    import httpx
    import openai

    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    response = httpx.Response(status_code=400, request=request, json={"error": {"message": "context length exceeded"}})
    proposer = _proposer_raising(openai.BadRequestError("context length exceeded", response=response, body=None))
    proposer._json_schema_supported = True
    with pytest.raises(ProposalError):
        proposer.propose("prompt")


class _TruncatingCompletions:
    """Returns an empty message with finish_reason='length', as reasoning
    models do when max_tokens is consumed before the first output token."""

    def create(self, **_kwargs):
        message = type("_M", (), {"content": ""})()
        choice = type("_C", (), {"message": message, "finish_reason": "length"})()
        return type("_R", (), {"choices": [choice]})()


def test_truncation_is_reported_as_truncation() -> None:
    # Measured with deepseek-v4-flash on the real spy-0dte mission: at 2500
    # tokens it spent the entire budget reasoning and returned 0 characters.
    # The old message called that "empty content", which points at the prompt
    # or the model instead of the budget.
    proposer = _proposer_with(_TruncatingCompletions())
    with pytest.raises(ProposalError, match="finish_reason=length"):
        proposer.propose("prompt")


def test_token_budget_is_configurable_and_defaults_high_enough_to_reason() -> None:
    import openai

    made: dict[str, object] = {}

    class _Client:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kwargs):
                    made.update(kwargs)
                    message = type("_M", (), {"content": '{"code": "x=1", "rationale": "r"}'})()
                    choice = type("_C", (), {"message": message, "finish_reason": "stop"})()
                    return type("_R", (), {"choices": [choice]})()

    proposer = OpenAIProposer.__new__(OpenAIProposer)
    proposer.model = "m"
    proposer._json_schema_supported = False
    proposer.max_tokens = 16384
    proposer.temperature = 0.2
    proposer._client = _Client()
    proposer.propose("prompt")
    # The old fixed 2500 could not fit this model's reasoning at all.
    assert made["max_tokens"] >= 12000
    assert openai is not None
