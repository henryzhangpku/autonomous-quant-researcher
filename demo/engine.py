"""The browser demo engine: the real schema-v3 loop on a synthetic tape.

Everything below the API layer is the shipped engine, unmodified —
`research.v3.campaign` (frozen contract, admission, hash-chained ledger),
`research.v3.runtime` (discovery → frozen shortlist → validation, stopping
before the one-use holdout), `research.v3.evaluator` (cost scenarios, bootstrap
lower bounds, gate sets) and `research.backtest` (options mechanics).

Two things are stand-ins, and the page says so:

* **The tape is synthetic.** A seeded random walk on a five-minute grid,
  2018 → 2025, with one regime deliberately planted in the discovery years and
  absent afterwards: a weak open tends to recover. Discovery can find it;
  validation, where it no longer exists, is meant to refute it. That is the
  loop doing its job, on purpose.
* **The proposer is scripted.** The demo plays the LLM's part with a fixed
  sequence of formulations — including an invalid one, one that smuggles a
  `code` field, an exact duplicate and a near-duplicate — followed by seeded
  variations. Every string still goes through the real declarative boundary
  (`DeclarativeProposer` → `parse_proposal_json`), so what the ledger records
  is what the engine decided, not what the script intended.

Runs under CPython (tests) and under Pyodide (the page).
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from research.backtest.data import Session
from research.v3.campaign import CAMPAIGN_SCHEMA_VERSION, CampaignContract, CampaignCoordinator
from research.v3.evaluator import EVALUATOR_VERSION
from research.v3.proposer import DeclarativeProposer
from research.v3.runtime import CampaignRuntime, ModeledResearchEvaluator
from research.v3.stage_store import StageAccess, materialize_stage_stores

MISSION = (
    "Demo program: discover a robust SPY same-day-expiry options hypothesis on "
    "the synthetic tape. Propose a declarative hypothesis only — an entry minute, "
    "a bounded signal over causal observations, and one defined-risk structure. "
    "Trusted code interprets, prices, costs, and gates it across discovery, "
    "frozen validation, and a sealed one-use holdout."
)
MARKET = "SPY · synthetic five-minute tape (seed 7)"
DISCOVERY_BUDGET = 10
FORMULATION_LIMIT = 60
FAMILY_QUOTA = 3
TAPE_SEED = 7
TRIGGER = -0.0010   # 10:00 price vs the open, discovery years only
RECOVERY = 0.008    # planted drift over the rest of the session

TERMINAL_STAGES = {
    "holdout_ready", "no_discovery_survivor", "no_validation_survivor",
    "holdout_consumed", "finding_ready", "failed",
}
OUTCOMES = {
    "ready": "Waiting for the research worker",
    "discovery": "Exploratory discovery in progress — not accepted alpha",
    "shortlist_frozen": "Discovery closed; shortlist frozen",
    "validation": "Frozen validation in progress",
    "holdout_ready": "Validation survivor ready for deliberate one-use holdout",
    "holdout_consumed": "Final holdout consumed; modeled evidence recorded",
    "finding_ready": "Research-only finding artifact ready",
    "no_discovery_survivor": "No discovery candidate passed absolute gates",
    "no_validation_survivor": "No frozen candidate survived validation",
    "failed": "Research stopped with durable failure evidence",
}

_ROOT = Path("/demo")
_TAPE: dict[str, Any] = {}
_SESSIONS: dict[str, dict[str, Any]] = {}
_COUNTER = [0]
_LOG: list[str] = []


# ── Synthetic tape ─────────────────────────────────────────────────────────

def synthetic_sessions(seed: int = TAPE_SEED) -> list[Session]:
    """Weekday sessions 2018-01-02 → 2025-08-29 on the 09:30–15:55 grid.

    The planted regime: in the discovery years only, when the 10:00 price is
    more than 0.10% below the open, the rest of the day drifts up about 0.8%.
    From 2022 on there is no such tendency. Everything else is noise. The
    numbers were chosen by running the real evaluator: the regime clears every
    discovery gate and fails validation, which is the lesson the demo exists
    to show.
    """
    rng = random.Random(seed)
    minutes = list(range(570, 960, 5))  # 78 bars, last one at 15:55
    sessions: list[Session] = []
    price = 250.0
    current = date(2018, 1, 2)
    end = date(2025, 8, 29)
    while current <= end:
        if current.weekday() < 5:
            price *= math.exp(rng.gauss(0.0, 0.006))
            planted = current.year <= 2021
            bars = []
            close = price
            drift = 0.0
            for index, minute in enumerate(minutes):
                bar_open = close
                if minute == 600 and planted and (bar_open / price - 1.0) < TRIGGER:
                    drift = RECOVERY / (len(minutes) - index)
                step = rng.gauss(drift, 0.00055)
                close = bar_open * math.exp(step)
                wiggle = abs(rng.gauss(0.0, 0.00025))
                high = max(bar_open, close) * (1.0 + wiggle)
                low = min(bar_open, close) * (1.0 - wiggle)
                bars.append((minute, round(bar_open, 4), round(high, 4),
                             round(low, 4), round(close, 4)))
            sessions.append(Session(current.isoformat(), tuple(bars)))
            price = close
        current += timedelta(days=1)
    return sessions


# ── Scripted proposer ──────────────────────────────────────────────────────

def _hyp(entry: int, signal: dict[str, Any], helper: str, width: float,
         offset: float = 0.0) -> dict[str, Any]:
    return {"schema_version": 3, "entry_minute": entry, "signal": signal,
            "structure": {"helper": helper, "width": width, "otm_offset": offset}}


def _cmp(op: str, obs: dict[str, Any], value: float) -> dict[str, Any]:
    return {"op": op, "observation": obs, "value": value}


def _rfo(minute: int) -> dict[str, Any]:
    return {"obs": "return_from_open", "minute": minute}


def _between(start: int, end: int) -> dict[str, Any]:
    return {"obs": "return_between", "start_minute": start, "end_minute": end}


def _touch(after: int, offset: float) -> dict[str, Any]:
    return {"obs": "first_touch_before_entry", "after_minute": after, "open_offset": offset}


def _proposal(hypothesis: dict[str, Any], rationale: str) -> str:
    return json.dumps({"hypothesis": hypothesis, "rationale": rationale})


DIP = _hyp(605, _cmp("lt", _rfo(600), -0.1), "call_debit_spread", 2.0)

SCRIPT: list[str] = [
    # 1. Not JSON at all. Recorded as an invalid formulation; no trial spent.
    "I think the market tends to bounce after a weak open, so we should buy calls.",
    # 2. Smuggles executable source. The boundary rejects the extra field.
    json.dumps({"hypothesis": DIP, "rationale": "dip buy", "code": "import os"}),
    # 3. The planted regime, stated declaratively.
    _proposal(DIP, "A weak first half hour tends to recover; buy a near-money call debit spread."),
    # 4. Exact semantic duplicate of 3 — rejected before it spends a trial.
    _proposal(DIP, "Same idea, phrased again."),
    # 5. Near duplicate: one knob turned.
    _proposal(_hyp(605, _cmp("lt", _rfo(600), -0.11), "call_debit_spread", 2.0),
              "Slightly deeper dip threshold."),
    # 6. The mirror: strong open, sell a put credit spread.
    _proposal(_hyp(605, _cmp("gt", _rfo(600), 0.1), "put_credit_spread", 2.0, 0.5),
              "Strength in the first half hour; harvest premium below the market."),
    # 7. Momentum between 09:30 and 10:30.
    _proposal(_hyp(635, _cmp("gt", _between(570, 630), 0.2), "call_debit_spread", 2.0),
              "Follow a first-hour advance."),
    # 8. First touch of open −0.5% before 11:00.
    _proposal(_hyp(660, {"op": "observed", "observation": _touch(575, -0.5)},
                   "call_debit_spread", 3.0),
              "If price has already touched half a percent below the open, fade it."),
    # 9. Compound: weak open AND not already recovered.
    _proposal(_hyp(640, {"op": "and", "args": [
                       _cmp("lt", _rfo(600), -0.1),
                       _cmp("lt", _between(600, 635), 0.1)]},
                   "call_debit_spread", 2.0),
              "Weak open that has not yet bounced by 10:35."),
    # 10. Late-day: afternoon weakness, put credit below.
    _proposal(_hyp(780, _cmp("lt", _between(690, 775), -0.25), "put_credit_spread", 2.0, 1.0),
              "Sell downside premium into afternoon weakness."),
    # 11. Third member of the dip family — quota pressure.
    _proposal(_hyp(605, _cmp("lt", _rfo(600), -0.25), "call_debit_spread", 2.0),
              "A deeper dip, same structure."),
    # 12. Wider structure on the mirror idea.
    _proposal(_hyp(610, _cmp("gt", _rfo(600), 0.2), "put_credit_spread", 3.0, 1.0),
              "Wider put credit on a strong open."),
]


class ScriptedCompletion:
    """Plays the model: the fixed script, then seeded variations forever."""

    def __init__(self, seed: int) -> None:
        self._script = list(SCRIPT)
        self._rng = random.Random(seed)
        self.calls = 0

    def __call__(self, prompt: str) -> str:
        self.calls += 1
        if self._script:
            return self._script.pop(0)
        rng = self._rng
        entry = rng.choice([605, 620, 635, 660, 700, 750, 780])
        look = rng.choice([575, 590, 600])
        look = min(look, entry - 5)
        obs = _rfo(look) if rng.random() < 0.6 else _between(570, look)
        op = rng.choice(["lt", "gt"])
        value = round(rng.choice([0.15, 0.2, 0.25, 0.35, 0.45]) * (-1 if op == "lt" else 1), 2)
        helper = rng.choice(["call_debit_spread", "put_credit_spread"])
        width = rng.choice([1.0, 2.0, 3.0])
        offset = 0.0 if helper == "call_debit_spread" else rng.choice([0.5, 1.0])
        return _proposal(_hyp(entry, _cmp(op, obs, value), helper, width, offset),
                         f"Variation {self.calls}: {op} {value} at {look}, {helper} w{width}.")


# ── Boot ───────────────────────────────────────────────────────────────────

def configure(root: str | Path) -> None:
    global _ROOT
    _ROOT = Path(root)


def boot() -> dict[str, Any]:
    """Materialize the immutable stage stores once, then start one campaign."""
    if _TAPE:
        return health()
    started = time.time()
    sessions = synthetic_sessions()
    canonical = json.dumps([[s.day, list(map(list, s.bars))] for s in sessions],
                           separators=(",", ":"))
    source_hash = hashlib.sha256(canonical.encode()).hexdigest()
    stage_root = _ROOT / "stages"
    manifests = materialize_stage_stores(stage_root, sessions, source_hash=source_hash)
    _TAPE.update({
        "root": stage_root, "source_hash": source_hash,
        "manifest_hashes": {name: m.manifest_hash for name, m in manifests.items()},
        "sessions": len(sessions), "first": sessions[0].day, "last": sessions[-1].day,
        "materialized_seconds": round(time.time() - started, 2),
    })
    _log(f"tape materialized: {len(sessions)} sessions in {_TAPE['materialized_seconds']}s")
    create_session("Canonical demo campaign", MISSION)
    return health()


def _log(text: str) -> None:
    _LOG.append(f"{_now()} {text}")
    del _LOG[:-200]


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


# ── Sessions ───────────────────────────────────────────────────────────────

def create_session(title: str, mission: str) -> dict[str, Any]:
    if not _TAPE:
        raise RuntimeError("engine is not booted")
    if mission.strip() != MISSION:
        raise ValueError(
            "The demo runs one frozen program contract; any other mission text is a "
            "different contract and fails closed. Use the canonical mission."
        )
    _COUNTER[0] += 1
    session_id = f"demo-{_COUNTER[0]:03d}"
    root = _ROOT / "campaigns" / session_id
    policy = {"program": "demo-spy-price-v3", "discovery_budget": DISCOVERY_BUDGET,
              "family_quota": FAMILY_QUOTA, "tape_seed": TAPE_SEED}
    contract = CampaignContract(
        campaign_id=session_id,
        policy_hash=hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest(),
        evaluator_version=EVALUATOR_VERSION,
        stage_manifest_hashes=dict(_TAPE["manifest_hashes"]),
        mission_hash=hashlib.sha256(MISSION.encode("utf-8")).hexdigest(),
        discovery_budget=DISCOVERY_BUDGET,
        family_quota=FAMILY_QUOTA,
    )
    coordinator = CampaignCoordinator(
        root, contract, lambda stage: StageAccess(_TAPE["root"], stage)
    )
    completion = ScriptedCompletion(seed=_COUNTER[0])
    runtime = CampaignRuntime(
        coordinator, DeclarativeProposer(completion), ModeledResearchEvaluator(),
        mission=MISSION, formulation_limit=FORMULATION_LIMIT,
    )
    record = {
        "id": session_id, "title": title.strip() or session_id, "mission": MISSION,
        "created_at": _now(), "updated_at": _now(), "status": "queued",
        "paused": False, "error": None, "coordinator": coordinator, "runtime": runtime,
        "completion": completion, "step_seconds": [],
    }
    _SESSIONS[session_id] = record
    _log(f"{session_id} created: {record['title']}")
    return snapshot(session_id)


def _live() -> dict[str, Any] | None:
    for record in reversed(list(_SESSIONS.values())):
        stage = record["coordinator"].state["state"]
        if stage not in TERMINAL_STAGES and not record["paused"]:
            return record
    return None


def tick() -> dict[str, Any]:
    """Advance the newest live campaign by one scientific trial (or to the end)."""
    record = _live()
    if record is None:
        return {"idle": True}
    coordinator = record["coordinator"]
    before = coordinator.state["scientific_trials"]
    record["status"] = "running"
    started = time.time()
    try:
        state = record["runtime"].run_until_holdout(
            should_pause=lambda: (
                coordinator.state["scientific_trials"] > before or record["paused"]
            )
        )
    except Exception as exc:  # noqa: BLE001 — the ledger keeps it, the page shows it
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["status"] = "failed"
        _log(f"{record['id']} step failed: {record['error']}")
        return {"id": record["id"], "error": record["error"]}
    record["step_seconds"].append(round(time.time() - started, 3))
    record["updated_at"] = _now()
    stage = state["state"]
    if stage in TERMINAL_STAGES:
        record["status"] = stage
        _log(f"{record['id']} finished: {stage}")
    return {"id": record["id"], "stage": stage,
            "scientific_trials": state["scientific_trials"],
            "seconds": record["step_seconds"][-1]}


# ── Snapshots (the shape the legacy board renders) ─────────────────────────

def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _attempts(state: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for index, item in enumerate(state["discovery_attempts"], 1):
        evaluation = item.get("evaluation") or {}
        candidate = item.get("candidate") or {}
        descriptor = candidate.get("descriptor") or {}
        failures = evaluation.get("failures") or ()
        rows.append({
            "run": index, "attempt_id": item.get("attempt_id"),
            "admitted": item.get("admitted"), "reason": item.get("reason"),
            "exploratory": True, "evaluation": item.get("evaluation"),
            "metric": _finite(evaluation.get("ranking_score_pp")),
            "accepted": False, "improved": bool(evaluation.get("eligible")),
            "feedback": item.get("reason") or "; ".join(failures),
            "error": None if item.get("admitted") else item.get("reason"),
            "label": descriptor.get("family") or "—",
            "duration_seconds": None,
        })
    return rows


def snapshot(session_id: str) -> dict[str, Any]:
    record = _SESSIONS[session_id]
    coordinator = record["coordinator"]
    state = coordinator.state
    stage = str(state.get("state") or "ready")
    status = stage if stage in TERMINAL_STAGES else record["status"]
    if record["paused"] and stage not in TERMINAL_STAGES:
        status = "paused"
    attempts = _attempts(state)
    scored = [(row["metric"], row) for row in attempts
              if row["admitted"] and row["metric"] is not None]
    best_metric = max((score for score, _ in scored), default=None)
    best_rationale = None
    if scored:
        best = max(scored, key=lambda pair: pair[0])[1]
        for item in reversed(record["runtime"].formulations.records()):
            if (item.get("attempt_id") == best["attempt_id"]
                    and isinstance(item.get("rationale"), str)):
                best_rationale = item["rationale"]
                break
    formulations = record["runtime"].formulations.count
    validation = list((state.get("validation_results") or {}).values())
    last = attempts[-1]["feedback"] if attempts else None
    return {
        "id": session_id, "title": record["title"], "mission": MISSION, "market": MARKET,
        "created_at": record["created_at"], "updated_at": record["updated_at"],
        "schema_version": 3, "campaign_schema_version": CAMPAIGN_SCHEMA_VERSION,
        "read_only": False, "archived": False,
        "resume_allowed": stage in {"ready", "discovery", "shortlist_frozen", "validation"},
        "status": status, "stage": stage, "outcome": OUTCOMES.get(stage, "Schema-v3 research evidence"),
        "formulations": formulations, "formulation_limit": FORMULATION_LIMIT,
        "scientific_trials": int(state.get("scientific_trials", 0)),
        "max_attempts": DISCOVERY_BUDGET, "run_count": int(state.get("scientific_trials", 0)),
        "discovery": {"label": "Exploratory discovery — not accepted alpha",
                      "attempts": attempts, "shortlist": state.get("shortlist")},
        "validation": {"label": "Frozen out-of-sample validation",
                       "results": validation, "finalist": state.get("finalist")},
        "holdout": {"label": "One-use final holdout", "receipt": state.get("holdout_receipt"),
                    "result": state.get("holdout_result"), "ready": stage == "holdout_ready",
                    "action_available": False,
                    "consumed": state.get("holdout_receipt") is not None},
        "opra": {"label": "Actual OPRA evidence", "available": False, "state": "unavailable"},
        "finding": state.get("finding"), "paper_shadow_review_eligible": False,
        "accepted_alpha": False, "best_metric": best_metric, "best_rationale": best_rationale,
        "attempts": attempts, "last_feedback": last,
        "summary": OUTCOMES.get(stage, ""),
        "error": record["error"] or ((state.get("campaign_failure") or {}).get("reason")),
        "demo": {"proposer": "scripted", "tape": "synthetic", "seed": TAPE_SEED,
                 "step_seconds": record["step_seconds"][-5:]},
    }


def _summary(session_id: str) -> dict[str, Any]:
    full = snapshot(session_id)
    keys = ("id", "title", "created_at", "updated_at", "status", "stage", "outcome",
            "read_only", "archived", "schema_version", "campaign_schema_version",
            "scientific_trials", "formulations", "best_metric")
    return {key: full[key] for key in keys}


def health() -> dict[str, Any]:
    ready = bool(_TAPE)
    return {
        "status": "ready" if ready else "not_ready", "research_ready": ready,
        "product": "Autonomous Quant Researcher — browser demo",
        "access": "runs entirely in your browser (Pyodide); nothing is sent anywhere",
        "mission": MISSION,
        "gpu": {"available": False, "state": "not needed: the proposer is scripted"},
        "model": {"available": True, "state": "scripted proposer",
                  "name": "scripted formulations + seeded variations"},
        "data": {"ready": ready, "state": "synthetic tape",
                 "sessions": _TAPE.get("sessions"), "first": _TAPE.get("first"),
                 "last": _TAPE.get("last"), "source_hash": _TAPE.get("source_hash")},
        "stages": {"ready": ready, **_TAPE.get("manifest_hashes", {})},
        "opra_adapter": {"available": False, "state": "unavailable"},
        "session_count": len(_SESSIONS),
        "gateway": {"enabled": False, "state": "demo", "pending": [], "reports": [],
                    "history": []},
        "research_programs": [{
            "id": "demo-spy-price-v3", "name": "SPY 0DTE / weekly options (synthetic)",
            "status": "online" if ready else "not ready", "provider": "synthetic",
            "campaign_schema_version": CAMPAIGN_SCHEMA_VERSION,
            "mode": "price-only staged research",
            "metric": "absolute edge and net P&L gates (percentage points)",
        }],
        "log": _LOG[-12:],
    }


# ── HTTP-shaped dispatch for the page ──────────────────────────────────────

def handle(method: str, path: str, body_json: str | None = None) -> str:
    """Return JSON `[status, payload]` for one legacy-board request."""
    try:
        status, payload = _dispatch(method.upper(), path.strip("/"),
                                    json.loads(body_json) if body_json else {})
    except ValueError as exc:
        status, payload = 400, {"error": str(exc)}
    except KeyError as exc:
        status, payload = 404, {"error": f"unknown session {exc}"}
    except Exception as exc:  # noqa: BLE001
        status, payload = 500, {"error": f"{type(exc).__name__}: {exc}"}
    return json.dumps([status, payload], allow_nan=False, default=str)


def _dispatch(method: str, path: str, body: dict[str, Any]) -> tuple[int, Any]:
    parts = path.split("/")
    if parts[0] != "api":
        return 404, {"error": "not an api path"}
    route = parts[1:]
    if method == "GET":
        if route == ["ping"]:
            return 200, {"ok": True}
        if route == ["health"]:
            return 200, health()
        if route == ["sessions"]:
            return 200, {"sessions": [_summary(sid) for sid in reversed(list(_SESSIONS))]}
        if len(route) == 2 and route[0] == "sessions":
            return 200, snapshot(route[1])
        if len(route) == 3 and route[0] == "sessions" and route[2] == "equity":
            _SESSIONS[route[1]]
            return 404, {"error": "the demo does not publish an equity curve"}
        return 404, {"error": f"no route for GET {path}"}
    if method == "POST":
        if route == ["sessions"]:
            return 201, create_session(str(body.get("title", "")), str(body.get("mission", "")))
        if route == ["ideas"]:
            idea = str(body.get("idea", "")).strip()
            if not idea:
                raise ValueError("Describe the idea first.")
            return 200, {
                "slug": f"idea-{hashlib.sha256(idea.encode()).hexdigest()[:8]}",
                "title": idea[:80],
                "mission": (
                    "Demo intake: the browser demo runs exactly one frozen program "
                    "contract (below) and cannot compile arbitrary ideas — that needs "
                    "the idea compiler and a data provider. Your text becomes the "
                    f"campaign title.\n\n{MISSION}"
                ),
            }
        if route == ["ideas", "launch"]:
            title = str(body.get("title") or body.get("slug") or "Launched from the sheet")
            return 201, create_session(title, MISSION)
        if route == ["queue"] or route == ["queue", "move"]:
            raise ValueError("Queueing is disabled in the demo — launches run immediately.")
        if route == ["gateway"]:
            return 200, {"enabled": False, "state": "demo"}
        if len(route) == 3 and route[0] == "sessions":
            record = _SESSIONS[route[1]]
            action = route[2]
            if action == "pause":
                record["paused"] = True
            elif action == "resume":
                record["paused"] = False
                record["status"] = "running"
            elif action == "holdout":
                raise ValueError("The final holdout stays sealed in the demo. It is single-use.")
            else:
                return 404, {"error": f"no action {action}"}
            record["updated_at"] = _now()
            return 200, snapshot(route[1])
        return 404, {"error": f"no route for POST {path}"}
    return 405, {"error": f"{method} not allowed"}
