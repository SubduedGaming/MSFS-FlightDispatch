'use strict';
/* SkyDispatch browser remote. Everything runs on the Windows PC; this page only shows it and sends commands.
   All dynamic text goes through h() as text nodes, never innerHTML, so chat and job text can't inject markup. */

// ------------------------------------------------------------------ helpers
const $ = (s, r = document) => r.querySelector(s);
function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'value' || k === 'checked' || k === 'disabled' || k === 'selected' || k === 'href' || k === 'download') el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  const add = (k) => {
    if (k == null || k === false) return;
    if (Array.isArray(k)) k.forEach(add);
    else el.append(k.nodeType ? k : document.createTextNode(String(k)));
  };
  kids.forEach(add);
  return el;
}
// append/replace that skips null and false (the DOM would otherwise print the word "null")
function put(el, ...kids) {
  const add = (k) => { if (k == null || k === false) return; if (Array.isArray(k)) k.forEach(add); else el.append(k.nodeType ? k : document.createTextNode(String(k))); };
  kids.forEach(add);
  return el;
}
const fill = (el, ...kids) => { el.replaceChildren(); return put(el, ...kids); };
const muted = (t) => h('span', { class: 'muted' }, t);
const btn = (label, onclick, cls = '', extra = {}) => h('button', { class: cls, onclick, ...extra }, label);

async function api(path, body) {
  const opt = body === undefined ? {} :
    { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-SkyDispatch': '1' }, body: JSON.stringify(body) };
  const r = await fetch(path, { credentials: 'same-origin', cache: 'no-store', ...opt });
  if (r.status === 401) { showLogin(); throw new Error('Signed out'); }
  let data = {};
  try { data = await r.json(); } catch (e) { /* not JSON */ }
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}
const post = (path, body = {}) => api(path, body);
async function act(fn) {
  try { return await fn(); } catch (e) { if (e.message !== 'Signed out') toast('bad', e.message); return null; }
}
function toast(level, msg) {
  const t = h('div', { class: 'toast ' + (level || 'info') }, msg);
  $('#toasts').append(t);
  setTimeout(() => t.remove(), 6500);
}
function modal(...content) {
  const bg = h('div', { class: 'modal-bg', onclick: (e) => { if (e.target === bg) bg.remove(); } });
  const box = h('div', { class: 'modal', role: 'dialog' }, ...content);
  bg.append(box);
  document.body.append(bg);
  return { close: () => bg.remove(), box };
}
function notice(title, text, onClose) {
  const m = modal(h('h2', {}, title), h('p', {}, text),
    h('div', { class: 'row end' }, btn('OK', () => { m.close(); if (onClose) onClose(); }, 'primary')));
  return m;
}
const tiles = (list) => h('div', { class: 'tiles' }, list.map((t) =>
  h('div', { class: 'tile', title: t.hint || '' }, h('div', { class: 'lbl' }, t.label), h('div', { class: 'val ' + (t.tone || '') }, t.value))));
function table(cols, rows, opts = {}) {
  return h('div', { class: 'tablewrap' }, h('table', {},
    h('thead', {}, h('tr', {}, cols.map((c) => h('th', { class: c.num ? 'num' : '' }, c.label)))),
    h('tbody', {}, rows.map((r) => h('tr', {
      class: (opts.onclick ? 'click ' : '') + (opts.selected && opts.selected(r) ? 'sel' : ''),
      onclick: opts.onclick ? () => opts.onclick(r) : null,
    }, cols.map((c) => { const v = c.get(r); return h('td', { class: (c.num ? 'num ' : '') + (c.tone ? c.tone(r) || '' : '') }, v); }))))));
}
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

// ------------------------------------------------------------------ canvas drawing
function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
function setupCanvas(cv, height) {
  cv.style.height = height + 'px';
  const dpr = window.devicePixelRatio || 1, w = cv.clientWidth, hh = height;
  cv.width = Math.max(1, w * dpr); cv.height = hh * dpr;
  const g = cv.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, hh);
  return { g, w, h: hh };
}
function drawMap(cv, route, track, ac) {
  const { g, w, h: hh } = setupCanvas(cv, cv.dataset.height ? +cv.dataset.height : 260);
  const pts = [];
  if (route && route.from) pts.push([route.from.lat, route.from.lon]);
  if (route && route.to) pts.push([route.to.lat, route.to.lon]);
  (track || []).forEach((p) => pts.push(p));
  if (ac) pts.push([ac.lat, ac.lon]);
  g.font = '12px sans-serif';
  if (!pts.length) { g.fillStyle = cssVar('--muted'); g.fillText('No route to show', 14, 24); return; }
  const lats = pts.map((p) => p[0]), lons = pts.map((p) => p[1]);
  const lat0 = (Math.min(...lats) + Math.max(...lats)) / 2, kx = Math.max(0.1, Math.cos(lat0 * Math.PI / 180));
  const minLon = Math.min(...lons), maxLat = Math.max(...lats);
  const spanX = Math.max(0.05, (Math.max(...lons) - minLon) * kx), spanY = Math.max(0.05, maxLat - Math.min(...lats));
  const pad = 34, s = Math.min((w - 2 * pad) / spanX, (hh - 2 * pad) / spanY);
  const offX = (w - spanX * s) / 2, offY = (hh - spanY * s) / 2;
  const P = (lat, lon) => [offX + (lon - minLon) * kx * s, offY + (maxLat - lat) * s];
  const accent = cssVar('--accent'), text = cssVar('--text'), mut = cssVar('--muted');
  if (route && route.from && route.to) {
    const a = P(route.from.lat, route.from.lon), b = P(route.to.lat, route.to.lon);
    g.strokeStyle = mut; g.setLineDash([6, 5]); g.lineWidth = 1.5;
    g.beginPath(); g.moveTo(...a); g.lineTo(...b); g.stroke(); g.setLineDash([]);
  }
  if (track && track.length > 1) {
    g.strokeStyle = accent; g.lineWidth = 2.5; g.beginPath();
    track.forEach((p, i) => { const q = P(p[0], p[1]); i ? g.lineTo(...q) : g.moveTo(...q); });
    g.stroke();
  }
  [route && route.from, route && route.to].forEach((ap) => {
    if (!ap) return;
    const q = P(ap.lat, ap.lon);
    g.fillStyle = text; g.beginPath(); g.arc(q[0], q[1], 5, 0, 7); g.fill();
    g.fillText(ap.icao, q[0] + 8, q[1] - 8);
  });
  if (ac) {
    const q = P(ac.lat, ac.lon);
    g.save(); g.translate(q[0], q[1]); g.rotate((ac.heading || 0) * Math.PI / 180);
    g.fillStyle = accent; g.beginPath(); g.moveTo(0, -11); g.lineTo(8, 9); g.lineTo(0, 5); g.lineTo(-8, 9); g.closePath(); g.fill();
    g.restore();
  }
}
function drawProfile(cv, rows) {
  const { g, w, h: hh } = setupCanvas(cv, 150);
  g.font = '11px sans-serif';
  if (!rows || rows.length < 2) { g.fillStyle = cssVar('--muted'); g.fillText('No telemetry recorded', 12, 22); return; }
  const t0 = rows[0][0], t1 = rows[rows.length - 1][0] || t0 + 1;
  const maxAlt = Math.max(100, ...rows.map((r) => r[1])), maxGs = Math.max(10, ...rows.map((r) => r[2]));
  const X = (t) => 36 + (t - t0) / Math.max(1, t1 - t0) * (w - 48);
  const area = (idx, max, color, fill) => {
    g.beginPath();
    rows.forEach((r, i) => { const x = X(r[0]), y = hh - 18 - (r[idx] / max) * (hh - 34); i ? g.lineTo(x, y) : g.moveTo(x, y); });
    if (fill) { g.lineTo(X(t1), hh - 18); g.lineTo(X(t0), hh - 18); g.closePath(); g.fillStyle = fill; g.fill(); }
    g.strokeStyle = color; g.lineWidth = 2; g.stroke();
  };
  area(1, maxAlt, cssVar('--accent'), cssVar('--surface2'));
  area(2, maxGs, cssVar('--good'), null);
  g.fillStyle = cssVar('--muted');
  g.fillText(Math.round(maxAlt).toLocaleString() + ' ft', 4, 14);
  g.fillText('altitude (blue), ground speed (green, max ' + Math.round(maxGs) + ' kt)', 40, hh - 3);
}

