# hmm-regime: lessons (one entry per loss or correction)

- 2026-10-04 (build): a filtered-vs-smoothed test on a cleanly separable
  planted tape passed trivially in the wrong direction: both posteriors were
  one-hot everywhere, so the "no smoothing" guarantee looked proven when it
  was untested. The test now uses overlapping regimes, where the backward
  pass actually moves probabilities. Rule: a no-lookahead test must be run on
  data where looking ahead would change the answer.
- 2026-10-04 (build): the lake's bar sync only extended forward from the last
  stored bar, so a backfill request silently returned 25 bars. Fixed to fill
  the history before the first stored bar too, with a test. Rule: a sync that
  returns far fewer rows than the window asked for is a bug, not a quiet day.
- 2026-10-04 (phase 4, SPY): the first full run reported the system flat
  and "31 of 31 refits frozen for drift" and that read as a market finding
  until the refit log was opened: every warm-started refit had failed on a
  missing hmmlearn attribute. The drift freeze did its job (no entries on a
  broken model) and in doing so hid a bug. Rule: a freeze that fires on
  every refit is a bug until proven otherwise; the report now prints the
  refit reasons.
- 2026-10-04 (phase 4, SPY): the base trend rule's measured Kelly on SPY
  daily is negative (p 0.31, b 2.0), so the system sizes to zero by the
  spec's own rule and the regime layer cannot be seen through it. Added a
  fixed-cap diagnostic arm rather than loosening the rule. Rule: when a
  gate zeros the system, show what it would have done, never relax the gate.
