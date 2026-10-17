# Results: intraday management of the mid-day 0DTE credit spread

Run 2026-08-27 against the preregistration frozen the same day
(`PREREGISTRATION.md`, sha256
`4466cc0f707e70845a2c5cc46e5995aa2c89eae3efe0dbb138c52bebe04f9b3d`).
Evaluator `research.validators.bars_universe`, unmodified, one subprocess per
cell per split per cost level. Staging `research/experiments/prepare_odte_path.py`,
sweep `research/experiments/sweep_odte_intervention.py`, ledger
`.research/sweeps/odte-intervention-v1/`, snapshots `.research/data/odte/path/<name>/`
(12 manifests, entry 12:00 ET, exit 15:00 ET settle, actual traded option bars).

## Verdict

**No management rule is SUPPORTED anywhere. Zero of 120 rule-vs-baseline
comparisons.** Intervention — stop at 2× credit, stop at 1× credit, exit on
strike touch, iron-condor conversion, cut-the-short-and-ride — fails to beat
mechanical hold-to-expiry on the same tape, at fixed conventions declared
before any payoff was read.

And the anchor produced the mission's real finding: **M0 itself, the
unmanaged near-money spread entered at 12:00 ET, clears the full
eight-condition bar on three tapes — SPY put, QQQ put, QQQ call** — the bar
the 10:00-ET family never cleared in five prior campaigns. That result is
reported with its caveats below, not promoted from this file.

## The home tapes (0.5% entry cost, net bps of width per session)

SPY put (643 sessions, 2024-01-19 → 2026-08-20):

| rule | disc net | val net | disc rawLB | val rawLB | verdict |
|---|---:|---:|---:|---:|---|
| **M0 hold** | **+245.8** | **+255.8** | **+0.0047** | **+0.0078** | clears full bar |
| M1 stop-200 | +273.3 | +180.4 | +0.0083 | −0.0017 | NOT SUPPORTED |
| M2 stop-100 | +208.1 | +133.5 | +0.0016 | −0.0011 | NOT SUPPORTED |
| M3 strike touch | +85.2 | +73.3 | −0.0058 | −0.0068 | NOT SUPPORTED |
| M4 condor | +273.0 | +188.5 | +0.0084 | −0.0016 | NOT SUPPORTED |
| M5 cut short, ride long | +363.3 | +126.7 | +0.0135 | −0.0108 | NOT SUPPORTED |

QQQ put (644 sessions):

| rule | disc net | val net | disc rawLB | val rawLB | verdict |
|---|---:|---:|---:|---:|---|
| **M0 hold** | **+342.0** | **+299.7** | **+0.0107** | **+0.0120** | clears full bar |
| M1 | +327.4 | +230.4 | +0.0102 | +0.0017 | NOT SUPPORTED |
| M2 | +336.9 | +168.2 | +0.0133 | −0.0001 | NOT SUPPORTED |
| M3 | +215.0 | +292.6 | +0.0043 | +0.0155 | NOT SUPPORTED |
| M4 | +327.7 | +229.7 | +0.0101 | +0.0015 | NOT SUPPORTED |
| M5 | +402.9 | +159.8 | +0.0140 | −0.0082 | NOT SUPPORTED |

QQQ call M0 also clears (+440.3 / +315.1, rawLB +0.0130 / +0.0020). Every
managed rule on every tape fails at least one condition; the failure that
matters is condition 4 — several managed cells pass the validator with
positive LBs (QQQ put M1/M2/M4, SPY put M1) and **none beats doing nothing
out of sample**.

## What the rules actually did — four mechanisms, all predicted, now measured

**1. Stops truncate the past tail and tax the future one.** M1 on SPY put
posts the best discovery rawLB of any cell on the tape (+0.0083 vs M0's
+0.0047) and a *negative* validation rawLB (−0.0017 vs +0.0078). Discovery
(2024-01 → 2025-08) contains the shock weeks stops are designed for;
validation does not. The stop's benefit is real and it is concentrated
exactly where the disasters already happened. Out of sample it crystallizes
losses at the local extreme and pays the crossing for the privilege.

**2. The condor conversion is a stop wearing a hedge.** M4 tracks M1 to
within noise on every tape (SPY put: −0.3 disc bps, +8.1 val bps of
difference). The opposite-side spread, sold at its 12:00 strikes when the
market has moved against the first side, is too far from the money to
recover meaningful credit. The preregistration's conservative-approximation
caveat stands (a live conversion picks nearer strikes), but the measured
recovery is so small that the burden of proof is now on the conversion
idea, not on the approximation.

**3. The runner leg is convexity bought with realized losses.** M5 has the
widest split-to-split variance of any rule: discovery-best on both index
put tapes (+363 / +403) and validation-poor (+127 / +160, negative rawLBs).
The retained far-OTM long leg pays in the shock-heavy window and bleeds in
the calm one. It is a long-volatility overlay financed by stop-outs, and
out of sample the financing cost exceeds the payout.

