const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const pretty = (o) => esc(JSON.stringify(o, null, 2));
const num = (n) => (n === null || n === undefined ? '—' : n.toLocaleString());
const pct = (v) => (typeof v === 'number' ? (v * 100).toFixed(1) + '%' : '—');
const unit = (g) => Object.values(g.key)[0];

function slide(eyebrow, heading, sub, body, big) {
  return `<section class="slide">
    <div class="eyebrow">${esc(eyebrow)}</div>
    ${big ? `<h1>${heading}</h1>` : `<h2>${heading}</h2>`}
    ${sub ? `<p class="sub">${sub}</p>` : ''}
    <div class="body">${body}</div></section>`;
}

/* ---------- 1. title ---------- */
const title = (e) => slide(
  'Academic Wasteland · a research town',
  'Authorised is not the same as&nbsp;allowed.',
  `Zerzura answers statistical questions about intensive-care patients for people who can
   prove they may ask, and never releases a record. What follows is a requester getting
   all the way in — and being refused anyway.`,
  `<div class="card"><div class="head"><span>captured</span><b>${esc(e.captured.slice(0,16).replace('T',' '))} UTC</b>
     <span>·</span><span>every figure and message here was produced by
     <code>examples/demo_capture.py</code>, not written by hand</span></div></div>
   <p class="note">Two datasets appear. An openly licensed extract of MIMIC-IV, which this
   town serves over the relay; and the full credentialed database, which it does not. The
   difference between them turns out to be the whole point.</p>`, true);

/* ---------- 2. what it answers ---------- */
function answers(e) {
  const q = e.real.headline.queries[0];
  const rows = q.groups.map((g) => `<tr><td>${esc(unit(g))}</td>
    <td class="n">${num(g.n)}</td><td class="n">${pct(g.value)}</td></tr>`).join('');
  return slide('01 · what it answers',
    'Ninety-four thousand intensive-care stays.',
    `In-hospital mortality by admission type, over the full MIMIC-IV database. Real
     de-identified records from Beth Israel Deaconess. Computed here, and not served to
     anyone over the relay.`,
    `<div class="card"><table><thead><tr><th>admission type</th><th class="n">ICU stays</th>
       <th class="n">died in hospital</th></tr></thead><tbody>${rows}</tbody></table></div>
     <p class="note">Emergency admissions die at seven times the rate of same-day surgical
     ones. That is the kind of question the service exists to answer — and the kind that
     needs answering without handing anybody the records.</p>`);
}

/* ---------- 3. two datasets, two policies ---------- */
function twoDatasets(e) {
  const row = e.real.comparison.find((r) => r.query.aggregate === 'count');
  const side = (label) => {
    const s = row.sides[label];
    const rows = s.result.groups.map((g) => `<tr><td>${esc(unit(g)).slice(0, 34)}</td>${
      'suppressed' in g ? '<td class="hide">withheld</td>'
                        : `<td class="n">${num(g.n)}</td>`}</tr>`).join('');
    return `<div class="card">
      <div class="head"><span class="tag ${label === 'demo' ? 'yes' : 'no'}">${label === 'demo' ? 'served publicly' : 'not served'}</span>
        <b>${num(s.dataset.icu_stays)}</b><span>stays</span><span>·</span>
        <span>${esc(s.policy.disclosure_control)}</span></div>
      <table><tbody>${rows}</tbody></table></div>`;
  };
  return slide('02 · two datasets',
    'The same question, asked of both.',
    `ICU stays by care unit. On the left, the openly licensed extract — 140 stays, and
     nothing suppressed, because anyone can download those rows already. On the right, the
     full database, where three units hold too few patients to report.`,
    `<div class="two">${side('demo')}${side('controlled')}</div>
     <p class="note">Disclosure control is a property of the data, not a setting of the
     town. A database that declares itself openly licensed receives none; anything that
     declares nothing — which is what a credentialed database looks like — receives all of
     it, without anyone remembering to configure that.</p>`);
}

/* ---------- 4. the extract is not a research resource ---------- */
function misleading(e) {
  const row = e.real.comparison.find((r) => r.query.aggregate === 'mortality_rate');
  const demo = new Map(row.sides.demo.result.groups.map((g) => [unit(g), g]));
  const rows = row.sides.controlled.result.groups.map((g) => {
    const d = demo.get(unit(g));
    return `<tr><td>${esc(unit(g)).slice(0, 30)}</td>
      <td class="n ${d ? '' : 'off'}">${d ? pct(d.value) : 'absent'}</td>
      <td class="n off">${d ? num(d.n) : '—'}</td>
      <td class="n">${pct(g.value)}</td><td class="n off">${num(g.n)}</td></tr>`;
  }).join('');
  return slide('03 · and why that matters',
    'The public extract cannot be trusted for anything.',
    `The same mortality question, side by side. The extract puts elective mortality at zero
     — from five stays. Three admission types do not appear in it at all.`,
    `<div class="card"><table><thead><tr><th>admission type</th>
       <th class="n">extract</th><th class="n">n</th>
       <th class="n">full</th><th class="n">n</th></tr></thead><tbody>${rows}</tbody></table></div>
     <p class="note">So the dataset that is safe to publish is the one with no scientific
     value, and the dataset worth asking about is the one that cannot be handed over. That
     gap is the reason a service like this exists at all, rather than a download link.</p>`);
}

