/*
 * Broadcast mode — the task-driven research loop, rendered for an audience.
 *
 * Deliberately self-contained: it polls the same read-only endpoints the
 * operator UI polls (`api/health`, `api/sessions`, `api/sessions/{id}`) and
 * owns its own DOM. It never imports from app.js and never mutates shared
 * state. If this file threw on every tick, the research app behind it would
 * still work.
 *
 * It issues exactly FIVE mutations, all behind the operator's own control and
 * all hitting research-session doors: `api/ideas` to compile a plain-English
 * idea into a frozen mission, `api/ideas/launch` to run it, `api/queue` to
 * park an idea for the loop to run later, `api/queue/move` to reorder that
 * queue, and `api/gateway` to pause or resume the gateway's task intake. Those
 * are the same doors as the old desktop compose view and header toggle plus a
 * queue the loop drains in order, reachable with a thumb -- not a shortcut
 * around the science. The mission contract, the frozen evaluator, the costs
 * and the whole gate battery are the server's, unchanged.
 *
 * NOTHING ELSE is reachable from here. Pause, resume, promote and above all
 * the sealed final holdout stay out, permanently: the holdout is single-use,
 * and a one-tap surface is the worst possible place to spend it.
 *
 * WHAT THE SHOW IS. A quant lab on camera is not a chart; it is an argument.
 * The machine proposes hypotheses, most of them die, a few survive absolute
 * gates, one gets frozen for validation, and a single sealed holdout is never
 * opened casually. That sequence is the drama, so the layout puts the method
 * on screen — formulations vs admitted trials vs discards, the stage the run
 * is actually in, and the fact that the holdout is still sealed.
 *
 * HONESTY RULES, which are also the growth rules. This is public video, so
 * every element has to survive being screenshotted out of context:
 *   - "Best exploratory score" always carries "not accepted alpha". A bare
 *     number on a stream is a performance claim.
 *   - Every rotating rail line must be literally true of what is on screen.
 *   - Nothing invents data. Unknown renders as an em dash, never as zero —
 *     a confident 0 is a lie about an unread value.
 */

