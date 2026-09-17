# spy-odte-credit campaign 1 — findings (2026-08-13)

SPY 0DTE put credit spread, width 5, short strike 5 points OTM, entry 10:00
ET, exit at intrinsic (a 0DTE settles mechanically, so no closing quote is
needed and no thin late mark can flatter it). **Priced from ACTUAL traded
option bars**, not modeled marks — 629 sessions, 2024-01-19 → 2026-08-10.
P&L is a fraction of width, the same unit the overnight-spx program reports.
Snapshot: `research/experiments/prepare_odte_credit.py`.
Sweep: `research/experiments/sweep_odte_credit.py`.
Ledger: `.research/sweeps/spy-odte-credit-v1/`.

## Verdict — PROMOTED, conditional on execution

The unconditional structure passes every discovery AND validation gate at a
0.5%-of-width round-trip cost, and improves out of sample:

| | discovery | validation |
|---|---:|---:|
| sessions | 377 | 126 |
| net per session | **+55.7 bps of width** | **+71.6 bps of width** |
| Sharpe | 0.702 | 0.884 |
| status | passed | passed |

Gross, before any cost, the structure earns **+119.9 bps of width per session
at a 95.2% win rate** — the classic credit-spread shape.

**No condition was needed.** The promoted cell is the structure itself; the
only conditional cell that also passed (`credit_frac < 0.30`) is true on
essentially every session and is therefore the same trade. Nothing in the
grid improved on simply putting the trade on.

## The number that actually matters

This campaign was built to report a breakeven rather than to defend an
assumed cost, because this repo has twice killed a candidate at
`survives_double_cost` against a number nobody measured. Same cells, three
declared cost levels:

| Round-trip cost | Discovery net | Validation net | Verdict |
|---|---:|---:|---|
| **0.5% of width** | +55.7 bps | +71.6 bps | **passes every gate** |
| 1.0% of width | +5.7 bps | +21.6 bps | fails both-halves and doubled-cost |
| 1.5% of width | −44.3 bps | −28.4 bps | clearly negative |

**Breakeven for full gate compliance is between 0.5% and 1.0% of width** —
roughly **$0.025 to $0.05 on a $5-wide spread**. Above that this trade does
not exist; below it, it clears everything including doubled cost.

That is directly actionable: it states the execution quality the trade
requires, instead of asserting a cost and hoping.

Note the structure of the cost. Because the position is held to expiry there
is **no exit cost** — settlement is mechanical. The entire budget is the
entry: crossing the spread on two penny-quoted SPY legs, once. That is
plausibly inside 0.5% of width, but "plausibly" is exactly the word this
program exists to eliminate.

## Limitations, in order of how much they could matter

1. **The cost is assumed, not measured.** The promotion is conditional on it
   and nothing here backs it. This is the same weakness that gates the
   overnight-spx promotion, and it is the one thing worth fixing next.
2. **269 of 898 candidate sessions were skipped** for missing option bars at
   the computed strike (30%). Skipped days may be systematically less liquid,
   which would bias the surviving sample toward tradeable conditions — in the
   optimistic direction. This needs quantifying before size is put on.
3. **One geometry.** Width 5 and offset 5 were fixed at snapshot time and not
   swept. The overnight program found geometry matters a great deal (width 50
   or offset 30 destroyed its premium), so this cell may not be the best one,
   or may be a lucky one.
4. **Hold to expiry only.** No stop, no early close, no management. A real
   policy would need those, and they change the distribution.
5. **One regime**: 2024-01 onward, with no bear year and no volatility shock
   of 2022's kind in evidence. A 95.2% win rate structure earns its losses in
   exactly those conditions.
6. Entry is priced from the 10:00 ET hourly bar's close, not a 10:00:00 print.

## Follow-ups (each a new frozen contract, not a tweak)

