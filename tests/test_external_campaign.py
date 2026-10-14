"""Portfolio-recipe campaigns end to end, against a fake external evaluator."""

import json
import sys
import textwrap
from pathlib import Path

import pytest

from research.external import cli
from research.external.campaign import (BudgetExhausted, CampaignError, ContractMismatch,
                                        PortfolioCampaign, PortfolioCampaignContract)
from research.external.portfolio import ExternalEvaluatorSpec
from research.external.proposer import ProposalError, parse_proposal
from research.prereg.ledger import PreregistrationError

CODE_HASH = "e" * 64
# The fake reads the observation name: "alpha" pays in every stage, "decay" only
# in discovery, "noise" never; "boom" crashes; "bad" is refused as outside the
# vocabulary.
FAKE = textwrap.dedent('''
    import argparse, hashlib, json, math, sys
    from datetime import date, timedelta
    p = argparse.ArgumentParser()
    p.add_argument("--describe", action="store_true")
    for k in ("recipe", "config", "stage-start", "stage-end", "base-bps", "elevated-bps",
              "risk-target", "out"):
        p.add_argument("--" + k)
    a = p.parse_args()
    ident = {"name": "fake", "version": "1", "code_hash": "''' + CODE_HASH + '''"}
    if a.describe:
        print(json.dumps({"evaluator": ident, "vocabulary": {"schema": "example/recipe/1",
              "observations": {"alpha": "", "decay": "", "noise": ""}}}))
        sys.exit(0)
    recipe = json.loads(sys.stdin.read())
    canon = json.dumps(recipe, sort_keys=True, separators=(",", ":"))
    def find(node):
        return node.get("name") or find(node.get("x", {}))
    name = find(recipe["signal"])
    if name == "boom":
        sys.exit(1)
    out = {"evaluator": ident, "stage": {"start": a.stage_start, "end": a.stage_end},
           "recipe_hash": hashlib.sha256(canon.encode()).hexdigest(), "data_hash": "d" * 64,
           "summary": {"ir_base": 1.0}}
    if name == "bad":
        out["refusal"] = {"reason": "recipe_invalid", "detail": "unknown observation"}
    else:
        start, end = date.fromisoformat(a.stage_start), date.fromisoformat(a.stage_end)
        discovery = start.year < 2022
        drift = {"alpha": 0.004, "decay": 0.004 if discovery else -0.003, "noise": -0.001}
        drift = drift.get(name, -0.001)
        weeks, d, i = [], start + timedelta(days=(4 - start.weekday()) % 7), 0
        while d <= end:
            base = drift + 0.002 * math.sin(i * 1.7)
            weeks.append({"week": str(d), "base": base, "elevated": base - 0.0002})
            d += timedelta(days=7); i += 1
        out["weeks"] = weeks
    open(a.out, "w").write(json.dumps(out))
    sys.exit(2 if "refusal" in out else 0)
''')


def recipe(name, halflife=20):
    return {"schema": "example/recipe/1",
            "signal": {"op": "ewm", "halflife": halflife, "x": {"op": "obs", "name": name}}}


def proposal(name, halflife=20):
    return {"recipe": recipe(name, halflife), "rationale": f"{name} at {halflife}"}


@pytest.fixture
def make_campaign(tmp_path):
    script = tmp_path / "fake.py"
    script.write_text(FAKE)
    spec = ExternalEvaluatorSpec(name="fake", version="1", code_hash=CODE_HASH,
                                 command=(sys.executable, str(script)), cwd=str(tmp_path),
                                 universe="u", base_bps=5.0, elevated_bps=10.0)

    def make(budget=6, **kw):
        contract = PortfolioCampaignContract(campaign_id="c1", mission="find durable edges",
                                             evaluator=spec, vocabulary_hash="a" * 64,
                                             discovery_budget=budget, **kw)
        return PortfolioCampaign(tmp_path / "campaign", contract)
    return make


def types(campaign):
    return [e["type"] for e in campaign.events()]


def test_full_lifecycle_promotes_only_the_durable_edge(make_campaign):
    c = make_campaign()
    for name in ("alpha", "decay", "noise"):
        c.submit(proposal(name))
    assert c.state()["trials"] == 3
    # duplicates, one-knob variants and over-represented families spend no trial
    c.submit(proposal("alpha"))
    c.submit(proposal("alpha", halflife=22))
    c.submit(proposal("noise", halflife=60))   # family "ewm:noise" now holds 2
    c.submit(proposal("noise", halflife=200))  # quota of 2 reached
    rejected = [e["payload"]["reason"] for e in c.events() if e["type"] == "admission_rejected"]
    assert rejected == ["duplicate", "near_duplicate", "family_quota"]
    assert c.state()["trials"] == 4

    c.close_discovery()
    shortlist = {x["family"] for x in c.state()["shortlist"]}
    assert shortlist == {"ewm:alpha", "ewm:decay"}
    with pytest.raises(CampaignError):
        c.submit(proposal("alpha", halflife=300))  # discovery is closed

    c.run_validation()
    state = c.state()
    verdicts = {v["semantic_hash"]: v["verdict"] for v in state["validation"].values()}
    assert sorted(verdicts.values()) == ["failed", "passed"]
    assert state["phase"] == "holdout_ready"
    assert state["finalist"]["family"] == "ewm:alpha"
    gate_names = {g["name"] for v in state["validation"].values() for g in v["gates"]}
    assert {"bootstrap_lower_bound_bonferroni", "year_2022_positive"} <= gate_names

    with pytest.raises(CampaignError, match="one-use"):
        c.consume_holdout(end="2025-12-31")
    c.consume_holdout(end="2025-12-31", confirm=True)
    assert types(c)[-2:] == ["holdout_receipt", "holdout_result"]
    assert c.state()["holdout"]["verdict"] == "passed"
    with pytest.raises(CampaignError):
        c.consume_holdout(end="2025-12-31", confirm=True)


