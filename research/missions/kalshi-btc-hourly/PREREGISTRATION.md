# Preregistration: is Kalshi's hourly BTC binary pricing exploitable?

Frozen 2026-08-23, before any price-versus-outcome relationship in this data
has been computed. Price and liquidity distributions were inspected to design
the filters; settlement outcomes were not read against price.

## Data

`research/data/kalshi_hourly.jsonl.gz`, `data_sha256 ae682ab3…`, staged
2026-08-15 from Kalshi's public trade API. 410,956 settled hourly threshold
markets, KXBTCD and KXETHD, close times 2026-06-30 → 2026-08-14, each carrying
the final ~65 minutes of yes bid/ask candles plus the settlement result.

## Why most of the file is unusable, declared now

A BTC hourly ladder lists 40–300 strikes and only those near spot ever trade.
Measured at the START of each window (~T-60min), 17,886 markets (4.4%) price
between 0.05 and 0.95, and 12,481 of those have non-zero volume. The remaining
95% sit pinned at 0.5c or 99.5c and are excluded as untradeable, not as
evidence.

## Entry, cost and payoff (frozen)

- One observation per market, at the FIRST staged candle (~60 minutes before
  close). No intra-window timing, no re-entry.
- Filter: mid = (yes_bid + yes_ask)/2 in [0.05, 0.95] AND window volume > 0.
- Buying YES pays `yes_ask`; buying NO pays `1 - yes_bid`. The spread is
  therefore crossed on entry, as a taker.
- Kalshi taker fee, charged once on entry, settlement free:
  `fee = 0.07 * P * (1 - P)` per contract at execution price P.
- Held to settlement. Payoff is 1 if the side wins, else 0.

## Claims under test (frozen, conventional parameters, no grid)

**R1 — longshot fade.** Prediction markets are widely reported to overprice
longshots. Buy NO when `yes_ask <= 0.15`.

**R2 — favourite backing.** The mirror claim. Buy YES when `yes_ask >= 0.85`.

**R3 — control.** Buy YES on every qualifying market. If pricing is fair, this
returns roughly zero before fees and negative after them.

## Universe split and replication (frozen)

- Primary universe: **KXBTCD** (BTC). Splits by `close_time`, chronological,
  60/20/20 into discovery, validation and holdout.
- **KXETHD (ETH) is an untouched replication set.** It is not read until the
  BTC verdict is decided, and it never contributes to the BTC decision.

## Sample floor

A rule counts in a split only with at least 100 qualifying observations there.
Fewer is recorded as UNTESTABLE, not as refuted.

## Decision rule (frozen)

A rule is **SUPPORTED** only if mean net profit per contract is positive in
discovery AND validation AND holdout, after spread and fee, with the sample
floor met in all three. Anything else is **NOT SUPPORTED**; negative in the
majority of splits is **REFUTED**.

Replication is reported separately and cannot rescue a failed BTC verdict.

## What this cannot say

Forty-five days, one crypto regime, hourly contracts only — nothing here speaks
to the 15-minute (KXBTC15M) or daily ladders, which are not staged. Candle
closes proxy executable prices, so intraminute book movement is unseen, and a
resting order might have been filled better than a taker cross. Size is assumed
available at the quote; the median qualifying market trades 3,394 contracts,
but a large order would move these books.

Backtests are hypothetical, research-only evidence and are not investment
advice.
