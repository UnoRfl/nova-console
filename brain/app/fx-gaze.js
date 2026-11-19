// Nova Brain -- fx-gaze: her attention travelling (SPEC 4.1 X-1, X-2, X-7, X-8, condensed).
// Every tool call launches a leg: a four-arm head leaves the photon ring (the
// first leg of a turn slingshots behind the hole, lensed by the ray-march via
// bh.uEmit), flies a gravity-bent path to the target star, locks on, breathes
// while the tool runs, and a bead of light carries the result home. One
// LineSegments draw for all legs, one Points draw for heads + beads.
// Owns: star states TARGET/OUTSIDE/ACTION, fxu.uLight[2..3], bh.uEmit/uEmitC/uHot, finish.uIris.

import {
  THREE, scene, camera, bus, state, stars, fxu, bh, finish, pulse, onFrame, pixelRatio,
  ST, RS, COLORS, easings, REDUCED, wake,
} from './core.js';
import { project } from './core.js';

const NL = 32, SEG = 64, NH = 8;
const WARM = COLORS.GOLD, COOL = COLORS.COOL, EMBER = COLORS.EMBER;
const tmpV = new THREE.Vector3();

/* ------------------------------------------------------------------ geometry: all legs in one draw */
const lp = new Float32Array(NL * SEG * 2 * 3), lu = new Float32Array(NL * SEG * 2), ll = new Float32Array(NL * SEG * 2);
for (let l = 0; l < NL; l++) for (let s = 0; s < SEG; s++) for (let e = 0; e < 2; e++) { const v = (l * SEG + s) * 2 + e; lu[v] = (s + e) / SEG; ll[v] = l; }
const lg = new THREE.BufferGeometry();
const aLP = new THREE.BufferAttribute(lp, 3); aLP.setUsage(THREE.DynamicDrawUsage);
lg.setAttribute('position', aLP); lg.setAttribute('aU', new THREE.BufferAttribute(lu, 1)); lg.setAttribute('aLeg', new THREE.BufferAttribute(ll, 1));
const legA = [], legB = [];          // per-leg uniforms: A = (head u, alpha, temp 0 warm/1 cool, phase 0 fly/1 run/2 done/3 err) B = (bead u, s0, s1, length)
for (let i = 0; i < NL; i++) { legA.push(new THREE.Vector4(0, 0, 0, 0)); legB.push(new THREE.Vector4(-1, 0, 0, 1)); }
const legMat = new THREE.ShaderMaterial({
  transparent: true, depthWrite: false, depthTest: true, blending: THREE.AdditiveBlending,
  uniforms: { uLegA: { value: legA }, uLegB: { value: legB }, uT: fxu.uT, uRecap: { value: -1 } },
  vertexShader: `attribute float aU, aLeg; uniform vec4 uLegA[${NL}]; uniform vec4 uLegB[${NL}];
    varying float vU; varying vec4 vA; varying vec4 vB;
    void main(){ int i=int(aLeg+.5); vA=uLegA[i]; vB=uLegB[i]; vU=aU;
      gl_Position = vA.y>0.001 ? projectionMatrix*modelViewMatrix*vec4(position,1.) : vec4(2.,2.,2.,1.); }`,
  fragmentShader: `uniform float uT, uRecap; varying float vU; varying vec4 vA; varying vec4 vB;
    void main(){
      float h=vA.x; if(vU>h+.001) discard;
      float L=max(vB.w,1.);
      float a=(smoothstep(h-.35,h,vU)*.78+.22)*.55;
      if(vA.w>.5 && vA.w<1.5){ float d=fract(vU*L/26.+uT*1.6); a+=.45*smoothstep(0.,.12,d)*smoothstep(.42,.12,d); }
      if(vB.x>=0.) a+=1.6*exp(-pow((vU-vB.x)*L/16.,2.));
      if(uRecap>=0.) a+=1.2*exp(-pow((mix(vB.y,vB.z,vU)-uRecap)/.04,2.));
      a*=vA.y;
      vec3 c=vA.w>2.5 ? vec3(${EMBER.join(',')}) : (vA.z>.5 ? vec3(${COOL.join(',')}) : vec3(1.,.86,.62));
      gl_FragColor=vec4(c*a,a); }`,
});
const legLines = new THREE.LineSegments(lg, legMat); legLines.frustumCulled = false; legLines.renderOrder = 2; scene.add(legLines);

