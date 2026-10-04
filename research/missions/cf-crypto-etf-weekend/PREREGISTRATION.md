# Pre-registration: 20-day crypto-ETF flow contrarian vs two-week ETH (frozen 2026-10-04 00:45 UTC)

The lead (FINDINGS_HORIZONS.md): `basket_bull_share_20`, the premium-weighted
bull share of the last 20 trading days of CheddarFlow prints on IBIT, ETHA,
FBTC, GBTC, ARKB and BITO, is NEGATIVELY related to the ETH log return over
the following two weeks (Spearman -0.42 raw, -0.41 after detrending, p 0.001,
58 Friday observations overlapping by half, present in both halves). It was
found on all the data there was, so it is in-sample everywhere. This file
freezes it so the next observations are an honest test.

## Frozen rule

Every Friday at 16:00 ET, from the lake's prints of the 20 trading days
ending that Friday (production `_flow_direction` labelling, pooled):

- `s` = (bullish premium - bearish premium) / total premium over the six ETFs
- if `s` > +0.10: call = ETH DOWN over the next two weeks
- if `s` < -0.10: call = ETH UP over the next two weeks
- otherwise: no call

Outcome: ETH/USD log return from Friday 16:00 ET to the Friday 16:00 ET two
weeks later (Alpaca hourly bar opens, as in the study).

The 0.10 band is set now, from the in-sample distribution (about the middle
third is inside it), and does not move.

## Wall

Discovery: everything through 2026-10-02 (the last Friday scored in the
study). Forward: Fridays from 2026-10-09 on. The in-sample split by half is
recorded in FINDINGS_HORIZONS.md for reference and is not evidence.

## Scoring

`qs-research/research/cf_crypto_etf_weekend/score_forward.py` appends one
row per Friday to `forward_calls.csv` (call, s, outcome when known) and is
idempotent; it reads only the lake. Acceptance on the forward rows, all
required, first checked when 25 non-overlapping calls have resolved (about
a year, since two-week outcomes on weekly calls overlap):
- hit rate >= 60% on the called weeks,
- Spearman(s, outcome) <= -0.25 with p < 0.05 on all forward Fridays,
- mean outcome of DOWN calls negative and of UP calls positive.

Failing any of these retires the lead. There is no tuning step between now
and that check.

## What the loop should do meanwhile

Propose structures on the same observation (the 20-day pooled flow window
over the lake export) under the v3 contract, with 2026-07-01 onward sealed as
holdout, so a gate-passing hypothesis, if any, is evaluated on data this
study never saw. This file is the human-stated baseline it must beat.
