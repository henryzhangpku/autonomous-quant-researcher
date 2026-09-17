# Preregistration: call-side asymmetry in 0DTE near-money credit spreads

Frozen 2026-08-22, before any replication data is staged or inspected.

## Claim under test (from QSR Research Digest 001)

Across liquid 0DTE markets, near-money 0DTE **call** credit spreads carry
higher net expectancy than the equivalent **put** credit spreads. In the
original 12-market measurement, 6 of 11 call configs were net-positive in all
three time splits versus 1 of 11 put configs (digest says 1 of 9; two
thin-history names excluded there).

## Why this needs a replication

The side pattern was discovered by inspecting all configurations before the
claim was named (multiple comparisons). The original data cannot judge it —
it is the data that produced it.

## Replication universe (frozen)

Eight liquid 0DTE markets NOT in the original twelve: **DIA, XLF, XLE, SMH,
NFLX, AMD, COIN, PLTR**. If a name's daily expiries or option bars are
unavailable from the provider, it is dropped and recorded; the test requires
at least 6 of 8 staged, both sides.

## Frozen measurement

- Structure: 0DTE near-money credit spread, pct-defined strikes
  (offset_pct 0.0026, width_pct 0.0064), entry 10:00 ET, settled at
  intrinsic at the close. Real option bars only. Both sides, same sessions.
- Statistic per market: `diff = net_pct_width(CCS) − net_pct_width(PCS)`,
  net of 50 bps of width per trade, full staged window.
- Sample floor: a market counts only if BOTH sides have ≥ 60 trades.

## Decision rule (frozen)

The claim **replicates** if: the median per-market `diff` across counted
markets is > 0 AND at least 3/4 of counted markets have `diff` > 0.
Anything else — including a positive median with a split vote — is
**not replicated**. A negative median is **refuted**.

## What this test cannot say

It tests the side-asymmetry sign pattern, not its size, and says nothing
about whether either side is tradable after real execution costs. Costs are
assumed 50 bps of width, not measured fills.

Backtests are hypothetical, research-only evidence and are not investment
advice.
