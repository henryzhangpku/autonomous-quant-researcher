/*
 * The legacy research board (static/broadcast.js) polls `api/health`,
 * `api/sessions` and `api/sessions/{id}` and posts to a handful of doors. It
 * was written against a Python server; here that server is the engine itself,
 * running in a Web Worker under Pyodide. This shim is the only glue: it
 * intercepts fetch() for relative `api/…` paths and answers them from the
 * worker. The board's code is untouched.
 *
 * This script must run BEFORE broadcast.js — it is loaded synchronously in
 * <head>, the board is deferred.
 */
(() => {
  'use strict';

  const PRODUCT = 'Autonomous Quant Researcher — browser demo';
  const worker = new Worker('worker.js', { type: 'module' });
  const pending = new Map();
  let seq = 0;
  let ready = false;
  let failed = false;
  let bootText = 'Starting…';

  worker.onerror = (event) => {
    // A worker that dies before its first status (a CDN that will not load,
    // a syntax error) must not leave the board saying "Starting…" forever.
    failed = true;
    bootText = `worker error: ${event.message || 'unknown'}${event.lineno ? ` (line ${event.lineno})` : ''}`;
    renderBanner();
  };

  worker.onmessage = (event) => {
    const message = event.data || {};
    if (message.type === 'status') {
      bootText = message.text;
      if (message.ready) {
        ready = true;
        const mission = document.getElementById('research-mission');
        if (mission && message.mission) mission.value = message.mission;
      }
      if (message.failed) failed = true;
      renderBanner();
      return;
    }
    if (message.type === 'tick' || message.type === 'log') {
      // Visible in DevTools; the board itself renders the ledger.
      console.debug('[demo]', message.detail || message.text);
      return;
    }
    const resolve = pending.get(message.id);
    if (resolve) {
      pending.delete(message.id);
      resolve(message.result);
    }
  };

  function call(method, path, body) {
    return new Promise((resolve) => {
      const id = ++seq;
      pending.set(id, resolve);
      worker.postMessage({ id, method, path, body });
    });
  }

  function jsonResponse(status, payload) {
    return new Response(JSON.stringify(payload), {
      status,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  function preBoot(method, path) {
    if (method === 'GET' && path === 'api/health') {
      return jsonResponse(200, {
        status: 'not_ready', research_ready: false, product: PRODUCT,
        access: 'runs entirely in your browser',
        model: { available: false, state: bootText },
        data: { ready: false, state: bootText },
        stages: { ready: false },
        opra_adapter: { available: false, state: 'unavailable' },
        session_count: 0,
        gateway: { enabled: false, state: failed ? 'failed' : 'booting', pending: [], reports: [], history: [] },
        research_programs: [],
      });
    }
    if (method === 'GET' && path === 'api/sessions') return jsonResponse(200, { sessions: [] });
    return jsonResponse(503, { error: bootText });
  }

  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    if (!/^api\//.test(url)) return nativeFetch(input, init);
    const options = init || {};
    const method = String(options.method || 'GET').toUpperCase();
    let body = null;
    if (options.body) {
      try { body = JSON.parse(options.body); } catch (_error) { body = null; }
    }
    if (!ready || failed) return preBoot(method, url);
    const raw = await call(method, url, body);
    let status = 500;
    let payload = { error: 'engine returned nothing' };
    try {
      [status, payload] = JSON.parse(raw);
    } catch (error) {
      payload = { error: `bad engine reply: ${error}` };
    }
    return jsonResponse(status, payload);
  };

  // ── A one-line banner the board does not own ────────────────────────────
  function renderBanner() {
    let node = document.getElementById('demo-banner');
    if (!node) {
      node = document.createElement('div');
      node.id = 'demo-banner';
      node.setAttribute('role', 'note');
      Object.assign(node.style, {
        position: 'fixed', left: '0', right: '0', bottom: '0', zIndex: '9999',
        font: '12px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif',
        color: '#e8e8e8', background: 'rgba(12,12,14,0.92)',
        borderTop: '1px solid rgba(255,255,255,0.12)',
        padding: '6px 12px', textAlign: 'center', letterSpacing: '0.01em',
      });
      document.body.appendChild(node);
    }
    const link = '<a href="https://github.com/henryzhangpku/autonomous-quant-researcher" style="color:#8fd3ff">source</a>';
    if (failed) {
      node.innerHTML = `Demo failed to start: ${escapeHtml(bootText)} · ${link}`;
    } else if (!ready) {
      node.innerHTML = `${escapeHtml(bootText)} · the real engine is loading into your browser · ${link}`;
    } else {
      node.innerHTML = 'Live demo: the real v3 engine running in your browser (Pyodide) on a '
        + '<b>synthetic tape</b> with a <b>scripted proposer</b>. Nothing here is market evidence. '
        + 'The holdout stays sealed. · ' + link;
    }
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', renderBanner);
  else renderBanner();
})();
