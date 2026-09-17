# 0DTE call-side single names + two loop defects — findings (2026-08-25)

Owner request: find 0DTE options alpha in the house universe (SPY, QQQ, IWM,
Mag 7, AVGO, IBIT). Two campaigns were run on the strongest un-campaigned
leads from the 12-market verification (`odte-credit-both-sides-v1`), and the
campaigns themselves reproduced two loop defects, both now fixed with
regression tests.

## Context that bounds every claim here

The 12-market verification read **every split of every staged 0DTE dataset,
including the holdouts**, for the unconditional cells. No sealed 0DTE data
remains in this universe. A gate pass on these tapes earns a forward test
(capture_odte_quotes.py), never a promotion. Both mission briefs said so.

## Campaigns (frozen bars-universe-portfolio-v1, 50 bps of width, target 0.75)

| Campaign | Attempts | Best | Verdict |
|---|---:|---:|---|
| ibit-0dte-call-credit-v1 | 24 | −2.81 | negative; 20 of 24 candidates fired on zero sessions |
| msft-0dte-call-credit-v1 | 24 | **1.05 (run 3)** | held by ordering, lost by the loop; recovered by hand — see below |

**MSFT run 3 recovered** (`otm_pct < 0.1 and credit_frac > 0.15 and
gap_open < 0.003`): discovery reproduces to six decimals (1.054664). On
validation it nets +5.55% of width per trade, Sharpe 2.40, and passes 8 of 9
gates — **rejected on `positive_both_halves`**, the identical gate that
killed the mag7-friday portfolio twice. Note the rule fires on 35 of 38
validation sessions (~92%): it is essentially the unconditional cell wearing
a light filter, which the mission brief explicitly warned discovers nothing.

Third consecutive confirmation of the family boundary: single-name 0DTE
credit premium is real but lumpy — earnings and idiosyncratic gaps break it
where index premium holds. **The promoted 0DTE alpha remains the SPY put
credit structure.** No registry change.

## The two loop defects (each reproduced twice on 2026-08-24)

1. **Held qualifiers were lost.** A candidate meeting the target before
   `min_attempts` was scored, held by ordering, and — because the novelty
   check forbids re-proposing its logic — could never be accepted. The
   advisory ledger note ("keep its mechanism") was demonstrably ignored by
   the proposer. Lost this way: `does-aapl-lag…` run 1 (1.731; the run then
   died on admission collapse) and msft-0dte-call-credit-v1 run 3 (1.054;
   budget exhausted). **Fix:** once `min_attempts` is satisfied and the
   current run is not accepted, the loop accepts the best held qualifier as
   it stands — the frozen validator already scored it; nothing re-runs, no
   trial is spent. State records `accepted_from_hold`. Regressions:
   `test_held_qualifier_is_accepted_once_min_attempts_is_reached`,
   `test_no_retro_acceptance_when_nothing_ever_met_the_target`.

2. **Zero-fire degeneration.** The proposer does not know the feature
   distributions, so it guesses thresholds outside the data's range and
   repeats the shape (20 of 24 IBIT candidates; the 20-day-high family died
   the same way on AAPL/AVGO). **Fix:** bars_universe preflight now runs the
   candidate over the discovery split and rejects a zero-fire rule before a
   trial is spent, returning per-feature discovery quantiles
   (min/p10/p50/p90/max) in the rejection so the retry can aim. Data access
   stays inside the validator; the loop is untouched for this fix.
   Regressions: `test_preflight_rejects_zero_fire_candidate_with_feature_ranges`,
   `test_preflight_reports_discovery_fires_for_a_firing_candidate`,
   `test_preflight_without_rows_skips_the_zero_fire_check`.

**AAPL run 1 recovered and settled:** discovery reproduces (1.731039);
validation **−6.228** — decisively rejected. The rule was "buy AAPL more
than 5% below its 20-day high": a dip-buyer, and dip-buyers catch the knife
out of sample in this lab's record. Nothing real was lost — but that is
known only because the recovery was actually run.

## Artifacts

Campaign specs/missions: `.research/missions/{ibit,msft}-0dte-call-credit-v1/`
(host-local). Ledgers: `.research/runs/<slug>/`. Recovery evaluations were
made with the frozen validator, hash-pinned spec and data, `--split
validation` — exactly the read the loop would have made on acceptance.

Hypothetical backtests, research-only evidence. Not investment advice.
