"""Validated non-executable hypothesis documents and trusted interpretation."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from research.backtest.data import CandidateSessionView
from research.backtest.structures import Structure, call_debit_spread, put_credit_spread

SCHEMA_VERSION = 3
MINUTE_MIN = 9 * 60 + 30
MINUTE_MAX = 15 * 60 + 55
MINUTE_STEP = 5
MAX_DEPTH = 6
MAX_CONDITIONS = 8
MAX_ABS_THRESHOLD = 100.0
SUPPORTED_STRUCTURES = {"call_debit_spread", "put_credit_spread"}
SUPPORTED_OBSERVATIONS = {
    "return_from_open",
    "return_between",
    "first_touch_before_entry",
}


class HypothesisError(ValueError):
    """The declarative document is unsupported or unsafe."""


@dataclass(frozen=True)
class Hypothesis:
    schema_version: int
    entry_minute: int
    signal: Mapping[str, Any]
    structure: Mapping[str, Any]


@dataclass(frozen=True)
class DiversityDescriptor:
    semantic_hash: str
    family: str
    entry_bucket: int
    observation_minutes: tuple[int, ...]
    thresholds: tuple[float, ...]
    structure_helper: str
    width: float
    otm_offset: float

    @property
    def cluster_key(self) -> tuple[str, int, str]:
        return (self.family, self.entry_bucket, self.structure_helper)


def _finite_number(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HypothesisError(f"{name} must be a finite number")
    value = float(value)
    if not math.isfinite(value) or abs(value) > MAX_ABS_THRESHOLD:
        raise HypothesisError(f"{name} is outside the supported finite range")
    if positive and value <= 0:
        raise HypothesisError(f"{name} must be positive")
    return value


def _canonical_number(value: float) -> int | float:
    """Use one JSON representation for numerically equal finite values."""
    return int(value) if value.is_integer() else value


def _minute(value: Any, name: str, *, entry: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise HypothesisError(f"{name} must be an integer minute")
    if not MINUTE_MIN <= value <= entry:
        raise HypothesisError(f"{name} must be between market open and entry")
    if value % MINUTE_STEP:
        raise HypothesisError(f"{name} must be on the supported 5-minute grid")
    return value


def _validate_observation(node: Mapping[str, Any], entry: int) -> None:
    obs = node.get("obs")
    if obs not in SUPPORTED_OBSERVATIONS:
        raise HypothesisError(f"unsupported observation: {obs!r}")
    if obs == "return_from_open":
        _minute(node.get("minute"), "minute", entry=entry)
        allowed = {"obs", "minute"}
    elif obs == "return_between":
        start = _minute(node.get("start_minute"), "start_minute", entry=entry)
        end = _minute(node.get("end_minute"), "end_minute", entry=entry)
        if start >= end:
            raise HypothesisError("return_between requires start_minute < end_minute")
        allowed = {"obs", "start_minute", "end_minute"}
    else:
        after = _minute(node.get("after_minute"), "after_minute", entry=entry)
        if after >= entry:
            raise HypothesisError("first_touch_before_entry requires after_minute < entry")
        _finite_number(node.get("open_offset"), "open_offset")
        allowed = {"obs", "after_minute", "open_offset"}
    if set(node) != allowed:
        raise HypothesisError(f"unexpected fields for {obs}")


def _validate_expression(node: Any, entry: int, depth: int, count: list[int]) -> None:
    if depth > MAX_DEPTH or not isinstance(node, Mapping):
        raise HypothesisError("signal expression is malformed or too deeply nested")
    op = node.get("op")
    if op in {"and", "or"}:
        args = node.get("args")
        if (set(node) != {"op", "args"} or not isinstance(args, list)
                or not 2 <= len(args) <= MAX_CONDITIONS):
            raise HypothesisError(f"{op} requires two to {MAX_CONDITIONS} args")
        for arg in args:
            _validate_expression(arg, entry, depth + 1, count)
        return
    if op == "not":
        if set(node) != {"op", "arg"}:
            raise HypothesisError("not requires exactly one arg")
        _validate_expression(node["arg"], entry, depth + 1, count)
        return
    if op in {"lt", "lte", "gt", "gte"}:
        if set(node) != {"op", "observation", "value"}:
            raise HypothesisError(f"{op} requires observation and value")
        if not isinstance(node["observation"], Mapping):
            raise HypothesisError("observation must be an object")
        _validate_observation(node["observation"], entry)
        if node["observation"].get("obs") == "first_touch_before_entry":
            raise HypothesisError("first_touch_before_entry is Boolean and cannot be compared")
        _finite_number(node["value"], "comparison value")
        count[0] += 1
    elif op == "observed":
        if set(node) != {"op", "observation"} or not isinstance(node["observation"], Mapping):
            raise HypothesisError("observed requires one observation")
        _validate_observation(node["observation"], entry)
        if node["observation"].get("obs") != "first_touch_before_entry":
            raise HypothesisError("observed is only supported for first-touch observations")
        count[0] += 1
    else:
        raise HypothesisError(f"unsupported expression node: {op!r}")
    if count[0] > MAX_CONDITIONS:
        raise HypothesisError("signal has too many conditions")


def _normalize_observation(node: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(node)
    if "open_offset" in normalized:
        normalized["open_offset"] = _canonical_number(float(normalized["open_offset"]))
    return normalized


def _normalize_expression(node: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize Boolean identities and reject scientifically redundant children."""
    op = node["op"]
    if op in {"and", "or"}:
        args: list[dict[str, Any]] = []
        for arg in node["args"]:
            normalized_arg = _normalize_expression(arg)
            if normalized_arg["op"] == op:
                args.extend(normalized_arg["args"])
            else:
                args.append(normalized_arg)
        keyed_args = [
            (json.dumps(arg, sort_keys=True, separators=(",", ":"), allow_nan=False), arg)
            for arg in args
        ]
        keyed_args.sort(key=lambda item: item[0])
        if any(left[0] == right[0] for left, right in pairwise(keyed_args)):
            raise HypothesisError(f"{op} contains a duplicate child")
        return {"op": op, "args": [arg for _, arg in keyed_args]}
    if op == "not":
        normalized_arg = _normalize_expression(node["arg"])
        if normalized_arg["op"] == "not":
            return normalized_arg["arg"]
        if normalized_arg["op"] in {"and", "or"}:
            dual = "or" if normalized_arg["op"] == "and" else "and"
            return _normalize_expression({
                "op": dual,
                "args": [
                    {"op": "not", "arg": arg}
                    for arg in normalized_arg["args"]
                ],
            })
        return {"op": op, "arg": normalized_arg}
    normalized = {"op": op, "observation": _normalize_observation(node["observation"])}
    if "value" in node:
        normalized["value"] = _canonical_number(float(node["value"]))
    return normalized