// ------------------------------------------------------------------ chat widgets
function bubbles(box, data, who, handlers) {
  box.replaceChildren();
  for (const m of data.messages) {
    if (m.kind === 'offer') {
      const o = m.offer;
      if (!o) { box.append(h('div', { class: 'offer' }, 'This flight is no longer available.')); continue; }
      const row = h('div', { class: 'row' });
      if (o.status === 'offered') {
        row.append(btn('Accept flight', () => handlers.accept(o.id), 'primary', { disabled: !data.can_act, title: data.can_act ? '' : 'Finish or abandon your current flight first' }),
          btn('Decline', () => handlers.decline(o.id)));
      } else {
        const tone = { completed: 'good', accepted: 'accent', active: 'accent', failed: 'bad' }[o.status] || 'muted';
        row.append(h('b', { class: tone }, ({ accepted: 'Accepted', active: 'In progress', completed: 'Completed', failed: 'Failed', declined: 'Declined', expired: 'Expired' })[o.status] || o.status));
      }
      box.append(h('div', { class: 'offer' }, h('div', { class: 't' }, '✈  ' + o.title), h('div', {}, m.content),
        o.aircraft ? h('div', { class: 'muted' }, 'Aircraft: ' + o.aircraft + '  ·  Deadline ' + o.deadline) : null, row));
      continue;
    }
    const cap = m.role === 'hr' ? 'HR' : m.kind === 'callout' ? who + ' · callout' : m.role === 'assistant' ? who : '';
    box.append(h('div', { class: 'bubble ' + (m.kind === 'callout' ? 'callout' : m.role) }, cap ? h('div', { class: 'cap' }, cap) : null, m.content));
  }
  if (data.chips && data.chips.length) {
    box.append(h('div', { class: 'chips' }, data.chips.map((c) => btn(c.label, () => handlers.chip(c.minutes), 'chip'))));
  }
  if (data.busy) box.append(h('div', { class: 'typing' }, who.split(' - ')[0] + ' is typing…'));
  box.scrollTop = box.scrollHeight;
}
function talkButton(getTarget) {
  const b = h('button', { class: 'talk', title: 'Hold to talk. Uses the microphone on the Windows PC.' }, 'Hold to talk');
  let live = false, starting = null;
  const reset = () => { live = false; b.classList.remove('live'); b.textContent = 'Hold to talk'; };
  b.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    if (live) return;
    live = true; b.classList.add('live'); b.textContent = 'Listening…';
    starting = post('/api/voice/start', { target: getTarget() }).catch((err) => { reset(); toast('warn', err.message); });
  });
  const stop = async () => {
    if (!live) return;
    reset();
    await starting;
    act(() => post('/api/voice/stop', { target: getTarget() }));
  };
  ['pointerup', 'pointercancel', 'pointerleave'].forEach((ev) => b.addEventListener(ev, stop));
  b.addEventListener('contextmenu', (e) => e.preventDefault());
  return b;
}

// ------------------------------------------------------------------ app shell & routing
const PAGES = [['dashboard', 'Dashboard'], ['jobboard', 'Job Board'], ['messenger', 'Messenger'], ['flight', 'Flight'],
  ['market', 'Freelance'], ['hangar', 'Hangar'], ['logbook', 'Logbook'], ['finance', 'Finances'], ['settings', 'Settings']];
const ui = { thread: 'general', employer: null, jbTab: 'companies', hangarTab: 'fleet', aircraft: null, flightTab: 'copilot',
  job: null, q: '', kind: '', flyable: false, flight: null };
let current = null, es = null, live = false, appState = null;

