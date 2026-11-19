// Nova Brain -- fx-reveal: what she is handed, what she searches, what she quotes.
//   X-3 recall reveal: a 120 ms pinch of the ring, the sonar front (no geometry), handed memories lit
//       gold exactly as the front crosses them, near misses ringed, days lit, house lights down,
//       one-shot gold pours spiralling into the disk (bh.uFeed)
//   X-4 search fronts: recall(query) candidates tentative -> confirmed gold, search_past along the
//       diary arm, Grep/Glob over the folder's subtree with path:line sparks in sweep order
//   X-5 lamp 0 (the subject) + lamp 1 (the hole, gold .25 during turns); rack focus while the
//       director's shot is FOCUS / BIRTH / HERO
//   X-6 spoken citations: each speak:chunk is scored against the stars lit this turn
// Owns: star states USED / TENTATIVE / CONSIDERED / CITED, fxu.uLight[0..1] + uLightCol[0..1],
// fxu.uSonar, fxu.uFrontC/N, fxu.uFocus, bh.uFeed, look.house / look.dust / look.dim.
// Emits 'reveal' {stars, ranks, centroid, t, tOn, kind}, 'cite' {star, t, score}, 'cite:none' {t},
// 'house' {on, t}. Listens: recall, step, step:done, mode, speak:chunk, turn:start, turn:end, shot,
// gaze:launch, gaze:land, graph:gone, clock:rebase, resize.
// Rule: nothing in the frame hook allocates (scalars and pooled objects only); events may.
// No memory text lives in this file: it only reads node text at runtime (token-gated API).

import {
  THREE, state, look, pulse, fxu, bh, stars, ST, COLORS, REDUCED, bus, onFrame, camera, controls,
  scene, easings, Spring, pixelRatio, api,
} from './core.js';
import * as data from './data.js';

/* ------------------------------------------------------------------ constants */
const SONAR_R = 2000, SONAR_S = 1.1;          // front radius (u) and duration (s)
const PINCH_UP = .12, PINCH_DOWN = .3;        // ring pinch: 120 ms inQuad in, then out
const FRONT_S = .9;                           // search sweep duration (s)
const HOUSE_DELAY = .24;                      // house lights go down 240 ms after the recall
const HOUSE_HOLD = 2.5, HOUSE_RAMP = 1.8;     // restore 2.5 s after speaking ends, over 1.8 s
const POURS = 8, PER = 96, POUR_TOP = 6, POUR_GAP = .14;
const FEED_STEP = .03, FEED_CAP = .15, FEED_TAU = 1.4;
const LAMP_ATTACK = .18, LAMP_TAU = 1.4, LAMP_R2 = 300 * 300;
const LIT_CAP = 30, CITE_HOLD = .6, NEAR_CAP = 12, TENT_CAP = 24, SPARK_CAP = 6, SPARK_GAP = .06;
const RANK_MAX = 5;                           // the shader shrinks ranks > 5 below rest size: clamp the texel
const GOLD = COLORS.GOLD, COOL = COLORS.COOL, WHITE = COLORS.WHITE;
const FOCUS_SHOTS = new Set(['FOCUS', 'BIRTH', 'HERO']);
// Clusters whose NAME is a real word she would say (the +2 of a citation). Generic sector names
// (You, Files, Web, Actions, Diary, Skills, Studio, Machine) would cite everything in them.
const NAMED = new Set(['orbit', 'school', 'tpn', 'mc', 'roblox', 'nova']);

