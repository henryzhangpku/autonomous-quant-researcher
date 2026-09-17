# Preregistration: does the SmartFit pivot-anchored channel breakout hold?

Frozen 2026-08-27, before the snapshot carrying `chan_*`/`adx_14` was staged
and before any split of it was read. Nothing in this file was written after
seeing a payoff.

## What is under test, and why it is not ta-premises P2 again

`ta-premises` P2 tested a regression channel and failed:
`trend_slope_20 > 0 and trend_r2_20 > 0.5 and range_pos < 0.33` passed all
nine discovery gates at +16.26 bps and Sharpe 1.655, then printed −22.32 bps
and Sharpe −2.589 in validation. That result stands and is not being
relitigated.

But P2 was a *fixed-window mean-reversion* rule, and it differs from the
script named here in three ways that the daily family could not previously
express at all:

1. **The window is not fixed.** The channel re-anchors at confirmed swing
   pivots, so its length is a property of the tape rather than a parameter.
2. **The signal is a breakout, not a reversion.** P2 bought the lower band.
   This script's long signal is price leaving the *upper* band. Those are
   opposite trades on the same drawing.
3. **The fit gate is signed and is part of the signal.** `trend_r2_20` is
   squared and cannot say which way a channel leans; and in the script the
   |r| and minimum-bar tests sit *inside* the breakout definition, not beside
   it as optional filters.

So this is a different claim on a related construct, and P2's failure is a
reason to expect little — recorded here in advance, so a negative result
cannot later be presented as a surprise.

## Source of the parameters

Defaults were read from the published Pine v6 source
(`tradingview.com/script/lLbTezjC-`, © MarkitTick, CC BY-NC-SA 4.0), not from
the description page, so these are the author's numbers and not a paraphrase:

| input | default | used as |
|---|---|---|
| Auto Pivot Lookback | on → **10** for a 1-day chart | pivot confirmation window |
| Min Fit Quality (\|r\|) | **0.50** | inside the breakout test |
| Min Fit Bars | **5** | inside the breakout test |
| Deviation Z-Score | **1.96** | band half-width, in residual sigmas |
| Use ADX Filter | **off** | S3/S4 turn it on |
| ADX Threshold / Length | **20.0 / 14** | S3/S4 |
| Adaptive Filter | **None** | regression runs on raw close |

Daily is a first-class timeframe for this script — the auto-pivot ladder has
an explicit `tfSeconds <= 86400` rung — so this is not a test of an intraday
tool forced onto the wrong resolution.

Only the premise and these numeric defaults are ported. No Pine source is
redistributed in this repository.

### Two deviations from the source, recorded now rather than discovered later

- **Bar alignment.** The script reads `close[1]`/`high[1]`/`low[1]` because a
  Pine indicator evaluates on the live forming bar and the author wants
  confirmed data. The lab decides at session *t*'s close, where bar *t* is
  already confirmed, so bar *t* is the lab's equivalent of the script's `[1]`.
  Using the lagged bar instead would discard a session of information without
  adding any causal safety.
- **Anchor cap.** The script lets a channel grow unbounded, so its value at a
  session depends on how far back the chart happens to load. A frozen snapshot
  may not have that property — staging 2015+ and 2019+ would put different
  numbers on the same session — so the anchor is capped at 250 sessions, far
  beyond the length at which breakouts restart channels in practice.

## Claims under test (frozen)

One rule per claim, at the author's defaults, **no grid and no alternatives.**
Searching parameterisations is what produced the findings that died in digests
001 and 002. The |r| ≥ 0.50 and ≥ 5-bar gates are already inside
`chan_breakout`, exactly as they are inside the script's `isBullishBreak`.

**S1 — Upside breakout, long.** The author's default long signal: a confirmed
close above the upper band of a channel that passes its own fit test predicts
a higher next session. Rule: `chan_breakout > 0.5`, held long.

**S2 — Downside breakout, short.** The author's default short signal, and S1's
mirror. Rule: `chan_breakout < -0.5`, held short.

**S3 — Upside breakout with the ADX gate.** The author states the filter
exists "specifically to reduce breakout signals firing inside genuinely
trendless conditions." Rule: `chan_breakout > 0.5 and adx_14 >= 20.0`, long.

**S4 — Downside breakout with the ADX gate.** Rule:
`chan_breakout < -0.5 and adx_14 >= 20.0`, held short.

