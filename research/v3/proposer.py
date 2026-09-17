"""Strict declarative proposal boundary for schema-v3 research."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from research.v3.hypothesis import (
    Hypothesis,
    canonical_hypothesis,
    describe_hypothesis,
    parse_hypothesis,
)


class ProposalError(ValueError):
    """A model response did not satisfy the non-executable proposal contract."""


class TransientProposalError(RuntimeError):
    """The configured OpenAI-compatible proposer was temporarily unavailable."""


@dataclass(frozen=True)
class DeclarativeProposal:
    hypothesis: Hypothesis
    rationale: str
    source_hash: str

    @property
    def document(self) -> dict[str, Any]:
        return json.loads(canonical_hypothesis(self.hypothesis))


class Completion(Protocol):
    def __call__(self, prompt: str) -> str: ...


PROPOSAL_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "hypothesis": {"$ref": "#/$defs/hypothesis"},
        "rationale": {"type": "string", "minLength": 1},
    },
    "required": ["hypothesis", "rationale"],
    "additionalProperties": False,
    "$defs": {
        "minute": {
            "type": "integer",
            "minimum": 570,
            "maximum": 955,
            "multipleOf": 5,
        },
        "bounded_number": {
            "type": "number",
            "minimum": -100,
            "maximum": 100,
        },
        "return_from_open_observation": {
            "type": "object",
            "properties": {
                "obs": {"enum": ["return_from_open"]},
                "minute": {"$ref": "#/$defs/minute"},
            },
            "required": ["obs", "minute"],
            "additionalProperties": False,
        },
        "return_between_observation": {
            "type": "object",
            "properties": {
                "obs": {"enum": ["return_between"]},
                "start_minute": {"$ref": "#/$defs/minute"},
                "end_minute": {"$ref": "#/$defs/minute"},
            },
            "required": ["obs", "start_minute", "end_minute"],
            "additionalProperties": False,
        },
        "first_touch_observation": {
            "type": "object",
            "properties": {
                "obs": {"enum": ["first_touch_before_entry"]},
                "after_minute": {"$ref": "#/$defs/minute"},
                "open_offset": {"$ref": "#/$defs/bounded_number"},
            },
            "required": ["obs", "after_minute", "open_offset"],
            "additionalProperties": False,
        },
        "numeric_observation": {
            "anyOf": [
                {"$ref": "#/$defs/return_from_open_observation"},
                {"$ref": "#/$defs/return_between_observation"},
            ],
        },
        "expression": {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {
                        "op": {"enum": ["and", "or"]},
                        "args": {
                            "type": "array",
                            "items": {"$ref": "#/$defs/expression"},
                            "minItems": 2,
                            "maxItems": 4,
                        },
                    },
                    "required": ["op", "args"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "op": {"enum": ["not"]},
                        "arg": {"$ref": "#/$defs/expression"},
                    },
                    "required": ["op", "arg"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "op": {"enum": ["lt", "lte", "gt", "gte"]},
                        "observation": {"$ref": "#/$defs/numeric_observation"},
                        "value": {"$ref": "#/$defs/bounded_number"},
                    },
                    "required": ["op", "observation", "value"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "op": {"enum": ["observed"]},
                        "observation": {"$ref": "#/$defs/first_touch_observation"},
                    },
                    "required": ["op", "observation"],
                    "additionalProperties": False,
                },
            ],
        },
        "structure": {
            "type": "object",
            "properties": {
                "helper": {"enum": ["call_debit_spread", "put_credit_spread"]},
                "width": {"type": "number", "exclusiveMinimum": 0, "maximum": 20},
                "otm_offset": {"type": "number", "minimum": -20, "maximum": 20},
            },
            "required": ["helper", "width", "otm_offset"],
            "additionalProperties": False,
        },
        "hypothesis": {
            "type": "object",
            "properties": {
                "schema_version": {"enum": [3]},
                "entry_minute": {"$ref": "#/$defs/minute"},
                "signal": {"$ref": "#/$defs/expression"},
                "structure": {"$ref": "#/$defs/structure"},
            },
            "required": ["schema_version", "entry_minute", "signal", "structure"],
            "additionalProperties": False,
        },
    },
}


def _atomic_signal_schema(atom: str) -> dict[str, Any]:
    op, observation = atom.split(":", 1)
    if op == "observed" and observation == "first_touch_before_entry":
        return {
            "type": "object",
            "properties": {
                "op": {"enum": ["observed"]},
                "observation": {"$ref": "#/$defs/first_touch_observation"},
            },
            "required": ["op", "observation"],
            "additionalProperties": False,
        }
    observation_defs = {
        "return_from_open": "return_from_open_observation",
        "return_between": "return_between_observation",
    }
    if op in {"lt", "lte", "gt", "gte"} and observation in observation_defs:
        return {
            "type": "object",
            "properties": {
                "op": {"enum": [op]},
                "observation": {"$ref": f"#/$defs/{observation_defs[observation]}"},
                "value": {"$ref": "#/$defs/bounded_number"},
            },
            "required": ["op", "observation", "value"],
            "additionalProperties": False,
        }
    raise ValueError(f"unsupported hypothesis atom: {atom}")


def _atomic_signal_document(atom: str) -> dict[str, Any]:
    op, observation = atom.split(":", 1)
    if op == "observed":
        return {
            "op": "observed",
            "observation": {
                "obs": "first_touch_before_entry",
                "after_minute": 570,
                "open_offset": -0.005,
            },
        }
    if observation == "return_from_open":
        observed = {"obs": observation, "minute": 600}
    else:
        observed = {"obs": observation, "start_minute": 570, "end_minute": 600}
    return {"op": op, "observation": observed, "value": -0.005}


def _lane_family(lane: str) -> str:
    root, left, right = lane.split("|", 2)
    document = {
        "schema_version": 3,
        "entry_minute": 900,
        "signal": {
            "op": root,
            "args": [_atomic_signal_document(left), _atomic_signal_document(right)],
        },
        "structure": {"helper": "call_debit_spread", "width": 5.0, "otm_offset": 0.0},
    }
    return describe_hypothesis(document).family


def _build_compound_lanes() -> tuple[str, ...]:
    comparisons = ("lt", "lte", "gt", "gte")
    touch = "observed:first_touch_before_entry"
    candidates: list[str] = []
    for comparison in comparisons:
        for root in ("and", "or"):
            candidates.append(
                f"{root}|{comparison}:return_from_open|{comparison}:return_between"
            )
    for observation in ("return_from_open", "return_between"):
        for comparison in comparisons:
            for root in ("and", "or"):
                candidates.append(f"{root}|{touch}|{comparison}:{observation}")
    lanes: list[str] = []
    families: set[str] = set()
    for lane in candidates:
        family = _lane_family(lane)
        if family in families:
            raise RuntimeError(f"compound lane family is not unique: {lane}")
        families.add(family)
        lanes.append(lane)
    if len(lanes) != 24:
        raise RuntimeError("compound hypothesis lane catalog must contain 24 families")
    return tuple(lanes)


COMPOUND_LANES = _build_compound_lanes()
COMPOUND_LANE_FAMILIES = {lane: _lane_family(lane) for lane in COMPOUND_LANES}


def _compound_lane_signal_schema(lane: str) -> dict[str, Any]:
    root, left, right = lane.split("|", 2)
    return {
        "type": "object",
        "properties": {
            "op": {"enum": [root]},
            "args": {
                "type": "array",
                "prefixItems": [_atomic_signal_schema(left), _atomic_signal_schema(right)],
                "minItems": 2,
                "maxItems": 2,
            },
        },
        "required": ["op", "args"],
        "additionalProperties": False,
    }


def _spread_variant_schema(variant: str) -> dict[str, Any]:
    variants = {
        "A": ("call_debit_spread", 5.0, 0.0),
        "B": ("put_credit_spread", 10.0, 5.0),
    }
    try:
        helper, width, offset = variants[variant]
    except KeyError as exc:
        raise ValueError(f"unsupported spread variant: {variant}") from exc
    return {
        "type": "object",
        "properties": {
            "helper": {"enum": [helper]},
            "width": {"enum": [width]},
            "otm_offset": {"enum": [offset]},
        },
        "required": ["helper", "width", "otm_offset"],
        "additionalProperties": False,
    }


def proposal_schema_for_diversity(
    diversity_feedback: Sequence[str],
) -> dict[str, Any]:
    """Constrain model output to the coordinator's current family lane."""
    schema = copy.deepcopy(PROPOSAL_JSON_SCHEMA)
    combined = " ".join(str(item) for item in diversity_feedback)
    signal_schema: dict[str, Any] | None = None
    if "Required next compound lane:" in combined:
        lane = next((lane for lane in COMPOUND_LANES if lane in combined), None)
        if lane is not None:
            signal_schema = _compound_lane_signal_schema(lane)
            variant = next(
                (name for name in ("A", "B") if f"spread variant {name}" in combined),
                None,
            )
            if variant is not None:
                schema["$defs"]["hypothesis"]["properties"]["structure"] = (
                    _spread_variant_schema(variant)
                )
    elif "Required next hypothesis family" in combined:
        scheduled = (
            "lt:return_between",
            "lt:return_from_open",
            "observed:first_touch_before_entry",
        )
        allowed = [family for family in scheduled if family in combined]
        if allowed:
            signal_schema = {
                "anyOf": [_atomic_signal_schema(family) for family in allowed]
            }
    elif "atomic-family schedule is satisfied" in combined:
        signal_schema = {
            "type": "object",
            "properties": {
                "op": {"enum": ["and", "or"]},
                "args": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/expression"},
                    "minItems": 2,
                    "maxItems": 4,
                },
            },
            "required": ["op", "args"],
            "additionalProperties": False,
        }
    if signal_schema is not None:
        schema["$defs"]["hypothesis"]["properties"]["signal"] = signal_schema
    return schema


