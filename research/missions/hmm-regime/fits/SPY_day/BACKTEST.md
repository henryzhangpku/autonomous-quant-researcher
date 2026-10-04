# Walk-forward backtest: SPY day

Generated 2026-10-04T01:53+00:00. Out of sample 2023-01-03 to 2026-10-01 (940 candles) after fit to 2022-01-01 and validation to 2023-01-01. 4 states (CHOP, CALM_UP, STRESS, CRASH); refits every 30 days on an expanding window, 31 refits of which 16 froze entries for drift. Costs 3 bps per side. Base-strategy Kelly inputs measured on the fit window: p=0.31, b=2.01 (f* = -0.036). Mean size cap of the system 0.00; state switches 37.

## Arms (identical data, costs, limits)

| arm | total_return | ann_return | sharpe | max_dd | t_stat | trades | hit_rate | avg_trade_bps | costs_paid | time_in_mkt | gate_mean |
|---|---|---|---|---|---|---|---|---|---|---|---|
| hmm | +0.0% | +0.0% | 0.00 | 0.0% | 0.00 | 0 |  |  | 0.000 | 0% |  |
| hmm_fixed | +24.3% | +6.0% | 1.01 | 5.6% | 1.96 | 9 | 44.4% | +268 | 0.005 | 33% |  |
| trend_only | +19.8% | +5.0% | 0.68 | 7.9% | 1.31 | 13 | 38.5% | +157 | 0.008 | 43% |  |
| buy_hold | +100.6% | +20.5% | 1.34 | 19.0% | 2.58 | 0 |  |  | 0.000 | 100% |  |
| jev_only | +1.2% | +0.3% | 0.11 | 4.5% | 0.22 | 48 | 54.2% | +1 | 0.026 | 9% | 0.18 |
| hmm_jev | -0.2% | -0.1% | -0.02 | 2.4% | -0.04 | 22 | 59.1% | -6 | 0.009 | 4% | 0.18 |
| hmm_jev_conf | +6.7% | +1.8% | 1.07 | 1.8% | 2.07 | 26 | 61.5% | +25 | 0.015 | 4% | 0.07 |

## Gates (verbatim), on the system arm out of sample

| gate | threshold | passed |
|---|---|---|
| sharpe | > 1.5 | FAIL |
| max_drawdown | < 0.15 | PASS |
| hit_rate | > 0.55 | FAIL |
| t_stat | > 2.0 | FAIL |
| beats_buy_hold | after costs | FAIL |
| beats_best_static | after costs | FAIL |

**REJECTED: the system does not clear its own gates.**

## Gates by arm

| arm | sharpe | max_drawdown | hit_rate | t_stat | beats_buy_hold | beats_best_static | all |
|---|---|---|---|---|---|---|---|
| hmm | FAIL | PASS | FAIL | FAIL | FAIL | FAIL | FAIL |
| hmm_fixed | FAIL | PASS | FAIL | FAIL | FAIL | PASS | FAIL |
| jev_only | FAIL | PASS | FAIL | FAIL | FAIL | FAIL | FAIL |
| hmm_jev | FAIL | PASS | PASS | FAIL | FAIL | FAIL | FAIL |
| hmm_jev_conf | FAIL | PASS | PASS | PASS | FAIL | FAIL | FAIL |

## By state (out of sample): share, the fixed-cap arm's P&L while in the state, and the raw next-candle return

| state | candles | share | pnl_bps_per_candle | time_in_market_fixed | next_ret_bps |
|---|---|---|---|---|---|
| CALM_UP | 387 | 0.412 | 5.899 | 0.664 | 6.677 |
| CHOP | 345 | 0.367 | -0.127 | 0.148 | 5.222 |
| CRASH | 36 | 0.038 | 0.000 | 0.000 | 22.853 |
| STRESS | 172 | 0.183 | -0.008 | 0.012 | 10.627 |

## System P&L by year

| year | pnl | candles |
|---|---|---|
| 2023 | +0.000 | 250 |
| 2024 | +0.000 | 252 |
| 2025 | +0.000 | 250 |
| 2026 | +0.000 | 188 |

## Refits

| t | frozen | reason |
|---|---|---|
| 2023-02-15 | False | ok |
| 2023-03-30 | True | state CRASH statistics moved beyond 1.0 sigma |
| 2023-05-12 | True | state CRASH statistics moved beyond 1.0 sigma |
| 2023-06-27 | True | state CRASH statistics moved beyond 1.0 sigma |
| 2023-08-09 | False | ok |
| 2023-09-21 | False | ok |
| 2023-11-02 | False | ok |
| 2023-12-15 | False | ok |
| 2024-01-31 | False | ok |
| 2024-03-14 | False | ok |
| 2024-04-26 | False | ok |
| 2024-06-10 | False | ok |
| 2024-07-24 | False | ok |
| 2024-09-05 | False | ok |
| 2024-10-17 | False | ok |
| 2024-11-29 | True | transition row CRASH moved by 0.33 L1 |
| 2025-01-15 | True | transition row CRASH moved by 0.34 L1 |
| 2025-02-28 | True | transition row CRASH moved by 0.35 L1 |
| 2025-04-11 | False | ok |
| 2025-05-27 | False | ok |
| 2025-07-10 | False | ok |
| 2025-08-21 | True | transition row CRASH moved by 0.35 L1 |
| 2025-10-03 | True | transition row CRASH moved by 0.35 L1 |
| 2025-11-14 | True | transition row CRASH moved by 0.41 L1 |
| 2025-12-30 | True | transition row CRASH moved by 0.42 L1 |
| 2026-02-12 | True | transition row CRASH moved by 0.37 L1 |
| 2026-03-27 | True | transition row CRASH moved by 0.38 L1 |
| 2026-05-11 | True | transition row CRASH moved by 0.38 L1 |
| 2026-06-24 | True | transition row CRASH moved by 0.37 L1 |
| 2026-08-06 | True | transition row CRASH moved by 0.37 L1 |
| 2026-09-18 | True | transition row CRASH moved by 0.37 L1 |

Last rolling calibration of P(active state) vs persistence: Brier 0.062, ECE 0.036, n 200 (calibrated).
