// Nova Brain -- director: the camera, the black hole's moods, her voice drive,
// and the handoff with the panel (SPEC 4.2 D-1..D-5, condensed).
// Owns: camera.position, controls.target, state.director, state.viewOff,
// look.bh.*, look.ts, look.tintCol, bh.uBand/uIgnite/uWince, state.drive,
// voice.write(), finish.uGlass/uGlassK/uEdge. Emits 'shot' and 'letterbox'.

import {
  THREE, bus, state, look, pulse, camera, controls, bh, finish, voice, stars, onFrame, Spring, easings,
  minHoleDistance, project, REDUCED, EMBED, HOME_DIST, wake,
} from './core.js';

const D2R = Math.PI / 180;
const HOME_EL = 11.3 * D2R;
const tmpV = new THREE.Vector3(), tmpP = [0, 0, 0];

/* ------------------------------------------------------------------ rig */
// Spherical springs around a look target. Fixed 120 Hz substeps inside Spring.
const rig = {
  tx: new Spring(0, 4.5), ty: new Spring(0, 4.5), tz: new Spring(0, 4.5),
  az: new Spring(0, 3.2), el: new Spring(HOME_EL, 3.2), dist: new Spring(HOME_DIST, 3.2),
};
let follow = true, parked = false, resumeRamp = 1, drift = 0;
let shot = { name: 'ESTABLISH', since: 0, until: 0, prio: 0 };
let pending = null;
const PRIO = { user: 6, GENESIS: 5, BIRTH: 4, FOCUS: 3, TWO_SHOT: 3, GROUP: 3, REVEAL: 2.5, SEARCH: 2, LEAN_IN: 1, HOME: 0, ESTABLISH: 0 };

function seedFromCamera() {
  const t = controls.target;
  tmpV.copy(camera.position).sub(t);
  const d = Math.max(1, tmpV.length());
  rig.tx.snap(t.x); rig.ty.snap(t.y); rig.tz.snap(t.z);
  rig.dist.snap(d); rig.el.snap(Math.asin(Math.max(-1, Math.min(1, tmpV.y / d)))); rig.az.snap(Math.atan2(tmpV.x, tmpV.z));
}
/** Nearest equivalent of angle a to reference r (keeps the azimuth spring continuous). */
function unwrap(a, r) { while (a - r > Math.PI) a -= Math.PI * 2; while (a - r < -Math.PI) a += Math.PI * 2; return a; }
function limitTurn(a, r, max) { const d = unwrap(a, r) - r; return r + Math.max(-max, Math.min(max, d)); }

function aim(target, dist, el, az, prioName = 'ESTABLISH') {
  rig.tx.target = target[0]; rig.ty.target = target[1]; rig.tz.target = target[2];
  rig.dist.target = dist; rig.el.target = el; rig.az.target = unwrap(az, rig.az.x);
}

/* ------------------------------------------------------------------ shots */
const turnStops = [];          // star indices she visited this turn (for REVEAL)
let lastRecall = null;
function starPos(i) { return [stars.pos[i*3], stars.pos[i*3+1], stars.pos[i*3+2]]; }
function centroid(pts) { const c = [0, 0, 0]; for (const p of pts) { c[0] += p[0]; c[1] += p[1]; c[2] += p[2]; } const n = Math.max(1, pts.length); return [c[0]/n, c[1]/n, c[2]/n]; }
function radius(pts, c) { let r = 0; for (const p of pts) r = Math.max(r, Math.hypot(p[0]-c[0], p[1]-c[1], p[2]-c[2])); return r; }
function fitDist(r) { const fov = camera.fov * D2R, free = Math.min(1, (innerWidth - state.insetRight) / Math.max(1, innerHeight) / camera.aspect); return r * 1.25 / Math.sin(Math.min(fov, fov * free * camera.aspect) / 2); }

/** Ask for a shot. Higher priority wins; bursts merge.
 *  Every move gets COMMIT_MS to land before the next one starts (Sir, 2026-11-20: the camera
 *  "flicks back and forth before locking on" -- a recall shot cut off mid-swing by the first
 *  tool, then that tool's shot cut off by the group). Only a clearly bigger moment (birth,
 *  genesis) or his own click cuts in at once; everything else waits as `pending` (latest wins). */
