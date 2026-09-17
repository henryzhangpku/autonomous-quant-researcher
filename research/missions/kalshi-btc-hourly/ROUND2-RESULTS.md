# Round 2: conditional structure in Kalshi hourly BTC binaries — nothing clears the bar

Explored 2026-08-22. Round 1 (`6691e58b2` preregistration, `f293ff27e` results)
refuted the three unconditional rules; this round mined BTC for *conditional*
alpha under a pre-committed candidate bar, with KXETHD reserved as the only
remaining out-of-sample set.

**Verdict up front: zero of 138 conditional hypotheses met the candidate bar.
No rules were frozen, no ROUND2 preregistration was written, and KXETHD
outcomes remain unread.** The one cell that formally passed is a demonstrated
look-ahead artifact, documented below.

## Discipline

- KXBTCD outcomes were read against price in round 1 across all three splits,
  so **all of BTC is treated as in-sample exploration data** here. Nothing
  found on BTC alone can be called alpha; it can only earn the right to one
  frozen shot at ETH.
- **KXETHD outcomes were never read against price.** The only ETH numbers
  computed were feasibility counts: 218,210 markets, 5,208 qualifying under
  the frozen filter (comfortably above the 100-per-split floor had any rule
  earned the test).
- The candidate bar, fixed before exploration: a rule must fire on >= 300 BTC
  observations, show positive mean net per contract after spread and fee on
  BTC as a whole AND in each 60/20/20 chronological tercile, and carry a
  one-sentence economic rationale. The bar was not lowered when nothing
  cleared it.

## Frozen economics (unchanged from round 1)

Entry at the first staged candle (~T-60min); qualify on mid = (yes_bid +
yes_ask)/2 in [0.05, 0.95] and window volume > 0; buy YES pays `yes_ask`, buy
NO pays `1 - yes_bid`; Kalshi taker fee `0.07·P·(1−P)` once at entry at
execution price P; settlement free; payoff 1 if the side wins, else 0. For
momentum rules the signal is computed on candles before the entry candle and
entry is priced at the candle *after* the signal window (candle index 5 or
10), never at the signal candles themselves.

Universe: KXBTCD, 192,746 markets, 7,278 qualifying, split chronologically
60/20/20 into 4,366 / 1,456 / 1,456 (identical to round 1).

## What was explored — the full census

138 hypotheses across four passes. Every family listed in the mission brief
was covered; every cell's per-tercile numbers are in the appendix.

| family | # cells | best overall | summary verdict |
|---|---:|---:|---|
| H1 hour-of-day blocks × side (incl. US day 13–21 UTC vs overnight) | 16 | −0.011 | negative everywhere; no block sign-stable |
| H2 day-of-week × side, weekend | 16 | −0.012 | negative everywhere; tercile signs flip |
| H3 momentum (early drift → same direction, entry at candle 5/10) | 12 | −0.002 | best cell −0.0016; nothing positive overall |
| H4 contrarian (early drift → fade) | 12 | −0.003 | worse than momentum |
| H5 spread-width buckets × side (pass-1 buggy + pass-2 corrected) | 14 | +0.019* | *n=237 (<300) and terciles flip; wide spread is a −20 to −27c fire pit |
| H6 window-volume buckets × side (descriptive only — see artifact note) | 8 | −0.010 | negative everywhere |
| H7 ladder distance (strike vs ladder-implied spot, 6 buckets × side) | 12 | +0.003 | deep-ITM YES +0.003 pooled but −0.054 in t1 |
| H8 persistence (prior-hour surprise sign/size → this hour) | 8 | −0.018 | all negative; both momentum and contrarian |
| H9/H10/H12 favourite-longshot pockets × tight spread / hour (S-curve, cost-minimised) | 14 | +0.006* | *H12 US-day favourite cell; t1 = −0.067 |
| H11 favourite/longshot × early-drift confirmation | 6 | +0.003 | fav+drift-up +0.0025 pooled, t2 −0.020 |
| H13 volume-conditioned favourite backing | 2 | +0.164 | **look-ahead artifact — see below** |
| H14 near-money ladder rungs × side, tight | 2 | −0.010 | negative |
| H15 favourite backing after quiet prior hour | 2 | −0.001 | t1 −0.044 |
| H16/H17 entry-legal volume conditioning (first-candle / first-10 volume) | 6 | +0.006 | +0.028 / −0.044 / −0.025 across terciles: no |
| H18 within-ladder relative value (rung vs neighbour interpolation) | 6 | −0.016 | rich-fade and cheap-buy both negative |
| H19 confirmed favourite (mid ≥ 0.6, tight, drift up, entry candle 5) | 2 | +0.005 | +0.0114 / +0.0008 / −0.0086: dies in the newest data |
| **total** | **138** | | **0 legitimate passes** |