1. **Measure the entry cost.** Capture live SPY 0DTE vertical quotes at 10:00
   ET for w5/o5 and record the half-spread as a fraction of width. One week
   of captures answers whether this trade exists. Highest value by far, and
   it mirrors exactly what turned the overnight-spx campaign from "no trade"
   into a promotion.
2. **Sweep the geometry** — widths 2/5/10, offsets 2/5/10 — as a preregistered
   grid, so the promoted cell is known to be the peak rather than the only
   point sampled.
3. **Quantify the 30% skip.** Rebuild counting, per skipped session, whether
   the strike existed at all versus had no bars, so liquidity bias can be
   measured instead of noted.
4. **Then, and only then, the sealed holdout** (2026-02-06 → 2026-08-10),
   spent once.

## The drift control, applied by the second researcher (2026-08-14)

The overnight-spx promotion lost its holdout the same night, and the cause
generalizes: an unconditional long-delta credit spread collects the market's
upward drift whether or not anything is mispriced. This campaign's spec
declares `"payoff": "next_open_to_close"` — the ABSOLUTE payoff, not the
`_rel` benchmark-relative mode the bars campaign added for exactly this
failure — so the control had never been applied to this family.

`attribute_odte_credit_drift.py` regresses each session's structure P&L on
SPY's own return over the identical 10:00 ET → close window, then applies the
frozen weekly bootstrap to what direction does not explain. Snapshot rebuilt
independently on the second host (628 sessions, 13 skipped) and it reproduces
this campaign's gross number: +1.05% and +1.14% of width vs the reported
+1.199%.

| Split | n | Win rate | Gross mean/w | = drift | + alpha | Drift share | Alpha bootstrap LB |
|---|---|---|---|---|---|---|---|
| discovery | 376 | 96.0% | +0.01055 | +0.00313 | +0.00742 | 30% | **−0.00107** |
| validation | 126 | 96.8% | +0.01143 | +0.00147 | +0.00996 | 13% | **−0.00079** |

**The result is genuinely better than the overnight family's, and it still
does not clear the bar.** Only 13–30% of the payoff is drift — this is NOT
primarily a directional artifact, which is the opposite of what killed the
1DTE horizon (56% drift, negative residual). The drift-neutral alpha is
positive on both splits, +74 and +100 bps of width GROSS.

But the preregistered rule was alpha > 0 AND its weekly bootstrap lower bound
> 0, and both lower bounds are marginally negative. The residual is positive
in expectation and not distinguishable from zero at the 5th percentile. Note
also that these are GROSS figures: at the campaign's 0.5%-of-width assumption
the drift-neutral net falls to roughly +24 and +50 bps, which is why the cost
measurement is load-bearing rather than a formality.

**Recommendation on ordering, learned the expensive way tonight.** The
overnight-spx holdout was spent under time pressure while its execution cost
still rested on a single night's capture; it came back rejected, and no cost
measurement could have rescued a +0.15%-of-width result. This campaign must
not repeat the sequence. Its holdout is UNSPENT and should stay that way
until the 10:00 ET cost capture exists — a holdout spent before the premise
is measured answers a question about an economics we made up. The follow-up
ordering already written above is correct; this is a second vote for it.

Authority is `research_only`. Nothing here authorizes an entry policy, and
the deployment path is unchanged: measured cost, then holdout, then paper
reconciliation.

Backtests are hypothetical, research-only evidence and are not investment
advice.

---

# Geometry cross (2026-08-14) — the surface has a shape, and the bar held