const COMMIT_MS = 1600;
function request(name, plan, holdMs = 1200) {
  if (REDUCED && name !== 'ESTABLISH') return;
  const now = performance.now();
  const p = PRIO[name] ?? 1;
  if (now < shot.until && p < shot.prio) { return; }
  const cutIn = name === 'FOCUS' || p >= shot.prio + 2;
  if (now - shot.since < COMMIT_MS && !cutIn) { if (!pending || p >= (PRIO[pending.name] ?? 1)) pending = { name, plan, holdMs }; return; }
  apply(name, plan, holdMs);
}
function apply(name, plan, holdMs) {
  const now = performance.now();
  shot = { name, since: now, until: now + holdMs, prio: PRIO[name] ?? 1 };
  pending = null;
  plan();
  bus.emit('shot', { name, subject: [rig.tx.target, rig.ty.target, rig.tz.target], star: shotStar, t: state.t });
  wake();
}
let shotStar = -1;

const sideOf = c => {      // azimuth that looks across the hole->c line (perpendicular), nearest to now
  const a = Math.atan2(c[0], c[2]);
  const s1 = unwrap(a + Math.PI / 2, rig.az.x), s2 = unwrap(a - Math.PI / 2, rig.az.x);
  return Math.abs(s1 - rig.az.x) < Math.abs(s2 - rig.az.x) ? s1 : s2;
};
function establish() { shotStar = -1; aim([0, 0, 0], HOME_DIST, HOME_EL, rig.az.x); }
function leanIn() { shotStar = -1; aim([0, 0, 0], HOME_DIST * .86, 6 * D2R, rig.az.x); }
/** Hits far from the hole: look in from just outside them, hole glowing behind (keeps the coverage rule for free). */
function frameOutward(list, { el = 13 * D2R, minDist = 520 } = {}) {
  const pts = list.map(starPos); if (!pts.length) return establish();
  const c = centroid(pts), r = Math.max(90, radius(pts, c)), cd = Math.hypot(c[0], c[2]);
  const out = Math.atan2(c[0], c[2]);                       // camera beyond the cluster, looking back toward the hole
  const cand = [out - 38 * D2R, out + 38 * D2R].map(a => unwrap(a, rig.az.x));
  const az = Math.abs(cand[0] - rig.az.x) < Math.abs(cand[1] - rig.az.x) ? cand[0] : cand[1];
  const k = Math.max(.72, 1 - 260 / Math.max(cd, 1));        // aim a little inside the cluster so the hole shows behind
  aim([c[0] * k, c[1], c[2] * k], Math.min(2400, Math.max(minDist, fitDist(r) * .9)), el, az);
}
function farFromHole(list) { const c = centroid(list.map(starPos)); return Math.hypot(c[0], c[1], c[2]) > 600; }
function frameStars(list, { include0 = true, el = null, maxTurn = 35 * D2R, minD = 0 } = {}) {
  const pts = list.map(starPos); if (include0) pts.push([0, 0, 0]);
  if (!pts.length) return establish();
  const c = centroid(pts), r = Math.max(120, radius(pts, c));
  const az = limitTurn(include0 ? sideOf(c) : rig.az.x, rig.az.x, maxTurn);
  aim(c, Math.min(4200, Math.max(minD, fitDist(r))), el ?? Math.max(8 * D2R, Math.min(24 * D2R, rig.el.target)), az);
}