def test_crashes_spend_trials_refusals_and_bad_shapes_do_not(make_campaign):
    c = make_campaign()
    c.submit({"recipe": "print('hi')", "rationale": "code is not a recipe"})
    c.submit({"recipe": recipe("alpha"), "rationale": "x", "code": "import os"})
    c.submit(proposal("bad"))
    c.submit(proposal("boom"))
    s = c.state()
    assert s["trials"] == 1
    statuses = [r["status"] for r in s["results"].values()]
    assert sorted(statuses) == ["crashed", "formulation_refused"]
    assert types(c).count("formulation_rejected") == 2


def test_budget_exhaustion_pauses_and_is_not_success(make_campaign):
    c = make_campaign(budget=1)
    c.submit(proposal("noise"))
    with pytest.raises(BudgetExhausted):
        c.submit(proposal("alpha"))
    c.close_discovery()
    assert c.state()["phase"] == "closed"  # nothing passed; no survivor


def test_changed_contract_or_tampered_ledger_fails_closed(make_campaign):
    c = make_campaign()
    c.submit(proposal("alpha"))
    with pytest.raises(ContractMismatch):
        make_campaign(budget=99)
    lines = c.ledger_path.read_text().splitlines()
    lines[1] = lines[1].replace('"alpha"', '"noise"')
    c.ledger_path.write_text("\n".join(lines) + "\n")
    with pytest.raises(PreregistrationError):
        c.state()


def test_cli_init_and_scripted_run(tmp_path):
    script = tmp_path / "fake.py"
    script.write_text(FAKE)
    mission = tmp_path / "mission.md"
    mission.write_text("Find durable cross-sectional edges.")
    proposals = tmp_path / "proposals.json"
    proposals.write_text(json.dumps([proposal("alpha"), proposal("noise"), {"no": "recipe"}]))
    d = tmp_path / "camp"
    assert cli.main(["init", "--dir", str(d), "--campaign-id", "cli", "--mission", str(mission),
                     "--evaluator-cwd", str(tmp_path),
                     "--evaluator-cmd", f"{sys.executable} {script}", "--universe", "u",
                     "--base-bps", "5", "--elevated-bps", "10", "--budget", "5"]) == 0
    assert cli.main(["run", "--dir", str(d), "--scripted", str(proposals),
                     "--max-formulations", "4"]) == 0
    campaign, vocabulary = cli.load(d)
    assert campaign.state()["trials"] == 2
    assert vocabulary["schema"] == "example/recipe/1"
    assert json.loads((d / "contract.json").read_text())["evaluator"]["code_hash"] == CODE_HASH


@pytest.mark.parametrize("text", ['{"recipe": {}}', '{"recipe": {}, "rationale": "", "code": 1}',
                                  'not json', '{"recipe": "x", "rationale": "y"}'])
def test_proposals_are_strict(text):
    with pytest.raises(ProposalError):
        parse_proposal(text)


def test_prompt_examples_are_valid_and_rejections_are_fed_back(make_campaign):
    from research.external.proposer import _examples, build_prompt
    from research.external.recipes import describe
    vocabulary = {"schema": "example/recipe/1", "max_depth": 6, "max_nodes": 16,
                  "observations": {"ret": "daily return"},
                  "operators": {"obs": {"children": [], "params": {"name": {"type": "str", "min": None,
                                                                         "max": None, "choices": ["ret"]}}},
                                "ewm": {"children": ["x"], "params": {"halflife": {"type": "int", "min": 1, "max": 520}}},
                                "xscore": {"children": ["x"], "params": {}},
                                "neg": {"children": ["x"], "params": {}},
                                "rank": {"children": ["x"], "params": {}},
                                "rolling_sum": {"children": ["x"], "params": {"window": {"type": "int", "min": 1, "max": 520}}}}}
    for example in _examples(vocabulary):
        describe(example)  # shape-valid
    c = make_campaign()
    c.submit({"recipe": {"schema": "example/recipe/1", "signal": "ret"},
              "rationale": "bare string input"})
    c.submit(proposal("bad"))  # shape-valid, refused by the evaluator's vocabulary
    feedback = c.rejection_feedback()
    assert [f["kind"] for f in feedback] == ["formulation", "vocabulary"]
    assert '"signal": "ret"' in feedback[0]["excerpt"]
    assert feedback[1]["reason"] == "unknown observation"
    prompt = build_prompt(mission="m", vocabulary=vocabulary, rejections=feedback)
    assert "ewm(x: node, halflife: int 1..520)" in prompt
    assert "never a bare string" in prompt and "bare string input" in prompt
