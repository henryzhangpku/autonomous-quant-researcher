# Results: does the maker side of Kalshi BTC binaries survive adverse selection?

Mission completed 2026-08-22. **Hypothesis generation, not a preregistered
test:** KXBTCD and KXBTC15M outcomes were already read against price by the
prior missions (kalshi-btc-hourly, 141 hypotheses; kalshi-btc-15m, 88
hypotheses), so every BTC number below is in-sample by construction.
*Hypothetical backtests, not investment advice.*

## Verdict up front

**No. The maker side does not survive adverse selection, under either fill
model, in any tercile, in any cell.** The maker premise was arithmetically
right — if fills were random, resting at the touch would have earned +1 to
+2 cents in the sign-stable hourly buckets — but conditioning on *being
filled* costs 2–5 cents on the hourly ladders and 8–10 cents on the
15-minute markets, more than the spread-plus-fee toll the maker saves.
**A live maker probe is not justified. KXETHD remains sealed** (the
pre-stated condition for spending it — a cell positive under both fill
models in all terciles — was met by zero of 28 evaluated cells).

## The question

Prior missions established that the taker side is dead: a real 3–8c
favourite/longshot mispricing exists, but crossing the spread plus the
`0.07·P·(1−P)` taker fee costs 3–5c and eats it. Kalshi charges **0% maker
fee** on standard markets, so a resting order earns the half-spread instead
of paying it. The open objection: a resting order fills preferentially when
the market is moving through it. Candle data cannot show exact fills, but it
can **bound** the effect between an optimistic and a pessimistic fill model.
This mission computes both bounds.

## Data

- `research/data/kalshi_hourly.jsonl.gz` (KXBTCD rows), gz sha256
  `ae682ab36dd3…` matching `kalshi_hourly_manifest.json`.
- `research/data/kalshi_15m.jsonl.gz` (KXBTC15M rows), uncompressed-jsonl
  sha256 `017647db80f0…` matching `kalshi_15m_manifest.json`. (Note the two
  manifests hash different representations — gz vs uncompressed — verified
  both.)

Per-minute `[ts, yes_bid, yes_ask, volume]` candles over each market's final
window (~65 min hourly, ~15–20 min for 15m), plus settlement.

## Simulation (exact definitions)

Scripts host-local in `tmp/maker/` (maker_sim.py, maker_sim2.py; outputs
results_run1.txt, results_run2_fixed.txt).

- **Entry/quote candle:** the first candle with a two-sided quote
  (bid > 0 and ask < 1). Qualify on entry mid ∈ [0.05, 0.95]; for hourly
  additionally window volume > 0 (the frozen round-1 filter, kept for bucket
  comparability; its look-ahead caveat from round 2 carries over). This
  yields 6,357 qualifying KXBTCD markets (vs round 1's 7,278 — the
  two-sided-quote requirement is stricter than "first staged candle") and
  4,249 KXBTC15M markets.
- **Resting orders**, placed at the entry candle, resting until close (GTC,
  never cancelled), fill price = resting price, held to settlement, 0% fee:
  - BUY YES rests at `p = yes_bid(entry)`.
  - BUY NO rests at `q = 1 − yes_ask(entry)` (yes-space level
    `a = yes_ask(entry)`).
- **OPTIMISTIC ("touch") fill:** some strictly-later candle has
  (buy YES) `yes_bid ≤ p`, or (buy NO) `yes_ask ≥ a`, **and** that candle's
  volume > 0. Any trading while the market is at or beyond the level is
  assumed to reach our order.
- **PESSIMISTIC ("run over") fill:** some strictly-later candle's mid
  `(bid+ask)/2` is **strictly through the level by ≥ 1 tick** —
  (buy YES) `mid ≤ p − 0.01`, (buy NO) `mid ≥ a + 0.01` — with empty-book
  candles (bid ≤ 0 and ask ≥ 1) skipped and cumulative volume since entry
  > 0. You are filled only when the market traded through you and kept
  going; the truth lies between the two models.
- Net per filled contract = `1{side wins} − fill price`. Terciles are the
  same 60/20/20 chronological splits by close_time as the prior missions,
  taken on each series' qualifying universe.

