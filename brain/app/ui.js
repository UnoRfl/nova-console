// Nova Brain -- ui: everything on top of the canvas (SPEC 4.3, condensed).
// Words (+ karaoke), search terms falling into the hole, the trail, pop-up
// cards, letterbox, 3D labels, cluster-label hygiene, tooltip, detail card, dock.
// DOM is pooled at init; per-frame work only toggles classes when a value changes.

import { bus, state, stars, overlay, project, onFrame, fxu, toHost, setLensSky, look, ST, REDUCED, EMBED, wake } from './core.js';
import { graph, nodeOf, neighbours } from './data.js';
import { INTER_WOFF2 } from './font-inter.js';

const $ = id => document.getElementById(id);
const root = document.documentElement;
const tmp = [0, 0, 0];

/* ------------------------------------------------------------------ helpers */
const clip = (s, n) => { s = String(s || '').replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
const midClip = (s, n) => { s = String(s || ''); if (s.length <= n) return s; const k = Math.floor((n - 1) / 2); return s.slice(0, k) + '…' + s.slice(s.length - (n - 1 - k)); };
function setVars() { root.style.setProperty('--inset', (state.insetRight || 0) + 'px'); }

/* ------------------------------------------------------------------ words */
const W = { q: null, say: null, lines: [], cur: 0, spans: [[], []], text: '', shown: 0, chars: [], qTimer: 0, clearTimer: 0 };
function showQuestion(text) {
  if (!text) return;
  clearTimeout(W.qTimer);
  W.q.textContent = '“' + clip(text, 140) + '”';
  W.q.classList.remove('dim'); W.q.classList.add('on');
  W.qTimer = setTimeout(() => W.q.classList.add('dim'), 1200);
}
function hideQuestion() { W.q.classList.remove('on', 'dim'); }
function sayLine(text) {
  text = clip(text, 220);
  if (!text || text === W.text) return;
  W.text = text;
  const old = W.lines[W.cur], next = W.lines[1 - W.cur];
  // old line slides up and fades
  if (old.dataset.on === '1') { old.animate([{ transform: 'none', opacity: 1 }, { transform: 'translateY(-10px)', opacity: 0 }], { duration: 300, easing: 'cubic-bezier(.33,1,.68,1)', fill: 'forwards' }); old.dataset.on = '0'; }
  // fill the new one with word spans (pooled per line)
  const words = text.split(' ').slice(0, 80), pool = W.spans[1 - W.cur];
  while (pool.length < words.length) { const s = document.createElement('span'); next.appendChild(s); next.appendChild(document.createTextNode(' ')); pool.push(s); }
  let acc = 0; W.chars = [];
  for (let i = 0; i < pool.length; i++) {
    const s = pool[i];
    if (i < words.length) { s.textContent = words[i]; s.style.display = ''; s.classList.remove('on'); acc += words[i].length + 1; W.chars.push(acc); }
    else { s.textContent = ''; s.style.display = 'none'; }
  }
  next.dataset.on = '1';
  next.animate([{ transform: 'translateY(6px)', opacity: 0 }, { transform: 'none', opacity: 1 }], { duration: 260, easing: 'cubic-bezier(.33,1,.68,1)', fill: 'forwards' });
  W.cur = 1 - W.cur; W.shown = 0; W.revealed = 0;
  clearTimeout(W.clearTimer);
}
function clearWords(delay = 6000) {
  clearTimeout(W.clearTimer);
  W.clearTimer = setTimeout(() => {
    for (const l of W.lines) if (l.dataset.on === '1') { l.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 500, fill: 'forwards' }); l.dataset.on = '0'; }
    hideQuestion(); W.text = '';
  }, delay);
}
// karaoke: words turn white as she speaks them (14.5 chars/s while her voice is up)
function karaoke(dt) {
  if (!W.text) return;
  const speaking = state.mode === 'speaking' && state.drive > .04;
  if (speaking || state.mode !== 'speaking') W.revealed = (W.revealed || 0) + dt * (speaking ? 14.5 : 40);
  const pool = W.spans[W.cur];
  let k = 0; while (k < W.chars.length && W.chars[k] <= W.revealed) k++;
  if (k !== W.shown) { for (let i = W.shown; i < k && i < pool.length; i++) pool[i].classList.add('on'); W.shown = k; }
}