**4. Exiting at the strike is exiting at maximum threat.** M3 is the worst
rule on almost every tape (SPY put: +85/+73 vs M0's +246/+256). When the
underlying sits at the short strike the spread is at its worst mark and the
session's mean-reversion — the thing the premium pays for — has not yet
happened. Systematically selling that moment is systematically selling the
bottom of the intraday distribution.

## The rest of the universe

- **IWM**: validator rejects every cell, every rule, both sides, on the
  validation split — the family's known IWM boundary holds under management
  too. Management does not rescue a tape the premium never reached.
- **Single names**: AMZN, AVGO, GOOGL-put, IBIT and NVDA are **UNTESTABLE**
  (validation splits of 10–34 sessions against the frozen 35 floor — too
  few trades is our ignorance, not the market's answer). The testable ones
  (AAPL, GOOGL-call, META, MSFT, TSLA) are all NOT SUPPORTED, with wild
  thin-sample validation swings in both directions (META call M0 +1043.7
  val bps; TSLA put M5 −1171.1). Nothing here changes the both-sides
  verification's conclusion: side asymmetry is name-specific and no
  unconditional single-name edge exists on this window.
- Multiplicity check: 120 comparisons, ~6 chance clears expected under
  independence; observed zero. The managed rules did not reach chance level
  because condition 4 (beat M0 on the same tape) is where they die.

## The anchor finding, stated precisely and with its limits

At 12:00 ET, pct-defined near-money (0.26% OTM, width 0.64% of spot), held
to settle, at 0.5%-of-width entry cost:

- **SPY put**: validator passed both splits; rawLB +0.0047 / +0.0078;
  residLB +0.0046 / +0.0102; drift share 18.9% disc.
- **QQQ put**: passed both; rawLB +0.0107 / +0.0120; residLB +0.0119 /
  +0.0136; **still clears every condition at 1.0% cost** (rawLB +0.0057 /
  +0.0070) — the first cell in the family to do that.
- **QQQ call**: passed both; rawLB +0.0130 / +0.0020; at 1.0% cost the
  validation rawLB goes negative (−0.0030).
- SPY put at 1.0% cost: discovery rawLB −0.0003 — the family's signature
  within-a-bp-of-zero margin, again.

The 10:00-ET campaigns failed exactly one condition, the discovery weekly
bootstrap, at every geometry and on both index tapes. At 12:00 ET that
condition clears on both index tapes, in both splits, drift-controlled. The
honest reading is NOT "the edge moved to noon": these calendar windows have
now been read twice (10:00 by `odte-both-sides`, 12:00 here), 24 M0 anchors
were evaluated on this snapshot, and the three clears are correlated tapes
(QQQ put and QQQ call share an underlying and every session). It IS a real
difference in the same direction on the family's two home names, it was
produced by a frozen contract that made no noon-specific promise, and the
entry-hours surface independently flagged 12:00 as the best out-of-sample
cell. That is enough to earn the question, not enough to answer it.

What stands between this and a decision, in the family's established order:
(1) entry-cost capture at 12:00 ET — the existing 10:00 capture program
does not cover this hour, and SPY put's margin at 1.0% is one basis point;
(2) a named, preregistered single-cell confirmation with the holdout spend
as the declared stake. Neither is done from this file.

## What was NOT spent

Every holdout stays sealed. Nothing here required reading one, and the
granularity caveat (hourly marks) independently bars spending one on a
managed-rule claim. The M0 anchor is settle-priced and unaffected by that
caveat, but its holdout is a one-shot resource and the ordering above is
the declared precondition for using it.

## Deviations from the preregistration, recorded

Two, both made at staging time, both conservative, both flagged in
`prepare_odte_path.py`'s docstring:

1. The 12:00 ET entry mark must be the actual hourly print (no look-back
   leniency), because the sweep's carry-forward contract requires the entry
   mark to exist. Legs without one are skipped as `no_option_bars` —
   11–12 sessions per index ETF over 2.5 years.
2. `no_option_bars`/`degenerate` skip counts are side-level (sides are
   staged independently); the unit is recorded in each manifest as
   `skipped_unit`.

## Limitations

Hourly trigger granularity (declared in the preregistration: this test can
refute a managed rule; a survivor would have needed minute-bar restaging —
no survivor emerged, so no restaging is owed). One geometry, one entry
hour, fixed trigger parameters. Costs assumed, not measured — and managed
exits cross the spread under stress in the day's worst liquidity, so true
intervention costs are if anything higher than modeled, which only widens
the margin by which management lost. Single-name samples are thin by
calendar, not by exclusion. These windows were read once before at 10:00;
the multiplicity record is in the preregistration.

A refuted rule does not mean no management can work. It means these five
rules, at these conventions, on twelve names and 2.5 years of actual traded
option bars, could not beat doing nothing — and that the version of this
trade worth studying further is the one with no intervention in it at all.

Backtests are hypothetical, research-only evidence and are not investment
advice.