/* ------------------------------------------------------------------ moods (D-4) */
const MOODS = {
  idle:      { spin: 1,   tempExp: 1.35, ring: 1,    ringSharp: 16, expo: 1,    ts: 1 },
  listening: { spin: .8,  tempExp: 1.35, ring: 1.10, ringSharp: 22, expo: 1.08, ts: .55 },
  thinking:  { spin: 2.0, tempExp: 1.15, ring: 1.10, ringSharp: 16, expo: 1.05, ts: 1 },
  tool:      { spin: 1.4, tempExp: 1.25, ring: 1.10, ringSharp: 16, expo: 1,    ts: 1 },
  speaking:  { spin: 1.1, tempExp: 1.30, ring: 1,    ringSharp: 16, expo: 1,    ts: 1 },
  remember:  { spin: 1,   tempExp: 1.35, ring: 1,    ringSharp: 16, expo: 1,    ts: .6 },
  approval:  { spin: .3,  tempExp: 1.35, ring: .85,  ringSharp: 18, expo: .9,   ts: .12 },
};
function setMood(mode) {
  const m = MOODS[mode] || MOODS.idle;
  look.bh.spin = m.spin; look.bh.tempExp = m.tempExp; look.bh.ring = m.ring; look.bh.ringSharp = m.ringSharp; look.bh.expo = m.expo;
  if (!rewinding) look.ts = m.ts;
  look.bh.tintA = mode === 'approval' ? .25 : 0; look.tintCol = [1, .72, .35];
  look.bh.ringR = state.flavour === 'looking' ? 1.53 * .95 : 1.53;
}
let rememberAt = -1e9, absorbAt = -1e9, absorbAmt = 0, winceAt = -1e9;

/* ------------------------------------------------------------------ drive (her voice) */
let driveRaw = 0, lastVoiceWrite = 0;
function readDrive(dt) {
  let target = 0;
  if (EMBED) {
    try { const c = window.parent && window.parent.__novaCore; if (c && typeof c.speakEnv === 'number') target = c.speakEnv; else target = levelDrive(); }
    catch { target = levelDrive(); }
  } else target = levelDrive();
  target += impulse(); if (state.game) target *= .85;
  driveRaw = Math.max(target, driveRaw * Math.pow(.94, dt * 60));
  state.drive += (Math.min(1, driveRaw) - state.drive) * (1 - Math.exp(-18 * dt));
}
function levelDrive() { return state.mode === 'speaking' && state.level > .01 ? Math.min(1, .25 + state.level * 5) : 0; }
function impulse() {
  const t = performance.now(), k = state.game ? .6 : 1;
  return k * (absorbAmt * Math.exp(-(t - absorbAt) / 260) + .7 * Math.exp(-(t - rememberAt) / 320));
}

/* ------------------------------------------------------------------ handoff (D-2) */
const hand = { phase: 'none', t0: 0, ms: 620, windup: 100, from: [0, 0], glass: null, inset: 396 };
const off = [0, 0];
function orbOffset(o) { return [innerWidth / 2 - (o ? o.x : innerWidth / 2), innerHeight / 2 - (o ? o.y : innerHeight / 2)]; }
function t0Local(epoch) { return (+epoch || 0) - performance.timeOrigin; }

/* ------------------------------------------------------------------ set pieces */
let rewinding = false, genesisUntil = 0;
function rewind(days = 3) {
  if (REDUCED || state.game || rewinding) return;
  rewinding = true;
  look.ts = -.8;
  const hold = 300 + Math.min(1000, 400 + 20 * days);
  setTimeout(() => { look.ts = 1.3; setTimeout(() => { rewinding = false; look.ts = MOODS[state.mode]?.ts ?? 1; }, 600); }, hold);
}