/* ------------------------------------------------------------------ terms falling into the hole */
function dropTerms(terms) {
  if (REDUCED || !terms || !terms.length) return;
  const box = $('terms'), q = W.q.getBoundingClientRect();
  const hx = state.bhPx[0], hy = state.bhPx[1];
  terms.slice(0, 8).forEach((t, k) => {
    const el = document.createElement('i'); el.textContent = t; box.appendChild(el);
    const x0 = q.left + 8 + k * 46, y0 = q.top + 4;
    const a = el.animate([
      { transform: `translate(${x0}px,${y0}px) scale(1)`, opacity: 0 },
      { transform: `translate(${x0}px,${y0 - 6}px) scale(1)`, opacity: .95, offset: .18 },
      { transform: `translate(${hx}px,${hy}px) scale(.4)`, opacity: 0 },
    ], { duration: 700, delay: 40 * k, easing: 'cubic-bezier(.55,0,1,.45)', fill: 'forwards' });
    a.onfinish = () => { el.remove(); bus.emit('termLand', { t: state.t }); };
  });
}

/* ------------------------------------------------------------------ trail */
const GLYPHS = `<svg class="g" viewBox="-5 -5 10 10"><path class="star" d="M0-4.5L1-1 4.5 0 1 1 0 4.5-1 1-4.5 0-1-1z"/><rect class="square" x="-3.5" y="-3.5" width="7" height="7"/><circle class="dot" r="1"/><ellipse class="ring" rx="4" ry="2.6"/><path class="triangle" d="M0-4L3.6 2.8H-3.6z"/><path class="hexagon" d="M0-4.2 3.6-2.1 3.6 2.1 0 4.2-3.6 2.1-3.6-2.1z"/></svg>`;
const STATUS = `<svg class="s" viewBox="0 0 10 10"><path class="sp" d="M5 1a4 4 0 1 1-3.5 2"/><path class="tk" pathLength="1" d="M2 5.3 4.2 7.4 8.2 2.8"/><path class="ds" d="M2.5 5h5"/></svg>`;
const T = { el: null, rows: [], byId: new Map(), order: [], fadeTimer: 0, last: 0, queue: [], pumpT: 0 };
function makeRow() {
  const r = document.createElement('div'); r.className = 'row';
  r.innerHTML = `<div class="in">${GLYPHS}<span class="v"></span><span class="sep">·</span><span class="o"></span>${STATUS}</div>`;
  r.v = r.querySelector('.v'); r.o = r.querySelector('.o');
  r.addEventListener('pointerenter', () => { if (r.star >= 0) { fxu.uHov.value[1] = r.star; wake(); } r.classList.add('hot'); toHost({ type: 'hover', id: r.stepId }); bus.emit('trail:hover', { id: r.stepId, star: r.star }); });
  r.addEventListener('pointerleave', () => { fxu.uHov.value[1] = -1; r.classList.remove('hot'); toHost({ type: 'hover', id: null }); });
  T.el.appendChild(r); return r;
}
/** Coalesced: at most one trail mutation per 120 ms (keeps layout calm during bursts). */
function trailPush(fn) { T.queue.push(fn); if (!T.pumpT) pumpTrail(); }
function pumpTrail() {
  const fn = T.queue.shift(); if (!fn) { T.pumpT = 0; return; }
  fn(); T.pumpT = setTimeout(pumpTrail, 120);
}
function addRow(id, glyph, verb, object, star = -1) {
  trailPush(() => {
    let r = T.rows.find(x => !x.busy) || null;
    if (!r) { r = T.order.shift(); }          // recycle the oldest
    r.busy = true; r.stepId = id; r.star = star;
    r.dataset.g = glyph || 'triangle'; r.dataset.s = 'run';
    r.v.textContent = verb || ''; r.o.textContent = object ? midClip(object, 28) : '';
    r.querySelector('.sep').style.display = object ? '' : 'none';
    r.classList.remove('cited', 'hot');
    T.order = T.order.filter(x => x !== r); T.order.push(r);
    if (id) T.byId.set(id, r);
    layoutRows(r);
  });
}
function layoutRows(fresh) {
  const vis = T.order.slice(-4);
  for (const r of T.order) if (!vis.includes(r)) { r.style.opacity = '0'; r.classList.remove('live'); setTimeout(() => { if (!T.order.slice(-4).includes(r)) r.busy = false; }, 220); }
  vis.forEach((r, k) => {
    const age = vis.length - 1 - k;
    r.style.transform = `translateY(${(4 - vis.length + k) * 18}px)`;
    r.style.opacity = age === 0 ? '1' : age === 1 ? '.78' : age === 2 ? '.55' : '.3';
    r.classList.add('live');
    if (r === fresh && !REDUCED) r.animate([{ clipPath: 'inset(0 100% 0 0)', transform: `translate(-8px,${(4 - vis.length + k) * 18}px)` }, { clipPath: 'inset(0 0 0 0)', transform: `translateY(${(4 - vis.length + k) * 18}px)` }], { duration: 280, easing: 'cubic-bezier(.22,1,.36,1)' });
  });
}
function setStatus(id, s) { trailPush(() => { const r = T.byId.get(id); if (r) r.dataset.s = s; }); }
function fadeTrail(delay) {
  clearTimeout(T.fadeTimer);
  T.fadeTimer = setTimeout(() => { for (const r of T.order) { r.style.opacity = '0'; r.classList.remove('live'); r.busy = false; } T.order = []; T.byId.clear(); }, delay);
}