**S5 — Band reversion on the anchored channel (the P2 control).** Not a claim
the script makes; it is the *previous mission's* premise re-expressed on this
channel, and it is the only way to learn whether P2 failed because of the
premise or because of the fixed window. Rule:
`chan_pearson >= 0.5 and chan_z < -1.96`, held long.

S1/S2 and S3/S4 are mirrors and cannot both be right for the same reason.
Genuine support looks like one side working and its mirror failing; both
sides "working" is the instrument's drift wearing the channel's name.

## The discriminating tests

A threshold is only interesting if THAT threshold is special. This is the test
that killed the 0.618 level in `ta-premises` and Connors' 10 in
`ta-indicators`, and both times the profile turned out to be a gradient.

- **Fit quality.** Mean next-session return by decile of `chan_pearson` over
  the same sessions. If the profile is smooth through 0.50, the "statistical
  validity filter" is decoration and the rule is trend-following wearing a
  correlation coefficient. Reported for S1 and S2.
- **Band position.** Mean next-session return by decile of `chan_z`. If it is
  monotone, 1.96 is a point on a slope and any other multiple would serve.
- **Does the ADX filter earn its place?** S3 is compared against S1 **on S1's
  own sessions**, split into ADX ≥ 20 and ADX < 20. The filter is supported
  only if the ADX ≥ 20 subset beats the ADX < 20 subset. A filter that merely
  reduces trade count without improving per-session expectancy has removed
  sample, not noise. Same for S4 against S2.

## Universe, payoff and splits (frozen)

- Universe: the twelve house names — SPY, QQQ, IWM, NVDA, AAPL, MSFT, AMZN,
  GOOGL, META, TSLA, AVGO, IBIT.
- Decision once per session at the close; payoff `next_open_to_close`.
- Splits: the staging manifest's `suggested_splits`, taken as produced.
- Costs: 5 bps per trade, the daily-bars family default.
- Bars are split-adjusted. Unadjusted bars manufactured a −95% GOOGL session
  in an earlier mission; the adjustment is part of the frozen contract.

**Discovery is run first and alone.** Validation is a separate, deliberate
decision recorded in RESULTS.md, and the holdout is not read until discovery
and validation are both complete and this file has been compared against the
result. This matters more than usual here: on this universe the `ta-premises`
holdout and the `ta-indicators` validation *and* holdout are all still sealed,
and they are the same calendar windows. Opening them for this mission spends
them for every future one.

## Baseline

Every rule is judged against **being long every session in the split**, not
against being long the same sessions the rule fires on. The latter wording,
inherited from `ta-premises` and `ta-indicators`, is degenerate: for a
long-only rule the two series are identical by construction, and the
comparison printed back exactly equal for six of eight claims in
`ta-indicators`. That defect was recorded in that mission's RESULTS and is
corrected here rather than copied forward. Short claims (S2, S4) are judged
against the same always-long baseline with its sign reversed.

## Sample floor

A claim counts only if it reaches the family's single-name floor: 40
qualifying days in discovery, 15 in validation, 15 in holdout. `chan_breakout`
is an edge-triggered event — it fires once per excursion, not on every bar
price sits outside the band — so these rules are expected to be *sparse*, and
S1–S4 may well not reach the floor. A rule that fires too rarely is recorded
as **UNTESTABLE**, explicitly not as refuted. Too few trades is our ignorance,
not the market's answer.

## Multiple comparisons

Five claims. Under independence at a conventional threshold roughly 0.25 would
be expected to clear by chance; any count of survivors is reported against
that expectation. Survivors are checked for whether they are correlated
variants of one family — S1 and S3 differ only by the ADX gate and are not
independent evidence — before any of them is called a finding.

This is also the **third** mission to read this discovery window: `ta-premises`
spent 4 premises plus a control there, `ta-indicators` spent 8 claims. Adding
5 brings the running total to 18. That burden accumulates across missions even
though each was preregistered separately, and it is recorded here so a
survivor is read against 18 and not against 5.

## What this test cannot say

Five rules, twelve names, one parameterisation each, daily bars, one decision
per session at the close. It tests the script at its own defaults, not the
eight adaptive source filters, the channel-merge engine, the manual pivot
length, or any other timeframe. Costs are assumed, not measured fills. The
drawn segments and dashboard are not evaluated at all — only the signal.

A refuted rule does not mean no version of the idea can work; it means this
version, at the author's own settings, did not clear a bar set before looking.

Backtests are hypothetical, research-only evidence and are not investment
advice.
