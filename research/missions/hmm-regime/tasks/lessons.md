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