def parse_hypothesis(document: Mapping[str, Any]) -> Hypothesis:
    """Parse and fully validate an untrusted v3 hypothesis document."""
    if not isinstance(document, Mapping) or set(document) != {
        "schema_version", "entry_minute", "signal", "structure"
    }:
        raise HypothesisError("hypothesis must contain exactly the v3 contract fields")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise HypothesisError("unsupported hypothesis schema version")
    entry = _minute(document.get("entry_minute"), "entry_minute", entry=MINUTE_MAX)
    count = [0]
    _validate_expression(document.get("signal"), entry, 1, count)
    structure = document.get("structure")
    if not isinstance(structure, Mapping) or set(structure) != {"helper", "width", "otm_offset"}:
        raise HypothesisError("structure must contain helper, width, and otm_offset")
    if structure.get("helper") not in SUPPORTED_STRUCTURES:
        raise HypothesisError("unsupported defined-risk structure")
    width = _finite_number(structure.get("width"), "width", positive=True)
    offset = _finite_number(structure.get("otm_offset"), "otm_offset")
    if width > 20 or abs(offset) > 20:
        raise HypothesisError("structure dimensions exceed v3 bounds")
    canonical_structure = {
        "helper": structure["helper"],
        "width": _canonical_number(width),
        "otm_offset": _canonical_number(offset),
    }
    return Hypothesis(
        SCHEMA_VERSION,
        entry,
        _normalize_expression(document["signal"]),
        canonical_structure,
    )


def _normalized_hypothesis(hypothesis: Hypothesis | Mapping[str, Any]) -> Hypothesis:
    if isinstance(hypothesis, Hypothesis):
        hypothesis = {
            "schema_version": hypothesis.schema_version,
            "entry_minute": hypothesis.entry_minute,
            "signal": hypothesis.signal,
            "structure": hypothesis.structure,
        }
    return parse_hypothesis(hypothesis)


def canonical_hypothesis(hypothesis: Hypothesis | Mapping[str, Any]) -> str:
    h = _normalized_hypothesis(hypothesis)
    document = {
        "entry_minute": h.entry_minute,
        "schema_version": h.schema_version,
        "signal": h.signal,
        "structure": h.structure,
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)


def hypothesis_hash(hypothesis: Hypothesis | Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_hypothesis(hypothesis).encode()).hexdigest()


def _observe(node: Mapping[str, Any], session: CandidateSessionView, entry: int) -> float | bool | None:
    obs = node["obs"]
    if obs == "return_from_open":
        return session.ret_from_open(node["minute"])
    if obs == "return_between":
        start = session.price_at(node["start_minute"])
        end = session.price_at(node["end_minute"])
        return None if start is None or end is None else (end / start - 1.0) * 100.0
    touch = session.first_touch_from_open(node["open_offset"], node["after_minute"])
    return touch is not None and touch < entry


