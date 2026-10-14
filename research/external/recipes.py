"""Admission rules for declarative portfolio recipes.

The external evaluator owns the recipe *vocabulary* (and rejects anything
outside it). This module owns *admission*, independently of that vocabulary:
it only needs the shape every recipe has -- a tree of ``{"op": ..., ...}``
nodes whose child nodes are objects, whose numeric fields are parameters and
whose string fields are identities (such as an observation name).

* exact duplicates share a canonical hash;
* a near duplicate has the same tree, operators and identities as an admitted
  recipe and differs in exactly one number by less than 25% (one knob turned);
* a recipe's family is its top-level operator plus the set of identities it
  reads (``neg:ret`` and ``xscore:ret`` are different ideas on the same
  input); a campaign admits at most ``quota`` recipes per family.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

MAX_JSON_BYTES = 8_192
MAX_DEPTH = 8
MAX_NODES = 32
NEAR_DUPLICATE_RADIUS = 0.25


class RecipeShapeError(ValueError):
    """Not a recipe-shaped document; rejected before it reaches an evaluator."""


@dataclass(frozen=True)
class RecipeDescriptor:
    semantic_hash: str
    structure_hash: str           # tree with every number blanked
    family: str                   # root op + sorted identities, e.g. "xscore:carry+ret"
    numbers: tuple[float, ...]    # parameters in canonical traversal order


def canonical_json(document: Mapping[str, Any]) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)


def semantic_hash(document: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(document).encode()).hexdigest()


def _walk(node: Any, depth: int, count: list[int], numbers: list[float],
          identities: set[str]) -> Any:
    if depth > MAX_DEPTH:
        raise RecipeShapeError(f"deeper than {MAX_DEPTH}")
    if not isinstance(node, dict) or not isinstance(node.get("op"), str):
        raise RecipeShapeError("every node must be an object with a string 'op'")
    count[0] += 1
    if count[0] > MAX_NODES:
        raise RecipeShapeError(f"more than {MAX_NODES} nodes")
    blank: dict[str, Any] = {}
    for key in sorted(node):
        value = node[key]
        if key == "op":
            blank[key] = value
        elif isinstance(value, dict):
            blank[key] = _walk(value, depth + 1, count, numbers, identities)
        elif isinstance(value, bool):
            raise RecipeShapeError(f"{key}: booleans are not recipe parameters")
        elif isinstance(value, (int, float)):
            if not math.isfinite(value):
                raise RecipeShapeError(f"{key}: non-finite number")
            numbers.append(float(value))
            blank[key] = "#"
        elif isinstance(value, str):
            identities.add(value)
            blank[key] = value
        else:
            raise RecipeShapeError(f"{key}: unsupported value type {type(value).__name__}")
    return blank


def describe(document: Mapping[str, Any]) -> RecipeDescriptor:
    if not isinstance(document, dict) or set(document) != {"schema", "signal"}:
        raise RecipeShapeError("a recipe has exactly 'schema' and 'signal'")
    if not isinstance(document["schema"], str):
        raise RecipeShapeError("schema must be a string")
    text = canonical_json(document)
    if len(text.encode()) > MAX_JSON_BYTES:
        raise RecipeShapeError(f"recipe exceeds {MAX_JSON_BYTES} bytes")
    numbers: list[float] = []
    identities: set[str] = set()
    blank = _walk(document["signal"], 1, [0], numbers, identities)
    structure = hashlib.sha256(
        canonical_json({"schema": document["schema"], "signal": blank}).encode()).hexdigest()
    family = document["signal"]["op"] + ":" + ("+".join(sorted(identities)) or "none")
    return RecipeDescriptor(semantic_hash(document), structure, family, tuple(numbers))


def is_near_duplicate(a: RecipeDescriptor, b: RecipeDescriptor,
                      radius: float = NEAR_DUPLICATE_RADIUS) -> bool:
    if a.semantic_hash == b.semantic_hash:
        return True
    if a.structure_hash != b.structure_hash or len(a.numbers) != len(b.numbers):
        return False
    changed = [(x, y) for x, y in zip(a.numbers, b.numbers) if x != y]
    if len(changed) != 1:
        return False
    x, y = changed[0]
    scale = max(abs(x), abs(y))
    return scale > 0 and abs(x - y) / scale < radius


def admission_reason(candidate: RecipeDescriptor, admitted: Sequence[RecipeDescriptor],
                     quota: int) -> str | None:
    """None if admissible, else the reason it is rejected (no trial is spent)."""
    for prior in admitted:
        if prior.semantic_hash == candidate.semantic_hash:
            return "duplicate"
        if is_near_duplicate(candidate, prior):
            return "near_duplicate"
    if sum(prior.family == candidate.family for prior in admitted) >= quota:
        return "family_quota"
    return None
