// Nova Brain -- fx-life: memories being born, whole constellations forming,
// and memories falling into the hole (SPEC 4.1 X-9, X-10, X-11, condensed).
// Owns star states BIRTH / FALLING / LOOP, fxu.uDustFx[0..3], fxu.uWell[0..3].

import {
  THREE, scene, camera, bus, state, stars, edges, fxu, bh, pulse, onFrame, pixelRatio, toHost,
  ST, RS, easings, REDUCED, wake,
} from './core.js';
import { graph, nodeOf } from './data.js';

const TAU = Math.PI * 2;

/* ------------------------------------------------------------------ motes (sparks) */
const NM = 2048, PER = 48;
const mp = new Float32Array(NM * 3), ma = new Float32Array(NM), ms = new Float32Array(NM);
const mg = new THREE.BufferGeometry();
const A = (arr, n) => { const a = new THREE.BufferAttribute(arr, n); a.setUsage(THREE.DynamicDrawUsage); return a; };
const aMP = A(mp, 3), aMA = A(ma, 1), aMS = A(ms, 1);
mg.setAttribute('position', aMP); mg.setAttribute('aA', aMA); mg.setAttribute('aS', aMS);
const moteMat = new THREE.ShaderMaterial({
  transparent: true, depthWrite: false, depthTest: true, blending: THREE.AdditiveBlending,
  uniforms: { uPR: { value: pixelRatio() } },
  vertexShader: `attribute float aA, aS; uniform float uPR; varying float vA;
    void main(){ vA=aA; gl_PointSize=aS*uPR; gl_Position=aA>.001 ? projectionMatrix*modelViewMatrix*vec4(position,1.) : vec4(2.,2.,2.,1.); }`,
  fragmentShader: `varying float vA; void main(){ float d=length(gl_PointCoord-.5)*2.; float a=exp(-d*d*5.)*vA; gl_FragColor=vec4(vec3(1.,.82,.55)*a,a); }`,
});
const motes = new THREE.Points(mg, moteMat); motes.frustumCulled = false; motes.renderOrder = 3; scene.add(motes);
const moteFree = []; for (let b = NM / PER - 1; b >= 0; b--) moteFree.push(b);

/* ------------------------------------------------------------------ filament (genesis) */
const FS = 160;
const fp = new Float32Array(FS * 2 * 3), fu = new Float32Array(FS * 2);
for (let s = 0; s < FS; s++) { fu[s*2] = s / FS; fu[s*2+1] = (s + 1) / FS; }
const fg = new THREE.BufferGeometry();
const aFP = A(fp, 3); fg.setAttribute('position', aFP); fg.setAttribute('aU', new THREE.BufferAttribute(fu, 1));
const filMat = new THREE.ShaderMaterial({
  transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  uniforms: { uHead: { value: 0 }, uA: { value: 0 } },
  vertexShader: `attribute float aU; varying float vU; void main(){ vU=aU; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.); }`,
  fragmentShader: `uniform float uHead, uA; varying float vU; void main(){ if(vU>uHead) discard; float a=(.35+.65*smoothstep(uHead-.25,uHead,vU))*uA; gl_FragColor=vec4(vec3(1.,.85,.62)*a,a); }`,
});
const filament = new THREE.LineSegments(fg, filMat); filament.frustumCulled = false; filament.visible = false; scene.add(filament);

/* ------------------------------------------------------------------ helpers */
const P = i => [stars.pos[i*3], stars.pos[i*3+1], stars.pos[i*3+2]];
/** The disk's Doppler-bright side: where it moves toward the camera. */
function brightSide(r = 135) {
  const cx = camera.position.x, cz = camera.position.z, th = Math.atan2(-cx, cz);
  return { th, p: [Math.cos(th) * r, 0, Math.sin(th) * r] };
}
let busyN = 0;
function busy(on) { busyN = Math.max(0, busyN + (on ? 1 : -1)); toHost({ type: 'busy', on: busyN > 0 }); }
let dustSlot = 0;
function dustFx(kind, pos, radius, strength, dur, t0) {
  const i = dustSlot++ % 4;
  fxu.uDustFx.value[i].set(pos[0], pos[1], pos[2], t0);
  fxu.uDustFxP.value[i].set(kind, radius, strength, dur);
}