/* heads (8) + beads (32) as one Points draw: astroid four-arm star / soft bead */
const NP = NH + NL;
const hp = new Float32Array(NP * 3), ha = new Float32Array(NP), hr = new Float32Array(NP), hs = new Float32Array(NP), hc = new Float32Array(NP * 3);
const hg = new THREE.BufferGeometry();
const A = (arr, n) => { const a = new THREE.BufferAttribute(arr, n); a.setUsage(THREE.DynamicDrawUsage); return a; };
const aHP = A(hp, 3), aHA = A(ha, 1), aHR = A(hr, 1), aHS = A(hs, 1), aHC = A(hc, 3);
hg.setAttribute('position', aHP); hg.setAttribute('aA', aHA); hg.setAttribute('aRot', aHR); hg.setAttribute('aSz', aHS); hg.setAttribute('color', aHC);
const headMat = new THREE.ShaderMaterial({
  transparent: true, depthWrite: false, depthTest: true, blending: THREE.AdditiveBlending,
  uniforms: { uPR: { value: pixelRatio() } },
  vertexShader: `attribute float aA, aRot, aSz; attribute vec3 color; uniform float uPR; varying float vA, vRot, vKind; varying vec3 vC;
    void main(){ vA=aA; vRot=aRot; vC=color; vKind=aSz<0. ? 1. : 0.;
      gl_PointSize=abs(aSz)*uPR; gl_Position=aA>.001 ? projectionMatrix*modelViewMatrix*vec4(position,1.) : vec4(2.,2.,2.,1.); }`,
  fragmentShader: `varying float vA, vRot, vKind; varying vec3 vC;
    void main(){ vec2 c=(gl_PointCoord-.5)*2.; float r=length(c);
      float a;
      if(vKind>.5){ a=exp(-r*r*6.)*1.2; }
      else {
        float cs=cos(vRot), sn=sin(vRot); vec2 q=vec2(cs*c.x-sn*c.y, sn*c.x+cs*c.y);
        float ast=pow(abs(q.x),.66)+pow(abs(q.y),.66);        // astroid: four arms
        float arms=smoothstep(.92,.55,ast);
        a=arms*.9+exp(-r*r*40.)*1.4+exp(-r*r*5.)*.18;
      }
      a*=vA; gl_FragColor=vec4(mix(vC,vec3(1.),.35)*a,a); }`,
});
const heads = new THREE.Points(hg, headMat); heads.frustumCulled = false; heads.renderOrder = 3; scene.add(heads);

/* ------------------------------------------------------------------ jets (actions) */
const jetGeo = new THREE.CylinderGeometry(14, 3, 1, 20, 24, true); jetGeo.translate(0, .5, 0);
const jetMat = new THREE.ShaderMaterial({
  transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
  uniforms: { uLen: { value: 0 }, uA: { value: 0 }, uT: fxu.uT },
  vertexShader: `uniform float uLen; varying float vV; varying vec3 vN, vView;
    void main(){ vV=position.y; vec3 p=vec3(position.x*mix(.2,1.,pow(position.y,.6)), position.y*uLen, position.z*mix(.2,1.,pow(position.y,.6)));
      vec4 mv=modelViewMatrix*vec4(p,1.); vN=normalize(normalMatrix*normal); vView=normalize(-mv.xyz); gl_Position=projectionMatrix*mv; }`,
  fragmentShader: `uniform float uA, uT, uLen; varying float vV; varying vec3 vN, vView;
    void main(){ float I=pow(1.-vV,1.4)*pow(abs(dot(vN,vView)),1.6);
      float knots=0.; for(int k=0;k<3;k++){ float x=(vV-fract(1.1*uT+float(k)/3.))*uLen/18.; knots+=.8*exp(-x*x); }
      float a=min((I+knots*(1.-vV))*uA,.35); gl_FragColor=vec4(vec3(1.,.92,.8)*a,a); }`,
});
const jets = new THREE.Group();
for (const s of [1, -1]) { const m = new THREE.Mesh(jetGeo, jetMat); m.rotation.z = s > 0 ? .21 : Math.PI + .21; m.frustumCulled = false; jets.add(m); }
jets.visible = false; scene.add(jets);
const jet = { on: false, t0: 0, off: 0, lastFire: -1e9, err: false };

