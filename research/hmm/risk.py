"""Illustrative risk block for the research example.

The numbers in ``RiskLimits`` are deliberately fake examples, not a house
risk policy. Replace them with explicit limits for any real deployment; this
module only demonstrates the mechanics: every limit is checked before an
order, a breach caps, flattens or kills, and no model output can raise a
limit or un-trip the kill switch.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Clearly illustrative state caps for the example. Not a production policy.
STATE_CAP = {"CALM_UP": 0.75, "CHOP": 0.40, "CHOP2": 0.40, "STRESS": 0.20, "CRASH": 0.0}


@dataclass(frozen=True)
class RiskLimits:
    """Example limits only: 5% daily loss, 20% drawdown, 10k approval."""

    max_position: float = 0.75  # fraction of allotted capital
    daily_loss_limit: float = 0.05
    max_drawdown: float = 0.20
    max_stale_candles: int = 3
    manual_approval_notional: float = 10_000.0


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