/* ------------------------------------------------------------------ births */
const births = [];          // live choreographies
const queue = [];           // waiting (spacing >= 900 ms, or hidden)
let lastBirth = -1e9;
function requestBirth(e) {
  const star = e.stars && e.stars.length ? e.stars[0] : -1;
  if (star < 0) return;
  if (queue.length >= 12) queue.shift();
  queue.push({ star, kind: e.kind, extra: (e.stars || []).slice(1) });
}
function startBirth(q) {
  const t = state.t, star = q.star, slot = P(star);
  const block = moteFree.length ? moteFree.pop() : -1;
  const ign = t + (REDUCED ? .2 : 1.33);
  stars.setState(star, ST.BIRTH, ign, 1e9, 0);            // hidden until ignition; spring + colour ramp in the shader
  const bs = brightSide();
  const b = { star, slot, kind: q.kind, t0: t, ign, block, th0: bs.th, r0: 135, rSlot: Math.hypot(slot[0], slot[2]) || 1, linked: false, ignited: false, extra: q.extra };
  births.push(b); lastBirth = t; busy(true);
  bus.emit('birth:start', { star, slot, kind: q.kind, t, ignitionAt: ign });
  for (const x of q.extra) stars.setState(x, ST.BIRTH, ign + .12 * (q.extra.indexOf(x) + 1), 1e9, 0);
  wake();
}
function moteAt(b, k, s, out) {
  // spiral out along the dust arm, then a Bezier onto the slot (control lifted 220 u)
  const r = b.r0 + (b.rSlot - b.r0) * s, th = b.th0 + 2.4 * Math.log(r / b.r0) * Math.sign(b.rSlot - b.r0 || 1);
  const sx = Math.cos(th) * r, sz = Math.sin(th) * r, sy = b.slot[1] * s;
  const u = 1 - s, c = [b.slot[0] * .5, b.slot[1] + 220, b.slot[2] * .5], st = [Math.cos(b.th0) * b.r0, 0, Math.sin(b.th0) * b.r0];
  const bx = u * u * st[0] + 2 * u * s * c[0] + s * s * b.slot[0], by = u * u * st[1] + 2 * u * s * c[1] + s * s * b.slot[1], bz = u * u * st[2] + 2 * u * s * c[2] + s * s * b.slot[2];
  const w = s < .45 ? 0 : (s - .45) / .55, j = (k * 0.6180339 % 1 - .5) * 18 * Math.sin(Math.PI * s);
  out[0] = sx + (bx - sx) * w + j; out[1] = sy + (by - sy) * w + j * .4; out[2] = sz + (bz - sz) * w - j;
  return out;
}
const mo = [0, 0, 0];
function tickBirths(t) {
  if (queue.length && t - lastBirth >= .9 && state.visible) startBirth(queue.shift());
  for (let n = births.length - 1; n >= 0; n--) {
    const b = births[n], age = t - b.t0, base = b.block * PER;
    // motes: 150 ms in, fly 1100 ms, gather until ignition
    if (b.block >= 0) {
      for (let k = 0; k < PER; k++) {
        const i = base + k, lag = (k / PER) * .18;
        const s = Math.max(0, Math.min(1, easings.inOutCubic(Math.max(0, (age - .15) / 1.1 - lag))));
        if (age < .15 || age > (b.ign - b.t0) + .05) { ma[i] = 0; continue; }
        moteAt(b, k, s, mo);
        const g = Math.max(0, Math.min(1, (age - 1.15) / .18));      // gather onto the point
        mp[i*3] = mo[0] + (b.slot[0] - mo[0]) * g; mp[i*3+1] = mo[1] + (b.slot[1] - mo[1]) * g; mp[i*3+2] = mo[2] + (b.slot[2] - mo[2]) * g;
        ma[i] = .7 * (1 - .5 * (k / PER)) * Math.min(1, (age - .15) / .15); ms[i] = 2.2 - s;
      }
    }
    if (age > 1.15 && !b.leaned) { b.leaned = true; dustFx(1, b.slot, 140, 10, .5, t); }
    if (t >= b.ign && !b.ignited) {
      b.ignited = true;
      dustFx(2, b.slot, 320, 12, .9, t);
      bus.emit('birth:ignite', { star: b.star, t });
      if (b.kind === 'loop-open') stars.setState(b.star, ST.LOOP, t + .6, 1e9, 0);
    }
    if (b.ignited) {
      const k = (t - b.ign) / .5; if (k < 1) pulse.ring -= .3 * (1 - k);        // the photon ring dips, recovers
      if (!b.linked && t - b.ign > .25) {
        b.linked = true;
        for (const e of edges.of(b.star)) edges.drawOn(e, t, b.star);
        // 1-2 more links to the nearest memories of the same cluster
        const nd = nodeOf(b.star), cl = nd && graph.clusters.get(nd.cluster);
        if (cl) {
          const near = cl.mains.filter(i => i !== b.star).map(i => [i, dist2(P(i), b.slot)]).sort((x, y) => x[1] - y[1]).slice(0, 2);
          near.forEach(([j], q) => edges.add(b.star, j, .08, { drawOn: t + .13 * (q + 1), from: b.star }));
        }
      }
    }
    if (age > (b.ign - b.t0) + 2.6) { if (b.block >= 0) { for (let k = 0; k < PER; k++) ma[base + k] = 0; moteFree.push(b.block); } births.splice(n, 1); busy(false); bus.emit('birth:done', { star: b.star }); }
  }
}
const dist2 = (a, b) => (a[0]-b[0]) ** 2 + (a[1]-b[1]) ** 2 + (a[2]-b[2]) ** 2;