function showLogin(msg) {
  if (es) { es.close(); es = null; }
  current = null;
  const input = h('input', { type: 'password', placeholder: 'Access code', autocomplete: 'current-password', autofocus: true });
  const err = h('div', { class: 'bad' }, msg || '');
  const go = async () => {
    try {
      await fetch('/api/login', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-SkyDispatch': '1' }, body: JSON.stringify({ token: input.value }) })
        .then(async (r) => { if (!r.ok) throw new Error((await r.json()).error || 'Sign-in failed'); });
      start();
    } catch (e) { err.textContent = e.message; }
  };
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
  $('#root').replaceChildren(h('div', { class: 'login' }, h('h1', {}, '✈  SkyDispatch'),
    h('p', { class: 'muted' }, 'Remote control for the SkyDispatch app on your Windows PC. Enter the access code shown in Settings > Simulator on the PC.'),
    input, err, btn('Sign in', go, 'primary')));
  input.focus();
}

function buildShell() {
  const nav = h('nav', {}, h('div', { class: 'brand' }, '✈  SkyDispatch'),
    PAGES.map(([k, label], i) => h('a', { href: '#/' + k, 'data-k': k, class: i === 8 ? 'settings-link' : '' }, label)),
    h('div', { class: 'spacer' }),
    h('div', { class: 'conn' }, h('span', { class: 'dot', id: 'd-live' }), h('span', { id: 't-live' }, 'Connecting…'), h('br', {}),
      h('span', { class: 'dot', id: 'd-sim' }), h('span', { id: 't-sim' }, 'Sim'), h('br', {}),
      h('span', { class: 'dot', id: 'd-ai' }), h('span', { id: 't-ai' }, 'AI')));
  $('#root').replaceChildren(h('div', { id: 'app' }, nav, h('main', { id: 'main' })));
}
function setDot(id, cls, text) {
  const d = $('#d-' + id), t = $('#t-' + id);
  if (d) d.className = 'dot ' + cls;
  if (t) t.textContent = text;
}
function setLive(on) { live = on; setDot('live', on ? 'ok' : 'busy', on ? 'Live' : 'Reconnecting…'); }

const loaders = {};
async function route() {
  if (!$('#main')) return;
  const k = (location.hash.replace(/^#\//, '') || 'dashboard');
  const key = loaders[k] ? k : 'dashboard';
  document.querySelectorAll('nav a').forEach((a) => a.classList.toggle('active', a.dataset.k === key));
  if (current && current.ctl && current.ctl.dispose) current.ctl.dispose();
  const root = h('div', { class: 'page' });
  $('#main').replaceChildren(root);
  current = { key, root, ctl: null };
  try {
    current.ctl = (await loaders[key](root)) || {};
  } catch (e) {
    if (e.message !== 'Signed out') root.replaceChildren(h('div', { class: 'card' }, h('h2', {}, 'Could not load this page'), h('p', { class: 'bad' }, e.message)));
  }
}
const reload = debounce(() => { if (current && $('#main')) { const y = $('#main').scrollTop; route().then(() => { $('#main').scrollTop = y; }); } }, 250);
const go = (k) => { if (location.hash === '#/' + k) reload(); else location.hash = '#/' + k; };

function dispatch(type, d) {
  if (type === 'toast') return toast(d.level, d.message);
  if (type === 'sim_status') setDot('sim', d.status === 'connected' ? 'ok' : d.status === 'error' ? 'bad' : 'busy', 'Sim: ' + d.status);
  if (type === 'ai_status') setDot('ai', d.ok ? 'ok' : 'bad', 'AI: ' + (d.ok ? 'online' : 'offline'));
  const ctl = current && current.ctl;
  if (ctl && ctl.event && ctl.event(type, d) === true) return;
  if (type === 'career' || type === 'settings' || type === 'sim_status' || type === 'ai_status') {
    if (type === 'career' && d.name === 'flight_event') return;
    reload();
  }
}
function connect() {
  if (es) es.close();
  es = new EventSource('/api/stream');
  let first = true;
  es.onopen = () => { setLive(true); if (!first) reload(); first = false; };
  es.onerror = () => {
    setLive(false);
    if (es.readyState === 2) setTimeout(start, 3000);      // closed for good: re-check sign-in
  };
  ['state', 'career', 'thread', 'busy', 'toast', 'sim_status', 'ai_status', 'settings'].forEach((t) =>
    es.addEventListener(t, (ev) => { try { dispatch(t, JSON.parse(ev.data)); } catch (e) { /* ignore */ } }));
}
async function start() {
  try {
    const ping = await fetch('/api/ping', { cache: 'no-store' }).then((r) => r.json());
    if (!ping.authed) return showLogin();
    appState = await api('/api/state');
  } catch (e) { return showLogin('Cannot reach SkyDispatch. Is it running on the PC?'); }
  buildShell();
  setDot('sim', appState.sim.status === 'connected' ? 'ok' : 'busy', 'Sim: ' + appState.sim.status);
  setDot('ai', appState.ai_online ? 'ok' : appState.ai_online === false ? 'bad' : '', 'AI: ' + (appState.ai_online ? 'online' : appState.ai_online === false ? 'offline' : '?'));
  connect();
  route();
}
window.addEventListener('hashchange', route);

// ------------------------------------------------------------------ pages
const jobLine = (j) => j ? h('div', {}, h('b', {}, j.title), h('div', { class: 'muted' }, j.info)) : null;

loaders.dashboard = async (root) => {
  const d = await api('/api/dashboard');
  const s = await api('/api/state');
  put(root, 
    s.update ? h('div', { class: 'banner' }, h('b', {}, 'SkyDispatch ' + s.update.version + ' is available.'),
      muted('Install it from the Windows PC (Help > Check for updates).'), h('a', { href: s.update.url, target: '_blank', rel: 'noopener' }, 'Release notes')) : null,
    h('h1', {}, d.hello), muted(d.sub), tiles(d.tiles),
    h('div', { class: 'cols' },
      h('div', { class: 'card' }, h('h2', {}, 'Current contract'),
        d.job ? jobLine(d.job) : h('div', {}, h('b', {}, 'No active contract'),
          h('p', { class: 'muted' }, 'Apply for a job, ask your dispatcher for a flight, or take a freelance contract. Flights without a contract are still logged.')),
        h('div', { class: 'row' },
          d.job ? btn('Open flight tracker', () => go('flight'), 'primary') : null,
          btn('Find work', () => go('jobboard')),
          d.job ? btn('Abandon', () => { if (confirm('Abandon this job? It will cost reputation.')) act(() => post('/api/job/abandon')); }, 'danger') : null)),
      h('div', { class: 'card' }, h('h2', {}, 'Systems'),
        h('div', {}, h('b', {}, 'Simulator '), d.systems.sim), h('div', {}, h('b', {}, 'AI dispatcher '), d.systems.ai),
        h('div', {}, h('b', {}, 'Voice '), d.systems.voice))),
    h('h2', {}, 'Recent flights'),
    table([{ label: 'Date', get: (r) => r.date }, { label: 'Route', get: (r) => r.route }, { label: 'Aircraft', get: (r) => r.aircraft },
      { label: 'Landing', get: (r) => r.landing }, { label: 'Score', get: (r) => r.score, num: true },
      { label: 'Net', get: (r) => r.net, num: true, tone: (r) => r.net_tone }], d.flights));
};

loaders.jobboard = async (root) => {
  const d = await api('/api/jobboard');
  if (!ui.employer || !d.employers.some((e) => e.id === ui.employer)) ui.employer = d.employers[0].id;
  const detail = h('div', { class: 'card' });
  const showDetail = async () => {
    const e = await api('/api/employer/' + ui.employer);
    detail.replaceChildren(h('h2', {}, e.name), h('p', {}, e.blurb),
      h('div', { class: 'kv' }, h('span', {}, 'Base'), e.base, h('span', {}, 'Work'), e.work, h('span', {}, 'Aircraft'), e.fleet, h('span', {}, 'Pay'), e.pay,
        e.record ? [h('span', {}, 'Your record'), e.record] : null),
      h('h3', {}, 'Requirements'),
      e.checks.length ? h('div', { class: 'tablewrap checks' }, h('table', {}, h('thead', {}, h('tr', {}, ['Requirement', 'Needed', 'You have', ''].map((x) => h('th', {}, x)))),
        h('tbody', {}, e.checks.map((c) => h('tr', {}, h('td', {}, c.label), h('td', {}, c.required), h('td', {}, c.actual), h('td', { class: c.met ? 'ok' : 'no' }, c.met ? '✓' : '✗')))))) : muted('No requirements: they will train you.'),
      h('p', { class: e.employed ? 'good' : e.note ? 'warn' : e.meets ? 'good' : 'warn' },
        e.employed ? 'You work here. Open the messenger to get flights.' : e.note ? e.note + '.' : e.meets ? 'You meet every requirement.' : "You don't meet every requirement yet. You can still apply, but you'll be turned down."),
      h('div', { class: 'row' },
        !e.employed ? btn('Apply', () => act(async () => {
          const r = await post('/api/employer/' + e.id + '/apply');
          notice(r.accepted ? 'Welcome to ' + r.company : r.company + ': application declined', r.message, () => { if (r.accepted) { ui.thread = r.thread; go('messenger'); } else reload(); });
        }), 'primary', { disabled: !!e.note }) : null,
        e.employed ? btn('Open messenger', () => { ui.thread = e.thread; go('messenger'); }, 'primary') : null,
        e.employed ? btn('Resign', () => { if (confirm('Resign from ' + e.name + '? Open flight offers will be withdrawn.')) act(async () => { await post('/api/employer/' + e.id + '/resign'); reload(); }); }, 'danger') : null));
  };
  const list = h('div', { class: 'list' }, d.employers.map((e) => h('div', { class: 'item' + (e.id === ui.employer ? ' sel' : ''), onclick: () => { ui.employer = e.id; list.querySelectorAll('.item').forEach((x) => x.classList.toggle('sel', x.dataset.id === e.id)); showDetail(); }, 'data-id': e.id },
    h('b', {}, e.name), h('div', { class: 'sub' }, h('span', { class: e.tone }, e.state), '  ·  ' + e.tagline))));
  const tab = (k, label) => btn(label, () => { ui.jbTab = k; reload(); }, ui.jbTab === k ? 'on' : '');
  put(root, h('h1', {}, 'Job Board'), muted('Apply to companies. They check your total time, recent experience, skill level and time on their aircraft. Once hired, their dispatcher messages you flights.'),
    tiles(d.tiles), h('p', { class: 'muted' }, d.classes), h('div', { class: 'tabs' }, tab('companies', 'Companies'), tab('apps', 'Applications')));
  if (ui.jbTab === 'apps') {
    put(root, table([{ label: 'Company', get: (r) => r.company }, { label: 'Result', get: (r) => r.result }, { label: 'Date', get: (r) => r.date }, { label: 'Details', get: (r) => r.details }], d.applications));
  } else {
    put(root, h('div', { class: 'split' }, list, detail));
    await showDetail();
  }
};

loaders.messenger = async (root) => {
  const box = h('div', { class: 'msgs' });
  const title = h('h2', {}), sub = h('div', { class: 'muted' });
  const input = h('input', { placeholder: 'Message your dispatcher…  (or hold the talk button)' });
  const extras = h('div', { class: 'row' });
  const threadsBox = h('div', { class: 'list' });
  let data = null, first = true;
  const draw = () => {
    if (!data) return;
    title.textContent = data.name; sub.textContent = data.sub;
    bubbles(box, data, data.name, {
      accept: (id) => act(async () => { await post('/api/offer/' + id + '/accept', { thread: ui.thread }); go('flight'); }),
      decline: (id) => act(() => post('/api/offer/' + id + '/decline', { thread: ui.thread })),
      chip: (m) => act(() => post('/api/thread/' + ui.thread + '/availability', { minutes: m })),
    });
    fill(extras, 
      data.is_employer ? btn('Different amount of time', () => act(() => post('/api/thread/' + ui.thread + '/ask_time')), '', { disabled: !data.can_ask_time }) : null,
      btn('Clear conversation', () => { if (confirm('Clear this conversation?')) act(() => post('/api/thread/' + ui.thread + '/clear')); }));
  };
  const loadThread = async () => { data = await api('/api/thread/' + encodeURIComponent(ui.thread) + (first ? '?open=1' : '')); first = false; draw(); };
  const loadThreads = async () => {
    const t = await api('/api/threads');
    if (!t.threads.some((x) => x.id === ui.thread)) ui.thread = 'general';
    threadsBox.replaceChildren(...t.threads.map((x) => h('div', { class: 'item' + (x.id === ui.thread ? ' sel' : ''), onclick: () => { ui.thread = x.id; first = true; loadThreads(); loadThread(); } },
      h('b', {}, x.name), h('div', { class: 'sub' }, x.preview || ' '))));
  };
  const send = () => { const t = input.value.trim(); if (!t) return; input.value = ''; act(() => post('/api/thread/' + encodeURIComponent(ui.thread) + '/send', { text: t })); };
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') send(); });
  const settings = await api('/api/settings');
  const speak = h('input', { type: 'checkbox', checked: settings.auto_speak_replies, onchange: (e) => act(() => post('/api/settings', { auto_speak_replies: e.target.checked })) });
  put(root, h('h1', {}, 'Messenger'), muted('Talk to your dispatchers. The PC speaks the replies and listens through its own microphone.'),
    h('div', { class: 'split', style: 'margin-top:12px' }, threadsBox, h('div', {}, title, sub, h('div', { class: 'chat' }, box,
      h('div', { class: 'input' }, input, btn('Send', send, 'primary'), talkButton(() => ui.thread), btn('Stop voice', () => act(() => post('/api/voice/silence'))))),
      h('div', { class: 'row', style: 'margin-top:8px' }, h('label', { class: 'check' }, speak, 'Speak replies on the PC'), h('span', { class: 'grow' }), extras))));
  await Promise.all([loadThreads(), loadThread()]);
  return {
    event(type, d) {
      if (type === 'thread' && d.thread !== 'copilot') { loadThreads(); if (d.thread === ui.thread) loadThread(); return true; }
      if (type === 'busy' && d.thread === ui.thread && data) { data.busy = d.busy; draw(); return true; }
      return false;
    },
  };
};

loaders.flight = async (root) => {
  const f = await api('/api/flight');
  ui.flight = f;
  const phase = h('div', { class: 'phase' }, f.phase), bar = h('div', { class: 'bar' }, h('i', { style: 'width:' + Math.round((f.progress || 0) * 100) + '%' }));
  const eta = h('div', { class: 'muted' }, f.eta || '');
  const cv = h('canvas', { 'data-height': '260' });
  const names = [['alt', 'Altitude'], ['ias', 'IAS'], ['gs', 'Ground speed'], ['vs', 'Vertical speed'], ['hdg', 'Heading'], ['fuel', 'Fuel'], ['g', 'G-force'], ['dist', 'Distance flown']];
  const cells = {};
  const grid = h('div', { class: 'cells' }, names.map(([k, l]) => { cells[k] = h('div', { class: 'val' }, (f.cells && f.cells[k]) || '-'); return h('div', {}, h('div', { class: 'lbl' }, l), cells[k]); }));
  let ac = f.live ? { lat: f.lat, lon: f.lon, heading: f.heading } : null;
  const paint = () => drawMap(cv, f.route, null, ac);
  const events = h('ul', { class: 'events' }, f.events.map((e) => h('li', {}, e.kind === 'result' ? e.detail : e.kind.replace(/_/g, ' ') + ': ' + e.detail)));
  const cbox = h('div', { class: 'msgs' }), cinput = h('input', { placeholder: 'Ask your copilot…' });
  let cop = f.copilot;
  const drawCop = () => bubbles(cbox, { messages: cop.messages, busy: cop.busy, chips: [], can_act: false }, cop.name, {});
  const ask = (body) => act(() => post('/api/copilot/ask', body));
  const sendCop = () => { const t = cinput.value.trim(); if (t) { cinput.value = ''; ask({ text: t }); } };
  cinput.addEventListener('keydown', (e) => { if (e.key === 'Enter') sendCop(); });
  const copPane = h('div', {}, h('div', { class: 'row' }, h('b', {}, 'Copilot: ' + cop.name), h('span', { class: 'grow' }),
      h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: cop.callouts, onchange: (e) => act(() => post('/api/copilot/callouts', { on: e.target.checked })) }), 'Callouts')),
    cop.enabled ? null : h('p', { class: 'warn' }, 'The copilot is switched off in Settings on the PC.'),
    h('div', { class: 'chat', style: 'margin-top:8px' }, cbox,
      h('div', { class: 'input' }, cop.quick.map((q) => btn(q.label, () => ask({ quick: q.key }), 'chip')),
        cinput, btn('Ask', sendCop, 'primary'), talkButton(() => 'copilot'))));
  const logPane = h('div', {}, events);
  const body = h('div', {});
  const tab = (k, label) => btn(label, () => { ui.flightTab = k; showTab(); }, ui.flightTab === k ? 'on' : '');
  const showTab = () => { body.replaceChildren(h('div', { class: 'tabs' }, tab('copilot', 'Copilot'), tab('log', 'Flight log')), ui.flightTab === 'log' ? logPane : copPane); if (ui.flightTab !== 'log') drawCop(); };
  put(root, h('h1', {}, 'Flight Tracker'),
    h('div', { class: 'card' }, f.job ? jobLine({ title: f.job.title, info: f.job.line }) : h('b', {}, 'Free flight: no contract. Everything you fly is still logged to your logbook.'), phase, bar, eta),
    h('div', { class: 'cols' }, h('div', {}, cv), h('div', {}, h('div', { class: 'card' }, grid), body)),
    h('div', { class: 'row', style: 'margin-top:12px' },
      f.can_demo ? btn('Start demo flight (simulated mode)', () => act(() => post('/api/flight/demo')), 'primary', { disabled: !f.has_job }) : null,
      h('span', { class: 'grow' }), f.has_job ? btn('Abandon flight', () => { if (confirm('Abandon the current job? Reputation will suffer.')) act(() => post('/api/job/abandon')); }, 'danger') : null));
  showTab(); requestAnimationFrame(paint);
  const onResize = debounce(paint, 150);
  window.addEventListener('resize', onResize);
  return {
    dispose() { window.removeEventListener('resize', onResize); },
    event(type, d) {
      if (type === 'state') {
        phase.textContent = d.phase;
        if (d.cells) for (const k in d.cells) if (cells[k]) cells[k].textContent = d.cells[k];
        if (d.progress != null) bar.firstChild.style.width = Math.round(d.progress * 1000) / 10 + '%';
        if (d.eta) eta.textContent = d.eta;
        if (d.live) { ac = { lat: d.lat, lon: d.lon, heading: d.heading }; paint(); }
        return true;
      }
      if (type === 'career' && d.name === 'flight_event') { events.append(h('li', {}, d.kind.replace(/_/g, ' ') + ': ' + d.detail)); events.scrollTop = events.scrollHeight; return true; }
      if (type === 'thread' && d.thread === 'copilot') { api('/api/copilot').then((c) => { cop = c; if (ui.flightTab !== 'log') drawCop(); }); return true; }
      if (type === 'busy' && d.thread === 'copilot') { cop.busy = d.busy; if (ui.flightTab !== 'log') drawCop(); return true; }
      if (type === 'career' && d.name === 'flight_finished') { events.append(h('li', {}, 'Result: ' + d.outcome + ', score ' + d.score + ' (' + d.grade + ')  payout ' + d.payout)); }
      return false;
    },
  };
};