/* ------------------------------------------------------------------ leg state */
const legs = [];                        // live leg records (<= NL)
const freeSlots = []; for (let i = NL - 1; i >= 0; i--) freeSlots.push(i);
const headSlots = []; for (let i = NH - 1; i >= 0; i--) headSlots.push(i);
let turnLegs = 0, turnIdx = 0, recapAt = -1, recapLegs = [];
const byStep = new Map();

function photonPoint(target, out) {
  // where the head leaves the ring: hole + 78 u toward the target, in the plane facing the camera
  const vx = camera.position.x, vy = camera.position.y, vz = camera.position.z, vl = Math.hypot(vx, vy, vz) || 1;
  const nx = vx / vl, ny = vy / vl, nz = vz / vl, d = target[0] * nx + target[1] * ny + target[2] * nz;
  let px = target[0] - d * nx, py = target[1] - d * ny, pz = target[2] - d * nz; const pl = Math.hypot(px, py, pz) || 1;
  out[0] = px / pl * 78; out[1] = py / pl * 78; out[2] = pz / pl * 78; return out;
}
function bez(P, t, out) {
  const u = 1 - t, a = u * u * u, b = 3 * u * u * t, c = 3 * u * t * t, d = t * t * t;
  out[0] = a * P[0] + b * P[3] + c * P[6] + d * P[9]; out[1] = a * P[1] + b * P[4] + c * P[7] + d * P[10]; out[2] = a * P[2] + b * P[5] + c * P[8] + d * P[11];
  return out;
}
const tp = [0, 0, 0], tq = [0, 0, 0];

function launch(e) {
  const star = e.primary;
  if (star == null || star < 0 || !freeSlots.length) return;
  const slot = freeSlots.pop();
  const T = [stars.pos[star*3], stars.pos[star*3+1], stars.pos[star*3+2]];
  const first = turnLegs === 0 && !REDUCED;
  turnLegs++;
  // P0: the ring (or, for the first leg, a point behind the hole it swings to)
  const P0 = [0, 0, 0];
  if (first) { const cl = camera.position.length() || 1; P0[0] = -camera.position.x / cl * 51; P0[1] = -camera.position.y / cl * 51 + 8; P0[2] = -camera.position.z / cl * 51; }
  else photonPoint(T, P0);
  const mid = [(P0[0] + T[0]) / 2, (P0[1] + T[1]) / 2, (P0[2] + T[2]) / 2], ml = Math.hypot(mid[0], mid[1], mid[2]) || 1;
  const L = Math.hypot(T[0] - P0[0], T[1] - P0[1], T[2] - P0[2]);
  const pull = .35 * L * Math.min(1.5, 900 / ml), side = (e.gi || 0) % 2 ? -1 : 1;
  const P = new Float32Array(12);
  P.set(P0, 0); P.set(T, 9);
  for (const [k, f] of [[3, 1 / 3], [6, 2 / 3]]) {
    P[k] = P0[0] + (T[0] - P0[0]) * f - mid[0] / ml * pull + side * mid[2] / ml * .1 * L;
    P[k + 1] = P0[1] + (T[1] - P0[1]) * f + .12 * L;
    P[k + 2] = P0[2] + (T[2] - P0[2]) * f - mid[2] / ml * pull - side * mid[0] / ml * .1 * L;
  }
  // write the curve into this leg's slice of the shared buffer
  let o = slot * SEG * 6;
  for (let s = 0; s < SEG; s++) { bez(P, s / SEG, tp); bez(P, (s + 1) / SEG, tq); lp[o++] = tp[0]; lp[o++] = tp[1]; lp[o++] = tp[2]; lp[o++] = tq[0]; lp[o++] = tq[1]; lp[o++] = tq[2]; }
  aLP.addUpdateRange(slot * SEG * 6, SEG * 6); aLP.needsUpdate = true;
  const queued = legs.filter(l => l.phase === 0).length;
  let dur = Math.min(.85, Math.max(.42, .30 + L / 2600)); if (queued >= 1) dur = Math.max(.34, dur * .7);
  const cool = e.temp === 'cool';
  const leg = { slot, star, P, L, t0: state.t + (e.gi || 0) * .09, swing: first ? .32 : 0, dur, phase: 0, landAt: 0, doneAt: -1, ok: true, yieldv: 0,
    cool, kind: e.kind, tool: e.tool, head: headSlots.length ? headSlots.pop() : -1, alpha: 1, endAt: 0, s0: 0, s1: 1, id: e.id, retime: false };
  legs.push(leg); byStep.set(e.id, leg);
  legA[slot].set(0, 1, cool ? 1 : 0, 0); legB[slot].set(-1, 0, 1, L);
  bus.emit('gaze:launch', { id: e.id, star, to: T, t0: leg.t0, dur, slingshot: first });
  if (first && project(T[0], T[1], T[2], tp)) {        // ring hot spot at the departure angle (GL screen space, y up)
    bh.uniforms.uHot.value.set(Math.atan2(-(tp[1] - state.bhPx[1]), tp[0] - state.bhPx[0]), state.t, .3, .4);
  }
  wake();
}

