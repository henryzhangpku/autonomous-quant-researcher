# Results: is BTC direction forecastable at Kalshi's hourly horizon?

Mission completed 2026-08-23. *Hypothetical backtests, not investment advice.*

## Verdict up front

**Nothing cleared discovery. Validation and holdout were never read and stay
sealed.** A coherent short-horizon *reversal* family exists in-sample —
fading the recent move calls the next hour's direction right 52–54% of the
time in discovery — but its gross edge is 1.6–4.1 cents on a $1 contract,
at or below the 3–5 cent Kalshi round-trip toll, and in return space its
per-trade net is negative even at the 5 bps headline cost. Under the frozen
evaluator every candidate was rejected on discovery, so per the
preregistered design no preregistration was filed and no out-of-sample data
was spent.

This is still the most structure any Kalshi-relevant mission has found:
prior missions (kalshi-btc-hourly 141 hypotheses, kalshi-btc-15m 88,
kalshi-maker-side) asked whether Kalshi's *price* was wrong and found
nothing tradable. This one asked whether the *direction* is forecastable at
all, and the answer is: weakly yes, tradably no.

## Data

`.research/data/btc-kalshi-hourly/sessions.jsonl` (host-local by the
two-GPU discipline; only findings travel), 9,049 rows, sessions 2025-08-08
→ 2026-08-20, `data_sha256
47ccb4a381bcb2855f3a602e3baceb632e582ef2bd58105a0a2d5569941f0f64`, verified
before every evaluator run. One row = one clock-hour window on BTC/USD
(Alpaca 1-hour bars); decision at the window open, features strictly from
windows that closed before it, payoff `next_open_to_close` — the window's
own return, which is what a Kalshi hourly up/down contract settles on.

Splits (manifest `suggested_splits`, verbatim):

| split | window | rows | sessions |
|---|---|---:|---:|
| discovery | 2025-08-08 → 2026-03-21 | 5,424 | 226 |
| validation | 2026-03-22 → 2026-06-05 | 1,824 | 76 (unread) |
| holdout | 2026-06-06 → 2026-08-20 | 1,801 | 76 (unread, sealed) |

Discovery base rate of an up window: 49.76%. Mean |hourly return|: 32 bps.
Mean hourly return: −0.9 bps (mildly down regime).

## Evaluation surface

Frozen spec `spec-hourly.json` (committed alongside): universe
`["BTC-HOURLY"]`, payoff `next_open_to_close`, `cost_bps` 5.0,
`target_sharpe` 0.75, `min_symbols` 1, `max_concentration` 1.0.

Sample floors: one calendar date is one portfolio observation and ~24
windows share a session, so `min_trades` alone would be met by a handful of
days. Floors were set as `min_sessions` 100/35/35 and `min_trades`
300/100/100 (discovery/validation/holdout): ≥35 independent daily
observations is the minimum for the annualized-Sharpe gate to mean
anything, and ≥100 trades caps the binomial s.e. of directional accuracy
at 5 points. A candidate firing ≥10% of windows clears both with margin.

Evaluator: `research/validators/bars_universe.py`, unmodified, run with
`--data-hash` pinned. Exploration scripts host-local in `tmp/btcwin/`
(explore.py, explore2.py, explore3.py).

## Hypothesis space: 77 formulated, 69 fired, 0 cleared

77 hypotheses were formulated before any promotion decision (full table in
the appendix): 2 controls, 12 unconditional momentum/reversal, 12 streak,
6 big-move follow/fade, 12 vol-conditioned, 5 multi-horizon agreement,
12 pure clock/seasonal, 8 clock-conditioned momentum, 8 trend-threshold.
Eight streak-down variants fired zero trades — the staged `streak_up`
feature is capped to 0..3 and never goes negative, so "streak down"
is not expressible in this feature set. Denominator kept at 77.

