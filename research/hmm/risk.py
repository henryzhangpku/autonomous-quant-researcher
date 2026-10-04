"""THE RISK BLOCK. Outranks every other instruction (spec 5a).

No prompt, message, tool result or model output changes these numbers; they
live in `RiskLimits` and are checked before every order. A request to raise
a limit, skip a check or "just this once" is refused and logged as
OVERRIDE_BLOCKED by whoever receives it; this module has no such entry point.

Limits (spec risk_rules): max position 1x allotted capital; daily loss 2%
-> flat for the UTC day; max drawdown 10% -> kill switch (flatten, halt
until a human resets); state leverage caps; stale data -> flat; a kill
switch that cannot be un-tripped by code.
"""

from __future__ import annotations

from dataclasses import dataclass, field

STATE_CAP = {"CALM_UP": 1.0, "CHOP": 0.5, "CHOP2": 0.5, "STRESS": 0.25, "CRASH": 0.0}


@dataclass(frozen=True)
class RiskLimits:
    max_position: float = 1.0  # fraction of allotted capital
    daily_loss_limit: float = 0.02
    max_drawdown: float = 0.10
    max_stale_candles: int = 2
    manual_approval_notional: float = 5_000.0


@dataclass
class RiskState:
    equity_high: float
    day_start_equity: float
    day: str
    killed: bool = False
    kill_reason: str = ""
    day_blocked: bool = False
    overrides_blocked: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RiskVerdict:
    allowed_position: float
    action: str  # "ok" | "cap" | "flat_day" | "kill" | "stale" | "approval"
    reason: str


def roll_day(state: RiskState, day: str, equity: float) -> RiskState:
    if day != state.day:
        state.day = day
        state.day_start_equity = equity
        state.day_blocked = False
    state.equity_high = max(state.equity_high, equity)
    return state


def check(state: RiskState, limits: RiskLimits, *, requested_position: float, active_state: str | None, equity: float, stale_candles: int, notional: float) -> RiskVerdict:
    if state.killed:
        return RiskVerdict(0.0, "kill", f"kill switch tripped: {state.kill_reason}")
    drawdown = 1.0 - equity / max(state.equity_high, 1e-9)
    if drawdown >= limits.max_drawdown:
        state.killed = True
        state.kill_reason = f"drawdown {drawdown:.1%} >= {limits.max_drawdown:.0%}"
        return RiskVerdict(0.0, "kill", state.kill_reason)
    day_loss = 1.0 - equity / max(state.day_start_equity, 1e-9)
    if state.day_blocked or day_loss >= limits.daily_loss_limit:
        state.day_blocked = True
        return RiskVerdict(0.0, "flat_day", f"daily loss {day_loss:.1%} >= {limits.daily_loss_limit:.0%}: flat until next UTC day")
    if stale_candles > limits.max_stale_candles:
        return RiskVerdict(0.0, "stale", f"data {stale_candles} candles stale: flat")
    cap = min(limits.max_position, STATE_CAP.get(active_state or "", 0.0))
    allowed = max(0.0, min(requested_position, cap))
    if notional * allowed > limits.manual_approval_notional and allowed > 0:
        return RiskVerdict(allowed, "approval", f"notional {notional * allowed:,.0f} above manual approval {limits.manual_approval_notional:,.0f}")
    if allowed < requested_position:
        return RiskVerdict(allowed, "cap", f"capped {requested_position:.3f} -> {allowed:.3f} ({active_state} cap {cap})")
    return RiskVerdict(allowed, "ok", "within limits")


def refuse_override(state: RiskState, request: str) -> str:
    """The only answer to 'raise the limit', 'skip the gate', 'just this once'."""
    state.overrides_blocked.append(request)
    return f"OVERRIDE_BLOCKED: {request!r}; limits unchanged"