// Port of nova_memory.py STOP + _terms(): r"[A-Za-z0-9][A-Za-z0-9'_-]{1,}" on the lowercased text,
// stop words dropped, the first 12 kept. Matching uses the bridge's coverage test: a term counts
// when its first 5 characters occur in the row text (+ topic).
const STOP = new Set(`a an the and or but if then so to of in on at by for with from as is are was were be been
i you he she it we they me my your his her its our their this that these those what which who how why
when where one two three four five plus minus times many much some any do does did can could would should will shall have has had not no yes please nova hey just
about tell show give get make let there here now up out me`.split(/\s+/).filter(Boolean));
// Her own filler words: never evidence that she is quoting something (citations only).
const SPEECH = new Set(`sir okay ok course moment sure right well very good great alright certainly indeed also still yeah
thanks thank done got it's that's i'm i'll i've i'd let's here's there's you're you've you'll we're they're what's
really quite actually perhaps maybe though shall`.split(/\s+/).filter(Boolean));
const WORD_RE = /[a-z0-9][a-z0-9'_-]{1,}/g;
/** JS port of nova_memory._terms(): the words a search for `text` looks for (at most 12). */
export function terms(text, extra = null) {
  const words = String(text || '').toLowerCase().match(WORD_RE) || [];
  const out = [];
  for (const w of words) { if (STOP.has(w) || (extra && extra.has(w))) continue; out.push(w); if (out.length >= 12) break; }
  return out;
}
const plainWords = s => (String(s || '').toLowerCase().match(/[a-z0-9]+/g) || []).filter(w => w.length >= 2 && !STOP.has(w));

/* ------------------------------------------------------------------ small helpers */
const clamp01 = x => x < 0 ? 0 : x > 1 ? 1 : x;
const P = stars.pos;
const nodeOf = i => data.graph.starNode[i] || null;
const valid = i => Number.isInteger(i) && i >= 0 && i < stars.count;
const distOf = i => Math.hypot(P[i * 3], P[i * 3 + 1], P[i * 3 + 2]);
/** A normalized event's t0 on the shader clock: missing or in the future -> now. */
function tOf(t0) { const t = state.t; return (typeof t0 === 'number' && Number.isFinite(t0) && t0 <= t + 2) ? t0 : t; }
/** May this module write star i? Never over a birth (< 6 s), a fall, a loop or a pending approval. */
function writable(i) {
  if (!valid(i)) return false;
  const st = stars.stateOf(i);
  if (st === ST.FALLING || st === ST.LOOP || st === ST.PENDING) return false;
  if (st === ST.BIRTH && state.t - stars.tOnOf(i) < 6) return false;
  const n = nodeOf(i);
  return !(n && n.gone) && stars.bright[i] > .001;
}
const MINE = new Set([ST.USED, ST.TENTATIVE, ST.CONSIDERED, ST.CITED]);
const dbg = { writes: 0, releases: 0, reveals: 0, cites: 0, lastReveal: null, lastScore: null };
/** Every texel this module writes goes through here (counted for the X-3 test). */
function put(i, st, tOn, tOff = 1e9, prm = 0) {
  stars.setState(i, st, tOn, tOff, prm); dbg.writes++;
  if (st !== ST.CONSIDERED && st !== ST.TENTATIVE) owned.add(i); else soft.add(i);
}
function cool(i, t) { if (valid(i) && MINE.has(stars.stateOf(i))) { stars.release(i, t); dbg.releases++; } }
const owned = new Set();      // stars this turn holds in USED / CITED (released at turn end)
const soft = new Set();       // tentative / considered stars (released at turn end too)

/* ------------------------------------------------------------------ pours (shader-driven, pooled) */
const pour = (() => {
  const n = POURS * PER, p = new Float32Array(n * 3);
  for (let k = 0; k < POURS; k++) for (let j = 0; j < PER; j++) { const i = k * PER + j; p[i * 3] = k; p[i * 3 + 1] = j / (PER - 1); p[i * 3 + 2] = i + 1; }
  const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(p, 3));
  const uA = [], uB = [];
  for (let k = 0; k < POURS; k++) { uA.push(new THREE.Vector4(0, 0, 0, -1e9)); uB.push(new THREE.Vector4(0, 0, 0, 0)); }
  const material = new THREE.ShaderMaterial({ transparent: true, depthWrite: false, depthTest: true, blending: THREE.AdditiveBlending,
    uniforms: { uT: fxu.uT, uPR: { value: pixelRatio() }, uPA: { value: uA }, uPB: { value: uB } },
    // A: (r0, theta0, y0, tStart)  B: (dur s, gain, spread u, feed)
    vertexShader: `uniform float uT, uPR; uniform vec4 uPA[${POURS}]; uniform vec4 uPB[${POURS}];
      varying vec3 vC; varying float vA;
      float h1(float n){ return fract(sin(n*12.9898)*43758.5453); }
      void main(){
        int k=int(position.x+.5); vec4 A=uPA[k], B=uPB[k];
        float u=position.y, dur=B.x;
        float s=dur>0. ? (uT-A.w)/dur-.18*u : -1.;
        if(s<=0.||s>=1.){ vA=0.; vC=vec3(0.); gl_PointSize=0.; gl_Position=vec4(0.,0.,2.,1.); return; }
        float r0=A.x, r=mix(r0,87.,s*s*s);
        float th=A.y+2.2*log(r0/r);
        float y=A.z*(1.-s)*(1.-s);
        float sp=B.z*(1.-s);
        r+=(h1(position.z)-.5)*sp; y+=(h1(position.z+7.13)-.5)*sp*.7; th+=(h1(position.z+3.71)-.5)*sp/max(r,1.);
        vec4 mv=modelViewMatrix*vec4(cos(th)*r,y,sin(th)*r,1.);
        gl_Position=projectionMatrix*mv;
        gl_PointSize=mix(2.8,1.5,s)*uPR;
        vA=.95*(1.-u)*(1.-u)*B.y*smoothstep(0.,.05,s)*(1.-smoothstep(.92,1.,s));
        vC=mix(vec3(1.,.97,.93),vec3(1.,.66,.32),smoothstep(.05,.75,s)); }`,
    fragmentShader: `varying vec3 vC; varying float vA;
      void main(){ if(vA<.002) discard; float d=length(gl_PointCoord-.5)*2.; float a=vA*exp(-d*d*2.5); gl_FragColor=vec4(vC*a,a); }` });
  const pts = new THREE.Points(g, material); pts.frustumCulled = false; pts.visible = false; pts.renderOrder = 3;
  scene.add(pts);
  return { pts, material, uA, uB, fed: new Float32Array(POURS), next: 0 };
})();
/** One stream from star i: starts at tStart, lasts dur = clamp(1.1 sqrt(r0/700), .9, 1.4) s. */
function pourFrom(i, tStart, gain = 1) {
  const x = P[i * 3], y = P[i * 3 + 1], z = P[i * 3 + 2], r0 = Math.hypot(x, z);
  if (r0 < 150) return;
  let k = -1;
  for (let j = 0; j < POURS; j++) if (pour.uB[j].x <= 0) { k = j; break; }
  if (k < 0) { k = pour.next; pour.next = (pour.next + 1) % POURS; }      // all busy: the oldest slot is reused
  const dur = Math.min(1.4, Math.max(.9, 1.1 * Math.sqrt(r0 / 700)));
  pour.uA[k].set(r0, Math.atan2(z, x), y, tStart); pour.uB[k].set(dur, gain, 9, 1); pour.fed[k] = 0;
  pour.pts.visible = true;
}

