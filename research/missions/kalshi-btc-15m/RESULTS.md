# Results: Kalshi 15-minute BTC up/down binaries (KXBTC15M)

Mission completed 2026-08-22. *Hypothetical backtests, not investment
advice.* Preregistration: `PREREGISTRATION.md` (committed before the holdout
read, commit `1fa7a8e9d`).

## Verdict

**NOT SUPPORTED. No taker-side alpha found in KXBTC15M.**

The single preregistered candidate — fade the prior window's direction when
the prior market's mid traversed a range ≥ 0.60 ("volatility-reversal
fade") — lost money on the one-shot holdout:

| split | N | mean net/contract | t |
|---|---|---|---|
| discovery | 994 | +0.0108 | +0.70 |
| validation | 322 | +0.0171 | +0.63 |
| **holdout** | **308** | **-0.0351** | **-1.25** |

The mechanism that justified promotion also vanished out-of-sample: in
discovery+validation, after a high-range window the market priced the
prior-move side at 0.486 while it realised 0.452; in holdout the same
conditional read 0.496 priced vs 0.510 realised — continuation slightly
*won*. The in-sample calibration gap was regime noise, not a persistent
retail bias.

## What the market looks like

4,250 settled KXBTC15M markets, 2026-07-09 → 2026-08-23, one up/down market
per 15-minute window, 24/7 coverage. Every market shows its first two-sided
quote exactly 60 s after window open. Median spread is 1 cent (vs 3-5c on
the hourly ladders), entry mids centre tightly on 0.5 (IQR 0.415-0.575),
and the calibration curve is flat: no decile of entry mid shows a
significant miscalibration in discovery+validation. This book is *better*
priced than the hourly ladders — the deep open interest buys tight, honest
quotes. Cost per round trip at P≈0.5 is ~0.5c half-spread + 1.75c fee ≈
2.25c, and nothing we found clears it.

Controls: buy-YES-everything nets -1.2c to -2.1c per contract per split;
buy-NO-everything -2.2c to -3.1c — both ≈ the cost floor, i.e. fair pricing.

## Honesty accounting

- **88 hypotheses** tested on discovery+validation (appendix below). At our
  bar (N ≥ 300, positive mean net in both splits) the chance-expected pass
  count is roughly 2-4. Exactly 2 passed; one ("buy NO on Mondays",
  t = 0.87/0.33) was recorded but not promoted for lacking any economic
  rationale; the other was promoted and failed holdout. The outcome is
  fully consistent with zero true edge in the hypothesis space searched.
- Splits never pooled where they disagreed; all per-split numbers above.
- Holdout was read exactly once, after the preregistration commit.
- Round-2 lesson honoured: all volume conditioning used only candles at or
  before the entry candle; all drift signals priced at the candle AFTER the
  signal candle.
- Caught error, kept: the exploratory tercile threshold for prior-window
  range was computed on pooled discovery+validation (a feature-distribution
  peek across the split boundary, though not an outcome peek). The frozen
  rule replaced it with an absolute 0.60 before the holdout read.
- Daily BTC contracts are not staged and remain out of scope. The hourly
  series (KXBTCD/KXETHD) was not re-tested (see kalshi-btc-hourly mission:
  141 hypotheses, zero taker alpha).

## KXETH15M replication

Preregistration bound us to run the identical frozen rule once on the ETH
archive, reported separately (it could not rescue the failed BTC verdict).

The archive landed during the mission
(`research/data/kalshi_15m.jsonl.gz`, manifest
`kalshi_15m_manifest.json`, `data_sha256
017647db80f0d55d1a7c535b7adf09d6b12e0a9313d4df6a9e06664ee8327082`,
8,502 markets, close times 2026-07-09 05:30Z -> 2026-08-23 05:30Z, 12
dropped for no candles, 0 fetch errors); the ETH evaluation reads that
committed-manifest archive, not the `.part` checkpoint, so it is
reproducible. 4,252 KXETH15M markets parsed, own 60/20/20 chronological
split, identical frozen rule and economics, evaluated once:

| ETH split | N | mean net/contract | t |
|---|---|---|---|
| discovery | 1,022 | -0.0226 | -1.50 |
| validation | 341 | -0.0066 | -0.25 |
| holdout | 325 | -0.0297 | -1.09 |
| pooled | 1,688 | -0.0207 | -1.77 |

