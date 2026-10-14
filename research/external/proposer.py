"""Proposer for declarative portfolio recipes.

The model returns strict JSON with exactly ``recipe`` and ``rationale``; the
recipe is data in the evaluator's published vocabulary, never code. The
completion function is injectable (tests and demos use a scripted one); the
default adapter uses the lab's endpoint resolver, so the same local or remote
runtime proposes here as everywhere else.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

Completion = Callable[[str], str]


class ProposalError(ValueError):
    pass


def build_prompt(*, mission: str, vocabulary: Mapping[str, Any],
                 attempts: Sequence[Mapping[str, Any]] = (), max_attempts_shown: int = 30) -> str:
    shown = list(attempts)[-max_attempts_shown:]
    return (
        "You propose ONE falsifiable daily portfolio signal as a declarative recipe.\n"
        "Trusted code evaluates it as a dollar-neutral long/short book after costs, on\n"
        "chronological data behind discovery / validation / holdout stages. You never write\n"
        "code. Return strict JSON with exactly two keys: \"recipe\" and \"rationale\".\n\n"
        f"Mission:\n{mission.strip()}\n\n"
        "Recipe vocabulary (the only operators, observations and parameter bounds allowed):\n"
        f"{json.dumps(vocabulary, sort_keys=True)}\n\n"
        "A recipe is {\"schema\": <vocabulary schema>, \"signal\": <node>}; a node is\n"
        "{\"op\": <operator>, <child keys>: <node>, <params>: <values>}. A higher score\n"
        "means long. Values at date t may use data up to t only; the evaluator lags them.\n\n"
        "Rules: one idea per proposal; do not resubmit an earlier recipe or turn one knob of\n"
        "it; prefer simple, economically motivated signals; at most two recipes per set of\n"
        "observations are admitted.\n\n"
        "Earlier attempts (discovery evidence only):\n"
        f"{json.dumps(shown, sort_keys=True, default=str)}\n"
    )


def parse_proposal(text: str) -> dict[str, Any]:
    try:
        document = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ProposalError(f"proposal is not JSON: {exc}") from exc
    if not isinstance(document, dict) or set(document) != {"recipe", "rationale"}:
        raise ProposalError("proposal must have exactly 'recipe' and 'rationale'")
    if not isinstance(document["recipe"], dict) or not isinstance(document["rationale"], str):
        raise ProposalError("recipe must be an object and rationale a string")
    return document


class RecipeProposer:
    def __init__(self, complete: Completion):
        self._complete = complete

    def propose(self, *, mission: str, vocabulary: Mapping[str, Any],
                attempts: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
        return parse_proposal(self._complete(
            build_prompt(mission=mission, vocabulary=vocabulary, attempts=attempts)))


def scripted(proposals: Sequence[Mapping[str, Any]]) -> Completion:
    """A deterministic proposer for tests and demos: returns proposals in order."""
    queue = [json.dumps(p) for p in proposals]

    def complete(_prompt: str) -> str:
        if not queue:
            raise ProposalError("scripted proposer is exhausted")
        return queue.pop(0)
    return complete


def openai_completion(timeout: float = 120.0) -> Completion:  # pragma: no cover - network
    from openai import OpenAI

    from research.lab.endpoints import resolve_endpoint

    endpoint = resolve_endpoint()
    client = OpenAI(base_url=endpoint.base_url, api_key=endpoint.api_key or "local-not-required",
                    timeout=timeout)

    def complete(prompt: str) -> str:
        response = client.chat.completions.create(
            model=endpoint.model, messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}, temperature=0.7)
        return response.choices[0].message.content or ""
    return complete
