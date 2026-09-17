# Idiosyncratic-gap campaign (2026-08-14) — the anecdote refuted, and a real
# effect found where it wasn't looked for

Trigger: AVGO -5.94% on a day SPY fell -0.20%. A same-day AVGO-only
diagnostic (n=6) suggested idiosyncratic gaps CONTINUE. This campaign was
preregistered to test that on a panel the diagnostic never touched: 28 liquid
names + the 9-name tradeable universe, 2016 -> 2026-08-13, AVGO flagged
contaminated and excluded from all gates. Grid, costs (10 bps RT, doubled
stress), gates and split declared in `experiments/sweep_idio_gap.py` before
any panel row was read. 1,945 events: 744 idiosyncratic, 1,201 market-wide.

## Verdict 1 — the idiosyncratic-continuation hypothesis is REFUSED

Every IDIO cell failed gates, in both directions, on both panels:

| cell (ex-AVGO) | n disc | disc | val | gate verdict |
|---|---:|---:|---:|---|
| -4% h1 | 468 | +0.12% | +0.09% | halves-disagree |
| -4% h5 | 468 | +0.32% | +0.36% | halves-disagree |
| -6% h1 | 159 | +0.18% | +0.32% | bootstrap-crosses-zero |
| -6% h5 | 159 | +0.42% | +0.72% | halves-disagree |

The AVGO n=6 "continuation" was small-sample noise. After a large single-name
drop the market did not share, the panel says the next 1-5 days are a coin
flip after costs — no continuation edge to short, no reversion edge to buy.
The honest answer to "buy this dip?" on an idiosyncratic gap is: **no signal
exists in either direction on daily bars.**

## Verdict 2 — crash-day loser reversion is real, survives everything

The MIRROR series (same drops on days SPY <= -1%) produced three cells that
passed every discovery gate AND one-shot validation AND the benchmark-
relative control (name forward minus SPY forward, same cost):

| cell (ex-AVGO, MKT) | n disc | disc raw | val raw | disc REL | val REL | boot5 REL |
|---|---:|---:|---:|---:|---:|---:|
| -4% h1 | 904 | +0.70% | +0.24% | +0.26% | +0.19% | +0.10% |
| -4% h5 | 904 | +0.67% | +1.56% | +0.81% | +0.51% | +0.48% |
| -6% h5 | 313 | +1.09% | +1.63% | +1.78% | +0.74% | +1.10% |

Sign flip vs the IDIO regime reproduced exactly as the mechanism predicts:
a crash-day seller is a margin clerk (reverts); an idiosyncratic seller is an
analyst with new information (no rebound — and no reliable continuation
either). Recorded failure that keeps the gauntlet honest: -6% h1 passed
discovery at +1.93% and FAILED validation at -0.08%.

Caveats before anyone trades this: long-single-name-losers on crash days is
a stock expression, not an options one; borrow/liquidity not modeled; overlap
between events on the same crash day means the effective independent sample
is smaller than n suggests (weekly clustering not yet controlled). It is a
**candidate finding**, research_only, needing an expression + capacity +
cluster-robust study before any policy artifact exists.

## Product consequences

- No single-name dip policy. The 0DTE/1DTE single-name lane stays governed by
  the earlier boundary result (nine-name credit REFUSED; index-only).
- The crash-day reversion is consistent with why the index put credit's
  richest bucket is turbulent days: both monetize post-panic normalization.
  It does not change any existing policy's authority.
- AVGO specifically, 2026-08-14: idiosyncratic regime -> no researched signal.
  Any position is discretion, not research.

Backtests are hypothetical, research-only evidence and are not investment
advice.