(() => {
  'use strict';

  const POLL_MS = 5000;
  const REQUEST_TIMEOUT_MS = 30000;
  const RAIL_MS = 11000;
  const LEDGER_ROWS = 15; // fills the panel at 1080p; more visible evidence is the point
  const QUEUE_ROWS = 4; // head of the queue; the rest stays server-side
  const REVIEW_ROWS = 20; // recent sessions in the past-research overlay

  // Every line must be literally true of what this lab does and what is on
  // screen. No outcome, performance, or endorsement claims — ever.
  const RAIL_LINES = [
    'Every formulation is recorded, including the invalid ones that never became trials.',
    'Discovery evidence is exploratory. A positive score is not accepted alpha.',
    'The evaluator is frozen before the first result — candidates cannot rewrite costs or acceptance.',
    'The final holdout is sealed and single-use. It is never opened automatically.',
    'Failures are kept on purpose: a best-of-N result cannot hide its trial count.',
  ];

  // The QS desk link is hidden from the public surface for now (Henry,
  // 2026-08-17); the rail carries no outbound CTA. History preserves it.

  const state = {
    on: false,
    sessionId: null,
    session: null,
    health: null,
    pinned: false, // camera is pinned to a past session the operator opened
    lastNarration: '',
    railIndex: 0,
    timers: [],
    // Equity curve cache, keyed by session id: fetched once per session,
    // null data means "this id has no curve" (404/422) — the block stays
    // hidden rather than redrawing an empty chart every poll.
    equity: { id: null, data: null, pending: false },
  };

  // ── Pure helpers ────────────────────────────────────────────────────────

  /** Which session the show follows: the newest running one, else the newest
   *  overall so the stream still shows the last thing that happened. */
  function selectSession(sessions) {
    const rows = (Array.isArray(sessions) ? sessions : []).filter(Boolean).slice();
    // Sort on last ACTIVITY. Lab snapshots carry no `started`, so the old key
    // parsed to NaN for exactly the rows this show most needs to rank, and a
    // four-hour-dead run could win the camera over one working right now.
    rows.sort((a, b) => recencyOf(b) - recencyOf(a));
    // A running loop always wins the camera. Then the newest live-research
    // session, and only then a read-only finding artifact -- otherwise last
    // night's finding hides tonight's actual work.
    // Live work wins the camera. Otherwise the MOST RECENT real session,
    // read-only or not: preferring non-read-only rows meant a stale canonical
    // run from hours ago outranked the lab question that had just finished,
    // so between runs the show kept replaying old news. Findings are last —
    // last night's artifact must not hide tonight's work.
    const visible = rows.filter((s) => !s.archived);
    return visible.find(isLive)
      || visible.find((s) => !isFinding(s))
      || visible[0] || null;
  }

  /** Statuses that mean WORK IS HAPPENING NOW.
   *
   *  Lab runs report `ready` while they grind and are flagged read_only, so
   *  the old test (`queued`/`running` only) could not see them at all: with a
   *  lab question genuinely running, the board showed the last finished
   *  canonical session and a big IDLE badge — the engine looked stopped while
   *  it was working (Henry, 2026-08-15). Findings are artifacts and are never
   *  live whatever their status string says. */
  const WORKING_STATUSES = ['queued', 'running', 'ready'];

  /** How recently this session did anything, as an epoch ms (0 = unknown). */
  function recencyOf(session) {
    const stamp = Date.parse(session?.updated_at || session?.started || session?.created_at || '');
    return Number.isFinite(stamp) ? stamp : 0;
  }

  /** A working status is not enough: a run that DIED in a working status
   *  keeps that status forever. The engine already refuses to treat a row
   *  untouched for 30 minutes as a live loop; the show has to agree, or it
   *  puts a four-hour-old corpse on camera under a LIVE badge. */
  const LIVE_STALE_AFTER_MS = 30 * 60 * 1000;

  /** A lab row rewrites its state file constantly, so 30 minutes of silence
   *  means it died. A schema-v3 session does NOT: its updated_at moves only on
   *  a status change, so a campaign that is grinding through trials looks
   *  motionless the whole time. Judging both on the same window would drop the
   *  camera off the very run this show exists to display. */
  const V3_STALE_AFTER_MS = 3 * 60 * 60 * 1000;

  function isLive(session) {
    if (!session || isFinding(session)) return false;
    if (!WORKING_STATUSES.includes(String(session.status))) return false;
    const seen = recencyOf(session);
    // Unknown recency keeps the benefit of the doubt for a v3 session (whose
    // status is authoritative) but not for a read-only lab row.
    if (!seen) return !session.read_only;
    const window = session.read_only ? LIVE_STALE_AFTER_MS : V3_STALE_AFTER_MS;
    return Date.now() - seen < window;
  }

  /** Findings are a different shape from live sessions: no formulations, no
   *  attempts, no best_metric -- but a real funnel (grid cells -> gate passers
   *  -> shortlist -> promoted) and a plain-English conclusion. Rendering one
   *  with the live-loop template produced em dashes, an empty ledger, and a
   *  "Validation: Not started" chip under a headline saying it had PASSED
   *  validation. */
  function isFinding(session) {
    return session?.source === 'finding' || String(session?.id || '').startsWith('finding--');
  }

  /** A number we were given, or an em dash. Never a fabricated zero.
   *  Null/undefined/'' are rejected BEFORE the finite check because
   *  Number(null) is 0 — a failed trial with no metric rendered as "+0.00",
   *  which is precisely the confident zero this file forbids. */
  function present(value) {
    return value !== null && value !== undefined && value !== '';
  }

  function metric(value) {
    if (!present(value) || !Number.isFinite(Number(value))) return '—';
    const n = Number(value);
    return `${n >= 0 ? '+' : ''}${n.toFixed(2)}`;
  }

  function count(value) {
    if (!present(value) || !Number.isFinite(Number(value))) return '—';
    return String(Number(value));
  }

  function decisionOf(attempt) {
    if (attempt.error || attempt.metric === null) return 'failed';
    if (attempt.accepted) return 'accepted';
    if (attempt.improved) return 'improved';
    return 'discarded';
  }

  function ageLabel(iso) {
    const t = Date.parse(iso || '');
    if (!Number.isFinite(t)) return '';
    const s = Math.max(0, Math.round((Date.now() - t) / 1000));
    if (s < 90) return `${s}s ago`;
    if (s < 5400) return `${Math.round(s / 60)}m ago`;
    return `${Math.round(s / 3600)}h ago`;
  }

  /** Attempt timestamps are less standardized than session ones: accept an
   *  ISO string or an epoch (seconds or ms), else NaN. */
  function stampOf(value) {
    if (typeof value === 'number' && Number.isFinite(value)) return value < 1e12 ? value * 1000 : value;
    const t = Date.parse(value || '');
    return Number.isFinite(t) ? t : NaN;
  }

  function etClock() {
    return new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York', hour12: false,
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    }).format(new Date());
  }

  // ── DOM ─────────────────────────────────────────────────────────────────

  function build() {
    const el = document.createElement('div');
    el.className = 'bc';
    el.id = 'broadcast';
    el.innerHTML = `
      <header class="bc-band">
        <div class="bc-mark">α</div>
        <div class="bc-brand"><b>AUTONOMOUS QUANT RESEARCHER</b><span>LIVE DEMO · SYNTHETIC TAPE</span></div>
        <div class="bc-badges">
          <button class="bc-pill bc-auto" id="bc-auto" type="button" hidden>GATEWAY</button>
          <span class="bc-pill bc-live" id="bc-live"><i></i>LIVE</span>
          <span class="bc-clock" id="bc-clock">--:--:-- ET</span>
        </div>
      </header>

      <main class="bc-stage">
        <div class="bc-review-banner" id="bc-review-banner" hidden>
          <span id="bc-review-banner-text"></span>
          <button class="bc-btn go" id="bc-review-live" type="button">Back to live</button>
        </div>
        <div class="bc-loop" id="bc-loop">
          <div class="bc-kicker">THE LOOP — RUNS ON ASSIGNMENT</div>
          <div class="bc-loop-stages" id="bc-loop-stages"></div>
          <div class="bc-loop-count" id="bc-loop-count">— loops today</div>
        </div>

        <div class="bc-mission" id="bc-mission">Starting the research engine in your browser…</div>
        <h1 class="bc-outcome"><span id="bc-outcome">Standing by</span><small id="bc-detail"></small></h1>
        <div class="bc-scope" id="bc-scope" hidden></div>
        <div class="bc-objective" id="bc-objective" hidden></div>

        <div class="bc-stages" id="bc-stages"></div>

        <div class="bc-counters">
          <div class="bc-tile"><b id="bc-formulations">—</b><span>Formulations</span><small>every model attempt</small></div>
          <div class="bc-tile"><b id="bc-trials">—</b><span>Scientific trials</span><small>admitted candidates</small></div>
          <div class="bc-tile is-muted"><b id="bc-discards">—</b><span>Discarded / failed</span><small>kept, never hidden</small></div>
          <div class="bc-tile is-accent"><b id="bc-best">—</b><span>Best exploratory score</span><small>NOT accepted alpha</small></div>
        </div>

        <div class="bc-lower">
          <section class="bc-narration">
            <div class="bc-kicker" id="bc-activity-kicker">ACTIVITY — WHAT THE LOOP IS DOING</div>
            <p id="bc-narration">Waiting for the first candidate.</p>
            <div class="bc-queue" id="bc-queue" hidden>
              <div class="bc-kicker">UP NEXT — ASSIGNED TASKS</div>
              <div id="bc-queue-rows"></div>
            </div>
            <div class="bc-loops" id="bc-loops" hidden>
              <div class="bc-kicker">TODAY'S TASKS — WHAT THE GATEWAY RAN</div>
              <div id="bc-loops-rows"></div>
            </div>
            <div class="bc-loops" id="bc-reports" hidden>
              <div class="bc-kicker">REPORTED BACK — CONCLUSIONS</div>
              <div id="bc-reports-rows"></div>
            </div>
            <div class="bc-age" id="bc-narration-age"></div>
          </section>
          <section class="bc-ledger">
            <div class="bc-traj" id="bc-trajectory-block" hidden>
              <div class="bc-kicker">AUTORESEARCH — BEST SCORE VS TRIAL</div>
              <div class="bc-traj-chart">
                <svg id="bc-trajectory" viewBox="0 0 300 72" preserveAspectRatio="none" aria-hidden="true"></svg>
                <span class="bc-traj-max" id="bc-traj-max" hidden></span>
                <span class="bc-traj-min" id="bc-traj-min" hidden></span>
              </div>
              <div class="bc-traj-rate" id="bc-trajectory-rate"></div>
            </div>
            <div class="bc-eq" id="bc-equity-block" hidden>
              <div class="bc-kicker">EQUITY — DISPLAY: FULL WINDOW, NET OF COSTS</div>
              <div class="bc-eq-chart">
                <svg id="bc-equity" viewBox="0 0 300 72" preserveAspectRatio="none" aria-hidden="true"></svg>
                <span class="bc-eq-max" id="bc-eq-max" hidden></span>
                <span class="bc-eq-min" id="bc-eq-min" hidden></span>
                <span class="bc-eq-date bc-eq-date-a" id="bc-eq-date-a" hidden></span>
                <span class="bc-eq-date bc-eq-date-b" id="bc-eq-date-b" hidden></span>
              </div>
              <div class="bc-eq-note" id="bc-equity-note"></div>
            </div>
            <div class="bc-kicker">AUTORESEARCH LOG — KEPT OR DISCARDED, NONE HIDDEN</div>
            <div id="bc-rows"></div>
          </section>
        </div>
      </main>

      <footer class="bc-rail">
        <button class="bc-review-open" id="bc-review-open" type="button">Past research</button>
        <div class="bc-rail-line" id="bc-rail-line">${RAIL_LINES[0]}</div>
      </footer>`;
    document.body.append(el);

    const start = document.createElement('button');
    start.className = 'bc-new';
    start.type = 'button';
    start.textContent = '+ New research';
    start.addEventListener('click', () => openSheet());
    // Lives IN the rail: on a phone it slots inline next to the desk chip
    // (floating it overlapped the rail text — Henry's 8/15 screenshot); on
    // desktop it stays fixed bottom-right, where the parent is irrelevant.
    el.querySelector('.bc-rail').append(start);

    // The GATEWAY pill is a toggle, not just an indicator: it pauses or
    // resumes task intake, state shown on the pill itself.
    el.querySelector('#bc-auto').addEventListener('click', () => void toggleGateway());

    // Past research: the overlay reads with the same two GETs the poll loop
    // already issues. The one thing a tap there changes is WHICH session the
    // camera is pinned to — never the record itself.
    el.querySelector('#bc-review-open').addEventListener('click', () => void openReview());
    el.querySelector('#bc-review-live').addEventListener('click', () => void backToLive());

    // No exit chip in the only-UI shell: there is nothing underneath to
    // return to, and on a touch phone a ghost chip over the header is pure
    // cost. The legacy ?broadcast=1 path (over operator chrome) keeps it.
    if (!state.onlyUi) {
      const exit = document.createElement('button');
      exit.className = 'bc-exit';
      exit.type = 'button';
      exit.textContent = 'Exit broadcast (Esc)';
      exit.addEventListener('click', () => stop());
      el.append(exit);
    }
    return el;
  }

  const byId = (id) => document.getElementById(id);

  // ── Start a run (the sheet: two of this surface's three mutations) ──────

  function buildSheet() {
    const sheet = document.createElement('div');
    sheet.className = 'bc-sheet';
    sheet.id = 'bc-sheet';
    sheet.hidden = true;
    sheet.innerHTML = `
      <div class="bc-sheet-card" role="dialog" aria-label="Start new research">
        <div class="bc-kicker">START NEW RESEARCH</div>
        <p class="bc-sheet-help">Ask a market question in plain English. It becomes a fixed experiment — data, periods, and pass/fail gates — shown to you for approval before anything runs.</p>
        <textarea id="bc-idea" rows="3" maxlength="2000" placeholder="e.g. Does SPY drift up the day after it closes near the low?"></textarea>
        <div class="bc-sheet-error" id="bc-sheet-error"></div>
        <pre class="bc-sheet-mission" id="bc-sheet-mission" hidden></pre>
        <div class="bc-sheet-actions">
          <button type="button" class="bc-btn ghost" id="bc-sheet-cancel">Cancel</button>
          <button type="button" class="bc-btn" id="bc-sheet-compile">Compile</button>
          <button type="button" class="bc-btn ghost" id="bc-sheet-queue" hidden>Add to queue instead</button>
          <button type="button" class="bc-btn go" id="bc-sheet-launch" hidden>Start research</button>
        </div>
        <div class="bc-sheet-or">or</div>
        <button type="button" class="bc-btn ghost wide" id="bc-sheet-canonical">Run the built-in SPY 0DTE program instead</button>
        <p class="bc-sheet-help small">A fixed reference experiment on already-staged data — starts immediately, no approval step.</p>
      </div>`;
    document.body.append(sheet);

    const close = () => { sheet.hidden = true; resetSheet(); };
    sheet.addEventListener('click', (e) => { if (e.target === sheet) close(); });
    byId('bc-sheet-cancel').addEventListener('click', close);
    byId('bc-sheet-compile').addEventListener('click', () => void compileIdea());
    byId('bc-sheet-launch').addEventListener('click', () => void launchIdea());
    byId('bc-sheet-queue').addEventListener('click', () => void queueIdea());
    byId('bc-sheet-canonical').addEventListener('click', () => void launchCanonical());
    return sheet;
  }

  let compiledSlug = null;

  function resetSheet() {
    compiledSlug = null;
    const mission = byId('bc-sheet-mission');
    if (mission) { mission.hidden = true; mission.textContent = ''; }
    const launch = byId('bc-sheet-launch');
    if (launch) launch.hidden = true;
    const queue = byId('bc-sheet-queue');
    if (queue) queue.hidden = true;
    const err = byId('bc-sheet-error');
    if (err) err.textContent = '';
  }

  function sheetError(message) {
    const err = byId('bc-sheet-error');
    if (err) err.textContent = message;
  }

  async function compileIdea() {
    const idea = String(byId('bc-idea')?.value || '').trim();
    if (!idea) { sheetError('Describe the idea first.'); return; }
    sheetError('Compiling…');
    try {
      const preview = await post('api/ideas', { idea });
      compiledSlug = preview.slug || null;
      const mission = byId('bc-sheet-mission');
      // Show what actually got FROZEN. The point of the one-tap path is that
      // it is easy to ask, not that it is vague about what it will test.
      mission.textContent = preview.mission || preview.summary || JSON.stringify(preview, null, 1);
      mission.hidden = false;
      byId('bc-sheet-launch').hidden = !compiledSlug;
      // Queueing the same text is offered exactly when launching it is: the
      // idea compiled, so the loop can take it now or later.
      byId('bc-sheet-queue').hidden = !compiledSlug;
      sheetError(compiledSlug ? '' : 'Compiled, but no runnable slug came back.');
    } catch (error) {
      resetSheet();
      sheetError(error.message || 'Could not compile that idea.');
    }
  }

  async function launchIdea() {
    if (!compiledSlug) return;
    sheetError('Starting…');
    try {
      await post('api/ideas/launch', { slug: compiledSlug });
      byId('bc-sheet').hidden = true;
      resetSheet();
      const field = byId('bc-idea');
      if (field) field.value = '';
      // Follow the new run immediately rather than waiting for the next poll
      // to notice it — the whole point on camera is that it starts NOW.
      state.pinned = false;
      state.sessionId = null;
      await poll();
    } catch (error) {
      sheetError(error.message || 'Could not start that research run.');
    }
  }

  /** The OTHER door out of a compiled idea: park the ORIGINAL question text
   *  on the loop's queue instead of spending a run on it right now. The
   *  immediate-launch path above is untouched — same idea, later slot. */
  async function queueIdea() {
    const idea = String(byId('bc-idea')?.value || '').trim();
    if (!idea) { sheetError('Describe the idea first.'); return; }
    sheetError('Queueing…');
    try {
      const reply = await post('api/queue', { idea });
      // The position is stated only when it is KNOWN. The bare contract
      // ({"queued": true}) carries none, so it is re-read from health after
      // the write rather than guessed from anything client-side.
      let position = present(reply.position) && Number.isFinite(Number(reply.position))
        ? Number(reply.position) : null;
      const health = await get('api/health').catch(() => null);
      if (health) {
        state.health = health;
        const pending = health.gateway?.pending;
        const depth = Array.isArray(pending) ? pending.length : health.gateway?.queue_depth;
        if (position === null && present(depth) && Number.isFinite(Number(depth))) {
          position = Number(depth);
        }
      }
      sheetError(position !== null ? `Queued — position ${position}` : 'Queued.');
      // Brief pause so the confirmation is actually read, then close — the
      // board itself confirms the entry once the next render sees the queue.
      window.setTimeout(() => {
        byId('bc-sheet').hidden = true;
        resetSheet();
        const field = byId('bc-idea');
        if (field) field.value = '';
        render();
      }, 1100);
    } catch (error) {
      sheetError(error.message || 'Could not queue that idea.');
    }
  }

  /** The frozen SPY program: the highest-confidence run the lab can start,
   *  and the one that cannot fail on a missing data key. The mission text is
   *  taken from the operator form already in this page — the server rejects
   *  any alternative wording as a different contract, so duplicating it here
   *  would be a time-bomb the first time the canonical text changed. */
  async function launchCanonical() {
    const mission = document.getElementById('research-mission')?.value?.trim();
    if (!mission) {
      sheetError('The canonical mission is not available on this page — use the operator UI.');
      return;
    }
    const typed = String(byId('bc-idea')?.value || '').trim();
    const stamp = new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(new Date());
    sheetError('Starting the frozen program…');
    try {
      const created = await post('api/sessions', {
        title: typed || `Live session ${stamp} ET`,
        mission,
      });
      byId('bc-sheet').hidden = true;
      resetSheet();
      const field = byId('bc-idea');
      if (field) field.value = '';
      state.pinned = false;
      state.sessionId = created?.id || null;
      await poll();
    } catch (error) {
      sheetError(error.message || 'Could not start the frozen program.');
    }
  }

  function openSheet() {
    if (!byId('bc-sheet')) buildSheet();
    byId('bc-sheet').hidden = false;
    byId('bc-idea')?.focus();
  }

  // ── Gateway toggle (the header pill's other job) ───────────────────────

  let autoBusy = false;

  /** A failed toggle must still SAY so on camera. The sheet's error line is
   *  sheet-scoped, so board-level errors get a small transient toast. */
  function flashToast(message) {
    const host = byId('broadcast');
    if (!host) return;
    byId('bc-toast')?.remove();
    const toast = document.createElement('div');
    toast.className = 'bc-toast';
    toast.id = 'bc-toast';
    toast.textContent = message;
    host.append(toast);
    window.setTimeout(() => toast.remove(), 4500);
  }

  async function toggleGateway() {
    // One toggle in flight: a double-click must not queue on→off→on.
    if (autoBusy) return;
    autoBusy = true;
    const auto = byId('bc-auto');
    if (auto) auto.classList.add('is-busy');
    const enabled = Boolean(state.health?.gateway?.enabled);
    try {
      await post('api/gateway', { enabled: !enabled });
      // Refresh from the server rather than trusting the write: the pill
      // shows what health reports, not what we asked for.
      await poll();
    } catch (error) {
      flashToast(`Gateway ${enabled ? 'pause' : 'resume'} failed — ${error.message || 'request failed'}`);
    } finally {
      autoBusy = false;
      if (auto) auto.classList.remove('is-busy');
    }
  }

  // ── Past research — finished runs, reviewable on the board ─────────────

  function buildReview() {
    const overlay = document.createElement('div');
    overlay.className = 'bc-review';
    overlay.id = 'bc-review';
    overlay.hidden = true;
    overlay.innerHTML = `
      <div class="bc-review-head">
        <div class="bc-kicker">PAST RESEARCH — KEPT, SUCCESS OR FAILURE</div>
        <button type="button" class="bc-btn ghost" id="bc-review-close">Close</button>
      </div>
      <div class="bc-review-rows" id="bc-review-rows"></div>`;
    document.body.append(overlay);
    byId('bc-review-close').addEventListener('click', closeReview);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) closeReview(); });
    return overlay;
  }

  function closeReview() {
    const overlay = byId('bc-review');
    if (overlay) overlay.hidden = true;
  }

  /** What a list row says about how the run ended: the shared verdict module
   *  when it can read the snapshot, the raw status otherwise — never a guess. */
  function reviewVerdictOf(session) {
    const status = String(session?.status || '—').replace(/_/g, ' ').toUpperCase();
    try {
      if (window.ResearchVerdict) {
        const verdict = window.ResearchVerdict.researchVerdict(session);
        if (verdict && verdict.headline && verdict.kind !== 'idle') return verdict.headline;
      }
    } catch (_e) { /* the raw status below is the honest fallback */ }
    return status;
  }

  function reviewNote(text) {
    const note = document.createElement('div');
    note.className = 'bc-review-empty';
    note.textContent = text;
    return note;
  }

  async function openReview() {
    if (!byId('bc-review')) buildReview();
    byId('bc-review').hidden = false;
    const host = byId('bc-review-rows');
    host.replaceChildren(reviewNote('Loading…'));
    let rows;
    try {
      const list = await get('api/sessions');
      rows = (Array.isArray(list?.sessions) ? list.sessions : [])
        .filter((s) => s && !s.archived)
        .slice()
        .sort((a, b) => recencyOf(b) - recencyOf(a))
        .slice(0, REVIEW_ROWS);
    } catch (error) {
      host.replaceChildren(reviewNote(`Could not load past research — ${error.message || 'request failed'}`));
      return;
    }
    host.replaceChildren();
    if (!rows.length) {
      host.append(reviewNote('No past research recorded yet.'));
      return;
    }
    rows.forEach((session) => {
      // A row is one big tap target — the phone is the primary review device.
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'bc-review-row';
      row.innerHTML = '<span class="bc-rv-title"></span><span class="bc-rv-verdict"></span><span class="bc-rv-score"></span><span class="bc-rv-age"></span>';
      row.querySelector('.bc-rv-title').textContent = String(session.title || session.id || '—');
      row.querySelector('.bc-rv-verdict').textContent = reviewVerdictOf(session);
      row.querySelector('.bc-rv-score').textContent = metric(session.best_metric);
      row.querySelector('.bc-rv-age').textContent = ageLabel(session.updated_at || session.created_at) || '—';
      row.addEventListener('click', () => void reviewSession(session.id));
      host.append(row);
    });
  }

  /** Pin the camera to a finished session: the detail payload is the same
   *  shape render() already consumes, so the board just renders it. The pin
   *  stops the poll loop re-following live work until "Back to live". */
  async function reviewSession(id) {
    if (!id) return;
    try {
      const detail = await get(`api/sessions/${encodeURIComponent(id)}`);
      state.pinned = true;
      state.sessionId = id;
      state.session = detail;
      closeReview();
      render();
    } catch (error) {
      flashToast(`Could not open that session — ${error.message || 'request failed'}`);
    }
  }

  async function backToLive() {
    state.pinned = false;
    state.sessionId = null;
    render();
    await poll();
  }

  // ── Render ──────────────────────────────────────────────────────────────

  function renderStages(session) {
    const host = byId('bc-stages');
    if (!host) return;
    const trials = session?.scientific_trials ?? session?.run_count;
    const validation = isFinding(session)
      ? (session?.artifact?.validation?.length || 0)
      : (session?.validation?.results?.length || 0);
    const holdout = session?.holdout || {};

    // "Sealed" is the most important claim on this board, so it must be READ,
    // not defaulted. Only a v3 payload carries a holdout object; lab, history
    // and finding runs have no holdout stage at all, and asserting "Sealed"
    // from a missing field is a confident value for an unread one — the same
    // rule as the em-dash counters.
    let holdoutLabel = session?.holdout ? 'Sealed' : '—';
    if (holdout.consumed) holdoutLabel = 'Consumed';
    else if (holdout.ready && holdout.action_available) holdoutLabel = 'Ready — deliberate action';
    else if (holdout.ready) holdoutLabel = 'Sealed — adapter unavailable';

    // Likewise, a payload with no validation stage reads unknown, not
    // "Not started" — the latter implies a stage that is coming.
    const validationLabel = validation
      ? `${validation} frozen result(s)`
      : (isFinding(session) || session?.validation) ? 'Not started' : '—';

    const stage = String(session?.stage || '');
    const active = validation ? 'validation' : 'discovery';

    const chips = [
      {
        key: 'discovery',
        label: 'DISCOVERY',
        value: isFinding(session) && session?.artifact?.grid_cells
          ? `${session.artifact.grid_cells} cells evaluated`
          : (present(trials) && Number.isFinite(Number(trials)) ? `${trials} trials` : '—'),
      },
      { key: 'validation', label: 'VALIDATION', value: validationLabel },
      { key: 'holdout', label: 'FINAL HOLDOUT', value: holdoutLabel },
    ];

    host.replaceChildren();
    chips.forEach((chip, i) => {
      if (i) {
        const arrow = document.createElement('span');
        arrow.className = 'bc-arrow';
        arrow.textContent = '→';
        host.append(arrow);
      }
      const node = document.createElement('div');
      node.className = 'bc-stage-chip';
      if (chip.key === active && stage !== 'holdout_ready' && isLive(session)) node.classList.add('is-active');
      if (chip.key === 'holdout' && !holdout.consumed) node.classList.add('is-sealed');
      node.innerHTML = `<b>${chip.label}</b><span></span>`;
      node.querySelector('span').textContent = chip.value;
      host.append(node);
    });
  }

  // ── The loop strip — the machine's own cycle, always on camera ──────────

  const LOOP_STAGES = [
    { key: 'ask', label: 'ASK', note: 'you assign the question' },
    { key: 'compile', label: 'COMPILE', note: 'frozen mission + evidence' },
    { key: 'propose', label: 'PROPOSE', note: '7B writes a candidate' },
    { key: 'admit', label: 'ADMIT', note: 'novelty + contract, free' },
    { key: 'evaluate', label: 'EVALUATE', note: 'Alpaca-backed validator' },
    { key: 'verdict', label: 'VERDICT', note: 'gates scored' },
    { key: 'learn', label: 'LEARN ↻', note: 'memory feeds round N' },
  ];

  /** The newest GRID SWEEP launch still plausibly in flight, or null. Sweeps
   *  run as detached subprocesses and only become `finding--sweep--*`
   *  sessions when they FINISH, so while one runs the launch entry in the
   *  gateway history is the only sign of life it has. The newest launch
   *  decides; 'in flight' is derived from `at` recency alone — a missing or
   *  unparseable stamp means we know nothing, and the board claims nothing. */
  const SWEEP_FRESH_MS = 15 * 60 * 1000;

  function sweepInFlight(health) {
    const history = Array.isArray(health?.gateway?.history) ? health.gateway.history : [];
    for (let i = history.length - 1; i >= 0; i -= 1) {
      const entry = history[i];
      if (!entry || !entry.launched) continue;
      if (String(entry.launched) !== 'sweep') return null;
      const at = Date.parse(entry.at || '');
      if (!Number.isFinite(at)) return null;
      return Date.now() - at < SWEEP_FRESH_MS
        ? { target: String(entry.target || ''), at: String(entry.at || '') }
        : null; // an old sweep is a FINISHED sweep — never claim activity
    }
    return null;
  }

  /** Which loop stage the live state HONESTLY implies — or null when nothing
   *  is derivable. An unlit strip is true; a guessed highlight is not. */
  function loopStage(session, health) {
    const attempts = Array.isArray(session?.attempts) ? session.attempts : [];
    if (isLive(session)) {
      if (!attempts.length) return 'compile';
      const newest = stampOf(attempts[attempts.length - 1]?.timestamp);
      // A verdict that landed seconds ago means the pipeline is mid-cycle.
      if (Number.isFinite(newest) && Date.now() - newest < 90 * 1000) return 'evaluate';
      return 'propose';
    }
    // No live session, but a sweep in flight: a sweep IS evaluation, and it
    // outranks the just-finished 'learn' echo — one is happening now, the
    // other happened minutes ago.
    if (sweepInFlight(health)) return 'evaluate';
    if (session && !isFinding(session)) {
      const settled = ['complete', 'completed', 'paused', 'failed'].includes(String(session.status));
      const when = recencyOf(session);
      if (settled && when && Date.now() - when < 5 * 60 * 1000) return 'learn';
      return null;
    }
    // A queued task is the only honest 'ask': an idle gateway lights nothing.
    if (!session && Array.isArray(health?.gateway?.pending) && health.gateway.pending.length) return 'ask';
    return null;
  }

  function renderLoop(session, health) {
    const host = byId('bc-loop-stages');
    if (!host) return;
    const active = loopStage(session, health);
    host.replaceChildren();
    LOOP_STAGES.forEach((stage, i) => {
      if (i) {
        const arrow = document.createElement('span');
        arrow.className = 'bc-loop-arrow';
        arrow.textContent = '→';
        host.append(arrow);
      }
      const node = document.createElement('div');
      node.className = 'bc-loop-chip';
      if (stage.key === active) node.classList.add('is-active');
      node.innerHTML = '<b></b><small></small>';
      node.querySelector('b').textContent = stage.label;
      node.querySelector('small').textContent = stage.note;
      host.append(node);
    });
    const counter = byId('bc-loop-count');
    // count() renders the em dash when health has not answered yet — an
    // unread launch count is unknown, never a confident zero.
    if (counter) counter.textContent = `${count(health?.gateway?.launches_today)} tasks today`;
  }

  /** TODAY'S TASKS — the outer loop made concrete under the narration.
   *  Entries are the gateway's own launch history, filtered to the same
   *  UTC day the server counts `launches_today` against (gateway.py's tick
   *  rolls `launch_day` on the UTC date), so the list is literally what
   *  "N LOOPS TODAY" claims. Entries carry NO outcome field, so none is
   *  shown — question, family, age, nothing invented. No entries, no block. */
  function renderLoopsToday(health) {
    const block = byId('bc-loops');
    const host = byId('bc-loops-rows');
    if (!block || !host) return;
    const history = Array.isArray(health?.gateway?.history) ? health.gateway.history : [];
    const today = new Date().toISOString().slice(0, 10);
    // History is append-order (newest last); the list shows newest first.
    const entries = history
      .filter((e) => e && e.launched && String(e.at || '').slice(0, 10) === today)
      .reverse();
    if (!entries.length) {
      block.hidden = true;
      host.replaceChildren();
      return;
    }
    block.hidden = false;
    host.replaceChildren();
    entries.slice(0, 6).forEach((entry) => {
      const row = document.createElement('div');
      row.className = 'bc-loop-row';
      row.innerHTML = '<span class="bc-loopq"></span><span class="bc-loopm"></span>';
      const q = row.querySelector('.bc-loopq');
      const kind = String(entry.launched);
      if (kind === 'sweep') {
        q.textContent = `grid sweep — «${entry.target || '—'}»`;
      } else if (kind === 'canonical') {
        q.textContent = 'the frozen SPY 0DTE program';
      } else {
        const idea = String(entry.idea || '').trim();
        const family = String(entry.family || '').trim();
        q.textContent = `«${idea || '—'}»${family ? ` — ${family}` : ''}`;
      }
      row.querySelector('.bc-loopm').textContent = ageLabel(entry.at);
      host.append(row);
    });
    scheduleTrim();
  }

  /** REPORTED BACK — the gateway's conclusions, newest first. Entries come
   *  straight from health.gateway.reports; verdict and headline are the
   *  server's words, never paraphrased here. No reports, no block. */
  function renderReports(health) {
    const block = byId('bc-reports');
    const host = byId('bc-reports-rows');
    if (!block || !host) return;
    const reports = Array.isArray(health?.gateway?.reports) ? health.gateway.reports : [];
    if (!reports.length) {
      block.hidden = true;
      host.replaceChildren();
      return;
    }
    block.hidden = false;
    host.replaceChildren();
    reports.slice(-4).reverse().forEach((report) => {
      const row = document.createElement('div');
      row.className = 'bc-loop-row';
      row.innerHTML = '<span class="bc-loopq"></span><span class="bc-loopm"></span>';
      const question = String(report.question || report.kind || '').trim();
      row.querySelector('.bc-loopq').textContent =
        `«${question || '—'}» — ${String(report.verdict || '')}: ${String(report.headline || '')}`;
      row.querySelector('.bc-loopm').textContent = ageLabel(report.finished_at);
      host.append(row);
    });
    scheduleTrim();
  }

  // ── UP NEXT — the queue the loop drains, head first ────────────────────

  /** Entries come straight from health.gateway.pending. A host that
   *  predates the queue API has no such field, and an empty queue is an empty
   *  queue — in both cases the block stays hidden rather than inventing one.
   *  Whole rows only: the render caps the list, nothing is ever half-clipped.
   *  The ↑/↓ controls exist only on a fine pointer: on a phone the queue is
   *  a readout, not a console (matchMedia, not width — a tablet with a mouse
   *  is a pointer too). */
  function renderQueue(health) {
    const block = byId('bc-queue');
    const host = byId('bc-queue-rows');
    if (!block || !host) return;
    const pending = Array.isArray(health?.gateway?.pending) ? health.gateway.pending : [];
    if (!pending.length) {
      block.hidden = true;
      host.replaceChildren();
      return;
    }
    block.hidden = false;
    host.replaceChildren();
    const finePointer = Boolean(window.matchMedia && window.matchMedia('(pointer: fine)').matches);
    pending.slice(0, QUEUE_ROWS).forEach((entry, index) => {
      const row = document.createElement('div');
      row.className = 'bc-queue-row';
      // All three fields are untrusted strings: textContent, never HTML.
      row.innerHTML = '<span class="bc-qq"></span><span class="bc-q-tag"></span><span class="bc-qf"></span>';
      row.querySelector('.bc-qq').textContent = String(entry?.question || '—');
      row.querySelector('.bc-q-tag').textContent = String(entry?.source || '—').toUpperCase();
      row.querySelector('.bc-qf').textContent = String(entry?.family || '');
      if (finePointer) {
        const moves = document.createElement('span');
        moves.className = 'bc-q-moves';
        [['↑', 'up', 'Move earlier in the queue'], ['↓', 'down', 'Move later in the queue']]
          .forEach(([glyph, direction, label]) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'bc-q-move';
            button.textContent = glyph;
            button.title = label;
            button.setAttribute('aria-label', label);
            // The head cannot go earlier; "front" is just repeated up.
            if (direction === 'up' && index === 0) button.disabled = true;
            button.addEventListener('click', () => void moveQueue(index, direction));
            moves.append(button);
          });
        row.prepend(moves);
      }
      host.append(row);
    });
  }

  let queueBusy = false;

  async function moveQueue(index, direction) {
    // One move in flight: a double-click must not reorder twice.
    if (queueBusy) return;
    queueBusy = true;
    try {
      await post('api/queue/move', { index, direction });
      // Re-poll rather than trusting the write: the block renders what health
      // reports, not what we asked for.
      await poll();
    } catch (error) {
      flashToast(`Queue move failed — ${error.message || 'request failed'}`);
    } finally {
      queueBusy = false;
    }
  }

  // ── The autoresearch trajectory — best-so-far ratcheting upward ─────────

  // padL leaves a left gutter for the min/max score labels (an HTML overlay
  // in .bc-traj-chart — SVG <text> would stretch with preserveAspectRatio
  // "none" and read as smeared type on the encoded stream).
  const TRAJ = { w: 300, h: 72, pad: 7, padL: 26 };
  const SVG_NS = 'http://www.w3.org/2000/svg';

  function renderTrajectory(attempts) {
    const block = byId('bc-trajectory-block');
    const svg = byId('bc-trajectory');
    const rateNode = byId('bc-trajectory-rate');
    if (!block || !svg) return;
    const rows = Array.isArray(attempts) ? attempts : [];
    // No trials, no chart: a flat placeholder line would be a fabricated
    // trajectory, the chart-shaped version of a confident zero.
    if (!rows.length) { block.hidden = true; return; }
    block.hidden = false;
    svg.replaceChildren();

    const { w, h, pad, padL } = TRAJ;
    const values = rows.map((a) => (present(a.metric) && Number.isFinite(Number(a.metric)) ? Number(a.metric) : null));
    const scored = values.filter((v) => v !== null);
    const lo = scored.length ? Math.min(...scored) : 0;
    const hi = scored.length ? Math.max(...scored) : 1;
    const span = hi - lo;

    // Axis context: the REAL scored extremes at the left edge (top = best,
    // bottom = worst). No scored trials, no labels — a chart does not get to
    // invent its scale. A flat run shows the value once, not twice.
    const maxEl = byId('bc-traj-max');
    const minEl = byId('bc-traj-min');
    if (maxEl && minEl) {
      maxEl.hidden = !scored.length;
      minEl.hidden = !scored.length || span === 0;
      if (scored.length) maxEl.textContent = metric(hi);
      if (scored.length && span !== 0) minEl.textContent = metric(lo);
    }

    // The baseline axis first, under everything else: a flat or sparse
    // trajectory still reads as a chart with a floor, never as a broken one.
    const axis = document.createElementNS(SVG_NS, 'path');
    axis.setAttribute('d', `M ${pad} ${h - pad} H ${w - pad}`);
    axis.setAttribute('class', 'bc-traj-axis');
    svg.append(axis);
    const xAt = (i) => (rows.length > 1 ? padL + (i * (w - padL - pad)) / (rows.length - 1) : (padL + w - pad) / 2);
    const yAt = (v) => (span > 0 ? pad + (1 - (v - lo) / span) * (h - 2 * pad) : h / 2);

    // The ratchet: a step line of best-so-far over the scored trials only.
    // Higher is better for every metric this board plots — no per-metric
    // direction to special-case.
    if (scored.length) {
      let best = -Infinity;
      let d = '';
      values.forEach((v, i) => {
        if (v === null) return;
        const x = xAt(i).toFixed(1);
        if (v > best) {
          d += d ? ` H ${x} V ${yAt(v).toFixed(1)}` : `M ${x} ${yAt(v).toFixed(1)}`;
          best = v;
        } else {
          d += ` H ${x}`;
        }
      });
      const line = document.createElementNS(SVG_NS, 'path');
      line.setAttribute('d', d);
      line.setAttribute('class', 'bc-traj-line');
      svg.append(line);
    }

    rows.forEach((attempt, i) => {
      const decision = decisionOf(attempt);
      const x = xAt(i);
      if (values[i] === null) {
        // A trial with no metric is still a trial: a failed tick on the
        // baseline keeps the denominator honest.
        const tick = document.createElementNS(SVG_NS, 'path');
        tick.setAttribute('d', `M ${x.toFixed(1)} ${(h - pad - 4).toFixed(1)} V ${(h - pad + 4).toFixed(1)}`);
        tick.setAttribute('class', 'bc-traj-fail');
        svg.append(tick);
        return;
      }
      const y = yAt(values[i]);
      if (decision === 'failed') {
        const r = 2.6;
        const cross = document.createElementNS(SVG_NS, 'path');
        cross.setAttribute('d', `M ${(x - r).toFixed(1)} ${(y - r).toFixed(1)} L ${(x + r).toFixed(1)} ${(y + r).toFixed(1)} M ${(x - r).toFixed(1)} ${(y + r).toFixed(1)} L ${(x + r).toFixed(1)} ${(y - r).toFixed(1)}`);
        cross.setAttribute('class', 'bc-traj-fail');
        svg.append(cross);
        return;
      }
      const dot = document.createElementNS(SVG_NS, 'circle');
      dot.setAttribute('cx', x.toFixed(1));
      dot.setAttribute('cy', y.toFixed(1));
      dot.setAttribute('r', '3.2');
      dot.setAttribute('class', `bc-traj-dot bc-d-${decision}`);
      svg.append(dot);
    });

    // The pace line: trials, survivors, and throughput from real timestamps.
    // The rate is omitted under two stamps — a one-point "rate" is invented.
    if (rateNode) {
      const improved = rows.filter((a) => a.improved || a.accepted).length;
      const stamps = rows.map((a) => stampOf(a.timestamp)).filter(Number.isFinite).sort((a, b) => a - b);
      let rate = '';
      if (stamps.length >= 2) {
        const hours = (stamps[stamps.length - 1] - stamps[0]) / 3600000;
        if (hours > 0) rate = ` · ${(rows.length / hours).toFixed(1)}/hr`;
      }
      // A flat ratchet is an honest result, but the chart cannot SAY so —
      // the pace line does, instead of letting a horizontal line imply a slope.
      const flat = scored.length > 1 && span === 0 ? ' · flat — every scored trial equal' : '';
      rateNode.textContent = `${rows.length} trials · ${improved} improved${rate}${flat}`;
    }
  }

  // ── The equity curve — net daily P&L summed over the full window ───────

  const EQ = { w: 300, h: 72, pad: 7, padL: 26 };

  function equityLabel(value) {
    if (!Number.isFinite(value)) return '—';
    return Number(value).toFixed(3);
  }

  /** Fetch the curve once per session id. Unsupported ids (404) and
   *  unmappable evidence (422) both land in the same place: no curve, no
   *  chart, no console noise — the block simply never unhides. */
  async function fetchEquity(id) {
    state.equity = { id, data: null, pending: true };
    let data = null;
    try {
      data = await get(`api/sessions/${encodeURIComponent(id)}/equity`);
    } catch (_e) { /* unsupported id — the section stays hidden */ }
    state.equity = { id, data, pending: false };
    if (state.sessionId === id) renderEquity();
  }

  function renderEquity() {
    const block = byId('bc-equity-block');
    const svg = byId('bc-equity');
    if (!block || !svg) return;
    const id = state.session ? state.sessionId : null;
    const entry = state.equity;
    const points = entry.data && Array.isArray(entry.data.points) ? entry.data.points : [];
    if (!id || entry.id !== id || points.length < 2) {
      // One point is a value, not a curve — same rule as the trajectory's
      // "no trials, no chart".
      block.hidden = true;
      // Probe only the id families the resolver can ever serve: v3 hex ids
      // and history-- have no curve, and fetching them anyway would 404 on
      // every pin — a console full of failed-resource noise for a section
      // that is meant to simply stay hidden.
      const servable = id && (id.startsWith('lab--') || id.startsWith('finding--'));
      if (servable && entry.id !== id && !entry.pending) void fetchEquity(id);
      return;
    }
    block.hidden = false;
    svg.replaceChildren();

    const { w, h, pad, padL } = EQ;
    const values = points.map((p) => Number(p.equity));
    const lo = Math.min(...values);
    const hi = Math.max(...values);
    const span = hi - lo;
    const xAt = (i) => padL + (i * (w - padL - pad)) / (points.length - 1);
    const yAt = (v) => (span > 0 ? pad + (1 - (v - lo) / span) * (h - 2 * pad) : h / 2);

    const maxEl = byId('bc-eq-max');
    const minEl = byId('bc-eq-min');
    if (maxEl && minEl) {
      maxEl.hidden = false;
      maxEl.textContent = equityLabel(hi);
      minEl.hidden = span === 0;
      if (span !== 0) minEl.textContent = equityLabel(lo);
    }
    const dateA = byId('bc-eq-date-a');
    const dateB = byId('bc-eq-date-b');
    if (dateA && dateB) {
      dateA.hidden = false;
      dateB.hidden = false;
      dateA.textContent = String(points[0].date || '');
      dateB.textContent = String(points[points.length - 1].date || '');
    }

    // The floor axis first, then the 1.0 stake line when it falls inside the
    // plotted range: with additive fixed-notional staking, 1.0 is "the money
    // you started each session with", and a curve below it is genuinely down.
    const axis = document.createElementNS(SVG_NS, 'path');
    axis.setAttribute('d', `M ${pad} ${h - pad} H ${w - pad}`);
    axis.setAttribute('class', 'bc-eq-axis');
    svg.append(axis);
    if (span > 0 && lo < 1 && hi > 1) {
      const stake = document.createElementNS(SVG_NS, 'path');
      stake.setAttribute('d', `M ${pad} ${yAt(1).toFixed(1)} H ${w - pad}`);
      stake.setAttribute('class', 'bc-eq-stake');
      svg.append(stake);
    }

    const d = points
      .map((p, i) => `${i ? 'L' : 'M'} ${xAt(i).toFixed(1)} ${yAt(Number(p.equity)).toFixed(1)}`)
      .join(' ');
    const line = document.createElementNS(SVG_NS, 'path');
    line.setAttribute('d', d);
    line.setAttribute('class', 'bc-eq-line');
    svg.append(line);

    const note = byId('bc-equity-note');
    if (note) {
      const candidate = String(entry.data.candidate || '').trim();
      note.textContent = [
        candidate,
        `${points.length} sessions · display only — not a gate evaluation`,
      ].filter(Boolean).join(' — ');
    }
  }

  /** Whole rows only: a row half-clipped by the panel bottom reads as a
   *  rendering bug on camera, not as data. How many rows fit depends on which
   *  optional blocks are on screen, so the count is MEASURED after layout,
   *  never assumed. Runs every render, so a resized capture self-corrects on
   *  the next poll.
   *
   *  Rect-based, not scrollHeight: integer scrollHeight rounding plus the old
   *  +1px tolerance let the last row's descenders paint past the clip edge
   *  (1490×715 capture, 2026-08-16). Now the last row's bottom must sit a
   *  safety margin INSIDE the host's clip box or the row goes. */
  function trimRowsToFit(host) {
    // Nothing to trim when the box is content-sized rather than clipped: the
    // phone layout lets the page scroll, so #bc-rows is exactly as tall as
    // its rows — trimming there would just delete the last row every render.
    if (host.scrollHeight <= host.clientHeight) return;
    const MARGIN_PX = 4;
    // Freeze the clip edge BEFORE removing anything. A content-sized host
    // (the phone loops list under its max-height) re-anchors its bottom edge
    // as rows leave — each removal shrinks the box by one row, so a LIVE
    // re-measurement cascades the trim all the way to zero rows (seen on the
    // 390px capture, 2026-08-16). With the edge frozen and the host's top
    // fixed, each removal lifts the last row clear of it and the trim
    // converges. Flex-sized hosts (the desktop ledger) have a constant clip
    // edge anyway, so freezing changes nothing there.
    const clipBottom = host.getBoundingClientRect().bottom;
    let last = host.lastElementChild;
    while (last) {
      if (last.getBoundingClientRect().bottom <= clipBottom - MARGIN_PX) break;
      last.remove();
      last = host.lastElementChild;
    }
  }

  /** Row trimming runs AFTER layout settles, not synchronously inside
   *  render(): render() runs mid-poll while the flex heights it just changed
   *  (trajectory reveal, narration reflow, tile relabels) can still be in
   *  flux, and trimming against a transient height removed ledger rows that
   *  never came back — a 50-trial campaign showed 2 rows in a 5-row panel
   *  (2026-08-16). Double rAF: the first frame can still precede the style
   *  pass for content changed this render; the second measures the settled
   *  panel. Every render rebuilds the full row list, so each poll re-fits. */
  let trimScheduled = false;

  function scheduleTrim() {
    if (trimScheduled) return;
    trimScheduled = true;
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        trimScheduled = false;
        const ledger = byId('bc-rows');
        if (ledger) trimRowsToFit(ledger);
        const loops = byId('bc-loops-rows');
        if (loops) trimRowsToFit(loops);
      });
    });
  }

  function renderLedger(attempts) {
    const host = byId('bc-rows');
    if (!host) return;
    host.replaceChildren();
    const rows = [...(attempts || [])].reverse().slice(0, LEDGER_ROWS);
    if (!rows.length) {
      const empty = document.createElement('div');
      empty.className = 'bc-row';
      empty.innerHTML = '<span class="bc-note">No experiments completed yet.</span>';
      host.append(empty);
      scheduleTrim();
      return;
    }
    rows.forEach((attempt, index) => {
      const decision = decisionOf(attempt);
      const row = document.createElement('div');
      row.className = `bc-row${index === 0 ? ' is-new' : ''}`;
      // Floor at 0.6: 0.32 on this background is under 2:1 contrast, and a
      // video encoder destroys thin low-contrast text before anything else —
      // the evidence rows are the argument, so they have to survive it.
      row.style.opacity = String(Math.max(0.6, 1 - index * 0.055));
      // The candidate's own LABEL leads when the API has it — what was tried —
      // then why it lived or died. Null falls back to the verdict text alone.
      // Both pieces are untrusted strings: DOM nodes + textContent, never HTML.
      const base = attempt.feedback || attempt.error || 'no additional evidence';
      row.innerHTML = `<span class="bc-run"></span><span class="bc-metric bc-d-${decision}"></span><span class="bc-note"></span>`;
      row.querySelector('.bc-run').textContent = present(attempt.run) ? `#${attempt.run}` : '—';
      row.querySelector('.bc-metric').textContent = `${metric(attempt.metric)} ${decision}`;
      const noteEl = row.querySelector('.bc-note');
      if (attempt.label) {
        const labelEl = document.createElement('span');
        labelEl.className = 'bc-cand-label';
        labelEl.textContent = `«${attempt.label}»`;
        noteEl.append(labelEl, document.createTextNode(` — ${base}`));
      } else {
        noteEl.textContent = base;
      }
      host.append(row);
    });
    scheduleTrim();
  }

  /** The four counter tiles are re-labelled per session shape, so the same
   *  boxes carry a live loop or a finished campaign without ever showing a
   *  label whose number does not exist. */
  function setTile(index, value, label, scope) {
    const tile = document.querySelectorAll('.bc-tile')[index];
    if (!tile) return;
    tile.querySelector('b').textContent = value;
    tile.querySelector('span').textContent = label;
    const small = tile.querySelector('small');
    if (small) small.textContent = scope;
  }

  /** The finished-campaign ledger: what actually survived, and its gates. */
  function renderShortlist(artifact) {
    const host = byId('bc-rows');
    if (!host) return;
    host.replaceChildren();
    const rows = artifact.validation?.length ? artifact.validation : (artifact.shortlist || []);
    if (!rows.length) {
      const empty = document.createElement('div');
      empty.className = 'bc-row';
      empty.innerHTML = '<span class="bc-note">No shortlist recorded.</span>';
      host.append(empty);
      scheduleTrim();
      return;
    }
    rows.slice(0, LEDGER_ROWS).forEach((row, index) => {
      const gates = row.gates || {};
      const names = Object.keys(gates);
      const passed = names.filter((k) => gates[k]).length;
      const allPassed = names.length > 0 && passed === names.length;
      const node = document.createElement('div');
      node.className = `bc-row${index === 0 ? ' is-new' : ''}`;
      const structure = String(row.structure || 'candidate').replace(/_/g, ' ');
      const detail = [
        row.width ? `${row.width}-wide` : '',
        row.offset !== undefined ? `offset ${row.offset}` : '',
        row.entry_minute !== undefined ? `entry +${row.entry_minute}m` : '',
      ].filter(Boolean).join(' \u00b7 ');
      node.innerHTML = '<span class="bc-run"></span><span class="bc-metric"></span><span class="bc-note"></span>';
      node.querySelector('.bc-run').textContent = structure;
      const verdict = node.querySelector('.bc-metric');
      verdict.textContent = names.length ? `${passed}/${names.length} gates` : metric(row.score);
      verdict.classList.add(allPassed ? 'bc-d-accepted' : 'bc-d-discarded');
      node.querySelector('.bc-note').textContent = detail;
      host.append(node);
    });
    scheduleTrim();
  }

  function render() {
    const session = state.session;
    const health = state.health;

    const live = byId('bc-live');
    const running = isLive(session);
    if (live) {
      live.classList.toggle('is-idle', !running);
      live.lastChild.textContent = running ? 'LIVE' : 'IDLE';
    }

    // The review pin: the banner, not the pill, carries the "this is not
    // live" signal — the pill keeps reporting the PINNED session's own
    // status, which for a finished run honestly reads IDLE.
    const banner = byId('bc-review-banner');
    if (banner) {
      const pinnedTitle = state.pinned && state.session
        ? String(state.session.title || state.sessionId || '') : '';
      banner.hidden = !pinnedTitle;
      if (pinnedTitle) {
        byId('bc-review-banner-text').textContent = `REVIEWING PAST RESEARCH — ${pinnedTitle}`;
      }
    }

    renderLoop(session, health);
    renderLoopsToday(health);
    renderReports(health);
    renderQueue(health);

    if (!session) {
      // No health answer yet is NOT "not ready" — accusing a healthy host
      // during the first poll round trip put a false failure banner on the
      // live stream (phone, 2026-08-15).
      byId('bc-mission').textContent = !health
        ? 'Loading the engine (Pyodide) — about ten seconds…'
        : health.research_ready
          ? 'Engine ready — no campaign running'
          : 'Engine not ready';
      byId('bc-outcome').textContent = 'Standing by';
      byId('bc-outcome').parentElement.className = 'bc-outcome is-idle';
      byId('bc-detail').textContent = 'The next session will appear here automatically.';
      const idleScope = byId('bc-scope');
      if (idleScope) idleScope.hidden = true;
      renderStages(null);
      renderTrajectory([]);
      renderLedger([]);
      renderEquity();
      return;
    }

    // "READ-ONLY HISTORY" is true of the RECORD (a lab run cannot be resumed
    // from here) but reads as "this is old" over a run happening right now.
    // A live one says so instead.
    const provenance = running ? ' · LIVE RUN' : session.read_only ? ' · READ-ONLY HISTORY' : '';
    byId('bc-mission').textContent = `${session.market || 'RESEARCH PROGRAM'}${provenance}`;

    // The verdict is the headline. It is shared with the operator UI so the
    // same session can never read "alpha found" here and "exploratory" there.
    const verdict = window.ResearchVerdict
      ? window.ResearchVerdict.researchVerdict(session)
      : { kind: 'idle', headline: session.outcome || session.title || 'Research session', detail: session.title || '', scope: '' };
    const head = byId('bc-outcome');
    head.textContent = verdict.headline;
    head.parentElement.className = `bc-outcome is-${verdict.kind}`;
    const detailText = [verdict.detail, session.title]
      .filter(Boolean).join(' · ');
    byId('bc-detail').textContent = detailText;
    const scope = byId('bc-scope');
    if (scope) {
      // The scope line is what keeps the headline honest in a screenshot.
      scope.textContent = verdict.scope;
      scope.hidden = !verdict.scope;
    }
    const objective = byId('bc-objective');
    if (objective) {
      const mission = String(session.mission || '').trim();
      const stop = mission.indexOf('. ');
      // Plain indexOf, not a lookbehind: `(?<=\.)` is a PARSE-time error on
      // iOS Safari before 16.4, which fails the whole file rather than this
      // one line, and the board simply never appears.
      let firstSentence = stop > 0 ? mission.slice(0, stop + 1) : mission;
      // And it is clamped: a compiled mission often has no early period, so
      // the 'first sentence' became the whole paragraph and pushed the
      // counters and both panels out of a fixed, non-scrolling frame.
      if (firstSentence.length > 180) firstSentence = firstSentence.slice(0, 177).trimEnd() + '…';
      // The lab titles a run BY its question and the mission opens with the
      // same sentence, so the detail subline and this line can say the
      // question twice in a row. Compared punctuation-blind; when the detail
      // already carries it, this line steps back — say the question once.
      const words = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
      const duplicate = words(firstSentence) && words(detailText).includes(words(firstSentence));
      objective.textContent = firstSentence && !duplicate ? `OBJECTIVE — ${firstSentence}` : '';
      objective.hidden = !firstSentence || Boolean(duplicate);
    }

    const trials = session.scientific_trials ?? session.run_count;
    const attempts = session.attempts || [];
    const discards = attempts.filter((a) => ['discarded', 'failed'].includes(decisionOf(a))).length;
    const art = session.artifact || {};
    const finding = isFinding(session);

    // A finished campaign carries its own, better funnel: grid cells -> gate
    // passers -> shortlist -> promoted. Show that rather than live-loop
    // counters it does not have.
    // A lab run records every model attempt in `attempts` and has no separate
    // `formulations` count, so the tile fell to an em dash for the whole run.
    // The fallback is only taken when the array actually exists — a missing
    // array stays unknown rather than becoming a confident zero.
    const formulations = session.formulations ?? (session.attempts ? attempts.length : null);
    setTile(0, finding ? count(art.grid_cells) : count(formulations),
            finding ? 'Grid cells' : 'Formulations',
            finding ? 'combinations evaluated' : 'every model attempt');
    setTile(1, finding ? count(art.discovery_gate_passers) : count(trials),
            finding ? 'Passed discovery' : 'Scientific trials',
            finding ? 'of the full grid' : 'admitted candidates');
    // A verify artifact has neither shortlist nor promoted, so `(x || []).length`
    // printed a confident 0 for concepts that run does not have — the exact
    // fabricated zero this file's header forbids. Absent stays an em dash.
    setTile(2, finding ? (art.shortlist ? count(art.shortlist.length) : '—') : (attempts.length ? String(discards) : '—'),
            finding ? 'Shortlisted' : 'Discarded / failed',
            finding ? 'frozen for validation' : 'kept, never hidden');
    // The "NOT accepted alpha" qualifier is mandated by this file's honesty
    // rules and is paired to the rail line — but it shipped in the static DOM
    // and was then overwritten on the first render, so it survived only in
    // the idle state.
    setTile(3, finding ? (art.promoted ? count(art.promoted.length) : '—') : metric(session.best_metric),
            finding ? 'Promoted' : 'Best exploratory score',
            finding ? 'promoted — research only' : 'exploratory — NOT accepted alpha');

    const narration = finding
      ? ([art.question, session.summary].filter(Boolean).join('\n\n') || session.outcome)
      : (session.best_rationale || session.last_feedback || session.error || 'Waiting for the first measurable candidate.');
    const kicker = byId('bc-activity-kicker');
    // A sweep in flight LEADS the narration: the campaign on screen is real
    // history, but the live fact is the grid running underneath it — without
    // this line the board reads IDLE over a stale campaign and looks dead
    // while the engine is working (Henry's "dead?", 2026-08-16).
    const sweep = running ? null : sweepInFlight(health);
    if (kicker) {
      kicker.textContent = running || sweep
        ? 'ACTIVITY — WHAT THE LOOP IS DOING'
        : 'BEST CANDIDATE — RECORDED RATIONALE';
    }
    const activity = sweep
      ? `GRID SWEEP RUNNING — «${sweep.target || '—'}» — exhaustive single-feature grid over the paid-for dataset — ${ageLabel(sweep.at) || 'just now'}\n\n${narration}`
      : narration;
    if (activity !== state.lastNarration) {
      state.lastNarration = activity;
      byId('bc-narration').textContent = activity;
    }
    const age = ageLabel(session.updated_at);
    byId('bc-narration-age').textContent = age ? `updated ${age}` : '';

    renderStages(session);
    // A finding has no per-trial attempts — its trajectory stays hidden
    // rather than showing an empty chart over the shortlist ledger.
    renderTrajectory(finding ? [] : attempts);
    renderEquity();
    if (finding) renderShortlist(art);
    else renderLedger(attempts);
  }

  // ── Polling ─────────────────────────────────────────────────────────────

  async function post(path, body) {
    const response = await fetch(path, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
        // The server refuses mutations without this; see _mutation_allowed.
        'X-Research-Request': '1',
      },
      body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || payload.detail || `Request failed (${response.status})`);
    return payload;
  }

  async function get(path) {
    // Capped so a dead host cannot hang a poll forever (the board froze on
    // its initial DOM once) — but generous, because the first request
    // through a slow reverse proxy on a grinding host was measured at 7.7s, a 6s
    // cap turned that cold spike into a dark phone board, and a campaign
    // worker mid-evaluation can hold the API well past 12s (2026-08-16).
    // Polls are serialized, so a slow response only stretches the cadence.
    let signal;
    if (typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function') {
      signal = AbortSignal.timeout(REQUEST_TIMEOUT_MS);
    } else {
      // Older OBS/CEF builds have no AbortSignal.timeout; without this the
      // call throws on every poll and the board never leaves its first frame.
      const controller = new AbortController();
      window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
      signal = controller.signal;
    }
    const response = await fetch(path, { headers: { Accept: 'application/json' }, signal });
    if (!response.ok) throw new Error(`${response.status}`);
    return response.json();
  }

  let pollBusy = false;

  async function poll() {
    if (!state.on) return;
    // One poll in flight at a time: overlapping polls against a busy host
    // pile up and make the very slowness they are suffering from worse.
    if (pollBusy) return;
    pollBusy = true;
    try {
      // Each read fails independently — but the reason is KEPT. A bare
      // `.catch(() => null)` here hid a health read that was failing on every
      // single poll: the board said "Connecting…"
      // indefinitely while `lastPollError` stayed null, because only the
      // outer catch was recording anything (2026-08-16).
      const [health, list] = await Promise.all([
        get('api/health').catch((e) => { state.healthError = String(e && e.message || e).slice(0, 200); return null; }),
        get('api/sessions').catch((e) => { state.sessionsError = String(e && e.message || e).slice(0, 200); return null; }),
      ]);
      if (health) state.healthError = null;
      if (list) state.sessionsError = null;
      if (health) state.health = health;
      const auto = byId('bc-auto');
      if (auto && health) {
        // The pill is a toggle, so it stays on screen in BOTH states: lit
        // while the gateway accepts tasks, struck through when paused. Hidden
        // only until the first health read confirms the gateway exists at all.
        const ap = health.gateway || null;
        auto.hidden = !ap;
        const on = Boolean(ap && ap.enabled);
        auto.classList.toggle('is-off', Boolean(ap) && !on);
        auto.textContent = ap && ap.state ? `GATEWAY · ${String(ap.state).toUpperCase()}` : 'GATEWAY';
        auto.title = on
          ? 'Gateway accepting tasks — it runs assigned research and reports back. Click to pause intake.'
          : 'Gateway paused — assigned tasks wait. Click to resume.';
      }
      // A failed read is not an empty lab: keep showing the last known session
      // rather than blanking the stream on one dropped request. A pinned
      // review keeps the camera too: auto-follow resumes on "Back to live".
      if (list && !state.pinned) {
        const chosen = selectSession(list.sessions || []);
        state.sessionId = chosen?.id || state.sessionId;
      }
      if (state.sessionId) {
        const detail = await get(`api/sessions/${encodeURIComponent(state.sessionId)}`).catch(() => null);
        if (detail) state.session = detail;
      }
      render();
    } catch (error) {
      // The show keeps playing on the last good frame — but the error is
      // KEPT: an invisible failure in the poll loop cost hours of blind
      // debugging (2026-08-15).
      state.lastPollError = String(error && error.message || error).slice(0, 300);
    } finally {
      state.pollCount = (state.pollCount || 0) + 1;
      pollBusy = false;
    }
  }

  // ── Lifecycle ───────────────────────────────────────────────────────────

  function start() {
    if (state.on) return;
    state.on = true;
    document.body.classList.add('bc-on');
    if (!byId('broadcast')) build();
    byId('broadcast').hidden = false;
    // First paint must not show an empty loop strip: the chips are static
    // knowledge (the loop's shape), so they render before the first poll
    // answers — unlit until live state says where the loop actually is.
    renderLoop(null, null);
    try { window.localStorage.setItem('researchBroadcast', '1'); } catch (_e) { /* private mode */ }

    // Operator furniture reveals itself only to a pointer. A stream has no
    // cursor, so on camera the exit chip stays out of the frame it was
    // sitting on top of.
    if (!state.pointerBound) {
      state.pointerBound = true;
      const reveal = () => {
        document.body.classList.add('bc-pointer');
        window.clearTimeout(state.pointerTimer);
        state.pointerTimer = window.setTimeout(
          () => document.body.classList.remove('bc-pointer'), 2500,
        );
      };
      window.addEventListener('mousemove', reveal, { passive: true });
      // Touch reveals nothing: operator furniture is a fine-pointer affair,
      // and on a phone the board IS the page — a ghost chip over the header
      // on every tap is pure cost.
      if (window.matchMedia && window.matchMedia('(pointer: fine)').matches) {
        window.addEventListener('touchstart', reveal, { passive: true });
      }
    }

    state.timers.push(window.setInterval(poll, POLL_MS));
    state.timers.push(window.setInterval(() => {
      const node = byId('bc-clock');
      if (node) node.textContent = `${etClock()} ET`;
    }, 1000));
    state.timers.push(window.setInterval(() => {
      state.railIndex = (state.railIndex + 1) % RAIL_LINES.length;
      const node = byId('bc-rail-line');
      if (node) node.textContent = RAIL_LINES[state.railIndex];
    }, RAIL_MS));

    void poll();
  }

  function stop() {
    if (!state.on) return;
    state.on = false;
    state.timers.forEach((id) => window.clearInterval(id));
    state.timers = [];
    document.body.classList.remove('bc-on');
    const node = byId('broadcast');
    if (node) node.hidden = true;
    // Leaving must stick: a mode you cannot escape reopens on every reload.
    try { window.localStorage.removeItem('researchBroadcast'); } catch (_e) { /* private mode */ }
    if (window.location.search.includes('broadcast=')) {
      window.history.replaceState({}, '', window.location.pathname);
    }
  }

  window.addEventListener('keydown', (event) => {
    // Esc closes the review overlay first — it is the topmost thing open.
    if (event.key === 'Escape' && state.on) {
      const review = byId('bc-review');
      if (review && !review.hidden) { closeReview(); return; }
    }
    // Esc exits only when there is something to exit TO (operator chrome
    // underneath). In the only-UI shell it is a no-op.
    if (event.key === 'Escape' && state.on && !state.onlyUi) stop();
    // A quiet operator shortcut, so the stream can be armed without hunting
    // for a control that must not be visible to the audience.
    if (event.key.toLowerCase() === 'b' && (event.metaKey || event.ctrlKey) && event.shiftKey) {
      event.preventDefault();
      if (state.on) { if (!state.onlyUi) stop(); } else start();
    }
  });

  function boot() {
    const params = new URLSearchParams(window.location.search);
    const forced = params.get('broadcast');
    if (forced === '0') { try { window.localStorage.removeItem('researchBroadcast'); } catch (_e) { /* ignore */ } return; }
    let sticky = false;
    try { sticky = window.localStorage.getItem('researchBroadcast') === '1'; } catch (_e) { sticky = false; }
    // The board IS the UI when the page ships without the operator chrome:
    // the stripped shell has no #broadcast-pill (and nothing else), so start
    // immediately rather than waiting for a button that does not exist.
    // Remember HOW we booted: in this mode there is nothing to exit TO, so
    // the exit chip stays unbuilt and Esc is a no-op — stop() would reveal
    // an empty shell.
    state.onlyUi = !document.getElementById('broadcast-pill');
    if (forced === '1' || sticky || state.onlyUi) start();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();

  // The operator UI's button hooks in here; nothing else is exposed.
  window.ResearchBroadcast = {
    start,
    stop,
    /** Stream-ops X-ray: what the board believes right now. */
    debug: () => ({
      on: state.on,
      pollCount: state.pollCount || 0,
      lastPollError: state.lastPollError || null,
      healthError: state.healthError || null,
      sessionsError: state.sessionsError || null,
      sessionId: state.sessionId,
      hasSession: Boolean(state.session),
      sessionStatus: state.session ? state.session.status : null,
      healthReady: state.health ? state.health.research_ready : null,
    }),
  };
})();