loaders.market = async (root) => {
  const tbody = h('div', {}), detail = h('div', { class: 'card' }, muted('Select a contract'));
  const q = h('input', { placeholder: 'Filter by airport or city…', value: ui.q }), kind = h('select', {});
  const flyable = h('input', { type: 'checkbox', checked: ui.flyable });
  const data0 = await api('/api/market');
  kind.append(h('option', { value: '' }, 'All types'), ...Object.entries(data0.kinds).map(([k, v]) => h('option', { value: k, selected: k === ui.kind }, v)));
  const loadList = async () => {
    const qs = new URLSearchParams({ q: ui.q, kind: ui.kind, flyable: ui.flyable ? '1' : '0' });
    const d = await api('/api/market?' + qs);
    tbody.replaceChildren(d.jobs.length ? table([{ label: 'Type', get: (r) => r.kind_label }, { label: 'Route', get: (r) => r.origin + ' → ' + r.dest },
      { label: 'Distance', get: (r) => r.distance, num: true }, { label: 'Load', get: (r) => r.load, num: true }, { label: 'Payout', get: (r) => r.payout, num: true },
      { label: 'Deadline', get: (r) => r.deadline, num: true }], d.jobs, { onclick: (r) => { ui.job = r.id; loadList(); showJob(); }, selected: (r) => r.id === ui.job })
      : muted('No contracts match. Try “Find new contracts” or relax the filters.'));
  };
  const showJob = async () => {
    if (!ui.job) return;
    let j;
    try { j = await api('/api/market/' + ui.job); } catch (e) { detail.replaceChildren(muted(e.message)); return; }
    const sel = h('select', {}, j.planes.length ? j.planes.map((p) => h('option', { value: p.id, 'data-ok': p.ok ? '1' : '0', 'data-why': p.why }, p.label)) : [h('option', { value: '' }, 'No aircraft in hangar')]);
    const first = j.planes.findIndex((p) => p.ok); if (first >= 0) sel.selectedIndex = first;
    const note = h('div', {}), accept = btn('Accept job', () => act(async () => { await post('/api/market/' + j.id + '/accept', { aircraft_id: +sel.value }); ui.job = null; go('flight'); }), 'primary');
    const upd = () => { const o = sel.selectedOptions[0]; const ok = o && o.dataset.ok === '1'; note.className = ok ? 'good' : 'warn'; note.textContent = !o || !o.value ? 'Buy an aircraft in the Hangar to take jobs.' : ok ? 'This aircraft can fly the job.' : o.dataset.why; accept.disabled = !ok || j.active; accept.title = j.active ? 'Finish or abandon your current job first' : ''; };
    sel.addEventListener('change', upd);
    const cv = h('canvas', { 'data-height': '140' });
    detail.replaceChildren(h('h2', {}, j.title), h('p', {}, j.briefing),
      h('div', { class: 'kv' }, h('span', {}, 'Client'), j.client, h('span', {}, 'From'), j.origin_name + ' (' + j.origin + ')', h('span', {}, 'To'), j.dest_name + ' (' + j.dest + ')' + (j.dest_info ? ' - ' + j.dest_info : ''),
        h('span', {}, 'Pays'), j.payout, h('span', {}, 'Deadline'), j.deadline, h('span', {}, 'Expires'), j.expires),
      cv, h('h3', {}, 'Assign aircraft'), sel, note,
      h('div', { class: 'row', style: 'margin-top:10px' }, accept, btn('Decline', () => act(async () => { await post('/api/market/' + j.id + '/decline'); ui.job = null; reload(); })),
        btn('Ask dispatcher', () => act(async () => { await post('/api/market/' + j.id + '/ask'); ui.thread = 'general'; go('messenger'); }))));
    upd(); drawMap(cv, j.route, null, null);
  };
  q.addEventListener('input', debounce(() => { ui.q = q.value; loadList(); }, 250));
  kind.addEventListener('change', () => { ui.kind = kind.value; loadList(); });
  flyable.addEventListener('change', () => { ui.flyable = flyable.checked; loadList(); });
  put(root, h('div', { class: 'row' }, h('h1', { class: 'grow' }, 'Freelance Contracts'), q, kind, h('label', { class: 'check' }, flyable, 'Only jobs I can fly now'),
    btn('Find new contracts', (e) => { e.target.disabled = true; act(() => post('/api/market/refresh')); setTimeout(() => { e.target.disabled = false; }, 1500); })),
    h('div', { class: 'cols', style: 'margin-top:12px' }, tbody, detail));
  await loadList(); if (ui.job) await showJob();
  return { event(type, d) { if (type === 'career' && d.name === 'market_changed') { loadList(); return true; } return false; } };
};

