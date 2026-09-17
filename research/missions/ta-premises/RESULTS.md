# Results: the premises under popular TradingView trend scripts

Evaluated 2026-08-23 against the preregistration frozen and committed in
`30898818d`, before any data for this test was staged.

Universe: SPY QQQ IWM NVDA AAPL MSFT AMZN GOOGL META TSLA AVGO IBIT.
Daily bars from Alpaca, 2019-04-01 → 2026-08-19, 21,019 rows,
`data_sha256 e0fc0f41…`. Decision once per session at the close, payoff
`next_open_to_close`, 5 bps per trade. Splits taken from the staging manifest
as produced: discovery 2019-04-01→2023-08-31, validation 2023-09-01→2025-02-25,
holdout 2025-02-26→2026-08-19.

## Verdicts

| Premise | Discovery | Validation | Verdict |
|---|---:|---:|---|
| P1 Fibonacci retracement | +3.06 bps | **−30.10** | NOT SUPPORTED |
| P2 Regression channel | **+16.26** (all 9 gates) | **−22.32** | NOT SUPPORTED |
| P3 Moving-average ribbon | −7.48 | −7.14 | REFUTED |
| P3 control, single MA | +0.03 | −4.19 | — |
| P4 Support, recency (intended) | −12.96 | −11.08 | REFUTED |
| P4 Support (rule as literally frozen) | −6.21 | −6.67 | REFUTED |
| Baseline, always long | +0.70 | −3.11 | — |

Net portfolio mean, bps per session, net of cost.

**Every premise failed at validation.** The frozen rule requires positive
expectancy in discovery AND validation AND holdout, so none can still reach
SUPPORTED.

**In validation, every premise also underperformed simply being long** —
−30.10, −22.32, −7.14 and −11.08 against the baseline's −3.11. The window was
mildly negative for the market; the indicators were substantially worse than
doing nothing in it.

## The holdout was NOT spent

No verdict could change: SUPPORTED requires all three splits positive, and all
four premises are already negative in validation. Reading a sealed window that
cannot alter an outcome spends evidence for nothing, so the holdout
(2025-02-26 → 2026-08-19) remains unread and available for a future
preregistered test on this universe.

## P2 is the instructive one

The regression channel — rising slope, `trend_r2_20 > 0.5`, price in the lower
third of its range — passed **all nine gates** in discovery at +16.26 bps and
Sharpe 1.655, then printed −22.32 bps and Sharpe −2.589 in validation. It was
never fitted: the rule and its parameters were frozen in advance. A plausible
premise, stated conventionally, can look excellent in one window and invert in
the next without anybody overfitting anything.

## The discriminating test: the Fibonacci level is not special

Next-session return by `rsv_20` decile (position in the 20-day range; a
retracement R sits at rsv = 1−R), bps net:

| rsv decile | discovery | validation | fib level |
|---|---:|---:|---|
| 0.0-0.1 | +12.84 | −5.98 | |
| 0.1-0.2 | +1.75 | +9.81 | |
| 0.2-0.3 | −0.75 | +7.36 | 0.786 retracement |
| **0.3-0.4** | **−9.00** | **−9.81** | **0.618 retracement** |
| 0.4-0.5 | −8.24 | +5.59 | |
| 0.5-0.6 | −3.22 | −10.71 | 0.500 retracement |
| 0.6-0.7 | +5.99 | +4.06 | 0.382 retracement |
| 0.7-0.8 | +3.83 | −11.07 | 0.236 retracement |
| 0.8-0.9 | +3.99 | −10.20 | |
| 0.9-1.0 | −2.70 | −3.26 | |

The preregistration required the 0.618 decile to beat both neighbours. It
loses to both, in both splits — it is the worst decile in discovery and among
the worst in validation. The most-cited level in retail technical analysis was
the worst place in the range to buy.

The deeper result is the profile itself. Correlation between the discovery and
validation decile shapes is **r = −0.055**, and **5 of 10** deciles keep their
sign across splits — a coin flip. The premise shared by Fibonacci levels,
support/resistance and order-block zones is that a level derived from recent
range predicts what happens next. Across these two windows that structure does
not persist at all.

## Corrections and deviations, recorded

1. **A units error in the preregistration.** P4 was frozen as
   `low_recency_20 <= 3`, written as if the feature were a day count. It is
   normalised 0–1, so the literal rule matches every row, and 1.0 means the low
   was set *today* — so a `<=` threshold selects *stale* lows, the opposite of
   the stated premise. Both readings were run and both are reported above;
   both are refuted. Reporting only the better one would have been undetectable
   from the outside.