/* ------------------------------------------------------------------ lamps 0 (subject) + 1 (the hole) */
// mode: 0 off, 1 attack, 2 hold, 3 release, 4 dip (a far move: fade 90 ms, jump, attack)
function makeLamp() {
  return { x: 0, y: 0, z: 0, I: 0, set: 0, from: 0, aT: 0, mode: 0, r: 1, g: 1, b: 1, tr: 1, tg: 1, tb: 1,
    gx: 0, gy: 0, gz: 0, tx: 0, ty: 0, tz: 0, gT: -1, gD: .3,
    pAt: -1e9, px: 0, py: 0, pz: 0, pI: 0, pr: 1, pg: 1, pb: 1, dip: 0, dT: 0, star: -1, pStar: -1, mod: 0 };
}
const lamps = [makeLamp(), makeLamp()];
/** Point lamp k at (x,y,z) with intensity I and colour c, from time `at` (state.t). */
function lampTo(k, x, y, z, I, c, at = state.t, star = -1) {
  const L = lamps[k];
  L.pAt = at; L.px = x; L.py = y; L.pz = z; L.pI = I; L.pr = c[0]; L.pg = c[1]; L.pb = c[2]; L.pStar = star;
}
function lampStar(k, i, I, c, at) { if (valid(i)) lampTo(k, P[i * 3], P[i * 3 + 1], P[i * 3 + 2], I, c, at, i); }
function lampOff(k, at = state.t) { const L = lamps[k]; L.pAt = at; L.pI = 0; L.pStar = -1; L.px = L.tx; L.py = L.ty; L.pz = L.tz; L.pr = L.tr; L.pg = L.tg; L.pb = L.tb; }
function lampApply(L, t) {
  L.pAt = -1e9;
  if (L.pI <= 0) { if (L.mode !== 0) L.mode = 3; L.star = -1; return; }
  L.tr = L.pr; L.tg = L.pg; L.tb = L.pb; L.star = L.pStar;
  const dx = L.px - L.x, dy = L.py - L.y, dz = L.pz - L.z, far = dx * dx + dy * dy + dz * dz > LAMP_R2;
  if (L.I < .02 || L.mode === 0) {                   // dark: jump there and open
    L.x = L.tx = L.px; L.y = L.ty = L.py; L.z = L.tz = L.pz; L.gT = -1;
    L.r = L.tr; L.g = L.tg; L.b = L.tb; L.from = L.I; L.set = L.pI; L.aT = t; L.mode = 1;
  } else if (far) {                                  // lit and far: a quick dip, then the jump
    L.dip = L.I; L.dT = t; L.mode = 4; L.set = L.pI; L.tx = L.px; L.ty = L.py; L.tz = L.pz;
  } else {                                           // lit and near: glide there
    L.gx = L.x; L.gy = L.y; L.gz = L.z; L.tx = L.px; L.ty = L.py; L.tz = L.pz; L.gT = t; L.gD = .3;
    L.from = L.I; L.set = L.pI; L.aT = t; L.mode = 1;
  }
}
function lampFrame(L, k, t, dt) {
  if (L.pAt > -1e8 && t >= L.pAt) lampApply(L, t);
  switch (L.mode) {
    case 1: { const a = clamp01((t - L.aT) / LAMP_ATTACK); L.I = L.from + (L.set - L.from) * easings.outCubic(a); if (a >= 1) L.mode = 2; break; }
    case 2: L.I = L.set; break;
    case 3: L.I *= Math.exp(-dt / LAMP_TAU); if (L.I < .002) { L.I = 0; L.mode = 0; } break;
    case 4: { const a = clamp01((t - L.dT) / .09); L.I = L.dip * (1 - a);
      if (a >= 1) { L.x = L.tx; L.y = L.ty; L.z = L.tz; L.gT = -1; L.r = L.tr; L.g = L.tg; L.b = L.tb; L.from = 0; L.aT = t; L.mode = 1; }
      break; }
    default: L.I = 0;
  }
  if (L.gT >= 0) {
    const a = clamp01((t - L.gT) / L.gD), e = easings.EASE(a);
    L.x = L.gx + (L.tx - L.gx) * e; L.y = L.gy + (L.ty - L.gy) * e; L.z = L.gz + (L.tz - L.gz) * e;
    if (a >= 1) L.gT = -1;
  }
  if (L.star >= 0 && L.gT < 0 && L.mode !== 4) { const o = L.star * 3; L.x = P[o]; L.y = P[o + 1]; L.z = P[o + 2]; L.tx = L.x; L.ty = L.y; L.tz = L.z; }
  const cr = Math.min(1, dt * 8); L.r += (L.tr - L.r) * cr; L.g += (L.tg - L.g) * cr; L.b += (L.tb - L.b) * cr;
  const I = L.I * (1 + L.mod);
  fxu.uLight.value[k].set(L.x, L.y, L.z, I > .001 ? I : 0);
  fxu.uLightCol.value[k].set(L.r, L.g, L.b);
}
/** The colour a lamp takes on star i: gold for memories, cool for the outside world, white for actions. */
function colourOf(i) {
  const n = nodeOf(i); if (!n) return GOLD;
  if (n.kind === 'tool') return WHITE;
  if (n.place || n.kind === 'site' || n.kind === 'page' || n.kind === 'file' || n.kind === 'folder') return COOL;
  return GOLD;
}

/* ------------------------------------------------------------------ house lights */
const house = { downAt: -1e9, isDown: false, restoreAt: -1e9, rampT: -1e9, lastAct: 0 };
function houseDown(at) {
  house.downAt = at; house.restoreAt = -1e9; house.rampT = -1e9;
  bus.emit('house', { on: true, t: at });
}
function houseRestore(at) {
  if (!house.isDown && house.downAt < -1e8) return;
  if (house.restoreAt > -1e8 && house.restoreAt <= at) return;
  house.restoreAt = at;
  bus.emit('house', { on: false, t: at });
}

/* ------------------------------------------------------------------ turn bookkeeping */
const turn = { active: false, t0: -1e9, prevT0: -1e9, speaking: false, recallC: null, recallStar: -1, ended: true };
const lit = [];               // citation candidates: {star, rank, t, hay, names, fileW}
const flights = new Map();    // gaze id -> {star, land} (expected landing, state.t)
const searches = new Map();   // step id -> pending search (front candidates)
function recordOf(i, rank) {
  const n = nodeOf(i); if (!n) return null;
  const place = !!n.place;
  let topic = '';
  if (n.kind === 'project' && n.parent && String(n.parent.key).startsWith('topic:')) topic = n.parent.key.slice(6);
  else if (n.kind === 'topic' && String(n.key).startsWith('topic:')) topic = n.key.slice(6);
  const hay = (String(n.text || '') + ' ' + topic.replace(/[-_]+/g, ' ') + (place || n.kind === 'skill' ? ' ' + (n.label || '') : '')).toLowerCase();
  const names = [];
  const tw = plainWords(topic.replace(/[-_]+/g, ' ')); if (tw.length) names.push(tw);
  const cl = data.graph.clusters.get(n.cluster);
  if (cl && (NAMED.has(cl.id) || cl.sector != null)) { const cw = plainWords(cl.label); if (cw.length) names.push(cw); }
  let fileW = null;
  if (n.kind === 'file' || n.kind === 'folder') {
    const base = String(n.text || n.label || '').split('/').pop().replace(/\.[a-z0-9]{1,6}$/i, '');
    fileW = plainWords(base);
  } else if (n.kind === 'site' || n.kind === 'page') {
    const host = String(n.text || '').split('/')[0].replace(/^www\./, '');
    const first = host.split('.')[0]; fileW = first && first !== 'search' ? plainWords(first) : null;
    if (String(n.key).startsWith('page:q:') || String(n.key) === 'site:search') fileW = null;
  }
  return { star: i, rank: rank || 0, t: state.t, hay, names, fileW };
}
function litAdd(i, rank = 0) {
  if (!valid(i)) return;
  for (const r of lit) if (r.star === i) { if (rank && (!r.rank || rank < r.rank)) r.rank = rank; r.t = state.t; return; }
  const rec = recordOf(i, rank); if (!rec) return;
  if (lit.length >= LIT_CAP) {       // full: drop the weakest (unranked, oldest)
    let w = -1, wv = 1e9;
    for (let k = 0; k < lit.length; k++) { const r = lit[k], v = (r.rank ? 100 - r.rank : 0) + r.t * 1e-3; if (v < wv) { wv = v; w = k; } }
    if (w < 0 || (rank === 0 && lit[w].rank)) return;
    lit.splice(w, 1);
  }
  lit.push(rec);
}
function startTurn(t) {
  if (turn.active) return;
  turn.active = true; turn.ended = false; turn.prevT0 = turn.t0; turn.t0 = t;
  lit.length = 0; turn.recallC = null; turn.recallStar = -1;
  lampTo(1, 0, 0, 0, .25, GOLD, t);
}
function endTurn() {
  if (turn.ended) return;
  const t = state.t;
  turn.ended = true; turn.active = false; turn.speaking = false;
  uncite(t, true);
  for (const i of owned) cool(i, t);
  for (const i of soft) cool(i, t);
  owned.clear(); soft.clear(); searches.clear(); flights.clear();
  lampOff(0, t); lampOff(1, t);
  houseRestore(t + HOUSE_HOLD);
}

