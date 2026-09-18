const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const pretty = (o) => esc(JSON.stringify(o, null, 2));

function table(head, rows) {
  const th = head.map((h) => `<th class="${h.n ? 'n' : ''}">${esc(h.t ?? h)}</th>`).join('');
  const tr = rows.map((r) => '<tr>' + r.map((c) =>
    `<td class="${typeof c === 'object' && c.n ? 'n' : ''}">${esc(typeof c === 'object' ? c.t : c)}</td>`
  ).join('') + '</tr>').join('');
  return `<div class="card" style="overflow-x:auto"><table><thead><tr>${th}</tr></thead><tbody>${tr}</tbody></table></div>`;
}

function headline(real) {
  if (!real) return '<p class="note">No controlled database was present when this page was captured.</p>';
  const d = real.headline.dataset;
  const blocks = real.headline.queries.map((q) => {
    const measured = q.groups.some((g) => 'measured' in g);
    const rate = q.aggregate === 'mortality_rate' || q.title.includes('mortality');
    const head = ['', {t: 'stays', n: 1}, {t: rate ? 'died in hospital' : 'mean days', n: 1}];
    if (measured) head.push({t: 'with a value', n: 1});
    const rows = q.groups.map((g) => {
      const shown = rate && typeof g.value === 'number'
        ? (g.value * 100).toFixed(1) + '%' : (g.value ?? '—');
      const row = [Object.values(g.key)[0], {t: (g.n ?? '—').toLocaleString(), n: 1},
                   {t: shown, n: 1}];
      // Where a measure is missing for some records, say how many carried one.
      if (measured) row.push({t: (g.measured ?? g.n ?? 0).toLocaleString(), n: 1});
      return row;
    });
    return `<h3 style="font-size:16px;margin:26px 0 10px;font-weight:600">${esc(q.title)}
      <span class="tag" style="margin-left:8px">${q.cohort_size.toLocaleString()} ICU stays</span></h3>`
      + table(head, rows);
  }).join('');
  return `<p class="note" style="margin:0 0 4px"><span class="tag">computed here</span>
    &nbsp;${esc(d.name)} · ${d.icu_stays.toLocaleString()} ICU stays ·
    ${d.synthetic === false ? 'real de-identified records' : 'synthetic'} ·
    not served over the relay</p>${blocks}`;
}

function walk(gated) {
  return gated.steps.map((s, i) => {
    const ok = s.reply.ok === true;
    const msg = ok ? '' : `<div class="msg">${esc(s.reply.error ?? '')}</div>`;
    return `<details class="step">
      <summary>
        <span class="badge ${ok ? 'yes' : 'no'}">${ok ? 'allowed' : 'refused'}</span>
        <span><span class="title">${i + 1}. ${esc(s.title)}</span>
          <div class="why">${esc(s.explains)}</div>${msg}</span>
      </summary>
      <div class="two">
        <div><div class="sum" style="border-bottom:1px solid var(--rule)">sent</div>
          <pre>${pretty(s.request)}</pre></div>
        <div><div class="sum" style="border-bottom:1px solid var(--rule)">received</div>
          <pre>${pretty(s.reply)}</pre></div>
      </div></details>`;
  }).join('');
}

function differencing(d) {
  if (!d) return '<p class="note">Not captured.</p>';
  const q = (x) => x.cohort.map((c) => `${c.field} ${c.op} ${Array.isArray(c.value) ? `(${c.value.length} values)` : JSON.stringify(c.value)}`).join(', ');
  return `<div class="card">
    <div class="sum"><span>Question one — ${esc(q(d.first.query))}</span><b>${d.first.cohort_size.toLocaleString()}</b>
      <span class="badge yes">allowed</span></div>
    <div class="sum"><span>Question two — the same, minus one ${esc(d.dimension)}</span><b>${d.second.cohort_size.toLocaleString()}</b>
      <span class="badge no">refused</span></div>
    <div class="arith">${d.first.cohort_size.toLocaleString()} &minus; ${d.second.cohort_size.toLocaleString()}
      = <span class="r">${d.difference}</span>
      <div class="note" style="margin-top:10px">
        ${d.difference === 1 ? 'One patient.' : `${d.difference} patients.`}
        The excluded category, <code>${esc(d.hidden_category)}</code>, holds
        ${d.hidden_size} — below the minimum cell size of ${d.minimum_cell_size},
        so it could never be reported directly.</div></div>
    <pre style="border-top:1px solid var(--rule)">${esc(d.refusal ?? 'not refused')}</pre></div>
  <p class="note" style="margin-top:16px">Every gate said yes. The requester is entitled to
  ask both questions. Zerzura remembers which records answered the first, and refuses any
  request whose difference from it would describe individuals.</p>`;
}