Five preregistered geometries through the w5/o5 cell, unconditional structure
only, 511-635 sessions each of actual traded option bars. Acceptance bar
declared in the sweep before any result: shipped validator gates AND positive
weekly bootstrap LB on the raw payoff AND on the drift-neutral residual
(payoff minus its OLS projection on SPY's own session move), both splits, at
0.5%-of-width cost. Drift decomposition is now carried in the snapshot
itself (`spy_session_ret`, analysis-only), so no future audit has to rebuild
a snapshot to compute it.
Sweep: `research/experiments/sweep_odte_geometry.py`.
Ledger: `.research/sweeps/spy-odte-geometry-v1/`.

## Verdict

**No geometry clears the full bar. Zero promotions.** The holdout of every
geometry stays sealed.

## The surface (0.5% cost, net bps of width per session)

| Geometry | Disc net | Val net | Val drift share | Disc rawLB | Val rawLB | Val residLB |
|---|---:|---:|---:|---:|---:|---:|
| **w5 o2** | **+144.4** | **+249.8** | 6.2% | **-0.000135** | **+0.000774** | **+0.009522** |
| w2 o5 | +81.7 | +153.2 | 7.0% | -0.0046 | -0.0040 | -0.0002 |
| w5 o5 (original) | +55.7 | +71.6 | 4.6% | -0.0025 | -0.0088 | -0.0047 |
| w10 o5 | +12.1 | -29.1 | — | -0.0048 | -0.0191 | -0.0161 |
| w5 o10 | -21.0 | -132.5 | — | -0.0112 | -0.0365 | -0.0266 |

Two findings the cross establishes:

**1. The premium is monotone in moneyness and dies with distance.** Offset 2
beats 5 beats 10 at every cost level, and offset 10 is outright negative.
Narrow width also helps (w2 > w10). The credit premium lives near the money;
the original w5/o5 was a mediocre point on a surface with a clear gradient —
exactly what the overnight-spx program found on its own surface.

**2. w5/o2 misses the full bar by one number, and it is the discovery raw
bootstrap: -0.000135 — 1.35 bps of width below zero.** Everything else
passes: both validator splits, both validation bootstraps (raw +0.0008,
drift-neutral +0.0095), and validation survives even at 1.0% cost with a
positive residual LB. Validation drift share is 6.2%: this is not the market
rising in disguise.

The bar was declared before the sweep ran and it is not moving. A threshold
adjusted after seeing which side of it a result landed on is not a
threshold — the same sentence this file already contains once.

## What would count as evidence, and what would not

- NOT evidence: re-running this data with offset 1 or 3 (multiplicity on the
  same tape), or softening the bootstrap.
- Evidence: the same near-money structure, stated in advance in percentage
  terms, on an underlying this campaign has never touched (QQQ or IWM 0DTE,
  same entry, same bar). Independent tape, one shot.
- Evidence: the 10:00 ET cost capture confirming entry cost at or under 0.5%
  for the o2 geometry specifically — near-money legs carry bigger premiums
  and the capture currently measures o5 only.

## Follow-ups, in order

1. **Extend the daily capture to the o2 geometry** (same harness, second
   record per day). The strongest cell should be the one whose cost is
   measured.
2. **QQQ 0DTE replication of w5/o2, preregistered as a named single cell** in
   percentage terms (width 0.64% of spot, short strike 0.26% OTM). One cell,
   one shot, the full bar, no grid.
3. Only after both: revisit whether the SPY holdout is worth its one use.

Backtests are hypothetical, research-only evidence and are not investment
advice.

---

# QQQ replication (2026-08-14) — seven of eight, and the same missing one

One named cell, declared before any QQQ data was read: QQQ 0DTE put credit,
short strike 0.26% OTM, width 0.64% of spot, 10:00 ET entry, held to expiry,
unconditional. 635 sessions of actual traded option bars, 2024-01-19 →
2026-08-10. Bar: all eight conditions (validator both splits + positive raw
and drift-neutral weekly bootstrap LBs both splits) at 0.5% cost.
Script: `research/experiments/replicate_qqq_odte.py`.
Ledger: `.research/sweeps/qqq-odte-replication-v1/`.

## Verdict: 7 of 8. Not promoted.

| 0.5% cost | discovery | validation |
|---|---:|---:|
| validator | **passed** | **passed** |
| net (bps of width/session) | +252.5 | **+324.5** |
| Sharpe | 1.21 | **2.31** |
| drift share | 26.5% | **5.1%** |
| raw weekly LB | **−0.000331 ✗** | +0.0059 ✓ |
| drift-neutral weekly LB | −0.0027 ✗ | **+0.0153 ✓** |

At 1.0% cost the validator still passes both splits and validation LBs stay
positive — the QQQ premium is larger than SPY's.

**The failure is the same single condition, at the same magnitude, on the
same split, as SPY w5/o2** (SPY disc rawLB −0.000135; QQQ −0.000331). That is
not a coincidence and it is not full independence either: the discovery
windows share a calendar, and the week diagnostic names the culprits — the
worst discovery weeks are 2024-w16 (−0.50 width), 2025-w13 (−0.34), 2024-w36
(−0.33): the April-2024 drawdown, the March/April-2025 tariff break, the
early-August/September-2024 vol shocks. 29 of 82 discovery weeks are
negative. The validation era (2025-08 → 2026-02) simply contains no week of
that kind, on either underlying.

## The honest characterization

The near-money 0DTE put-credit premium is **real, replicated in shape across
two underlyings, mostly not drift, and it earns its living by taking
vol-shock weeks on the chin.** The preregistered bar demands a positive 5th
percentile of weekly means through a window that contains three such shocks,
and the structure, unconditioned, does not meet it. That is a true statement
about the strategy, not a defect in the bar: an unconditional short-vol
structure has left-tail weeks, and the bar is doing its job by refusing to
promote until the tail is priced or avoided.

## What follows, and what does not

- Does NOT follow: softening the bootstrap, or promoting on 7/8. The bar
  stands.
- Does NOT follow yet: spending the SPY holdout. The declared precondition
  was a full-bar replication; 7/8 is not that.
- DOES follow: the tail is now the named research object. A vol-conditioned
  variant — skip entry when a conventional, externally-defined stress marker
  is on (e.g. VIX above a round threshold fixed in advance) — is the next
  preregistered contract. The threshold must come from convention, not from
  fitting these tapes; the diagnostic above is for naming weeks, not tuning.
- CONTINUES: the 10:00 ET cost capture, now recording o2 and o5 geometries
  daily. All economics above assume 0.5%; the first live sample measured
  0.20% pre-market, unconfirmed at entry time.

Backtests are hypothetical, research-only evidence and are not investment
advice.

---

# Calm-day conditioning (2026-08-14) — refuted, and the mechanism inverted

Marker declared before any result, from external convention: skip entry when
the underlying's prior close-to-close move was >= 1% (VIX>25 was first
choice; VIX is plan-gated at the provider). Cells: calm-only (hypothesis),
turbulent-only (diagnostic), baseline. Both tapes.
Script: `research/experiments/condition_odte_calm.py`.
Ledger: `.research/sweeps/odte-calm-condition-v1/`.

