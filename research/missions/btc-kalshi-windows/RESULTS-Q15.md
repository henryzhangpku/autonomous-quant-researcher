# Results: BTC direction at Kalshi's 15-minute horizon (Q15 surface)

Mission completed 2026-08-23. *Hypothetical backtests, not investment
advice.* Sibling mission on the hourly surface reports separately
(`*HOURLY*` files in this directory).

Question: is BTC direction forecastable at the 15-minute horizon from
bar-derived price/vol/clock features alone — no reference to Kalshi
quotes? This is the prior question to the taker-alpha missions
(`kalshi-btc-15m/`, `kalshi-btc-hourly/`, `kalshi-maker-side/`), which
asked whether Kalshi's *price* was wrong.

## Verdict

**NOTHING CLEARED DISCOVERY under the frozen evaluator. Validation and
holdout were never read.** No preregistration was filed; the holdout
stays sealed for any future mission on this surface.

Two honest sub-findings:

1. **A statistically real short-horizon mean-reversion signal exists in
   *direction*.** Fading the prior 15-minute window's sign hits 52.1%
   over all 11,128 discovery windows (session-clustered t = 4.70 over
   116 days); conditioning on a large prior move (|ret| >= discovery
   q90) raises the hit rate to 58.2% (clustered t = 5.67); adding a
   3-of-3 same-direction window count reaches ~60%. Direction is not a
   coin flip after a large move.
2. **The signal is economically too small to clear the lab's bar.** The
   best candidate's gross edge is ~5 bps of underlying per trade; at
   the spec's 5 bps cost the best net Sharpe is 0.488 (target 0.75),
   and it fails `positive_both_halves` and `survives_double_cost`. In
   Kalshi-contract terms the naive edge looks large (see cost section)
   — but the `kalshi-btc-15m` mission already tested effectively this
   rule against real KXBTC15M quotes and it **failed its holdout**: the
   market prices most of the reversion, and the residual was regime
   noise. That prior out-of-sample failure is the strongest evidence in
   this report.

## Data and evaluation surface

- `.research/data/btc-kalshi-q15/sessions.jsonl` (host-local staging),
  18,525 rows, sessions 2026-02-08 -> 2026-08-20, `data_sha256
  a38c9d78fea76011c19b89fbf3e20d83e185037e35a35ae2c676053385ef30f5`
  (verified). One row = one 15-minute window; decision at window OPEN
  from features of windows that CLOSED before it; payoff
  `next_open_to_close` = the window's own return, exactly what a
  KXBTC15M up/down contract settles on. Provider: Alpaca BTC/USD 15-min
  bars. Outcomes never read outside the evaluator.
- Frozen machinery: `research.validators.bars_universe` with the spec in
  Appendix A: universe `["BTC-QUARTER_HOUR"]`, splits verbatim from the
  manifest — discovery 2026-02-08 -> 2026-06-03 (116 sessions, 11,132
  windows), validation 2026-06-04 -> 2026-07-12 (39 sessions), holdout
  2026-07-13 -> 2026-08-20 (39 sessions) — `min_symbols 1`,
  `max_concentration 1.0`, payoff `next_open_to_close`, `cost_bps 5.0`,
  `target_sharpe 0.75`.
- Sample floors: **one calendar date is one portfolio observation**
  (~96 windows share a session), so the binding floors are
  `min_sessions` = 60/25/25 (discovery/validation/holdout) — at least
  ~two-thirds of each split's days must be active for the daily Sharpe
  to mean anything — plus `min_trades` = 200/60/60 so a "daily"
  portfolio is not one lucky window per day.
- Discovery base rate: 50.55% of windows close up; mean window return
  -0.04 bps, sd 24.1 bps. A 5 bps toll is ~21% of one standard
  deviation of the thing being predicted — the economics, not the
  statistics, are the constraint at this horizon.

## Multiple-comparisons accounting

**81 hypotheses** were evaluated, on discovery only (full table in
Appendix B): 66 in a fixed grid (momentum/reversal on six return
features x follow/fade; window-count streaks; vol- and
magnitude-conditioned momentum; hour buckets, day-of-week, weekend,
minute-of-hour clock cells; settlement-boundary interactions), then 10
selective-fade refinements, then 5 final combinations. Refinement
rounds were motivated by the round-1 observation that every fade-side
hypothesis had hit rate > 0.5 — they are counted in the denominator.

