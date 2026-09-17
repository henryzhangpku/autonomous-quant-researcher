# SPY dip-expression campaign — findings (2026-08-13)

Preregistered grid (`sweep_spy_dip_expression.py`): the validated dip signal
(SPY down ≥0.5% from open mid-afternoon; relative edge confirmed on both
2016-2021 and 2022-2024) frozen, expression searched — 54 cells over entry
{14:00, 14:30, 15:00} × {call debit, put credit} × width {1,2,3} × offset
{0,1,2}, evaluator `options_v2` (absolute-economics gate battery, cost 3% of
width, doubled-cost stress 6%).

## Verdict

**Zero discovery gate-passers. Nothing promoted; the shortlist never formed
and validation was never opened.** The SPY slot keeps its current strategy.

## The shape of the rejection

- Best cell — **14:00 ET, ATM 1-wide put credit**: net +2.3% of width per
  trade, 194 trades across six years, passed six of seven gates and failed
  ONLY the weekly-cluster bootstrap lower bound. A genuine near-miss: the
  mean is positive but not statistically separable from zero under weekly
  clustering.
- Structure ordering is consistent everywhere: put credit > call debit
  (collecting the bounce beats paying theta for it), ATM > OTM, narrow >
  wide, and 14:00 > 14:30 > 15:00 (the earlier dip read captures more of the
  afternoon).
- Every non-ATM and every 14:30+ cell fails multiple gates outright.

## Disposition

The dip signal stays on the shelf as REAL TIMING WITHOUT A PROMOTABLE
EXPRESSION at honest modeled costs. Two legitimate future evidence axes, in
preregistered campaigns only — not gate relaxation:

1. Re-evaluate the near-miss cell on REAL SPY 0DTE quotes (Alpaca OPRA,
   2024-02+) via the engine's leg_pricer path — an independent pricing axis
   that can tighten or kill the +2.3%.
2. Dip-day as a NIGHT filter for the promoted overnight SPX policy (does a
   down day make the 20:15 ET put credit richer?) — ~60 qualifying nights in
   the ES snapshot, at the discovery floor.

Backtests are hypothetical, research-only evidence and are not investment
advice.
