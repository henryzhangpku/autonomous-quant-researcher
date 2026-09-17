"""Candidate proposer protocol and OpenAI-compatible adapter."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Protocol


class ProposalError(RuntimeError):
    """Raised when a proposer cannot return a valid strict JSON proposal."""


class TransientProposalError(RuntimeError):
    """Raised when proposer transport is temporarily unavailable."""


@dataclass(frozen=True)
class CandidateProposal:
    """Strict candidate payload expected from any LLM proposer."""

    code: str
    rationale: str


class Proposer(Protocol):
    """Injectable proposer interface for tests, local models, or remote LLMs."""

    def propose(self, prompt: str) -> CandidateProposal:
        """Return candidate Python code and rationale for the supplied prompt."""


class OpenAIProposer:
    """OpenAI-compatible chat proposer with env-configurable endpoint/model."""

    def __init__(
        self,
        *,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 120.0,
        max_tokens: int | None = None,
    ) -> None:
        from openai import OpenAI

        from .endpoints import resolve_endpoint

        # ONE PLACE DECIDES WHICH RUNTIME PROPOSES, and it says which out loud.
        # Both call sites construct OpenAIProposer() with no arguments, so
        # resolving here covers the CLI and the web app together. Explicit
        # constructor arguments still win, which is what the tests rely on.
        self.endpoint = resolve_endpoint()
        self.model = model or self.endpoint.model
        self.temperature = float(os.environ.get("AUTOQUANT_LAB_TEMPERATURE", "0.2"))
        if not 0.0 <= self.temperature <= 2.0:
            raise ValueError("AUTOQUANT_LAB_TEMPERATURE must be between 0 and 2")
        client_kwargs: dict[str, object] = {
            "api_key": api_key or self.endpoint.api_key or "local-not-required",
            "timeout": timeout,
        }
        resolved_base_url = base_url or self.endpoint.base_url
        if resolved_base_url:
            client_kwargs["base_url"] = resolved_base_url
        self._client = OpenAI(**client_kwargs)
        # ROOM FOR MODELS THAT THINK BEFORE THEY ANSWER.
        #
        # This was a fixed 2500, which predates reasoning models. On those,
        # max_tokens caps reasoning AND output together, so a long mission
        # prompt consumed the entire budget on private reasoning and returned
        # EMPTY content with finish_reason='length'. Measured against the real
        # spy-0dte mission on 2026-08-15 with deepseek-v4-flash: at 2500 the
        # model spent all 2500 tokens reasoning and wrote 0 characters; at
        # 12000 it reasoned for 10430 and produced a 1146-character candidate.
        # Every attempt failed, and the message blamed an "empty" reply.
        # The ceiling is 65536, not 16384: on 2026-08-15 deepseek-v4-flash hit
        # the 16384 budget on a real spy-0dte attempt and returned nothing, so
        # a cap at that value would forbid the setting that fixes it. Keeping
        # the validation, raising the roof.
        self.max_tokens = int(
            max_tokens
            or os.environ.get("AUTOQUANT_LAB_MAX_TOKENS")
            or 16_384
        )
        if not 256 <= self.max_tokens <= 65_536:
            raise ValueError("AUTOQUANT_LAB_MAX_TOKENS must be between 256 and 65536")
        # Probed on first use and remembered; see _JSON_SCHEMA_FORMAT.
        self._json_schema_supported = True

    # STRICT SCHEMA WHERE IT EXISTS, PLAIN JSON WHERE IT DOES NOT.
    #
    # `json_schema` is an OpenAI extension. DeepSeek — and most other
    # OpenAI-compatible endpoints — reject it outright ("This response_format
    # type is unavailable now"), which failed all 50 attempts of the first real
    # mission on 2026-08-15 without a single candidate being evaluated.
    #
    # Downgrading costs nothing in rigour: `parse_proposal_json` below already
    # enforces the entire contract in code — exactly the keys `code` and
    # `rationale`, both present, code a non-empty string. The schema was a
    # convenience that let the server reject malformed output early, never the
    # thing that made the contract safe.
    _JSON_SCHEMA_FORMAT = {
        "type": "json_schema",
        "json_schema": {
            "name": "research_candidate",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["code", "rationale"],
                "additionalProperties": False,
            },
        },
    }
    _JSON_OBJECT_FORMAT = {"type": "json_object"}

    def _create(self, prompt: str, response_format: dict[str, object]):
        return self._client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Return strict JSON only with string keys 'code' and "
                        "'rationale'. The code must be a single Python candidate. "
                        "Follow the mission's candidate interface and function "
                        "signatures exactly. Do not invent helper APIs, attributes, "
                        "or undefined names."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format=response_format,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )

    def propose(self, prompt: str) -> CandidateProposal:
        from openai import APIConnectionError, APITimeoutError, BadRequestError, OpenAIError

        try:
            if self._json_schema_supported:
                try:
                    response = self._create(prompt, self._JSON_SCHEMA_FORMAT)
                except BadRequestError as exc:
                    if "response_format" not in str(exc):
                        raise
                    # Remembered for the process, so the mission pays this
                    # probe once instead of on every attempt.
                    self._json_schema_supported = False
                    response = self._create(prompt, self._JSON_OBJECT_FORMAT)
            else:
                response = self._create(prompt, self._JSON_OBJECT_FORMAT)
        except (APIConnectionError, APITimeoutError) as exc:
            raise TransientProposalError("Local proposer connection was interrupted.") from exc
        except OpenAIError as exc:
            # EVERY proposer failure must reach the loop as a ProposalError.
            #
            # Only connection and timeout were wrapped, so an auth failure, a
            # rate limit or any other API status error propagated raw. The
            # loop's except clause lists (LabLoopError, ProposalError,
            # TimeoutExpired) — which is exactly the path that RECORDS a failed
            # attempt — so an unwrapped error skipped it, crashed the process
            # and left `runs.jsonl` empty with status still "ready".
            #
            # Observed 2026-08-15: a misconfigured key produced a traceback and
            # a mission that looked untouched. The run-log contract says record
            # every attempt including invalid ones and never summarize failures
            # away; an infrastructure failure that leaves no trace is the same
            # defect as a silent catch, arrived at from the opposite direction.
            raise ProposalError(f"Proposer request failed: {type(exc).__name__}: {exc}") from exc
        choice = response.choices[0]
        content = choice.message.content
        if not content:
            # SAY WHICH KIND OF EMPTY. Truncation and a genuinely blank reply
            # need opposite responses — raise the budget versus fix the prompt
            # or the model — and the ledger only helps if it distinguishes
            # them. "Proposer returned empty content" sent us looking at the
            # wrong one for a whole mission.
            if getattr(choice, "finish_reason", None) == "length":
                raise ProposalError(
                    f"Proposer hit the {self.max_tokens}-token budget before writing a "
                    "candidate (finish_reason=length). Raise AUTOQUANT_LAB_MAX_TOKENS; "
                    "reasoning models spend this budget before their first output token."
                )
            raise ProposalError(
                "Proposer returned empty content "
                f"(finish_reason={getattr(choice, 'finish_reason', None)!r}, {_usage_note(response)})."
            )
        return parse_proposal_json(content)


def parse_proposal_json(content: str) -> CandidateProposal:
    """Parse and validate the strict JSON proposal contract."""

    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ProposalError(f"Proposer returned invalid JSON: {exc}") from exc
    if set(payload) != {"code", "rationale"}:
        raise ProposalError("Proposal JSON must contain exactly code and rationale.")
    code = payload["code"]
    rationale = payload["rationale"]
    if not isinstance(code, str) or not code.strip():
        raise ProposalError("Proposal code must be a non-empty string.")
    if not isinstance(rationale, str):
        raise ProposalError("Proposal rationale must be a string.")
    return CandidateProposal(code=code, rationale=rationale)


def _usage_note(response: object) -> str:
    """Token accounting for an unusable reply.

    An empty answer means different things depending on where the budget
    went: all of it into private reasoning is a budget problem, almost none
    of it is a prompt or model problem. Guessing between those cost a whole
    mission on 2026-08-15, so the ledger now carries the numbers.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return "usage unavailable"
    details = getattr(usage, "completion_tokens_details", None)
    reasoning = getattr(details, "reasoning_tokens", None) if details else None
    return (
        f"completion_tokens={getattr(usage, 'completion_tokens', None)}, "
        f"reasoning_tokens={reasoning}"
    )
