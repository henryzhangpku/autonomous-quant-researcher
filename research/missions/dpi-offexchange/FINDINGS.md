# Findings: off-exchange share of volume vs forward stock returns

Read with `RESULTS.md` (the pre-registered grid), `robustness.md` (deciles,
liquid subsample, top-1,000, monthly book) and `study.py` (design). First
full run 2026-10-04 on the research data lake: Quiver off-exchange history
(FINRA daily OTC volume per ticker, 2021-01 onward, 5,551 tickers) joined to
Alpaca adjusted daily bars, every signal lagged one trading day.

## Verdict

**The number Quiver sells, DPI (short share of off-exchange volume), predicts
nothing. The SHARE OF A STOCK'S TRADING THAT HAPPENS OFF-EXCHANGE does, and
in the direction a retail-attention story predicts: the most off-exchange
decile underperforms, every horizon, every year, in liquid names, after the
standard controls.** This is the first result today that clears its own
pre-registered bar, and it is the strongest thing in the stack.

## Numbers

Full universe (close >= $5, 20-day median dollar volume >= $5M; 1,234
decision days, median 1,758 names):

| Signal | IC 1d (t) | IC 5d (t) | IC 20d (t) | D10-D1 20d |
|---|---|---|---|---|
| dpi | -0.001 (-0.8) | -0.005 (-1.2) | -0.006 (-0.9) | -16 bps |
| otc_share = OTC volume / consolidated volume | -0.013 (-4.6) | -0.021 (-3.3) | -0.036 (-3.1) | -269 bps |
| short_vol_share = OTC short volume / consolidated volume | -0.013 (-5.8) | -0.023 (-4.5) | -0.038 (-3.6) | -260 bps |

t-stats on non-overlapping periods for h > 1. Ranking out 5-day reversal and
size each day changes nothing (control-adjusted IC -0.013 / -0.023 / -0.039).
Negative in every calendar year 2021 to 2026; weakest in 2023 (t -0.6 at 1d),
strongest in 2021 and 2026.

**Where it lives: the top decile.** Excess return vs the equal-weight
universe over 20 days, `short_vol_share` deciles: decile 10 = -187 bps
(t -3.95); decile 9 = -17 bps (t -1.2); deciles 1 to 4 = +31 to +60 bps
(t 2 to 4). The long side is real but modest; the short/avoid side is where
the money is.

**It survives where it can be traded.** Liquid subsample (close >= $10,
median dollar volume >= $50M, 798 names a day): IC 1d -0.016 (t -5.6), 20d
-0.044 (t -4.1), D10-D1 20d -192 bps (t -3.95). Top 1,000 names by dollar
volume: the same. The effect is not a micro-cap artifact.

**A monthly book.** First decision day of each month, hold 20 trading days,
long decile 1 / short decile 10 by `short_vol_share`, equal weight: **+209
bps per month, t 3.82, 69 months, positive in 72% of months, worst month
-1,949 bps.** Decile 10 alone: -155 bps per month excess (t -3.67, negative
in 72% of months). Decile 1 alone: +55 bps (t 3.1, positive in 68%).

## Reading

Off-exchange volume in US equities is mostly wholesaler-internalized retail
order flow plus dark-pool blocks. A stock whose trading is unusually
off-exchange is a stock retail is crowding into; the literature on retail
order imbalance and attention finds exactly this kind of subsequent
underperformance. Nothing here depends on the short ratio: the short share
of that off-exchange flow (DPI) adds no information once the share itself is
known. That is also why this is not the "dark pool short volume" story the
data vendor markets.

## What is not settled

- **Survivorship.** The universe is every ticker Quiver reports today;
  delisted names are absent. If retail-crowded names delist more often, the
  decile-10 underperformance is UNDERstated; if the reverse, overstated.
  Delisted-stock bars resolve this and should be the next data batch.
- **Implementation on the short side.** Decile 10 is retail-crowded names:
  borrow can be expensive or unavailable and squeezes happen. The worst
  month of the monthly book (-19.5%) is that risk realized. A long-only
  tilt (avoid decile 10, overweight deciles 1-4) keeps most of the
  cross-sectional spread without the borrow problem, at the cost of market
  beta.
- **Costs.** The daily-rebalanced spreads are gross of trading costs and the
  decile membership turns over fast at a daily cadence. The monthly book is
  the realistic shape: 20-day holds, two rebalances a month at most.
- **Crowding.** This is a measurable, public signal; the 2023 dip could be a
  regime or the first sign of arbitrage. The forward test decides.

## What to do with it

1. **Forward test, pre-registered** (`PREREGISTRATION.md`): the monthly book
   on the liquid subsample, decisions on the first trading day of each
   month from November 2026, scored by `book.py` from the lake with no
   discretion. Bar: positive mean monthly spread with t >= 2 after 12
   months, or it dies.
2. **A paper book in FST now**, long-only version first (deciles 1-4 equal
   weight vs the universe), so the execution path and costs are measured
   while the forward test runs.
3. **Delisted-stock bars** into the lake to close the survivorship question.
4. **Hand the signal family to the researcher loop** as an approved
   observation so it can propose structures (horizon, weighting, interaction
   with reversal) under the gate set, with 2026 sealed as holdout.