function land(leg) {
  leg.phase = 1; leg.landAt = state.t;
  const st = stars.stateOf(leg.star);
  if (st !== ST.BIRTH && st !== ST.FALLING && st !== ST.LOOP && st !== ST.PENDING) {
    const action = leg.kind === 'app' || leg.kind === 'music' || leg.kind === 'mail' || leg.kind === 'messages' || leg.kind === 'system' || leg.kind === 'game';
    const opened = leg.tool === 'web_open' || leg.tool === 'web_read' || leg.tool === 'WebFetch';
    stars.setState(leg.star, action ? ST.ACTION : leg.cool ? ST.OUTSIDE : ST.TARGET, state.t, 1e9, opened ? 1 : leg.cool ? 1 : 0);
  }
  bus.emit('gaze:land', { id: leg.id, star: leg.star, t: state.t });
}
function done(leg, ok, y) {
  leg.doneAt = state.t; leg.ok = ok; leg.yieldv = y || 0;
  if (leg.phase === 0) {                       // finished before arriving: hurry the rest (<= 250 ms)
    const k = flightK(leg); leg.retime = { from: k, at: state.t };
  }
}
function flightK(leg) { const t = state.t - leg.t0 - leg.swing; return Math.max(0, Math.min(1, t / leg.dur)); }

/* ------------------------------------------------------------------ approval */
function hold(id) { const l = byStep.get(id); if (l) l.held = true; }
function release(id, allow) { const l = byStep.get(id); if (!l) return; l.held = false; if (!allow) { l.doneAt = state.t; l.ok = false; } }

