# Findings, longer horizons: accumulated crypto-ETF flow vs crypto returns

Henry, after the one-Friday null: "not just friday one day, longer horizon
please." Read with `RESULTS_HORIZONS.md` (generated grid), `study_horizons.py`
(pre-registered design) and `robustness_horizons.txt` (split-sample, time-trend
and detrended checks).

## Verdict

**Pooling the flow over longer windows does change the picture, but in the
contrarian direction, and most of the apparent strength is a regime, not a
relationship.** Accumulated bullish or call-heavy flow on the crypto ETFs is
followed by *weaker* BTC/ETH returns over the next one to two weeks. One cell
survives every check and is worth handing to the researcher loop as a lead.
Nothing here is a tradeable result yet.

## What the grid shows (400 cells, Bonferroni bar p < 0.000125)

**Daily events (313 days), trailing windows of 1/3/5/10/20 days, forward 1/5/10
trading days.** The sign is negative in essentially every cell: the more
call-heavy IBIT's recent flow, the lower the forward return. Raw numbers look
strong (`ibit_call_share_20` vs 10-day BTC/IBIT: rho -0.26, p < 0.0001, n ~295;
`ibit_call_share_3` vs 5-day: rho -0.19, p 0.0007). But:

- **The 20-day call share trends hard over the sample** (Spearman vs time
  -0.84): IBIT flow went from call-heavy in 2025 to put-heavier in 2026 while
  crypto drifted up (+0.24). Remove a linear trend from both and the 10-day
  cells fall to rho -0.11 / -0.12 (p 0.03-0.06 on overlapping data, p 0.4-0.5
  on the non-overlapping 30-period subsample).
- **The effect lives entirely in the second half** (Feb 2026 on): first-half
  rho is +0.05 to +0.14 in every daily cell, second-half -0.27 to -0.38.
- **The best daily lead after detrending** is the 3-day call share vs the next
  5 trading days: detrended rho -0.15 (p 0.011, overlapping) and -0.20 (p 0.13)
  on the 61 non-overlapping weeks. Its mechanism is lopsided: IBIT flow is
  call-heavy 80% of the time, so the signal is really "the 64 days when puts
  dominated the last three sessions", after which BTC averaged +223 bps over
  five days (median +120, up 67%). Those days cluster in Jan, Jun, Jul and Aug
  2026, so this is closer to six or eight episodes than 64 observations.

**Friday events (62 weekends), targets out to two weeks.** The weekend itself
(48h, Monday morning) stays null for the short windows, as in the first study.
The 10- and 20-day basket windows add a contrarian read:

- **`basket_bull_share_20` vs ETH two weeks ahead: rho -0.42, p 0.0009, and it
  is the one cell that passes the checks**: present in both halves (-0.34 and
  -0.49), the signal has little time trend (-0.14), detrended rho -0.41
  (p 0.001). Net-bullish 20-day basket flow was followed by a mean ETH move of
  -485 bps over two weeks; net-bearish by +325 bps (Welch p 0.017). The 14-day
  targets on weekly events overlap by half, so the effective sample is nearer
  29 than 58, and the p-value does not clear the grid correction.
- `basket_bull_share_10` and `ibit_bull_share_10` vs Monday morning and the
  IBIT gap: detrended rho -0.28 to -0.31 (p 0.015-0.03), second half only.
- Activity (`ibit_log_premium_20`) vs the size of next week's ETH move: raw
  rho -0.34, but premium rose +0.56 with time while realised moves fell -0.34;
  detrended rho -0.19 (p 0.15). A regime, not a relationship.

## Reading

Across both event sets the same shape appears: when the crypto ETFs' options
tape has been leaning bullish for a couple of weeks, the next one to two
weeks are weak, and when it has been leaning to puts, they are strong. That
is the classic contrarian read of retail-visible options flow, and the
Friday-only tercile hint in the first study pointed the same way. The honest
caveats are that most of the daily-set significance is the 2025-to-2026
regime shift, the surviving cell is a two-week horizon on weekly events with
roughly 29 independent observations, and the "put-dominant" branch is a few
episodes. None of this clears the pre-registered bar.

## What to do with it

1. **Hand `basket_bull_share_20` -> 2-week return to the researcher loop** as
   the lead, with a discovery/validation split that puts 2026 H2 behind the
   wall. The lake export already carries every print the loop needs.
2. **Grow the sample before re-testing**: every new trading day lands in the
   lake automatically; re-run both scripts monthly. At ~100 weekends the
   two-week cell either firms up or dies.
3. **Do not add this to `btc_weekend`**: the weekend horizon is still null, and
   the contrarian tilt at 1-2 weeks is the opposite of what Gate 1 assumes.
