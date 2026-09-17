# 0DTE / options backtest engine

A small, honest backtesting engine for same-day-expiry (and short-dated)
options structures on liquid US underlyings, powered by Alpaca market data.
Built 2026-08-08 out of the dip-call-spread study; every design rule below was
paid for by a bug or a wrong number found during that work.

**This is research tooling. It never places orders.** Autonomous Quant Researcher is a no-trade
repository; the engine reads market data and writes reports.

## Architecture

```
research/backtest/
  data.py        SessionStore (intraday underlying bars) + AlpacaOptionData
                 (real option leg pricing, flow aggregation). The ONLY module
                 that touches the network.
  structures.py  Option structures (vertical spreads, single legs) with
                 settlement payoff and modeled pricing. Pure functions.
  engine.py      The driver: universe -> signal -> structure -> entry pricing
                 -> settlement -> report. Data-agnostic; consumes Sessions.
  __init__.py    Public API.
tests/test_backtest_engine.py   Offline tests on synthetic sessions.
```

Split rationale: `engine.py` and `structures.py` are deterministic and fully
testable offline; `data.py` isolates every network call and every data-quality
guard, so a data bug cannot hide inside strategy logic.

## Quick start

```python
from research.backtest import (
    BacktestEngine, CostModel, SessionStore, call_debit_spread,
)

store = SessionStore.load()          # research/data/spy_5min.csv.gz
engine = BacktestEngine(costs=CostModel())

result = engine.run(
    sessions=store.sessions(),
    entry_hm=(12, 30),
    signal=lambda s, entry: s.ret_from_open(entry) <= -0.5,   # the dip
    structure=lambda s, px: call_debit_spread(px, width=2.0),
    label="dip -0.5% @ 12:30, ATM 20pt call spread",
)
print(result.report())               # win rates, CI, D/W, edge, P&L, by-year
```

Real-quote validation for the Feb-2024+ window (Alpaca OPRA):

```python
from research.backtest import AlpacaOptionData
real = AlpacaOptionData.from_env()   # needs ALPACA_API_KEY/SECRET in env
result = engine.run(
    ...,
    leg_pricer=real.leg_pricer(),
    raw_price_provider=real.raw_prices,
)
```

## Data layers, and which to trust

| Layer | Range | Source | Trust |
|---|---|---|---|
| Underlying 5-min bars | 2016 -> now | Alpaca SIP (`download_spy_intraday.py`) | high |
| Real option 1-min bars | **2024-02 -> now** | Alpaca OPRA | high (trade-derived) |
| Modeled option prices | any | Black-Scholes, 20d RV x 1.10 VRP proxy | validated ~2pp on D/W vs real quotes; still a model |

Alpaca's native option history starts **February 2024** and covers OPRA
equities: SPY yes, **SPX index options no** (SPY at ~1/10 scale is the proxy;
20 SPX points = $2 SPY). Historical **open interest is not available**, so
GEX-style signals cannot be backtested from this data -- they belong on the
forward pre-registration ledger (`research/data/*.jsonl`).

## Rules the engine enforces (each one was a live bug)

1. **Strikes come from RAW prices.** The stored bar file is dividend-adjusted
   (correct for same-day return signals -- the multiplier cancels). But
   adjusted 2024 prices sit ~3% below what traded, so strikes derived from
   them are ~3% ITM in reality. First run of the validation layer produced a
   nonsense 74.9% D/W exactly this way. `AlpacaOptionData` re-fetches raw
   prices for anything that touches strike space.
2. **Option quotes go stale, not absent.** Outside RTH the book widens and
   the last bar still returns a number, so a bad price looks like a price.
   Leg prices are rejected if the freshest bar is more than 15 minutes older
   than entry; spreads with degenerate debits (<=0 or >= width) are skipped
   loudly, never silently.
3. **D/W is the market's win-probability estimate.** A debit spread priced at
   D/W = 1/3 is a ~33% proposition by construction. The report always prints
   the realized win rate NEXT TO mean D/W plus the cost hurdle, because
   beating zero is not the test -- beating the price is.
4. **Costs are part of the evaluator, not the experiment.** `CostModel` is
   frozen; a search that can lower its own costs will. Default: 3% of width
   per round trip (entry half-spread + fees; settlement leg free for
   cash-settled styles).
5. **A control group is not optional.** `run()` scores the signal AND the
   every-session baseline in one pass. A signal must beat its base rate, not
   just feel good. (The dip: +6pp lift over control, real -- and still ~18pp
   short of the priced hurdle.)
6. **Small samples say so.** Reports carry Wilson CIs and a minimum
   detectable effect line. 47-per-arm can only confirm ~25pp effects; the
   report says "not measurable" instead of "no effect".
7. **Trials are counted.** Every parameter change after seeing a result is a
   new trial (Deflated Sharpe discipline). The engine can't enforce your
   honesty, but `Result.meta` records the full configuration so the ledger
   can. Family history for the founding study: 4 trials, all negative,
   recorded in `research/experiments/dip_call_spread*.py` docstrings.

## Findings already established with this machinery (don't re-buy them)

- 0DTE ATM call spreads, held to close: **-8.7% of width per trade** across
  2,642 sessions (2016-2026). On dip days: same. In 2020 itself: negative.
- The dip signal is real (+6pp win-rate lift, non-overlapping CIs) and the
  market prices it (post-dip debits are richer). Lift covers ~1/4 of the
  hurdle.
- 2:1 payoff variant (further OTM): worse (-12.5%/trade). The pricing
  identity means ratio-shopping moves you along the curve, not off it.
- Same-day 0DTE call/put premium imbalance as an overlay: no rescue
  (trial 3 arms unbalanced 86/8 -- put premium dominates dip days
  mechanically; trial 4 median split: bullish arm did WORSE by 4pp).

## Extending

- New structure: add a factory in `structures.py` returning a `Structure`
  (legs + settlement payoff). Credit spreads = negative-debit verticals.
- New signal: any `(Session, entry_price) -> bool`. Signals needing external
  data (flow, IV rank) should be computed into a per-date map first, then
  closed over -- keeps the engine loop pure.
- New underlying: run `download_spy_intraday.py --symbol QQQ`, then
  `SessionStore.load(symbol="QQQ")`.
```
