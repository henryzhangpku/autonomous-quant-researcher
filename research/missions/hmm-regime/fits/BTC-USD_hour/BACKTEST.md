# Walk-forward backtest: BTC/USD hour

Generated 2026-10-04T03:44+00:00. Out of sample 2024-07-01 to 2026-10-03 (19,729 candles) after fit to 2024-01-01 and validation to 2024-07-01. 5 states (CHOP, CHOP2, CALM_UP, STRESS, CRASH); refits every 30 days on an expanding window, 27 refits of which 0 froze entries for drift. Costs 15 bps per side. Base-strategy Kelly inputs measured on the fit window: p=0.25, b=4.70 (f* = +0.087). Mean size cap of the system 0.02; state switches 746.

## Arms (identical data, costs, limits)

| arm | total_return | ann_return | sharpe | max_dd | t_stat | trades | hit_rate | avg_trade_bps | costs_paid | time_in_mkt | gate_mean |
|---|---|---|---|---|---|---|---|---|---|---|---|
| hmm | -1.2% | -0.5% | -1.92 | 1.3% | -2.88 | 226 | 28.8% | -22 | 0.015 | 24% |  |
| hmm_fixed | -7.7% | -3.5% | -0.67 | 10.6% | -1.00 | 22 | 27.3% | -19 | 0.069 | 2% |  |
| trend_only | -10.0% | -4.6% | -0.88 | 10.0% | -1.32 | 10 | 20.0% | -94 | 0.030 | 1% |  |
| buy_hold | +33.4% | +13.6% | 0.52 | 53.9% | 0.78 | 0 |  |  | 0.002 | 100% |  |
| jev_only | -10.2% | -4.7% | -1.34 | 10.2% | -2.01 | 18 | 11.1% | -54 | 0.054 | 0% | 0.20 |
| hmm_jev | -10.0% | -4.6% | -1.40 | 10.1% | -2.11 | 37 | 10.8% | -50 | 0.070 | 1% | 0.20 |
| hmm_jev_conf | -10.1% | -4.6% | -2.07 | 10.1% | -3.10 | 35 | 11.4% | -47 | 0.061 | 0% | 0.09 |

## Gates (verbatim), on the system arm out of sample

| gate | threshold | passed |
|---|---|---|
| sharpe | > 1.5 | FAIL |
| max_drawdown | < 0.15 | PASS |
| hit_rate | > 0.55 | FAIL |
| t_stat | > 2.0 | FAIL |
| beats_buy_hold | after costs | FAIL |
| beats_best_static | after costs | PASS |

**REJECTED: the system does not clear its own gates.**

## Gates by arm

| arm | sharpe | max_drawdown | hit_rate | t_stat | beats_buy_hold | beats_best_static | all |
|---|---|---|---|---|---|---|---|
| hmm | FAIL | PASS | FAIL | FAIL | FAIL | PASS | FAIL |
| hmm_fixed | FAIL | PASS | FAIL | FAIL | FAIL | PASS | FAIL |
| jev_only | FAIL | PASS | FAIL | FAIL | FAIL | FAIL | FAIL |
| hmm_jev | FAIL | PASS | FAIL | FAIL | FAIL | FAIL | FAIL |
| hmm_jev_conf | FAIL | PASS | FAIL | FAIL | FAIL | FAIL | FAIL |

## By state (out of sample): share, the fixed-cap arm's P&L while in the state, and the raw next-candle return

| state | candles | share | pnl_bps_per_candle | time_in_market_fixed | next_ret_bps |
|---|---|---|---|---|---|
| CALM_UP | 5599 | 0.284 | -0.130 | 0.013 | 0.560 |
| CHOP | 6390 | 0.324 | -0.048 | 0.019 | -0.291 |
| CHOP2 | 7141 | 0.362 | 0.052 | 0.039 | 0.352 |
| STRESS | 599 | 0.030 | -0.100 | 0.018 | -1.332 |

## System P&L by year

| year | pnl | candles |
|---|---|---|
| 2024 | -0.002 | 4393 |
| 2025 | -0.005 | 8736 |
| 2026 | -0.006 | 6600 |

## Refits

| t | frozen | reason |
|---|---|---|
| 2024-07-31 | False | ok |
| 2024-08-30 | False | ok |
| 2024-09-29 | False | ok |
| 2024-10-29 | False | ok |
| 2024-11-28 | False | ok |
| 2024-12-28 | False | ok |
| 2025-01-27 | False | ok |
| 2025-02-26 | False | ok |
| 2025-03-28 | False | ok |
| 2025-04-27 | False | ok |
| 2025-05-27 | False | ok |
| 2025-06-26 | False | ok |
| 2025-07-27 | False | ok |
| 2025-08-26 | False | ok |
| 2025-09-25 | False | ok |
| 2025-10-25 | False | ok |
| 2025-11-24 | False | ok |
| 2025-12-24 | False | ok |
| 2026-01-23 | False | ok |
| 2026-02-22 | False | ok |
| 2026-03-25 | False | ok |
| 2026-04-24 | False | ok |
| 2026-05-24 | False | ok |
| 2026-06-23 | False | ok |
| 2026-07-23 | False | ok |
| 2026-08-22 | False | ok |
| 2026-09-21 | False | ok |

Last rolling calibration of P(active state) vs persistence: Brier 0.085, ECE 0.059, n 200 (NOT calibrated: sizing stayed at the floor).
