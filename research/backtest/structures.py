"""Option structures: legs, settlement payoff, and modeled pricing. Pure functions.

A structure is a set of legs. Sign convention: quantity +1 is long, -1 is
short. Settlement is cash-style at the underlying close (the 0DTE case this
engine exists for); early assignment is out of scope and documented as such.

The Black-Scholes layer is deliberately minimal (r=0, total-vol form): on a
same-day horizon rates are noise, and the founding study validated this model
against real SPY 0DTE quotes to within ~2pp of D/W. It remains a model; when
real leg bars exist (2024-02+), the engine prefers them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(cp: str, s: float, k: float, sigma_t: float) -> float:
    """Black-Scholes with r=0; sigma_t is TOTAL vol over the horizon (sigma*sqrt(T))."""
    if sigma_t <= 0:
        intrinsic = s - k if cp == "C" else k - s
        return max(intrinsic, 0.0)
    d1 = (math.log(s / k) + 0.5 * sigma_t * sigma_t) / sigma_t
    d2 = d1 - sigma_t
    call = s * norm_cdf(d1) - k * norm_cdf(d2)
    return call if cp == "C" else call - s + k    # put via parity, r=0


@dataclass(frozen=True)
class Leg:
    cp: str        # "C" or "P"
    strike: float
    qty: int       # +1 long, -1 short

    def settle(self, close: float) -> float:
        intrinsic = max(close - self.strike, 0.0) if self.cp == "C" else max(self.strike - close, 0.0)
        return self.qty * intrinsic

    def model_price(self, spot: float, sigma_t: float) -> float:
        return bs_price(self.cp, spot, self.strike, sigma_t)


@dataclass(frozen=True)
class Structure:
    """A named set of legs entered simultaneously."""

    legs: tuple[Leg, ...]
    label: str

    @property
    def width(self) -> float:
        """Strike span; the natural P&L unit for verticals."""
        ks = [l.strike for l in self.legs]
        return max(ks) - min(ks) if len(ks) > 1 else max(ks)

    def entry_cost(self, prices: dict[tuple[str, float], float]) -> float:
        """Net debit (positive) or credit (negative) from per-leg prices."""
        return sum(leg.qty * prices[(leg.cp, leg.strike)] for leg in self.legs)

    def model_entry_cost(self, spot: float, sigma_t: float) -> float:
        return sum(leg.qty * leg.model_price(spot, sigma_t) for leg in self.legs)

    def settle(self, close: float) -> float:
        return sum(leg.settle(close) for leg in self.legs)

    def short_strike(self, cp: str) -> float | None:
        shorts = [l.strike for l in self.legs if l.qty < 0 and l.cp == cp]
        return shorts[0] if shorts else None


# -- factories ---------------------------------------------------------------

def call_debit_spread(entry_px: float, width: float, otm_offset: float = 0.0,
                      round_strikes: bool = True) -> Structure:
    """Long call at/above spot, short call `width` higher. The founding structure.

    ``otm_offset`` shifts the whole spread OTM (trial 2 showed why not to:
    ratio-shopping slides you along the pricing curve, it does not beat it).
    """
    k_long = entry_px + otm_offset
    if round_strikes:
        k_long = float(round(k_long))
    k_short = k_long + width
    return Structure(
        legs=(Leg("C", k_long, +1), Leg("C", k_short, -1)),
        label=f"call debit {k_long:g}/{k_short:g}",
    )


def put_credit_spread(entry_px: float, width: float, otm_offset: float = 0.0,
                      round_strikes: bool = True) -> Structure:
    """Short put below spot, long put `width` lower. The mirror-image bull bet:
    same exposure family as the debit call spread with the win/loss profile
    inverted. Entry cost is negative (a credit)."""
    k_short = entry_px - otm_offset
    if round_strikes:
        k_short = float(round(k_short))
    k_long = k_short - width
    return Structure(
        legs=(Leg("P", k_short, -1), Leg("P", k_long, +1)),
        label=f"put credit {k_long:g}/{k_short:g}",
    )


def long_put(strike: float) -> Structure:
    """Single long put -- the article's late-day hedge leg."""
    return Structure(legs=(Leg("P", strike, +1),), label=f"long put {strike:g}")


# -- index-grid verticals (SPX complex: strikes snap to a 5-point grid) -------

def _snap(value: float, step: float) -> float:
    return float(round(value / step) * step) if step > 0 else value


def put_debit_spread(entry_px: float, width: float, otm_offset: float = 0.0,
                     strike_step: float = 1.0) -> Structure:
    """Long put at/below spot, short put `width` lower. The bearish debit twin
    of ``call_debit_spread``: same defined-risk shape, direction inverted."""
    k_long = _snap(entry_px - otm_offset, strike_step)
    k_short = k_long - width
    return Structure(
        legs=(Leg("P", k_long, +1), Leg("P", k_short, -1)),
        label=f"put debit {k_short:g}/{k_long:g}",
    )


def call_credit_spread(entry_px: float, width: float, otm_offset: float = 0.0,
                       strike_step: float = 1.0) -> Structure:
    """Short call above spot, long call `width` higher. The bearish credit twin
    of ``put_credit_spread``. Entry cost is negative (a credit)."""
    k_short = _snap(entry_px + otm_offset, strike_step)
    k_long = k_short + width
    return Structure(
        legs=(Leg("C", k_short, -1), Leg("C", k_long, +1)),
        label=f"call credit {k_short:g}/{k_long:g}",
    )


def spx_call_debit_spread(entry_px: float, width: float, otm_offset: float = 0.0) -> Structure:
    """SPX-grid bullish debit vertical: strikes snapped to the 5-point grid."""
    k_long = _snap(entry_px + otm_offset, 5.0)
    k_short = k_long + width
    return Structure(
        legs=(Leg("C", k_long, +1), Leg("C", k_short, -1)),
        label=f"call debit {k_long:g}/{k_short:g}",
    )


def spx_put_debit_spread(entry_px: float, width: float, otm_offset: float = 0.0) -> Structure:
    """SPX-grid bearish debit vertical."""
    return put_debit_spread(entry_px, width, otm_offset, strike_step=5.0)


def spx_put_credit_spread(entry_px: float, width: float, otm_offset: float = 0.0) -> Structure:
    """SPX-grid bullish credit vertical."""
    k_short = _snap(entry_px - otm_offset, 5.0)
    k_long = k_short - width
    return Structure(
        legs=(Leg("P", k_short, -1), Leg("P", k_long, +1)),
        label=f"put credit {k_long:g}/{k_short:g}",
    )


def spx_call_credit_spread(entry_px: float, width: float, otm_offset: float = 0.0) -> Structure:
    """SPX-grid bearish credit vertical."""
    return call_credit_spread(entry_px, width, otm_offset, strike_step=5.0)
