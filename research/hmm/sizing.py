"""Size from calibrated confidence (spec: sizing).

    size_cap = min(KELLY_FRACTION * f*, MAX_FRACTION) * P(active state) * (1 - entropy)

f* = (b p - q) / b from the base strategy's own measured win rate p and
payoff ratio b on the fit window; never assumed. If f* <= 0 the size is 0.
Before the probability can scale size, it must be calibrated: the rolling
Brier score and reliability curve of P(state) against the realized next
state on the trailing CALIBRATION_WINDOW decisions must show ECE below
MAX_ECE; until then the size is the floor.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

KELLY_FRACTION = 0.25
MAX_FRACTION = 1.0
FLOOR_FRACTION = 0.1
CALIBRATION_WINDOW = 200
MAX_ECE = 0.05


def kelly_fraction(p: float, b: float) -> float:
    if b <= 0:
        return 0.0
    return (b * p - (1.0 - p)) / b


@dataclass(frozen=True)
class Calibration:
    brier: float
    ece: float
    n: int
    bins: tuple[tuple[float, float, int], ...]  # (mean predicted, mean realized, count)

    @property
    def ok(self) -> bool:
        return self.n >= CALIBRATION_WINDOW and self.ece <= MAX_ECE


def calibration(predicted: np.ndarray, realized: np.ndarray, n_bins: int = 10) -> Calibration:
    """predicted: P(the state the model calls) at t; realized: 1 if that state
    was still the argmax at t+1 (the only truth a state model has)."""
    predicted = np.asarray(predicted, dtype=float)
    realized = np.asarray(realized, dtype=float)
    n = len(predicted)
    if n == 0:
        return Calibration(float("nan"), float("nan"), 0, ())
    brier = float(np.mean((predicted - realized) ** 2))
    edges = np.linspace(0, 1, n_bins + 1)
    bins = []
    ece = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (predicted >= lo) & (predicted < hi if hi < 1 else predicted <= hi)
        if mask.sum() == 0:
            continue
        mp, mr, c = float(predicted[mask].mean()), float(realized[mask].mean()), int(mask.sum())
        bins.append((mp, mr, c))
        ece += abs(mp - mr) * c / n
    return Calibration(brier, float(ece), n, tuple(bins))


def size_cap(*, p_active: float, entropy: float, kelly_p: float, kelly_b: float, calibrated: bool, switch_multiplier: float) -> float:
    f_star = kelly_fraction(kelly_p, kelly_b)
    if f_star <= 0:
        return 0.0
    base = min(KELLY_FRACTION * f_star, MAX_FRACTION)
    if not calibrated:
        return min(base, FLOOR_FRACTION) * switch_multiplier
    return base * float(np.clip(p_active, 0, 1)) * float(np.clip(1.0 - entropy, 0, 1)) * switch_multiplier