/* ------------------------------------------------------------------ X-3 recall reveal */
const fx = { pinchT: -1e9, pinchA: 1, sonarEnd: -1e9, dipT: -1e9, frontEnd: -1e9 };
/** Sonar crossing time of star i for a sonar launched at ts. */
function sonarCross(i, ts) { const d = Math.min(distOf(i) / SONAR_R, 1); return ts + SONAR_S * (1 - Math.pow(1 - d, .25)); }
function centroidNear(list) {
  if (!list.length) return null;
  const a = list[0], ax = P[a * 3], ay = P[a * 3 + 1], az = P[a * 3 + 2];
  let x = 0, y = 0, z = 0, w = 0;
  for (let k = 0; k < list.length; k++) {
    const i = list[k], dx = P[i * 3] - ax, dy = P[i * 3 + 1] - ay, dz = P[i * 3 + 2] - az;
    if (dx * dx + dy * dy + dz * dz > 400 * 400) continue;
    const wk = 1 / (k + 1); x += P[i * 3] * wk; y += P[i * 3 + 1] * wk; z += P[i * 3 + 2] * wk; w += wk;
  }
  return [x / w, y / w, z / w];
}
function onRecall(ev) {
  ev = ev || {};
  const now = state.t, t0 = tOf(ev.t0), late = now - t0 > 2.5;
  house.lastAct = now;
  if (!turn.active) startTurn(t0);
  const ids = Array.isArray(ev.ids) ? ev.ids.map(Number).filter(Number.isFinite) : null;
  // handed stars in rank order; ids whose row is not on the map yet are fetched below
  let hand = [], missing = [];
  if (ids && ids.length) {
    for (const id of ids) { const n = data.graph.byRow.get(id); if (n && !n.gone) { if (!hand.includes(n.idx)) hand.push(n.idx); } else missing.push(id); }
  } else if (Array.isArray(ev.stars)) for (const i of ev.stars) if (valid(i) && !hand.includes(i)) hand.push(i);
  const near = [];
  const nearSrc = Array.isArray(ev.nearStars) ? ev.nearStars : Array.isArray(ev.near) ? data.starsOfRows(ev.near) : [];
  for (const i of nearSrc) { if (near.length >= NEAR_CAP) break; if (valid(i) && !hand.includes(i) && !near.includes(i)) near.push(i); }
  const days = [];
  const daySrc = Array.isArray(ev.dayStars) ? ev.dayStars : Array.isArray(ev.days) ? ev.days.map(d => data.graph.byKey.get('day:' + d)?.idx).filter(valid) : [];
  for (const i of daySrc) if (valid(i) && !hand.includes(i) && !near.includes(i) && !days.includes(i)) days.push(i);
  const empty = !hand.length && !near.length && !days.length && !missing.length;
  const calm = state.game ? .6 : 1;
  const ts = t0 + PINCH_UP;                                      // the sonar leaves as the pinch peaks
  const recent = turn.t0 - turn.prevT0 < 10;
  if (!late) {
    fx.pinchT = t0; fx.pinchA = calm;
    if (!REDUCED) { fxu.uSonar.value.set(ts, (empty || recent ? .5 : 1) * calm, 1, 0); fx.sonarEnd = ts + SONAR_S + .05; }
  }
  if (empty) { if (!late) fx.dipT = ts; return; }
  // used memories: lit exactly as the front crosses them (rank 1 brightest, longer spikes)
  const tOn = [], ranks = [];
  for (let k = 0; k < hand.length; k++) {
    const i = hand[k], tk = late ? t0 : REDUCED ? ts : sonarCross(i, ts);
    tOn.push(tk); ranks.push(k + 1);
    if (writable(i)) put(i, ST.USED, tk, 1e9, Math.min(k + 1, RANK_MAX));
    litAdd(i, k + 1);
  }
  // near misses: a faint hollow ring as the front passes (600 ms), released as the ring ends
  if (!late) for (const i of near) if (writable(i)) { const tk = REDUCED ? ts : sonarCross(i, ts); put(i, ST.CONSIDERED, tk, tk + .6, 0); }
  // days she was handed light as used
  for (const i of days) {
    const tk = late ? t0 : REDUCED ? ts : sonarCross(i, ts);
    if (writable(i)) put(i, ST.USED, tk, 1e9, RANK_MAX);
    litAdd(i, hand.length + 1);
  }
  const lead = hand.length ? hand : days;
  const c = centroidNear(lead);
  if (c) {
    turn.recallC = c; turn.recallStar = lead[0];
    if (!late) houseDown(t0 + HOUSE_DELAY); else houseDown(now);
    lampTo(0, c[0], c[1], c[2], .8, GOLD, late ? now : t0 + HOUSE_DELAY);
  }
  // one-shot pours: the top ranks, 140 ms apart, once the front has passed
  if (!late && !REDUCED) {
    const n = Math.min(POUR_TOP, hand.length);
    for (let k = 0; k < n; k++) pourFrom(hand[k], Math.max(ts + SONAR_S + POUR_GAP * k, tOn[k] + .3));
  }
  dbg.reveals++;
  const t1 = tOn.length ? Math.min(...tOn) : ts;
  const payload = { stars: hand.concat(days), ranks: ranks.concat(days.map((_, k) => hand.length + 1 + k)), centroid: c, t: t1,
    tOn: tOn.concat(days.map(i => late ? t0 : sonarCross(i, ts))), kind: 'recall' };
  dbg.lastReveal = { n: payload.stars.length, near: near.length, missing: missing.length, t: t1 };
  bus.emit('reveal', payload);
  if (missing.length) igniteLater(missing, ids);
}
/** Ids the map does not have yet: fetch since=max, then ignite them without wave timing. */
function igniteLater(missing, ids) {
  data.pollNew().catch(() => null).then(() => {
    const got = [];
    for (const id of missing) {
      const n = data.graph.byRow.get(id); if (!n || n.gone) continue;
      const i = n.idx, rank = ids.indexOf(id) + 1;
      const go = () => { if (writable(i) && !turn.ended) { put(i, ST.USED, state.t, 1e9, Math.min(rank, RANK_MAX)); litAdd(i, rank); } };
      if (stars.stateOf(i) === ST.BIRTH) setTimeout(go, Math.max(0, 6 - (state.t - stars.tOnOf(i))) * 1000 + 50);   // born by this poll: let the birth play
      else go();
      got.push([i, rank]);
    }
    if (got.length) bus.emit('reveal', { stars: got.map(g => g[0]), ranks: got.map(g => g[1]), centroid: turn.recallC, t: state.t, tOn: got.map(() => state.t), kind: 'late' });
  });
}