/* ------------------------------------------------------------------ genesis (a repo studied -> a new constellation) */
const gen = { on: false, topic: '', stars: [], lit: 0, next: 0, t0: 0, gap: .09, centre: [0, 0, 0], quick: false, endAt: 0, wells: 0 };
function spiralTo(c) {
  const bs = brightSide(); const r1 = Math.hypot(c[0], c[2]) || 1, a1 = Math.atan2(c[2], c[0]);
  let o = 0;
  const pt = (s, out) => { const r = 135 + (r1 - 135) * s; let a = bs.th + (a1 - bs.th) * s + .75 * TAU * s * (1 - s); out[0] = Math.cos(a) * r; out[2] = Math.sin(a) * r; out[1] = c[1] * s + 60 * Math.sin(Math.PI * s); return out; };
  const p0 = [0, 0, 0], p1 = [0, 0, 0];
  for (let s = 0; s < FS; s++) { pt(s / FS, p0); pt((s + 1) / FS, p1); fp[o++] = p0[0]; fp[o++] = p0[1]; fp[o++] = p0[2]; fp[o++] = p1[0]; fp[o++] = p1[1]; fp[o++] = p1[2]; }
  aFP.needsUpdate = true;
}
function genesisStart(e) {
  gen.on = true; gen.topic = e.topic; gen.stars = (e.stars || []).slice(); gen.lit = 0; gen.t0 = state.t;
  gen.quick = state.game || !state.focused || REDUCED; gen.centre = e.centre || centroid(gen.stars);
  gen.next = state.t + (gen.quick ? .2 : 1.9); gen.endAt = 0;
  for (const s of gen.stars) stars.setState(s, ST.BIRTH, 1e9, 1e9, 0);      // hidden until their turn
  spiralTo(gen.centre); filament.visible = true; filMat.uniforms.uA.value = 0; busy(true);
  const w = fxu.uWell.value[gen.wells++ % 4]; w.set(gen.centre[0], gen.centre[1], gen.centre[2], 0); gen.well = w;
  wake();
}
function centroid(list) { const c = [0, 0, 0]; for (const i of list) { const p = P(i); c[0] += p[0]; c[1] += p[1]; c[2] += p[2]; } const n = Math.max(1, list.length); return [c[0]/n, c[1]/n, c[2]/n]; }
function tickGenesis(t, dt) {
  if (!gen.on) return;
  const age = t - gen.t0, total = gen.quick ? 2 : 7.4;
  filMat.uniforms.uHead.value = Math.min(1, Math.max(0, (age - .3) / (gen.quick ? .4 : 1.6)));
  filMat.uniforms.uA.value = gen.endAt ? Math.max(0, 1 - (t - gen.endAt) / 1.2) : Math.min(1, age / .3) * (age > 4.5 ? .25 : 1);
  if (gen.well && gen.well.w < 18) gen.well.w = Math.min(18, gen.well.w + dt * 9);
  // cascade: gaps 90 -> 40 ms, whole cascade capped at 2.4 s
  while (gen.lit < gen.stars.length && t >= gen.next) {
    const s = gen.stars[gen.lit++];
    stars.setState(s, ST.BIRTH, t, 1e9, 0);
    for (const e of edges.of(s)) { const [a, b] = edges.ends(e); const other = a === s ? b : a; if (stars.stateOf(other) !== ST.BIRTH || stars.tOnOf(other) <= t) edges.drawOn(e, t, other); }
    const k = gen.lit / Math.max(1, gen.stars.length);
    let gap = (gen.quick ? .025 : .09 - .05 * k);
    gap = Math.min(gap, (gen.quick ? 1.0 : 2.4) / Math.max(1, gen.stars.length));
    gen.next += gap;
  }
  if (!gen.endAt && gen.lit >= gen.stars.length && age > total - 1.2) gen.endAt = t;
  if (gen.endAt && t - gen.endAt > 1.3) { gen.on = false; filament.visible = false; busy(false); }
}

