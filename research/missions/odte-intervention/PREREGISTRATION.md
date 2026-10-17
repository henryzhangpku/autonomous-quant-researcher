# Preregistration: does intraday management improve the mid-day 0DTE credit spread?

Frozen 2026-08-27, before the leg-path snapshot carrying hourly option marks
was staged and before any payoff derived from it was read. Nothing in this
file was written after seeing a managed-P&L number.

## The question

Every promoted-or-nearly-promoted cell of the 0DTE credit family is
**hold-to-expiry**: enter, settle mechanically at intrinsic, no stop, no
early close, no adjustment. `spy-odte-credit` limitation 4 names this
explicitly: "A real policy would need those, and they change the
distribution." The family's one un-cleared condition is the weekly bootstrap
lower bound hovering at zero — left-tail shock weeks — and the calm-day
mission showed the tail cannot be filtered from *prior-day* information.

What has never been tested here is the practitioner's standard answer:
**manage the position intraday.** Cut the loser before it reaches full
width, convert it to an iron condor, or cut the short leg and let the long
leg run. This mission tests whether any of those interventions, stated in
advance at fixed parameters, improves on mechanical hold-to-expiry on the
same tape.

The entry-conditioning half of the underlying trading idea (entering only at
trend-level rejections) is **out of scope** here. Entry is unconditional at
12:00 ET. Conditioning needs intraday level features this snapshot does not
carry, and the calm-day mission already burned one conditioning hypothesis
on this family; if management cannot beat hold-to-expiry unconditionally,
conditioned-plus-managed is a second mission, not a rescue of this one.

## Relationship to what is already known

- `spy-odte-credit` + geometry cross + QQQ/IWM replications + entry-hours
  surface: the near-money hold-to-expiry premium is real, session-wide from
  10:00–13:00 ET, mostly not drift, and fails exactly one condition — the
  discovery weekly bootstrap LB sits within ~1 bp of zero. IWM fails
  validation outright.
- `odte-both-sides` verification: at 10:00 ET, no name or side clears +10%
  net expectancy; side asymmetry is name-specific. Those runs read the same
  calendar windows at a different entry hour with settle-only pricing.
- This mission re-reads those windows a second time, at 12:00 ET, with
  intraday path data. The multiplicity burden is recorded below.

## Universe, structure, entry (frozen)

- **Universe**: the twelve house names — SPY, QQQ, IWM, NVDA, AAPL, MSFT,
  AMZN, GOOGL, META, TSLA, AVGO, IBIT.
- **Structure**: 0DTE near-money credit spread, both sides (put and call),
  pct-defined geometry — short strike 0.26% OTM, width 0.64% of spot, the
  qqq-nearmoney / friday-names convention of every `*-nm` snapshot. Single
  names probe the $1/$2.5/$5 strike grids as in `stage_odte_sides.py`.
- **Entry**: 12:00 ET, one entry hour, no grid. Justification declared in
  advance: mid-day entry after the opening range is the idea under test;
  the entry-hours surface showed 10:00–13:00 equivalent within noise with
  e12o2 best out of sample, and a single declared hour avoids spending a
  second grid.
- **Pricing**: actual traded option bars (Alpaca OPRA), the family standard.
  No Black-Scholes marks anywhere.

## The path snapshot (frozen conventions)

Per name and session: both sides' short and long legs (four legs), hourly
marks 12:00 → 15:00 ET, plus the underlying's hourly path. Settle is
mechanical at intrinsic from the 15:00 underlying mark, as in every
campaign of this family.

- **Carry-forward**: a leg with no bar in hour *h* takes its most recent
  earlier mark (the `_at` convention of `prepare_odte_credit.py`, extended
  forward through the session). A leg with no mark at entry is a skipped
  session, exactly as before.
- **Granularity caveat, declared now**: hourly marks cannot see the
  intra-hour path. A stop that would have triggered intra-hour and then
  reverted is invisible to this test, and a triggered exit fills at the
  hour's mark, which may be better or worse than a live stop fill. The
  direction of the net bias is ambiguous. Consequence, declared now: this
  test can **refute** a management rule outright, but a rule that survives
  must be restaged on minute bars before its holdout is spent. Hourly is
  the cheap first court, not the final one.

## The management rules (frozen, no grid)

All P&L is expressed as a fraction of the original spread width, the family
unit. "Spread value" at hour *h* is short-leg mark minus long-leg mark —
the cost of buying the spread back. Triggers are evaluated at the 13:00,
14:00 and 15:00 marks only (a decision needs one mark after entry).

- **M0 — hold (baseline).** Credit − intrinsic at settle. The promoted-cell
  mechanics, restaged at 12:00.
- **M1 — stop 200.** Exit at the first mark where spread value ≥ 3× entry
  credit (a loss of 2× credit). Otherwise hold to settle.