def parse_proposal_json(content: str) -> DeclarativeProposal:
    """Parse a strict JSON proposal; executable source has no accepted field."""
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ProposalError(f"proposer returned invalid JSON: {exc}") from exc
    if not isinstance(payload, Mapping) or set(payload) != {"hypothesis", "rationale"}:
        raise ProposalError("proposal must contain exactly hypothesis and rationale")
    if not isinstance(payload["rationale"], str) or not payload["rationale"].strip():
        raise ProposalError("proposal rationale must be a non-empty string")
    raw_hypothesis = payload["hypothesis"]
    if not isinstance(raw_hypothesis, Mapping):
        raise ProposalError("proposal hypothesis must be an object")
    try:
        hypothesis = parse_hypothesis(raw_hypothesis)
    except (TypeError, ValueError) as exc:
        raise ProposalError(f"invalid declarative hypothesis: {exc}") from exc
    canonical_source = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return DeclarativeProposal(
        hypothesis=hypothesis,
        rationale=payload["rationale"].strip(),
        source_hash=hashlib.sha256(canonical_source.encode()).hexdigest(),
    )


def build_prompt(*, mission: str, previous_attempts: Sequence[Mapping[str, Any]] = (),
                 diversity_feedback: Sequence[str] = ()) -> str:
    """Build the bounded vocabulary prompt supplied to an untrusted model."""
    compact_attempts = [
        {
            key: record[key]
            for key in ("attempt_id", "status", "admitted", "reason", "semantic_hash")
            if key in record
        }
        for record in list(previous_attempts)[-20:]
    ]
    feedback = json.dumps(
        [record for record in compact_attempts if record],
        sort_keys=True,
        allow_nan=False,
    )
    diversity = json.dumps(list(diversity_feedback)[-20:], sort_keys=True, allow_nan=False)
    return f"""Mission:\n{mission.strip()}\n\nReturn JSON only: {{"hypothesis": <object>, "rationale": <string>}}.
The hypothesis is data, never Python or executable text. It must contain exactly:
- schema_version: 3
- entry_minute: a 5-minute-grid integer 570..955 (America/New_York)
- signal: Boolean expression using op and/or/not, comparisons lt/lte/gt/gte, or observed
- observations: return_from_open(minute), return_between(start_minute,end_minute),
  first_touch_before_entry(after_minute,open_offset)
- structure: helper call_debit_spread or put_credit_spread, width > 0 and <= 20,
  otm_offset between -20 and 20
Every declared observation minute must be on the same 5-minute grid. A first touch must occur
strictly before entry_minute; a touch in the entry bar is unavailable. Use only information
observable at or before entry_minute. Vary causal pattern, entry timing,
thresholds, and structure meaningfully; cosmetic or one-small-parameter permutations are rejected.

Minutes are integer minutes after midnight in New York, not HHMM clock labels:
- 570 = 09:30 ET, 600 = 10:00 ET, 840 = 14:00 ET, 870 = 14:30 ET, 900 = 15:00 ET.
- Never describe 840 as 8:40 AM or 870 as 8:70 AM.
- return_between requires 570 <= start_minute < end_minute <= entry_minute.
- first_touch_before_entry requires 570 <= after_minute < entry_minute; leave at least one
  five-minute bar between after_minute and entry_minute.

Family admission is exact. If diversity feedback lists required next hypothesis families,
choose exactly one listed family and make signal one atomic root predicate; do not wrap it in
and, or, or not until the frozen family schedule is satisfied. Exact root templates are:
- lt:return_from_open -> {{"op":"lt","observation":{{"obs":"return_from_open","minute":600}},"value":-0.005}}
- lt:return_between -> {{"op":"lt","observation":{{"obs":"return_between","start_minute":570,"end_minute":600}},"value":-0.005}}
- observed:first_touch_before_entry -> {{"op":"observed","observation":{{"obs":"first_touch_before_entry","after_minute":570,"open_offset":-0.005}}}}
Set entry_minute later than every observation in the chosen template.
Diversity target (highest priority):\n{diversity}
Past formulation/admission reasons (never copy their omitted documents):\n{feedback}\n"""


