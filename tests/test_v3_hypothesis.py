from __future__ import annotations

import json
import math

import pytest

from research.backtest.data import CandidateSessionView, Session
from research.v3.campaign import FrozenCandidate
from research.v3.hypothesis import (
    HypothesisError,
    canonical_hypothesis,
    describe_hypothesis,
    family_is_admissible,
    hypothesis_hash,
    interpret_signal,
    is_near_duplicate,
    make_structure,
    parse_hypothesis,
)


def document(*, threshold: float = -0.5, entry: int = 600,
             helper: str = "call_debit_spread") -> dict:
    return {
        "schema_version": 3,
        "entry_minute": entry,
        "signal": {
            "op": "lte",
            "observation": {"obs": "return_from_open", "minute": entry - 5},
            "value": threshold,
        },
        "structure": {"helper": helper, "width": 2.0, "otm_offset": 0.0},
    }


def test_canonical_hash_and_descriptor_are_deterministic() -> None:
    first = document()
    reordered = {key: first[key] for key in reversed(first)}
    assert hypothesis_hash(first) == hypothesis_hash(reordered)
    assert describe_hypothesis(first) == describe_hypothesis(reordered)


def _condition(minute: int, value: float, *, op: str = "lte") -> dict:
    return {
        "op": op,
        "observation": {"obs": "return_from_open", "minute": minute},
        "value": value,
    }


def _assert_semantically_equivalent(first: dict, second: dict) -> None:
    assert canonical_hypothesis(first) == canonical_hypothesis(second)
    assert hypothesis_hash(first) == hypothesis_hash(second)
    first_descriptor = describe_hypothesis(first)
    second_descriptor = describe_hypothesis(second)
    assert first_descriptor == second_descriptor
    assert is_near_duplicate(first_descriptor, second_descriptor)


def test_numeric_spellings_cannot_masquerade_as_novel() -> None:
    integer_spelling = document(threshold=1)
    float_spelling = document(threshold=1.0)
    integer_spelling["structure"] = {"helper": "call_debit_spread", "width": 2,
                                     "otm_offset": 0}

    assert canonical_hypothesis(integer_spelling) == canonical_hypothesis(float_spelling)
    assert hypothesis_hash(integer_spelling) == hypothesis_hash(float_spelling)
    assert describe_hypothesis(integer_spelling) == describe_hypothesis(float_spelling)
    assert is_near_duplicate(
        describe_hypothesis(integer_spelling), describe_hypothesis(float_spelling)
    )


def test_commutative_argument_permutations_have_one_semantic_identity() -> None:
    first = document()
    first["signal"] = {
        "op": "and",
        "args": [_condition(590, -1), _condition(595, 1, op="gte")],
    }
    permuted = document()
    permuted["signal"] = {"op": "and", "args": list(reversed(first["signal"]["args"]))}

    assert canonical_hypothesis(first) == canonical_hypothesis(permuted)
    assert hypothesis_hash(first) == hypothesis_hash(permuted)
    assert describe_hypothesis(first) == describe_hypothesis(permuted)


def test_nested_commutative_permutations_have_one_semantic_identity() -> None:
    left = _condition(585, -2)
    middle = _condition(590, -1)
    right = _condition(595, 1, op="gte")
    first = document()
    first["signal"] = {
        "op": "or",
        "args": [{"op": "and", "args": [left, middle]}, right],
    }
    permuted = document()
    permuted["signal"] = {
        "op": "or",
        "args": [right, {"op": "and", "args": [middle, left]}],
    }

    assert canonical_hypothesis(first) == canonical_hypothesis(permuted)
    assert hypothesis_hash(first) == hypothesis_hash(permuted)
    assert describe_hypothesis(first) == describe_hypothesis(permuted)


def test_duplicate_commutative_child_is_rejected_after_normalization() -> None:
    value = document()
    value["signal"] = {
        "op": "and",
        "args": [_condition(595, 1), _condition(595, 1.0)],
    }

    with pytest.raises(HypothesisError, match="duplicate child"):
        parse_hypothesis(value)