function contrast(c) {
  if (!c) return '';
  const cards = ['public', 'controlled'].map((k) => {
    const d = c.datasets[k], groups = d.result.groups;
    const rows = groups.map((g) => [
      Object.values(g.key)[0],
      {t: g.n === null ? 'withheld' : g.n.toLocaleString(), n: 1},
    ]);
    const hidden = groups.filter((g) => 'suppressed' in g).length;
    return `<div><div class="sum"><span class="badge ${k === 'public' ? 'yes' : 'no'}">${k}</span>
      <span>${esc(d.policy.disclosure_control)}</span></div>
      ${table(['care unit', {t: 'stays', n: 1}], rows)}
      <p class="note" style="margin:10px 2px 0">${hidden ? `${hidden} of ${groups.length} withheld` : 'all reported'}</p></div>`;
  }).join('');
  return `<div class="two">${cards}</div>
    <p class="note" style="margin-top:18px">Identical rows, identical query. The left-hand
    dataset declares itself openly licensed, so nothing is suppressed — including a unit
    holding a single patient, whose record anyone can already download. The right-hand copy
    declares nothing, and is therefore treated as controlled. A database with no declaration
    at all — which is what a credentialed one looks like — gets the strict treatment
    automatically.</p>`;
}

const LIMITS = [
  ['The requester is who they claim.', 'What is established is that an issuer asserted something about a subject, and that the requester holds the key named in it. No external registry is consulted.'],
  ['The issuer keys are beyond doubt.', 'They arrive over the same relay that carries the messages. Trusting them means trusting the relay, unless they are pinned out of band.'],
  ['This is differential privacy.', 'It is not. Cell suppression and the composition ledger close specific, named attacks. No formal guarantee is offered.'],
  ['Collusion is prevented.', 'The composition ledger is per subject. Two requesters who share answers each stay within their own. That gap is addressed by a signed undertaking, which is a promise rather than a mechanism.'],
  ['The controlled data is served.', 'It is not. Results above marked as computed here were calculated locally. The relay exchanges use an openly licensed extract.'],
];

fetch('./evidence.json').then((r) => r.json()).then((e) => {
  $('headline-out').innerHTML = headline(e.real);
  $('walk-out').innerHTML = walk(e.gated);
  $('diff-out').innerHTML = differencing(e.real && e.real.differencing);
  $('contrast-out').innerHTML = contrast(e.policy_contrast);
  $('limits-out').innerHTML = LIMITS.map(([a, b]) => `<li><b>${esc(a)}</b> ${esc(b)}</li>`).join('');
  const live = e.live ? Object.keys(e.live).length : 0;
  $('prov').innerHTML = `Captured ${esc(e.captured.slice(0, 19).replace('T', ' '))} UTC by
    <code>examples/demo_capture.py</code>, which regenerates everything on this page.
    ${live} open operations were recorded against the public relay; the gated exchanges ran
    over a relay, worker and demonstration issuer started by that script, so they reproduce
    anywhere. The demonstration issuer's public key is
    <code>${esc(e.demonstration_issuer.publicKey)}</code> — no town trusts it by default.`;
}).catch((err) => {
  document.querySelectorAll('[id$="-out"]').forEach((n) => {
    n.innerHTML = `<div class="err">Could not load evidence.json: ${esc(err)}</div>`;
  });
});
