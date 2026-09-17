# bars-rules campaign 1 — findings (2026-08-13)

Campaign: `bars-rules-v1`, evaluator `bars-universe-portfolio-v1`, snapshot
`alpaca` — 1,240 sessions × 8 symbols = 9,920 causal rows, 2021-08-31 →
2026-08-10. Preregistered grid, no LLM proposer.
Sweep: `research/experiments/sweep_bars_rules.py`.
Ledger: `.research/sweeps/bars-rules-v1/`.

Universe: SPY, QQQ, IWM, DIA, XLF, XLE, XLK, SMH.
Splits (frozen at prepare time): discovery 2021-08-31 → 2024-08-15,
validation 2024-08-16 → 2025-08-13, holdout 2025-08-14 → 2026-08-10.

## Verdict

**232 cells, 4 discovery gate-passers, 0 promotions.** The preregistered
top-3 shortlist went to validation and all three failed every performance
gate. The holdout was never opened. Authority `research_only`; nothing here
authorizes an entry policy.

## What the grid actually mapped

**1. Every discovery passer was the same feature, in both directions.** All
four were `ret_20d`: long after a 20-day decline (< −5%) AND long after a
20-day advance (> +5%). A condition whose opposite passes equally carries no
information — what passed was the long side, not the condition. Discovery
spans 2021-08 → 2024-08, where holding these eight ETFs paid regardless of
entry rule. The gates caught sample-size and stability problems but cannot
detect that a whole discovery window has one direction in it.

**2. Validation removed the drift, and the effect went with it.**

| Cell (all long) | Disc score | Val bps/trade | Val bps/session | Val Sharpe |
|---|---:|---:|---:|---:|
| `ret_20d < −0.05`, close→close | 0.889 | **+18.87** | **−12.22** | −0.86 |
| `ret_20d > +0.05`, close→close | 0.854 | −4.04 | −11.17 | −1.41 |
| `ret_20d > +0.05`, open→close | 0.824 | −11.53 | −14.51 | −2.60 |

Each failed `positive_net_mean`, `positive_both_halves`,
`survives_without_best_day`, and `survives_double_cost`.

**3. The leader's positive per-trade number is a clustering artifact, and
naming it is the most useful thing this campaign produced.** `ret_20d < −5%`
posted **+18.87 bps per trade** while losing **−12.22 bps per session**. A
20-day drawdown is a market-wide event: the condition fires on all eight
symbols on the same day, so a handful of macro bounce days are counted eight
times in the trade-weighted mean, while sessions where only one or two
symbols qualified lost money. Equal-weight daily aggregation refuses to treat
one macro event as eight independent observations, which is precisely why the
portfolio mean and not the trade mean drives the gates.

Any future rule built on a broadly-correlated condition over a correlated
universe will show this divergence. Read `net_portfolio_mean_bps` first; a
large gap between it and `net_trade_mean_bps` is a correlated-firing warning,
not an edge.

**4. Nothing else came close.** The remaining 228 cells — single-session
returns, gaps, close-position-in-range, volume ratios, realized vol, and
distance from 20-day extremes, at both comparison directions and both payoff
modes — failed discovery outright. Short cells were uniformly worse than
their long counterparts, consistent with (1).

## Limitations

- One discovery regime (2021-08 → 2024-08) with a strong long drift; a grid
  cannot correct for a window that has a direction in it. A discovery split
  spanning a bear year would test rules very differently.
- Single-condition rules only, by design. Compound conditions were excluded
  to keep the grid preregisterable; they are also where overfitting lives.
- Daily decision at the close, next-session payoff. Intraday timing — where
  the overnight-spx campaign found its edge — is outside this family.
- 5 bps per trade assumed, not measured. Unlike overnight-spx, no live
  execution capture backs this number for these ETFs.

## Follow-ups (each a new frozen contract, not a tweak)

1. **Market-relative payoff.** Replace the raw next-session return with a
   return net of SPY's, so a long-drift window cannot pass a rule by itself.
   This directly attacks the failure mode above and is the highest-value
   change to this family.
2. **A discovery split containing a bear year.** Extend the snapshot back to
   2018 so discovery includes 2018-Q4 and 2022; the provider boundary, not
   the design, currently sets the start.
