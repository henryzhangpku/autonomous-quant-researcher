# Findings: Friday crypto-ETF options flow vs the weekend crypto move

Read with `RESULTS.md` (generated) and `study.py` (the pre-registered design).
Written 2026-10-03 after the first full run on the research data lake.

## Verdict

**No. On 60 weekends (2025-07-11 to 2026-09-25), Friday's CheddarFlow flow on
IBIT, the crypto-ETF basket, or ETHA does not predict the weekend BTC or ETH
move.** Nothing in the 84-cell grid clears the Bonferroni bar (p < 0.001); the
best raw p-values belong to the two *baselines*, not to any flow signal, and
the flow signals that look mildly positive at 24 hours are gone at 48 hours
and by Monday morning.

## What the numbers say

- **The production direction call** (`ibit_direction_sign`, the app's
  bullish/bearish label with the 15% net-directional rule) was right 60% of the
  time on BTC at 24h (n=40, p=0.27) and exactly **50% at 48h**. On Monday
  morning it was 45%. That is a coin flip.
- **IBIT bull share** (the continuous version) has a Spearman of -0.06 with the
  48h BTC return and **-0.20 with Monday morning** and with IBIT's own
  Friday-close-to-Monday-open gap. The sign is the wrong way: the most bullish
  third of Fridays had the worst Monday outcomes (BTC -52 bps, 45% up; IBIT
  gap -55 bps, 45% up) while the middle third had the best (+116 bps, 65% up).
  Not significant, but if anything the loud Friday call buying is a fade.
- **CF's own day aggregate** (call share of session premium, its sentiment
  label) is flat: hit rates 45-53%, Spearman within +-0.10.
- **The largest print of the day** is the only flow feature with a positive
  24h read (63% on BTC, 61% on ETH, Welch p 0.03 / 0.02) and it is **gone at
  48h (47%) and Monday (49%)**. With 51 observations and this many cells, that
  is the shape of noise, not of an edge.
- **The basket** (pure ETFs, or with MSTR and COIN at half weight, the
  production `btc_weekend` basket) adds nothing: 50-58% hit rates, all p > 0.2.
- **ETHA's own flow vs ETH** is likewise null (ETH 48h: Spearman 0.04).
- **The baselines are the closest thing to a signal**: IBIT's Friday return
  sign is *negatively* related to ETH's weekend (Spearman -0.33, p=0.011; 36%
  hit rate), i.e. a weekend reversal of Friday's ETF move. Raw p only; it would
  not survive the grid correction and it is a 60-observation sample.

The unconditional weekend drift in this window was small and positive (BTC
48h median +42 bps, up 63% of weekends), which is the bar any directional
rule has to beat. None did.

## Why this is not the final word

- **CheddarFlow's stored history is thin.** It starts in July 2025 for these
  names, and the median Friday has 12 IBIT prints; only from mid-2026 are there
  50-260 prints a day. ARKB has 4 prints in the whole window, GBTC 25, FBTC 49.
  The lake keeps every new day, so this re-runs for free as the sample grows.
- **60 weekends** with fat-tailed outcomes: a handful of weekends dominate any
  mean, which is why the medians and hit rates are reported alongside.
- **Only Friday's prints were used**, as pre-registered. Intraday timing (the
  last hour), strike/expiry structure, and conditioning on the week's trend
  were not tested, and should be proposed by the researcher loop rather than
  hand-tuned here, per the no-hand-authored-rules discipline.

## This weekend (2026-10-02 to 2026-10-05), for the record

Friday's IBIT flow was the most bullish print of the window: 134 prints,
$16.8M premium, bull share +0.33, CF session call share +0.70, largest print
bullish, the production call = bullish. The history above says that tells us
nothing about Sunday; BTC is +66 bps since Friday 16:00 ET as of Saturday
17:00 UTC. The script scores this weekend automatically on the next run.

## Implication for the product

The `btc_weekend` family's Gate 1 (direction from Friday's IBIT flow) has no
support in its own history at the 48h horizon. Its calibration endpoint is
the right place to confirm this on live journaled markets before any sizing
is raised; this study says do not raise it.
