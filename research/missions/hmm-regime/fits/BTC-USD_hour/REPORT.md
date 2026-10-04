# HMM fit report: BTC/USD hour

Generated 2026-10-04T01:28+00:00. Candles 2021-01-01 to 2026-10-04 (50,199); feature rows 49,600; fit = first 25,527 rows (to 2024-01-01), validation 4,343 rows (to 2024-07-01), forward 19,730 rows. Features: ret, rvol, range, vol_ratio, trend (standardized on the past only).

## State count selection (BIC on fit, log-likelihood per observation on validation; simpler wins within 2%)

| k | ll | bic | oos_ll_per_obs |
|---|---|---|---|
| 2 | -1.956e+05 | 3.917e+05 | -22.75 |
| 3 | -1.513e+05 | 3.034e+05 | -9.871 |
| 4 | -1.41e+05 | 2.83e+05 | -8.496 |
| 5 | -1.332e+05 | 2.676e+05 | -7.608 |

**Chosen: 5 states**, labelled by their statistics on the fit window: CHOP, CHOP2, CALM_UP, STRESS, CRASH.

## Transition matrix (row = from, col = to)

| from \ to | CHOP | CHOP2 | CALM_UP | STRESS | CRASH |
|---|---|---|---|---|---|
| CHOP | 0.9279 | 0.0012 | 0 | 0.0709 | 0 |
| CHOP2 | 0.0296 | 0.9154 | 0 | 0.0549 | 0.0001 |
| CALM_UP | 0.0036 | 0.0294 | 0.891 | 0.076 | 0 |
| STRESS | 0.1039 | 0.2011 | 0.2054 | 0.4892 | 0.0003 |
| CRASH | 0 | 0.4994 | 0 | 0.5006 | 0 |

## Expected duration (candles) = 1 / (1 - self-transition)

| state | expected_duration | share_fit |
|---|---|---|
| CHOP | 13.9 | 0.3069 |
| CHOP2 | 11.8 | 0.3534 |
| CALM_UP | 9.2 | 0.2237 |
| STRESS | 2 | 0.1159 |
| CRASH | 1 | 7.835e-05 |

## Per-state statistics by segment, on the forward-filtered state (what a decision would have seen)

`next_ret_bps` = mean return of the candle AFTER the state was read: the only number that can be traded.

| segment | state | share | mean_ret_bps | vol_bps | next_ret_bps | n |
|---|---|---|---|---|---|---|
| fit | CHOP | 0.3069 | 0.2842 | 37.3 | -0.4387 | 7834 |
| fit | CHOP2 | 0.3534 | 0.3106 | 46.37 | 0.1165 | 9021 |
| fit | CALM_UP | 0.2237 | 2.936 | 55.53 | 0.5441 | 5711 |
| fit | STRESS | 0.1159 | -6.2 | 159 | 0.6707 | 2959 |
| fit | CRASH | 7.835e-05 | -277.6 | 149.1 | 78.71 | 2 |
| validation | CHOP | 0.2929 | 0.8938 | 27.8 | 0.7795 | 1272 |
| validation | CHOP2 | 0.335 | 1.691 | 38.35 | 0.4417 | 1455 |
| validation | CALM_UP | 0.2807 | 2.597 | 52.07 | 0.8565 | 1219 |
| validation | STRESS | 0.09141 | -7.126 | 135.6 | 3.129 | 397 |
| forward | CHOP | 0.2887 | -0.1033 | 24.93 | -0.4207 | 5697 |
| forward | CHOP2 | 0.3161 | 0.04588 | 33.25 | -0.08215 | 6236 |
| forward | CALM_UP | 0.269 | 2.144 | 39.89 | 0.5079 | 5307 |
| forward | STRESS | 0.1262 | -3.234 | 108.8 | 1.287 | 2490 |

Filtered-state switches over the whole series: 8285 (16.7% of candles). Mean normalized entropy 0.084; share of candles with entropy > 0.5: 1.4%.

(chart skipped: No module named 'matplotlib')
