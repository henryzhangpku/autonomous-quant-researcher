# Preregistration: do the standard oscillators hold on the house universe?

Frozen 2026-08-24, before any split of `ta-indicators` is evaluated. Nothing
in this file was written after reading a payoff.

## Why this mission exists

`ta-indicators` has been staged since 2026-08-23 and carries six features no
question has ever touched: `rsi_2`, `rsi_14`, `macd_hist`, `bb_pctb`,
`bb_bandwidth`, `streak`. Across 845 autopilot sessions, zero mention MACD,
zero mention Bollinger, and the single RSI hit is a hand-written analog that
predates the feature. The forge sees all 51 names in one comma-joined list and
samples from the head of its own prior, so the tail has stayed dark.

This is not the same claim as `ta-premises`. That mission tested *drawn
constructs* — Fibonacci retracements, regression channels, MA ribbons, recency
weighted support — and went 0 of 4. It deliberately excluded the oscillators:
its own scope note recorded that the RSI scalper needed "an RSI feature and
intraday decisions" the family did not then carry. The feature now exists.
These are the textbook indicators themselves, at their conventional settings.

## Claims under test (frozen)

One rule per claim, conventional parameters, **no grid and no alternatives**.
Searching parameterisations is what produced the findings that died in digests
001 and 002. Each directional claim is stated in the direction its own
literature states it.

**I1 — RSI-2, oversold (Connors).** A 2-period RSI below 10 predicts a higher
next session. Rule: `rsi_2 < 10`.

**I2 — RSI-2, overbought (Connors).** Above 90 predicts a lower next session.
Rule: `rsi_2 > 90`, held short.

**I3 — RSI-14, oversold (Wilder).** Below 30 predicts a higher next session.
Rule: `rsi_14 < 30`.

**I4 — RSI-14, overbought (Wilder).** Above 70 predicts a lower next session.
Rule: `rsi_14 > 70`, held short.

**I5 — MACD histogram.** A positive histogram signals bullish momentum and
predicts a higher next session. Rule: `macd_hist > 0`.

**I6 — Bollinger %B, lower band.** Price below the lower band reverts up.
Rule: `bb_pctb < 0`.

**I7 — Streak reversal.** Three or more consecutive down closes predict a
higher next session. Rule: `streak <= -3`.

**I8 — Streak continuation.** Three or more consecutive up closes predict a
higher next session. Rule: `streak >= 3`. I7 and I8 are stated together on
purpose: they are mirror claims and cannot both be right for the same reason.

### Not a directional claim, and recorded as such now

`bb_bandwidth` encodes the squeeze premise — low bandwidth precedes expansion.
That premise says nothing about **which way**, so it cannot be tested as a
direction rule without inventing content its own literature does not claim.
It is therefore tested as a magnitude claim only: mean |next-session return|
in the lowest `bb_bandwidth` decile versus all other sessions. A squeeze that
precedes larger moves in either direction is SUPPORTED as a volatility
statement and explicitly **not** as a trading rule.

## The discriminating test (applies to I1–I6)

A threshold is only interesting if THAT threshold is special. If next-session
return varies smoothly with the indicator, the rule is momentum or mean
reversion wearing an oscillator's name and the number is decoration.

So each of I1–I6 additionally reports mean next-session return by decile of
its own indicator across the same sessions. A claim counts as support for the
indicator ONLY if the decile containing its threshold beats the adjacent
decile on the interior side. A monotone or flat profile is recorded as "the
threshold is not special" even where the rule itself is profitable.

This is the test that killed P1 in `ta-premises`, and it is the reason that
result was worth publishing.

## Universe, payoff and splits (frozen)

- Universe: the twelve house names — SPY, QQQ, IWM, NVDA, AAPL, MSFT, AMZN,
  GOOGL, META, TSLA, AVGO, IBIT.
- Decision once per session at the close; payoff `next_open_to_close`.
- Splits: the staging manifest's `suggested_splits`, taken as produced —
  discovery 2019-04-01 → 2023-08-31, validation 2023-09-01 → 2025-02-25,
  holdout 2025-02-26 → 2026-08-19.
- **The holdout is not read until discovery and validation are both complete
  and this file has been compared against the result.**
- Costs: 5 bps per trade, the daily-bars family default.
- Bars are split-adjusted. Unadjusted bars manufactured a −95% GOOGL session
  and an RSI of 10.2 out of a 20:1 split in an earlier mission; the adjustment
  is part of the frozen contract, not a preference.

## Baseline

Every rule is judged against being unconditionally long the same instrument on
the same sessions. An indicator that is profitable but no better than always
being in the market has demonstrated nothing — and that is the comparison
retail evidence almost never reports. Short rules (I2, I4) are judged against
the same always-long baseline with its sign reversed on those sessions.

## Sample floor

A claim counts only if it reaches the family's single-name floor: 40
qualifying days in discovery, 15 in validation, 15 in holdout. A rule that
fires too rarely is recorded as **UNTESTABLE**, explicitly not as refuted.
Too few trades is our ignorance, not the market's answer.

`streak` is signed (−10..+10) in this family, so I7 can fire. This was
verified by reading the feature definition in `prepare_bars_universe.py`, not
by reading data. It is recorded because the Kalshi hourly mission lost eight
hypotheses to an unsigned `streak_up` that could never express a down-run.

## Multiple comparisons

Eight directional claims are evaluated. Under independence at a conventional
threshold, roughly 0.4 would be expected to clear validation by chance. Any
count of survivors is reported **against that expectation**, and survivors are
checked for whether they are correlated variants of one family — as the Kalshi
reversal hits turned out to be — before any of them is called a finding.

I1/I2 and I3/I4 are the same indicator at both ends, and I7/I8 are mirrors.
Genuine support looks like one side working and its mirror failing. Both sides
"working" is a sign the effect is the instrument's drift, not the indicator.

## Decision rule (frozen)

A claim is **SUPPORTED** only if all four hold:

1. net expectancy positive in discovery AND validation AND holdout;
2. net expectancy above the baseline in all three splits;
3. the sample floor met in all three splits;
4. the threshold decile beats its interior neighbour.

Anything else is **NOT SUPPORTED**. A negative net median across the three
splits is **REFUTED**. A claim passing 1–3 but failing 4 is recorded as
"profitable but the threshold is not special" — that is a real outcome and
gets published as one.

## What this test cannot say

Eight rules, twelve names, one parameterisation each, daily bars, one decision
per day at the close. It tests the indicators as conventionally stated, not
every variant an author might choose, and says nothing about discretionary use
by a human reading a chart, nor about intraday use. Costs are assumed, not
measured fills. A refuted indicator does not mean no version of the idea can
work; it means this version, stated the usual way, did not.

Backtests are hypothetical, research-only evidence and are not investment
advice.