2. **Stricter gates than preregistered.** The prereg quoted the single-name
   floor (40/15/15). This is a twelve-name universe, so the family's own
   contract applies: 80/35/35 trades, ≥6 active symbols, ≤0.35 concentration.
   The bar was raised, never lowered.
3. **An annotation error caught before publication.** The first decile table
   marked 0.618 at rsv 0.6–0.7. A 0.618 retracement from the high sits at
   rsv ≈ 0.382. Corrected above; the corrected mapping is what the verdict
   uses.

## What this cannot say

Four rules, twelve names, one parameterisation each, daily bars, two windows.
It tests these premises as conventionally stated, not every variant an author
might choose, and says nothing about a human reading a chart discretionarily.
Costs are assumed at 5 bps, not measured fills. A refuted premise does not mean
no version of the idea can work — it means this version, stated the usual way,
did not.

Backtests are hypothetical, research-only evidence and are not investment
advice.

---

# CORRECTION, 2026-08-23: the tape was not split-adjusted

Everything above was computed on daily bars that were never split-adjusted.
The bug was found hours later while sanity-checking newly added indicator
features, and is fixed in `7cd8978ae`: Alpaca's `StockBarsRequest` defaults to
`adjustment="raw"` and nothing overrode it, so every stock split rendered as a
one-day crash — GOOGL's 20:1 as −95.1%, AMZN's 20:1 as −94.9%, NVDA's 10:1 as
−89.9%. Eight such events sit inside this universe, and each corrupts moving
average, range and momentum features for weeks afterwards.

The whole test was re-run on split-adjusted bars
(`data_sha256 33d7c431…`, same universe, same frozen splits, same frozen rules).

## Verdicts after correction: unchanged

| Premise | Discovery | Validation | Verdict |
|---|---:|---:|---|
| P1 Fibonacci retracement | +4.06 bps | **−36.24** | NOT SUPPORTED |
| P2 Regression channel | **+14.72** (all 9 gates) | **−22.64** | NOT SUPPORTED |
| P3 Moving-average ribbon | −7.67 | −7.18 | REFUTED |
| P3 control, single MA | −0.06 | −4.61 | — |
| P4 Support, recency (intended) | −10.00 | −9.82 | REFUTED |
| P4 Support (as literally frozen) | −4.50 | −6.99 | REFUTED |
| Baseline, always long | +0.69 | −3.11 | — |

All four still fail at validation. All four still underperform the always-long
baseline in validation. The ribbon still loses to its own single-MA control in
both splits.

## The discriminating test after correction: unchanged

Next-session return by `rsv_20` decile, bps net, split-adjusted:

| rsv decile | discovery | validation | fib level |
|---|---:|---:|---|
| 0.0-0.1 | +15.94 | −1.51 | |
| 0.1-0.2 | +1.48 | +14.23 | |
| 0.2-0.3 | +1.04 | +5.87 | 0.786 retracement |
| **0.3-0.4** | **−8.52** | **−9.91** | **0.618 retracement** |
| 0.4-0.5 | −8.46 | +0.55 | |
| 0.5-0.6 | −2.90 | −13.03 | 0.500 retracement |
| 0.6-0.7 | +5.26 | +4.09 | 0.382 retracement |
| 0.7-0.8 | +3.02 | −11.07 | 0.236 retracement |
| 0.8-0.9 | +3.71 | −9.69 | |
| 0.9-1.0 | −3.23 | −3.63 | |

The 0.618 decile loses to both neighbours in both splits, exactly as before —
the preregistered criterion required it to beat both.

## One figure changed and is corrected here

Profile persistence between discovery and validation is **r = +0.179** on
split-adjusted data, with **6 of 10** deciles keeping their sign. The
uncorrected run reported r = −0.055 and 5 of 10. Both readings describe a weak,
unreliable profile, but −0.055 was quoted publicly and +0.179 is the correct
number. The qualitative claim stands: range position carries little persistent
information, and the Fibonacci level specifically is among the worst places in
the range.

## Why this is reported rather than quietly patched

The digest was already published when the bug was found. Re-running could have
overturned the headline; it did not. Had it done so, the correction would read
the same way. A process that only discloses errors when the conclusion happens
to survive is not a process.
