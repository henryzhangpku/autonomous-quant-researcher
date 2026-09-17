# friday-odte-names campaign 1 — findings (2026-08-14)

Nine-name Mon/Wed/Fri 0DTE near-money put credit portfolio: AAPL, AMZN,
GOOGL, META, MSFT, NVDA, TSLA, AVGO, IBIT — the owner's single-name book.
One preregistered unconditional cell, equal-weight portfolio, actual traded
option bars, 1,504 rows. Strike grids resolved per name by ladder ($1/$2.5/
$5), cached per symbol-month; the first run's failure was itself the lesson
that near-money grids are per-name, per-price-era.
Sweep: `research/experiments/sweep_friday_odte_names.py`.
Ledger: `.research/sweeps/friday-odte-names-v1/`.

## Verdict: not promoted

| 0.5% cost | discovery | validation |
|---|---:|---:|
| validator | **passed** | rejected (`positive_both_halves`) |
| net bps of width / session | +246.6 | +129.4 |
| Sharpe | 2.08 | 1.08 |
| raw weekly LB | -0.0053 | **-0.0527** |
| drift-neutral LB | -0.0005 | -0.0195 |

## What it means

The single names carry a **diluted, lumpier** version of the index premium:
positive expectancy in both splits (+247 then +129 bps of width), but one
half of the validation window was negative outright, and the worst-week
bounds are several times deeper than SPY/QQQ's (-0.053 vs -0.001-class).
Earnings weeks and single-name gaps do to this portfolio what only
market-wide shocks do to the index tapes — and they happen more often.

The family boundary now has three data points and a clean gradient:

    SPY / QQQ (large-cap index)    7/8, worst-week ~0     <- the alpha
    nine single names (equal-wt)   positive, lumpy, fails  <- diluted
    IWM (small-cap index)          refused outright        <- absent

The premium is strongest exactly where the underlying is most diversified.
Selling single-name insurance means selling earnings/idiosyncratic risk,
and the market prices that closer to fair.

## Consequences

- Mag 7 / AVGO / IBIT 0DTE credit does NOT enter the policy registry. The
  tradeable alpha remains SPY and QQQ.
- The 1DTE rotation (Tue/Thu into next expiry) remains unbuilt; given the
  0DTE result, its prior is modest and it should not jump the queue ahead of
  the SPY/QQQ paper gauntlet.
- The skip census (210-287 per name of ~390 candidate days) doubles as a
  listing-history map: single-name same-day expiries are dominated by
  Fridays on this tape.

Backtests are hypothetical, research-only evidence and are not investment
advice.
