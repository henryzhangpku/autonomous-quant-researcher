# Preregistration: Kalshi 15-minute BTC up/down binaries (KXBTC15M)

Frozen 2026-08-22, after exploration on the discovery and validation splits
only. Holdout outcomes have not been read. KXETH15M outcomes have not been
read. *Hypothetical backtests, not investment advice.*

## Data

`research/data/kalshi_15m.part.jsonl`, KXBTC15M rows only, extracted to a
host-local snapshot of 4,250 settled markets (snapshot sha256
`4eb526ad7acee048a1d8cefcff4b7d4851ddd892e484fc21e8f47b0f3e89533f`),
close times 2026-07-09 05:30Z → 2026-08-23 05:15Z. One up/down market per
15-minute window; candles are per-minute yes bid/ask over a ~20-minute
staging window. Daily BTC contracts are not staged and are out of scope.
The hourly series (KXBTCD) was exhaustively tested in the prior mission
(141 hypotheses, no taker alpha) and is not re-tested here.

## Frozen entry and economics (carried over from the hourly rounds)

- Entry: the FIRST candle with a two-sided quote (bid > 0 and ask < 1, both
  non-null). In this dataset that is always 60 s after window open.
- Qualifying filter: entry mid in [0.05, 0.95].
- Buy YES pays `yes_ask`; buy NO pays `1 - yes_bid` (taker, spread crossed).
- Kalshi taker fee once at entry: `0.07 * P * (1-P)` at execution price P.
  Settlement free. Payoff 1 if the side wins, else 0. Held to settlement.

## Split (frozen)

Chronological by close_time, 60/20/20:
- discovery: 2,550 rows, close_time ≤ 2026-08-05 05:15Z
- validation: 850 rows, 2026-08-05 05:30Z → 2026-08-14 06:15Z
- holdout: 850 rows, close_time ≥ 2026-08-14 06:30Z — **evaluated exactly once**

## Exploration summary (discovery + validation)

88 hypotheses were tested (full table in RESULTS.md): calibration by entry-mid
decile both sides, favourite/longshot, UTC session blocks, day-of-week,
prev-window follow/fade, settlement streaks 2-4, early price drift
(momentum/reversal, k=1-3 min, signal candle strictly before trade candle),
spread width, entry-legal volume terciles, prior-window volatility, prior
close extremity. Chance-expected count of hypotheses passing the bar
(N ≥ 300, positive mean net in discovery AND validation separately) is
roughly 2-4; exactly 2 passed. "Buy NO on Mondays" passed numerically
(t = 0.87 / 0.33) but is a bare directional seasonal with no economic
rationale and is NOT promoted. One candidate is promoted.

## Candidate R1 — volatility-reversal fade (frozen rule)

**Rationale (one sentence):** after an unusually volatile 15-minute window,
the next window's opening quote overprices continuation of the prior move —
the market prices the prior-move side at 0.486 while it realises 0.452
(vs 0.490 / 0.498, i.e. fair, after quiet windows).

**Rule (exact):**
1. The immediately prior window's market exists (close_time exactly 15 min
   earlier) and is settled.
2. `prev_traj_range` = max − min of the two-sided mid over ALL of the prior
   market's candles ≥ **0.60**.
3. If the prior window settled `yes`, buy NO at entry; if `no`, buy YES.
4. Entry, qualifying filter, fees as frozen above. One contract per signal.

In-sample: discovery N = 994, mean net **+0.0108** (t = 0.70); validation
N = 322, mean net **+0.0171** (t = 0.63). Monotone in the threshold
(T = 0.7: +0.0286 / +0.0466; T = 0.8: +0.0478 / +0.0325) and positive in
5 of 6 ISO weeks. Declared weakness: per-split t-stats are < 1; promotion
rests on the dose-response and calibration mechanism, and holdout decides.

## Verdict procedure (frozen)

- Holdout evaluated ONCE. **SUPPORTED** requires mean net > 0 with ≥ 100
  qualifying holdout observations. Anything else is NOT SUPPORTED.
- **KXETH15M replication:** untouched until the BTC verdict is written. The
  identical frozen rule is then applied once to the full ETH archive with its
  own 60/20/20 chronological split, reported per split and pooled,
  separately from BTC. ETH cannot rescue a failed BTC verdict.