loaders.hangar = async (root) => {
  const d = await api('/api/hangar');
  if (!ui.aircraft || !d.fleet.some((a) => a.id === ui.aircraft)) ui.aircraft = d.fleet.length ? d.fleet[0].id : null;
  const detail = h('div', { class: 'card' });
  const meter = (label, pct, text, tone) => h('div', {}, h('div', { class: 'row' }, h('span', { class: 'muted' }, label), h('span', { class: 'grow' }), text), h('div', { class: 'bar ' + (tone || '') }, h('i', { style: 'width:' + pct + '%' })));
  const doit = (a, what, msg) => () => { if (msg && !confirm(msg)) return; act(async () => { await post('/api/hangar/' + a.id + '/' + what); reload(); }); };
  const showAircraft = () => {
    const a = d.fleet.find((x) => x.id === ui.aircraft);
    if (!a) { detail.replaceChildren(h('h2', {}, 'Your hangar is empty'), muted('Visit the dealer to buy your first aircraft.')); return; }
    detail.replaceChildren(h('h2', {}, a.registration + '  ' + a.type), h('div', { class: 'kv' }, h('span', {}, 'Location'), a.location, h('span', {}, 'Total time'), a.hours, h('span', {}, 'Value'), a.value || '-'),
      h('p', { class: 'muted' }, a.specs || ''),
      meter('Condition', a.condition, a.condition + '%', a.condition < 40 ? 'bad' : a.condition < 70 ? 'warn' : 'good'),
      meter('Hours to next inspection', a.inspection_pct || 0, a.inspection_label || '', (a.inspection_pct || 0) < 15 ? 'warn' : ''), meter('Fuel', a.fuel_pct || 0, a.fuel_label || ''),
      h('p', { class: a.airworthy ? 'good' : 'bad' }, a.status),
      h('div', { class: 'row' }, btn('Refuel', doit(a, 'refuel'), '', { disabled: !a.can_refuel }), btn('Inspection (' + a.inspect_cost + ')', doit(a, 'inspect')),
        btn('Repair (' + a.repair_cost + ')', doit(a, 'repair'), '', { disabled: !a.can_repair }),
        btn('Rename', () => { const n = prompt('Nickname:', a.nickname); if (n !== null) act(async () => { await post('/api/hangar/' + a.id + '/rename', { nickname: n }); reload(); }); }),
        btn('Sell (' + a.value + ')', doit(a, 'sell', 'Sell ' + a.registration + ' for ' + a.value + '?'), 'danger')));
  };
  const list = h('div', { class: 'list' }, d.fleet.map((a) => h('div', { class: 'item' + (a.id === ui.aircraft ? ' sel' : ''), 'data-id': a.id, onclick: () => { ui.aircraft = a.id; list.querySelectorAll('.item').forEach((x) => x.classList.toggle('sel', +x.dataset.id === a.id)); showAircraft(); } },
    h('b', {}, a.registration + (a.nickname ? ' - ' + a.nickname : '')), h('div', { class: 'sub' }, a.type + '  |  at ' + a.location + (a.airworthy ? '' : '  |  GROUNDED')))));
  const tab = (k, label) => btn(label, () => { ui.hangarTab = k; reload(); }, ui.hangarTab === k ? 'on' : '');
  put(root, h('div', { class: 'row' }, h('h1', { class: 'grow' }, 'Hangar'), btn('Buy aircraft', openDealer, 'primary')), h('div', { class: 'tabs' }, tab('fleet', 'My aircraft'), tab('flown', 'Aircraft flown')));
  if (ui.hangarTab === 'flown') {
    put(root, muted('Every aircraft you fly in the simulator is recorded here, whether or not you own it.'),
      table([{ label: 'Simulator aircraft', get: (r) => r.title }, { label: 'Catalog match', get: (r) => r.match }, { label: 'Flights', get: (r) => r.flights, num: true }, { label: 'Time', get: (r) => r.time, num: true }, { label: 'Last flown', get: (r) => r.last }], d.flown));
  } else { put(root, h('div', { class: 'split' }, list, detail)); showAircraft(); }
};
async function openDealer() {
  const d = await act(() => api('/api/dealer'));
  if (!d) return;
  let pick = null;
  const loc = h('input', { value: d.home, maxlength: 4, size: 6 }), nick = h('input', { placeholder: 'Optional nickname' }), cond = h('select', {}, h('option', { value: '0' }, 'New (full price)'), h('option', { value: '1' }, 'Used (-30%, worn, more hours)'));
  const msg = h('div', { class: 'bad' });
  const rows = d.aircraft.map((t) => ({ ...t }));
  const wrap = h('div', {});
  const draw = () => wrap.replaceChildren(table([{ label: 'Aircraft', get: (r) => r.name }, { label: 'Class', get: (r) => r.category }, { label: 'Seats', get: (r) => r.seats, num: true }, { label: 'Cargo', get: (r) => r.cargo, num: true },
    { label: 'Range', get: (r) => r.range, num: true }, { label: 'Cruise', get: (r) => r.cruise, num: true }, { label: 'Price', get: (r) => r.price, num: true }], rows, { onclick: (r) => { pick = r; draw(); }, selected: (r) => pick && r.id === pick.id }));
  draw();
  const m = modal(h('h2', {}, 'Aircraft dealer'), d.restricted ? muted('Showing only aircraft installed in your simulator, since jobs can only use those.') : null, wrap,
    h('div', { class: 'form' }, 'Condition', cond, 'Deliver to airport', loc, 'Nickname', nick), msg,
    h('div', { class: 'row end' }, btn('Close', () => m.close()), btn('Buy', async () => {
      if (!pick) { msg.textContent = 'Select an aircraft first.'; return; }
      const used = cond.value === '1';
      if (!confirm('Buy ' + pick.name + ' for ' + (used ? pick.used_price : pick.price) + '?')) return;
      try { await post('/api/hangar/buy', { type_id: pick.id, location: loc.value, nickname: nick.value, used }); m.close(); reload(); } catch (e) { msg.textContent = e.message; }
    }, 'primary')));
}

