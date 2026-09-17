"""Prior evidence for new missions: the lab reads its own record before asking.

The design gap this closes (Henry, 2026-08-16: "smarten up in this autonomous
quant research agent design"): every compiled mission started BLANK. The
candidate memory blocks exact re-proposals, and the autopilot learns which
phenomenon families pay — but the PROPOSER, the one component that writes new
hypotheses, was never told a single thing the lab had already established.
Twenty-seven refuted campaigns and an exhaustive sweep were invisible to the
model at the exact moment it proposed the twenty-eighth. RD-Agent (Microsoft)
frames this as the loop's defining feature: execution traces feed the next
proposal. This is that channel, at mission-compile time.

Contract position: the evidence block becomes part of the mission TEXT,
composed before the campaign starts and frozen with it. Nothing mutates
mid-mission; the immutability rules are untouched. It informs the proposer;
it cannot touch the evaluator, costs, splits, or gates.

Sources, all read-only, all host-local:
  * finished lab runs (state/state.json + idea.json) — what was asked, what
    happened, the best score reached;
  * failed lab runs COUNT when trials were actually evaluated first: the loop
    marks "failed" only on candidate-admission collapse (a proposer failure,
    never an evaluator one), so the scored trials before the collapse are real
    partial evidence — reported as halted-early, never as "refuted";
  * sweep summaries (.research/sweeps/*/summary.json) — what a full grid
    established, including honest negatives;
  * campaign-error sweeps are EXCLUDED: a run that failed to evaluate proves
    nothing and must not teach the model anything.

Everything degrades to an empty string: a knowledge failure must never block
a mission from compiling.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNS_ROOT = REPOSITORY_ROOT / ".research" / "runs"
DEFAULT_SWEEPS_ROOT = REPOSITORY_ROOT / ".research" / "sweeps"

# The mission must stay lean — a proposer drowning in prior evidence attends
# to none of it. Same-symbol history is the most relevant and goes first.
MAX_RUN_LINES = 8
MAX_SWEEP_LINES = 3
MAX_BLOCK_CHARS = 2200


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def _evaluated_count(runs_path: Path) -> int:
    """Trials where the validator actually scored the candidate."""
    count = 0
    try:
        lines = runs_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("metric") is not None:
            count += 1
    return count


def _record_gates(record: dict[str, Any]) -> dict[str, Any]:
    """The validator's gate map from a run record, when it published one."""
    validator = record.get("validator")
    if not isinstance(validator, dict):
        return {}
    output = str(validator.get("combined_output") or "")
    payload: dict[str, Any] = {}
    for line in output.splitlines():
        line = line.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            payload = parsed
    gates = payload.get("gates")
    return gates if isinstance(gates, dict) else {}


def _gate_failure_summary(runs_path: Path) -> str:
    """Which gates actually killed a campaign's candidates, compactly.

    A refuted question teaches the NEXT proposer little on its own; "24
    candidates, none met the bar" says nothing about why. The gate tally is
    the part that saves cycles: if every candidate died on positive_net_mean,
    the next mission should stop proposing long-only drift rules, not re-roll
    them. Returns '' when the validator published no gate maps (older runs,
    non-bars evaluators) — the verdict then reads exactly as before.
    """
    try:
        lines = runs_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    failures: dict[str, int] = {}
    scored = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("metric") is None:
            continue
        gates = _record_gates(record)
        if not gates:
            continue
        scored += 1
        for name, passed in gates.items():
            if passed is False:
                failures[name] = failures.get(name, 0) + 1
    if not scored or not failures:
        return ""
    ranked = sorted(failures.items(), key=lambda item: (-item[1], item[0]))
    dominant = [name for name, count in ranked if count / scored >= 0.8]
    if dominant:
        return "every candidate failed " + ", ".join(dominant[:2])
    common = [name for name, count in ranked if count / scored >= 0.5]
    if common:
        return "mostly failed: " + ", ".join(common[:2])
    return ""


