/*
 * The verdict — the one sentence a research run has earned.
 *
 * This exists because the most valuable moment in an autonomous quant lab is
 * the rare one where the machine says YES, and that moment has to mean
 * something. It is the broadcast board's only verdict source — the same
 * session must never read "alpha found" in one place and "exploratory" in
 * another.
 *
 * THE LINE THIS FILE DEFENDS. "Alpha found" over a positive exploratory score
 * would be the single claim that discredits everything else on screen — the
 * whole lab is built on the fact that a positive number is NOT acceptance.
 * So the headline fires only where the evidence has actually cleared the
 * gates the server owns:
 *
 *   promoted        a candidate passed every discovery AND validation gate
 *   holdout_ready   one finalist passed the frozen validation gates
 *   complete        ONLY with a recorded validation result — a lab run that
 *                   merely met its target on the discovery split is not alpha
 *
 * and never on best_metric, never on an improved trial, never mid-discovery.
 *
 * The scope line is not decoration and must not be trimmed. Even a candidate
 * that cleared everything is a research finding: it authorises no execution,
 * the final holdout is still sealed, and paper/shadow review is the most it
 * can permit. A screenshot of the headline without the scope is exactly how a
 * research lab turns into a tip sheet.
 *
 * A refutation is a first-class verdict too. "We tested it and there is no
 * edge" is the result that makes the yes believable, so it gets a real
 * headline rather than being rendered as an absence.
 */