Chance-expected passes at the frozen bar: at 5 bps cost the drag on a
rule trading k windows/day is ~5·sqrt(k)/24 daily-Sharpe units, so a
zero-gross-edge rule needs a >3-sigma fluke to print net Sharpe 0.75
over ~110 sessions; the expected chance pass count across all 81 tests
is **well under 0.1**. Zero passed — consistent, but note even one pass
would have been strong evidence. At a hypothetical *zero-cost* bar the
same target (t ~ 0.5 over 110 days) would pass by chance roughly a
quarter of the time per test (~20 expected of 81), which is exactly why
the gross-positive fades below are diagnostics, not results.

Clock check (the likeliest artifact axis): no bare seasonal was ever a
contender — every hour-bucket, day-of-week, weekend and minute-of-hour
cell is net-negative in discovery, and `minute_of_hour == 0` (the
window spanning the KXBTCD hourly settlement) shows no distinctive
directional behaviour (hit 0.488-0.512 unconditional; interacting it
with momentum *worsens* the plain fade). Nothing to report, nothing to
promote — the "buy NO on Mondays" trap did not recur.

## The three formal candidates (frozen validator, discovery split)

Three rules with economic rationales were run through the frozen
validator. All three were **rejected on discovery**; under the
mission's procedure that ends the campaign with validation and holdout
unread.

| # | rule (label) | rationale | trades | sessions | gross bps/trade | net portfolio bps/day | net Sharpe | verdict |
|---|---|---|---|---|---|---|---|---|
| 1 | short next window after `streak_up == 3` and prior ret >= q75 (+20.4 bps) | exhaustion: three consecutive up windows capped by a large move over-extends short-horizon positioning | 295 | 86 | +4.93 | +0.49 | **0.488** | rejected: both-halves, double-cost, symbol-fraction gates |
| 2 | symmetric version of #1 (fade 3-of-3 same-direction + >=q75 move, both sides) | same exhaustion mechanism, both tails | 625 | 110 | +3.27 | -0.72 | -0.893 | rejected: net mean negative |
| 3 | fade any prior 15-min move with abs ret >= q90 (35.4 bps) | liquidity-demand overshoot after a large move reverts | 1,114 | 112 | +1.79 | -2.45 | -4.123 | rejected: net mean negative |

Thresholds (q75 = 0.00204104, q90 = 0.00353843) are discovery-only
quantiles of the prior-window absolute return, hard-coded before any
validation/holdout contact. Candidate #1's downside mirror (long after
3-of-3 down + <= -q75) was separately measured at net Sharpe -1.26 —
the "pass-looking" long-side/short-side asymmetry is exactly the kind
of one-sided residue 81 comparisons produce, reinforcing non-promotion.

## Cost sensitivity: the Kalshi-realistic view

The headline evaluator charges 5 bps of underlying per window. A
KXBTC15M contract is a $1 binary settling on direction only, so the
relevant translation is the **hit rate**: naive net per contract =
hit - entry price - round-trip cost. The 15m mission measured KXBTC15M
economics directly: median spread 1 cent, taker fee 0.07·P·(1-P)
(~1.75c at P = 0.5), so a realistic round trip is ~2.25-3c on this
book, within the 3-5c bracket the hourly/maker missions measured on the
ladders; we report 3c and 5c. Discovery-split arithmetic, *assuming
entry at 0.50*:

| rule | disc hit | naive net/contract @3c | @5c |
|---|---|---|---|
| fade prior move >= q90 | 0.582 | +0.052 | +0.032 |
| fade prior move >= q95 | 0.596 | +0.066 | +0.046 |
| #1 exhaustion fade (streak 3 + q75) | 0.597 | +0.067 | +0.047 |

These numbers are **not realizable edge**, for two measured reasons:
(a) entry at 0.50 is fiction — the 15m mission found KXBTC15M prices
the prior-move side at ~0.486 after volatile windows, i.e. the market
already sells you the reversion at a premium; and (b) when that
mission's frozen fade rule (economically the same trade, entered at
real quotes) met its holdout (2026-08), it lost 3.5c/contract and the
underlying conditional itself flipped to continuation (0.510 realised).
The bar-feature hit rates above are in-sample readings of the same
phenomenon that already failed out-of-sample against real prices. No
surviving candidate exists to carry these costs forward.

## Limitations

- Bar closes proxy Kalshi's settlement reference; the exchange's index
  is constructed differently, and last-trade prints embed bid-ask
  bounce that inflates bar-measured reversion (bounce can plausibly
  account for ~1-2 of the ~5 gross bps, though not the bulk of the
  hit-rate shift).
- No order-book state: depth, queue, and fill probability invisible.
- One calendar date is one portfolio observation; 116 discovery
  sessions is the true sample size, not 11,132 windows.
- 2026-02 -> 2026-08 is a single crypto regime; the 15m mission's
  holdout flip shows these conditionals are regime-fragile.
