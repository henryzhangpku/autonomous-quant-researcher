# Results: the SmartFit pivot-anchored channel breakout

Discovery run 2026-08-27 against the preregistration frozen the same day
(`PREREGISTRATION.md`, sha256
`b3824b173bf82bfb2672cf33ba501fdaa9b9d25f0405352370024c082e0e86c5`).
Evaluator `research.validators.bars_universe`, unmodified, one subprocess per
claim. Runner: `research/experiments/sweep_smartfit_channels.py`.
Snapshot `data_sha256 9e646fcb…`, 21,007 rows, 2019-04-01 → 2026-08-18.

## Verdict

**NOTHING CLEARED DISCOVERY. All five claims were rejected. Validation and
holdout were never read and stay sealed.**

And unusually for this lab, the rejection is not a shrug: every claim met its
sample floor comfortably — 144 to 206 trades across 11 names and 113 to 141
sessions, against a `min_trades` gate of 80 — so all five failed on
**performance**, not on sample. The preregistration anticipated that
edge-triggered breakouts might be too sparse to test and reserved UNTESTABLE
for that case. It was not needed. This test had the power to see an effect and
saw the opposite one.

## The table

Discovery 2019-04-01 → 2023-08-30, 12,243 symbol-sessions, 11 names, payoff
`next_open_to_close`, 5 bps per trade. Baseline — long every session, every
name — is **+0.82 bps**.

| claim | rule | n | per-trade | portfolio | vs baseline | Sharpe |
|---|---|---:|---:|---:|---:|---:|
| S1 breakout long | `chan_breakout > 0.5` | 187 | −20.99 | **−12.59** | −13.41 | −0.90 |
| S2 breakout short | `chan_breakout < -0.5` | 206 | −49.46 | **−42.49** | −41.67 | −2.71 |
| S3 breakout long + ADX | `… and adx_14 >= 20` | 144 | −16.70 | **−13.59** | −14.41 | −0.90 |
| S4 breakout short + ADX | `… and adx_14 >= 20` | 160 | −59.28 | **−52.34** | −51.51 | −3.05 |
| S5 band reversion (P2 control) | `chan_pearson >= 0.5 and chan_z < -1.96` | 177 | +2.22 | **−7.36** | −8.18 | −0.63 |

All five fail `positive_net_mean`, `positive_both_halves`,
`survives_double_cost` and `survives_without_best_day`; S1–S4 also fail
`positive_symbol_fraction`.

## One — the signal is not absent, it is inverted, on both sides at once

S1 is long after an upside breakout and loses. S2 is **short** after a
downside breakout and loses more. Read together those say the same thing from
opposite ends: after price leaves the upper band the next session falls, and
after it leaves the lower band the next session rises.

Channel breakouts on daily bars **revert**. They do not continue. That is the
precise opposite of what the signals are for, and it is the cleanest result
this mission produced.

The preregistration warned that a claim and its mirror both "working" would
indicate drift rather than the indicator. Here both mirrors *lose*, which is
the same diagnostic run backwards and rules the drift explanation out: an
instrument-drift artifact cannot make a long rule and a short rule lose
simultaneously. Something real is happening at the band; it just points the
other way.

S5 is the corroboration. It is the only claim with a positive per-trade
number (+2.22 bps) and it is the only one that trades *reversion* — buying the
lower band rather than selling the break of it. It still fails, and the reason
is instructive (below), but its sign is consistent with S1–S4's.

## Two — the thresholds are decoration and the gradient runs the wrong way

Mean next-session return by decile (discovery, gross bps):

- **`chan_z`** (position in the channel, in residual sigmas):
  **30.6**, 9.6, −0.1, 8.9, 5.2, −8.3, 5.0, 3.2, 4.0, **0.2**
- **`chan_pearson`** (signed fit quality):
  **22.8**, 6.6, −0.7, 4.3, −1.6, −0.4, 5.8, 3.6, 5.7, **12.2**
- **`adx_14`**: 4.8, 3.1, 1.6, −0.2, 2.8, 7.8, −1.5, 10.6, **15.3**, **14.1**

`chan_z` declines from +30.6 bps at the bottom of the channel to roughly zero
at the top, smoothly. There is no step at 1.96 or anywhere else. The author's
deviation z-score is a point on a slope, and the slope's payoff is at the
*lower* end — which is why the breakout rules, which fire at both extremes and
trade in the direction of the break, are on the wrong side of it. This is the
same shape that killed the 0.618 Fibonacci level in `ta-premises` and Connors'
10 in `ta-indicators`. Three missions, three indicators, the same finding.

