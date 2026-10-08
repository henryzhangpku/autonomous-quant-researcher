/*
 * Renders demo/data/gpu-leads-revisions.json: the frozen pre-registration,
 * its ledger hashes, the live GPU index series and signal status, and the
 * data-requirements checklist. Every string from the snapshot goes in through
 * textContent. There are no metrics to render; the campaign has refused.
 */
(() => {
  'use strict';
  const SVG = 'http://www.w3.org/2000/svg';
  const $ = (id) => document.getElementById(id);

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === 'class') node.className = value;
      else node.setAttribute(key, value);
    }
    for (const child of children) {
      if (child == null) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function svg(tag, attrs = {}) {
    const node = document.createElementNS(SVG, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    return node;
  }

  const fmtDate = (iso) => new Date(`${iso}T12:00:00Z`).toLocaleDateString('en-US',
    { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
  const fmtShort = (iso) => new Date(`${iso}T12:00:00Z`).toLocaleDateString('en-US',
    { month: 'short', day: 'numeric', timeZone: 'UTC' });
  const usd = (v) => `$${v.toFixed(2)}`;
  const day = (iso) => Date.parse(`${iso}T00:00:00Z`) / 86400000;

  function list(target, items) {
    const node = $(target);
    for (const item of items) node.append(el('li', {}, item));
  }

  function table(target, head, rows) {
    const node = $(target);
    const tr = el('tr');
    for (const h of head) tr.append(el('th', { scope: 'col' }, h));
    node.append(el('thead', {}, tr));
    const body = el('tbody');
    for (const row of rows) {
      const line = el('tr');
      for (const cell of row) line.append(cell instanceof Node && cell.tagName === 'TD' ? cell : el('td', {}, cell));
      body.append(line);
    }
    node.append(body);
  }

  // ── Section 1: the ledger ────────────────────────────────────────────────
  function renderLedger(pre) {
    const dl = $('cp-hashes');
    const add = (label, value, lead) => {
      dl.append(el('dt', {}, label), el('dd', lead ? { class: 'cp-lead' } : {}, value));
    };
    add('Ledger event hash', pre.ledger_event_hash, true);
    add('Frozen at (UTC)', pre.frozen_at.replace('T', ' ').replace(/\.\d+/, ''));
    add('preregistration.json', pre.spec_sha256);
    add('PREREGISTRATION.md', pre.document_sha256);
    for (const [path, hash] of Object.entries(pre.code_sha256)) add(path.replace('research/prereg/', ''), hash);
  }

  // ── Section 2: one small multiple per GPU ────────────────────────────────
  const tip = () => $('cp-tip');

  function chart(code, block) {
    const W = 300, H = 130, L = 34, R = 8, T = 10, B = 20;
    const pts = block.points.map(([d, v, ver]) => ({ d, v, ver, x: day(d) }));
    const node = svg('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img',
      'aria-label': `${code} rental index, ${pts.length} published session fixings` });
    if (!pts.length) return node;
    const current = pts[pts.length - 1].ver;
    const x0 = pts[0].x, x1 = pts[pts.length - 1].x;
    let lo = Math.min(...pts.map((p) => p.v)), hi = Math.max(...pts.map((p) => p.v));
    const pad = (hi - lo) * 0.12 || 0.1; lo -= pad; hi += pad;
    const sx = (x) => L + ((x - x0) / Math.max(1, x1 - x0)) * (W - L - R);
    const sy = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);

    for (const v of [lo + pad, hi - pad]) {
      node.append(svg('line', { x1: L, x2: W - R, y1: sy(v), y2: sy(v), stroke: '#1b212e', 'stroke-width': 1 }));
      const label = svg('text', { x: L - 4, y: sy(v) + 3.5, 'text-anchor': 'end', 'font-size': 9, fill: '#6b7a93' });
      label.textContent = usd(v); node.append(label);
    }
    for (const [p, anchor] of [[pts[0], 'start'], [pts[pts.length - 1], 'end']]) {
      const label = svg('text', { x: sx(p.x), y: H - 5, 'text-anchor': anchor, 'font-size': 9, fill: '#6b7a93' });
      label.textContent = fmtShort(p.d); node.append(label);
    }
    // Segments break at a missing stretch (more than a weekend) and at a
    // methodology change, so a gap reads as a gap, never as a straight line.
    let seg = [];
    const flush = () => {
      if (!seg.length) return;
      const prior = seg[0].ver !== current;
      const d = seg.map((p, i) => `${i ? 'L' : 'M'}${sx(p.x).toFixed(1)},${sy(p.v).toFixed(1)}`).join('');
      node.append(svg('path', { d, fill: 'none', stroke: prior ? '#4a5568' : '#c7841f', 'stroke-width': 2,
        'stroke-linejoin': 'round', 'stroke-linecap': 'round', ...(prior ? { 'stroke-dasharray': '4 3' } : {}) }));
      if (seg.length === 1) node.append(svg('circle', { cx: sx(seg[0].x), cy: sy(seg[0].v), r: 2.5,
        fill: prior ? '#4a5568' : '#c7841f' }));
      seg = [];
    };
    pts.forEach((p, i) => {
      if (i && (p.x - pts[i - 1].x > 4 || p.ver !== pts[i - 1].ver)) flush();
      seg.push(p);
    });
    flush();
    const change = pts.find((p) => p.ver === current);
    if (change && change !== pts[0]) {
      node.append(svg('line', { x1: sx(change.x), x2: sx(change.x), y1: T, y2: H - B, stroke: '#93a1b8',
        'stroke-width': 1, 'stroke-dasharray': '2 3' }));
      const label = svg('text', { x: sx(change.x) + 3, y: T + 8, 'font-size': 9, fill: '#93a1b8' });
      label.textContent = `v${current}`; node.append(label);
    }
    const last = pts[pts.length - 1];
    node.append(svg('circle', { cx: sx(last.x), cy: sy(last.v), r: 4, fill: '#c7841f', stroke: '#090c12', 'stroke-width': 2 }));

    // Hover: a crosshair snapping to the nearest published session.
    const cross = svg('line', { y1: T, y2: H - B, stroke: '#f2f5fa', 'stroke-width': 1, opacity: 0 });
    const dot = svg('circle', { r: 4, fill: '#f2f5fa', opacity: 0 });
    const hit = svg('rect', { x: L, y: 0, width: W - L - R, height: H, fill: 'transparent', tabindex: 0 });
    node.append(cross, dot, hit);
    let focusIndex = pts.length - 1;
    const show = (p, cx, cy) => {
      cross.setAttribute('x1', sx(p.x)); cross.setAttribute('x2', sx(p.x)); cross.setAttribute('opacity', 0.5);
      dot.setAttribute('cx', sx(p.x)); dot.setAttribute('cy', sy(p.v)); dot.setAttribute('opacity', 1);
      const t = tip();
      t.replaceChildren(el('b', {}, `${usd(p.v)} / GPU-hour`), el('span', {}, `${code} · ${fmtDate(p.d)} · methodology ${p.ver}`));
      t.hidden = false;
      t.style.left = `${Math.min(cx + 14, window.innerWidth - t.offsetWidth - 8)}px`;
      t.style.top = `${cy - t.offsetHeight - 10}px`;
    };
    const hide = () => { cross.setAttribute('opacity', 0); dot.setAttribute('opacity', 0); tip().hidden = true; };
    const nearest = (clientX) => {
      const box = node.getBoundingClientRect();
      const xv = x0 + ((clientX - box.left) / box.width * W - L) / (W - L - R) * (x1 - x0);
      let best = 0;
      pts.forEach((p, i) => { if (Math.abs(p.x - xv) < Math.abs(pts[best].x - xv)) best = i; });
      return best;
    };
    hit.addEventListener('pointermove', (e) => { focusIndex = nearest(e.clientX); show(pts[focusIndex], e.clientX, e.clientY); });
    hit.addEventListener('pointerleave', hide);
    hit.addEventListener('blur', hide);
    const keyShow = () => {
      const box = node.getBoundingClientRect();
      const p = pts[focusIndex];
      show(p, box.left + (sx(p.x) / W) * box.width, box.top + (sy(p.v) / H) * box.height);
    };
    hit.addEventListener('focus', keyShow);
    hit.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowLeft') focusIndex = Math.max(0, focusIndex - 1);
      else if (e.key === 'ArrowRight') focusIndex = Math.min(pts.length - 1, focusIndex + 1);
      else return;
      e.preventDefault(); keyShow();
    });
    return node;
  }

  function renderFeed(data) {
    const series = data.series;
    const any = series.H100;
    const firstCurrent = any.points.find((p) => p[2] === any.signal.methodology_version);
    $('cp-feed-note').textContent =
      `Published daily values from the public GPU rental price index, read point-in-time, session dates only. ` +
      `The 3-month signal needs ${any.signal.required_sessions} sessions under one methodology version; ` +
      `version ${any.signal.methodology_version} began ${firstCurrent ? fmtDate(firstCurrent[0]) : 'recently'}, ` +
      `so the clock started there. Dashed grey is the earlier version, which the index does not splice across.`;
    const wrap = $('cp-multiples');
    for (const code of ['H100', 'H200', 'B200']) {
      const block = series[code];
      const s = block.signal;
      const last = block.points[block.points.length - 1];
      const role = code === 'H100' ? 'primary signal' : 'independent check';
      const share = Math.min(1, s.history_sessions / s.required_sessions);
      const bar = el('div', { class: 'cp-progress', role: 'progressbar', 'aria-valuemin': 0,
        'aria-valuemax': s.required_sessions, 'aria-valuenow': s.history_sessions,
        'aria-label': `${code} history toward the 3-month signal` }, el('span'));
      bar.firstChild.style.width = `${(share * 100).toFixed(1)}%`;
      const status = el('p', { class: 'cp-status' },
        el('b', {}, s.status === 'live' ? 'Signal live' : s.message.replace(/^./, (c) => c.toUpperCase())),
        ` · ${s.history_sessions} of ${s.required_sessions} sessions`);
      wrap.append(el('article', { class: 'cp-card' },
        el('header', {}, el('b', {}, `${code} · ${role}`),
          el('span', { class: 'cp-last' }, last ? usd(last[1]) : '–', el('small', {}, ' /GPU-h'))),
        chart(code, block), status, bar,
        el('p', { class: 'cp-eta' }, s.earliest_live_session
          ? `Earliest possible live date: ${fmtDate(s.earliest_live_session)}, if every session from here publishes.`
          : (s.value != null ? `3-month change: ${(s.value * 100).toFixed(1)}%` : ''))));
    }
    wrap.after(el('div', { class: 'cp-legend' },
      el('span', {}, el('i'), 'current methodology'),
      el('span', {}, el('i', { class: 'prior' }), 'earlier methodology (not usable for the signal)')));
    const rows = [];
    const dates = [...new Set(['H100', 'H200', 'B200'].flatMap((c) => series[c].points.map((p) => p[0])))].sort().reverse();
    for (const d of dates) {
      const cells = [fmtDate(d)];
      for (const code of ['H100', 'H200', 'B200']) {
        const p = series[code].points.find((q) => q[0] === d);
        cells.push(el('td', { class: 'num' }, p ? usd(p[1]) : 'not published'));
      }
      cells.push(series.H100.points.find((q) => q[0] === d)?.[2] ?? '');
      rows.push(cells);
    }
    table('cp-fixings', ['Session', 'H100', 'H200', 'B200', 'Methodology'], rows);
  }

  // ── Section 3: the requirements ──────────────────────────────────────────
  const STATE = { met: 'MET', partial: 'PARTIAL', missing: 'MISSING', not_licensed: 'NOT LICENSED' };
  function reqRows(items) {
    return items.map((item) => {
      const share = item.required ? Math.min(1, item.available / item.required) : 0;
      const bar = el('span', { class: 'cp-bar', 'aria-hidden': 'true' }, el('span'));
      bar.firstChild.style.width = `${(share * 100).toFixed(1)}%`;
      return [
        el('td', {}, item.description, el('span', { class: 'cp-detail' }, item.detail)),
        item.source,
        el('td', { class: 'num' }, `${item.available.toLocaleString()} / ${item.required.toLocaleString()} ${item.unit}`, bar),
        el('td', {}, el('span', { class: `cp-badge ${item.state}` }, STATE[item.state] || item.state)),
      ];
    });
  }

  function renderRequirements(data) {
    table('cp-requirements', ['Requirement', 'Source', 'Have / need', 'State'], reqRows(data.campaign_1.unmet));
    table('cp-requirements-2', ['Requirement', 'Source', 'Have / need', 'State'], reqRows(data.campaign_2.unmet));
    $('cp-providers').textContent = 'Licensed providers declare their schemas and refuse: ' + data.providers
      .map((p) => `${p.product} (${p.reason})`).join('; ') + '. No row is ever substituted.';
  }

  // ── Section 4: the rules ─────────────────────────────────────────────────
  function renderRules(spec) {
    $('cp-hypothesis').textContent = spec.hypothesis;
    const sig = spec.signal;
    $('cp-signal').textContent = `${sig.primary}: ${sig.definition}. ${sig.checks.join(' and ')} are an independent check in the holdout. ` +
      `Position: ${sig.position}.`;
    list('cp-signal-rules', [...sig.observation_rules, `Timing: ${sig.lag}.`]);
    list('cp-basket', [
      `Suppliers and neoclouds: ${spec.baskets.suppliers_and_neoclouds.join(', ')}`,
      `Hyperscalers (capex confirmation): ${spec.baskets.hyperscalers.join(', ')}`,
      `Returns benchmark (campaign 2): ${spec.baskets.returns_benchmark}`,
    ]);
    const o = spec.campaign_1.primary_outcome;
    $('cp-outcome').textContent = `Campaign 1: the ${o.horizon_weeks}-week change in consensus ${o.fiscal_period} ${o.measure}, ${o.per_name}; ${o.basket_value}. ` +
      `Campaign 2, only after a campaign 1 PASS: ${spec.campaign_2.outcome}.`;
    $('cp-capture').textContent = `capture = ${spec.campaign_1.capture}`;
    const st = spec.stages;
    table('cp-stages', ['Stage', 'Decision weeks', 'Signal source'], [
      ['Discovery', `${st.discovery.start} to ${st.discovery.end}`, 'licensed H100 history'],
      ['Validation', `${st.validation.start} to ${st.validation.end}`, 'licensed H100 history'],
      ['Embargo', `${st.embargo.start} to ${st.embargo.end}`, 'not read'],
      ['Holdout', `from ${st.holdout.start}, ${st.holdout.closes_after_evaluable_weeks} evaluable weeks`, 'public index, point-in-time'],
    ]);
    list('cp-gates', spec.gates.every_stage);
    const f = spec.gates.sample_floor;
    $('cp-floor').textContent = `Floor: ${f.min_weeks.discovery} evaluable weeks per stage, each signal state in at least ` +
      `${f.min_state_share * 100}% of them; below it a stage is ${spec.gates.below_floor}. ${spec.gates.holdout_checks}.`;
    list('cp-decision', spec.decision_rule);
    const future = $('cp-future');
    for (const src of spec.future_sources_not_in_this_campaign) {
      const links = el('p', {});
      src.sources.forEach((url, i) => { if (i) links.append(' · '); links.append(el('a', { href: url, rel: 'noopener' }, new URL(url).hostname + new URL(url).pathname.replace(/^(.{0,40}).*$/, '$1…'))); });
      future.append(el('p', {}, el('b', {}, src.name), ` — ${src.status}. ${src.use}`), links);
    }
    $('cp-cannot').textContent = spec.what_this_cannot_say;
  }

  function renderVerdict(data) {
    $('cp-verdict-title').textContent = 'Refusing to evaluate: the data has not landed';
    const n = data.campaign_1.unmet.length;
    $('cp-verdict-text').textContent = `${n} of ${n} campaign 1 requirements are unmet, so there is no result, by design. ` +
      `Campaign 2 is sealed until campaign 1 passes. When every requirement is met the campaign is opened once, judged on the rules below, and the verdict is appended to the same ledger.`;
  }

  async function main() {
    let data;
    try {
      const res = await fetch('data/gpu-leads-revisions.json', { cache: 'no-cache' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      data = await res.json();
    } catch (err) {
      $('cp-verdict-title').textContent = 'The snapshot could not be loaded';
      $('cp-verdict-text').textContent = String(err);
      return;
    }
    renderRules(data.spec);
    renderVerdict(data);
    renderLedger(data.preregistration);
    renderFeed(data);
    renderRequirements(data);
    $('cp-generated').textContent = `Snapshot generated ${data.generated_at.replace('T', ' ').replace('+00:00', ' UTC')} ` +
      `for session ${data.as_of_session}, from ${data.tape.source} (sha256 ${data.tape.sha256.slice(0, 16)}…).`;
  }

  main();
})();
