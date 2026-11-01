"""The code decides when the active state changes (spec: switching_rules).

A pure step function over filtered probabilities. Hysteresis so it never
flip-flops:

1. a candidate state must exceed THRESHOLD filtered probability to take over
2. it must hold that lead for HOLD consecutive candles
3. COOLDOWN candles after any switch, no further switch
4. if P(next candle in a stress-class state) exceeds EARLY_CUT, size x 0.5
5. if the top two states are within UNCERTAIN of each other, size 0
6. stale data (candle age > MAX_STALE candles) -> flat; model error -> flat

Every switch and every size-zero carries a reason string for the ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

THRESHOLD = 0.70
HOLD = 3
UNCERTAIN = 0.15
EARLY_CUT = 0.25
MAX_STALE = 2
STRESS_CLASS = ("STRESS", "CRASH")


@dataclass(frozen=True)
class SwitchState:
    active: str | None = None  # None = no state yet (flat)
    candidate: str | None = None
    candidate_count: int = 0
    cooldown_left: int = 0
    last_switch_index: int = -1


@dataclass(frozen=True)
class Decision:
    active: str | None
    size_multiplier: float  # 0, 0.5 or 1 from the switching layer alone
    switched: bool
    reason: str


def step(
    state: SwitchState,
    probs: np.ndarray,
    labels: tuple[str, ...],
    next_probs: np.ndarray,
    *,
    index: int,
    cooldown: int,
    stale_candles: int = 0,
    model_ok: bool = True,
) -> tuple[SwitchState, Decision]:
    if not model_ok:
        return state, Decision(None, 0.0, state.active is not None, "model error: flat")
    if stale_candles > MAX_STALE:
        return state, Decision(None, 0.0, state.active is not None, f"stale data ({stale_candles} candles): flat")

    order = np.argsort(probs)[::-1]
    top, second = labels[order[0]], labels[order[1]] if len(labels) > 1 else None
    p_top, p_second = float(probs[order[0]]), float(probs[order[1]]) if len(labels) > 1 else 0.0

    new = state
    switched = False
    reason = "hold"
    # cooldown ticks down regardless of what the probabilities say
    if new.cooldown_left > 0:
        new = replace(new, cooldown_left=new.cooldown_left - 1)

    if new.active is None:
        if p_top >= THRESHOLD:
            new = replace(new, active=top, candidate=None, candidate_count=0, cooldown_left=cooldown, last_switch_index=index)
            switched, reason = True, f"initial state {top} at p={p_top:.2f}"
    elif top != new.active and p_top > THRESHOLD:
        if new.candidate == top:
            new = replace(new, candidate_count=new.candidate_count + 1)
        else:
            new = replace(new, candidate=top, candidate_count=1)
        if new.candidate_count >= HOLD and state.cooldown_left == 0:
            new = replace(new, active=top, candidate=None, candidate_count=0, cooldown_left=cooldown, last_switch_index=index)
            switched, reason = True, f"switch {state.active} -> {top}: p={p_top:.2f} held {HOLD} candles"
        elif new.candidate_count >= HOLD:
            reason = f"{top} leads but cooldown {state.cooldown_left} left"
        else:
            reason = f"{top} leads ({new.candidate_count}/{HOLD})"
    else:
        if new.candidate is not None:
            new = replace(new, candidate=None, candidate_count=0)

    if new.active is None:
        return new, Decision(None, 0.0, switched, f"no state above {THRESHOLD:.2f}: flat")

    size = 1.0
    if second is not None and (p_top - p_second) < UNCERTAIN:
        size = 0.0
        reason = f"uncertain: {top} {p_top:.2f} vs {second} {p_second:.2f}"
    else:
        p_stress_next = float(sum(next_probs[i] for i, lab in enumerate(labels) if lab in STRESS_CLASS and lab != new.active))
        if p_stress_next > EARLY_CUT:
            size = 0.5
            reason = f"early cut: P(next stress-class)={p_stress_next:.2f}"
    return new, Decision(new.active, size, switched, reason)


def run(probs: np.ndarray, labels: tuple[str, ...], transmat: np.ndarray, *, cooldown: int, stale: np.ndarray | None = None) -> list[Decision]:
    """Apply `step` over a whole filtered series (backtest use)."""
    st = SwitchState()
    out: list[Decision] = []
    for i in range(len(probs)):
        nxt = probs[i] @ transmat
        st, d = step(st, probs[i], labels, nxt, index=i, cooldown=cooldown, stale_candles=0 if stale is None else int(stale[i]))
        out.append(d)
    return out