## The one formal pass is a look-ahead artifact, kept for the record

`H13: buy YES, mid 0.6–0.9, spread ≤ 1c, window volume below median` showed
+0.164 net per contract, positive in every tercile. It conditions on **total
window volume — a quantity not knowable at entry**. Diagnosis: correlation
between window volume and |mid − outcome| is 0.53; low *future* volume means
the price never moved, so favourites selected this way won 98.9% of the time
(vs 66.0% in the high-volume half at similar entry prices). The signal is the
outcome leaking backwards through the conditioning variable.

The entry-legal versions collapse: conditioning on first-candle volume gives
+0.028 / −0.044 / −0.025 across terciles; on first-ten-candle volume with
entry at candle 10, −0.015 / −0.034 / +0.024. The H6 window-volume buckets
share the look-ahead flaw and are retained as descriptive statistics only.

There is also a subtler version of the same problem baked into the frozen
qualifying filter itself (window volume > 0, inherited from round 1): it
conditions the *universe* on future activity. It is kept frozen for
comparability, but any live implementation would need an entry-time-only
universe definition.

## Errors caught and kept

1. **Pass-1 spread buckets dropped the modal spread.** `spread ∈ [0.01, 0.01]`
   matched zero rows because 0.99 − 0.98 = 0.010000000000000009 in floating
   point; 5,908 one-cent-spread markets fell out of every bucket and pass 1's
   H5 covered only 1,282 of 7,278 rows. Corrected in pass 2 with
   `spread ≤ 0.015`; both versions are in the appendix.
2. **H13 was briefly a candidate** until the look-ahead was identified. It is
   recorded above rather than deleted.

## Near-misses, for honesty about what almost fooled us

Every cell with positive pooled net and n ≥ 300, and why each fails:

| cell | n | t0 / t1 / t2 | overall |
|---|---:|---|---:|
| H12 YES mid 0.6–0.9, tight, US day | 793 | +0.037 / **−0.067** / −0.010 | +0.006 |
| H16 YES fav, tight, vol0 ≤ median | 663 | +0.028 / **−0.044** / −0.025 | +0.006 |
| H19 YES mid ≥ 0.6, tight, drift5 > 0 | 1,096 | +0.011 / +0.001 / **−0.009** | +0.005 |
| H11 YES fav & drift5 > 0 | 1,377 | +0.013 / **−0.005** / −0.020 | +0.003 |
| H7 YES deep-ITM | 1,081 | +0.017 / **−0.054** / +0.004 | +0.003 |

The pattern repeats round 1's: pooled positives live in the discovery tercile
and die in the later data. Nothing here distinguishes itself from a
favourable first six weeks.

## Multiple comparisons

Under a driftless null with roughly independent terciles, a noise rule goes
positive in all three about 1/8 of the time, so 138 hypotheses would produce
roughly 17 false passes *if returns were cost-free noise*. We observed one
(the artifact). The deficit is the point: the ~3–5 cent spread-plus-fee toll
drags nearly every cell below zero in at least one split. This is what an
efficient market minus transaction costs looks like from the inside.

## Verdict