3. **Correlated-firing diagnostic in the evaluator.** Report the ratio of
   trade-weighted to session-weighted mean directly, so cells that only look
   good because their condition fires universe-wide are visible at discovery
   rather than at post-mortem.

Backtests are hypothetical, research-only evidence and are not investment
advice.

---

# bars-rules campaign 2 — findings (2026-08-13)

Campaign: `bars-rules-v2`. Identical to campaign 1 in universe, grid,
thresholds, costs, gates, splits and promotion rule. **One variable changed:**
the payoff is benchmark-relative — each symbol's forward return minus SPY's
return over the same forward window. Same interval, differenced, so no
lookahead is introduced; drift simply stops paying.
Sweep: `research/experiments/sweep_bars_rules_v2.py`.
Ledger: `.research/sweeps/bars-rules-v2/`.

## Verdict

**232 cells, 2 discovery gate-passers, 0 promotions.** Holdout untouched.
Authority `research_only`.

## The fix did what it was built to do

Campaign 1's four passers were all `ret_20d`, both directions, both long —
beta wearing a condition. Under a market-relative payoff **all four are
gone**, and both survivors are SHORT. Nothing passed by being invested.

## The near-miss, stated precisely

`ret_5d < -0.05` → short, close-to-close relative:

| | discovery | validation |
|---|---:|---:|
| net Sharpe | 1.116 | **2.064** |
| net bps/session | +9.66 | **+15.73** |
| trades / sessions | 334 / 135 | 83 / 38 |
| positive_symbol_fraction | 0.500 (pass) | **0.375 (FAIL, needs 0.40)** |

It improved out of sample on every performance measure and failed one gate,
breadth, by a single symbol: with eight names 0.375 is three profitable where
the gate wants four. **The gate was not moved and must not be.** A threshold
adjusted after seeing which side of it a result landed on is not a threshold.

The correlated-firing diagnostic added this campaign reads the opposite way
from campaign 1, and that is the most encouraging fact here.
`trade_minus_session_bps` was **+31** for campaign 1's leader (one macro event
counted once per symbol, inflating the trade mean). For this cell it is
**-10.5 in validation and -16.8 in discovery**: sessions with FEWER trades did
better. The effect concentrates when one or two symbols have fallen 5%
relative to the market, not when everything has. That is the signature of
idiosyncratic relative weakness rather than a market event, which is what a
genuine cross-sectional effect should look like.

Concentration is real but within the frozen limit: SMH is 93/334 discovery
and 28/83 validation trades (0.337, limit 0.35). The effect leans on
semiconductors and is one symbol away from being a semis finding.

Second passer, `ret_1d > +0.01` → short, open-to-close relative: validation
Sharpe 0.72, +3.90 bps/session, failed `positive_both_halves`,
`positive_symbol_fraction` and `survives_double_cost`. Weak; recorded, not
pursued.

## Limitations

- Eight symbols makes `positive_symbol_fraction` a coarse instrument: each
  symbol is 12.5 points of it, so a breadth gate at 0.40 can only be met in
  steps. This cuts both ways and is a universe-size problem, not a gate
  problem.
- Costs are assumed 5 bps, not measured. Unlike overnight-spx, nothing here
  is backed by an execution capture.
- Validation is a single 12-month window (2024-08 → 2025-08).

## Follow-ups (each a new frozen contract, not a tweak)

1. **Widen the universe to 25-40 liquid names before re-testing this rule.**
   Breadth is the binding constraint and eight symbols cannot measure it. A
   larger universe makes `positive_symbol_fraction` meaningful and would let
   this hypothesis pass or fail on its merits rather than on granularity.
   This is the single highest-value next campaign for this family.
2. **Preregister the semis question directly.** SMH carries a third of the
   trades; a single-symbol contract on SMH with its own frozen gates would
   say whether this is a market-wide cross-sectional effect or a semis one.
3. **Sector-relative rather than SPY-relative payoff.** Differencing against
   the symbol's own sector ETF instead of SPY separates sector rotation from
   idiosyncratic weakness — the two explanations still confounded here.

Backtests are hypothetical, research-only evidence and are not investment
advice.

---

# bars-rules campaign 3 — findings (2026-08-13)