/* ------------------------------------------------------------------ endings */
const falls = [];
function fall(list, silent) {
  for (const i of list) {
    if (i == null || i < 0 || falls.some(f => f.i === i)) continue;
    for (const e of edges.of(i)) edges.unthread(e, state.t, i);
    const p = P(i), r0 = Math.max(90, Math.hypot(p[0], p[2]));
    falls.push({ i, t0: state.t + .5, r0, phi0: Math.atan2(p[2], p[0]), y0: p[1], silent });
    bus.emit('fall', { star: i });
  }
  wake();
}
function tickFalls(t) {
  for (let n = falls.length - 1; n >= 0; n--) {
    const f = falls[n], tau = t - f.t0;
    if (tau < 0) continue;
    if (stars.stateOf(f.i) !== ST.FALLING) stars.setState(f.i, ST.FALLING, f.t0, 1e9, 0);
    const r = 84 + (f.r0 - 84) * Math.exp(-1.8 * tau), phi = f.phi0 + 2 * (1 - Math.exp(-1.2 * tau)), y = f.y0 * Math.exp(-2 * tau);
    stars.setPos(f.i, Math.cos(phi) * r, y, Math.sin(phi) * r);
    if (tau > 2.4) {
      stars.setBright(f.i, 0); stars.setState(f.i, 0, 0, 0, 0);
      if (!f.silent) { flickerAt = t; bh.uniforms.uFeed.value = Math.max(0, bh.uniforms.uFeed.value - .05); }
      falls.splice(n, 1);
    }
  }
  const k = (t - flickerAt) / .3; if (k >= 0 && k < 1) pulse.ring += .4 * Math.sin(Math.PI * k);
}
let flickerAt = -1e9;

/* ------------------------------------------------------------------ loops */
function closeLoop(i) { if (i >= 0) stars.setState(i, ST.LOOP, stars.tOnOf(i) || 0, state.t, 0); }
function pulseLoops() {
  const loops = graph.nodes.filter(n => n.kind === 'loop' && stars.bright[n.idx] > 0);
  loops.forEach((n, k) => setTimeout(() => {
    const b = stars.bright[n.idx]; stars.setBright(n.idx, b * 1.8); setTimeout(() => stars.setBright(n.idx, b), 300); wake();
  }, 120 * k));
}

/* ------------------------------------------------------------------ init */
export async function init() {
  for (const n of graph.nodes) if (n.kind === 'loop') stars.setState(n.idx, ST.LOOP, 0, 1e9, 0);
  bus.on('graph:node', e => { if (e.node && e.node.kind === 'loop' && !e.node.dynamic) stars.setState(e.node.idx, ST.LOOP, state.t + 1.4, 1e9, 0); });

  bus.on('learn', e => {
    if (!e) return;
    if (e.kind === 'day') { for (const s of e.stars || []) stars.setState(s, ST.TARGET, state.t, state.t + .3, 0); return; }
    if (e.kind === 'skill-improved') { for (const s of e.stars || []) { const b = stars.bright[s]; stars.setBright(s, Math.min(1.6, b * 1.08)); stars.setState(s, ST.TARGET, state.t, state.t + .6, 0); } return; }
    if (e.kind === 'loop-closed') { for (const s of e.stars || []) closeLoop(s); return; }
    if (gen.on && e.topic && e.topic === gen.topic) return;       // the cascade handles them
    requestBirth(e);
  });
  bus.on('genesis', e => {
    if (!e) return;
    if (e.phase === 'start') genesisStart(e);
    else if (e.phase === 'row' && gen.on) { for (const s of e.stars || []) if (!gen.stars.includes(s)) { gen.stars.push(s); stars.setState(s, ST.BIRTH, 1e9, 1e9, 0); } }
  });
  bus.on('forget', e => { if (e && e.stars && e.stars.length) fall(e.stars, !!e.silent); });
  bus.on('graph:gone', e => fall((e.nodes || []).map(n => n.idx), true));
  bus.on('graph:bind', e => { if (e && e.revived && e.node) { const k = falls.findIndex(f => f.i === e.node.idx); if (k >= 0) { falls.splice(k, 1); stars.setState(e.node.idx, 0, 0, 0, 0); } } });
  bus.on('step', e => { if (e.kind === 'loops' || e.tool === 'open_loops') pulseLoops(); });
  bus.on('click', () => { if (gen.on) gen.next = Math.min(gen.next, state.t); if (gen.on) gen.quick = true; });
  bus.on('host:visible', m => { if (m && m.on) lastBirth = state.t - .2; });
  bus.on('resize', e => { moteMat.uniforms.uPR.value = (e && e.pr) || pixelRatio(); });
  bus.on('clock:rebase', e => { const s = e.shift || 0; for (const b of births) { b.t0 -= s; b.ign -= s; } for (const f of falls) f.t0 -= s; gen.t0 -= s; gen.next -= s; if (gen.endAt) gen.endAt -= s; lastBirth -= s; flickerAt -= s; });

  onFrame((dt, t) => {
    if (!births.length && !queue.length && !gen.on && !falls.length) return;
    tickBirths(t); tickGenesis(t, dt); tickFalls(t);
    aMP.needsUpdate = aMA.needsUpdate = aMS.needsUpdate = true;
  }, 12);
  window.__brain = Object.assign(window.__brain || {}, { life: { births, falls, gen, fall, requestBirth } });
}
