// Nova Brain -- feed-demo: ?demo=1 plays a 60 s showcase loop through the real
// feed pipeline (no POSTs, no database writes). Fixtures hold no memory text:
// targets are picked at runtime from the loaded graph. Layout stores in
// localStorage ('novaBrain.*') are snapshotted and restored so the demo never
// changes Uno's real layout. Also: ?bench=1 (frame/GPU stats over the loop).

import { bus, state, stars, params, toHost, EMBED } from './core.js';
import * as data from './data.js';

const QUESTION = 'Nova, what did I decide about the Orbit pitch deck, and look up the latest on React 19';
const ANSWER1 = 'Of course, Sir. One moment.';
const ANSWER2 = 'You settled on scroll, intro, then the manual deck, Sir. React 19 is stable, with Actions and the new use hook.';

let api = null, timers = [], loopN = 0, snap = null, demoStars = [];
const at = (s, fn) => timers.push(setTimeout(() => { try { fn(); } catch (e) { console.error('[demo]', e); } }, s * 1000));
const orb = (mode, extra = {}) => api.injectOrb({ mode, flavour: '', level: 0, saying: '', game: false, pending: null, jobs: 0, turn: loopN, ...extra });
const ev = raw => api.inject(raw);

function snapshot() { const s = {}; for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); if (k && k.startsWith('novaBrain.')) s[k] = localStorage.getItem(k); } return s; }
function restore() {
  if (!snap) return;
  try {
    for (let i = localStorage.length - 1; i >= 0; i--) { const k = localStorage.key(i); if (k && k.startsWith('novaBrain.') && !(k in snap)) localStorage.removeItem(k); }
    for (const [k, v] of Object.entries(snap)) localStorage.setItem(k, v);
  } catch { /* private mode */ }
}

function pickTargets() {
  const g = data.graph;
  let topic = g.byKey.get('topic:orbit-pitch-deck');
  if (!topic) topic = g.nodes.filter(n => n.kind === 'topic' && n.cluster === 'orbit').sort((a, b) => b.kids.length - a.kids.length)[0];
  if (!topic) topic = g.nodes.filter(n => n.kind === 'topic').sort((a, b) => b.kids.length - a.kids.length)[0];
  const ids = topic ? topic.kids.filter(k => k.rowId != null).slice(0, 5).map(k => k.rowId) : [];
  const near = g.nodes.filter(n => n.rowId != null && n.cluster === (topic ? topic.cluster : '') && !ids.includes(n.rowId)).slice(0, 9).map(n => n.rowId);
  const loop = g.nodes.find(n => n.kind === 'loop' && stars.bright[n.idx] > 0);
  return { ids, near, loop };
}

function speak(text, from, to) {
  // synthetic voice: ~5 Hz syllable envelope while she "speaks"
  const steps = Math.floor((to - from) / .1);
  for (let k = 0; k <= steps; k++) {
    const t = from + k * .1, lvl = k === steps ? 0 : .05 + .11 * Math.abs(Math.sin(t * Math.PI * 5)) * (.7 + .3 * Math.sin(t * 1.3));
    at(t, () => orb(k === steps ? 'idle' : 'speaking', { level: lvl, saying: k === steps ? '' : text }));
  }
}