/* ------------------------------------------------------------------ init */
export async function init() {
  state.director = true;
  controls.autoRotate = false;
  seedFromCamera();

  bus.on('start', () => { seedFromCamera(); establish(); rig.az.snap(0); });
  bus.on('follow', e => { follow = !!(e && e.on); if (follow) { parked = false; seedFromCamera(); resumeRamp = .6; } });
  bus.on('user:grab', () => { parked = true; controls.enableDamping = true; });

  bus.on('mode', e => {
    const m = e.mode;
    setMood(m);
    if (m === 'listening') { request('LEAN_IN', leanIn); if (!EMBED) bh.uniforms.uIgnite.value = state.t; }
    if (m === 'speaking') revealSoon();
    if (m === 'remember') { rememberAt = performance.now(); bh.uniforms.uBand.value.set(state.t, 1); }
    if (state.flavour === 'fault') winceAt = performance.now();
  });
  bus.on('turn:start', () => { turnStops.length = 0; lastRecall = null; });
  bus.on('recall', e => {
    lastRecall = e;
    if (e.days && e.days.length) rewind(e.days.length);
    if (e.stars && e.stars.length) { const hits = e.stars.slice(0, 8);
      request('SEARCH', () => { shotStar = hits[0]; farFromHole(hits) ? frameOutward(hits) : frameStars(hits, { minD: minHoleDistance() }); }, 1600); }
  });
  // tools: one leg = two-shot; several within 2.5 s = group. Parallel calls land within a few
  // ms of each other, so a burst is gathered for a beat and framed in ONE move (the beams
  // themselves still leave at once -- fx-gaze launches them on the step, not on the shot).
  let burst = [], burstAt = 0, burstTimer = 0, lone = null;
  const flushBurst = () => {
    const b = [...new Set(burst)].slice(-6);
    if (!b.length) return;
    if (b.length === 1 && lone) { const e = lone; request('TWO_SHOT', () => { shotStar = e.primary; const t = (e.targets && e.targets.length ? e.targets : [e.primary]).slice(0, 6);
      farFromHole(t) ? frameOutward(t, { minDist: 460 }) : frameStars(t, { minD: minHoleDistance() }); }, 1400); }
    else request('GROUP', () => { shotStar = b[b.length - 1]; farFromHole(b) && radius(b.map(starPos), centroid(b.map(starPos))) < 700 ? frameOutward(b) : frameStars(b, { minD: minHoleDistance() }); }, 1400);
  };
  bus.on('step', e => {
    if (e.primary == null || e.primary < 0) return;
    if (e.tool === 'day_log') rewind(1);
    turnStops.push(e.primary);
    const now = performance.now();
    if (now - burstAt > 2500) { burst = []; lone = null; }
    burst.push(e.primary); burstAt = now;
    if (burst.length === 1) lone = e;
    shotStar = e.primary;
    clearTimeout(burstTimer); burstTimer = setTimeout(flushBurst, 240);
  });
  bus.on('step:hold', () => request('HOLD', () => {}, 4000));
  bus.on('absorb', e => { absorbAt = performance.now(); absorbAmt = .5 * ((e && e.amount) || 1); });
  bus.on('turn:end', () => { setTimeout(() => { if (!state.turn) request('HOME', () => { shotStar = -1; aim([0, 0, 0], HOME_DIST, HOME_EL, rig.az.x); }, 2200); }, 3500); });
  bus.on('click', e => { if (e.idx >= 0) request('FOCUS', () => { shotStar = e.idx; const p = starPos(e.idx); const d = Math.hypot(p[0], p[1], p[2]);
    aim(p, Math.max(640, Math.min(900, d * .9)), Math.max(10 * D2R, rig.el.x), rig.az.x); }, 6000); });
  bus.on('click:empty', () => { if (shot.name === 'FOCUS') request('HOME', establish, 1200); });
  bus.on('birth:start', e => {
    if (parked || !follow || performance.now() < genesisUntil) return;
    const s = e.slot || starPos(e.star), r = Math.hypot(s[0], s[1], s[2]);
    request('BIRTH', () => {
      shotStar = e.star ?? -1;
      if (r >= 900) aim(s, 900, 16 * D2R, limitTurn(Math.atan2(s[0], s[2]), rig.az.x, 60 * D2R));
      else { const c = [s[0] * .6, s[1] * .6, s[2] * .6]; aim(c, Math.max(1700, minHoleDistance(), fitDist(r * .6)), 14 * D2R, limitTurn(sideOf(s), rig.az.x, 40 * D2R)); }
    }, 4800);
    setTimeout(() => { if (shot.name === 'BIRTH') request('HOME', establish, 1200); }, 5200);
  });
  bus.on('genesis', e => {
    if (!e || e.phase !== 'start') return;
    const quick = state.game || !state.focused || REDUCED;
    const ms = quick ? 2000 : 7400;
    genesisUntil = performance.now() + ms;
    if (!quick) bus.emit('letterbox', { on: true });
    if (!quick && e.centre) request('GENESIS', () => {
      const c = e.centre; shotStar = -1;
      const mid = [c[0] * .55, c[1] * .55, c[2] * .55];
      aim(mid, Math.max(1700, minHoleDistance(), fitDist(Math.hypot(c[0], c[1], c[2]) * .6)), 20 * D2R, limitTurn(sideOf(c) + 35 * D2R, rig.az.x, 70 * D2R));
      heroUntil = performance.now() + 3600; heroRate = 40 * D2R / 3.6;
    }, ms);
    setTimeout(() => { bus.emit('letterbox', { on: false }); if (shot.name === 'GENESIS') request('HOME', establish, 1200); }, ms);
  });
  bus.on('graph:gone', () => {});
  bus.on('clock:rebase', e => { const s = e.shift || 0; const b = bh.uniforms.uBand.value; b.x -= s; if (bh.uniforms.uIgnite.value >= 0) bh.uniforms.uIgnite.value -= s; });

  // handoff with the panel
  bus.on('host:wake', m => {
    hand.phase = 'orb'; hand.from = orbOffset(m.orb);
    state.viewOffOwned = true; off[0] = hand.from[0]; off[1] = hand.from[1];
    seedFromCamera(); aim([0, 0, 0], rig.dist.x, rig.el.x, rig.az.x);
    rig.tx.snap(0); rig.ty.snap(0); rig.tz.snap(0);
    bh.uniforms.uIgnite.value = state.t;
    request('LEAN_IN', leanIn);
  });
  bus.on('host:falsewake', () => { if (hand.phase === 'orb') hand.phase = 'none'; });
  bus.on('host:glide', m => {
    hand.phase = 'glide'; hand.t0 = t0Local(m.t0Epoch) + (m.windupMs || 100); hand.ms = m.slideMs || 620;
    hand.inset = m.inset || state.insetRight || 396; hand.glass = m.glass || null;
    if (hand.from[0] === 0 && hand.from[1] === 0) hand.from = orbOffset(m.from ? { x: m.from.x, y: m.from.y } : null);
    state.viewOffOwned = true;
  });
  bus.on('host:retarget', m => { if (m.inset) hand.inset = m.inset; if (m.glass) hand.glass = m.glass; });
  bus.on('host:collapse', m => { hand.phase = 'collapse'; hand.t0 = t0Local(m.t0Epoch); hand.ms = m.ms || 700; });

  onFrame(frame, -10);
}