**Replication fails everywhere.** ETH never even shows the in-sample
effect that BTC discovery/validation showed (conditional pricing of the
prior-move side after volatile windows: 0.491 priced vs 0.489 realised —
fair). The rule loses roughly the cost floor on ETH, i.e. it is trading
noise at ~2c toll per contract.

## Appendix: all 88 hypotheses (discovery / validation, mean net per contract)

Nets are per contract after crossing the spread and the 0.07·P·(1-P) taker
fee. Bold = passed the numerical bar.

| hypothesis | N disc | net disc | N val | net val |
|---|---|---|---|---|
| A1 all YES | 2549 | -0.0211 | 850 | -0.0118 |
| A2 all NO | 2549 | -0.0221 | 850 | -0.0312 |
| A3 mid (0.05, 0.15] NO | 1 | +0.1316 | 0 | — |
| A3 mid (0.05, 0.15] YES | 1 | -0.1589 | 0 | — |
| A3 mid (0.15, 0.25] NO | 43 | +0.0390 | 17 | -0.0878 |
| A3 mid (0.15, 0.25] YES | 43 | -0.0728 | 17 | +0.0536 |
| A3 mid (0.25, 0.35] NO | 255 | -0.0139 | 97 | -0.0586 |
| A3 mid (0.25, 0.35] YES | 255 | -0.0263 | 97 | +0.0185 |
| A3 mid (0.35, 0.45] NO | 598 | -0.0339 | 183 | -0.0296 |
| A3 mid (0.35, 0.45] YES | 598 | -0.0102 | 183 | -0.0144 |
| A3 mid (0.45, 0.55] NO | 790 | -0.0263 | 280 | -0.0224 |
| A3 mid (0.45, 0.55] YES | 790 | -0.0190 | 280 | -0.0227 |
| A3 mid (0.55, 0.65] NO | 599 | -0.0178 | 177 | -0.0316 |
| A3 mid (0.55, 0.65] YES | 599 | -0.0260 | 177 | -0.0122 |
| A3 mid (0.65, 0.75] NO | 203 | -0.0071 | 75 | -0.0351 |
| A3 mid (0.65, 0.75] YES | 203 | -0.0328 | 75 | -0.0047 |
| A3 mid (0.75, 0.85] NO | 53 | -0.0273 | 17 | +0.0107 |
| A3 mid (0.75, 0.85] YES | 53 | -0.0064 | 17 | -0.0436 |
| A3 mid (0.85, 0.95] NO | 7 | -0.0101 | 4 | +0.1016 |
| A3 mid (0.85, 0.95] YES | 7 | -0.0167 | 4 | -0.1279 |
| A4 back fav (mid<=.35 NO) | 299 | -0.0058 | 114 | -0.0630 |
| A4 back fav (mid>=.65 YES) | 264 | -0.0257 | 97 | -0.0132 |
| A5 back dog (mid<=.35 YES) | 299 | -0.0335 | 114 | +0.0237 |
| A5 back dog (mid>=.65 NO) | 264 | -0.0126 | 97 | -0.0250 |
| B Asia 0-8 NO | 848 | -0.0104 | 284 | -0.0548 |
| B Asia 0-8 YES | 848 | -0.0329 | 284 | +0.0120 |
| B EU 8-14 NO | 621 | -0.0214 | 206 | -0.0744 |
| B EU 8-14 YES | 621 | -0.0218 | 206 | +0.0316 |
| B US 14-21 NO | 756 | -0.0453 | 252 | -0.0218 |
| B US 14-21 YES | 756 | +0.0021 | 252 | -0.0215 |
| B dow Fri NO | 384 | -0.0076 | 122 | -0.0159 |
| B dow Fri YES | 384 | -0.0355 | 122 | -0.0273 |
| **B dow Mon NO** | 384 | +0.0215 | 96 | +0.0159 |
| B dow Mon YES | 384 | -0.0647 | 96 | -0.0579 |
| B dow Sat NO | 384 | -0.0648 | 96 | +0.0118 |
| B dow Sat YES | 384 | +0.0210 | 96 | -0.0552 |
| B dow Sun NO | 384 | -0.0210 | 96 | -0.0227 |
| B dow Sun YES | 384 | -0.0224 | 96 | -0.0209 |
| B late 21-24 NO | 324 | -0.0003 | 108 | +0.0914 |
| B late 21-24 YES | 324 | -0.0432 | 108 | -0.1350 |
| C1 follow prev | 2543 | -0.0283 | 848 | -0.0351 |
| C2 fade prev | 2543 | -0.0150 | 848 | -0.0080 |
| C3 follow streak>=2 | 1220 | -0.0316 | 404 | -0.0456 |
| C3 follow streak>=3 | 572 | -0.0084 | 187 | -0.0845 |
| C3 follow streak>=4 | 283 | -0.0173 | 78 | -0.0459 |
| C4 fade streak>=2 | 1220 | -0.0118 | 404 | +0.0023 |
| C4 fade streak>=3 | 572 | -0.0351 | 187 | +0.0415 |
| C4 fade streak>=4 | 283 | -0.0263 | 78 | +0.0028 |
| C5 prev dn NO | 1275 | -0.0286 | 418 | -0.0461 |
| C5 prev dn YES | 1275 | -0.0146 | 418 | +0.0031 |
| C5 prev up NO | 1268 | -0.0155 | 430 | -0.0187 |
| C5 prev up YES | 1268 | -0.0279 | 430 | -0.0243 |
| D mom k=1 |d|>=0.01 | 2397 | -0.0213 | 797 | -0.0056 |
| D mom k=1 |d|>=0.03 | 1965 | -0.0242 | 647 | -0.0078 |
| D mom k=1 |d|>=0.05 | 1570 | -0.0160 | 507 | +0.0146 |
| D mom k=2 |d|>=0.01 | 2462 | -0.0115 | 804 | -0.0074 |
| D mom k=2 |d|>=0.03 | 2144 | -0.0086 | 705 | -0.0028 |
| D mom k=2 |d|>=0.05 | 1851 | -0.0053 | 611 | +0.0065 |
| D mom k=3 |d|>=0.01 | 2490 | -0.0080 | 828 | +0.0223 |
| D mom k=3 |d|>=0.03 | 2245 | -0.0035 | 732 | +0.0185 |
| D mom k=3 |d|>=0.05 | 2015 | -0.0015 | 643 | +0.0274 |
| D rev k=1 |d|>=0.01 | 2397 | -0.0187 | 797 | -0.0343 |
| D rev k=1 |d|>=0.03 | 1965 | -0.0155 | 647 | -0.0318 |
| D rev k=1 |d|>=0.05 | 1570 | -0.0234 | 507 | -0.0539 |
| D rev k=2 |d|>=0.01 | 2462 | -0.0268 | 804 | -0.0311 |
| D rev k=2 |d|>=0.03 | 2144 | -0.0293 | 705 | -0.0354 |
| D rev k=2 |d|>=0.05 | 1851 | -0.0323 | 611 | -0.0442 |
| D rev k=3 |d|>=0.01 | 2490 | -0.0286 | 828 | -0.0594 |
| D rev k=3 |d|>=0.03 | 2245 | -0.0326 | 732 | -0.0551 |
| D rev k=3 |d|>=0.05 | 2015 | -0.0341 | 643 | -0.0634 |
| E1 wide spread NO | 102 | -0.0218 | 21 | -0.0786 |
| E1 wide spread YES | 102 | -0.0318 | 21 | +0.0251 |
| E2 hi vol NO | 764 | +0.0035 | 369 | -0.0178 |
| E2 hi vol YES | 764 | -0.0456 | 369 | -0.0245 |
| E2 hi vol fade | 763 | -0.0276 | 369 | +0.0138 |
| E2 hi vol follow | 763 | -0.0146 | 369 | -0.0561 |
| E2 lo vol NO | 901 | -0.0383 | 232 | -0.0443 |
| E2 lo vol YES | 901 | -0.0056 | 232 | +0.0006 |
| E2 lo vol fade | 896 | -0.0306 | 230 | -0.0152 |
| E2 lo vol follow | 896 | -0.0134 | 230 | -0.0285 |
| **F big prev range fade** | 861 | +0.0144 | 279 | +0.0600 |
| F big prev range follow | 861 | -0.0576 | 279 | -0.1028 |
| F small prev range fade | 842 | -0.0272 | 288 | -0.0052 |
| F small prev range follow | 842 | -0.0162 | 288 | -0.0381 |
| G prev coinflip fade | 139 | -0.0752 | 65 | -0.1034 |
| G prev coinflip follow | 139 | +0.0316 | 65 | +0.0600 |
| G prev decisive fade | 2259 | -0.0078 | 731 | +0.0021 |
| G prev decisive follow | 2259 | -0.0354 | 731 | -0.0451 |

