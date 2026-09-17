# Preregistration: do the premises under popular TradingView trend scripts hold?

Frozen 2026-08-23, before any dataset for this test is staged or inspected.

## Why premises and not scripts

TradingView's Trend Analysis listing is dominated by drawn constructs rather
than textbook indicators. Of the eleven scripts listed 2026-08-23, three are
Fibonacci tools (two of them in the top three by likes), two are drawn zones
(order blocks, support/resistance), two are regression channels or moving
average ribbons, one is chart-pattern detection, one is order flow and one is
an RSI scalper. There is no MACD and no ADX.

Testing eleven Pine implementations would test eleven authors. The scripts
share a small number of falsifiable premises, and those are what retail is
actually buying. This tests the premises.

Three of the eleven are out of scope and recorded as such now, not later:
Footprint Delta Auction Map needs order-flow data this family does not carry;
Adaptive Head and Shoulders needs pattern recognition that does not exist as a
feature; the RSI scalper needs both an RSI feature and intraday decisions,
which this family refuses by contract.

## Claims under test (frozen)

Each premise gets ONE rule with conventional parameters. No grid, no search,
no alternatives. Searching for the best parameterisation is what produced the
findings that died in digests 001 and 002.

**P1 — Fibonacci retracement.** In an uptrend, a pullback to the 0.618
retracement of the recent range predicts a higher next session.
Rule: `trend_slope_20 > 0 and 0.33 <= rsv_20 <= 0.45`.

**P2 — Regression channel.** A rising, well-fitted channel predicts a higher
next session when price sits at the lower band.
Rule: `trend_slope_20 > 0 and trend_r2_20 > 0.5 and range_pos < 0.33`.

**P3 — Moving average ribbon.** A bullishly stacked ribbon beats a single
moving average.
Rule: `dist_ma_5 > 0 and ma_5_20_spread > 0 and dist_ma_60 > 0`.
Single-MA control, evaluated identically: `dist_ma_20 > 0`.

**P4 — Support level, weighted by recency.** Price near a recently set 20-day
low predicts a higher next session.
Rule: `dist_low_20 < 0.02 and low_recency_20 <= 3`.

## The discriminating test for P1

A Fibonacci level is only interesting if THAT level is special. If next-session
return varies smoothly with position in the range, the pullback is momentum or
mean reversion and the Fibonacci number is decoration.

So P1 additionally reports next-session mean return by `rsv_20` decile across
the same sessions. P1 counts as support for Fibonacci ONLY if the decile
containing 0.618 beats both adjacent deciles. A monotone or flat profile is
recorded as "the level is not special" even if the rule itself is profitable.

## Universe, payoff and splits (frozen)

- Universe: the twelve house equities — SPY, QQQ, IWM, NVDA, AAPL, MSFT, AMZN,
  GOOGL, META, TSLA, AVGO, IBIT.
- Decision once per session at the close; payoff `next_open_to_close`.
- Splits: the staging manifest's `suggested_splits`, taken as produced. The
  holdout is not read until discovery and validation are both complete.
- Costs: 5 bps per trade, the daily-bars family default.

## Baseline

Every rule is judged against being unconditionally long the same instrument on
the same sessions. An indicator that is profitable but no better than always
being in the market has not demonstrated anything, and that is the outcome
retail evidence almost never reports.

## Sample floor

A premise counts only if its rule reaches the family's single-name floor —
40 qualifying days in discovery, 15 in validation, 15 in holdout. A rule that
fires too rarely is recorded as UNTESTABLE, explicitly not as refuted; too few
trades is our ignorance, not the market's answer.

## Decision rule (frozen)

A premise is **SUPPORTED** only if all four hold:

1. net expectancy positive in discovery AND validation AND holdout;
2. net expectancy above the always-long baseline in all three splits;
3. the sample floor met in all three splits;
4. for P1 only, the 0.618 decile beats both neighbours.

Anything else is **NOT SUPPORTED**. A negative median across the three splits
is **REFUTED**. P3 is supported only if the ribbon also beats its single-MA
control in all three splits; a ribbon that merely matches one moving average
is not evidence for ribbons.

## What this test cannot say

Four rules, twelve names, one parameterisation each, daily bars only. It tests
the premises as conventionally stated, not every variant a script author might
choose, and says nothing about discretionary use by a human reading a chart.
Costs are assumed, not measured fills. A refuted premise does not mean no
version of the idea can work; it means this version, stated the usual way, did
not.

Backtests are hypothetical, research-only evidence and are not investment
advice.