/* ------------------------------------------------------------------ frame */
const headPos = [0, 0, 0];
let lastActive = 0;
function frame(dt, t) {
  let anyEmit = false, lampA = -1, lampB = -1;
  for (let n = legs.length - 1; n >= 0; n--) {
    const leg = legs[n], A = legA[leg.slot], B = legB[leg.slot];
    const age = t - leg.t0;
    let u = 0;
    if (age < 0) { A.y = 0; continue; }
    // flight
    if (leg.phase === 0) {
      let k;
      if (leg.retime) { const rk = Math.min(1, (t - leg.retime.at) / .25); k = leg.retime.from + (1 - leg.retime.from) * easings.outCubic(rk); }
      else k = easings.inOutCubic(flightK(leg));
      if (leg.held) k = Math.min(k, .85);
      u = k;
      if (k >= 1) land(leg);
    } else u = 1;
    A.x = u; A.y = leg.alpha;
    // head position
    if (leg.head >= 0) {
      const hi = leg.head;
      if (leg.swing && age < leg.swing) {           // slingshot: swing behind the hole (the ray-march lenses it)
        const s = age / leg.swing, ang = Math.PI * s;
        photonPoint([leg.P[9], leg.P[10], leg.P[11]], headPos);
        const bx = leg.P[0], by = leg.P[1], bz = leg.P[2];
        const c = Math.cos(ang), sn = Math.sin(ang);
        headPos[0] = headPos[0] * .65 * c + bx * (1 - c) * .5; headPos[1] = headPos[1] * .65 * c + by * (1 - c) * .5 + 30 * sn; headPos[2] = headPos[2] * .65 * c + bz * (1 - c) * .5;
      } else bez(leg.P, u, headPos);
      const r = Math.hypot(headPos[0], headPos[1], headPos[2]);
      if (r < 510) { bh.uniforms.uEmit.value.set(headPos[0] / RS, headPos[1] / RS, headPos[2] / RS, 1.2); anyEmit = true; }
      hp[hi*3] = headPos[0]; hp[hi*3+1] = headPos[1]; hp[hi*3+2] = headPos[2];
      const vis = Math.min(1, Math.max(0, (r - 470) / 40));
      let breathe = 1;
      if (leg.phase === 1 && leg.doneAt < 0) { const run = t - leg.landAt; breathe = 1 + .1 * Math.sin(run * 2 * Math.PI * 1.2) + Math.min(.3, Math.max(0, run - 3) * .04); }
      const snap = leg.phase === 1 ? Math.max(0, 1 - (t - leg.landAt) / .18) : 1;
      ha[hi] = vis * leg.alpha * (leg.doneAt >= 0 && leg.phase === 1 ? Math.max(0, 1 - (t - Math.max(leg.doneAt, leg.landAt + .12)) / .3) : 1);
      hr[hi] = leg.phase === 1 ? .785 * snap * easings.outBack(1 - snap) : t * .8;
      hs[hi] = 30 * breathe;
      const c = leg.cool ? COOL : WARM; hc[hi*3] = c[0]; hc[hi*3+1] = c[1]; hc[hi*3+2] = c[2];
      if (lampA < 0) lampA = hi; else if (lampB < 0) lampB = hi;
      if (ha[hi] <= .001 && leg.phase === 1 && leg.doneAt >= 0) { headSlots.push(hi); leg.head = -1; }
    }
    // done: bead rides home, then the thread settles
    if (leg.doneAt >= 0 && leg.phase === 1) {
      const bt = t - Math.max(leg.doneAt, leg.landAt + .12);
      if (!leg.ok) { A.w = 3; leg.alpha = Math.max(0, 1 - bt / .4); B.x = -1; if (leg.alpha <= 0) leg.endAt = t; }
      else {
        A.w = 2;
        if (bt >= 0 && bt <= .45) { B.x = 1 - easings.inCubic(bt / .45); }
        else if (bt > .45 && B.x >= 0) {
          B.x = -1;
          if (!leg.absorbed) { leg.absorbed = true; absorbAt = t; bus.emit('absorb', { t, amount: .5 + leg.yieldv * .5 }); bus.emit('gaze:home', { id: leg.id, t, yield: leg.yieldv }); stars.release(leg.star, t); }
        }
      }
    } else if (leg.phase === 1) A.w = 1;
    // long exposure after the turn
    if (!state.turn && leg.phase === 1 && (leg.doneAt >= 0 || t - leg.landAt > 20)) {
      if (!leg.coolAt) leg.coolAt = t;
      leg.alpha = Math.min(leg.alpha, .55 * Math.exp(-(t - leg.coolAt) / 90));
      if (leg.alpha < .02) leg.endAt = t;
    }
    if (leg.endAt) { A.y = 0; B.x = -1; if (leg.head >= 0) { ha[leg.head] = 0; headSlots.push(leg.head); } freeSlots.push(leg.slot); legs.splice(n, 1); byStep.delete(leg.id); }
  }
  // beads draw in the shader; heads + lamps
  aHP.needsUpdate = aHA.needsUpdate = aHR.needsUpdate = aHS.needsUpdate = aHC.needsUpdate = true;
  if (!anyEmit) bh.uniforms.uEmit.value.w = 0;
  pulse.ring += .25 * Math.max(0, 1 - (t - absorbAt) / .22);
  const L2 = fxu.uLight.value[2], L3 = fxu.uLight.value[3];
  if (lampA >= 0) { L2.set(hp[lampA*3], hp[lampA*3+1], hp[lampA*3+2], Math.min(1, L2.w + dt / .18)); } else L2.w *= Math.exp(-dt / 1.4);
  if (lampB >= 0) { L3.set(hp[lampB*3], hp[lampB*3+1], hp[lampB*3+2], Math.min(.8, L3.w + dt / .18)); } else L3.w *= Math.exp(-dt / 1.4);
  // recap glint: once, 300 ms after she starts speaking, along the route in order
  if (recapAt > 0) { const k = (t - recapAt) / 1.5; legMat.uniforms.uRecap.value = k >= 0 && k <= 1 ? easings.inOutSine(k) : -1; if (k > 1) recapAt = -1; }
  // jets
  if (jet.on || jets.visible) {
    const jt = t - jet.t0;
    const len = 520 * easings.outExpo(Math.min(1, jt / .28)) * (1 + .04 * Math.sin(t * 37));
    let a = 1;
    if (jet.off) { const ft = t - jet.off; a = jet.err ? (Math.floor(ft / .07) % 2 ? .2 : 1) * (ft < .21 ? 1 : 0) : (ft < .3 ? 1 : Math.exp(-(ft - .3) / .45)); if (a < .02 && ft > .3) { jets.visible = false; jet.on = false; } }
    jetMat.uniforms.uLen.value = len; jetMat.uniforms.uA.value = a;
  }
  // iris (screen capture / camera)
  if (irisAt > 0) { const k = (t - irisAt) / .65; finish.uniforms.uIris.value = k < 0 || k > 1 ? 0 : .8 * Math.sin(Math.PI * k); if (k > 1) irisAt = -1; }
  if (legs.length) lastActive = t;
}
let absorbAt = -1e9, irisAt = -1;