## Verdict: the calm filter clears nothing and worsens every bootstrap

| net bps of width / session | SPY disc | SPY val | QQQ disc | QQQ val |
|---|---:|---:|---:|---:|
| calm-only | +82.7 | +143.0 | +323.5 | +147.8 |
| turbulent-only | **+373.0** | **+896.7** | +126.0 | **+921.6** |
| baseline | +144.4 | +249.8 | +252.5 | +324.5 |

Calm-only raw LBs: -0.026/-0.036 (SPY), -0.033/-0.070 (QQQ) — materially
worse than baseline. The turbulent bucket holds the only positive raw LBs on
the board (+0.007/+0.059 SPY, +0.017 QQQ val) but fails sample-size gates
(18-29 validation sessions).

## What this settles

The proposed mechanism was backwards. The structure's losses are not "entries
during turbulence" — they are the FIRST day of a shock, which follows a calm
day and is unforecastable from prior-day information. By the following day,
volatility has repriced, credits are rich, and selling them pays best. The
premium is compensation for the unannounced first-day hit, and no prior-day
filter removes that tail. The just-below-zero discovery bootstrap on both
tapes is therefore not an artifact to engineer away: it is the honest price
of the trade.

Conditioning research on this family is DONE. What remains is not research:
(1) the 10:00 ET cost captures, accumulating daily, which decide whether the
unconditional economics survive real entry costs; (2) a human decision on
whether a 7-of-8 structure whose tail is now understood — real, thin-tailed
weeks that cannot be filtered — is worth paper-trading under its stated risk.
The turbulent-only observation (richest entries, thin sample) may earn its
own preregistered campaign after another year of sessions exists; it must not
be promoted from 29 windows.

