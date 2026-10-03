# Mission: Friday crypto-ETF options flow vs the weekend crypto move

**Asked by Henry, 2026-10-03 (a Saturday):** "we are at weekend, where only
crypto markets are open. I want to see if IBIT or crypto ETF data from
CheddarFlow can predict the weekend crypto price returns."

## Data

All inputs come from the qs-research data lake (`qs_research_web/datalake`),
which downloads vendor history once and appends only the missing days:

| Family | Source | Symbols | Coverage |
|---|---|---|---|
| Options flow (prints + CF day aggregate) | CheddarFlow via qs-research `/api/v1/flow-history` | IBIT, ETHA, FBTC, GBTC, ARKB, BITO, MSTR, COIN | 2025-07-01 onward (CF's stored history for these names is empty before July 2025 and thin until mid-2026) |
| Bars | Alpaca | BTC/USD, ETH/USD hourly; IBIT, ETHA daily; IBIT hourly | 2025-06-01 onward |

The flow export for this loop is produced by
`python -m qs_research_web.datalake export-flow <SYMBOL> --out <file>.jsonl`
and loads here through `research/providers/qs_datalake.py` as a validated
`FeatureExport` (canonical envelopes, hashed manifest, provenance clocks).

## Pre-registered study (first pass, run in qs-research)

Script: `qs-research/research/cf_crypto_etf_weekend/study.py`. Its docstring
is the pre-registration: event = every Friday; signals = Friday-only flow
features (production bull/bear labelling, production direction call, CF's own
day aggregate, largest print, pure-ETF basket, basket with MSTR/COIN at half
weight, the pooled week, ETHA's own flow); targets = BTC and ETH log returns
from Friday 16:00 ET to +24h, +48h and Monday 09:00 ET, plus IBIT's own
Friday-close-to-Monday-open gap; baselines = unconditional drift, BTC's
Friday return sign, IBIT's Friday return sign; tests = Spearman, sign hit
rate with binomial p, Welch t on signed means; Bonferroni bar p < 0.001 for
the grid.

Results: `RESULTS.md` in this directory (copied from the qs-research run) and
the per-weekend table `weekend_table.csv`.

## What the loop should do with this

This mission is a candidate for the v3 loop's `flow` family: the lake export
is the immutable source, the Friday 16:00 ET observation window is the
decision time, and the weekend return is the outcome. The first-pass study is
the human-readable baseline the loop's hypotheses must beat; if the loop
cannot find a gate-passing hypothesis either, that is the finding.

Limitations to carry into any finding: ~60 weekends; CheddarFlow stores only
notable prints (median 12 IBIT prints per Friday in this window); no options
data for the ETFs before July 2025; weekend crypto returns are fat-tailed, so
a handful of weekends dominate any mean.