loaders.logbook = async (root) => {
  const d = await api('/api/logbook');
  const detail = h('div', { class: 'card' }, muted('Select a flight'));
  const show = async (id) => {
    const f = await api('/api/logbook/' + id);
    const map = h('canvas', { 'data-height': '200' }), prof = h('canvas', {});
    detail.replaceChildren(h('h2', {}, f.title), h('div', { class: 'cols' }, h('div', {}, h('p', {}, h('b', {}, f.contract), h('br', {}), 'Sim aircraft: ' + f.sim_title),
      h('p', {}, f.stats.join('  ·  ')), h('p', {}, f.money), f.debrief ? h('p', {}, h('i', {}, 'Dispatcher: ' + f.debrief)) : null,
      h('ul', { class: 'events' }, f.events.map((e) => h('li', {}, e)))), h('div', {}, map, h('div', { style: 'height:8px' }), prof)));
    drawMap(map, f.route, f.track, null); drawProfile(prof, f.profile);
    detail.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  };
  put(root, h('div', { class: 'row' }, h('h1', { class: 'grow' }, 'Logbook'), muted(d.totals), h('a', { href: '/api/logbook.csv', download: 'logbook.csv' }, btn('Export CSV'))),
    table([{ label: 'Date', get: (r) => r.date }, { label: 'Route', get: (r) => r.route }, { label: 'Aircraft', get: (r) => r.aircraft }, { label: 'Air time', get: (r) => r.air, num: true },
      { label: 'Distance', get: (r) => r.distance, num: true }, { label: 'Landing', get: (r) => r.landing }, { label: 'Score', get: (r) => r.score, num: true }, { label: 'Result', get: (r) => r.result },
      { label: 'Net', get: (r) => r.net, num: true, tone: (r) => r.net_tone }], d.flights, { onclick: (r) => act(() => show(r.id)) }), h('div', { style: 'height:14px' }), detail);
};