class DeclarativeProposer:
    """Injectable proposer that validates every completion before returning it."""

    def __init__(self, complete: Completion):
        self._complete = complete

    def propose(self, *, mission: str, previous_attempts: Sequence[Mapping[str, Any]] = (),
                diversity_feedback: Sequence[str] = ()) -> DeclarativeProposal:
        prompt = build_prompt(
            mission=mission,
            previous_attempts=previous_attempts,
            diversity_feedback=diversity_feedback,
        )
        return parse_proposal_json(self._complete(prompt))


class OpenAICompatibleDeclarativeProposer(DeclarativeProposer):
    """OpenAI-compatible chat adapter whose response schema cannot contain code."""

    def __init__(self, *, model: str | None = None, base_url: str | None = None,
                 api_key: str | None = None, timeout: float = 120.0):
        from openai import OpenAI

        from research.lab.endpoints import resolve_endpoint

        # SAME RUNTIME SWITCH AS THE CLI PROPOSER.
        #
        # This is the path the web app and autopilot actually use, so without
        # it the product surface still defaulted to api.openai.com with the
        # "local-not-required" placeholder and 401'd on any box without a
        # local model — while `research.cli llm` cheerfully reported DeepSeek.
        # One resolver, both proposers, one answer.
        self.endpoint = resolve_endpoint()
        self.model = model or self.endpoint.model
        self._response_schema = PROPOSAL_JSON_SCHEMA
        # Probed once and remembered: json_schema is an OpenAI extension and
        # DeepSeek rejects it outright.
        self._json_schema_supported = True
        self.max_tokens = int(
            os.environ.get("AUTOQUANT_LAB_MAX_TOKENS") or 16_384
        )
        if not 256 <= self.max_tokens <= 65_536:
            raise ValueError("AUTOQUANT_LAB_MAX_TOKENS must be between 256 and 65536")
        resolved_api_key = (
            api_key or self.endpoint.api_key or "local-not-required"
        )
        resolved = base_url or self.endpoint.base_url
        if resolved:
            self._client = OpenAI(
                api_key=resolved_api_key,
                base_url=resolved,
                timeout=timeout,
            )
        else:
            self._client = OpenAI(api_key=resolved_api_key, timeout=timeout)
        super().__init__(self._complete_openai)

    def propose(self, *, mission: str,
                previous_attempts: Sequence[Mapping[str, Any]] = (),
                diversity_feedback: Sequence[str] = ()) -> DeclarativeProposal:
        previous_schema = self._response_schema
        self._response_schema = proposal_schema_for_diversity(diversity_feedback)
        try:
            return super().propose(
                mission=mission,
                previous_attempts=previous_attempts,
                diversity_feedback=diversity_feedback,
            )
        finally:
            self._response_schema = previous_schema

    def _send(self, prompt: str, response_format: dict) -> Any:
        return self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": (
                    "Return strict JSON only with keys hypothesis and rationale. "
                    "Never return Python, source code, imports, commands, or file paths."
                )},
                {"role": "user", "content": prompt},
            ],
            response_format=response_format,
            max_tokens=self.max_tokens,
            temperature=0.35,
            seed=int(hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8], 16),
        )

    def _complete_openai(self, prompt: str) -> str:
        from openai import APIConnectionError, APITimeoutError, BadRequestError, OpenAIError

        schema_format = {"type": "json_schema", "json_schema": {
            "name": "declarative_research_hypothesis", "strict": True,
            "schema": self._response_schema,
        }}
        try:
            if self._json_schema_supported:
                try:
                    response = self._send(prompt, schema_format)
                except BadRequestError as exc:
                    if "response_format" not in str(exc):
                        raise
                    self._json_schema_supported = False
                    response = self._send(prompt, {"type": "json_object"})
            else:
                response = self._send(prompt, {"type": "json_object"})
        except (APIConnectionError, APITimeoutError) as exc:
            raise TransientProposalError("declarative proposer connection was interrupted") from exc
        except OpenAIError as exc:
            # Anything unwrapped escapes the loop's recording path and kills
            # the run without leaving a trial behind.
            raise ProposalError(f"proposer request failed: {type(exc).__name__}: {exc}") from exc
        choice = response.choices[0]
        content = choice.message.content
        if not content:
            # Truncation and a blank reply need opposite fixes; say which.
            if getattr(choice, "finish_reason", None) == "length":
                raise ProposalError(
                    f"proposer hit the {self.max_tokens}-token budget before writing a "
                    "hypothesis (finish_reason=length). Raise AUTOQUANT_LAB_MAX_TOKENS; "
                    "reasoning models spend this budget before their first output token."
                )
            raise ProposalError(
                "proposer returned empty content "
                f"(finish_reason={getattr(choice, 'finish_reason', None)!r}, {_usage_note(response)})"
            )
        return content


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