Campaign: `bars-rules-v3`. Same grid, same thresholds, same costs, same
market-relative payoffs as campaign 2, on a **30-symbol** universe (eleven
SPDR sectors, five broad index funds, fourteen industry/thematic funds),
1,240 sessions x 30 symbols = 37,200 causal rows. Two gates tightened before
running because 30 names make the old settings meaningless:
`max_concentration` 0.35 -> 0.20, `min_symbols` 6 -> 10, `min_trades`
120/50 -> 200/80. `positive_symbol_fraction` deliberately unchanged at 0.40
so the measure that decided campaign 2 stays comparable.
Sweep: `research/experiments/sweep_bars_rules_v3.py`.
Ledger: `.research/sweeps/bars-rules-v3/`.

## Verdict

**232 cells, 2 discovery gate-passers, 0 promotions.** Holdout untouched.

## The prior hypothesis is refuted, and breadth was never the constraint

`ret_5d < -0.05` short, close-to-close relative, was named in the sweep
script BEFORE this campaign ran, precisely so its result would be evidence
rather than a second reading of the same data. The answer is unambiguous:

| | 8 symbols (campaign 2) | 30 symbols (campaign 3) |
|---|---:|---:|
| discovery Sharpe | **+1.116** | **-0.095** |
| discovery status | passed | rejected (5 gates) |
| validation Sharpe | 2.064 | 0.344 |
| validation bps/session | +15.73 | +2.73 |
| validation symbol fraction | 0.375 (FAIL) | **0.567 (PASS)** |
| validation concentration | 0.337 | 0.094 |

The gate that killed it in campaign 2 is comfortably passed here, and the
tightened concentration limit never binds (0.094 against 0.20). It fails on
performance instead — negative Sharpe at discovery, meaning the effect is not
merely weaker on 30 names, it is absent.

Campaign 2's universe was eight funds weighted heavily toward semiconductors
and large-cap tech (SMH, XLK, QQQ, SPY), and SMH alone carried a third of the
trades. The "cross-sectional relative weakness" effect was that concentration
speaking. Widening the universe was the correct test and it returned a clean
negative. **No follow-up on this hypothesis is warranted.**

## What the grid mapped instead

Both discovery passers are the same shape: fade a one-day advance, measured
against the market.

| Cell | Val Sharpe | Val bps/session | Failed |
|---|---:|---:|---|
| `ret_1d > +0.02` -> short, open->close rel | 0.496 | +3.12 | both_halves, double_cost |
| `ret_1d > +0.01` -> short, open->close rel | 0.684 | +3.59 | symbol_fraction, double_cost |

Both are positive out of sample and both die at doubled cost. A short-horizon
relative reversal exists at roughly +3 to +4 bps per session against a 5 bps
assumption — real enough to appear twice at different thresholds, thin enough
that execution quality decides whether it exists at all. As with overnight-spx
campaign 1, the binding constraint is cost, not signal. Unlike overnight-spx,
no execution capture backs the 5 bps number for these ETFs, so the honest
statement is that the effect is inside the noise floor of an unmeasured cost.

## Standing verdict for this family

Three campaigns, 696 preregistered cells, **zero promotions**:

1. Raw payoffs promoted nothing and exposed a drift artifact.
2. Market-relative payoffs removed the artifact and produced one near-miss.
3. A universe wide enough to measure it refuted that near-miss outright.

Single-condition daily-bar rules over liquid US ETFs, priced at an assumed
5 bps, do not clear this gate set. That is a real finding about the family,
not a failure of the campaigns, and it is the correct point at which to stop
adding cells to this grid.

## Follow-ups, in the order they are worth doing

1. **Measure the cost before running anything else here.** Two campaigns have
   now died at `survives_double_cost` against a number nobody measured. The
   overnight-spx program only became a promotion after a live capture replaced
   a modeled 5% with a measured 0.6-1.0%. The same discipline applies: capture
   real spreads for these ETFs at the close, then re-run campaign 3 unchanged.
   Until then every result here is a statement about an assumption.
2. **Change the decision surface, not the grid.** Single-condition rules over
   daily bars are exhausted. Intraday timing is where this repo's only
   promotion came from.
3. **Do not widen the grid further.** More thresholds on the same features
   would buy multiplicity, not information.

Backtests are hypothetical, research-only evidence and are not investment
advice.