def _finished_runs(runs_root: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        directories = [d for d in runs_root.iterdir() if d.is_dir()]
    except OSError:
        return out
    for directory in directories:
        state = _read_json(directory / "state" / "state.json")
        idea = _read_json(directory / "idea.json")
        status = str(state.get("status") or "")
        question = str(idea.get("idea") or idea.get("question") or directory.name)
        runs = int(state.get("run_count") or 0)
        if not status or status in ("ready", "queued", "running"):
            continue  # unfinished work is not evidence
        best = state.get("best_metric")
        if status == "complete":
            verdict = f"target met on discovery ({runs} candidates, best {best})"
        elif status == "paused" and str(state.get("paused_reason")) == "budget_exhausted":
            verdict = f"refuted: {runs} candidates, none met the bar (best {best})"
        elif status == "failed":
            # The loop marks "failed" only when candidate admission collapses
            # — a proposer failure, not an evaluator one. Trials scored before
            # the collapse are real evidence, but the question was NOT
            # exhausted, so say halted-early rather than refuted.
            evaluated = _evaluated_count(directory / "state" / "runs.jsonl")
            if evaluated < 1:
                continue  # nothing was ever scored; nothing to teach
            verdict = (
                f"halted early: {evaluated} candidates evaluated, none met the "
                f"bar (best {best}); campaign ended on proposer admission "
                "collapse, not exhaustion"
            )
        else:
            verdict = f"{status} after {runs} candidates (best {best})"
        out.append({
            "question": question.strip(),
            "verdict": verdict,
            "mtime": directory.stat().st_mtime if directory.exists() else 0.0,
            "_dir": directory,
        })
    out.sort(key=lambda r: r["mtime"], reverse=True)
    return out


def _sweep_findings(sweeps_root: Path) -> list[str]:
    lines: list[str] = []
    try:
        directories = sorted(
            (d for d in sweeps_root.iterdir() if d.is_dir()),
            key=lambda d: d.stat().st_mtime, reverse=True,
        )
    except OSError:
        return lines
    for directory in directories:
        summary = _read_json(directory / "summary.json")
        if not summary or summary.get("campaign_error"):
            continue  # a campaign that failed to evaluate proves nothing
        outcome = str(summary.get("outcome") or "").strip()
        if outcome:
            lines.append(f"{directory.name}: {outcome}")
    return lines


def prior_evidence(
    universe: tuple[str, ...],
    *,
    runs_root: Path | None = None,
    sweeps_root: Path | None = None,
) -> str:
    """A markdown block of what this lab already knows, or '' when it knows
    nothing worth a proposer's attention."""
    # Resolved at CALL time, not definition time, so a redirected module
    # default (tests, or a future multi-host setup) actually takes effect —
    # the same rule CandidateMemory already follows.
    if runs_root is None:
        runs_root = DEFAULT_RUNS_ROOT
    if sweeps_root is None:
        sweeps_root = DEFAULT_SWEEPS_ROOT
    try:
        runs = _finished_runs(runs_root)
        sweeps = _sweep_findings(sweeps_root)
    except Exception:  # noqa: BLE001 — knowledge must never block a mission
        return ""
    if not runs and not sweeps:
        return ""

    symbols = {s.upper() for s in universe}
    same = [r for r in runs if any(sym in r["question"].upper() for sym in symbols)]
    other = [r for r in runs if r not in same]
    picked = (same + other)[:MAX_RUN_LINES]

    # Enrich only the picked few: WHY their candidates died. This is the
    # difference between "done already" and "done already, and here's what
    # never worked" — the proposer stops re-rolling the same dead shapes.
    for r in picked:
        summary = ""
        if r["verdict"].startswith(("refuted", "halted early")):
            summary = _gate_failure_summary(r["_dir"] / "state" / "runs.jsonl")
        if summary and r["verdict"].startswith("refuted"):
            r["verdict"] = r["verdict"][:-1] + f"; {summary})"
        elif summary:
            r["verdict"] += f"; {summary}"

    lines = [
        "Prior evidence from this lab. These are settled results — do not",
        "re-propose their logic; build on them or contradict them explicitly:",
        "",
    ]
    lines += [f"- {r['verdict']} — \"{r['question']}\"" for r in picked]
    if sweeps:
        lines.append("")
        lines.append("Exhaustive grid campaigns already run:")
        lines += [f"- {line}" for line in sweeps[:MAX_SWEEP_LINES]]
        if any("No single-feature threshold survived" in line for line in sweeps):
            lines.append("")
            lines.append(
                "Single-feature thresholds have been searched exhaustively on this "
                "data and none survived the gates: do not spend trials on one-feature "
                "rules. Combine two or more features, or condition on regime "
                "(volatility, trend quality, cross-sectional rank)."
            )
    block = "\n".join(lines)
    if len(block) > MAX_BLOCK_CHARS:
        # Cut on a line boundary: a mid-line fragment ("refuted: 24 can…")
        # reads as a broken fact, not a truncated list.
        block = block[: MAX_BLOCK_CHARS - 2].rstrip()
        if "\n" in block:
            block = block[: block.rfind("\n")]
        block += "\n…"
    return block