- Validation and holdout of this snapshot remain unread — a future
  mission may spend them exactly once against a preregistered rule.

*Hypothetical backtests, not investment advice.*

## Appendix A: frozen spec

```json
{
  "name": "btc-kalshi-q15-direction",
  "universe": ["BTC-QUARTER_HOUR"],
  "splits": {"discovery": ["2026-02-08", "2026-06-03"],
             "validation": ["2026-06-04", "2026-07-12"],
             "holdout": ["2026-07-13", "2026-08-20"]},
  "min_trades": {"discovery": 200, "validation": 60, "holdout": 60},
  "min_sessions": {"discovery": 60, "validation": 25, "holdout": 25},
  "min_symbols": 1, "max_concentration": 1.0,
  "cost_bps": 5.0, "target_sharpe": 0.75,
  "payoff": "next_open_to_close"
}
```

Scripts host-local in `tmp/btcq15/` (explore.py, explore2.py,
explore3.py, mkspec.py, cand1-3.py); data host-local in
`.research/data/btc-kalshi-q15/`.

## Appendix B: all 81 hypotheses (discovery split only)

Columns: n trades, active sessions, portfolio mean bps/day net of 5 bps
per trade, annualised daily Sharpe (net), hit rate. H/W/D = passes
both-halves / without-best-day / double-cost checks. Round 1 grid (66):

```
name                                   n days    mean    shp    hit  H W D
B streak0 fade                      1172  116  -2.964 -5.994  0.564  0 0 0
B streak3 fade                      1281  116  -3.208  -7.19 0.5379  0 0 0
C hugemove fade                     2783  115  -3.258 -8.707 0.5483  0 0 0
C lovol follow                      3711   97   -4.46 -9.454 0.4697  0 0 0
C hivol fade                        3707   87  -4.705 -9.919 0.5182  0 0 0
B streak-extreme fade               2453  116  -3.626-10.969 0.5503  0 0 0
C hivol follow                      3707   87  -5.295-11.164 0.4815  0 0 0
E min0 fade prior                   2782  116  -3.507-11.268 0.5341  0 0 0
C lovol fade                        3711   97   -5.54-11.743 0.5303  0 0 0
D late short                        1392  116  -4.557-11.875 0.4907  0 0 0
E min45 follow prior                2780  116  -4.253-13.326 0.5036  0 0 0
D EU short                          2784  116   -4.58-13.628 0.4914  0 0 0
D min45 long                        2781  116  -4.225  -14.1 0.5167  0 0 0
D late long                         1392  116  -5.443-14.183 0.5093  0 0 0
B streak0 follow                    1172  116  -7.036 -14.23 0.4352  0 0 0
E min0 fade 4w                      2783  116  -4.019 -14.35 0.5304  0 0 0
D US long                           3244  116  -4.477-14.729 0.5219  0 0 0
D min0 short                        2784  116  -4.068-15.011 0.5119  0 0 0
B streak3 follow                    1281  116  -6.792-15.222 0.4621  0 0 0
D min15 short                       2784  116  -4.832-15.904  0.491  0 0 0
D EU long                           2784  116   -5.42-16.126 0.5086  0 0 0
D min15 long                        2784  116  -5.168 -17.01 0.5086  0 0 0
D min30 long                        2783  116  -4.841 -17.63 0.5088  0 0 0
E min45 fade prior                  2780  116  -5.747-18.007 0.4964  0 0 0
C hugemove follow                   2783  115  -6.742-18.021 0.4517  0 0 0
D US short                          3244  116  -5.523-18.174 0.4769  0 0 0
D min30 short                       2783  116  -5.159-18.791 0.4908  0 0 0
D min45 short                       2781  116  -5.775-19.274 0.4829  0 0 0
B streak-extreme follow             2453  116  -6.374-19.287 0.4492  0 0 0
C bigmove fade                      5566  116  -4.017-20.195 0.5356  0 0 0
E min0 follow prior                 2782  116  -6.493-20.867 0.4659  0 0 0
E min0 follow 4w                    2783  116  -5.981-21.355 0.4693  0 0 0
D min0 long                         2784  116  -5.932-21.893 0.4878  0 0 0
D Asia short                        3712  116  -4.897-22.512 0.5127  0 0 0
D Asia long                         3712  116  -5.103-23.457 0.4873  0 0 0
D dow0 long                         1632   17  -4.056-23.514 0.5098  0 0 0
D dow2 long                         1632   17  -4.903-25.517 0.5092  0 0 0
D dow2 short                        1632   17  -5.097-26.532 0.4908  0 0 0
D dow1 short                        1632   17  -4.496-28.822 0.4926  0 0 0
C bigmove follow                    5566  116  -5.983-30.076 0.4644  0 0 0
A fade mean_prior_ret              11132  116  -4.735-30.615 0.5259  0 0 0
D dow4 short                        1535   16  -4.711-30.679 0.5036  0 0 0
D weekday short                     7964   83  -4.971-31.287 0.4955  0 0 0
D weekday long                      7964   83  -5.029-31.647  0.504  0 0 0
A fade prior_window_ret            11128  116  -4.582-31.683 0.5213  0 0 0
A follow mean_prior_ret            11132  116  -5.265 -34.04 0.4738  0 0 0
A follow ret_24h                   11132  116  -4.995 -34.38 0.4905  0 0 0
D dow4 long                         1535   16  -5.289 -34.44 0.4964  0 0 0
A fade ret_24h                     11132  116  -5.005-34.452 0.5092  0 0 0
D dow0 short                        1632   17  -5.944 -34.46 0.4902  0 0 0
A fade prior_2window_ret           11129  116  -4.648-34.963 0.5272  0 0 0
D dow1 long                         1632   17  -5.504-35.291 0.5074  0 0 0
A follow ret_7d                    11132  116  -4.814-35.377 0.4997  0 0 0
A follow prior_window_ret          11128  116  -5.418-37.459 0.4786  0 0 0
A fade prior_4window_ret           11131  116  -4.846-37.526 0.5238  0 0 0
A fade ret_7d                      11132  116  -5.186-38.107 0.4999  0 0 0
A follow prior_4window_ret         11131  116  -5.154-39.907 0.4759  0 0 0
A follow prior_2window_ret         11129  116  -5.352-40.267 0.4726  0 0 0
D dow3 short                        1533   16   -4.57 -43.44  0.501  0 0 0
D dow6 short                        1632   17  -4.839-44.409 0.4957  0 0 0
D dow6 long                         1632   17  -5.161-47.366 0.5043  0 0 0
D dow3 long                         1533   16   -5.43-51.613 0.4964  0 0 0
D weekend short                     3168   33  -4.926-52.606 0.4908  0 0 0
D weekend long                      3168   33  -5.074-54.178 0.5092  0 0 0
D dow5 long                         1536   16  -4.981-64.441 0.5143  0 0 0
D dow5 short                        1536   16  -5.019-64.946 0.4857  0 0 0
```