def test_associative_and_groupings_have_one_semantic_identity() -> None:
    a = _condition(585, -2)
    b = _condition(590, -1)
    c = _condition(595, 1, op="gte")
    right_nested = document()
    right_nested["signal"] = {"op": "and", "args": [a, {"op": "and", "args": [b, c]}]}
    left_nested = document()
    left_nested["signal"] = {"op": "and", "args": [{"op": "and", "args": [a, b]}, c]}

    _assert_semantically_equivalent(right_nested, left_nested)


def test_associative_or_groupings_have_one_semantic_identity() -> None:
    a = _condition(585, -2)
    b = _condition(590, -1)
    c = _condition(595, 1, op="gte")
    right_nested = document()
    right_nested["signal"] = {"op": "or", "args": [a, {"op": "or", "args": [b, c]}]}
    left_nested = document()
    left_nested["signal"] = {"op": "or", "args": [{"op": "or", "args": [a, b]}, c]}

    _assert_semantically_equivalent(right_nested, left_nested)


def test_double_negation_has_the_identity_of_its_child() -> None:
    base = document()
    double_negated = document()
    double_negated["signal"] = {
        "op": "not",
        "arg": {"op": "not", "arg": base["signal"]},
    }

    _assert_semantically_equivalent(base, double_negated)


def test_de_morgan_and_has_the_identity_of_distributed_or() -> None:
    a = _condition(585, -2)
    b = _condition(590, -1)
    c = _condition(595, 1, op="gte")
    negated_group = document()
    negated_group["signal"] = {
        "op": "not",
        "arg": {"op": "and", "args": [a, {"op": "and", "args": [b, c]}]},
    }
    distributed = document()
    distributed["signal"] = {
        "op": "or",
        "args": [
            {"op": "not", "arg": c},
            {"op": "or", "args": [
                {"op": "not", "arg": a},
                {"op": "not", "arg": b},
            ]},
        ],
    }

    _assert_semantically_equivalent(negated_group, distributed)


def test_de_morgan_or_has_the_identity_of_distributed_and() -> None:
    a = _condition(585, -2)
    b = _condition(590, -1)
    c = _condition(595, 1, op="gte")
    negated_group = document()
    negated_group["signal"] = {
        "op": "not",
        "arg": {"op": "or", "args": [a, {"op": "or", "args": [b, c]}]},
    }
    distributed = document()
    distributed["signal"] = {
        "op": "and",
        "args": [
            {"op": "not", "arg": c},
            {"op": "and", "args": [
                {"op": "not", "arg": a},
                {"op": "not", "arg": b},
            ]},
        ],
    }

    _assert_semantically_equivalent(negated_group, distributed)


def test_de_morgan_equivalents_reject_redundant_children() -> None:
    a = _condition(590, -1)
    negated_group = document()
    negated_group["signal"] = {
        "op": "not",
        "arg": {"op": "and", "args": [
            a,
            {"op": "not", "arg": {"op": "not", "arg": dict(a)}},
        ]},
    }
    distributed = document()
    distributed["signal"] = {
        "op": "or",
        "args": [
            {"op": "not", "arg": a},
            {"op": "not", "arg": dict(a)},
        ],
    }

    for value in (negated_group, distributed):
        with pytest.raises(HypothesisError, match="duplicate child"):
            parse_hypothesis(value)


def test_duplicate_child_is_rejected_after_associative_flattening() -> None:
    a = _condition(590, -1)
    value = document()
    value["signal"] = {
        "op": "and",
        "args": [a, {"op": "and", "args": [_condition(595, 1), dict(a)]}],
    }

    with pytest.raises(HypothesisError, match="duplicate child"):
        parse_hypothesis(value)


def test_flattened_canonical_document_reparses_and_validates_when_frozen() -> None:
    conditions = [_condition(minute, minute - 590) for minute in range(575, 600, 5)]
    value = document()
    value["signal"] = {
        "op": "and",
        "args": [
            conditions[0],
            {"op": "and", "args": [
                conditions[1],
                {"op": "and", "args": [
                    conditions[2],
                    {"op": "and", "args": conditions[3:]},
                ]},
            ]},
        ],
    }
    source = canonical_hypothesis(value)
    reparsed = parse_hypothesis(json.loads(source))
    assert canonical_hypothesis(reparsed) == source
    FrozenCandidate(
        canonical_document=source,
        semantic_hash=hypothesis_hash(value),
        source_hash="0" * 64,
        policy_hash="0" * 64,
        evaluator_version="test",
        stage_manifest_hashes={},
        descriptor={},
    ).validate()


