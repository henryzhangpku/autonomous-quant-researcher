# gpu-leads-revisions: proxy track v3 (frozen 2026-10-08)

Parent: mission `gpu-leads-revisions`, ledger head `0f286d5dc8490c3b24b1c444cc9772171112d620291ab52355c0a49479f0577a`. That ledger accepts no new tracks once an evaluation is opened, so v3 is a separate mission pinned to it.

## Why there is a v3, and what was known

This is the **third** pre-registration of the proxy track. All three are in
this ledger.

- **v1 UNTESTABLE:** its discovery stage had 8 evaluable months (floor 12);
  the archive kept too few 2023 rate cards.
- **v2 UNTESTABLE:** also 8. Its decision grid started at its discovery start
  (2024-05), so the frozen warm-up rule, at least 3 outcomes matured before a
  month can be scored, consumed its first months.
- **v3** starts the decision grid at **2023-07-01**, so the warm-up uses earlier
  filings (outcomes need no GPU signal). Scored months are still only inside
  v2's stage windows: discovery 2024-05 to 2025-06, validation 2025-07 to
  2026-07. Signal, outcomes, baskets, gates, floors and code are unchanged.

**Known when v3 was frozen:** the H100 list-price series (so the signal
positions), and availability counts computed without any outcome value:
discovery 13 evaluable months (3 up, 10 down), validation 12 (5 up, 7 down).
**Not known:** any outcome value or capture.

Validation sits exactly at its floor of 12. A third attempt is weaker evidence
than a first; read any verdict with that in mind.

---

# Proxy track: a weaker test from free data

Frozen 2026-10-08, chained after the primary preregistration in the same
[`ledger.jsonl`](ledger.jsonl), before this repository read any of its inputs.
The machine-readable form is [`proxy-preregistration.json`](proxy-preregistration.json);
where the two differ, the JSON governs. The JSON, this document and the
evaluation code ([`research/prereg/proxy_campaign.py`](../../prereg/proxy_campaign.py),
plus the shared gates, signal and calendar) are bound by SHA-256 into the
`track_frozen` ledger event.

## Why there is a proxy track, and why it is weaker

The primary track needs licensed data: multi-year GPU rental history and
point-in-time consensus revisions. Consensus revisions remain the primary
campaign 1 outcome, and they remain unavailable. The proxy track asks a
weaker version of the same question from free inputs, so that some evidence
can exist before a licence does. It does not replace, amend or relax the
primary track, and each track is judged once, separately.

It is weaker in three ways, stated now so they cannot be forgotten later:

1. **The signal is list prices.** The discovery and validation signal is a
   monthly median reconstructed from archived public rate cards. These are
   list prices, not transactions, and they do not follow the public index's
   methodology.
2. **The outcome is coarse.** Reported quarterly results move four times a
   year, so the outcome series is short and heavily overlapping. Consensus
   revisions would move with every analyst update.
3. **The pre-freeze history is public.** XBRL filings, prices and archived
   rate cards for 2023 to 2026 are public. This repository had not read them
   when the track was frozen, but they are not unknowable. Only the holdout,
   which begins after the freeze, is untouched by construction.

## Signal (frozen)

Decisions are taken at the first XNYS session of each month. At the first
session of month `m` the signal is `log(M_(m-1) / M_(m-4))`, the 3-month
change between two completed months.

- **Discovery and validation:** `M` is the monthly H100 list-price median from
  the gpu-index back-series. A month counts only if it is labelled
  `list_price` and has at least 3 rate cards.
- **Holdout:** `M` is the mean of the public index's published H100 session
  fixings in the month, read point-in-time. A month counts only if 90% of its
  sessions have a fixing, and the four months of a window must share one
  methodology version.
- **Independent check:** the same signal on H200 and on B200, in the holdout.
- **Position:** +1 when the signal is above zero and -1 when below.

## Proxy campaign 1: does the signal lead reported growth acceleration?

The outcome is the acceleration in year-on-year growth of the next reported
quarter, computed from SEC XBRL facts stamped with their filing date. At each
decision only facts filed before that date are known.

- **Discrete quarter:** a directly reported 80 to 100 day value, or else the
  fiscal year-to-date value minus the prior year-to-date value with the same
  start. The latest filing on or before the as-of date wins.
- **Acceleration `A`:** the growth of the next quarter first filed after the
  decision, minus the growth of the last quarter filed before it. Growth is
  the log change against the quarter ending 365 days earlier (within 10 days),
  and that year-ago base must be known at the decision.