function loop() {
  timers.forEach(clearTimeout); timers = [];
  loopN++; restore(); snap = snapshot();
  api.reset();
  for (const i of demoStars) { stars.setBright(i, 0); stars.setState(i, 0, 0, 0, 0); }
  demoStars = [];
  const T = pickTargets();

  at(2.0, () => orb('listening'));
  at(5.4, () => { ev({ kind: 'heard', text: QUESTION }); orb('thinking'); toHost({ type: 'demo', beat: 'heard' }); });
  at(5.46, () => ev({ kind: 'recall', text: 'Memory', ids: T.ids, near: T.near, days: [], terms: ['orbit', 'pitch', 'deck', 'decide'] }));
  at(6.15, () => ev({ kind: 'text', text: ANSWER1 }));
  speak(ANSWER1, 6.2, 7.6);
  at(8.2, () => { orb('tool'); ev({ kind: 'tool', text: 'Read', id: 'demo-read-' + loopN, detail: 'pitch-deck.md', input: JSON.stringify({ file_path: 'C:/Users/pc/Downloads/Orbit/docs/pitch-deck.md' }) }); });
  at(8.215, () => ev({ kind: 'tool', text: 'mcp__nova__web_search', id: 'demo-web-' + loopN, detail: 'react 19 latest release', input: JSON.stringify({ query: 'react 19 latest release' }) }));
  at(8.6, () => ev({ kind: 'tool_done', text: 'ok', id: 'demo-read-' + loopN, output: '# Pitch deck\n'.padEnd(2400, '.') }));
  at(10.4, () => ev({ kind: 'tool_done', text: 'ok', id: 'demo-web-' + loopN, output: 'https://react.dev/blog/2024/12/05/react-19\nhttps://github.com/facebook/react/releases\nhttps://www.npmjs.com/package/react' }));
  at(10.65, () => ev({ kind: 'tool', text: 'mcp__nova__web_open', id: 'demo-open-' + loopN, detail: 'react.dev', input: JSON.stringify({ url: 'https://react.dev/blog/2024/12/05/react-19' }) }));
  at(12.4, () => ev({ kind: 'tool_done', text: 'ok', id: 'demo-open-' + loopN, output: 'React 19 is now stable.'.padEnd(5000, '.') }));
  at(12.6, () => ev({ kind: 'text', text: ANSWER2 }));
  speak(ANSWER2, 12.9, 21);

  at(26, () => { orb('remember'); ev({ kind: 'remember', text: 'Remembered', detail: 'Main monitor now runs at 165 Hz' }); setTimeout(() => orb('idle'), 1200); });
  at(33, () => ev({ kind: 'remember', text: 'Learned how', detail: 'open-an-app-and-set-its-volume' }));
  at(40, () => genesis());
  at(49, () => { if (T.loop) ev({ kind: 'remember', text: 'Finished', detail: T.loop.text }); });
  at(54, () => {
    const fact = data.graph.nodes.find(n => n.text === 'Main monitor now runs at 165 Hz');
    if (fact) bus.emit('forget', { text: fact.text, stars: [fact.idx], silent: false });
    for (const i of demoStars) setTimeout(() => stars.setBright(i, 0), 600);
  });
  at(60, loop);
}

function genesis() {
  const now = new Date().toISOString().slice(0, 19).replace('T', ' ');
  const made = [];
  for (let k = 0; k < 40; k++) {
    const x = data.addRow({ id: 900000000 + loopN * 100 + k, kind: 'project', topic: 'demo-repo', source: 'demo', at: now, text: `Demo note ${k + 1}: a section of the studied repository` }, { born: false, bulk: true });
    if (x && x.node) made.push(x.node.idx);
    if (x && x.created) for (const n of x.created) if (!made.includes(n.idx)) made.push(n.idx);
  }
  demoStars.push(...made);
  const c = [0, 0, 0]; for (const i of made) { c[0] += stars.pos[i*3]; c[1] += stars.pos[i*3+1]; c[2] += stars.pos[i*3+2]; }
  for (let k = 0; k < 3; k++) c[k] /= Math.max(1, made.length);
  bus.emit('genesis', { topic: 'demo-repo', label: 'Demo Repo', phase: 'start', stars: made, centre: c, count: made.length });
  setTimeout(() => bus.emit('genesis', { topic: 'demo-repo', label: 'Demo Repo', phase: 'end', stars: made, centre: c, count: made.length }), 6000);
}

/* ------------------------------------------------------------------ bench */
function bench() {
  const fr = [], gpu = [], longs = []; let heap0 = performance.memory ? performance.memory.usedJSHeapSize : 0;
  try { new PerformanceObserver(l => { for (const e of l.getEntries()) longs.push(e.duration); }).observe({ entryTypes: ['longtask'] }); } catch { /* unsupported */ }
  let last = performance.now();
  bus.on('frame', () => {});
  const tick = () => { const now = performance.now(); fr.push(now - last); last = now; if (state.gpuMs > 0) gpu.push(state.gpuMs); if (now - t0 < 60000) requestAnimationFrame(tick); else done(); };
  const t0 = performance.now(); requestAnimationFrame(tick);
  const pct = (a, p) => { const s = a.slice().sort((x, y) => x - y); return +(s[Math.floor(s.length * p)] || 0).toFixed(2); };
  function done() {
    const r = { frames: fr.length, p50: pct(fr, .5), p95: pct(fr, .95), p99: pct(fr, .99), gpuP50: pct(gpu, .5), gpuP95: pct(gpu, .95), gpuMax: +Math.max(0, ...gpu).toFixed(2),
      longTasks: longs.length, heapMB: performance.memory ? +((performance.memory.usedJSHeapSize - heap0) / 1e6).toFixed(1) : null };
    window.__brainBench = r; console.log('[bench]', JSON.stringify(r));
  }
}

export async function start(feedApi) {
  api = feedApi;
  api.setLive(false);
  snap = snapshot();
  addEventListener('pagehide', restore);
  if (params.has('bench')) bench();
  if (params.has('demo') || params.has('bench')) setTimeout(loop, 1500);
  window.__brain = Object.assign(window.__brain || {}, { demo: { loop, restore } });
}