def _evaluate(node: Mapping[str, Any], session: CandidateSessionView, entry: int) -> bool:
    op = node["op"]
    if op == "and":
        return all(_evaluate(arg, session, entry) for arg in node["args"])
    if op == "or":
        return any(_evaluate(arg, session, entry) for arg in node["args"])
    if op == "not":
        return not _evaluate(node["arg"], session, entry)
    observed = _observe(node["observation"], session, entry)
    if op == "observed":
        return observed is True
    if observed is None or isinstance(observed, bool):
        return False
    value = node["value"]
    return {"lt": observed < value, "lte": observed <= value,
            "gt": observed > value, "gte": observed >= value}[op]


def interpret_signal(hypothesis: Hypothesis, session: CandidateSessionView) -> bool:
    return _evaluate(hypothesis.signal, session, hypothesis.entry_minute)


def make_structure(hypothesis: Hypothesis, entry_price: float) -> Structure:
    if not math.isfinite(entry_price) or entry_price <= 0:
        raise HypothesisError("entry price must be finite and positive")
    spec = hypothesis.structure
    factory = call_debit_spread if spec["helper"] == "call_debit_spread" else put_credit_spread
    return factory(entry_price, spec["width"], spec["otm_offset"])


def _walk_descriptor(node: Mapping[str, Any], operations: list[str], minutes: list[int],
                     thresholds: list[float], shape: list[str]) -> None:
    op = node["op"]
    shape.append(op)
    if op in {"and", "or"}:
        for arg in node["args"]:
            _walk_descriptor(arg, operations, minutes, thresholds, shape)
        return
    if op == "not":
        _walk_descriptor(node["arg"], operations, minutes, thresholds, shape)
        return
    observation = node["observation"]
    operations.append(observation["obs"])
    for key in ("minute", "start_minute", "end_minute", "after_minute"):
        if key in observation:
            minutes.append(observation[key])
    if "value" in node:
        thresholds.append(float(node["value"]))
    if "open_offset" in observation:
        thresholds.append(float(observation["open_offset"]))


def describe_hypothesis(hypothesis: Hypothesis | Mapping[str, Any]) -> DiversityDescriptor:
    h = _normalized_hypothesis(hypothesis)
    operations: list[str] = []
    minutes: list[int] = []
    thresholds: list[float] = []
    shape: list[str] = []
    _walk_descriptor(h.signal, operations, minutes, thresholds, shape)
    family = f"{'/'.join(shape)}:{'+'.join(sorted(operations))}"
    return DiversityDescriptor(
        semantic_hash=hypothesis_hash(h),
        family=family,
        entry_bucket=(h.entry_minute // 30) * 30,
        observation_minutes=tuple(sorted(set(minutes))),
        thresholds=tuple(thresholds),
        structure_helper=str(h.structure["helper"]),
        width=float(h.structure["width"]),
        otm_offset=float(h.structure["otm_offset"]),
    )


def is_near_duplicate(a: DiversityDescriptor, b: DiversityDescriptor,
                      *, threshold_radius: float = 0.10,
                      width_radius: float = 0.50,
                      offset_radius: float = 0.25) -> bool:
    """True only for one small numeric permutation of the same research idea."""
    if threshold_radius <= 0 or width_radius <= 0 or offset_radius <= 0:
        raise ValueError("near-duplicate radii must be positive")
    if a.semantic_hash == b.semantic_hash:
        return True
    if (a.cluster_key != b.cluster_key or a.observation_minutes != b.observation_minutes
            or len(a.thresholds) != len(b.thresholds)):
        return False
    differences = [
        abs(x - y) / threshold_radius
        for x, y in zip(a.thresholds, b.thresholds) if x != y
    ]
    if a.width != b.width:
        differences.append(abs(a.width - b.width) / width_radius)
    if a.otm_offset != b.otm_offset:
        differences.append(abs(a.otm_offset - b.otm_offset) / offset_radius)
    return len(differences) == 1 and differences[0] < 1.0


def family_is_admissible(candidate: DiversityDescriptor,
                         admitted: list[DiversityDescriptor], *, quota: int = 2,
                         required_families: frozenset[str] = frozenset()) -> bool:
    """Deterministic family quota helper for the campaign admission layer."""
    counts = {family: 0 for family in required_families}
    for item in admitted:
        counts[item.family] = counts.get(item.family, 0) + 1
    missing = {family for family in required_families if counts[family] == 0}
    if missing and candidate.family not in missing:
        return False
    return counts.get(candidate.family, 0) < quota