let heroUntil = 0, heroRate = 0, lastEdge = 0, revealTimer = 0;
function revealSoon() {
  clearTimeout(revealTimer);
  revealTimer = setTimeout(() => {
    const stops = [...new Set(turnStops)];
    if (lastRecall && lastRecall.stars) for (const s of lastRecall.stars.slice(0, 3)) stops.push(s);
    if (!stops.length) return;
    request('REVEAL', () => frameStars(stops, { el: 18 * D2R, maxTurn: 30 * D2R, minD: minHoleDistance() }), 2400);
  }, 250);
}

/* ------------------------------------------------------------------ frame */
function frame(dt, t, now) {
  // pending merged shot
  if (pending && now - shot.since >= COMMIT_MS) apply(pending.name, pending.plan, pending.holdMs);

  // moods that breathe with time / voice (additive, never fights the eased base)
  readDrive(dt);
  if (now - lastVoiceWrite > 100) { voice.write(state.drive); lastVoiceWrite = now; }
  if (state.mode === 'thinking') pulse.ring += .08 * Math.sin(t * TAU8);
  if (state.mode === 'speaking') { const d = state.drive; pulse.ring += .25 * d; pulse.ringSharp += 1 / (1 + .33 * d) - 1; pulse.expo += .12 * d; }
  pulse.expo += .2 * Math.exp(-(now - rememberAt) / 400);
  const w = (now - winceAt) / 1000;
  bh.uniforms.uWince.value = w < .12 ? w / .12 : w < 1.02 ? 1 - (w - .12) / .9 : 0;
  if (bh.uniforms.uIgnite.value >= 0 && t - bh.uniforms.uIgnite.value > 1.2) bh.uniforms.uIgnite.value = -1;
  if (bh.uniforms.uBand.value.y > .5 && t - bh.uniforms.uBand.value.x > 1.3) bh.uniforms.uBand.value.y = 0;

  // handoff composition
  if (state.viewOffOwned) {
    let p = 1;
    if (hand.phase === 'orb') p = 0;
    else if (hand.phase === 'glide') p = easings.EASE(Math.max(0, Math.min(1, (now - hand.t0) / hand.ms)));
    else if (hand.phase === 'collapse') p = 1 - easings.inOutCubic(Math.max(0, Math.min(1, (now - hand.t0) / hand.ms)));
    const ex = hand.inset / 2, ey = 0;
    off[0] = hand.from[0] + (ex - hand.from[0]) * p; off[1] = hand.from[1] + (ey - hand.from[1]) * p;
    state.viewOff[0] = off[0]; state.viewOff[1] = off[1];
    const g = hand.glass;
    if (g && hand.phase !== 'none') { finish.uniforms.uGlass.value.set(g.x0, g.y0, g.x1, g.y1); finish.uniforms.uGlassK.value = p; }
    if (hand.phase === 'collapse' && p <= 0) { hand.phase = 'none'; finish.uniforms.uGlassK.value = 0; }
  }

  // camera: the user has it
  if (parked) {
    if (!follow) return;
    if (now - state.userActiveAt < 8000) return;
    parked = false; controls.enableDamping = false; seedFromCamera(); resumeRamp = .6;
    request('HOME', () => aim([0, 0, 0], HOME_DIST, HOME_EL, rig.az.x), 1200);
  }
  if (!follow) return;

  // idle drift / hero orbit
  if (!REDUCED && !state.game) {
    if (now < heroUntil) rig.az.target += heroRate * dt;
    else if (shot.name === 'ESTABLISH' || shot.name === 'HOME') rig.az.target += 1.08 * D2R * dt;
    else rig.az.target += .35 * D2R * dt;
  }
  resumeRamp = Math.min(1, resumeRamp + dt * .2);
  const azMax = (state.game ? 15 : 25) * D2R;
  for (const s of [rig.tx, rig.ty, rig.tz, rig.az, rig.el, rig.dist]) s.w = (s === rig.tx || s === rig.ty || s === rig.tz ? 4.5 : shot.name === 'REVEAL' ? 2.4 : 3.2) * resumeRamp;
  rig.tx.step(dt); rig.ty.step(dt); rig.tz.step(dt); rig.el.step(dt); rig.dist.step(dt);
  rig.az.v = Math.max(-azMax, Math.min(azMax, rig.az.v)); rig.az.step(dt);
  rig.el.x = Math.max(-25 * D2R, Math.min(60 * D2R, rig.el.x));

  const tx = rig.tx.x, ty = rig.ty.x, tz = rig.tz.x;
  let d = rig.dist.x;
  const ce = Math.cos(rig.el.x), se = Math.sin(rig.el.x), sa = Math.sin(rig.az.x), ca = Math.cos(rig.az.x);
  // coverage: keep the camera far enough from the hole (hole disc <= 30 % of the screen, >= 640 u)
  const minD = minHoleDistance();
  for (let k = 0; k < 4; k++) {
    const px = tx + d * ce * sa, py = ty + d * se, pz = tz + d * ce * ca;
    if (Math.hypot(px, py, pz) >= minD) break;
    d *= 1.15;
  }
  camera.position.set(tx + d * ce * sa, ty + d * se, tz + d * ce * ca);
  controls.target.set(tx, ty, tz);

  // soft edge light toward an off-screen subject
  if (shotStar >= 0 && now - lastEdge > 80) {
    lastEdge = now;
    const P = stars.pos, i = shotStar * 3;
    const on = project(P[i], P[i+1], P[i+2], tmpP);
    const offscreen = !on || tmpP[0] < 0 || tmpP[1] < 0 || tmpP[0] > innerWidth - state.insetRight || tmpP[1] > innerHeight;
    if (offscreen && on) finish.uniforms.uEdge.value.set(Math.max(0, Math.min(innerWidth - state.insetRight, tmpP[0])), Math.max(0, Math.min(innerHeight, tmpP[1])), .12, 0);
    else finish.uniforms.uEdge.value.z = 0;
  } else if (shotStar < 0) finish.uniforms.uEdge.value.z = 0;
}
const TAU8 = Math.PI * 2 * .8;

window.__brain = Object.assign(window.__brain || {}, { director: { rig, get shot() { return shot.name; }, request } });
