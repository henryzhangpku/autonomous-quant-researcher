# overnight-spx v3 roadmap — settle the put-credit question permanently

Status: PREREGISTERED DRAFT (2026-08-18). This document is the frozen contract
for the confirmation campaign. It declares the decision rules BEFORE the
captures run; the rules below are not to be renegotiated after results arrive.

Context: campaign 1 (evaluator v1, 40 trials) and campaign 2 (evaluator v2,
228-cell grid) promoted a single leader cell — SPXW put credit, width 20,
short-strike offset 15 points OTM, entered 20:15 ET, cash-settled 16:00 ET
same day. The promotion was withdrawn after the first holdout spend failed
(+0.0015 of width net, alpha -0.0007 drift-neutral, bootstrap LB -0.0739,
30 trades / 7 ISO weeks). Two defects invalidated that rejection as proof:

1. The holdout was structurally underpowered: MIN_WEEKS = 8 for the holdout,
   but the snapshot ended 2026-08-12, so the 2026-07-01+ window could contain
   at most 7 ISO weeks. The floor was unreachable by construction.
2. The economics were never validated against real executable quotes at the
   actual entry minute (one capture existed, at 22:30 ET, not 20:15 ET).

v3 closes both. It is a confirmation run of the ALREADY-FROZEN leader cell on
a NEW sealed window with measured costs — not a re-search. The search space
was exhausted in campaigns 1-2 (put credit: the only surviving family in ~800
gated evaluations).

## 1. Capture plan (piece 1 of the thread)

Harness: the nightly SPXW curb capture on the Windows host
(`~/.qs-tools/spxw-curb-capture/`), scheduled at 20:16 / 00:00 / 03:15 ET.

- Record, for expiry = that night's SPXW 0DTE, the executable (bid-side) and
  mid put-credit vertical quotes for widths {20, 25} x offsets {10, 15, 20}
  (leader cell: width 20, offset 15).
- The entry-time capture at 20:15 ET is the decision-relevant one; 00:00 and
  03:15 ET are robustness windows only.
- Half-spread cost per structure =
  `(mid_credit - exec_credit) / width` (fraction of width), the SAME
  economics convention the v2 campaign used (cost 0.015 of width, 2x stress
  0.03).

### Decision rule (declared before the week of captures)

- Requirement: at least 5 distinct capture nights, each with a 20:15 ET
  observation of the leader cell.
- Let `m` = median half-spread cost of the leader cell (w20/o15) across
  qualifying nights.
  - `m <= 0.015` (1.5% of width): the v2 economics stand; proceed to piece 2.
  - `0.015 < m < 0.03`: re-price the promotion test at cost `m` rounded UP to
    the next 0.005 grid step, declared before the holdout is opened.
  - `m >= 0.03` (3% of width): the structure is permanently unprofitable at
    modeled-consistent economics; the answer is "no trade" and the overnight
    put-credit line is closed forever. No holdout is spent.

## 2. New frozen contract (piece 2)

Evaluator: reuse `research.validators.overnight_spx_v2` UNCHANGED
(cost-frac, vrp 1.0, EWMA decay 0.85, settlement exit). v3 is a new contract
via new pinned inputs, exactly as campaign 2 pinned economics on the CLI:

- New snapshot: re-pull `es_overnight_5min.csv.gz` via
  `prepare_overnight_spx.py` on or after 2026-08-26, extending the tape past
  the 8-week-holdout boundary (holdout = 2026-07-01+ must contain >= 8 ISO
  weeks with >= 25 sessions, per the frozen validator floors).
- New `--data-hash`: the sha256 of the re-pulled snapshot; recorded in the
  campaign policy file BEFORE any evaluation.
- Cost: per piece 1's decision rule (0.015 if `m <= 0.015`, else the declared
  re-priced grid step).
- The 20:15 ET / width 20 / offset 15 put-credit leader cell is the ONLY
  candidate on this contract. No search, no substitutions, one spend.

## 3. The single holdout decision (piece 3)

Run the leader cell on the NEW holdout split once, via the frozen validator
CLI plus the drift attribution harness (`attribute_overnight_1dte_drift.py`
pattern: reproduce validator economics to 1e-9 before attributing).

PASS requires ALL of, on the new holdout:

- `minimum_weeks` (>= 8) and `minimum_trades` (>= 25);
- `weekly_bootstrap_positive` (5th percentile of weekly-mean resample > 0);
- `survives_without_best_week` and `positive_both_halves`;
- positive net mean at the declared cost AND at 2x declared cost
  (survives_double_cost);
- drift-neutral residual > 0 with weekly bootstrap lower bound > 0
  (the same control the cell passed on discovery and validation).

On PASS: the promotion is reinstated as research_only with the capture
evidence attached; the deployment gauntlet (paper reconciliation in the 0DTE
app) is the next step, and it is still NOT authorization to trade.

On FAIL (any gate): the overnight put-credit line is closed permanently.
Standing lab state remains: no promoted policies, nothing to trade.

## 4. Timeline and owners

| Date | Step | Where |
|---|---|---|
| 2026-08-18 | Preregister this roadmap + policy-v3.toml (hash placeholder) | this repo |
| 08-18 .. 08-26 | Nightly 20:15 ET captures (>= 5 nights) | Windows host harness |
| 08-26 | Verify captures via `verify_overnight_cost_capture.py`; declare cost per rule 1 | repo analysis script |
| 08-26 | Re-pull ES snapshot; pin new data hash in policy-v3.toml | Windows host (or wherever the Polygon key runs) |
| 08-26 | Single holdout evaluation + drift control on the leader cell | Windows host CLI |
| ~08-28 | Verdict recorded in FINDINGS.md + registry artifact | repo |

Nothing in this roadmap authorizes a trade. Authority remains `research_only`
throughout; the only allowed next action after a pass is paper/shadow review.

## 5. Failure-of-contract clauses

- If captures yield < 5 qualifying nights by 09-01, the cost decision is
  deferred (not defaulted): no holdout is spent on an unverified cost level.
- If the re-pulled snapshot hash differs for any reason other than extended
  data (provider change, symbol mapping), the contract aborts and the
  preparation is re-audited before any evaluation.
- If the leader cell cannot be constructed on the new snapshot (missing bars,
  degenerate pricing), the contract aborts; no substitute cell is chosen.