/* ------------------------------------------------------------------ pop-up cards */
const STAR_SVG = '<svg viewBox="-7 -7 14 14"><path d="M0-7L1.4-1.4 7 0 1.4 1.4 0 7-1.4 1.4-7 0-1.4-1.4z"/><circle r="1.2"/></svg>';
const C = { pool: [], live: [], extra: 0 };
const TITLES = { fact: 'New memory', project: 'New memory', topic: 'New topic', skill: 'Learned skill', 'skill-improved': 'Skill improved', 'loop-closed': 'Loop closed', 'loop-open': 'Left open', forgot: 'Forgot' };
function card(kind, text, star = -1, waitIgnite = false, note = '') {
  if (C.live.length >= 2) { C.extra++; const c = C.live[C.live.length - 1]; c.b.innerHTML = c.title + `<em>+${C.extra} more</em>`; return; }
  const c = C.pool.find(x => !x.busy); if (!c) return;
  c.busy = true; C.live.push(c);
  c.title = TITLES[kind] || 'New memory';
  c.b.innerHTML = c.title + (note ? `<em>${note}</em>` : ''); c.s.textContent = clip(text, 64);
  c.el.style.top = `calc(var(--top) + ${118 + (C.live.length - 1) * 66}px)`;
  const go = () => {
    const from = starPoint(star, c.el);
    c.el.style.visibility = 'visible'; c.el.classList.remove('lit');
    const a = c.el.animate([{ transform: `translate(${from[0]}px,${from[1]}px) scale(.12)`, opacity: 0 }, { transform: 'none', opacity: 1 }],
      { duration: REDUCED ? 200 : 520, easing: 'cubic-bezier(.16,1,.3,1)', fill: 'forwards' });
    a.onfinish = () => c.el.classList.add('lit');
    c.timer = setTimeout(() => {
      const to = starPoint(star, c.el);
      const b = c.el.animate([{ transform: 'none', opacity: 1 }, { transform: `translate(${to[0]}px,${to[1]}px) scale(.12)`, opacity: 0 }], { duration: REDUCED ? 200 : 380, easing: 'cubic-bezier(.32,0,.67,0)', fill: 'forwards' });
      b.onfinish = () => { c.el.style.visibility = 'hidden'; c.busy = false; C.live = C.live.filter(x => x !== c); if (!C.live.length) C.extra = 0; };
    }, 4500 + (REDUCED ? 200 : 520));
  };
  if (waitIgnite && star >= 0) { c.waitStar = star; c.go = go; c.waitT = setTimeout(() => { if (c.go) { c.go = null; go(); } }, 1600); }
  else go();
}
function starPoint(star, el) {
  const r = el.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  if (star >= 0 && project(stars.pos[star*3], stars.pos[star*3+1], stars.pos[star*3+2], tmp)) return [tmp[0] - cx, tmp[1] - cy];
  return [0, -30];
}

