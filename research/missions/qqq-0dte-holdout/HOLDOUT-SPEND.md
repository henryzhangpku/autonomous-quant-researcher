# Holdout spend: qqq-0dte-credit survivor — REJECTED

Spent 2026-08-23, once, on explicit instruction. The QQQ holdout window
(2026-02-06 → 2026-08-10) is now burnt and can never serve as clean evidence
for this candidate again.

## The candidate (frozen, from campaign run 13)

    LABEL = "credit_frac and volume-based decision"

    def signal(symbol, features):
        if features['credit_frac'] > 0.1 and features['day_of_week'] < 2.5:
            return 1
        return 0

Sell the QQQ 0DTE near-money put credit spread on Monday/Tuesday when the
credit is more than 10% of width. Flat otherwise.
Candidate hash `b10baf678d45ab7e3e993cd0eac710aec811ea5b4150166d649b9f7aacbfbb64`.

## Why the spec had to be reconstructed

The campaign's frozen spec was written to a temp path by the launcher and is
not on disk; only `policy_hash` survives in `state/state.json`. Evaluating
under a guessed spec would not have been the preregistered test, so the
reconstruction was validated against the two splits that were already spent
BEFORE the holdout was read:

| | reproduced | recorded |
|---|---:|---:|
| discovery net | 5.69% of width | +5.7% (digest 001) |
| validation net | 4.82% | +4.8% (digest 001) |
| validation Sharpe | 3.33748 | 3.34 (digest 001) |
| discovery Sharpe | 2.942388 | `best_metric` 2.942388 |

The discovery Sharpe matches the campaign's recorded `best_metric` to six
decimals. Dataset sha256 `8910e7ea…` matches the staging manifest, so the
tape is unmodified. Spec at `.research/verifications/qqq-holdout-spend/spec.json`.

## Result

| | discovery | validation | holdout |
|---|---:|---:|---:|
| net per trade | +5.69% | +4.82% | **+0.52%** |
| gross per trade | — | — | +1.02% |
| net Sharpe | 2.942 | 3.337 | **0.263** |
| trades | 221 | 43 | 72 |
| status | passed | passed | **rejected** |

Eight of nine gates pass on holdout: minimum_trades, minimum_sessions,
symbol_breadth, symbol_concentration, positive_net_mean,
positive_symbol_fraction, survives_without_best_day, survives_double_cost.

The one failure is `positive_both_halves` — one half of the holdout window is
net negative.

## Verdict

**Not promotable.** The rule is not refuted outright: it stayed net-positive,
survived doubled costs and best-day removal, and cleared every sample floor.
But expectancy fell roughly 90% from what discovery and validation showed, the
Sharpe fell by an order of magnitude, and it is not stable across the holdout
window. A rule that reads +5.7% / +4.8% in-sample and +0.52% out-of-sample was
mostly measuring the window it was found in.

This is the second consecutive out-of-sample failure for a digest-001 headline,
after `side-asymmetry-replication-v1` (refuted). Both were sealed before the
data was read, and both are published rather than buried.

## What this cannot say

One candidate, one market, one holdout window of 72 trades. Costs are a flat
50 bps of width assumption, not measured fills. Backtests are hypothetical,
research-only evidence and are not investment advice.