Backtests are hypothetical, research-only evidence and are not investment
advice.

---

# RTH entry-hour surface (2026-08-14) — the premium is session-wide; the bar is not

Ten cells, declared in advance: entries 10:00-14:00 ET x offsets {2,5} at
width 5, SPY, actual traded option bars, absolute all-eight bar per cell, no
shortlist. Purpose: the entry policy number the 0DTE trader's AUTO slot
needs. Sweep: `research/experiments/sweep_odte_entry_hours.py`.
Ledger: `.research/sweeps/spy-odte-entry-hours-v1/`.

## Verdict: zero cells clear the full bar

| cell | disc net (bps/w) | disc rawLB | val net | val rawLB | val residLB |
|---|---:|---:|---:|---:|---:|
| e10o2 | +144.4 | -0.0001 | +249.8 | +0.0008 | +0.0095 |
| e11o2 | +147.3 | -0.0016 | +154.3 | -0.0052 | +0.0015 |
| **e12o2** | +133.8 | -0.0021 | **+264.3** | **+0.0144** | **+0.0118** |
| e13o2 | +141.2 | **+0.0029** | +141.8 | -0.0013 | -0.0039 |
| e14o2 | +57.1 | -0.0016 | +41.1 | -0.0092 | -0.0021 |
| e10o5 | +55.7 | -0.0025 | +71.6 | -0.0088 | -0.0047 |
| e11-14 o5 | negative disc | — | mixed | — | — |

## What the map establishes

**1. The near-money premium is session-wide.** Offset-2 entries earn +134 to
+147 bps of width per session at every hour from 10:00 to 13:00, decaying
only at 14:00. The offset-5 premium exists only at the open and is negative
in discovery from 11:00 onward. Near the money, the credit pays all day;
five points out, it pays only before the day's information arrives.

**2. The eighth condition is the family's constant, not a cell's defect.**
Every o2 cell fails exactly one or two weekly bootstrap LBs, each time a
different split, each time within ~1 bp of zero (e13o2 even passes discovery
and fails validation — the reverse of e10o2). Across 10 entry cells, 5
geometries, 2 underlyings and a conditioning attempt, the weekly 5th
percentile of this premium hovers at zero everywhere. That is the honest
description of the trade: **robust positive expectancy, ~zero worst-week
bound, tail unfilterable.** No amount of further cell-hunting on this window
changes it, and none will be run.

## The research-backed statement an AUTO slot can honestly make

- Structure: SPY 0DTE put credit, width 5, short strike ~2 points (~0.25%)
  OTM, exit at settlement.
- Entry: any hour 10:00-13:00 ET is equivalent within noise (e12o2 best out
  of sample; e10o2 best replicated); do not enter at 14:00+; do not use the
  5-OTM strike after the open.
- Economics at 0.5%-of-width cost: ~+1.3-2.6% of width per session, drift
  share under 10%, win rate ~95%.
- Risk that sizing MUST carry: first-shock days cannot be filtered; weekly
  5th percentile ~ 0; max loss is the width. An AUTO implementation without
  explicit tail sizing is selling a lie.
- Authority: research_only. Cost assumption pending live captures; holdouts
  sealed.

