# Findings: the HMM regime trading system, phases 1 to 4

Mission built 2026-10-04 from the pasted "HMM regime trading system" prompt
inside this repository's loop (SPEC.md). Everything below is out of sample,
after costs, on the research data lake, with the gates the prompt wrote
before any data was staged. Artifacts: `fits/SPY_day/BACKTEST.md`,
`fits/BTC-USD_hour/BACKTEST.md`, the fit reports and charts beside them.

## Verdict

**REJECTED on both assets by its own gates.** Neither the system (regime x
Kelly) nor any of the six other arms clears Sharpe > 1.5, hit rate > 55%,
t > 2 and both baselines after costs. Per the prompt's rule, no
`strategy.md` is written, paper trading does not start, and the mission
stops at phase 4 with this file as its result.

## What the two runs showed

**SPY daily, 2023-01 to 2026-10 (940 sessions, 3 bps per side).**

| Arm | Total | Sharpe | Max DD | Trades | Hit | In market |
|---|---|---|---|---|---|---|
| system (regime x Kelly) | 0.0% | 0.00 | 0.0% | 0 | | 0% |
| regime, fixed cap (diagnostic) | +24.3% | 1.01 | 5.6% | 9 | 44% | 33% |
| static trend rule | +19.8% | 0.68 | 7.9% | 13 | 38% | 43% |
| buy and hold | +100.6% | 1.34 | 19.0% | | | 100% |
| Jev only | +1.2% | 0.11 | 4.5% | 48 | 54% | 9% |
| regime + Jev | -0.2% | -0.02 | 2.4% | 22 | 59% | 4% |
| regime + Jev + confidence gate | +6.7% | 1.07 | 1.8% | 26 | 62% | 4% |

- The system never traded: the base trend rule's measured Kelly on the fit
  window is negative (p 0.31, b 2.0), and the spec sizes f* <= 0 to zero.
  That is the rule working, not a bug; the diagnostic arm shows what the
  regime layer does underneath it.
- The regime layer is a risk regulator: it beat the static rule on every
  risk number (drawdown 5.6% vs 7.9%, Sharpe 1.0 vs 0.7) by sitting out
  CHOP and STRESS, and it could not beat being long SPY through a bull tape.
- The confidence-gated Jev arm had the best trade quality in the grid (62%
  hit, t 2.07, 1.8% drawdown) at 4% time in market: the "abstain when
  uncertain" claim held, at the price of almost never trading.

**BTC/USD hourly, 2024-07 to 2026-10 (19,729 candles, 15 bps per side).**

| Arm | Total | Sharpe | Max DD | Trades | Hit | In market |
|---|---|---|---|---|---|---|
| system | -1.2% | -1.92 | 1.3% | 226 | 29% | 24% |
| regime, fixed cap | -7.7% | -0.67 | 10.6% | 22 | 27% | 2% |
| static trend rule | -10.0% | -0.88 | 10.0% | 10 | 20% | 1% |
| buy and hold | +33.4% | 0.52 | 53.9% | | | 100% |
| Jev only | -10.2% | -1.34 | 10.2% | 18 | 11% | 0% |
| regime + Jev | -10.0% | -1.40 | 10.1% | 37 | 11% | 1% |
| regime + Jev + confidence gate | -10.1% | -2.07 | 10.1% | 35 | 11% | 0% |

- Every rule-based arm lost money; buy and hold made 33% with a 54%
  drawdown. The states carry no direction on hourly BTC: next-candle return
  by state is +0.6 bps (CALM_UP), -0.3 (CHOP), +0.4 (CHOP2), -1.3 (STRESS),
  against 30 bps per round trip.
- The frozen base rule is the main culprit, and it was frozen before the
  backtest so it stays frozen here: a stop at 2 x hourly realized vol (about
  1%) is hit within hours of most entries, and the no-re-entry-after-a-stop
  rule then blocks the position until the trend flips, so the arms spent 1
  to 2% of the time in the market and most of their trades were stop-outs
  (11 to 29% hit rates). The system arm's 226 trades are the floor-sized
  position being switched off and on by the uncertainty rule, each round
  trip paying costs.
- Jev's battery mostly answered "not a clean setup" (mean multiplier 0.20,
  0.09 with the confidence gate) and the trades it allowed hit 11%. On
  hourly BTC its judgments added no information beyond reducing exposure.
- Sizing never left the floor: the rolling calibration of P(active state)
  against persistence ended at ECE 0.059, just above the 0.05 gate, so the
  system traded at a tenth of its cap throughout, by its own rule.
- State selection fell back to 5 states: every candidate count produced at
  least one tail-only state on hourly BTC returns, so the admissibility
  constraint rejected all of them and the raw rule applied, as documented in
  `model.select_and_fit`. CRASH then held 0 out-of-sample candles.

## Reading

The two runs agree with the phase-2 pause note: a Gaussian HMM on these
features is a volatility-and-trend classifier with no directional edge per
state. That makes it useless as a trader and useful as a size regulator on
a daily horizon, which is exactly the role the spec's FST section assigns
it. On an hourly horizon with 15 bps costs it is neither. Jev as an abstain
gate improves trade quality on daily SPY and does nothing on hourly BTC;
the article's claim that calibrated abstention helps is supported where
decisions are slow and refuted where they are fast and expensive.

The honest cost of the prompt's own discipline shows in the numbers: the
negative-Kelly rule and the calibration gate kept the system from trading,
which is what they are for. A looser system would have "traded more" and
lost more.

## What carries forward

1. **Regime layer for FST, daily only**: serve the SPY daily filtered state
   (and per-name daily states for the universe, phase 3 of the scope) as a
   `regime` signal family the runner reads as a size cap: CALM_UP 1.0, CHOP
   0.5, STRESS 0.25, CRASH 0. It may only reduce size. Gate before it
   touches live sizing: the SPY diagnostic's drawdown improvement must hold
   on the next six months of forward data.
2. **Jev as an abstain gate**, daily SPY shape, nowhere near hourly crypto
   decisions. Replay it on FST's own entries with the official endpoint
   before it earns anything more than a shadow log.
3. **The base rule is the open question, not the regime.** A re-registered
   trial with a stop sized to the holding horizon (e.g. 2 x daily vol on
   hourly entries, or no stop and a trend exit) is a legitimate next
   hypothesis for the loop to propose; it is not a tweak this mission makes.
4. Nothing from this mission is traded.

## Mission discipline, for the record

Seven approval or review pauses, four corrections logged in
`tasks/lessons.md` (a trivially passing no-lookahead test, a silent
backfill, a drift freeze that was a bug, a gate that zeroed the system),
thirty-eight tests, every number regenerated by `fit_report.py` and
`backtest.py` from the lake. The gates were never lowered.