- **Two outcomes, both required:**
  - hyperscaler capex for MSFT, AMZN, GOOGL, META and ORCL (at least 3 names);
  - revenue for NVDA, AMD and AVGO (all 3).
- **Capture:** `position × (A − m)`, where `m` is the mean of every `A` already
  matured (filed) at the decision, with at least 3 required. The stage series
  is the mean of the two captures. Each capture alone must also have a
  positive mean in every stage.
- **Reported only, never decisive:** NVDA Data Center segment revenue, and
  CRWV and NBIS revenue.

## Proxy campaign 2: do forward returns follow?

Proxy campaign 2 runs only after a proxy campaign 1 PASS.

- **Basket:** equal-weight NVDA, AMD, AVGO, TSM, MU, SMCI, ORCL, MSFT, AMZN,
  GOOGL, META, CRWV and NBIS. A window counts only if at least 8 names have
  closes at both ends.
- **Primary outcome:** the forward 1-month basket log return minus SMH's.
  Costs are 5 bps per side on each of the two legs whenever the position
  changes. A month without a signal is flat.
- **Confirmations, sign only, every stage:** 1 month against QQQ, and 3 months
  against SMH and against QQQ.
- **Baseline gate:** the strategy must beat always holding the basket against
  SMH.

## Stages (frozen)

| Stage | Decisions | Signal source |
|---|---|---|
| Discovery | 2024-05-01 to 2025-06-30 | list-price back-series |
| Validation | 2025-07-01 to 2026-07-31 | list-price back-series |
| Embargo | 2026-08-01 to 2026-10-08 | none |
| Holdout | from 2026-10-09, closing at the 12th evaluable month | the public index, point-in-time |

A discovery or validation decision counts only if its outcome window closed
on or before 2026-10-08. The holdout's first possible signal is at the start
of February 2027: the index's current methodology began on 17 September 2026,
so October 2026 is its first complete month, and the window runs from October
2026 to January 2027.

## Gates (frozen, every stage)

1. Mean monthly capture positive (after base costs, where traded).
2. Positive in the first half and in the second half of the stage's months.
3. Positive with the single best month removed.
4. The 5% lower bound of a 3-month block bootstrap of the mean is positive
   (2,000 resamples, seeded from the proxy preregistration hash).
5. Positive at twice the base trading cost. This applies to traded outcomes
   only.

**Sample floor:** 12 evaluable months in each stage, and each signal state in
at least 20% of them. Below the floor the stage is **UNTESTABLE**, which is
never recorded as refuted. Twelve months is thin, and that is part of why
this track is weaker.

## Decision rule (frozen)

- Stages run in order. A failed stage ends the campaign with **FAIL**. A stage
  below its floor ends it with **UNTESTABLE**.
- **Proxy campaign 1 PASS** requires:
  - every gate and both single-outcome checks in discovery, validation and
    holdout; and
  - both the H200 and the B200 checks in the holdout.
- **Proxy campaign 2** is evaluated only after a proxy campaign 1 PASS. It
  uses the same stages and gates, plus the baseline gate and the three
  confirmations, and its holdout also requires both checks.
- A proxy PASS is not a primary PASS and is never reported as one. A proxy
  FAIL does not judge the primary track.
- Each proxy campaign is opened once. Its receipt records the content hash of
  every input file before the evaluation reads it.
- Nothing here changes after this freeze. A changed proxy is a new mission.

## Data requirements

| Requirement | Where it comes from | Configure with |
|---|---|---|
| Monthly H100 list-price medians, March 2023 to June 2026, 90% of months | gpu-index back-series, a separate file from the published tape | `GPU_INDEX_BACKFILL` or `--backfill` |
| XBRL capex and revenue facts with filing dates, covering 90% of discovery and validation decisions | SEC XBRL | `PROXY_XBRL_FACTS` or `--facts` |
| Daily adjusted closes for the basket, SMH, QQQ and SPY (proxy campaign 2) | Alpaca | `PROXY_PRICES` or `--prices` |
| 12 holdout months with a live monthly signal and a matured outcome; H200 and B200 live in 90% of them | the public index | the index tape |

The declared file schemas are in the JSON. To see the requirements, the
refusal and, once the data lands, the one-time verdict, run
`python -m research.prereg.gpu_leads_revisions proxy-status`.

## What this track cannot say

A proxy PASS would say that a list-price signal carried information about
the next quarter's reported growth acceleration for these names over these
dates. That is weaker evidence than the primary track would give, for the
three reasons above. It is not a trading signal.

This is research-only evidence. It is not investment advice.