`chan_pearson` is more interesting and is **U-shaped, not monotone**: the best
decile is the most negative correlation (+22.8 bps) and the second best is the
most positive (+12.2), with the unfitted middle flat to negative. So fit
quality does carry information — strongly-fitted channels of *either* sign
precede larger up-moves — but the **sign does not work as a bias gauge**. The
script colours a strongly negative channel bearish; that decile was the
best-performing one in the window. |r| ≥ 0.50 is not a special line either;
the profile passes through it without a step.

`adx_14` carries the cleanest gradient of the three, rising to +15.3 bps in
the ninth decile. But the ninth decile begins around ADX 32, and the author's
threshold of 20 sits at the boundary of the *fourth* — in the flat, slightly
negative middle. The default is well below where the gradient lives.

## Three — the ADX filter is directional, and on one side it selects the worse half

Splitting each unfiltered breakout claim's own firing sessions at ADX 20 —
which asks the author's question properly, rather than comparing two
differently-sized samples:

| | kept (ADX ≥ 20) | dropped (ADX < 20) | filter helps? |
|---|---:|---:|:--:|
| S1 long | −16.70 bps (n=144) | −35.38 bps (n=43) | yes |
| S2 short | −59.28 bps (n=160) | −15.32 bps (n=46) | **no** |

On the long side the filter removes the worse trades, as advertised. On the
short side it removes the *better* ones and keeps the half that loses nearly
four times as much. The filter is not a general improvement to breakout
quality; its effect depends on the direction of the trade, which the stated
rationale — suppressing signals "inside genuinely trendless conditions" —
does not predict.

And note S3 against S1 at the portfolio level: the filter improved the trade
mean (−20.99 → −16.70) while making the **session** mean slightly worse
(−12.59 → −13.59). The trades it removed were disproportionately on sessions
that also carried other trades, so dropping them concentrated the book without
improving it. Filtering by trade quality is not the same as filtering by
portfolio quality, and only the second one is what a book earns.

## Four — the per-trade / portfolio gap, again

S5 posts **+2.22 bps per trade** and **−7.36 bps per portfolio session**, a
gap of 9.6 bps that flips the sign. `net_trade_mean_bps` weights every fill
equally; `net_portfolio_mean_bps` weights every session equally, because
several names hitting the lower band on the same morning is one bet on one
market, not several independent draws. A channel breakout is a broadly
correlated condition over a correlated universe, so this divergence is
structural for this whole family of rule, exactly as it was for RSI-14
oversold in `ta-indicators` (where the same gap was 44.68 bps).

Read `net_portfolio_mean_bps` first. A large gap between it and the trade mean
is a correlated-firing warning, not an edge.

## What was NOT spent

Validation and holdout were not read. Under the frozen decision rule
SUPPORTED requires positive expectancy in discovery **and** validation **and**
holdout; all five claims are already negative in discovery, so no verdict
could change. Reading a sealed window that cannot alter an outcome spends
evidence for nothing — the same reasoning `ta-premises` used to preserve its
holdout.

This matters more than usual here: on this universe the `ta-premises` holdout
and the `ta-indicators` validation and holdout are the same calendar windows,
and all of them remain available.

## Preregistration defect, found on execution

The preregistration states a sample floor of "40 qualifying days in discovery,
15 in validation, 15 in holdout" — the family's *single-name* floor, carried
over from `ta-indicators`. The spec actually used is the family's twelve-name
standard, `min_trades` 80/35/35, which is stricter. Nothing turns on it: every
claim cleared both floors, so no claim was recorded UNTESTABLE under either
number. Recorded here rather than quietly reconciled, and the wording should
be fixed before it is reused a third time.

## Limitations

Eleven names, not twelve: **IBIT has no data before 2024** and is absent from
discovery entirely — the same gap `ta-indicators` hit. One parameterisation
per claim, daily bars, one decision per session at the close, costs assumed
rather than filled. Discovery spans 2019-04 to 2023-08 and contains the COVID
drawdown and rebound, which is the most likely home of the episodic behaviour
`positive_both_halves` objects to; this was not verified by sub-period
attribution, since doing so would be reading discovery a second time to
explain away a rejection.

The test covers the script at its shipped defaults. It does not test the eight
adaptive source filters, the channel-merge engine, a manual pivot length, or
any timeframe other than daily. Two deliberate deviations from the source —
bar alignment and a 250-session anchor cap — are documented in the
preregistration and in `_channel_series`.

This is also the third mission to read this discovery window (`ta-premises` 4
premises plus a control, `ta-indicators` 8 claims, these 5), so the running
total is 18 preregistered claims against it. No survivor emerged here, so no
multiple-comparison correction was needed; the count is recorded for the next
mission that does find one.

A refuted rule does not mean no version of the idea can work. It means this
version, at the author's own settings, did not clear a bar set before looking
— and that the direction it fails in is consistent enough across five rules to
be worth more than the rules themselves.

Backtests are hypothetical, research-only evidence and are not investment
advice.
