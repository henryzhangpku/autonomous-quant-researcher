# Robustness: off-exchange share of volume vs forward returns

## short_vol_share, h=5: deciles (1 = lowest off-exchange share), excess vs equal-weight universe

| decile | fwd_bps | excess_vs_market_bps | t_excess | n_periods |
|---|---|---|---|---|
| 1 | 20.3 | 16.4 | 3.76 | 247 |
| 2 | 12.7 | 12.4 | 3.07 | 247 |
| 3 | 11.2 | 14 | 3.76 | 247 |
| 4 | 8.9 | 6.9 | 1.89 | 247 |
| 5 | 6 | 7.1 | 2.33 | 247 |
| 6 | 5 | 7 | 2.43 | 247 |
| 7 | 3.3 | -0.5 | -0.17 | 247 |
| 8 | -1.8 | -0.9 | -0.26 | 247 |
| 9 | -6.4 | -7.5 | -1.78 | 247 |
| 10 | -51.9 | -54.7 | -5.17 | 247 |

## short_vol_share, h=20: deciles (1 = lowest off-exchange share), excess vs equal-weight universe

| decile | fwd_bps | excess_vs_market_bps | t_excess | n_periods |
|---|---|---|---|---|
| 1 | 77.4 | 53.7 | 3.5 | 62 |
| 2 | 48 | 35.2 | 2.34 | 62 |
| 3 | 47.8 | 42.7 | 3.18 | 62 |
| 4 | 39.4 | 27.1 | 2.18 | 62 |
| 5 | 29.2 | 35.7 | 2.6 | 62 |
| 6 | 21.8 | 5.8 | 0.49 | 62 |
| 7 | 19.2 | -1.3 | -0.12 | 62 |
| 8 | 1.5 | 5.6 | 0.43 | 62 |
| 9 | -16.8 | -17.4 | -1.16 | 62 |
| 10 | -183 | -187 | -3.95 | 62 |

## otc_share, h=5: deciles (1 = lowest off-exchange share), excess vs equal-weight universe

| decile | fwd_bps | excess_vs_market_bps | t_excess | n_periods |
|---|---|---|---|---|
| 1 | 17.8 | 15 | 3.09 | 247 |
| 2 | 10.7 | 10.5 | 2.14 | 247 |
| 3 | 9.9 | 10.7 | 2.5 | 247 |
| 4 | 10.3 | 9.2 | 2.47 | 247 |
| 5 | 10.8 | 11.4 | 3.43 | 247 |
| 6 | 5.5 | 2.2 | 0.69 | 247 |
| 7 | 2.8 | 4.2 | 1.37 | 247 |
| 8 | 0.4 | 0.8 | 0.25 | 247 |
| 9 | -6.6 | -6.3 | -0.99 | 247 |
| 10 | -54.5 | -57.5 | -4.56 | 247 |

## otc_share, h=20: deciles (1 = lowest off-exchange share), excess vs equal-weight universe

| decile | fwd_bps | excess_vs_market_bps | t_excess | n_periods |
|---|---|---|---|---|
| 1 | 78 | 59.5 | 4 | 62 |
| 2 | 45.6 | 41.7 | 2.16 | 62 |
| 3 | 43.7 | 30.8 | 2 | 62 |
| 4 | 40.7 | 36.3 | 2.31 | 62 |
| 5 | 33.7 | 13.6 | 0.9 | 62 |
| 6 | 26 | 0.3 | 0.02 | 62 |
| 7 | 20 | 10.6 | 0.88 | 62 |
| 8 | 6.3 | 9.2 | 0.68 | 62 |
| 9 | -17.9 | -14.4 | -0.73 | 62 |
| 10 | -191.4 | -187.5 | -3.45 | 62 |

## Liquid subsample: close >= $10, 20-day median dollar volume >= $50M (798 names/day)

| signal | h | mean_IC | IC_t | IC_ctrl | IC_ctrl_t | D10-D1_bps | LS_t | n |
|---|---|---|---|---|---|---|---|---|
| short_vol_share | 1 | -0.0159 | -5.55 | -0.0145 | -5.34 | -13 | -4.84 | 1234 |
| short_vol_share | 5 | -0.028 | -4.6 | -0.0251 | -4.32 | -54.6 | -3.96 | 247 |
| short_vol_share | 10 | -0.0332 | -4.2 | -0.0324 | -4.27 | -103.8 | -4.07 | 123 |
| short_vol_share | 20 | -0.0441 | -4.08 | -0.042 | -4.07 | -192.3 | -3.95 | 62 |
| otc_share | 1 | -0.0143 | -4.39 | -0.015 | -4.95 | -12 | -3.79 | 1234 |
| otc_share | 5 | -0.0238 | -3.54 | -0.0225 | -3.55 | -58.1 | -3.5 | 247 |
| otc_share | 10 | -0.0261 | -3.14 | -0.0268 | -3.49 | -101.4 | -3.4 | 123 |
| otc_share | 20 | -0.0362 | -3.19 | -0.0372 | -3.59 | -182.1 | -3.3 | 62 |

## Top 1,000 names by dollar volume each day

| signal | h | mean_IC | IC_t | D10-D1_bps | LS_t | n |
|---|---|---|---|---|---|---|
| short_vol_share | 1 | -0.0165 | -6.08 | -15.4 | -5.6 | 1234 |
| short_vol_share | 5 | -0.0281 | -4.67 | -58.3 | -3.93 | 247 |
| short_vol_share | 10 | -0.0327 | -4.11 | -115.1 | -3.98 | 123 |
| short_vol_share | 20 | -0.0412 | -3.79 | -196.9 | -3.52 | 62 |

## Monthly rebalance (first decision day each month, 20-day hold), short_vol_share deciles, excess vs universe

| decile | excess_bps_per_month | t | months | share_positive |
|---|---|---|---|---|
| 1 | 54.5 | 3.09 | 69 | 0.68 |
| 2 | 26.1 | 1.74 | 69 | 0.58 |
| 3 | 51.1 | 3.75 | 69 | 0.74 |
| 4 | 30.4 | 2.48 | 69 | 0.62 |
| 5 | 14.9 | 1.37 | 69 | 0.62 |
| 6 | 5 | 0.4 | 69 | 0.52 |
| 7 | 4.3 | 0.42 | 69 | 0.54 |
| 8 | -12.4 | -0.97 | 69 | 0.42 |
| 9 | -18.9 | -1.19 | 69 | 0.48 |
| 10 | -154.9 | -3.67 | 69 | 0.28 |

Long decile 1 / short decile 10, monthly: **+209 bps per month**, t = 3.82, 69 months, positive in 72% of months, worst month -1949 bps.