- **M2 — stop 100.** Exit at the first mark where spread value ≥ 2× entry
  credit (a loss of 1× credit). Otherwise hold to settle.
- **M3 — strike touch.** Exit at the first mark where the underlying has
  crossed the short strike (put: below; call: above). Otherwise hold.
- **M4 — condor conversion.** On the M1 trigger: buy back the threatened
  spread at the mark, and sell the *opposite-side* spread staged for the
  same session (its 12:00 strikes, valued at the trigger-hour mark), held
  to settle at intrinsic. One intervention maximum; the new spread is
  unmanaged. **Declared approximation**: a live conversion picks fresh
  strikes near the then-current money; using the 12:00 strikes is
  conservative — at the trigger hour those strikes are further from the
  money and collect less credit than a real conversion would.
- **M5 — cut the short, keep the runner.** On the M1 trigger: buy back the
  short leg at its mark, keep the long leg to settle, collect its
  intrinsic. One intervention maximum.

A session where a triggered exit cannot be priced (no mark for a needed leg
even carried forward) is dropped from that rule's series and counted; it
stays in M0.

## Cost model (frozen)

- Entry: **0.5% of width**, the family standard, applied through the
  validator's `cost_bps` exactly as in every prior campaign.
- Each management event (a stop exit, the condor's close-plus-open, the
  M5 short-leg buyback) adds **0.5% of width**, baked into the rule's
  payoff column before the validator sees it. M4 is two events (1.0%).
- Robustness: every cell is also run at 1.0% entry cost (the geometry
  sweep's second level). The validator's own `survives_double_cost` gate
  doubles the entry leg only; management costs are already realized in the
  payoff and are not re-doubled. Declared here so it is not discovered
  later.

## Claims under test

One claim per name × side × rule, judged per cell against M0 on the same
tape. **The primary endpoint is the family's failing condition**: the
weekly bootstrap lower bound (5th percentile of resampled weekly means,
2000 resamples, the frozen `weekly_bootstrap_lb`), raw and drift-neutral,
on discovery and validation.

A management rule is **SUPPORTED** for a name × side only if, at 0.5%
entry cost:

1. every shipped `bars_universe` gate passes on discovery AND validation;
2. raw weekly bootstrap LB > 0 on both splits;
3. drift-neutral weekly bootstrap LB > 0 on both splits; and
4. the rule beats M0 on the same sessions on all four of: discovery net,
   validation net, and the two bootstrap LBs — management that clears the
   bar but pays for it with worse expectancy than doing nothing is a
   refutation of the intervention, not a finding.

M0 itself is run on every tape as the anchor; where M0 already fails
condition 2, the cell reports whether management moves the LB and at what
expectancy cost, which is the decision-relevant number even when nothing
reaches SUPPORTED.

## Splits, floors, holdout (frozen)

- Splits: the staging manifest's `suggested_splits` (chronological
  60/20/20), taken as produced.
- Floor: the family's twelve-name standard applied per cell —
  `min_trades`/`min_sessions` 80 discovery, 35 validation, 35 holdout. A
  cell below floor is **UNTESTABLE**, explicitly not refuted. Single names
  traded Mon/Wed/Fri expiries for much of the window and IBIT listed
  2024-11-19, so thin cells are expected.
- **Holdout stays sealed regardless of outcome**, exactly as in the
  geometry cross: management rules that "work" in discovery+validation
  must survive minute-bar restaging (see the granularity caveat) before
  any holdout spend is even proposed.

## Multiple comparisons

12 names × 2 sides × 5 managed rules vs M0 = 120 comparisons against
baseline, of which the 24 SPY/QQQ cells are the family's home tapes and
the rest are names where the both-sides verification already found no
unconditional edge. Under independence at a conventional threshold,
roughly 6 cells would "clear" by chance; any survivor count is reported
against that expectation, and same-name put/call cells are not independent
evidence (shared underlying, shared sessions). These windows were already
read once at 10:00 by `odte-both-sides`; this is the second spend of the
same calendar, recorded here.

## What this test cannot say

Hourly granularity (above). One geometry, one entry hour, fixed trigger
parameters — 1×/2× credit and strike-touch are conventions, not optimized
values, and no other values will be tried against this snapshot. Single-name
samples are thin. Costs are assumed, not measured fills — the 10:00 ET
capture program measures entry cost for hold-to-expiry, and no equivalent
measurement exists for managed exits, which cross the spread under stress,
in the worst liquidity of the day. If anything, the true cost of
intervention is higher than modeled. A refuted rule does not mean no
management can work; it means this rule, at these conventions, did not
beat doing nothing on this tape.

Backtests are hypothetical, research-only evidence and are not investment
advice.