/* ------------------------------------------------------------------ 3D labels (max 3) */
const L = { items: [], prio: { land: 3, cite: 2, reveal: 1 } };
function label(star, text, prio) {
  if (star < 0 || !text) return;
  if (L.items.some(x => x.star >= 0 && x.star !== star && x.span.textContent === clip(text, 42) && performance.now() - x.at < 6000)) return;   // one label per name
  let slot = L.items.find(x => x.star === star) || L.items.find(x => x.star < 0) || L.items.slice().sort((a, b) => a.prio - b.prio || a.at - b.at)[0];
  if (slot.star >= 0 && slot.star !== star && slot.prio > prio && performance.now() - slot.at < 6000) return;
  slot.star = star; slot.prio = prio; slot.at = performance.now();
  slot.h.o.idx = star; slot.span.textContent = clip(text, 42);
  clearTimeout(slot.on); clearTimeout(slot.off);
  slot.el.classList.remove('on');
  slot.on = setTimeout(() => slot.el.classList.add('on'), 140);
  slot.off = setTimeout(() => { slot.el.classList.remove('on'); setTimeout(() => { if (performance.now() - slot.at >= 6000) slot.star = -1; }, 300); }, 6000);
}
const nodeLabel = i => { const n = nodeOf(i); if (!n) return ''; return n.type === 'frag' && n.parent ? n.parent.label : n.label; };

/* ------------------------------------------------------------------ cluster labels: no overlaps */
function declutter() {
  const items = [];
  for (const cl of graph.clusters.values()) if (cl.handle && cl.handle.op > .05) items.push(cl);
  for (const cl of items) cl._hide = false;
  for (let i = 0; i < items.length; i++) for (let j = i + 1; j < items.length; j++) {
    const a = items[i].handle, b = items[j].handle;
    if (Math.abs(a.x - b.x) < 70 && Math.abs(a.y - b.y) < 16) {
      // fade the one farther from the camera
      project(0, 0, 0, tmp);
      const da = labelDepth(items[i]), db = labelDepth(items[j]);
      (da > db ? items[i] : items[j])._hide = true;
    }
  }
  for (const cl of items) cl.labelEl.classList.toggle('occl', !!cl._hide);
}
function labelDepth(cl) { const p = cl.handle.o.pos; const v = typeof p === 'function' ? p() : p; project(v[0], v[1], v[2], tmp); return tmp[2]; }
function freshLetters(cl) {
  const b = cl.labelEl && cl.labelEl.querySelector('b'); if (!b || REDUCED) return;
  const txt = b.textContent; b.textContent = '';
  [...txt].forEach((ch, k) => {
    const i = document.createElement('i'); i.textContent = ch === ' ' ? ' ' : ch; b.appendChild(i);
    i.animate([{ transform: `translateX(${(k - txt.length / 2) * 9}px)`, opacity: 0 }, { transform: 'none', opacity: 1 }], { duration: 900, delay: 28 * k, easing: 'cubic-bezier(.16,1,.3,1)', fill: 'backwards' });
  });
  cl.labelEl.classList.add('fresh'); setTimeout(() => cl.labelEl.classList.remove('fresh'), 4000);
}