/* ------------------------------------------------------------------ X-4 search fronts */
const camR = new THREE.Vector3(), camU = new THREE.Vector3();
/** n = normalize(camRight * .85 + up * .15), fixed at launch so the sweep reads left to right. */
function screenDir(out) {
  camera.updateMatrixWorld();
  const e = camera.matrixWorld.elements;
  camR.set(e[0], e[1], e[2]); camU.set(e[4], e[5], e[6]);
  out.copy(camR).multiplyScalar(.85).addScaledVector(camU, .15).normalize();
  return out;
}
/** Launch the (single) front; returns a crossing-time function for stars. */
function launchFront(c, n, ext, t0) {
  fxu.uFrontC.value.set(c[0], c[1], c[2], t0); fxu.uFrontN.value.set(n.x, n.y, n.z, REDUCED ? 0 : ext);
  fx.frontEnd = t0 + FRONT_S + .05;
  return i => {
    if (REDUCED) return t0 + .2;
    const x = (P[i * 3] - c[0]) * n.x + (P[i * 3 + 1] - c[1]) * n.y + (P[i * 3 + 2] - c[2]) * n.z;
    const f = clamp01((x + ext) / (2 * ext));
    return t0 + FRONT_S * Math.acos(1 - 2 * f) / Math.PI;
  };
}
function frontFor(list, t0, c0 = null) {
  const n = screenDir(new THREE.Vector3());
  let c = c0;
  if (!c) { c = [0, 0, 0]; for (const i of list) { c[0] += P[i * 3]; c[1] += P[i * 3 + 1]; c[2] += P[i * 3 + 2]; } c[0] /= list.length; c[1] /= list.length; c[2] /= list.length; }
  let ext = 0;
  for (const i of list) ext = Math.max(ext, Math.abs((P[i * 3] - c[0]) * n.x + (P[i * 3 + 1] - c[1]) * n.y + (P[i * 3 + 2] - c[2]) * n.z));
  ext = Math.max(90, ext + 40);
  return { n, c, ext, cross: launchFront(c, n, ext, t0) };
}
const kindOf = s => {
  const k = String(s.kind || '').toLowerCase(), tool = String(s.tool || '');
  if (k === 'memory' || /(^|__)recall$/.test(tool)) return 'memory';
  if (k === 'history' || /search_past/.test(tool)) return 'history';
  if (k === 'search-files' || k === 'files-search' || /^(grep|glob)$/i.test(tool) || /__(grep|glob)$/i.test(tool)) return 'files';
  return k;
};
function queryOf(s) {
  if (s.object && typeof s.object === 'string') return s.object;
  try { const o = typeof s.input === 'string' ? JSON.parse(s.input) : s.input || {}; return String(o.query || o.q || o.pattern || ''); } catch { return ''; }
}
function onStep(s) {
  if (!s) return;
  house.lastAct = state.t;
  if (!turn.active) startTurn(tOf(s.t0));
  const kind = kindOf(s), t0 = Math.max(tOf(s.t0), state.t);
  const primary = valid(s.primary) ? s.primary : (Array.isArray(s.targets) && valid(s.targets[0]) ? s.targets[0] : -1);
  if (kind === 'memory') return memorySearch(s, t0, primary);
  if (kind === 'history') return historySearch(s, t0, primary);
  if (kind === 'files') return filesSearch(s, t0, primary);
  // any other tool: its targets become citation candidates
  if (primary >= 0) litAdd(primary);
}
async function memorySearch(s, t0, primary) {
  let cand = Array.isArray(s.targets) ? s.targets.filter(valid) : [];
  let ids = null;
  if (cand.length <= 1) {                    // the resolver gave only the primary: ask the bridge what recall(q) gets
    const q = queryOf(s);
    if (q) { try { const m = await api('/api/brain/match?q=' + encodeURIComponent(q.slice(0, 300))); ids = (m.ids || []).map(Number); const st = data.starsOfRows(ids); if (st.length) cand = st; } catch { /* keep the resolver's */ } }
  }
  cand = cand.slice(0, TENT_CAP);
  const rec = { kind: 'memory', cand, ids, cross: new Map(), primary, t0 };
  searches.set(s.id, rec);
  if (!cand.length) return;
  const tt = Math.max(t0, state.t);
  const f = frontFor(cand, tt);
  for (const i of cand) { const tk = f.cross(i); rec.cross.set(i, tk); if (i !== primary && writable(i)) put(i, ST.TENTATIVE, tk, 1e9, 0); }
}
function historySearch(s, t0, primary) {
  const a = data.diaryArm(0), b = data.diaryArm(29);
  const c = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2];
  const n = new THREE.Vector3(b[0] - a[0], b[1] - a[1], b[2] - a[2]); const len = n.length(); n.normalize();
  const ext = len / 2 + 60;
  const rec = { kind: 'history', cand: [], cross: null, primary, t0, c, n, ext };
  rec.crossFn = launchFront(c, n, ext, t0);
  searches.set(s.id, rec);
}
function subtree(node, cap) {
  const out = [], q = [node];
  while (q.length && out.length < cap) { const m = q.shift(); for (const k of m.kids || []) { if (k.gone) continue; out.push(k.idx); q.push(k); if (out.length >= cap) break; } }
  return out;
}
function filesSearch(s, t0, primary) {
  const node = nodeOf(primary);
  const rec = { kind: 'files', cand: [], cross: new Map(), primary, t0, node };
  searches.set(s.id, rec);
  if (!node) return;
  const cand = subtree(node, TENT_CAP);
  const list = cand.length ? cand.concat([primary]) : [primary];
  const f = frontFor(list, t0, [P[primary * 3], P[primary * 3 + 1], P[primary * 3 + 2]]);
  rec.f = f; rec.cand = cand;
  for (const i of cand) { const tk = f.cross(i); rec.cross.set(i, tk); if (writable(i)) put(i, ST.TENTATIVE, tk, 1e9, 0); }
}
/** When the step's result may show: after its gaze head has landed and touched (X-2 timing guarantee). */
function doneAt(id) {
  const now = state.t, f = flights.get(id);
  if (!f) return now;
  if (f.landed) return Math.max(now, f.land + .12);
  return Math.min(Math.max(now, f.land), now + .25) + .12;    // gaze re-times an early finish to <= 250 ms
}
function onStepDone(d) {
  if (!d) return;
  house.lastAct = state.t;
  const rec = searches.get(d.id);
  if (!rec) return;
  searches.delete(d.id);
  const res = d.results || {}, ok = d.ok !== false, tD = doneAt(d.id);
  if (rec.kind === 'memory') {
    const rows = Array.isArray(res.rowIds) ? res.rowIds : Array.isArray(res.ids) ? res.ids : null;
    // no parse of the output: what recall(q) returns for that query (the match ids), else every candidate
    const conf = !ok ? [] : rows ? data.starsOfRows(rows) : rec.ids ? data.starsOfRows(rec.ids) : rec.cand.slice();
    const gold = [], tOn = [];
    for (let k = 0; k < conf.length; k++) {
      const i = conf[k]; if (!writable(i)) continue;
      const tk = Math.max(tD, rec.cross.get(i) ?? tD);
      put(i, ST.USED, tk, 1e9, Math.min(k + 1, RANK_MAX)); litAdd(i, k + 1); gold.push(i); tOn.push(tk);
    }
    for (const i of rec.cand) if (!gold.includes(i) && i !== rec.primary) cool(i, tD);
    if (gold.length) {
      const c = centroidNear(gold);
      if (!cite.on) lampTo(0, c[0], c[1], c[2], .8, GOLD, tD);
      if (!turn.recallC) { turn.recallC = c; turn.recallStar = gold[0]; }
      bus.emit('reveal', { stars: gold, ranks: gold.map((_, k) => k + 1), centroid: c, t: Math.min(...tOn), tOn, kind: 'confirm' });
    }
  } else if (rec.kind === 'history') {
    const dates = Array.isArray(res.dates) ? res.dates : [];
    for (const day of dates.slice(0, 30)) {
      const n = data.graph.byKey.get('day:' + String(day).slice(0, 10)); if (!n || !writable(n.idx)) continue;
      put(n.idx, ST.USED, Math.max(tD, rec.crossFn(n.idx)), 1e9, 4); litAdd(n.idx, 0);
    }
  } else if (rec.kind === 'files') {
    const hits = Array.isArray(res.hits) ? res.hits : [];
    const spark = [];
    for (const h of hits.slice(0, SPARK_CAP * 2)) {
      const i = hitStar(h, rec.node); if (i >= 0 && !spark.includes(i)) spark.push(i);
      if (spark.length >= SPARK_CAP) break;
    }
    if (rec.f) { const n = rec.f.n, c = rec.f.c; const along = i => (P[i * 3] - c[0]) * n.x + (P[i * 3 + 1] - c[1]) * n.y + (P[i * 3 + 2] - c[2]) * n.z; spark.sort((a, b) => along(a) - along(b)); }
    let tPrev = -1e9;
    for (const i of spark) {
      const tk = Math.max(tD, rec.cross.get(i) ?? (rec.f ? rec.f.cross(i) : tD), tPrev + SPARK_GAP);
      tPrev = tk;
      if (writable(i)) { put(i, ST.USED, tk, 1e9, 3); litAdd(i); }
    }
    for (const i of rec.cand) if (!spark.includes(i)) cool(i, tD);
    dbg.lastSparks = spark.slice();
  }
}
/** The star for a Grep/Glob hit ('path:line'): the file, else its deepest ancestor on the map. */
function hitStar(h, folder) {
  let p = String(h || '').trim().replace(/(:\d+)+$/, '');
  if (!p) return -1;
  if (folder && !/^([a-zA-Z]:|~|\/)/.test(p) && folder.text) p = folder.text + '/' + p;
  let norm = data.normPath(p).toLowerCase();
  for (let guard = 0; norm && guard < 12; guard++) {
    const n = data.graph.byKey.get('file:' + norm) || data.graph.byKey.get('dir:' + norm);
    if (n && !n.gone) return n === folder ? -1 : n.idx;
    const cut = norm.lastIndexOf('/'); if (cut <= 0) break;
    norm = norm.slice(0, cut);
  }
  return -1;
}

