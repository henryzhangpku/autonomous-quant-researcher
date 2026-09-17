# Results: no alpha in Kalshi hourly BTC binaries

Evaluated 2026-08-23 against the preregistration frozen in `6691e58b2`, before
any price-versus-outcome relationship was computed.

KXBTCD, 7,278 qualifying markets (mid 0.05–0.95 at ~T-60min, non-zero volume),
close times 2026-06-30 → 2026-08-14, split chronologically 60/20/20 into
4,366 / 1,456 / 1,456.

## The frozen rules: all refuted

Mean net profit per contract, after crossing the spread and paying the Kalshi
taker fee `0.07·P·(1−P)` once at entry; settlement free.

| Rule | Discovery | Validation | Holdout | Verdict |
|---|---:|---:|---:|---|
| R1 fade longshot (buy NO, ask ≤ 0.15) | −0.0051 | −0.0104 | −0.0142 | **REFUTED** |
| R2 back favourite (buy YES, ask ≥ 0.85) | −0.0185 | −0.0173 | −0.0032 | **REFUTED** |
| R3 control (buy YES, everything) | −0.0398 | −0.0525 | −0.0297 | — |

Negative in every split, on both sides. The widely repeated favourite–longshot
trade does not pay here.

## Why: the bias is real but smaller than the toll

Pooled across the window, the midpoint is visibly mis-calibrated in the classic
S-shape — longshots overpriced, favourites underpriced:

| mid bucket | n | mean mid | realised yes | bias |
|---|---:|---:|---:|---:|
| 0.2–0.3 | 702 | 0.240 | 0.187 | −0.054 |
| 0.3–0.4 | 581 | 0.354 | 0.274 | −0.080 |
| 0.6–0.7 | 606 | 0.645 | 0.729 | +0.085 |
| 0.8–0.9 | 1005 | 0.849 | 0.878 | +0.029 |

But crossing the spread and paying the fee costs **2.9–5.3 cents per contract**,
and the frozen thresholds sat in the buckets where bias is smallest (−0.016 at
≤0.1, +0.030 at ≥0.9). The edge lives in the middle of the curve and is 3–8
cents — the same order as the toll to reach it.

## And the S-curve does not survive splitting

| bucket | discovery | validation | holdout | sign stable |
|---|---:|---:|---:|:--|
| 0.0–0.1 | −0.025 | −0.007 | +0.007 | no |
| 0.1–0.2 | +0.001 | +0.009 | −0.026 | no |
| **0.2–0.3** | −0.032 | −0.105 | −0.047 | **yes** |
| 0.3–0.4 | −0.106 | +0.002 | −0.063 | no |
| 0.4–0.5 | −0.029 | −0.052 | +0.009 | no |
| 0.5–0.6 | +0.068 | −0.019 | −0.041 | no |
| 0.6–0.7 | +0.137 | −0.040 | +0.008 | no |
| 0.7–0.8 | +0.057 | −0.004 | +0.064 | no |
| **0.8–0.9** | +0.026 | +0.040 | +0.024 | **yes** |
| 0.9–1.0 | +0.043 | −0.009 | +0.024 | no |

**2 of 10 buckets keep their sign — chance alone predicts 2.5.** With three
splits a coin-flip bucket holds its sign one time in four, so ten buckets yield
about 2.5 stable ones from noise. Two is below that. The pooled +0.085 in
0.6–0.7 decomposes to +0.137 / −0.040 / +0.008.

Aggregate directional bias at mid also flips: **+0.0132, −0.0141, −0.0011**
across the three splits. Profile correlation discovery-vs-holdout is r = +0.499,
which is the pooled shape reasserting itself, not a tradeable pocket.

## Verdict

**No alpha found.** Kalshi's hourly BTC binaries are efficient to within
transaction costs on this window. The mispricing that exists is real but
smaller than the spread plus fee required to capture it, and its bucket
structure is indistinguishable from noise once split.

The one place an edge could still live is on the **maker** side: most Kalshi
markets carry a 0% maker fee, so resting an order earns the spread instead of
paying it, turning a 2.9–5.3 cent headwind into a tailwind. This dataset cannot
evaluate that — candle closes cannot say whether a resting order would have
been filled, and fill probability is the entire question. That needs a
different experiment, not a different rule.

## What this cannot say

Forty-five days, one crypto regime, hourly contracts only. Nothing here speaks
to the **15-minute (KXBTC15M)** or daily ladders, which are not staged. Candle
closes proxy executable prices, so intraminute book movement is unseen. Size is
assumed available at the quote; the median qualifying market traded 3,394
contracts, but a large order would move these books.

KXETHD was reserved as a replication set and remains unread — the BTC verdict
is refuted, so replication cannot rescue it and spending it would inform
nothing.

Backtests are hypothetical, research-only evidence and are not investment
advice.