Round 2 — selective fades (10; gross and net-of-5bps bps shown, plus
naive binary net/contract at 3c and 5c assuming 0.50 entry):

```
name                                       n days gross_bps   gShp net5_bps   nShp    hit binary_net@3c    @5c
fade prior>=q75                         2783  115     1.742   4.66   -3.258  -8.71 0.5483        0.0183-0.0017
fade prior>=q90                         1114  112     2.551   4.29   -2.449  -4.12 0.5817        0.0517 0.0317
fade prior>=q95                          557   97     1.840   2.09   -3.160  -3.58 0.5961        0.0661 0.0461
fade prior>=q90 & hivol                  797   87     2.402   3.06   -2.598  -3.31 0.5734        0.0434 0.0234
fade prior>=q90 & streak-extreme         279   92     4.927   3.96   -0.073  -0.06 0.6057        0.0757 0.0557
fade streak0 & prior<=-q75               330   96     3.882   4.39   -1.118  -1.26 0.5909        0.0609 0.0409
fade streak3 & prior>=q75                295   86     5.491   5.45    0.491   0.49 0.5966        0.0666 0.0466
fade 2w>=q75x2                          1662  112     1.754   3.39   -3.246  -6.27 0.5602        0.0302 0.0102
fade US hours prior>=q75                1042  109     3.056   6.03   -1.944  -3.84 0.5528        0.0228 0.0028
fade min0 prior>=q75                     738  112     2.325   2.99   -2.675  -3.44 0.5623        0.0323 0.0123
```

Session-clustered hit-rate diagnostics (same rules as grid members):

```
fade prior (all): day-mean hit 0.5213, clustered t vs 0.5 = 4.70 over 116 days
streak0/3 fade: day-mean hit 0.5639, clustered t vs 0.5 = 6.53 over 116 days
fade prior>=q90: day-mean hit 0.6136, clustered t vs 0.5 = 5.67 over 112 days
```

Round 3 — final combinations (5):

```
fade q90 & 4w same sign          938  112   2.321  -2.030  -3.22 0.5938
fade q85 & streak-extreme        407  102   2.906  -0.537  -0.54 0.5823
fade q90 & weekend               169   29   2.319  -2.467  -4.01 0.6154
fade q90 & Asia                  287   87   0.505  -1.694  -1.97 0.5854
fade q90 & not-min0              817  107   0.989  -3.098  -4.35 0.5679
```
