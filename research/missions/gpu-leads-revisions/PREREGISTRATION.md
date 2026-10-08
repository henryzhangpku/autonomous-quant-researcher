# Preregistration: do GPU rental prices lead estimate revisions?

Frozen 2026-10-08, before any of the data that will judge it exists in this
repository. The machine-readable form is [`preregistration.json`](preregistration.json);
where the two differ, the JSON governs. Both files, and the code that will
compute the verdict, are bound by SHA-256 into the hash-chained
[`ledger.jsonl`](ledger.jsonl). Editing any of them fails
`python -m research.prereg.gpu_leads_revisions verify`.

## The idea

Rental prices for H100, H200 and B200 GPUs move daily. Consensus estimates
for the companies that sell, host and buy that compute move when analysts
revise them, mostly around earnings. If rising rental rates mean compute
demand is outrunning supply, that should show up weeks later as upward
revisions to those companies' estimates. The claim under test is the lead:
the rental price moves first.

There are two questions, asked in order. Campaign 2 is asked only if
campaign 1 passes.

1. **Campaign 1.** Do GPU rental prices lead consensus estimate revisions for
   a fixed basket?
2. **Campaign 2.** Do the basket's returns follow?

## Why this is a preregistration and not a backtest

The test needs data this repository does not hold:

- multi-year daily H100 rental price history, for 2023 to mid-2026; and
- point-in-time consensus estimate history (what the consensus was on each
  date, as known that day) from S&P Capital IQ or LSEG I/B/E/S.

The public GPU rental price index this mission reads has published daily only
since 27 August 2026, and its current methodology version since 17 September
2026. That is not enough for one 3-month change.

So nothing is estimated here and there are no results. What is frozen is the
test: the signal, the basket, the outcome, the horizons, the dates, the gates
and the decision rule. The campaign refuses to evaluate until every data
requirement is met, and it will then be judged once on these rules.

## Signal (frozen)

- **Primary:** the 3-month change in the H100 rental price,
  `log(V_t / V_anchor)`, where the anchor is the fixing 63 XNYS sessions
  before `t` (or the nearest earlier one within 3 sessions).
- **Independent check:** the same signal on H200 and on B200, in the holdout.
- **Observation rules.** One value per index and date: the revision that was
  live at the time, never a later correction. Withheld days are missing and
  never filled. Weekend and holiday fixings are ignored. A window may not span
  a methodology version change; that is the index's own rule, which splits a
  series at a version rather than splicing across it. At least 90% of the
  sessions in a window must have a fixing.
- **Position:** +1 when the signal is above zero, -1 when below. A week with no
  live signal is not evaluated.
- **Timing:** decisions are taken at the last session of each ISO week, using
  the signal computable at the session before, read as of 14:30 UTC on the
  decision day.

## Basket (frozen)

- Suppliers and neoclouds: **NVDA, AMD, AVGO, CRWV, NBIS**.
- Hyperscalers, for capex: **MSFT, GOOGL, AMZN, META**.
- Returns benchmark: **SPY**.

Names that have no coverage early in the sample (CRWV and NBIS listed
recently) join when coverage exists. A week counts only if at least three
names do.

## Outcomes (frozen)

**Campaign 1, primary.** The 8-week change in consensus FY+1 revenue:
`log(consensus at t+8w / consensus at t)` for each name, with the fiscal year
pinned at `t` so a year roll inside the window is followed, not mixed. The
basket value is the equal-weight mean over names with at least 3 contributors
and an observation dated within 7 days at both ends.

The weekly capture is `position × (Y_t − m_t)`, where `m_t` is the mean of
every outcome whose 8-week window had already closed by `t`. Removing that
running mean is what stops a basket whose estimates rose throughout from
passing as a timing signal. At least 13 closed outcomes are needed before a
week is evaluated.

**Campaign 1, confirmation.** The same capture on hyperscaler FY+1 capex
revisions. It must have a positive mean in validation and in the holdout.

**Reported only, never decisive:** FY+1 EPS revisions, and the primary
outcome at 4 and 13 weeks.

**Campaign 2.** The next week's equal-weight basket log total return minus
SPY's. Capture is `position × Y_t − costs`. Costs are 5 bps per side on each
of the two legs whenever the position changes, and the first week pays to
enter.

