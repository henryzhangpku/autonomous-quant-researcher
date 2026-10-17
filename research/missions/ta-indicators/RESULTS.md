# Results: do the standard oscillators hold on the house universe?

Discovery run 2026-08-24 against the preregistration frozen the same day
(`PREREGISTRATION.md`, sha256 `abc474765b6d15c907a05f3522018402b8e7c5f30ebe84c118164d3e3b730e6a`).
Evaluator `research.validators.bars_universe`, unmodified, one subprocess per
claim. Runner: `research/experiments/sweep_ta_indicators.py`.

## Verdict

**NOTHING CLEARED DISCOVERY under the frozen gate battery. All eight claims
were rejected. Validation and holdout were never read and stay sealed for any
future mission on this surface.**

Every one of the eight fails `positive_both_halves`: each has a negative half
inside discovery. Whatever these indicators are measuring is episodic, not a
standing property of the tape.

## The table

Discovery 2019-04-01 → 2023-08-31, 12,254 symbol-sessions, 11 names, payoff
`next_open_to_close`, 5 bps per trade. Baseline — long every session, every
name — is **+0.69 bps**.

| claim | rule | n | per-trade | portfolio | gap | both halves |
|---|---|---:|---:|---:|---:|:--:|
| I1 RSI-2 oversold | `rsi_2 < 10` | 1178 | **+29.48** | **+14.34** | 15.14 | fail |
| I2 RSI-2 overbought | `rsi_2 > 90` short | 2018 | −2.85 | −7.67 | 4.82 | fail |
| I3 RSI-14 oversold | `rsi_14 < 30` | 258 | **+37.77** | **−6.91** | **44.68** | fail |
| I4 RSI-14 overbought | `rsi_14 > 70` short | 1158 | −8.41 | −16.41 | 7.99 | fail |
| I5 MACD histogram | `macd_hist > 0` | 6368 | +1.25 | +0.30 | 0.95 | fail |
| I6 Bollinger lower | `bb_pctb < 0` | 573 | +20.19 | +4.07 | 16.12 | fail |
| I7 streak reversal | `streak <= -3` | 1149 | **+29.69** | **+17.42** | 12.28 | fail |
| I8 streak continuation | `streak >= 3` | 1748 | −8.72 | −0.57 | −8.15 | fail |

## One — the number retail reports is not the number a book earns

`net_trade_mean_bps` weights every fill equally. `net_portfolio_mean_bps`
weights every **session** equally, because ten names going oversold on the
same morning is one bet on one market, not ten independent draws.

The gap between them is the whole story of this mission, and on I3 it is
**44.68 bps** — the difference between a headline +37.77 and an actual −6.91.
RSI-14 oversold fired on only 136 portfolio sessions at 1.90 trades apiece,
and the sessions carrying many simultaneous trades are the ones that produced
the return. Strip the concentration and the edge inverts to a loss.

Every claim shows the same sign of error: per-trade flatters portfolio in
seven of eight. The one exception is I8, where clustering made a bad rule look
worse. **This is the correction almost no retail indicator evidence applies**,
and it is large enough on its own to explain why these tools backtest well and
trade badly.

## Two — the thresholds are decoration, the gradient is real

Mean next-session return by decile of each indicator (discovery, gross bps):

- **RSI-2**: 32.8, 17.2, 9.7, 8.4, −0.2, −3.6, −13.2, 3.1, 15.6, −12.9
- **RSI-14**: 18.0, 7.8, 6.3, −5.1, 0.1, 6.9, 7.3, 10.6, 1.9, 3.0
- **%B**: 16.5, 2.6, 2.5, 0.6, 8.2, 2.1, 10.2, 3.9, 6.0, 4.5
- **MACD histogram**: 0.7, 5.2, 3.5, 6.5, 8.7, 5.2, 1.6, 7.7, 1.7, **16.0**

The oversold end declines smoothly from D1 through the middle rather than
stepping at any particular value. Condition 4 of the decision rule is met
mechanically — D1 beats D2 for I1, I3 and I6 — but the profile is a
**gradient, not a level**, which the preregistration explicitly records as
"the threshold is not special." Connors' 10 and Wilder's 30 are not lines the
market respects; they are points on a slope, and any lower number would have
served as well or better. This is the same shape that killed the 0.618
Fibonacci level in `ta-premises`.

MACD runs the other way — monotonically **increasing** to +16.0 bps in the top
decile — so the conventional `> 0` rule dilutes a real gradient across the
entire upper half and lands at +1.25 bps per trade, essentially the baseline.

## Three — the squeeze premise is refuted, and backwards

`bb_bandwidth` encodes "low volatility precedes expansion," which makes no
directional claim, so it was tested on magnitude only. Mean absolute
next-session move:

- lowest bandwidth decile (n=1,225): **67.15 bps**
- all other sessions: **138.20 bps**

A squeeze precedes moves **half the size** of an ordinary session, not larger
ones. This is ordinary volatility clustering — quiet begets quiet — and it is
the exact opposite of the premise as retail states it. Recorded as **REFUTED**,
and it is the cleanest negative in the mission because it needs no cost
assumption and no gate to interpret.

## Four — overbought does not mirror oversold

If these were one symmetric mean-reversion effect, the short side would earn
what the long side earns. It does not. I2 and I4 are negative on both
measures, and I4 is the informative one: shorting RSI-14 > 70 loses 16.41 bps
per portfolio session, which means the tape after a Wilder overbought reading
**continued upward**. Overbought is a momentum state on this universe, not a
reversal state.

I7/I8 tell the same story from the other side: fading a down-run reads
+17.42, riding an up-run reads −0.57. The reversion sits entirely on the
downside.

## Preregistration defect, found on execution

The frozen baseline was written as "unconditionally long the same instrument
**on the same sessions**." For a long-only rule that is the rule itself — the
two series are identical by construction, and the printed comparison came back
exactly equal for I1, I3, I5, I6, I7 and I8. Condition 2 as written is
vacuous.

The substantive comparison — long every session in the split, +0.69 bps — was
computed alongside and is what the table reports. This is a defect in the
preregistration, not a result, and it is recorded here rather than quietly
patched. The same wording is present in `ta-premises`; that mission's verdicts
do not turn on it, because its four premises failed on sign, but the wording
should be fixed before it is reused.

## Limitations

Eleven names, not twelve: **IBIT has no data before 2024** and is absent from
discovery entirely. One parameterisation per claim, daily bars, one decision
per session at the close, costs assumed rather than filled. Discovery spans
2019-04 to 2023-08 and therefore contains the COVID drawdown and rebound,
which is the most likely home of the episodic behaviour the both-halves gate
is objecting to — this was not verified by sub-period attribution, and doing so
would be reading discovery a second time to explain away a rejection.

A refuted indicator does not mean no version of the idea can work; it means
this version, stated the usual way, did not clear the bar we set before
looking.

Backtests are hypothetical, research-only evidence and are not investment
advice.
