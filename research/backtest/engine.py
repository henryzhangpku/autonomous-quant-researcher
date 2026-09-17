"""The backtest driver: universe -> signal -> structure -> pricing -> settlement.

Design rules (each bought with a bug during the founding study -- see README):
control group always computed; D/W printed next to realized win rate; costs
frozen; small samples flagged; full configuration recorded in Result.meta so
the trial ledger can count what was tried.

The engine is data-agnostic: it consumes ``Session`` objects and (optionally)
a ``leg_pricer`` callback for real quotes. Everything here runs offline on
synthetic sessions, which is how the tests exercise it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from research.backtest.data import CandidateSessionView, Session
from research.backtest.structures import Structure, long_put

SignalFn = Callable[[CandidateSessionView, int], bool]
StructureFn = Callable[[CandidateSessionView, float], Structure]


@dataclass(frozen=True)
class CostModel:
    """Frozen: an experiment must not be able to lower its own costs.

    cost_frac is charged once per structure per round trip, as a fraction of
    width -- entry half-spread plus fees, with the settlement leg free
    (cash-settled 0DTE). Hedge legs added intraday pay it again.
    """

    cost_frac: float = 0.03


def wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (centre - half, centre + half)


def mde_two_arm(n_per_arm: int, base: float = 0.25) -> float:
    """Crude minimum detectable difference (pp) between two arms, 95%/80%.

    Normal approximation: delta ~= 2.8 * sqrt(2 p (1-p) / n). Printed so a
    null on a small sample reads "not measurable", never "no effect".
    """
    if n_per_arm <= 0:
        return float("inf")
    return 2.8 * math.sqrt(2 * base * (1 - base) / n_per_arm) * 100.0


def realized_vol(sessions: list[Session], window: int = 20) -> dict[str, float]:
    """20d close-to-close vol known STRICTLY at the end of each date (no lookahead)."""
    out: dict[str, float] = {}
    rets: list[float] = []
    prev: float | None = None
    for s in sessions:
        if prev is not None:
            rets.append(math.log(s.close / prev))
        if len(rets) >= window:
            w = rets[-window:]
            mean = sum(w) / window
            out[s.day] = math.sqrt(sum((r - mean) ** 2 for r in w) / (window - 1))
        prev = s.close
    return out


@dataclass
class Result:
    label: str
    rows: list[dict[str, Any]]
    control_rows: list[dict[str, Any]]
    meta: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def _summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
        n = len(rows)
        if n == 0:
            return {"n": 0}
        wins = sum(1 for r in rows if r["full_win"])
        profitable = sum(1 for r in rows if r["pnl_w"] > 0)
        dw = sum(r["dw"] for r in rows) / n
        pnl = sum(r["pnl_w"] for r in rows)
        lo, hi = wilson_ci(wins, n)
        return {
            "n": n, "full_win": wins / n, "ci": (lo, hi),
            "profitable": profitable / n, "dw": dw,
            "pnl_widths": pnl, "pnl_pct_per_trade": pnl / n * 100.0,
        }

    def report(self) -> str:
        sig = self._summarise(self.rows)
        ctl = self._summarise(self.control_rows)
        cost = self.meta.get("cost_frac", 0.0)
        lines = [f"== {self.label} =="]
        for name, s in (("signal", sig), ("control", ctl)):
            if s["n"] == 0:
                lines.append(f"  {name:<8} n=0")
                continue
            edge = (s["full_win"] - s["dw"] - cost) * 100.0
            lines.append(
                f"  {name:<8} n={s['n']:<5} full-win {s['full_win']:6.1%} "
                f"(CI {s['ci'][0]:5.1%}..{s['ci'][1]:5.1%})  D/W {s['dw']:5.1%}  "
                f"edge {edge:+6.2f}pp  P&L {s['pnl_pct_per_trade']:+6.2f}%w/trade"
            )
        if sig["n"] and ctl["n"]:
            lines.append(f"  lift     {(sig['full_win'] - ctl['full_win']) * 100:+.2f}pp over control")
        if sig["n"]:
            lines.append(f"  MDE      ~{mde_two_arm(sig['n'], max(sig['full_win'], 0.05)):.0f}pp "
                         f"(two-arm, 95%/80%) - differences below this are not measurable here")
        by_year: dict[str, list[dict[str, Any]]] = {}
        for r in self.rows:
            by_year.setdefault(r["date"][:4], []).append(r)
        for y in sorted(by_year):
            s = self._summarise(by_year[y])
            lines.append(f"    {y}  n={s['n']:3d}  full-win {s['full_win']:5.1%}  "
                         f"pnl {s['pnl_widths']:+7.2f}w")
        return "\n".join(lines)


class BacktestEngine:
    """Runs one declared configuration over a session universe. One call = one trial."""

    def __init__(self, costs: CostModel | None = None, vrp_mult: float = 1.10):
        self.costs = costs or CostModel()
        self.vrp_mult = vrp_mult

    def run(
        self,
        sessions: list[Session],
        entry_hm: tuple[int, int],
        signal: SignalFn,
        structure: StructureFn,
        label: str,
        leg_pricer: Callable | None = None,
        raw_price_provider: Callable | None = None,
        hedge_put_on_target: bool = False,
    ) -> Result:
        """Score `structure` on signal days against the every-day control.

        ``leg_pricer(day_date, legs, entry_minute) -> prices | None`` switches
        entry pricing to real quotes; None falls back to skip (never silently
        to the model, so the two layers cannot blur together).

        Real option quotes require
        ``raw_price_provider(day_date, [entry_minute, 960])``. Option strikes
        and settlement live in unadjusted price space, while stored sessions
        may be dividend-adjusted. A bound ``AlpacaOptionData.leg_pricer``
        discovers its matching ``raw_prices`` method automatically.

        ``hedge_put_on_target`` implements the article's late-day hedge: at the
        first intraday touch of the short call strike after entry, buy an ATM
        0DTE put (model-priced with remaining time; costed like any leg) and
        hold both to the close. TRIAL 5 of the founding family.
        """
        entry_minute = entry_hm[0] * 60 + entry_hm[1]
        if leg_pricer is not None and raw_price_provider is None:
            owner = getattr(leg_pricer, "__self__", None)
            raw_price_provider = getattr(owner, "raw_prices", None)
        if leg_pricer is not None and raw_price_provider is None:
            raise ValueError("real option pricing requires a raw_price_provider")
        rv = realized_vol(sessions)
        rows: list[dict[str, Any]] = []
        control_rows: list[dict[str, Any]] = []
        skipped: list[str] = []

        prev_day: str | None = None
        for s in sessions:
            sigma_daily = rv.get(prev_day) if prev_day else None
            prev_day = s.day
            if sigma_daily is None or not s.is_full_day():
                continue
            entry_px = s.price_at(entry_minute)
            if entry_px is None:
                continue

            settlement_px = s.close
            if raw_price_provider is not None:
                raw_prices = raw_price_provider(
                    date.fromisoformat(s.day), [entry_minute, 16 * 60]
                )
                if raw_prices is None:
                    skipped.append(f"{s.day}: raw underlying unavailable")
                    continue
                entry_px = raw_prices[entry_minute]
                settlement_px = raw_prices[16 * 60]

            remaining = (16 * 60 - entry_minute) / (6.5 * 60)
            sigma_t = sigma_daily * self.vrp_mult * math.sqrt(remaining)
            candidate_session = CandidateSessionView.at(s, entry_minute)
            struct = structure(candidate_session, entry_px)
            width = struct.width

            if leg_pricer is not None:
                legs = [(l.cp, l.strike) for l in struct.legs]
                prices = leg_pricer(date.fromisoformat(s.day), legs, entry_minute)
                if prices is None:
                    skipped.append(s.day)
                    continue
                debit = struct.entry_cost(prices)
            else:
                debit = struct.model_entry_cost(entry_px, sigma_t)
            if width > 0 and not (-width < debit < width):
                skipped.append(f"{s.day}: degenerate entry {debit:.2f}")
                continue

            pnl = struct.settle(settlement_px) - debit - self.costs.cost_frac * width

            hedge_note = None
            if hedge_put_on_target:
                target = struct.short_strike("C")
                touch = s.first_touch(target, entry_minute) if target else None
                if touch is not None:
                    spot = s.price_at(touch) or target
                    hedge = long_put(float(round(spot)))
                    h_remaining = max((16 * 60 - touch) / (6.5 * 60), 1e-6)
                    h_sigma_t = sigma_daily * self.vrp_mult * math.sqrt(h_remaining)
                    h_cost = hedge.model_entry_cost(spot, h_sigma_t)
                    pnl += hedge.settle(settlement_px) - h_cost - self.costs.cost_frac * width
                    hedge_note = touch

            # Full win = the structure settles at its best possible value:
            # width for a net-debit vertical, 0 for a net-credit one. The old
            # `settle >= width` was debit-specific and would mark every credit
            # spread a loser.
            max_settle = width if debit >= 0 else 0.0
            # dw is the market-implied probability of the debit buyer's full
            # win. For a credit structure the same quantity is 1 - credit/width
            # -- i.e. still "P(full win for whoever is long)", so the report's
            # win-rate-vs-price comparison stays meaningful on both sides.
            if not width:
                implied = 0.0
            elif debit >= 0:
                implied = debit / width
            else:
                implied = 1.0 - (-debit / width)
            row = {
                "date": s.day,
                "dw": implied,
                "full_win": struct.settle(settlement_px) >= max_settle - 1e-9 if width else pnl > 0,
                "pnl_w": (pnl / width) if width else pnl,
                "hedged_at": hedge_note,
            }
            control_rows.append(row)
            if signal(candidate_session, entry_minute):
                rows.append(row)

        return Result(
            label=label, rows=rows, control_rows=control_rows,
            meta={
                "entry_hm": entry_hm, "cost_frac": self.costs.cost_frac,
                "vrp_mult": self.vrp_mult, "real_quotes": leg_pricer is not None,
                "raw_prices": raw_price_provider is not None,
                "hedge_put_on_target": hedge_put_on_target,
                "sessions": len(sessions), "skipped": len(skipped),
            },
        )
