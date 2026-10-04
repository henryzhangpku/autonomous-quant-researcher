# Mission: off-exchange share of volume vs forward US stock returns

**Asked by Henry, 2026-10-03/04:** "find cheddarflow data linkage to stock
return prediction", then "give me alpha", then "do all". Data batch two of
the qs-research lake (Quiver Quantitative, FINRA daily off-exchange volume
per ticker, 2021-01 onward, 5,551 tickers; Alpaca adjusted daily bars for
the same universe).

## Data

| Family | Source | Coverage |
|---|---|---|
| Off-exchange daily volume (OTC_Short, OTC_Total, DPI) | Quiver `historical/offexchange/{ticker}` via the lake | 2021-01-11 onward, 4.4M ticker-days |
| Adjusted daily bars | Alpaca multi-symbol, `adjust=all` | 2020-12 onward, 5,551 symbols |

Signals are lagged one trading day for publication. Universe filter: close
>= $5 and 20-day median dollar volume >= $5M (liquid subsample $10 / $50M).

## Result (first pass, qs-research `research/dpi_offexchange/`)

DPI (short share of off-exchange volume) is null. The off-exchange SHARE of
consolidated volume predicts lower forward returns at every horizon, every
year, in liquid names, after reversal and size controls; the effect sits in
the top decile. A monthly long-decile-1 / short-decile-10 book made +209
bps a month over 69 months (t 3.82). FINDINGS.md has the reading and the
open questions (survivorship, borrow, costs); PREREGISTRATION.md freezes the
forward test from the 2026-11-02 decision day.

## For the loop

Approved observation for proposals: `short_vol_share` and `otc_share` as
of t-1, cross-sectional rank. The human baseline to beat is the frozen
monthly book. Seal 2026 as holdout; the discovery data is 2021-2025.