/* ------------------------------------------------------------------ X-6 spoken citations */
const cite = { on: false, star: -1, t: -1e9, prev: null, score: 0, pending: null, timer: 0 };
/** Score a spoken chunk against the stars lit this turn. Returns {best, score, all}. */
export function scoreChunk(text) {
  const tl = [...new Set(terms(text, SPEECH))];
  const cw = new Set(String(text || '').toLowerCase().match(/[a-z0-9]+/g) || []);
  let best = null, bs = 0; const all = [];
  for (const r of lit) {
    let s = 0;
    for (const t of tl) if (r.hay.includes(t.slice(0, 5))) s++;
    for (const nm of r.names) if (nm.every(w => cw.has(w))) { s += 2; break; }
    if (r.fileW && r.fileW.length && r.fileW.every(w => cw.has(w))) s += 1;
    all.push([r.star, s]);
    if (!best || s > bs || (s === bs && better(r, best))) { best = r; bs = s; }
  }
  return { best, score: bs, terms: tl, all };
}
function better(a, b) {
  if (a.star === cite.star) return true; if (b.star === cite.star) return false;   // stability first
  if (Math.abs(a.t - b.t) > .05) return a.t > b.t;                              // then the most recent
  return (a.rank || 99) < (b.rank || 99);                                       // then recall rank
}
function onChunk(e) {
  const text = e && (e.text || e.chunk); if (!text) return;
  house.lastAct = state.t;
  if (!lit.length) return;
  const r = scoreChunk(text);
  dbg.lastScore = { score: r.score, star: r.best ? r.best.star : -1, terms: r.terms };
  want(r.score >= 2 ? r.best.star : -1, r.score);
}
/** Switch the citation, keeping the current one at least 600 ms (never ahead of the audio: chunks are "now"). */
function want(star, score) {
  if (star === cite.star) { cite.pending = null; return; }
  const left = cite.star >= 0 ? cite.t + CITE_HOLD - state.t : 0;
  if (left > .01) {
    cite.pending = { star, score };
    clearTimeout(cite.timer);
    cite.timer = setTimeout(() => { const p = cite.pending; cite.pending = null; if (p && !turn.ended) want(p.star, p.score); }, left * 1000 + 20);
    return;
  }
  cite.pending = null;
  const t = state.t;
  uncite(t, star >= 0);
  if (star < 0) return;
  if (!writable(star)) return;
  const prevSt = stars.stateOf(star);
  cite.prev = prevSt > 0 ? [prevSt, stars.tOnOf(star), stars.tOffOf(star), stars.paramOf(star)] : null;
  stars.setState(star, ST.CITED, t, 1e9, 0); dbg.writes++; owned.add(star);
  cite.on = true; cite.star = star; cite.t = t; cite.score = score; dbg.cites++;
  lampStar(0, star, 1, colourOf(star), t);
  bus.emit('cite', { star, t, score });
}
/** End the current citation: the star returns to what it was (if nobody changed it meanwhile). */
function uncite(t, quiet = false) {
  clearTimeout(cite.timer); cite.pending = null;
  if (cite.star < 0) return;
  const i = cite.star, pv = cite.prev;
  if (valid(i) && stars.stateOf(i) === ST.CITED) {
    if (pv && pv[0] !== ST.CITED) { stars.setState(i, pv[0], pv[1], pv[2], pv[3]); dbg.writes++; if (!MINE.has(pv[0])) owned.delete(i); }
    else cool(i, t);
  }
  cite.on = false; cite.star = -1; cite.prev = null;
  if (turn.recallC && !turn.ended) lampTo(0, turn.recallC[0], turn.recallC[1], turn.recallC[2], .8, GOLD, t);   // back to what she was handed
  if (!quiet) bus.emit('cite:none', { t });
}

