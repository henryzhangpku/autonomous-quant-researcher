/*
 * The demo's narrator. Two things the shared board cannot say because it was
 * written for an operator who already knows them:
 *
 *   1. While Pyodide loads (20-40 s on a first visit) the board reads
 *      "Standing by · ENGINE NOT READY", which a visitor reads as broken. The
 *      boot card says what is loading, how long, and what they are about to
 *      watch, then removes itself when the engine is ready.
 *
 *   2. The whole campaign runs in about a minute and then sits on its final
 *      state. A visitor arriving cold sees "NO EDGE — REFUTED" and a ledger
 *      of discards with no way to know that the refutation was the point:
 *      the synthetic tape has a regime planted ONLY in the discovery years,
 *      so discovery is meant to find it and validation is meant to kill it.
 *      The explainer under the narration says, per stage, what the loop is
 *      doing and why, and at the end what the result means.
 *
 * Read-only: it polls `api/sessions` through the same shim the board uses and
 * never posts. Nothing here touches the ledger, the gates or the holdout.
 */
(() => {
  'use strict';

  const POLL_MS = 4000;
  const TERMINAL = new Set([
    'holdout_ready', 'no_discovery_survivor', 'no_validation_survivor',
    'holdout_consumed', 'finding_ready', 'failed',
  ]);

  function esc(text) {
    return String(text).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  function score(value) {
    const n = Number(value);
    if (value === null || value === undefined || !Number.isFinite(n)) return '';
    return `${n >= 0 ? '+' : ''}${n.toFixed(2)}`;
  }

  // ── Boot card ───────────────────────────────────────────────────────────

  let bootNode = null;

  function renderBoot(boot) {
    if (!boot) return;
    if (boot.ready) {
      if (bootNode) { bootNode.remove(); bootNode = null; }
      return;
    }
    if (!bootNode) {
      bootNode = document.createElement('div');
      bootNode.className = 'demo-boot';
      bootNode.setAttribute('role', 'status');
      bootNode.innerHTML = `
        <div class="demo-boot-card">
          <h2>Loading the research engine into your browser</h2>
          <p class="demo-sub">Python 3.14 in WebAssembly (Pyodide). Usually 20–40 seconds on a first visit;
            there is no server, the loop runs on your machine. The full campaign then takes about a minute.</p>
          <div class="demo-status" id="demo-boot-status"><i></i><span>Starting…</span></div>
          <div class="demo-kicker">WHAT YOU ARE ABOUT TO WATCH</div>
          <ol>
            <li>A scripted proposer plays the LLM and submits <b>14 formulations</b>. Admission refuses four:
              a malformed one, one that smuggles code, an exact duplicate and a near duplicate. Every attempt is counted.</li>
            <li>The <b>10 admitted</b> hypotheses are priced, costed and gated on the discovery years of a synthetic
              tape, where a weak-open recovery was <b>deliberately planted</b>.</li>
            <li>The best survivor is <b>frozen</b> and re-run on later years, where the planted regime does not exist.</li>
            <li>Expect <b>NO EDGE — REFUTED</b>. That is the loop doing its job: a result that only exists in the data
              it was found in is the commonest failure in quant research, and the gates exist to catch it.</li>
          </ol>
          <p class="demo-fine">Synthetic tape, scripted proposer, <b>nothing here is market evidence</b>. The engine
            underneath is the real one; the final holdout stays sealed. Source on GitHub, linked at the bottom.</p>
        </div>`;
      document.body.appendChild(bootNode);
    }
    const status = bootNode.querySelector('#demo-boot-status');
    if (status) {
      status.classList.toggle('is-failed', !!boot.failed);
      status.lastElementChild.textContent = boot.failed
        ? `The demo failed to start: ${boot.text}`
        : (boot.text || 'Starting…');
    }
  }

  window.addEventListener('demo-boot', (event) => renderBoot(event.detail));
  if (window.__demoBoot) renderBoot(window.__demoBoot);

  // ── Stage explainer ─────────────────────────────────────────────────────

  function stageCopy(session) {
    const stage = String(session.stage || '');
    const status = String(session.status || '');
    const best = score(session.best_metric);
    const trials = Number(session.scientific_trials || 0);
    const formulations = Number(session.formulations || 0);

    if (status === 'failed') {
      return [
        `<p class="demo-verdict">The run stopped with a durable failure record.</p>`,
        `<p>${esc(session.error || 'The engine kept the error in the ledger rather than hiding it.')}</p>`,
        `<p class="demo-next">Run it again: + New research, bottom right.</p>`,
      ];
    }
    if (stage === 'ready' || (stage === 'discovery' && trials === 0)) {
      return [
        `<p><b>Discovery is starting.</b> The scripted proposer submits formulations one at a time, every few seconds.
          Watch the ledger on the right: the first rows arrive shortly.</p>`,
        `<p>Admission runs before any evaluation. A formulation that is malformed, that tries to smuggle code, or
          that duplicates an earlier one is refused and recorded as <b>failed</b>, never silently dropped.</p>`,
      ];
    }
    if (stage === 'discovery') {
      return [
        `<p><b>Discovery, trial ${trials} of 10</b> (${formulations} formulations so far).
          Each admitted hypothesis is priced with real options mechanics, charged trading costs, and held to a fixed
          gate set: enough trades, enough weeks, positive after costs, a bootstrap lower bound above zero.</p>`,
        `<p>Scores here are <b>exploratory</b>. The discovery years contain a planted weak-open recovery, so some
          candidates will look good${best ? ` (best so far ${esc(best)})` : ''}. Looking good in the data an idea was
          found in is not evidence.</p>`,
      ];
    }
    if (stage === 'shortlist_frozen' || stage === 'validation') {
      return [
        `<p><b>Discovery is closed; the shortlist is frozen.</b> The best candidate${best ? ` (${esc(best)} exploratory)` : ''}
          now re-runs on later years of the tape, where the planted regime does not exist.</p>`,
        `<p>Nothing can be tuned from here: the contract, the costs and the gates were fixed before the first result,
          and the candidate's own definition is hashed into the ledger.</p>`,
      ];
    }
    if (stage === 'no_validation_survivor' || stage === 'no_discovery_survivor') {
      const where = stage === 'no_discovery_survivor'
        ? 'No candidate cleared the discovery gates at all.'
        : `Discovery found the planted regime${best ? ` (best exploratory ${esc(best)})` : ''}. The frozen finalist then
           re-ran on years where that regime was never planted, and it failed the same gates.`;
      return [
        `<p class="demo-verdict">What just happened: ${where}</p>`,
        `<p>This is the loop doing its job. A result that holds only in the data it was found in is the commonest
          failure in quantitative research; the staged holdouts and the frozen gate set exist to catch it before any
          capital does. The final holdout was <b>never opened</b>: it is single-use and nothing earned it.</p>`,
        `<p>Every one of the ${formulations} formulations is still in the ledger, including the refusals, so a
          best-of-N result can never hide its trial count.</p>`,
        `<p class="demo-next">Run it again: + New research, bottom right. On real data, this is where most ideas die.</p>`,
      ];
    }
    if (stage === 'holdout_ready' || stage === 'holdout_consumed' || stage === 'finding_ready') {
      return [
        `<p class="demo-verdict">A finalist cleared the frozen validation gates.</p>`,
        `<p>Even so, this is a research finding, not a signal: the final holdout is single-use and is only opened
          deliberately, by a person, and a finding authorises no execution.</p>`,
      ];
    }
    return [];
  }

  function ensureExplainer() {
    let node = document.getElementById('demo-explainer');
    if (node) return node;
    const panel = document.querySelector('.bc-narration');
    if (!panel) return null;
    node = document.createElement('div');
    node.id = 'demo-explainer';
    node.className = 'demo-explainer';
    const age = panel.querySelector('#bc-narration-age');
    if (age) panel.insertBefore(node, age);
    else panel.appendChild(node);
    return node;
  }

  let lastKey = '';

  function renderExplainer(session) {
    const node = ensureExplainer();
    if (!node) return;
    if (!session) {
      node.innerHTML = '';
      lastKey = '';
      return;
    }
    const key = `${session.id}|${session.stage}|${session.status}|${session.scientific_trials}|${session.formulations}`;
    if (key === lastKey) return;
    lastKey = key;
    const parts = stageCopy(session);
    node.innerHTML = parts.length
      ? `<div class="bc-kicker">WHAT IS HAPPENING — DEMO NOTES</div>${parts.join('')}`
      : '';
  }

  function newest(sessions) {
    const rows = (Array.isArray(sessions) ? sessions : []).filter(Boolean);
    rows.sort((a, b) => Date.parse(b.updated_at || b.created_at || 0) - Date.parse(a.updated_at || a.created_at || 0));
    return rows.find((s) => !TERMINAL.has(String(s.stage))) || rows[0] || null;
  }

  async function poll() {
    try {
      const res = await fetch('api/sessions', { headers: { Accept: 'application/json' } });
      if (!res.ok) return;
      const payload = await res.json();
      renderExplainer(newest(payload.sessions));
    } catch (_error) {
      /* the board's own error handling speaks; the explainer just waits */
    }
  }

  function start() {
    void poll();
    setInterval(poll, POLL_MS);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
