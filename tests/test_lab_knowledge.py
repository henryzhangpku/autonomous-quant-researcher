"""The knowledge layer: the lab reads its own record before asking again.

The design gap it closes: every compiled mission started blank, so the
proposer re-searched ground the lab had already settled — 27 refuted
campaigns and an exhaustive sweep were invisible at the exact moment the
28th proposal was written.
"""

from __future__ import annotations

import json
from pathlib import Path

from research.lab.knowledge import prior_evidence


def _run(root: Path, name: str, *, status: str, reason: str = "", runs: int = 24,
         best: float = -1.5, question: str = "") -> None:
    d = root / name
    (d / "state").mkdir(parents=True)
    state = {"status": status, "run_count": runs, "best_metric": best}
    if reason:
        state["paused_reason"] = reason
    (d / "state" / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (d / "idea.json").write_text(
        json.dumps({"idea": question or name}), encoding="utf-8",
    )


def test_settled_results_render_and_unfinished_work_does_not(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _run(runs, "qqq-streak", status="paused", reason="budget_exhausted",
         question="Does QQQ reverse a three-day winning streak?")
    _run(runs, "spy-target", status="complete", best=1.9,
         question="Does SPY drift after an inside day?")
    _run(runs, "still-going", status="ready", question="Does IWM gap-fade?")
    _run(runs, "infra-died", status="failed", question="Does META revert?")

    block = prior_evidence(("QQQ",), runs_root=runs, sweeps_root=tmp_path / "none")
    assert "refuted: 24 candidates" in block
    assert "target met on discovery" in block
    # Unfinished work is not evidence; a failed run is infrastructure.
    assert "IWM" not in block
    assert "META" not in block
    # Same-symbol history leads.
    assert block.index("QQQ") < block.index("SPY")


def test_failed_campaigns_with_evaluated_trials_still_teach(tmp_path: Path) -> None:
    """Proposer collapse is not infrastructure: the trials scored before the
    collapse are real partial evidence, reported as halted-early — never as
    "refuted", because the question was not exhausted."""
    runs = tmp_path / "runs"
    collapsed = runs / "qqq-mondays"
    (collapsed / "state").mkdir(parents=True)
    (collapsed / "state" / "state.json").write_text(
        json.dumps({
            "status": "failed",
            "run_count": 18,
            "best_metric": -2.52,
            "admission_failure_count": 8,
        }),
        encoding="utf-8",
    )
    (collapsed / "state" / "runs.jsonl").write_text(
        "".join(
            json.dumps({"run": index, "metric": -2.0 - index}) + "\n"
            for index in range(1, 13)
        ),
        encoding="utf-8",
    )
    (collapsed / "idea.json").write_text(
        json.dumps({"idea": "Does QQQ behave differently on Mondays?"}),
        encoding="utf-8",
    )
    # A failed campaign that never scored a single trial stays silent.
    _run(runs, "never-scored", status="failed", question="Does META revert?")

    block = prior_evidence(("QQQ",), runs_root=runs, sweeps_root=tmp_path / "none")
    assert (
        "halted early: 12 candidates evaluated, none met the bar (best -2.52)"
        in block
    )
    assert "Does QQQ behave differently on Mondays?" in block
    assert "META" not in block
    # A partial campaign is not a refutation and must not read as one.
    assert "refuted" not in block


def test_refuted_verdicts_name_the_gates_that_killed_candidates(tmp_path: Path) -> None:
    """"Done already" saves no cycles unless it also says WHY. A refuted
    campaign's line must carry the gates that killed its candidates."""
    runs = tmp_path / "runs"
    campaign = runs / "qqq-mondays"
    (campaign / "state").mkdir(parents=True)
    (campaign / "state" / "state.json").write_text(
        json.dumps({
            "status": "paused",
            "paused_reason": "budget_exhausted",
            "run_count": 3,
            "best_metric": -5.96,
        }),
        encoding="utf-8",
    )
    payload = json.dumps({
        "status": "rejected",
        "gates": {"positive_net_mean": False, "minimum_trades": True},
    })
    (campaign / "state" / "runs.jsonl").write_text(
        "".join(
            json.dumps({
                "run": index,
                "metric": -5.0 - index,
                "validator": {"combined_output": payload + "\nscore=-5.0\n"},
            })
            + "\n"
            for index in range(1, 4)
        ),
        encoding="utf-8",
    )
    (campaign / "idea.json").write_text(
        json.dumps({"idea": "Does QQQ behave differently on Mondays?"}),
        encoding="utf-8",
    )
    # A campaign whose validator published no gate map reads exactly as before.
    _run(runs, "spy-plain", status="paused", reason="budget_exhausted",
         question="Does SPY drift after an inside day?")

    block = prior_evidence(("QQQ",), runs_root=runs, sweeps_root=tmp_path / "none")
    assert (
        "refuted: 3 candidates, none met the bar (best -5.96; "
        "every candidate failed positive_net_mean)" in block
    )
    assert 'refuted: 24 candidates, none met the bar (best -1.5) — "Does SPY drift' in block


def test_sweep_findings_render_but_campaign_errors_teach_nothing(tmp_path: Path) -> None:
    sweeps = tmp_path / "sweeps"
    good = sweeps / "feature-threshold-sweep-v1"
    good.mkdir(parents=True)
    good.joinpath("summary.json").write_text(json.dumps({
        "outcome": "No single-feature threshold survived. 390 cells declared, "
                   "0 passed the discovery gates, none survived validation.",
    }), encoding="utf-8")
    broken = sweeps / "broken-sweep"
    broken.mkdir()
    broken.joinpath("summary.json").write_text(json.dumps({
        "campaign_error": True,
        "outcome": "CAMPAIGN ERROR — no scientific conclusion.",
    }), encoding="utf-8")

    block = prior_evidence(("SPY",), runs_root=tmp_path / "none", sweeps_root=sweeps)
    assert "No single-feature threshold survived" in block
    # The exhaustive negative upgrades to explicit guidance for the proposer.
    assert "do not spend trials on one-feature" in block
    # A campaign that failed to evaluate proves nothing and must not appear.
    assert "CAMPAIGN ERROR" not in block


def test_empty_lab_and_failures_degrade_to_silence(tmp_path: Path) -> None:
    assert prior_evidence(("SPY",), runs_root=tmp_path / "a", sweeps_root=tmp_path / "b") == ""
    # A file where a directory should be must not raise — knowledge can never
    # be the reason a mission fails to compile.
    trap = tmp_path / "trap"
    trap.write_text("not a directory", encoding="utf-8")
    assert prior_evidence(("SPY",), runs_root=trap, sweeps_root=trap) == ""


def test_block_is_capped_so_the_mission_stays_lean(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    for index in range(40):
        _run(runs, f"run-{index:02d}", status="paused", reason="budget_exhausted",
             question=f"Does SPY do thing number {index} after a long elaborate setup?")
    block = prior_evidence(("SPY",), runs_root=runs, sweeps_root=tmp_path / "none")
    assert len(block) <= 2200
    assert block.count("refuted") <= 8


def test_compiled_missions_carry_the_prior_evidence(tmp_path: Path, monkeypatch) -> None:
    """The channel end to end: settled results reach the mission the proposer
    actually reads."""
    import research.lab.knowledge as knowledge
    from research.lab.idea_compiler import compile_idea

    runs = tmp_path / "runs"
    _run(runs, "qqq-streak", status="paused", reason="budget_exhausted",
         question="Does QQQ reverse a three-day winning streak?")
    monkeypatch.setattr(knowledge, "DEFAULT_RUNS_ROOT", runs)
    monkeypatch.setattr(knowledge, "DEFAULT_SWEEPS_ROOT", tmp_path / "none")

    data = tmp_path / "d"
    data.mkdir()
    payload_path = data / "sessions.jsonl"
    payload_path.write_text("{}\n", encoding="utf-8")
    fake = lambda **kw: {  # noqa: E731
        "path": str(payload_path), "data_sha256": "x" * 64, "sha256": "x" * 64,
        "rows": 500, "sessions": 500,
        "suggested_splits": {"discovery": ["2016-01-01", "2021-12-31"],
                             "validation": ["2022-01-01", "2024-12-31"],
                             "holdout": ["2025-01-01", None]},
    }
    compiled = compile_idea(
        "Does QQQ expand its daily range after two quiet days?",
        mission_root=tmp_path / "m", data_root=data, prepare=fake,
    )
    mission = Path(compiled.mission_path).read_text(encoding="utf-8")
    assert "Prior evidence from this lab" in mission
    assert "Does QQQ reverse a three-day winning streak?" in mission