/* ------------------------------------------------------------------ events */
const ACTION_TOOLS = /^(gmail_send|gmail_reply|spotify|play|media_key|open_app|close_window|set_volume|mute|tv|minecraft_|type_text|focus_window|call_me|say)/;
const pendingJets = new Map();
export async function init() {
  bus.on('turn:start', () => { turnLegs = 0; turnIdx++; recapLegs = []; });
  bus.on('step', e => {
    if (e.tool === 'capture_screen' || e.tool === 'look_through_camera' || e.kind === 'screen' || e.kind === 'camera') { irisAt = state.t; bus.emit('iris', { on: true }); }
    const actionTool = ACTION_TOOLS.test(e.tool || '');
    if (actionTool || e.tool === 'Bash') {
      const fire = () => { if (state.t - jet.lastFire < 2 || REDUCED) return; jet.on = true; jet.t0 = state.t; jet.off = 0; jet.err = false; jet.lastFire = state.t; jets.visible = true; bus.emit('jets', { on: true, star: e.primary }); };
      if (e.tool === 'Bash') pendingJets.set(e.id, setTimeout(fire, 600)); else fire();
    }
    launch(e);
    recapLegs.push(e.id);
  });
  bus.on('step:done', e => {
    const l = byStep.get(e.id); if (l) done(l, e.ok, e.yield);
    if (pendingJets.has(e.id)) { clearTimeout(pendingJets.get(e.id)); pendingJets.delete(e.id); }
    if (jet.on && !jet.off) { jet.off = state.t; jet.err = !e.ok; }
  });
  bus.on('step:hold', e => hold(e.id));
  bus.on('step:allow', e => release(e.id, true));
  bus.on('step:deny', e => release(e.id, false));
  bus.on('mode', e => {
    if (e.mode === 'speaking' && recapLegs.length > 1) {
      const route = recapLegs.map(id => byStep.get(id)).filter(Boolean), n = route.length;
      route.forEach((l, k) => legB[l.slot].set(legB[l.slot].x, k / n, (k + 1) / n, l.L));
      recapAt = state.t + .3;
    }
  });
  bus.on('turn:end', () => { for (const l of legs) if (l.doneAt < 0 && l.phase === 1) l.doneAt = state.t; });
  bus.on('resize', e => { headMat.uniforms.uPR.value = (e && e.pr) || pixelRatio(); });
  bus.on('clock:rebase', e => { const s = e.shift || 0; for (const l of legs) { l.t0 -= s; l.landAt -= s; if (l.doneAt >= 0) l.doneAt -= s; if (l.coolAt) l.coolAt -= s; } absorbAt -= s; if (recapAt > 0) recapAt -= s; jet.t0 -= s; if (jet.off) jet.off -= s; jet.lastFire -= s; });
  onFrame(frame, 10);
  window.__brain = Object.assign(window.__brain || {}, { gaze: { legs, launch } });
}