**Return space (the frozen machinery's yardstick), discovery, 5 bps:**
per-trade net mean was negative for all 69 firing hypotheses. Exactly 2
had a positive *daily-portfolio* mean (equal-weight days):

| rule | fires | trades | port bps/day | Sharpe | per-trade net bps | validator |
|---|---:|---:|---:|---:|---:|---|
| short after streak_up ≥ 3 | 11.1% | 600 | +5.69 | 2.97 | −2.83 | **rejected** |
| fade ret_24h when \|ret_24h\| > 2% | 31.8% | 1,727 | +1.76 | 1.28 | −3.74 | **rejected** |

Both were run through the frozen evaluator on discovery
(`candidate_streak_fade.py`, `candidate_daily_fade.py`, committed) and both
were **rejected** on the single-symbol `positive_symbol_fraction` gate,
which requires positive per-trade net. The rejection is correct, not a
technicality: the positive portfolio mean comes from equal-weighting days,
which up-weights the days these rules barely fired. Weighted the way a
Kalshi book would actually be traded — per window — both lose money at 5
bps. The `trade_minus_session_bps` diagnostics (−8.5 and −5.5) flag exactly
this artifact. Since the discovery bar was "positive net on discovery under
the frozen machinery," **zero candidates cleared, no preregistration was
filed, and validation/holdout remain unread.**

Chance accounting, return space: with a 5 bps toll against a ~2 bps gross
per-trade ceiling, the chance-expected validator pass count is well below 1;
observing 0 of 77 is fully consistent with no tradable return-space edge.

## The real finding: a coherent reversal family, below the toll

Kalshi contracts pay on the *sign*, not the size, so directional accuracy
was tabulated for all 77 (discovery only). The top of the table is not a
random scatter — it is one family, every variant of "fade the recent move":

| rule | n | acc | gross edge (cents) | binomial t |
|---|---:|---:|---:|---:|
| fade prior hour when vol_prior > 0.5% | 1,573 | 53.8% | +3.85 | 3.05 |
| fade prior hour when vol_prior > 0.3% | 3,722 | 52.3% | +2.34 | 2.85 |
| fade ret_24h when \|ret_24h\| > 1% | 3,162 | 52.5% | +2.52 | 2.83 |
| fade ret_24h (uncond) | 5,424 | 51.9% | +1.87 | 2.75 |
| fade ret_24h when \|ret_24h\| > 2% | 1,727 | 53.2% | +3.20 | 2.66 |
| fade prior hour (uncond) | 5,424 | 51.6% | +1.57 | 2.31 |
| short after streak_up ≥ 3 | 600 | 54.3% | +4.09 | 2.01 |

11 of 69 firing hypotheses had accuracy t ≥ 2 versus ~1.6 expected under
independence — but all 11 are correlated variants of the same reversal
condition, so this is one family-level observation, not eleven discoveries.
Every momentum variant (follow the move) sits *below* 50%, the mirror
image, which is what a real reversal effect looks like and what multiple
independent noise hits would not. Direction-stability held across
discovery's internal halves (split 2025-11-29): fade-prior-hour 51.1%/52.0%,
vol-conditioned 55.2%/53.0%, daily-fade 53.0%/53.3%, streak-fade
50.9%/58.2% (the streak rule's edge lived almost entirely in the second
half — the least stable of the four).

**Why it still isn't a trade.** Buying the signalled side at a price equal
to the base rate (~50c), the gross edge is the accuracy excess in cents:
1.6–4.1c in-sample. The measured toll on the hourly ladders
(kalshi-btc-hourly, kalshi-maker-side missions) is a 3–5 cent median
spread plus the `0.07·P·(1−P)` taker fee ≈ 1.75c at P≈0.5: call the
round-trip **3–5 cents** (half-spread 1.5–2.5c + fee, settlement free).
The single best in-sample cell — the maximum over 77 tries, before any
out-of-sample shrinkage — nets **−1.2c to +0.9c** at that toll. The rest of
the family nets negative across the whole band. The maker side cannot
rescue it: the maker-side mission measured adverse selection of 2–5c on
these ladders, larger than the maker's spread savings. So even taking the
in-sample numbers at face value, the reversal edge is inside the toll.

Cost sensitivity, return space (per-trade gross vs cost): streak-fade
gross +2.17 bps/trade and daily-fade +1.26 bps/trade are below even a 3 bps
cost; at 0 bps they are positive but sub-Sharpe. There is no cost
assumption under which the frozen gates pass on discovery.

## Seasonals

The clock features produced nothing even tempting: all 12 pure clock cells
(hour-of-day blocks, weekend/weekday, both directions) were negative net on
discovery; the best, "short 21–23 UTC," had Sharpe −1.99. Nothing to
report-but-not-promote this time — the 15m mission's "buy NO on Mondays"
has no hourly cousin.

## What a quoting policy can take from this

The mission's purpose was to learn what to *condition on*. Findings a
future maker/quoting mission can use as priors (in-sample, unvalidated):

1. Hourly BTC direction leans against the recent move; continuation is the
   wrong side. A quote skew (not a taker trade) that leans the book against
   the prior hour's direction, hardest when trailing vol is high, is the
   shape of the signal.
2. The effect size is small (2–4 points of win probability). It can pay a
   0-fee maker a fraction of a cent, never a 3–5c taker toll.
3. `streak_up`'s cap (0..3, never negative) means down-streaks are
   invisible to this feature set; a future staging should carry a signed
   streak.

Because validation and holdout were never read, the reversal family is
still testable out-of-sample by a future mission with a sign-payoff
evaluator — that machinery does not exist in the lab today and would need
to be built and frozen first. Do not spend those splits on the return-space
evaluator; this mission has shown its per-trade verdict is already negative
at any plausible cost.

## Honest limitations

- Alpaca 1-hour bar closes proxy the settlement reference. Kalshi's hourly
  contracts settle on a CF-Benchmarks-style reference that is constructed
  differently (multi-exchange, volume-weighted, specific observation
  window); disagreement in the final seconds flips a coin-flip window.
- No order-book state: fill probability, queue position and depth are
  invisible; all cents arithmetic assumes execution at the stated toll.
- One calendar date is one portfolio observation; ~24 windows share a
  session, and intraday correlation makes trade-level t-statistics
  overstated. The binomial t's above do not correct for this.
- Discovery-only claims are in-sample by construction. The 52–54% accuracy
  numbers are the selected maximum of a 77-hypothesis search and will
  shrink out-of-sample.
- The discovery regime (Aug 2025 – Mar 2026) was mildly down (−0.9
  bps/hour drift); a reversal fade partially proxies a short in that
  regime.

## Appendix: full hypothesis table (discovery, 5 bps, return space)

Columns: trades, sessions, fire rate, per-trade net bps, portfolio bps/day,
annualized Sharpe, directional accuracy. Controls included in the 77.

| # | hypothesis | n | days | fire | net/tr | port | Sharpe | acc |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | C1 always long | 5424 | 226 | 100% | −5.86 | −5.86 | −9.22 | 49.8% |
| 2 | C2 always short | 5424 | 226 | 100% | −4.14 | −4.14 | −6.51 | 50.2% |
| 3 | follow prior_window_ret | 5424 | 226 | 100% | −5.03 | −5.03 | −9.05 | 48.4% |
| 4 | fade prior_window_ret | 5424 | 226 | 100% | −4.97 | −4.97 | −8.94 | 51.6% |
| 5 | follow prior_2window_ret | 5424 | 226 | 100% | −4.76 | −4.76 | −7.88 | 48.4% |
| 6 | fade prior_2window_ret | 5424 | 226 | 100% | −5.24 | −5.24 | −8.67 | 51.6% |
| 7 | follow prior_4window_ret | 5424 | 226 | 100% | −4.34 | −4.34 | −6.79 | 49.3% |
| 8 | fade prior_4window_ret | 5424 | 226 | 100% | −5.66 | −5.66 | −8.86 | 50.7% |
| 9 | follow mean_prior_ret | 5424 | 226 | 100% | −4.27 | −4.27 | −6.87 | 48.7% |
| 10 | fade mean_prior_ret | 5424 | 226 | 100% | −5.73 | −5.73 | −9.22 | 51.3% |
| 11 | follow ret_24h | 5424 | 226 | 100% | −4.71 | −4.71 | −7.64 | 48.1% |
| 12 | fade ret_24h | 5424 | 226 | 100% | −5.29 | −5.29 | −8.59 | 51.9% |
| 13 | follow ret_7d | 5424 | 226 | 100% | −4.82 | −4.82 | −7.55 | 49.2% |
| 14 | fade ret_7d | 5424 | 226 | 100% | −5.18 | −5.18 | −8.12 | 50.8% |
| 15 | long after streak_up≥2 | 2716 | 226 | 50% | −5.40 | −7.11 | −7.62 | 47.9% |
| 16 | short after streak_up≥2 | 2716 | 226 | 50% | −4.60 | −2.89 | −3.10 | 52.1% |
| 17 | long after streak_up≥3 | 600 | 195 | 11% | −7.17 | −15.69 | −8.20 | 45.7% |
| 18 | short after streak_up≥3 | 600 | 195 | 11% | −2.83 | +5.69 | +2.97 | 54.3% |
| 19–26 | streak_up≥4 & streak_down≥2,3,4 (both dirs) | 0 | — | 0% | — | — | — | — |
| 27 | follow big prior \|ret\|>0.3% | 1988 | 218 | 37% | −4.70 | −4.26 | −3.77 | 47.5% |
| 28 | fade big prior \|ret\|>0.3% | 1988 | 218 | 37% | −5.30 | −5.74 | −5.07 | 52.5% |
| 29 | follow big prior \|ret\|>0.5% | 1028 | 201 | 19% | −4.97 | −3.29 | −1.44 | 47.4% |
| 30 | fade big prior \|ret\|>0.5% | 1028 | 201 | 19% | −5.03 | −6.71 | −2.93 | 52.6% |
| 31 | follow big prior \|ret\|>0.8% | 440 | 156 | 8% | −6.23 | −1.87 | −0.58 | 47.5% |
| 32 | fade big prior \|ret\|>0.8% | 440 | 156 | 8% | −3.77 | −8.13 | −2.51 | 52.5% |
| 33 | momentum, vol>0.2% | 4779 | 220 | 88% | −5.03 | −4.32 | −5.95 | 48.4% |
| 34 | reversal, vol>0.2% | 4779 | 220 | 88% | −4.97 | −5.68 | −7.80 | 51.6% |
| 35 | momentum, vol<0.2% | 645 | 66 | 12% | −5.07 | −1.61 | −1.06 | 48.8% |
| 36 | reversal, vol<0.2% | 645 | 66 | 12% | −4.93 | −8.39 | −5.53 | 51.2% |
| 37 | momentum, vol>0.3% | 3722 | 200 | 69% | −5.42 | −3.78 | −2.06 | 47.7% |
| 38 | reversal, vol>0.3% | 3722 | 200 | 69% | −4.58 | −6.22 | −3.39 | 52.3% |
| 39 | momentum, vol<0.3% | 1702 | 130 | 31% | −4.19 | −2.91 | −1.84 | 50.1% |
| 40 | reversal, vol<0.3% | 1702 | 130 | 31% | −5.81 | −7.09 | −4.48 | 49.9% |
| 41 | momentum, vol>0.5% | 1573 | 111 | 29% | −6.00 | −6.71 | −8.76 | 46.2% |
| 42 | reversal, vol>0.5% | 1573 | 111 | 29% | −4.00 | −3.29 | −4.29 | 53.8% |
| 43 | momentum, vol<0.5% | 3851 | 206 | 71% | −4.64 | −5.37 | −2.68 | 49.4% |
| 44 | reversal, vol<0.5% | 3851 | 206 | 71% | −5.36 | −4.63 | −2.31 | 50.6% |
| 45 | 1h & 24h agree, follow | 2944 | 226 | 54% | −4.76 | −5.28 | −6.93 | 46.8% |
| 46 | 1h vs 24h disagree, follow 24h | 2480 | 226 | 46% | −4.64 | −4.00 | −4.03 | 49.7% |
| 47 | 1h vs 24h disagree, follow 1h | 2480 | 226 | 46% | −5.36 | −6.00 | −6.05 | 50.3% |
| 48 | 24h & 7d agree, follow | 3230 | 216 | 60% | −4.60 | −7.62 | −8.21 | 47.8% |
| 49 | 24h & 7d agree, fade | 3230 | 216 | 60% | −5.40 | −2.38 | −2.57 | 52.2% |
| 50–57 | long/short by hour block (0–7, 8–13, 14–20, 21–23) | ≤1808 | 226 | ≤33% | ≤−3.20 | ≤−3.20 | ≤−1.99 | ≤50.8% |
| 58–61 | long/short weekend, weekday | ≤3864 | — | — | ≤−3.89 | ≤−3.89 | ≤−5.60 | ≤50.4% |
| 62 | momentum US hours 14–20 | 1582 | 226 | 29% | −4.96 | −4.96 | −4.03 | 48.6% |
| 63 | reversal US hours 14–20 | 1582 | 226 | 29% | −5.04 | −5.04 | −4.09 | 51.4% |
| 64 | momentum Asia 0–7 | 1808 | 226 | 33% | −3.01 | −3.01 | −3.38 | 49.7% |
| 65 | reversal Asia 0–7 | 1808 | 226 | 33% | −6.99 | −6.99 | −7.84 | 50.3% |
| 66 | weekend momentum | 1560 | 65 | 29% | −5.67 | −5.67 | −12.73 | 47.5% |
| 67 | weekend reversal | 1560 | 65 | 29% | −4.33 | −4.33 | −9.73 | 52.5% |
| 68 | weekday momentum | 3864 | 161 | 71% | −4.78 | −4.78 | −8.02 | 48.8% |
| 69 | weekday reversal | 3864 | 161 | 71% | −5.22 | −5.22 | −8.78 | 51.2% |
| 70 | follow ret_24h, \|ret_24h\|>1% | 3162 | 217 | 58% | −5.38 | −7.41 | −7.24 | 47.5% |
| 71 | fade ret_24h, \|ret_24h\|>1% | 3162 | 217 | 58% | −4.62 | −2.59 | −2.53 | 52.5% |
| 72 | follow ret_24h, \|ret_24h\|>2% | 1727 | 175 | 32% | −6.26 | −11.76 | −8.54 | 46.8% |
| 73 | fade ret_24h, \|ret_24h\|>2% | 1727 | 175 | 32% | −3.74 | +1.76 | +1.28 | 53.2% |
| 74 | follow ret_7d, \|ret_7d\|>3% | 3065 | 184 | 57% | −4.79 | −9.22 | −5.80 | 49.4% |
| 75 | fade ret_7d, \|ret_7d\|>3% | 3065 | 184 | 57% | −5.21 | −0.78 | −0.49 | 50.6% |
| 76 | follow ret_7d, \|ret_7d\|>5% | 1992 | 125 | 37% | −4.32 | −7.42 | −6.37 | 49.3% |
| 77 | fade ret_7d, \|ret_7d\|>5% | 1992 | 125 | 37% | −5.68 | −2.58 | −2.21 | 50.7% |

Grouped rows (19–26, 50–61) are individually enumerated in
`tmp/btcwin/explore.py` output; every one was negative net with \|Sharpe\|
direction as summarized, and none approached the discovery bar.

*Hypothetical backtests, not investment advice.*
