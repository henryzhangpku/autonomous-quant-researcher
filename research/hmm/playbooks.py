"""Playbooks as pure functions (spec: playbook_per_state, shape approved
2026-10-04 at the phase-2 pause: SIZE-REGULATION playbooks).

One base strategy per asset; the state only scales its size and can flatten
it. The base strategy is deliberately simple and frozen here, before the
backtest, so it is a hypothesis and not a tuned result:

    TREND  long when the standardized trend feature (EMA20 - EMA100 over
           rvol*close, z-scored on the past) is above 0, flat otherwise;
           exit when it drops below 0; protective stop at 2 x rvol below
           entry (candle-level), checked on the close; no take-profit.

State multipliers (playbooks/<STATE>.md say the same in words):

    CALM_UP 1.00   CHOP 0.50   CHOP2 0.50   STRESS 0.25   CRASH 0.00

Invalidation: a state of CRASH flattens whatever is held; a stop hit
flattens and blocks re-entry until the trend signal has been below 0 at
least once (no re-entry into the same move after being stopped).
"""

from __future__ import annotations

from dataclasses import dataclass

STATE_MULTIPLIER = {"CALM_UP": 1.0, "CHOP": 0.5, "CHOP2": 0.5, "STRESS": 0.25, "CRASH": 0.0}
STOP_RVOL_MULT = 2.0


@dataclass(frozen=True)
class BookState:
    position: float = 0.0  # fraction of allotted capital, >= 0 (long/flat)
    entry_price: float | None = None
    stop_price: float | None = None
    stopped_out: bool = False  # re-entry blocked until the trend has been <= 0 once


@dataclass(frozen=True)
class Order:
    target_position: float
    reason: str


def state_multiplier(state: str | None) -> float:
    if state is None:
        return 0.0
    return STATE_MULTIPLIER.get(state, 0.0)


def trend_signal(trend_z: float) -> bool:
    return trend_z > 0.0


def decide(book: BookState, *, close: float, rvol: float, trend_z: float, state: str | None, size_cap: float) -> tuple[BookState, Order]:
    """One candle of the base strategy under the state's multiplier.

    size_cap is everything upstream (switching size, Kelly, risk) already
    multiplied together; this function only applies the playbook's own
    multiplier and the trend rule.
    """
    mult = state_multiplier(state)
    want_long = trend_signal(trend_z)
    new = book
    if book.stopped_out and not want_long:
        new = BookState(position=0.0, stopped_out=False)
    if book.position > 0 and book.stop_price is not None and close <= book.stop_price:
        return BookState(position=0.0, stopped_out=True), Order(0.0, f"stop hit at {close:.2f} <= {book.stop_price:.2f}")
    if state == "CRASH" and book.position > 0:
        return BookState(position=0.0, stopped_out=book.stopped_out), Order(0.0, "CRASH: flat")
    target = mult * size_cap if (want_long and not new.stopped_out) else 0.0
    if target > 0 and new.position == 0:
        stop = close * (1.0 - STOP_RVOL_MULT * max(rvol, 1e-6))
        return BookState(position=target, entry_price=close, stop_price=stop, stopped_out=False), Order(target, f"enter {target:.3f} ({state} x{mult}) stop {stop:.2f}")
    if target == 0 and new.position > 0:
        return BookState(position=0.0, stopped_out=new.stopped_out), Order(0.0, "trend off: exit" if not want_long else f"{state}: size to 0")
    if target != new.position:
        return BookState(position=target, entry_price=new.entry_price, stop_price=new.stop_price, stopped_out=new.stopped_out), Order(target, f"resize to {target:.3f} ({state} x{mult})")
    return new, Order(new.position, "hold")