**No conditional alpha found on the taker side at T-60 entry.** Hour, day,
early drift, spread, volume, ladder position, within-ladder relative value,
and prior-hour persistence — none supports a rule that survives its own
in-sample terciles after costs. The candidate bar was not lowered, and
**KXETHD outcomes remain sealed** for whichever future experiment actually
earns them.

Round 1's closing judgment stands and is sharpened: the remaining plausible
edge in these markets is structural (maker-side fee asymmetry, resting-order
fills), not conditional (a smarter taker filter). That requires fill data
this dataset does not contain.

## What this cannot say

Same window limits as round 1: 46 days, one crypto regime, hourly ladders
only, candle closes as executable-price proxies, size assumed available at
the quote. Exploration reused the same 7,278 BTC observations round 1 read,
so even the near-misses above carry no evidential weight beyond hypothesis
generation.

Hypothetical backtests, not investment advice.

## Appendix: every hypothesis, every tercile

Mean net per contract after spread and fee; t0/t1/t2 are the 60/20/20
chronological terciles. Cells marked — had no observations. H6 and H13
condition on window volume (look-ahead; descriptive only). Momentum cells
(H3/H4/H11/H17/H19) price entry at candle 5 or 10 as labelled.
| # | family:rule | n | tercile nets (t0 / t1 / t2) | overall |
|---:|---|---:|---|---:|
| 1 | H1a buy YES, US day (13-21 UTC) | 3257 | -0.0306 / -0.0703 / -0.0218 | -0.0369 |
| 2 | H1b buy NO,  US day | 3257 | -0.0823 / -0.0177 / -0.0336 | -0.0592 |
| 3 | H1c buy YES, overnight | 4021 | -0.0471 / -0.0375 / -0.0363 | -0.0431 |
| 4 | H1d buy NO,  overnight | 4021 | -0.0537 / -0.0301 / -0.0231 | -0.0430 |
| 5 | H1e YES h00-03 | 1249 | -0.0709 / -0.0851 / -0.0089 | -0.0615 |
| 6 | H1f NO  h00-03 | 1249 | -0.0338 / +0.0216 / -0.0550 | -0.0267 |
| 7 | H1e YES h04-07 | 927 | -0.0580 / +0.0242 / -0.0423 | -0.0383 |
| 8 | H1f NO  h04-07 | 927 | -0.0070 / -0.0788 / -0.0108 | -0.0221 |
| 9 | H1e YES h08-11 | 919 | +0.0008 / -0.0637 / +0.0008 | -0.0109 |
| 10 | H1f NO  h08-11 | 919 | -0.0850 / +0.0081 / -0.0373 | -0.0596 |
| 11 | H1e YES h12-15 | 1623 | -0.0374 / -0.1438 / +0.0051 | -0.0502 |
| 12 | H1f NO  h12-15 | 1623 | -0.0975 / +0.0346 / -0.0637 | -0.0652 |
| 13 | H1e YES h16-19 | 1493 | -0.0232 / +0.0209 / -0.0818 | -0.0262 |
| 14 | H1f NO  h16-19 | 1493 | -0.0749 / -0.0935 / +0.0294 | -0.0571 |
| 15 | H1e YES h20-23 | 1067 | -0.0522 / -0.0421 / -0.0400 | -0.0475 |
| 16 | H1f NO  h20-23 | 1067 | -0.0781 / -0.0442 / -0.0355 | -0.0618 |
| 17 | H2 YES dow0 | 1188 | -0.0903 / -0.1032 / -0.0641 | -0.0857 |
| 18 | H2 NO  dow0 | 1188 | -0.0221 / -0.0050 / -0.0013 | -0.0144 |
| 19 | H2 YES dow1 | 1232 | -0.0153 / -0.0300 / -0.0138 | -0.0172 |
| 20 | H2 NO  dow1 | 1232 | -0.0871 / -0.0384 / -0.0352 | -0.0693 |
| 21 | H2 YES dow2 | 1362 | -0.0426 / -0.0199 / -0.0169 | -0.0337 |
| 22 | H2 NO  dow2 | 1362 | -0.0518 / -0.0364 / -0.0196 | -0.0431 |
| 23 | H2 YES dow3 | 1055 | -0.0351 / -0.0504 / -0.0196 | -0.0362 |
| 24 | H2 NO  dow3 | 1055 | -0.0748 / -0.0320 / -0.0289 | -0.0539 |
| 25 | H2 YES dow4 | 1037 | -0.0069 / -0.1174 / -0.0216 | -0.0451 |
| 26 | H2 NO  dow4 | 1037 | -0.0805 / +0.0422 / -0.0402 | -0.0342 |
| 27 | H2 YES dow5 | 680 | -0.0057 / +0.0316 / -0.0969 | -0.0116 |
| 28 | H2 NO  dow5 | 680 | -0.0955 / -0.0819 / +0.0284 | -0.0759 |
| 29 | H2 YES dow6 | 724 | -0.0786 / +0.0254 / +0.0018 | -0.0438 |
| 30 | H2 NO  dow6 | 724 | -0.0701 / -0.1329 / -0.0891 | -0.0838 |
| 31 | H2w YES weekend | 1404 | -0.0413 / +0.0288 / -0.0321 | -0.0282 |
| 32 | H2w NO weekend | 1404 | -0.0831 / -0.1051 / -0.0488 | -0.0800 |
| 33 | H3 mom YES drift5>+0.0 | 2687 | -0.0033 / -0.0158 / -0.0317 | -0.0124 |
| 34 | H3 mom NO  drift5<-0.0 | 2612 | -0.0173 / +0.0120 / -0.0055 | -0.0087 |
| 35 | H4 ctr NO  drift5>+0.0 | 2687 | -0.0307 / -0.0172 / -0.0026 | -0.0215 |
| 36 | H4 ctr YES drift5<-0.0 | 2612 | -0.0183 / -0.0468 / -0.0278 | -0.0263 |
| 37 | H3 mom YES drift5>+0.02 | 2122 | -0.0017 / -0.0277 / -0.0446 | -0.0168 |
| 38 | H3 mom NO  drift5<-0.02 | 2048 | -0.0055 / +0.0088 / -0.0017 | -0.0016 |
| 39 | H4 ctr NO  drift5>+0.02 | 2122 | -0.0333 / -0.0069 / +0.0095 | -0.0182 |
| 40 | H4 ctr YES drift5<-0.02 | 2048 | -0.0321 / -0.0434 / -0.0330 | -0.0347 |
| 41 | H3 mom YES drift5>+0.05 | 1260 | -0.0086 / -0.0217 / -0.0696 | -0.0253 |
| 42 | H3 mom NO  drift5<-0.05 | 1159 | +0.0012 / +0.0117 / -0.0309 | -0.0026 |
| 43 | H4 ctr NO  drift5>+0.05 | 1260 | -0.0299 / -0.0152 / +0.0318 | -0.0126 |
| 44 | H4 ctr YES drift5<-0.05 | 1159 | -0.0429 / -0.0489 / -0.0060 | -0.0372 |
| 45 | H3 mom YES drift10>+0.0 | 2612 | -0.0132 / -0.0208 / -0.0472 | -0.0223 |
| 46 | H3 mom NO  drift10<-0.0 | 2415 | -0.0253 / +0.0002 / +0.0017 | -0.0139 |
| 47 | H4 ctr NO  drift10>+0.0 | 2612 | -0.0193 / -0.0119 / +0.0125 | -0.0108 |
| 48 | H4 ctr YES drift10<-0.0 | 2415 | -0.0075 / -0.0335 / -0.0347 | -0.0191 |
| 49 | H3 mom YES drift10>+0.02 | 2238 | -0.0127 / -0.0207 / -0.0609 | -0.0249 |
| 50 | H3 mom NO  drift10<-0.02 | 2058 | -0.0197 / +0.0164 / +0.0053 | -0.0066 |
| 51 | H4 ctr NO  drift10>+0.02 | 2238 | -0.0205 / -0.0127 / +0.0255 | -0.0089 |
| 52 | H4 ctr YES drift10<-0.02 | 2058 | -0.0135 / -0.0505 / -0.0392 | -0.0269 |
| 53 | H3 mom YES drift10>+0.05 | 1608 | -0.0217 / -0.0250 / -0.0613 | -0.0314 |
| 54 | H3 mom NO  drift10<-0.05 | 1464 | -0.0229 / +0.0209 / +0.0074 | -0.0071 |
| 55 | H4 ctr NO  drift10>+0.05 | 1608 | -0.0128 / -0.0097 / +0.0259 | -0.0034 |
| 56 | H4 ctr YES drift10<-0.05 | 1464 | -0.0116 / -0.0563 / -0.0422 | -0.0277 |
| 57 | H5 YES spread[0.01,0.01] | 0 | � / � / � | � |
| 58 | H5 NO  spread[0.01,0.01] | 0 | � / � / � | � |
| 59 | H5 YES spread[0.02,0.05] | 237 | +0.0620 / -0.0413 / -0.0311 | +0.0192 |
| 60 | H5 NO  spread[0.02,0.05] | 237 | -0.1074 / -0.0024 / -0.0119 | -0.0637 |
| 61 | H5 YES spread[0.06,0.15] | 183 | -0.0606 / -0.0666 / -0.0696 | -0.0613 |
| 62 | H5 NO  spread[0.06,0.15] | 183 | -0.0778 / -0.0683 / -0.0590 | -0.0765 |
| 63 | H5 YES spread[0.16,1.0] | 862 | -0.2506 / -0.1935 / -0.1700 | -0.2305 |
| 64 | H5 NO  spread[0.16,1.0] | 862 | -0.2941 / -0.2196 / -0.2549 | -0.2751 |
| 65 | H6 YES vol q1 | 1820 | -0.1213 / -0.1007 / -0.0749 | -0.1125 |
| 66 | H6 NO  vol q1 | 1820 | -0.1503 / -0.1326 / -0.1366 | -0.1457 |
| 67 | H6 YES vol q2 | 1819 | -0.0021 / -0.0399 / -0.0065 | -0.0113 |
| 68 | H6 NO  vol q2 | 1819 | -0.0285 / +0.0082 / -0.0199 | -0.0186 |
| 69 | H6 YES vol q3 | 1819 | +0.0026 / -0.0446 / -0.0097 | -0.0102 |
| 70 | H6 NO  vol q3 | 1819 | -0.0418 / +0.0103 / -0.0243 | -0.0267 |
| 71 | H6 YES vol q4 | 1820 | -0.0159 / -0.0331 / -0.0489 | -0.0273 |
| 72 | H6 NO  vol q4 | 1820 | -0.0215 / -0.0039 / +0.0126 | -0.0098 |
| 73 | H7 YES dist deepITM | 1081 | +0.0170 / -0.0541 / +0.0036 | +0.0031 |
| 74 | H7 NO  dist deepITM | 1081 | -0.2522 / -0.1627 / -0.2341 | -0.2346 |
| 75 | H7 YES dist ITM | 1522 | -0.0021 / -0.0388 / -0.0107 | -0.0118 |
| 76 | H7 NO  dist ITM | 1522 | -0.0289 / +0.0112 / -0.0154 | -0.0174 |
| 77 | H7 YES dist nearITM | 1045 | +0.0055 / -0.0189 / -0.0112 | -0.0045 |
| 78 | H7 NO  dist nearITM | 1045 | -0.0496 / -0.0225 / -0.0281 | -0.0376 |
| 79 | H7 YES dist nearOTM | 1047 | -0.0233 / -0.0164 / -0.0415 | -0.0269 |
| 80 | H7 NO  dist nearOTM | 1047 | -0.0214 / -0.0245 / +0.0013 | -0.0157 |
| 81 | H7 YES dist OTM | 1548 | -0.0150 / -0.0279 / -0.0336 | -0.0217 |
| 82 | H7 NO  dist OTM | 1548 | -0.0175 / +0.0006 / +0.0077 | -0.0083 |
| 83 | H7 YES dist deepOTM | 1034 | -0.2087 / -0.2273 / -0.1413 | -0.2054 |
| 84 | H7 NO  dist deepOTM | 1034 | -0.0190 / +0.0139 / -0.0452 | -0.0163 |
| 85 | H8 mom YES prev_up | 2827 | -0.0543 / -0.0822 / -0.0280 | -0.0549 |
| 86 | H8 mom NO prev_dn | 2639 | -0.0656 / -0.0275 / -0.0049 | -0.0452 |
| 87 | H8 ctr NO prev_up | 2827 | -0.0635 / -0.0001 / -0.0359 | -0.0468 |
| 88 | H8 ctr YES prev_dn | 2639 | -0.0360 / -0.0474 / -0.0516 | -0.0416 |
| 89 | H8v YES prev big-move | 2188 | -0.0561 / -0.0989 / -0.0699 | -0.0662 |
| 90 | H8v NO prev big-move | 2188 | -0.0737 / +0.0059 / +0.0119 | -0.0472 |
| 91 | H8q YES prev quiet | 1738 | -0.0227 / -0.0239 / -0.0014 | -0.0176 |
| 92 | H8q NO prev quiet | 1738 | -0.0651 / -0.0491 / -0.0509 | -0.0585 |
| 93 | H5x YES spread<=1c | 5908 | -0.0062 / -0.0331 / -0.0184 | -0.0144 |
| 94 | H5x NO spread<=1c | 5908 | -0.0248 / +0.0021 / -0.0125 | -0.0166 |
| 95 | H5x YES spread 2-5c | 318 | +0.0410 / -0.0459 / -0.0409 | +0.0051 |
| 96 | H5x NO spread 2-5c | 318 | -0.0849 / +0.0033 / -0.0022 | -0.0485 |
| 97 | H5x YES spread>5c | 1052 | -0.2082 / -0.1853 / -0.1612 | -0.1997 |
| 98 | H5x NO spread>5c | 1052 | -0.2462 / -0.2127 / -0.2378 | -0.2396 |
| 99 | H9 YES mid[0.55,0.95] tight | 2758 | +0.0031 / -0.0462 / -0.0056 | -0.0088 |
| 100 | H9 YES mid[0.6,0.9] tight | 1800 | +0.0046 / -0.0532 / -0.0101 | -0.0105 |
| 101 | H9 YES mid[0.6,0.7] tight | 456 | +0.0040 / -0.0723 / -0.0146 | -0.0157 |
| 102 | H9 YES mid[0.7,0.9] tight | 1344 | +0.0047 / -0.0468 / -0.0085 | -0.0087 |
| 103 | H9 YES mid[0.8,0.95] tight | 1611 | +0.0067 / -0.0230 / -0.0096 | -0.0030 |
| 104 | H9 NO mid[0.05,0.45] tight (fade longshot) | 2794 | -0.0058 / -0.0105 / +0.0008 | -0.0053 |
| 105 | H9 NO mid[0.1,0.4] tight (fade longshot) | 1799 | -0.0138 / -0.0163 / +0.0041 | -0.0103 |
| 106 | H9 NO mid[0.2,0.4] tight (fade longshot) | 966 | -0.0104 / -0.0001 / -0.0014 | -0.0064 |
| 107 | H10 YES mid[0.6,0.9] any-spread | 2256 | +0.0062 / -0.0407 / -0.0081 | -0.0067 |
| 108 | H10 YES mid[0.6,0.7] any-spread | 611 | +0.0077 / -0.0573 / -0.0288 | -0.0095 |
| 109 | H10 NO mid[0.1,0.4] any-spread | 2171 | -0.0163 / -0.0098 / +0.0102 | -0.0094 |
| 110 | H10 NO mid[0.2,0.4] any-spread | 1284 | -0.0144 / +0.0036 / +0.0104 | -0.0057 |
| 111 | H11 YES fav & drift5>0 | 1377 | +0.0126 / -0.0046 / -0.0196 | +0.0025 |
| 112 | H11 NO  long & drift5<0 | 1369 | -0.0059 / +0.0077 / +0.0217 | +0.0019 |
| 113 | H11 YES long & drift5>0 (breakout) | 1310 | -0.0217 / -0.0271 / -0.0421 | -0.0280 |
| 114 | H11 NO  fav & drift5<0 (breakdown) | 1243 | -0.0315 / +0.0161 / -0.0294 | -0.0203 |
| 115 | H11c NO fav & drift5>0 | 1377 | -0.0446 / -0.0258 / -0.0134 | -0.0343 |
| 116 | H11c YES fav & drift5<0 | 1243 | -0.0043 / -0.0526 / -0.0056 | -0.0155 |
| 117 | H12 YES mid[0.6,0.9] tight usday | 793 | +0.0370 / -0.0670 / -0.0095 | +0.0063 |
| 118 | H12 YES mid[0.6,0.9] tight overnight | 1007 | -0.0215 / -0.0429 / -0.0106 | -0.0236 |
| 119 | H13 YES mid[0.6,0.9] tight hivol | 1146 | -0.1015 / -0.1570 / -0.0873 | -0.1102 |
| 120 | H13 YES mid[0.6,0.9] tight lovol | 654 | +0.1676 / +0.1554 / +0.1617 | +0.1642 |
| 121 | H14 YES neardist fav tight | 970 | -0.0011 / -0.0345 / -0.0075 | -0.0103 |
| 122 | H14 NO neardist long tight | 969 | -0.0185 / -0.0399 / -0.0075 | -0.0201 |
| 123 | H15 YES mid[0.6,0.9] tight prevquiet | 774 | +0.0082 / -0.0437 / +0.0134 | -0.0008 |
| 124 | H15 YES mid[0.6,0.9] tight prevloud | 1016 | +0.0005 / -0.0627 / -0.0293 | -0.0179 |
| 125 | H16 YES fav69 tight vol0==0 | 3 | +0.2087 / � / � | +0.2087 |
| 126 | H16 YES fav69 tight vol0>0 | 1797 | +0.0040 / -0.0532 / -0.0101 | -0.0108 |
| 127 | H16 YES fav69 tight vol0<=med | 663 | +0.0281 / -0.0435 / -0.0245 | +0.0059 |
| 128 | H17 YES fav(mid10e.6-.9) v10<=med | 524 | -0.0146 / -0.0335 / +0.0238 | -0.0123 |
| 129 | H17 YES fav(mid10e.6-.9) v10==0 | 0 | � / � / � | � |
| 130 | H17 YES fav(mid10e.6-.9) any | 1659 | -0.0107 / -0.0026 / -0.0306 | -0.0136 |
| 131 | H18 NO rich rv>+0.02 | 1657 | -0.0342 / -0.0041 / -0.0334 | -0.0277 |
| 132 | H18 YES cheap rv<-0.02 | 1547 | -0.0347 / -0.0111 / -0.0328 | -0.0294 |
| 133 | H18 NO rich rv>+0.05 | 653 | -0.0798 / -0.0427 / -0.0931 | -0.0753 |
| 134 | H18 YES cheap rv<-0.05 | 622 | -0.0506 / -0.0159 / -0.0005 | -0.0316 |
| 135 | H18t NO rich rv>0.02 tight | 1422 | -0.0186 / +0.0040 / -0.0316 | -0.0163 |
| 136 | H18t YES cheap rv<-0.02 tight | 1322 | -0.0229 / -0.0065 / -0.0260 | -0.0200 |
| 137 | H19 YES fav>=0.6 tight drift5>0 | 1096 | +0.0114 / +0.0008 / -0.0086 | +0.0051 |
| 138 | H19 YES fav>=0.6 tight drift5>=0.02 | 846 | +0.0100 / -0.0006 / -0.0243 | +0.0007 |
Hypothetical backtests, not investment advice.