/* ------------------------------------------------------------------ X-5 rack focus */
const focus = { ap: 0, want: 0, x: 0, y: 0, z: 0, star: -1, spring: new Spring(2300, 6, 1), was: 0 };
function onShot(s) {
  if (!s) return;
  focus.want = FOCUS_SHOTS.has(String(s.name || '').toUpperCase()) ? 1 : 0;
  focus.star = valid(s.star) ? s.star : -1;
  if (Array.isArray(s.subject) && s.subject.length >= 3) { focus.x = +s.subject[0] || 0; focus.y = +s.subject[1] || 0; focus.z = +s.subject[2] || 0; }
  else if (focus.star >= 0) { focus.x = P[focus.star * 3]; focus.y = P[focus.star * 3 + 1]; focus.z = P[focus.star * 3 + 2]; }
}
/** View depth (-z in view space) of a world point, from the live camera. */
function viewDepth(x, y, z) {
  const c = camera.position, tg = controls.target;
  let fx = tg.x - c.x, fy = tg.y - c.y, fz = tg.z - c.z; const l = Math.hypot(fx, fy, fz) || 1;
  fx /= l; fy /= l; fz /= l;
  return (x - c.x) * fx + (y - c.y) * fy + (z - c.z) * fz;
}

/* ------------------------------------------------------------------ the frame hook (no allocation) */
let usedPulse = 0;
function frame(dt, t) {
  // pinch: ring sharpness x1.6 and gain +25 % (120 ms inQuad), then out over 300 ms
  if (fx.pinchT > -1e8) {
    const a = t - fx.pinchT; let e = 0;
    if (a >= 0 && a < PINCH_UP) { const k = a / PINCH_UP; e = k * k; }
    else if (a >= PINCH_UP && a < PINCH_UP + PINCH_DOWN) { const k = 1 - (a - PINCH_UP) / PINCH_DOWN; e = k * k * k; }
    else if (a >= PINCH_UP + PINCH_DOWN) fx.pinchT = -1e9;
    if (e > 0) { pulse.ringSharp += .6 * e * fx.pinchA; pulse.ring += .25 * e * fx.pinchA; }
  }
  if (fx.sonarEnd > -1e8 && t > fx.sonarEnd) { fxu.uSonar.value.z = 0; fx.sonarEnd = -1e9; }
  if (fx.frontEnd > -1e8 && t > fx.frontEnd) { fxu.uFrontN.value.w = 0; fx.frontEnd = -1e9; }
  // empty recall: exposure .9 for 600 ms
  if (fx.dipT > -1e8) {
    const a = t - fx.dipT;
    if (a >= .6) fx.dipT = -1e9;
    else if (a >= 0) pulse.expo -= .1 * Math.min(1, a / .1) * Math.min(1, (.6 - a) / .15);
  }
  // house lights: down (core eases ~450 ms), restore 2.5 s after speaking ends over 1.8 s
  if (house.downAt > -1e8 && t >= house.downAt) { house.downAt = -1e9; house.isDown = true; house.rampT = -1e9; look.house = 1; look.dust = .6; }
  if (house.isDown && house.restoreAt < -1e8 && house.rampT < -1e8 && !turn.speaking && t - house.lastAct > 45) house.restoreAt = t;   // nobody ended the turn
  if (house.isDown && house.restoreAt > -1e8 && t >= house.restoreAt) { house.rampT = house.restoreAt; house.restoreAt = -1e9; }
  if (house.rampT > -1e8) {
    const k = clamp01((t - house.rampT) / HOUSE_RAMP), e = easings.inOutSine(k);
    look.house = 1 - e; look.dust = .6 + .4 * e;
    if (k >= 1) { house.rampT = -1e9; house.isDown = false; look.house = 0; look.dust = 1; }
  }
  // pours: arrivals warm the disk (+.03 each over the stream's tail, cap .15, tau 1.4 s)
  let add = 0, live = 0;
  for (let k = 0; k < POURS; k++) {
    const B = pour.uB[k]; if (B.x <= 0) continue;
    const a = (t - pour.uA[k].w) / B.x;
    if (a > 1.22) { B.x = 0; continue; }
    live++;
    const arr = clamp01((a - 1) / .18);
    if (arr > pour.fed[k]) { add += FEED_STEP * B.w * (arr - pour.fed[k]); pour.fed[k] = arr; }
  }
  if (pour.pts.visible !== live > 0) pour.pts.visible = live > 0;
  const uf = bh.uniforms.uFeed, fv = uf.value * Math.exp(-dt / FEED_TAU) + add;
  uf.value = fv < 1e-4 ? 0 : fv > FEED_CAP ? FEED_CAP : fv;
  // used stars breathe with her voice while she speaks and quotes nothing
  const up = turn.speaking && !cite.on && owned.size ? .15 : 0;
  usedPulse += (up - usedPulse) * Math.min(1, dt * 4);
  const uu = stars.material.uniforms.uUsedPulse;
  if (uu) { uu.value = usedPulse; lamps[0].mod = 0; }
  else lamps[0].mod = lamps[0].star < 0 && turn.recallC ? usedPulse * 2.3 * state.drive : 0;   // fallback: lamp 0 on the handed set
  lampFrame(lamps[0], 0, t, dt); lampFrame(lamps[1], 1, t, dt);
  // rack focus: aperture 400 ms in / 800 ms out; focus distance on a spring (w 6) to the subject
  const F = focus;
  if (F.want > F.ap) F.ap = Math.min(F.want, F.ap + dt / .4); else if (F.want < F.ap) F.ap = Math.max(F.want, F.ap - dt / .8);
  if (F.ap > 0) {
    if (F.star >= 0) { F.x = P[F.star * 3]; F.y = P[F.star * 3 + 1]; F.z = P[F.star * 3 + 2]; }
    if (F.was <= 0) F.spring.snap(viewDepth(controls.target.x, controls.target.y, controls.target.z));
    F.spring.target = Math.max(10, viewDepth(F.x, F.y, F.z)); F.spring.step(dt);
    fxu.uFocus.value.set(F.spring.x, F.ap * F.ap * (3 - 2 * F.ap));
  } else if (F.was > 0) fxu.uFocus.value.y = 0;
  F.was = F.ap;
}

