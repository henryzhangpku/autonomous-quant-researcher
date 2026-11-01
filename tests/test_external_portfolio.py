"""The external portfolio evaluator boundary, exercised with a fake evaluator."""

import json
import sys
import textwrap
from datetime import date, timedelta

import pytest

from research.external.portfolio import (ExternalEvaluatorError, ExternalEvaluatorSpec, Refusal,
                                         canonical_recipe_hash, evaluate_portfolio_stage,
                                         run_external)
from research.prereg.gates import StageVerdict

RECIPE = {"schema": "example/recipe/1", "signal": {"op": "obs", "name": "ret"}}
CODE_HASH = "c" * 64

FAKE = textwrap.dedent('''
    import argparse, hashlib, json, os, sys
    from datetime import date, timedelta
    p = argparse.ArgumentParser()
    p.add_argument("--mode"); p.add_argument("--recipe"); p.add_argument("--config")
    p.add_argument("--stage-start"); p.add_argument("--stage-end")
    p.add_argument("--base-bps"); p.add_argument("--elevated-bps")
    p.add_argument("--risk-target"); p.add_argument("--out")
    a = p.parse_args()
    recipe = json.loads(sys.stdin.read())
    rh = hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    m = a.mode
    result = {"evaluator": {"name": "fake", "version": "1",
                            "code_hash": "d" * 64 if m == "wrong_hash" else "''' + CODE_HASH + '''"},
              "stage": {"start": a.stage_start, "end": "2099-01-01" if m == "wrong_stage" else a.stage_end},
              "recipe_hash": "0" * 64 if m == "wrong_recipe" else rh,
              "env_has_alpaca": "ALPACA_API_KEY" in os.environ}
    if m == "refuse":
        result["refusal"] = {"reason": "recipe_invalid", "detail": "unknown operator"}
    else:
        start = date.fromisoformat(a.stage_start)
        n = 20 if m == "few" else 150
        weeks = []
        for i in range(n):
            base = 0.002 + 0.001 * ((i % 5) - 2)
            elev = base - 0.0002
            if m == "elevated_above" and i == 3:
                elev = base + 0.01
            weeks.append({"week": str(start + timedelta(days=4 + 7 * i)), "base": base,
                          "elevated": float("nan") if (m == "nan" and i == 7) else elev})
        if m == "outside":
            weeks.append({"week": "2099-12-31", "base": 0.0, "elevated": 0.0})
        result["weeks"] = weeks
    open(a.out, "w").write(json.dumps(result))
    sys.exit(2 if "refusal" in result else 0)
''')


@pytest.fixture
def spec_for(tmp_path):
    script = tmp_path / "fake_evaluator.py"
    script.write_text(FAKE)

    def make(mode="good"):
        return ExternalEvaluatorSpec(name="fake", version="1", code_hash=CODE_HASH,
                                     command=(sys.executable, str(script), "--mode", mode),
                                     cwd=str(tmp_path), universe="eq_mini",
                                     base_bps=5.0, elevated_bps=10.0)
    return make


def test_verified_evidence_is_gated_as_an_always_on_book(spec_for):
    report, result = evaluate_portfolio_stage(spec_for(), RECIPE, "discovery", campaign="t")
    assert report.verdict is StageVerdict.PASSED
    assert {g.name for g in report.gates} >= {"mean_positive", "bootstrap_lower_bound_positive",
                                              "double_cost_positive"}
    again, _ = evaluate_portfolio_stage(spec_for(), RECIPE, "discovery", campaign="t")
    assert again == report  # deterministic, including the bootstrap seed


@pytest.mark.parametrize("mode", ["wrong_hash", "wrong_stage", "wrong_recipe", "nan",
                                  "outside", "elevated_above"])
def test_untrustworthy_results_fail_closed(spec_for, mode):
    with pytest.raises(ExternalEvaluatorError):
        evaluate_portfolio_stage(spec_for(mode), RECIPE, "discovery")


def test_refusal_is_typed_not_a_result(spec_for):
    with pytest.raises(Refusal) as exc:
        run_external(spec_for("refuse"), RECIPE, "2016-01-01", "2021-12-31")
    assert exc.value.reason == "recipe_invalid"


def test_too_few_weeks_is_untestable_not_failed(spec_for):
    report, _ = evaluate_portfolio_stage(spec_for("few"), RECIPE, "validation")
    assert report.verdict is StageVerdict.UNTESTABLE


def test_evaluator_runs_without_credentials(spec_for, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "should-not-leak")
    result = run_external(spec_for(), RECIPE, "2016-01-01", "2021-12-31")
    assert result["env_has_alpaca"] is False


def test_holdout_needs_an_end_date_from_its_receipt(spec_for):
    with pytest.raises(ValueError, match="receipt"):
        evaluate_portfolio_stage(spec_for(), RECIPE, "holdout")


def test_spec_hash_pins_costs_and_universe(spec_for):
    a = spec_for()
    b = ExternalEvaluatorSpec(**{**a.__dict__, "base_bps": 4.0})
    assert a.contract_hash() != b.contract_hash()
    assert canonical_recipe_hash(RECIPE) == canonical_recipe_hash(json.loads(json.dumps(RECIPE)))