def test_interpreter_is_causal_and_builds_only_approved_structure() -> None:
    session = Session("2020-01-02", ((570, 100, 100, 100, 100), (595, 99, 99, 98, 99),
                                      (605, 110, 110, 110, 110)))
    hypothesis = parse_hypothesis(document())
    view = CandidateSessionView.at(session, 600)
    assert interpret_signal(hypothesis, view) is True
    structure = make_structure(hypothesis, 99)
    assert structure.width == 2
    assert all(leg.cp == "C" for leg in structure.legs)


def test_near_duplicate_rejects_only_one_subradius_numeric_change() -> None:
    base = describe_hypothesis(document(threshold=-0.50))
    near = describe_hypothesis(document(threshold=-0.55))
    material = describe_hypothesis(document(threshold=-0.75))
    other_entry = describe_hypothesis(document(threshold=-0.55, entry=660))
    other_structure = describe_hypothesis(document(threshold=-0.55, helper="put_credit_spread"))
    assert is_near_duplicate(base, near)
    assert not is_near_duplicate(base, material)
    assert not is_near_duplicate(base, other_entry)
    assert not is_near_duplicate(base, other_structure)


def test_minor_structure_permutation_and_family_balance_helpers() -> None:
    base_doc = document()
    width_doc = document()
    width_doc["structure"]["width"] = 2.25
    base = describe_hypothesis(base_doc)
    near_width = describe_hypothesis(width_doc)
    other_doc = document()
    other_doc["signal"]["observation"] = {
        "obs": "return_between", "start_minute": 570, "end_minute": 595,
    }
    other = describe_hypothesis(other_doc)
    assert is_near_duplicate(base, near_width)
    assert not family_is_admissible(
        base, [base], required_families=frozenset({base.family, other.family})
    )
    assert family_is_admissible(
        other, [base], required_families=frozenset({base.family, other.family})
    )


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(schema_version=2),
    lambda d: d["signal"].update(op="exec"),
    lambda d: d["signal"].update(value=math.nan),
    lambda d: d["signal"]["observation"].update(obs="future_close"),
    lambda d: d["signal"]["observation"].update(minute=601),
    lambda d: d.update(entry_minute=960),
])
def test_parser_fails_closed_for_unsupported_or_noncausal_documents(mutation) -> None:
    value = document()
    mutation(value)
    with pytest.raises(HypothesisError):
        parse_hypothesis(value)


def test_first_touch_in_entry_bar_is_not_causally_observed() -> None:
    value = document()
    value["signal"] = {
        "op": "observed",
        "observation": {
            "obs": "first_touch_before_entry", "after_minute": 570,
            "open_offset": 1.0,
        },
    }
    hypothesis = parse_hypothesis(value)
    entry_bar_touch = Session(
        "2020-01-02",
        ((570, 100, 100, 100, 100), (595, 100, 100.5, 99, 100),
         (600, 100, 101.5, 99, 101)),
    )
    assert interpret_signal(hypothesis, CandidateSessionView.at(entry_bar_touch, 600)) is False
    prior_touch = Session(
        "2020-01-03",
        ((570, 100, 100, 100, 100), (595, 100, 101.5, 99, 101),
         (600, 101, 101, 100, 101)),
    )
    assert interpret_signal(hypothesis, CandidateSessionView.at(prior_touch, 600)) is True


def test_negative_first_touch_uses_bar_low_for_dip_direction() -> None:
    value = document()
    value["signal"] = {
        "op": "observed",
        "observation": {
            "obs": "first_touch_before_entry", "after_minute": 570,
            "open_offset": -1.0,
        },
    }
    hypothesis = parse_hypothesis(value)
    dipped = Session(
        "2020-01-06",
        ((570, 100, 100, 100, 100), (595, 100, 100.5, 98.5, 99),
         (600, 99, 99.5, 99, 99)),
    )
    assert interpret_signal(hypothesis, CandidateSessionView.at(dipped, 600)) is True
    high_only = Session(
        "2020-01-07",
        ((570, 100, 100, 100, 100), (595, 100, 102, 99.5, 101),
         (600, 101, 101, 100, 101)),
    )
    assert interpret_signal(hypothesis, CandidateSessionView.at(high_only, 600)) is False