/* ------------------------------------------------------------------ init */
export const debug = dbg;
export async function init() {
  bus.on('recall', onRecall);
  bus.on('step', onStep);
  bus.on('step:done', onStepDone);
  bus.on('speak:chunk', onChunk);
  bus.on('shot', onShot);
  bus.on('turn:start', e => {
    const t = tOf(e && e.t0);
    if (turn.active && t - turn.t0 > 3) endTurn();          // the last turn never ended: close it first
    startTurn(t);
  });
  bus.on('turn:end', () => endTurn());
  let lastMode = '';
  bus.on('mode', e => {
    const m = e && e.mode, was = turn.speaking;
    lastMode = m; turn.speaking = m === 'speaking';
    if (turn.speaking) {
      house.restoreAt = -1e9; house.lastAct = state.t;
      if (house.rampT > -1e8 && turn.active) { house.rampT = -1e9; house.downAt = state.t; }   // she speaks again this turn
    } else if (was) { uncite(state.t); houseRestore(state.t + HOUSE_HOLD); }
    if (m === 'idle' && turn.active) setTimeout(() => { if (turn.active && lastMode === 'idle') endTurn(); }, 2500);
  });
  bus.on('gaze:launch', g => {
    if (!g || g.id == null) return;
    let dur = +g.dur || 0; if (dur > 5) dur /= 1000;                 // ms or s
    flights.set(g.id, { star: g.star, land: tOf(g.t0) + dur, landed: false });
  });
  bus.on('gaze:land', g => {
    if (!g || !valid(g.star)) return;
    const f = flights.get(g.id); const t = typeof g.t === 'number' ? g.t : state.t;
    if (f) { f.land = t; f.landed = true; } else if (g.id != null) flights.set(g.id, { star: g.star, land: t, landed: true });
    litAdd(g.star);
    if (!cite.on && !turn.ended) lampStar(0, g.star, colourOf(g.star) === GOLD ? .8 : .7, colourOf(g.star), t);
  });
  bus.on('graph:gone', e => {
    for (const n of (e && e.nodes) || []) {
      owned.delete(n.idx); soft.delete(n.idx);
      const k = lit.findIndex(r => r.star === n.idx); if (k >= 0) lit.splice(k, 1);
      if (cite.star === n.idx) { cite.star = -1; cite.on = false; cite.prev = null; }
      for (const L of lamps) if (L.star === n.idx) L.star = -1;
    }
  });
  bus.on('resize', e => { pour.material.uniforms.uPR.value = (e && e.pr) || pixelRatio(); });
  bus.on('clock:rebase', e => {
    const s = +(e && e.shift) || 0; if (!s) return;
    for (const v of pour.uA) v.w -= s;
    for (const k of ['pinchT', 'sonarEnd', 'dipT', 'frontEnd']) if (fx[k] > -1e8) fx[k] -= s;
    for (const k of ['downAt', 'restoreAt', 'rampT']) if (house[k] > -1e8) house[k] -= s;
    house.lastAct -= s; turn.t0 -= s; turn.prevT0 -= s; cite.t -= s;
    if (cite.prev) { cite.prev[1] -= s; if (cite.prev[2] < 1e8) cite.prev[2] -= s; }
    for (const L of lamps) { L.aT -= s; L.dT -= s; if (L.gT >= 0) L.gT -= s; if (L.pAt > -1e8) L.pAt -= s; }
    for (const r of lit) r.t -= s;
    for (const f of flights.values()) f.land -= s;
    for (const r of searches.values()) { r.t0 -= s; if (r.cross) for (const [i, v] of r.cross) r.cross.set(i, v - s); }
  });
  onFrame(frame, 20);
  window.__brain = Object.assign(window.__brain || {}, {});
  window.__brain.reveal = { debug: dbg, terms, scoreChunk, lamps, focus, pour, lit, cite, house, turn };
}