/* ---------- 5. the walkthrough ---------- */
function walkthrough(e) {
  const steps = e.gated.steps;
  const ticks = steps.map((s, i) =>
    `<button class="tick ${s.reply.ok ? 'yes' : 'no'}" data-step="${i}"
      aria-label="Exchange ${i + 1}"></button>`).join('');
  return slide('04 · getting in',
    'Eight exchanges between two towns.',
    `Every way a request is refused, and the one way it succeeds. Real messages over a
     relay; the panel below shows what actually crossed the wire.`,
    `<div class="ticks" id="ticks">${ticks}</div>
     <div id="exchange"></div>`);
}

function exchange(s, i, total) {
  return `<div class="exchange">
    <div class="bar">
      <span class="pill ${s.reply.ok ? 'yes' : 'no'}">${s.reply.ok ? 'allowed' : 'refused'}</span>
      <span><span class="t">${i + 1}/${total} &nbsp;${esc(s.title)}</span>
        <div class="w">${esc(s.explains)}</div>
        ${s.reply.ok ? '' : `<div class="m">${esc(s.reply.error ?? '')}</div>`}</span>
    </div>
    <div class="two" style="gap:0">
      <div><div class="head" style="border-right:1px solid var(--rule)">sent</div>
        <pre>${pretty(s.request)}</pre></div>
      <div><div class="head">received</div><pre>${pretty(s.reply)}</pre></div>
    </div></div>`;
}

/* ---------- 6. differencing ---------- */
function differencing(e) {
  const d = e.real.differencing;
  const cohort = (q) => q.cohort.map((c) =>
    `${c.field} ${c.op} ${Array.isArray(c.value) ? `(${c.value.length} values)` : JSON.stringify(c.value)}`).join(', ');
  return slide('05 · one question too many',
    'Every gate said yes. The answer is still no.',
    `The requester is now fully admitted: credential verified, key possession proven,
     undertaking signed. They ask two more questions about the full database. Each is
     perfectly legal on its own.`,
    `<div class="card">
       <div class="head"><span class="tag yes">allowed</span>
         <span>${esc(cohort(d.first.query))}</span><b>${num(d.first.cohort_size)}</b></div>
       <div class="head"><span class="tag no">refused</span>
         <span>the same, minus one ${esc(d.dimension)}</span><b>${num(d.second.cohort_size)}</b></div>
       <div class="arith">${num(d.first.cohort_size)} &minus; ${num(d.second.cohort_size)}
         = <span class="r">${d.difference}</span></div>
       <pre>${esc(d.refusal ?? '')}</pre></div>
     <p class="note">${d.difference === 1 ? 'One patient.' : d.difference + ' patients.'}
     The excluded unit, <code>${esc(d.hidden_category)}</code>, holds ${d.hidden_size} —
     below the minimum cell size of ${d.minimum_cell_size}, so it could never be reported
     directly. Zerzura remembers which records answered the first question and refuses any
     request whose difference from it would describe individuals. No permission system
     catches this, because it is not a property of the requester.</p>`);
}

/* ---------- 7. limits ---------- */
const LIMITS = [
  ['The requester is who they claim.', 'What is established is that an issuer asserted something about a subject, and that the requester holds the key named in it. No external registry is consulted.'],
  ['The issuer keys are beyond doubt.', 'They arrive over the same relay that carries the messages. Trusting them means trusting the relay, unless pinned out of band.'],
  ['This is differential privacy.', 'It is not. Cell suppression and the composition ledger close specific, named attacks. No formal guarantee is offered.'],
  ['Collusion is prevented.', 'The ledger is per subject. Two requesters who share answers each stay within their own. That gap is closed by a signed undertaking, which is a promise rather than a mechanism.'],
  ['The full database is served.', 'It is not. Every figure drawn from it was computed locally. The relay exchanges use the openly licensed extract.'],
];