/* ------------------------------------------------------------------ tooltip + detail card */
let tipH = null, cardH = null, lastTerms = [];
function showTip(idx) {
  const tip = $('tip');
  if (idx < 0) { tip.classList.remove('on'); return; }
  const n = nodeOf(idx); if (!n) return;
  tip.firstChild.textContent = n.type === 'frag' ? clip(n.text, 60) : clip(n.label, 50);
  tipH.o.idx = idx; tip.classList.add('on');
}
function showCard(idx) {
  const el = $('card');
  if (idx < 0) { el.classList.remove('on'); return; }
  const n = nodeOf(idx); if (!n) return;
  const when = n.at ? String(n.at).slice(0, 10) : '';
  const where = n.parent ? n.parent.label : (graph.clusters.get(n.cluster) || {}).label || '';
  el.innerHTML = '<div class="in"><div class="k"></div><div class="x"></div></div>';
  el.querySelector('.k').textContent = [where, when].filter(Boolean).join(' · ');
  const x = el.querySelector('.x'); const text = clip(n.text || n.label, 260);
  // matched terms in bold (built with text nodes: memory text never goes through innerHTML)
  const re = lastTerms.length ? new RegExp('\\b(' + lastTerms.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')', 'ig') : null;
  let last = 0;
  if (re) for (const m of text.matchAll(re)) { x.append(text.slice(last, m.index)); const b = document.createElement('b'); b.textContent = m[0]; x.append(b); last = m.index + m[0].length; }
  x.append(text.slice(last));
  cardH.o.idx = idx; requestAnimationFrame(() => el.classList.add('on'));
}