(() => {
  'use strict';

  const FOUND_STATUSES = new Set(['promoted', 'complete']);
  const REFUTED_STATUSES = new Set(['no_survivor', 'no_discovery_survivor', 'no_validation_survivor']);
  // `ready` is what a LAB run reports while it grinds — it is a working
  // state, not a finished one. Leaving it out made a live lab question fall
  // through to the idle branch, so the board announced "Standing by" over a
  // run that was actively proposing candidates (2026-08-15).
  const RUNNING_STATUSES = new Set(['queued', 'running', 'ready']);

  const SCOPE_FOUND =
    'Research finding — not a signal, not a trade. The final holdout is still sealed.';

  /** Any machine string that reaches the headline leaves as words, never as
   *  tokens: "budget_exhausted" at poster size is the exact bug this helper
   *  exists to make impossible. Applied at every boundary where a payload
   *  string becomes a headline or a detail. */
  function humanize(text) {
    return String(text || '').replace(/_/g, ' ').replace(/\s+/g, ' ').trim();
  }

  /** A score for a detail line, or '' when there isn't one — never a
   *  fabricated zero (same rule as the board's em dash). */
  function scoreText(value) {
    if (value === null || value === undefined || value === '') return '';
    const n = Number(value);
    if (!Number.isFinite(n)) return '';
    return `${n >= 0 ? '+' : ''}${n.toFixed(2)}`;
  }

  /**
   * @param {object|null} session a session or finding payload
   * @returns {{kind:'found'|'refuted'|'running'|'idle', headline:string, detail:string, scope:string}}
   */
  function researchVerdict(session) {
    if (!session) {
      return { kind: 'idle', headline: 'Standing by', detail: 'No research session is running.', scope: '' };
    }

    const status = String(session.status || '');
    const stage = String(session.stage || '');
    const artifact = session.artifact || {};
    const promoted = Array.isArray(artifact.promoted) ? artifact.promoted.length : 0;

    // A DISCOVERY-ONLY acceptance is not alpha, and calling it that is the
    // one claim that would discredit every other line on this board.
    //
    // A compiled lab run reaches `complete` by beating its target on the
    // DISCOVERY split alone — its validator is pinned to --split discovery,
    // it evaluates no out-of-sample data, and its payload carries no
    // `validation` and no `holdout` key at all. Rendering that as ALPHA FOUND
    // under the scope line "the final holdout is still sealed" asserts two
    // gates that never ran. Two independent reviews caught this on the eve of
    // a public showcase (2026-08-16); this file's own header forbids exactly
    // it. `complete` may only clear when the payload proves the frozen
    // contract ran — promoted candidates, a reached holdout stage, or a
    // recorded validation result.
    const validated = Array.isArray(session.validation && session.validation.results)
      && session.validation.results.length > 0;
    const cleared = stage === 'holdout_ready' || promoted > 0
      || (FOUND_STATUSES.has(status) && validated);

    if (FOUND_STATUSES.has(status) && !cleared) {
      // Honest, and still a real result worth showing on camera.
      return {
        kind: 'running',
        headline: 'TARGET MET — DISCOVERY ONLY',
        detail: 'Met the declared target on the discovery split.',
        scope: 'No out-of-sample validation has been run. This is not accepted alpha.',
      };
    }

    if (cleared) {
      // Say what actually cleared, and how many. A bare "alpha found" with no
      // count is the version that ages badly.
      let detail;
      if (promoted > 0) {
        detail = `${promoted} candidate${promoted === 1 ? '' : 's'} cleared every discovery and validation gate.`;
      } else if (stage === 'holdout_ready') {
        detail = 'One finalist cleared the frozen validation gates.';
      } else {
        detail = humanize(session.outcome) || 'A candidate passed every gate and the declared target.';
      }
      return { kind: 'found', headline: 'ALPHA FOUND', detail, scope: SCOPE_FOUND };
    }

    if (REFUTED_STATUSES.has(status)) {
      return {
        kind: 'refuted',
        headline: 'NO EDGE — REFUTED',
        detail: humanize(session.outcome) || 'No candidate survived the absolute gates.',
        // The refutation is the product here, not a failure to produce one.
        scope: 'A tested negative is a result — the refutation is the finding.',
      };
    }

    // A PAUSED lab run is two different things wearing one status, and the
    // reason token decides which. `budget_exhausted` means the campaign spent
    // its whole candidate budget accepting none — the knowledge layer records
    // that as "refuted: N candidates, none met the bar", so the headline says
    // exactly that: a RESULT, not an error. Any other recorded reason is a
    // pause, said in words; no reason at all is just "Paused" — the verdict
    // never invents one.
    if (status === 'paused') {
      const reason = String(session.paused_reason || '');
      if (reason === 'budget_exhausted') {
        const trials = Number.isFinite(Number(session.run_count)) ? Number(session.run_count)
          : (Array.isArray(session.attempts) ? session.attempts.length : null);
        const best = scoreText(session.best_metric);
        return {
          kind: 'refuted',
          headline: 'NO EDGE — REFUTED',
          detail: [
            trials !== null
              ? `${trials} candidates evaluated, none met the bar`
              : 'No candidate met the bar',
            best ? `(best ${best})` : '',
          ].filter(Boolean).join(' '),
          scope: 'A tested negative is a result — the refutation is the finding.',
        };
      }
      if (reason) {
        return {
          kind: 'idle',
          headline: `PAUSED — ${humanize(reason)}`,
          detail: humanize(session.summary) || '',
          scope: '',
        };
      }
      return {
        kind: 'idle',
        headline: 'Paused',
        detail: humanize(session.summary) || '',
        scope: '',
      };
    }

    // A failed run is not a refutation (the question was not exhausted) and
    // not an error banner. The lab loop marks a campaign "failed" only when
    // candidate ADMISSION collapses — a proposer failure, not an evaluator
    // one — and the server says so with one generic outcome string. That
    // signature gets the knowledge layer's own language: "halted early", with
    // whatever count and best score the record actually carries. A failure
    // WITHOUT that signature is a failure we do not recognize: plain human
    // wording, no invented verdict.
    if (status === 'failed') {
      const outcome = humanize(session.outcome);
      if (outcome.toLowerCase().includes('candidate admission needs attention')) {
        const trials = Number.isFinite(Number(session.run_count)) ? Number(session.run_count)
          : (Array.isArray(session.attempts) ? session.attempts.length : null);
        const best = scoreText(session.best_metric);
        const stats = trials !== null
          ? `${trials} candidates evaluated, none near the bar${best ? ` (best ${best})` : ''}`
          : (best ? `Best exploratory score ${best}` : '');
        return {
          kind: 'idle',
          headline: 'HALTED EARLY — PROPOSER COLLAPSE',
          detail: [stats, 'the question was not exhausted'].filter(Boolean).join('; '),
          scope: '',
        };
      }
      return {
        kind: 'idle',
        headline: outcome || 'Failed',
        detail: humanize(session.summary) || '',
        scope: '',
      };
    }

    if (RUNNING_STATUSES.has(status)) {
      return {
        kind: 'running',
        headline: humanize(session.outcome) || 'Research running',
        detail: 'Proposing, evaluating and discarding — every trial kept.',
        scope: '',
      };
    }

    // A fact-check campaign is its own kind of result: it tests someone
    // else's published claim. Worth naming rather than leaving unlabelled.
    if (status === 'verification') {
      return {
        kind: 'refuted',
        headline: 'CLAIM CHECKED',
        detail: humanize(session.outcome || session.summary) || '',
        scope: 'A published claim measured against the data. Research only.',
      };
    }

    // Anything else: a headline is a headline. A whole paragraph in the
    // 68px slot is unreadable on a stream, so long text becomes the detail
    // and the title carries the top line.
    const raw = humanize(session.outcome || 'Research session');
    const long = raw.length > 90;
    return {
      kind: 'idle',
      headline: long ? humanize(session.title) || 'Research session' : raw,
      detail: long ? raw : humanize(session.summary) || '',
      scope: '',
    };
  }

  window.ResearchVerdict = { researchVerdict };
})();