function limits(e) {
  const items = LIMITS.map(([a, b]) => `<li><b>${esc(a)}</b> ${esc(b)}</li>`).join('');
  return slide('06 · what this does not establish',
    'The parts that are not solved.',
    'A demonstration that lists only its strengths is advertising.',
    `<ul class="plain">${items}</ul>
     <p class="note">The demonstration issuer whose signatures appear in the transcript is
     <code>${esc(e.demonstration_issuer.publicKey)}</code>. It is accredited by nobody and
     no town trusts it by default — naming it is a deliberate act, which is the whole
     design. Full reasoning in the
     <a href="https://github.com/micheldumontier/zerzura/blob/main/docs/data-access-policy.md">data access policy</a>;
     the underlying <a href="./evidence.json">evidence file</a> is what this page renders.</p>`);
}

/* ---------- deck ---------- */
const SLIDE_MS = 11000, STEP_MS = 4200;
let deck = [], at = 0, sub = 0, subs = 0, playing = true, timer = null, started = 0;

function render(e) {
  const parts = [title(e)];
  if (e.real) { parts.push(answers(e), twoDatasets(e), misleading(e)); }
  parts.push(walkthrough(e));
  if (e.real && e.real.differencing) parts.push(differencing(e));
  parts.push(limits(e));
  document.getElementById('deck').innerHTML = parts.join('');
  deck = [...document.querySelectorAll('.slide')];
  document.getElementById('dots').innerHTML = deck.map((_, i) =>
    `<button class="dot" data-slide="${i}" aria-label="Slide ${i + 1}"><span class="fill"></span></button>`).join('');
  document.getElementById('dots').onclick = (ev) => {
    const b = ev.target.closest('[data-slide]');
    if (b) { stop(); go(+b.dataset.slide); }
  };
  window.EVIDENCE = e;
  go(0);
}

function status() {
  document.getElementById('where').textContent =
    `${at + 1} of ${deck.length}${subs ? ` · exchange ${sub + 1}/${subs}` : ''}`;
}

function paintSub() {
  const steps = window.EVIDENCE.gated.steps;
  const host = document.getElementById('exchange');
  if (!host) return;
  host.innerHTML = exchange(steps[sub], sub, steps.length);
  document.querySelectorAll('#ticks .tick').forEach((t, i) =>
    t.setAttribute('aria-current', String(i === sub)));
  status();
}

function go(i) {
  at = (i + deck.length) % deck.length;
  deck.forEach((s, k) => s.classList.toggle('on', k === at));
  const isWalk = !!deck[at].querySelector('#ticks');
  subs = isWalk ? window.EVIDENCE.gated.steps.length : 0;
  sub = 0;
  if (isWalk) {
    paintSub();
    document.getElementById('ticks').onclick = (ev) => {
      const b = ev.target.closest('[data-step]');
      if (b) { stop(); sub = +b.dataset.step; paintSub(); }
    };
  }
  document.querySelectorAll('.dot').forEach((d, k) => {
    d.setAttribute('aria-current', String(k === at));
    d.dataset.done = String(k < at);
    d.querySelector('.fill').style.width = k < at ? '100%' : '0';
  });
  status();
  schedule();
}

function schedule() {
  clearInterval(timer);
  if (!playing) return;
  started = Date.now();
  const span = subs ? STEP_MS : SLIDE_MS;
  timer = setInterval(() => {
    const done = (Date.now() - started) / span;
    const fill = deck[at] && document.querySelectorAll('.dot')[at].querySelector('.fill');
    if (fill) fill.style.width = Math.min(1, subs ? (sub + done) / subs : done) * 100 + '%';
    if (done < 1) return;
    started = Date.now();
    if (subs && sub < subs - 1) {
      sub++; paintSub();
    } else {
      go(at + 1);
    }
  }, 90);
}

function stop() {
  playing = false;
  clearInterval(timer);
  document.getElementById('play').textContent = 'Play';
}
function start() {
  playing = true;
  document.getElementById('play').textContent = 'Pause';
  schedule();
}

document.getElementById('next').onclick = () => { stop(); go(at + 1); };
document.getElementById('prev').onclick = () => { stop(); go(at - 1); };
document.getElementById('play').onclick = () => (playing ? stop() : start());
addEventListener('keydown', (ev) => {
  if (ev.key === 'ArrowRight') { stop(); go(at + 1); }
  else if (ev.key === 'ArrowLeft') { stop(); go(at - 1); }
  else if (ev.key === ' ') { ev.preventDefault(); playing ? stop() : start(); }
  else if (ev.key === 'Home') { stop(); go(0); }
  else if (ev.key === 'End') { stop(); go(deck.length - 1); }
});

fetch('./evidence.json').then((r) => r.json()).then(render).catch((err) => {
  document.getElementById('deck').innerHTML =
    `<section class="slide on"><h2>Could not load the evidence</h2>
     <p class="sub">${esc(err)}</p></section>`;
});