/* ------------------------------------------------------------------ init */
export async function init() {
  try { const f = new FontFace('Inter', `url(data:font/woff2;base64,${INTER_WOFF2})`, { weight: '300 600', style: 'normal' }); await f.load(); document.fonts.add(f); }
  catch (e) { console.warn('[ui] Inter not loaded, using Segoe UI', e); }
  if (REDUCED) document.body.classList.add('reduced');
  setVars();

  W.q = $('q'); W.say = $('say'); W.lines = [...W.say.querySelectorAll('.ln')];
  T.el = $('trail'); for (let i = 0; i < 6; i++) T.rows.push(makeRow());
  for (let i = 0; i < 2; i++) { const el = document.createElement('div'); el.className = 'pc'; el.innerHTML = STAR_SVG + '<b></b><span></span>'; $('toasts').appendChild(el); C.pool.push({ el, b: el.querySelector('b'), s: el.querySelector('span'), busy: false }); }
  for (let i = 0; i < 3; i++) { const el = document.createElement('div'); el.className = 'lb'; const span = document.createElement('span'); el.appendChild(span);
    const h = overlay.add(el, { idx: 0, anchor: 'left', dx: 12, dy: -12, near: 120 }); L.items.push({ el, span, h, star: -1, prio: 0, at: 0 }); }
  $('tip').innerHTML = '<span></span>'; tipH = overlay.add($('tip'), { idx: 0, anchor: 'left', dx: 14, dy: 0, near: 60 });
  cardH = overlay.add($('card'), { idx: 0, anchor: 'left', dx: 18, dy: 0, near: 60 });
  $('card').style.zIndex = 8;

  // host layout
  bus.on('host:top', m => root.style.setProperty('--top', (+m.px || 18) + 'px'));
  bus.on('host:inset', setVars); bus.on('resize', setVars);

  // turn lifecycle
  bus.on('turn:start', e => { document.body.classList.add('turn'); clearTimeout(T.fadeTimer); clearTimeout(W.clearTimer); if (e && e.heard) showQuestion(e.heard); $('bReplay').hidden = true; });
  bus.on('heard', e => showQuestion(e.text));
  bus.on('turn:end', () => { document.body.classList.remove('turn'); fadeTrail(6000); clearWords(7000); $('bReplay').hidden = false; });
  bus.on('mode', e => { if (e.mode === 'speaking') setTimeout(hideQuestion, 400); });
  bus.on('say', e => sayLine(e.text));
  bus.on('speak:chunk', e => sayLine(e.text));

  // what she is doing
  bus.on('recall', e => {
    lastTerms = (e.terms || []).slice(0, 8);
    addRow('recall:' + state.t.toFixed(2), 'star', e.verb || 'Searching memory', e.object || lastTerms.slice(0, 3).join(' '), e.stars && e.stars[0] != null ? e.stars[0] : -1);
    dropTerms(lastTerms);
  });
  bus.on('step', e => addRow(e.id, e.glyph, e.verb, e.object, e.primary ?? -1));
  bus.on('step:done', e => setStatus(e.id, e.ok ? 'done' : 'err'));
  bus.on('step:hold', e => setStatus(e.id, 'hold'));
  bus.on('step:allow', e => setStatus(e.id, 'run'));
  bus.on('step:deny', e => setStatus(e.id, 'err'));
  bus.on('cite', e => { const r = [...T.byId.values()].find(x => x.star === e.star); if (r) r.classList.add('cited'); label(e.star, nodeLabel(e.star), L.prio.cite); });
  bus.on('gaze:land', e => label(e.star, nodeLabel(e.star), L.prio.land));
  bus.on('reveal', e => { (e.stars || []).slice(0, 3).forEach((s, k) => setTimeout(() => label(s, nodeLabel(s), L.prio.reveal), 140 + k * 90)); });
  bus.on('host:hover', m => { for (const r of T.byId.values()) { const on = m && m.id && r.stepId === m.id; r.classList.toggle('hot', !!on); if (on && r.star >= 0) fxu.uHov.value[2] = r.star; } if (!m || !m.id) fxu.uHov.value[2] = -1; wake(); });

  // learning
  bus.on('learn', e => {
    if (!e || e.kind === 'day' || e.silent) return;
    const star = e.stars && e.stars.length ? e.stars[0] : -1;
    card(e.kind, e.text || (e.nodes && e.nodes[0] && e.nodes[0].text) || '', star, !!e.provisional);
  });
  bus.on('birth:ignite', e => { for (const c of C.pool) if (c.go && c.waitStar === e.star) { clearTimeout(c.waitT); const g = c.go; c.go = null; g(); } });
  bus.on('genesis', e => { if (e && e.phase === 'end') card('topic', e.label || e.topic || 'New topic', e.stars && e.stars[0] != null ? e.stars[0] : -1, false, e.count ? String(e.count) : ''); });
  bus.on('forget', e => { if (e && !e.silent && e.text) card('forgot', e.text, e.stars && e.stars[0] != null ? e.stars[0] : -1); });
  bus.on('letterbox', e => {
    if (REDUCED) return;
    const on = !!(e && e.on);
    for (const i of document.querySelectorAll('#lbox i')) i.animate([{ transform: `scaleY(${on ? 0 : 1})` }, { transform: `scaleY(${on ? 1 : 0})` }], { duration: on ? 600 : 500, easing: 'cubic-bezier(.33,1,.68,1)', fill: 'forwards' });
  });
  bus.on('graph:cluster', e => { if (e && e.cluster) setTimeout(() => freshLetters(e.cluster), 50); });

  // pointer
  bus.on('hover', e => showTip(e.idx));
  bus.on('click', e => {
    showCard(e.idx);
    if (e.idx >= 0) { stars.setState(e.idx, ST.TARGET, state.t, state.t + 6, 0); for (const j of neighbours(e.idx).slice(0, 12)) { const s = stars.stateOf(j); if (s === 0 || s === ST.TARGET) stars.setState(j, ST.TARGET, state.t + .12, state.t + 6, 0); } }
  });
  bus.on('click:empty', () => showCard(-1));

  // dock
  const bF = $('bFollow'), bAB = $('bAB'), bR = $('bReplay');
  bF.onclick = () => { const on = bF.getAttribute('aria-pressed') !== 'true'; bF.setAttribute('aria-pressed', String(on)); bus.emit('follow', { on }); };
  bAB.onclick = () => { const on = !look.lensSky; setLensSky(on); bAB.setAttribute('aria-pressed', String(on)); wake(); };
  bR.onclick = () => bus.emit('replay:request', {});

  let lastDeclutter = 0;
  onFrame((dt, t, now) => {
    karaoke(dt);
    if (now - lastDeclutter > 250) { lastDeclutter = now; declutter(); }
  }, 50);
  window.__brain = Object.assign(window.__brain || {}, { ui: { card, label, addRow, sayLine } });
}
