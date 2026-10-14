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


def _signature(name: str, spec: Mapping[str, Any]) -> str:
    parts = [f"{child}: node" for child in spec.get("children", [])]
    for param, bounds in spec.get("params", {}).items():
        if "choices" in bounds:
            parts.append(f"{param}: one of {bounds['choices']}")
        elif bounds.get("min") is not None:
            parts.append(f"{param}: {bounds['type']} {bounds['min']}..{bounds['max']}")
        else:
            parts.append(f"{param}: {bounds['type']}")
    return f"{name}({', '.join(parts)})"


def _examples(vocabulary: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Two complete recipes built only from operators the vocabulary offers."""
    schema = vocabulary.get("schema", "")
    ops = vocabulary.get("operators", {})
    observations = list(vocabulary.get("observations", {})) or ["ret"]
    leaf = {"op": "obs", "name": "ret" if "ret" in observations else observations[0]}
    out = []
    if "ewm" in ops and "xscore" in ops:
        out.append({"schema": schema, "signal": {"op": "xscore", "x": {
            "op": "ewm", "halflife": 120, "x": leaf}}})
    if "neg" in ops and "rolling_sum" in ops and "rank" in ops:
        out.append({"schema": schema, "signal": {"op": "neg", "x": {
            "op": "rank", "x": {"op": "rolling_sum", "window": 21, "x": leaf}}}})
    return out or [{"schema": schema, "signal": leaf}]


def build_prompt(*, mission: str, vocabulary: Mapping[str, Any],
                 attempts: Sequence[Mapping[str, Any]] = (),
                 rejections: Sequence[Mapping[str, Any]] = (), max_attempts_shown: int = 30) -> str:
    shown = list(attempts)[-max_attempts_shown:]
    operators = "\n".join("  " + _signature(name, spec)
                           for name, spec in sorted(vocabulary.get("operators", {}).items()))
    observations = "\n".join(f"  {name}: {text}"
                              for name, text in sorted(vocabulary.get("observations", {}).items()))
    examples = "\n".join("  " + json.dumps({"recipe": ex, "rationale": "<one sentence>"})
                          for ex in _examples(vocabulary))
    return (
        "You propose ONE falsifiable daily portfolio signal as a declarative recipe.\n"
        "Trusted code evaluates it as a dollar-neutral long/short book after costs, on\n"
        "chronological data behind discovery / validation / holdout stages. You never write code.\n\n"
        f"Mission:\n{mission.strip()}\n\n"
        "OUTPUT FORMAT (strict JSON, nothing else):\n"
        '  {"recipe": {"schema": "' + str(vocabulary.get("schema", "")) + '", "signal": NODE}, '
        '"rationale": "<one sentence>"}\n'
        "A NODE is always a JSON object with an \"op\" key plus exactly that operator's\n"
        "fields. Child fields hold another NODE object. Inputs are always\n"
        '{"op": "obs", "name": "<observation>"} -- never a bare string. No lists anywhere.\n'
        f"At most {vocabulary.get('max_depth')} levels deep and {vocabulary.get('max_nodes')} nodes.\n\n"
        f"Operators (name(fields)):\n{operators}\n\n"
        f"Observations:\n{observations}\n\n"
        f"Valid examples (do not resubmit these exact recipes):\n{examples}\n\n"
        "A higher score means long. Values at date t may use data up to t only; the\n"
        "evaluator lags them. One idea per proposal; do not resubmit an earlier recipe or\n"
        "turn one knob of it. At most two recipes per family are admitted, where a family is\n"
        "what a recipe does: each input with its net sign and lookback (about 1 week, 1 month,\n"
        "3-6 months, 1 year+). Wrapping the same idea in another operator does not make it new.\n\n"
        "Your recent rejected proposals and why (fix these mistakes):\n"
        f"{json.dumps(list(rejections), sort_keys=True)}\n\n"
        "Earlier evaluated attempts (discovery evidence only):\n"
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
                attempts: Sequence[Mapping[str, Any]] = (),
                rejections: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
        return parse_proposal(self._complete(
            build_prompt(mission=mission, vocabulary=vocabulary, attempts=attempts,
                         rejections=rejections)))


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