## Primary cells: the two sign-stable hourly buckets

`n` = qualifying markets, `fill%` = filled fraction (unfillable = 100 −
fill%), `px` = mean resting price = implied win rate of fills, `real(f)` =
realised win rate of filled contracts, `net` = mean net per filled contract
(= real(f) − px), `real(all)` = realised win rate of all qualifying markets
in the cell (the random-fill counterfactual settles at this rate).

### KXBTCD, entry mid [0.2, 0.3), BUY NO resting at 1−ask

| model | split | n | fill% | px | real(f) | net/fill | t | real(all) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| OPT | t0 | 347 | 86.2% | 0.749 | 0.726 | −0.0229 | −0.89 | 0.764 |
| OPT | t1 | 106 | 84.0% | 0.742 | 0.742 | −0.0006 | −0.01 | 0.783 |
| OPT | t2 | 106 | 91.5% | 0.744 | 0.732 | −0.0123 | −0.27 | 0.755 |
| OPT | all | 559 | 86.8% | 0.747 | 0.730 | −0.0167 | −0.83 | 0.766 |
| PESS | t0 | 347 | 80.1% | 0.748 | 0.705 | −0.0433 | −1.59 | 0.764 |
| PESS | t1 | 106 | 79.2% | 0.740 | 0.726 | −0.0143 | −0.29 | 0.783 |
| PESS | t2 | 106 | 87.7% | 0.745 | 0.720 | −0.0243 | −0.52 | 0.755 |
| PESS | all | 559 | 81.4% | 0.746 | 0.712 | −0.0340 | −1.61 | 0.766 |

### KXBTCD, entry mid [0.8, 0.9), BUY YES resting at bid

| model | split | n | fill% | px | real(f) | net/fill | t | real(all) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| OPT | t0 | 516 | 85.3% | 0.850 | 0.850 | −0.0004 | −0.03 | 0.872 |
| OPT | t1 | 179 | 86.6% | 0.849 | 0.813 | −0.0361 | −1.15 | 0.838 |
| OPT | t2 | 186 | 90.3% | 0.849 | 0.833 | −0.0154 | −0.54 | 0.849 |
| OPT | all | 881 | 86.6% | 0.850 | 0.839 | −0.0110 | −0.82 | 0.860 |
| PESS | t0 | 516 | 72.7% | 0.850 | 0.824 | −0.0255 | −1.30 | 0.872 |
| PESS | t1 | 179 | 75.4% | 0.849 | 0.785 | −0.0641 | −1.81 | 0.838 |
| PESS | t2 | 186 | 79.6% | 0.848 | 0.811 | −0.0376 | −1.18 | 0.849 |
| PESS | all | 881 | 74.7% | 0.849 | 0.813 | −0.0362 | −2.38 | 0.860 |

### The decomposition that decides it

Per pooled cell: **premise** = real(all) − px (edge if fills were random);
**adverse selection** = real(f) − real(all) (what conditioning on a fill
costs); their sum is the realised net.

| cell | premise | AS (OPT) | AS (PESS) | net OPT | net PESS |
|---|---:|---:|---:|---:|---:|
| hourly 0.2–0.3 NO | **+0.020** | −0.036 | −0.054 | −0.0167 | −0.0340 |
| hourly 0.8–0.9 YES | **+0.011** | −0.021 | −0.047 | −0.0110 | −0.0362 |
| hourly all YES | +0.008 | −0.017 | −0.034 | −0.0172 | −0.0340 |
| hourly all NO | +0.005 | −0.022 | −0.039 | −0.0216 | −0.0387 |
| 15m all YES | +0.009 | −0.089 | −0.109 | −0.0805 | −0.0996 |
| 15m all NO | +0.001 | −0.083 | −0.096 | −0.0819 | −0.0984 |

The maker tailwind is real (+0.5 to +2.0c). Adverse selection is 2–5× its
size on the hourly ladders and ~10× on the 15-minute markets. Filled
contracts settle your way **2.1–5.4 points less often than the unconditional
rate** (hourly) and **8–11 points less often** (15m), i.e. the fills you
get are precisely the markets that were moving against you.

## Baselines and 15m detail