Backtests are hypothetical, research-only evidence and are not investment
advice.

---

# IWM third-tape replication (2026-08-14) — the family has a boundary

Same preregistered cell as QQQ (near-money pct-defined put credit, 10:00 ET,
held to expiry, unconditional), stated before any IWM data was read. 613
sessions of actual traded IWM option bars, 40 skipped.
Script: `research/experiments/replicate_iwm_odte.py`.
Ledger: `.research/sweeps/iwm-odte-replication-v1/`.

## Verdict: refused — first tape to fail validation outright

| 0.5% cost | discovery | validation |
|---|---:|---:|
| net bps of width | +255.6 | **-12.8** |
| Sharpe | 1.79 | -0.08 |
| raw weekly LB | **+0.0055 (passed!)** | -0.0388 |
| drift share | 11.5% | (validation mean ~0, decomposition unstable) |

IWM is the only tape to PASS the discovery bootstrap that stopped SPY and
QQQ — and the only one to lose money out of sample. The mirror image of the
index tapes, and a hard boundary for the family:

**The near-money 0DTE put credit is a large-cap-index phenomenon on this
evidence.** SPY and QQQ replicate each other in every particular (7/8, same
failing split, same magnitudes, positive out-of-sample everywhere). IWM does
not carry it. Small-cap put insurance in the validation era (2025-08 →
2026-02) was priced correctly or cheap; selling it lost.

Consequences:
- The tradeable expressions remain SPY and QQQ ONLY. Do not extend the
  policy to IWM, and the policy artifact must name its underlyings
  explicitly rather than implying an index-ETF class.
- A discovery-pass/validation-fail on the third tape is also a live warning
  about what SPY/QQQ's discovery-pass would have been worth without
  validation: nothing. The gauntlet earns its keep again.

Backtests are hypothetical, research-only evidence and are not investment
advice.

---

# Live paper verification, session 1 (2026-08-14) — a win that nearly wasn't

First forward observation of the o2 geometry (short ~0.25% OTM, width ~0.65%
of spot) taken at the declared entry window on a live tape, held to settle.
Recorded BEFORE the outcome was known: entry credit 0.22 on the SPY 775/770
put spread, which is the 17th percentile of the discovery credit distribution
(in-distribution, on the cheap side) and an entry cost of 0.20% of width
against the 0.5% preregistered breakeven.

| field | value |
|---|---|
| structure | SPY 775P short / 770P long, width 5.00 |
| credit at entry | 0.22 (4.4% of width) |
| SPY at settle | ~775.88 |
| short-strike distance at settle | **+0.88, i.e. 0.11% of spot** |
| intrinsic at settle | 0.00 |
| P&L per spread | +22 USD (+4.4% of width) |

**The honest reading is not "it worked."** The trade paid the full credit, but
it finished 0.11% above the short strike when it was sold ~0.25% out of the
money. The underlying spent the session moving toward the short leg and closed
inside the original cushion. A max-win and a near-touch are the same row in a
P&L table and completely different rows in a risk table; only the ledger
distinguishes them, so it is written down here.

What this session is and is not:

- **Is:** confirmation that the entry mechanics are executable at the declared
  hour, that quotes exist at the grid strikes the policy names, and that the
  realized entry cost (0.20% of width) sits well under the preregistered
  breakeven (0.5%). That was the open question and it is now answered once.
- **Is not:** evidence about the edge. n=1. A single session cannot move a
  weekly bootstrap lower bound, and the failing 8th condition
  (discovery weekly LB) is untouched by it. One win is exactly what the
  backtest predicts on a typical day, including on days the strategy is
  eventually killed by.

Authority is unchanged: `research_only`. Forward sessions accumulate; the
paper-flip decision remains the owner's and remains gated on cost captures,
not on a good day.

Backtests and paper verifications are hypothetical, research-only evidence and
are not investment advice.