loaders.finance = async (root) => {
  const d = await api('/api/finance');
  put(root, h('h1', {}, 'Finances'), tiles(d.tiles), table([{ label: 'Date', get: (r) => r.date }, { label: 'Category', get: (r) => r.category }, { label: 'Description', get: (r) => r.description },
    { label: 'Amount', get: (r) => r.amount, num: true, tone: (r) => r.tone }, { label: 'Balance', get: (r) => r.balance, num: true }], d.rows));
};

loaders.settings = async (root) => {
  const s = await api('/api/settings');
  const set = (k, v) => act(async () => { await post('/api/settings', { [k]: v }); toast('good', 'Saved'); });
  const sel = (key, opts, val) => h('select', { onchange: (e) => set(key, e.target.value) }, opts.map(([v, l]) => h('option', { value: v, selected: v === val }, l)));
  put(root, h('h1', {}, 'Settings'), muted(s.note),
    h('div', { class: 'card' }, h('h2', {}, 'This remote'),
      h('div', { class: 'form' }, h('label', { class: 'check', style: 'grid-column:1/-1' }, h('input', { type: 'checkbox', checked: s.auto_speak_replies, onchange: (e) => set('auto_speak_replies', e.target.checked) }), 'Speak replies on the PC'),
        h('label', { class: 'check', style: 'grid-column:1/-1' }, h('input', { type: 'checkbox', checked: s.copilot_callouts, onchange: (e) => set('copilot_callouts', e.target.checked) }), 'Copilot callouts'),
        'Distance', sel('units_distance', [['nm', 'Nautical miles'], ['km', 'Kilometres']], s.units_distance),
        'Weight', sel('units_weight', [['lb', 'Pounds'], ['kg', 'Kilograms']], s.units_weight))),
    h('div', { class: 'card' }, h('h2', {}, 'Voice on the PC'),
      h('div', {}, 'Speech out: ', h('b', { class: s.voice.speech_out ? 'good' : 'warn' }, s.voice.speech_out ? 'ready' : 'unavailable')),
      h('div', {}, 'Speech in: ', h('b', { class: s.voice.speech_in ? 'good' : 'warn' }, s.voice.speech_in ? 'ready' : 'unavailable')), s.voice.note ? muted(s.voice.note) : null),
    h('div', { class: 'card' }, h('h2', {}, 'About'), h('div', {}, 'SkyDispatch ' + s.version),
      s.update ? h('p', {}, h('b', { class: 'accent' }, 'Version ' + s.update.version + ' is available. '), 'Install it from the Windows PC (Help > Check for updates). ', h('a', { href: s.update.url, target: '_blank', rel: 'noopener' }, 'Release notes')) : muted('You are up to date.'),
      h('div', { class: 'row', style: 'margin-top:10px' }, btn('Sign out of this browser', () => fetch('/api/logout', { method: 'POST', credentials: 'same-origin', headers: { 'X-SkyDispatch': '1', 'Content-Type': 'application/json' }, body: '{}' }).then(() => showLogin())))));
};

// A link like http://pc:8766/#code=XXXX signs in once; the code is removed from the address bar straight away.
(async () => {
  const m = location.hash.match(/^#code=(.+)$/);
  if (m) {
    const code = decodeURIComponent(m[1]);
    history.replaceState(null, '', location.pathname);
    await fetch('/api/login', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-SkyDispatch': '1' }, body: JSON.stringify({ token: code }) });
  }
  start();
})();