## Stages (frozen)

| Stage | Decision weeks | Signal source |
|---|---|---|
| Discovery | 2023-07-01 to 2024-12-31 | licensed H100 history |
| Validation | 2025-01-01 to 2026-07-31 | licensed H100 history |
| Embargo | 2026-08-01 to 2026-10-08 | not read by any stage |
| Holdout | from 2026-10-09, closing at the 52nd evaluable week | the public index tape, read point-in-time |

The 8-week outcomes of the last validation weeks close before this freeze.
Every holdout week comes after it, so the holdout is untouched by
construction, and it is opened once.

## Gates (frozen, every stage)

1. Mean weekly capture positive (after base costs, where traded).
2. Positive in the first half and in the second half of the stage's weeks.
3. Positive with the single best week removed.
4. The 5% lower bound of a consecutive-block bootstrap of the mean is
   positive: 2,000 resamples, 8-week blocks for revisions and 4-week blocks
   for returns, seeded from the preregistration hash.
5. Positive at twice the base trading cost. This applies to traded outcomes
   only and is marked not applicable for estimate revisions.

**Sample floor:** 52 evaluable weeks in each stage, and each signal state
(+1 and -1) in at least 20% of them. Below the floor the stage is
**UNTESTABLE**. That is recorded as our ignorance, never as a refutation.

**Campaign 2 only:** the mean capture must also beat always holding the
basket against SPY over the same weeks.

## Decision rule (frozen)

- Stages run in order. A failed stage ends the campaign with **FAIL**. A stage
  below its floor ends it with **UNTESTABLE**.
- **Campaign 1 PASS** requires all of the following:
  - discovery passes every gate;
  - validation passes every gate and the capex confirmation; and
  - the holdout passes every gate, the capex confirmation, and both the H200
    and the B200 checks.
- **Campaign 2** is evaluated only after a campaign 1 PASS. It uses the same
  stages and gates plus the always-long baseline gate, and its holdout also
  requires both checks. Without a campaign 1 PASS it stays sealed and is never
  evaluated.
- A refusal for missing data is not a verdict. It does not open the holdout.
- No parameter, basket, date, horizon or gate changes after this freeze. A
  changed idea is a new mission with a new ledger entry, and this campaign's
  verdict stands as recorded.

## Data requirements

| Requirement | Source | State at freeze |
|---|---|---|
| Daily H100 rental history, 2023-03-01 to 2026-07-31, 90% of sessions | licensed vendor | not held |
| Point-in-time consensus FY+1 revenue, supplier and neocloud basket, 90% of closed weeks | Capital IQ or LSEG | not licensed |
| Point-in-time consensus FY+1 capex, hyperscalers, 90% of closed weeks | Capital IQ or LSEG | not licensed |
| 52 holdout weeks with a live H100 signal and a closed 8-week window | public index | accumulating |
| H200 and B200 live in 90% of those weeks | public index | accumulating |
| Weekly total-return closes, basket and SPY (campaign 2) | Alpaca | not staged; sealed |

`python -m research.prereg.gpu_leads_revisions status` prints the current
state of each requirement, how much history exists against how much is
required, and the refusal.

## A future source, not part of this campaign

CME Group lists H100 and B200 rental index futures on NYMEX for trade date
5 October 2026, pending regulatory review. The exchange notice is
[SER-9785](https://www.cmegroup.com/content/dam/cmegroup/notices/ser/2026/08/ser-9785.pdf)
and the announcement is at [investor.cmegroup.com](https://investor.cmegroup.com/node/55686).
A futures curve would give a forward-looking rental price. Using it would
need a new preregistration; it cannot be added to this one.

## What this test cannot say

It tests one signal, one basket and one horizon. A PASS would say that the
3-month H100 rental change carried information about the following 8 weeks of
consensus revisions for these nine names over these dates. It would not be a
trading signal. Campaign 2 would still have to show that returns follow after
costs. A FAIL would say that this version of the idea, stated this way, did
not hold.

The discovery and validation signal comes from a licensed history and the
holdout signal from the public index. These are two methodologies for one
economic quantity, and a disagreement between them would show up as a holdout
failure, not be hidden by it.

This is research-only evidence. It is not investment advice.