Hourly all-qualifying (n = 6,357): BUY YES nets −0.005/−0.045/−0.026 (OPT
terciles) and −0.019/−0.070/−0.043 (PESS); BUY NO −0.029/−0.008/−0.012
(OPT) and −0.047/−0.029/−0.024 (PESS). Every tercile negative.

KXBTC15M all-qualifying (n = 4,249, median spread 1c): fills 80–86% (OPT),
79–83% (PESS); nets **−0.077 to −0.091 (OPT)** and **−0.096 to −0.109
(PESS)** in every tercile, both sides, |t| up to 12. The 15-minute book is
tight and fast: the entry quote is nearly fair (real(all) ≈ 0.50), so a
resting order is pure adverse-selection exposure with no bias tailwind. The
mirror buckets (mid 0.2–0.3 NO: n = 204; mid 0.8–0.9 YES: n = 33) are also
negative pooled under both models; n too small to weight.

Unfillable fractions are small everywhere — 8–25% (hourly) and 14–28% (15m)
depending on model and cell — so this is not a "no fills" story; it is a
"the fills are toxic" story.

## Sensitivity: cancel-if-unfilled (rest window limited)

Because a GTC-to-close rest is the worst-case policy, we re-ran with the
order live only K candles after entry (K = 10 for hourly, K = 5 for 15m),
fills counted only within the window, positions held to settlement. Result:
**equal or worse everywhere** (e.g. hourly 0.2–0.3 NO pooled: OPT −0.022,
PESS −0.039 vs −0.017/−0.034 GTC). Median time-to-fill is 1–2 minutes:
fills happen immediately and are immediately toxic; the late fills a GTC
rest adds are the more benign ones. Shortening the rest window does not
rescue the trade.

## Errors caught and kept

1. **v2 GTC sentinel bug.** maker_sim2.py encoded "never filled" as index
   10⁹ and the GTC rest limit as K = 10⁹, so `unfilled ≤ K` counted every
   market as filled at its resting price — producing a fantasy row with
   100% fills and positive nets (+0.020 in the 0.2–0.3 NO cell). Caught by
   cross-checking against maker_sim.py's independent boolean-flag
   implementation, which it contradicted. Fixed; the buggy row is precisely
   the "premise" counterfactual in the decomposition table and is reported
   there as such, never as a fill model.
2. **Manifest hash convention mismatch** (gz vs uncompressed jsonl) between
   the two data manifests — reconciled above rather than assumed.

## Multiple comparisons

28 cells were evaluated: 8 universe cells (4 hourly, 4 15m) × 2 fill
models = 16, plus the rest-window sensitivity on 6 of them × 2 models = 12.
Zero were positive pooled; zero were positive in even a single tercile
under the pessimistic model; the best single number anywhere was −0.0006
(hourly 0.2–0.3 NO, OPT, t1). With everything negative, the denominator
only strengthens the null verdict.

## Limitations (honest)

- **Candles are not a book.** Per-minute bid/ask closes cannot show
  intraminute touches, partial fills, or the trade tape; both fill models
  are proxies and the truth lies between them — but here both bounds are
  negative, which is what makes the verdict decidable.
- **Queue position is unmodelled.** A real resting order joins behind
  existing size and competes with other makers; it fills *last* at a level
  — exactly when the level is being run through — so real adverse selection
  is plausibly worse than even the pessimistic model, and real fill rates
  lower than either.
- No re-quoting, no inventory management, one contract per market; a
  sophisticated market-making policy (lean quotes off a fair-value model,
  cancel on toxicity signals) is a different animal this data cannot
  evaluate. What is refuted here is the specific, simple thesis "rest at
  the touch inside the mispriced buckets and let the 0% maker fee flip the
  sign".
- 45-day single crypto regime; all BTC outcomes previously read
  (hypothesis generation); universe definition differs slightly from
  round 1 (two-sided-quote entry).

## Disposition of the sealed sets

- **KXETHD (hourly ETH): still unread.** The spend condition (a cell live
  under both fill models in all terciles) was not met.
- **KXETH15M: already spent** by the 15m mission's replication read; it was
  not touched here and could not have served as fresh out-of-sample.

*Hypothetical backtests, not investment advice.*
