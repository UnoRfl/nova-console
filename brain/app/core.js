// Nova Brain -- core v3.
// Renderer, scene, GPU event clock, star/edge/dust shaders (states, shapes,
// sonar, search fronts, lamps, rack focus, dust wells), the ray-marched black
// hole (moods, voice history, remember band, wake ignition, emitter, lensed
// sky), finish pass (glass, handoff spikes, iris, lift, edge light), camera
// primitives, overlay projection (Typed OM), picking, event bus and a frame
// governor with a quality ladder. See ../CONTRACT.md and ../SPEC.md (4.6 CORE).
// Rule: nothing in the frame path allocates. Everything is pooled.

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';

export { THREE };
export const TAU = Math.PI * 2;
export const params = new URLSearchParams(location.search);
export const TOKEN = window.NOVA_TOKEN || params.get('token') || '';
export const EMBED = params.has('embed');
export const DEMO = params.has('demo');
export const REDUCED = params.has('reduced') || (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
/** ?fixed: every rendered frame advances exactly 1/60 s (deterministic replays / screenshot diffs). */
export const FIXED_DT = params.has('fixed') ? 1 / 60 : 0;
export const RS = 30;                                   // black-hole unit (world units per Rs)
export const BH_R = 17 * RS;                            // ray-march sphere radius (510 u)
export const DISK_IN = 2.6 * RS, DISK_OUT = 9.5 * RS;   // 78 .. 285 u
export const COLORS = {
  WHITE: [1, .97, .93], GOLD: [1, .85, .64], HOT: [1, .66, .32], COOL: [.80, .88, 1.0],
  AMBER: [1, .72, .35], EMBER: [.95, .52, .40], VIOLET: [.75, .55, 1.0],
};
/** Star states written into the GPU event clock (texel B). */
export const ST = { NONE: 0, USED: 1, TARGET: 2, TENTATIVE: 3, BIRTH: 4, FALLING: 5, OUTSIDE: 6, ACTION: 7, CONSIDERED: 8, CITED: 9, LOOP: 10, PENDING: 11 };
/** Star glyphs (aShape). */
export const SHAPE = { STAR: 0, SQUARE: 1, RING: 2, TRIANGLE: 3, HEXAGON: 4 };

/* ================================================================== api */
export async function api(path, opts = {}) {
  const r = await fetch(path, { ...opts, headers: { 'X-Nova-Token': TOKEN, ...(opts.headers || {}) } });
  if (!r.ok) throw new Error(path.split('?')[0] + ' -> ' + r.status);
  return r.json();
}

/* ================================================================== shared state */
export const state = {
  mode: 'idle', flavour: '', level: 0, drive: 0, game: false,
  turn: false, embed: EMBED, insetRight: EMBED ? 396 : 0,
  visible: !EMBED, paused: false, focused: true,
  userActiveAt: -1e9, activeAt: 0,            // last pointer input / last meaningful event (performance.now ms)
  now: 0, t: 0, dt: 0, frame: 0, ta: 0, ts: 1, // t = real shader clock (s); ta = ambient clock (s), advances by dt*ts
  introDone: false, director: false, viewOff: [0, 0], viewOffOwned: false,
  bhPx: [0, 0, 0],                            // black-hole centre (css px) + projected radius of the 510 u sphere (css px)
  tier: 'ACTIVE', quality: 0, gpuMs: -1, vsync: 16.67, fps: 0,
};
/** Targets other modules set; core eases toward them (dt-correct). */
export const look = {
  dim: 0,              // 0..1 fade non-involved stars (focus)
  house: 0,            // 0..1 house lights down (stars not involved -> 51 %)
  dust: 1,             // dust gain (house lights down -> .6)
  ts: 1,               // ambient time scale target (disk, dust, comets, sky). May be negative (rewind).
  exposure: 1,
  dustSpin: 1,
  bh: { spin: 1, bright: 1, ring: 1, glow: 1, tempExp: 1.35, ringSharp: 16, ringR: 1.53, expo: 1, tintA: 0 },
  tintCol: [1, .72, .35],
  lensSky: !params.has('nolens'),    // A/B toggle (C-4)
  comets: true,
};
/** Additive one-frame impulses (fx adds inside onFrame; core applies then zeroes). Final = eased mood x (1 + pulse). */
export const pulse = { ring: 0, ringSharp: 0, bright: 0, expo: 0, glow: 0, dust: 0, house: 0 };

/* ================================================================== bus */
const handlers = new Map();
const QUIET = new Set(['hover', 'resize', 'frame']);
export const bus = {
  on(type, fn) { (handlers.get(type) || handlers.set(type, []).get(type)).push(fn); return () => bus.off(type, fn); },
  off(type, fn) { const l = handlers.get(type); if (l) { const i = l.indexOf(fn); if (i >= 0) l.splice(i, 1); } },
  emit(type, payload) {
    if (!QUIET.has(type)) { state.activeAt = performance.now(); wake(); }
    for (const key of [type, '*']) {
      const l = handlers.get(key); if (!l) continue;
      for (const fn of l.slice()) { try { fn(payload, type); } catch (e) { console.error('[bus]', type, e); } }
    }
  },
};

/* ================================================================== easing / springs */
function cubicBezier(x1, y1, x2, y2) {
  const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx, cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
  const sx = t => ((ax * t + bx) * t + cx) * t, sy = t => ((ay * t + by) * t + cy) * t, dx = t => (3 * ax * t + 2 * bx) * t + cx;
  return x => { let t = x; for (let i = 0; i < 8; i++) { const e = sx(t) - x; if (Math.abs(e) < 1e-6) break; const d = dx(t); if (Math.abs(d) < 1e-6) break; t -= e / d; } return sy(Math.min(1, Math.max(0, t))); };
}
/** 64-entry lookup table of a cubic-bezier, as a function p(0..1). Shared by the panel glide and the brain. */
export function bezierLUT(x1, y1, x2, y2, n = 64) {
  const f = cubicBezier(x1, y1, x2, y2), tab = new Float32Array(n);
  for (let i = 0; i < n; i++) tab[i] = f(i / (n - 1));
  const fn = p => { if (p <= 0) return 0; if (p >= 1) return 1; const x = p * (n - 1), i = Math.floor(x), k = x - i; return tab[i] + (tab[Math.min(n - 1, i + 1)] - tab[i]) * k; };
  fn.table = tab; return fn;
}
export const easings = {
  linear: t => t,
  inOutCubic: t => t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2,
  outCubic: t => 1 - Math.pow(1 - t, 3), inCubic: t => t * t * t,
  outQuart: t => 1 - Math.pow(1 - t, 4), inQuad: t => t * t, outQuad: t => 1 - (1 - t) * (1 - t),
  outExpo: t => t >= 1 ? 1 : 1 - Math.pow(2, -10 * t),
  inOutQuint: t => t < .5 ? 16 * t * t * t * t * t : 1 - Math.pow(-2 * t + 2, 5) / 2,
  outBack: t => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2); },
  inOutSine: t => -(Math.cos(Math.PI * t) - 1) / 2,
  EASE: bezierLUT(.2, .8, .2, 1),
};
/** Critically/under-damped spring x'' = w^2 (target - x) - 2 z w x', stepped at a fixed 120 Hz. */
export class Spring {
  constructor(x = 0, w = 6, z = 1) { this.x = x; this.v = 0; this.target = x; this.w = w; this.z = z; this.acc = 0; }
  step(dt) {
    this.acc += Math.min(dt, .1); let n = 0;
    while (this.acc >= 1 / 120 && n < 12) { const h = 1 / 120, a = this.w * this.w * (this.target - this.x) - 2 * this.z * this.w * this.v; this.v += a * h; this.x += this.v * h; this.acc -= h; n++; }
    if (n === 12) this.acc = 0;
    return this.x;
  }
  snap(x) { this.x = this.target = x; this.v = 0; }
}
/** Analytic damped-spring response 0 -> 1 (used for pops). */
export function springResponse(t, w = 20, z = .42) {
  if (t <= 0) return 0; const wd = w * Math.sqrt(1 - z * z);
  return 1 - Math.exp(-z * w * t) * (Math.cos(wd * t) + z * w / wd * Math.sin(wd * t));
}

/* ================================================================== random / hash */
let seed = 1337;
export function rnd() { seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }
/** Deterministic PRNG from a seed (mulberry32). */
export function mulberry32(a) { return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
export function gauss() { let u = 0, v = 0; while (!u) u = rnd(); while (!v) v = rnd(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(TAU * v); }
/** Stable hash of a string to [0,1). Layout depends on it: never change. */
export function hash01(s) { s = String(s); let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } h ^= h >>> 13; h = Math.imul(h, 0x5bd1e995); h ^= h >>> 15; return (h >>> 0) / 4294967296; }

/* ================================================================== renderer */
export const canvas = document.getElementById('gl');
export const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, powerPreference: 'high-performance', stencil: false });
let PR = Math.min(devicePixelRatio, 1.75);
let prCap = PR;
renderer.setPixelRatio(PR); renderer.setSize(innerWidth, innerHeight);
renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.0;
export const scene = new THREE.Scene();
export const camera = new THREE.PerspectiveCamera(42, innerWidth / innerHeight, 2, 40000);
camera.position.set(0, 2600, 6400);
export const controls = new OrbitControls(camera, canvas);
Object.assign(controls, { enableDamping: true, dampingFactor: 0.045, rotateSpeed: 0.42, zoomSpeed: 0.7, panSpeed: 0.6,
  minDistance: 120, maxDistance: 5600, autoRotate: !REDUCED, autoRotateSpeed: 0.18,
  maxPolarAngle: Math.PI * 0.86, minPolarAngle: Math.PI * 0.08 });

/* ================================================================== GLSL chunks */
export const GLSL_NOISE = `
  float hash3(vec3 p){ p=fract(p*.3183099+.1); p*=17.; return fract(p.x*p.y*p.z*(p.x+p.y+p.z)); }
  float noise3(vec3 x){ vec3 i=floor(x), f=fract(x); f=f*f*(3.-2.*f);
    return mix(mix(mix(hash3(i),hash3(i+vec3(1,0,0)),f.x),mix(hash3(i+vec3(0,1,0)),hash3(i+vec3(1,1,0)),f.x),f.y),
               mix(mix(hash3(i+vec3(0,0,1)),hash3(i+vec3(1,0,1)),f.x),mix(hash3(i+vec3(0,1,1)),hash3(i+vec3(1,1,1)),f.x),f.y),f.z); }
  float fbm3(vec3 p){ float a=.5, s=0.; for(int i=0;i<6;i++){ s+=a*noise3(p); p=p*2.03+vec3(1.7,9.2,3.1); a*=.5; } return s; }`;

// Sky lookup shared by the sky dome and the black hole's lensed rays.
const GLSL_SKY = `
  uniform sampler2D uSky; uniform float uSkyRot; uniform float uStarsOn;
  vec3 skyTex(vec3 d){
    float c=cos(uSkyRot), s=sin(uSkyRot); d=vec3(c*d.x-s*d.z, d.y, s*d.x+c*d.z);
    float th=atan(d.z,d.x); float ph=acos(clamp(d.y,-1.,1.));
    return texture2D(uSky, vec2(fract(th/6.2831853), 1.-ph/3.1415926)).rgb; }
  float h13(vec3 p){ p=fract(p*.1031); p+=dot(p,p.zyx+31.32); return fract((p.x+p.y)*p.z); }
  // Stars on a cube-face grid: every cell is a clean square on the sphere, so a
  // star is never cut into slivers. pxA = angular size of one pixel (radians).
  vec3 skyStars(vec3 d, float pxA){
    float c=cos(uSkyRot), s=sin(uSkyRot); d=vec3(c*d.x-s*d.z, d.y, s*d.x+c*d.z);
    vec3 a=abs(d); vec2 uv; float face; float m;
    if(a.x>=a.y && a.x>=a.z){ m=a.x; uv=d.yz/m; face=d.x>0.?0.:1.; }
    else if(a.y>=a.z){ m=a.y; uv=d.xz/m; face=d.y>0.?2.:3.; }
    else { m=a.z; uv=d.xy/m; face=d.z>0.?4.:5.; }
    vec2 g=uv*90., cell=floor(g);
    vec3 key=vec3(cell,face*17.);
    if(h13(key)>.016) return vec3(0.);
    vec2 sp=cell+.3+.4*vec2(h13(key+1.7),h13(key+3.1));
    // one pixel in grid units: gnomonic projection stretches by 1/m^2 toward face edges
    float pxG=max(pxA*90./(m*m), 1e-4);
    vec2 q=(g-sp)/pxG;                       // distance in pixels
    float br=h13(key+9.2)<.05 ? .85 : .22+.24*h13(key+4.4);
    return vec3(.9,.92,1.)*exp(-dot(q,q)/(2.*.8*.8))*br; }
  vec3 skyColor(vec3 d, float pxA){ return skyTex(d) + (uStarsOn>.5 ? skyStars(d,pxA) : vec3(0.)); }`;

// Event-driven effect uniforms shared by stars, dust and lines.
const GLSL_FX = `
  uniform float uT, uHouse;
  uniform vec4 uSonar;                  // x t0, y amp, z on
  uniform vec4 uFrontC, uFrontN;        // C.xyz centre, C.w t0 | N.xyz direction, N.w extent (0 = off)
  uniform vec4 uLight[4]; uniform vec3 uLightCol[4];
  uniform vec2 uFocus;                  // x focus distance (view depth), y aperture 0..1
  float sonarShell(vec3 p){
    if(uSonar.z<.5) return 0.;
    float k=(uT-uSonar.x)/1.1; if(k<0.||k>1.) return 0.;
    float R=2000.*(1.-pow(1.-k,4.)), w=60.+140.*k, x=(length(p)-R)/w;
    return exp(-x*x)*(1.-.7*k)*uSonar.y; }
  float frontVal(vec3 p){
    if(uFrontN.w<=0.) return 0.;
    float k=(uT-uFrontC.w)/.9; if(k<0.||k>1.) return 0.;
    float ext=uFrontN.w, s=mix(-ext,ext,.5-.5*cos(3.14159265*k));
    vec3 q=p-uFrontC.xyz; float x=(dot(q,uFrontN.xyz)-s)/18.;
    return exp(-x*x)*step(length(q),1.2*ext); }
  float lampSum(vec3 p, out vec3 lc){
    float L=0.; lc=vec3(0.);
    for(int i=0;i<4;i++){ float I=uLight[i].w; if(I<=0.) continue; vec3 d=p-uLight[i].xyz; float l=I*48400./(48400.+dot(d,d)); L+=l; lc+=uLightCol[i]*l; }
    if(L>0.) lc/=L; return L; }
  float cocOf(float px, float viewZ){ return clamp(uFocus.y*abs(1.-uFocus.x/max(viewZ,1.))*26.,0.,10.); }`;

/** Shared uniform objects (same object in every material: write once, all update). */
export const fxu = {
  uT: { value: 0 }, uHouse: { value: 0 },
  uSonar: { value: new THREE.Vector4(0, 0, 0, 0) },
  uFrontC: { value: new THREE.Vector4(0, 0, 0, -1e9) }, uFrontN: { value: new THREE.Vector4(1, 0, 0, 0) },
  uLight: { value: [0, 1, 2, 3].map(() => new THREE.Vector4(0, 0, 0, 0)) },
  uLightCol: { value: [0, 1, 2, 3].map(() => new THREE.Vector3(1, 1, 1)) },
  uFocus: { value: new THREE.Vector2(0, 0) },
  uWell: { value: [0, 1, 2, 3].map(() => new THREE.Vector4(0, 0, 0, 0)) },
  uDustFx: { value: [0, 1, 2, 3].map(() => new THREE.Vector4(0, 0, 0, -1e9)) },
  uDustFxP: { value: [0, 1, 2, 3].map(() => new THREE.Vector4(0, 0, 0, 1)) },
  uHov: { value: [-1, -1, -1, -1] },
  uDrive: { value: 0 },
  uAspect: { value: innerWidth / innerHeight },
};

/* ================================================================== GPU event clock (C-1) */
// Texel i (x = i & 127, y = i >> 7): R tOn, G tOff (1e9 = still on), B state, A rank/param.
const EW = 128, EH = 128;          // 16384 texels = stars.CAP
const evData = new Float32Array(EW * EH * 4);
export const eventTex = new THREE.DataTexture(evData, EW, EH, THREE.RGBAFormat, THREE.FloatType);
eventTex.minFilter = eventTex.magFilter = THREE.NearestFilter; eventTex.generateMipmaps = false; eventTex.needsUpdate = true;
let evDirty = false;
fxu.uEvent = { value: eventTex };

/* ================================================================== composer + finish */
export const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
export const bloom = new UnrealBloomPass(new THREE.Vector2(innerWidth, innerHeight), 0.7, 0.42, 0.7);
composer.addPass(bloom);
function setHalation(on) { const c = on ? [1, .9, .82] : [1, 1, 1]; bloom.bloomTintColors[3].set(...c); bloom.bloomTintColors[4].set(...c); }
setHalation(look.lensSky);
export const finish = new ShaderPass({
  uniforms: {
    tDiffuse: { value: null }, uTime: { value: 0 }, uRes: { value: new THREE.Vector2(1, 1) },
    uLift: { value: 0 }, uIris: { value: 0 },
    uGlass: { value: new THREE.Vector4(0, 0, 0, 0) }, uGlassK: { value: 0 },          // rect in css px (x0,y0,x1,y1), k 0..1
    uSpike: { value: new THREE.Vector4(0, 0, 0, 0) }, uSpikeA: { value: 0 },          // centre css px (x,y), long arm px, angle rad
    uSpikeCol: { value: new THREE.Vector3(1, .94, .86) },
    uEdge: { value: new THREE.Vector4(0, 0, 0, 0) },                                  // css px point on the border (x,y), strength, -
    uPR: { value: PR },
  },
  vertexShader: `varying vec2 vUv; void main(){ vUv=uv; gl_Position=vec4(position.xy,0.,1.); }`,
  fragmentShader: `uniform sampler2D tDiffuse; uniform float uTime, uLift, uIris, uGlassK, uSpikeA, uPR; uniform vec2 uRes;
    uniform vec4 uGlass, uSpike, uEdge; uniform vec3 uSpikeCol; varying vec2 vUv;
    float h(vec2 p){ return fract(sin(dot(p,vec2(12.9898,78.233)))*43758.5453); }
    void main(){
      vec2 px=vUv*uRes/uPR; px.y=uRes.y/uPR-px.y;          // css px, origin top-left
      vec4 c=texture2D(tDiffuse,vUv);
      if(uGlassK>0.001 && px.x>=uGlass.x && px.x<=uGlass.z && px.y>=uGlass.y && px.y<=uGlass.w){
        vec2 tx=uPR/uRes*14.; vec3 b=vec3(0.);
        b+=texture2D(tDiffuse,vUv+tx*vec2(-.326,-.406)).rgb; b+=texture2D(tDiffuse,vUv+tx*vec2(-.840,-.074)).rgb;
        b+=texture2D(tDiffuse,vUv+tx*vec2(-.696,.457)).rgb;  b+=texture2D(tDiffuse,vUv+tx*vec2(-.203,.621)).rgb;
        b+=texture2D(tDiffuse,vUv+tx*vec2(.962,-.195)).rgb;  b+=texture2D(tDiffuse,vUv+tx*vec2(.473,-.480)).rgb;
        b+=texture2D(tDiffuse,vUv+tx*vec2(.519,.767)).rgb;   b+=texture2D(tDiffuse,vUv+tx*vec2(.185,-.893)).rgb;
        b+=texture2D(tDiffuse,vUv+tx*vec2(.507,.064)).rgb;   b+=texture2D(tDiffuse,vUv+tx*vec2(.896,.412)).rgb;
        b+=texture2D(tDiffuse,vUv+tx*vec2(-.322,-.933)).rgb; b+=texture2D(tDiffuse,vUv+tx*vec2(-.792,-.598)).rgb;
        vec3 frost=b/12.*.55;
        float e=min(min(px.x-uGlass.x,uGlass.z-px.x),min(px.y-uGlass.y,uGlass.w-px.y));
        frost+=vec3(.08)*exp(-e*e*1.5);
        c.rgb=mix(c.rgb,frost,uGlassK);
      }
      if(uSpikeA>0.001){
        vec2 d=px-uSpike.xy; float a=uSpike.w; vec3 acc=vec3(0.);
        for(int i=0;i<4;i++){
          float ang=a+float(i)*1.5707963; vec2 dir=vec2(cos(ang),sin(ang));
          float par=dot(d,dir), perp=abs(d.x*dir.y-d.y*dir.x);
          float len=uSpike.z*(i==0||i==2 ? 1. : .6);
          if(par>0.) acc+=uSpikeCol*exp(-pow(perp/1.1,2.))*exp(-par/max(len*.35,1.));
        }
        c.rgb+=acc*uSpikeA;
      }
      if(uEdge.z>0.){ vec2 d=px-uEdge.xy; c.rgb+=vec3(1.,.97,.93)*exp(-dot(d,d)/(2.*90.*90.))*uEdge.z; }
      vec2 dv=vUv-.5; c.rgb*=1.-dot(dv,dv)*(.9+uIris);
      float lift=clamp(uLift,0.,.08); c.rgb+=lift*(1.-c.rgb);
      c.rgb+=(h(gl_FragCoord.xy+fract(uTime)*91.)-.5)*.012;
      gl_FragColor=c; }`,
});
composer.addPass(finish);
composer.addPass(new OutputPass());

/* ================================================================== sky (baked once) + lensed dome */
const skyRT = new THREE.WebGLRenderTarget(2048, 1024, { type: THREE.HalfFloatType });
skyRT.texture.wrapS = THREE.RepeatWrapping;
{
  const q = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), new THREE.ShaderMaterial({
    vertexShader: `varying vec2 vUv; void main(){ vUv=uv; gl_Position=vec4(position.xy,0.,1.); }`,
    fragmentShader: `varying vec2 vUv; ${GLSL_NOISE}
      void main(){ float th=vUv.x*6.2831853, ph=(1.-vUv.y)*3.1415926;
        vec3 d=vec3(sin(ph)*cos(th), cos(ph), sin(ph)*sin(th));
        vec3 w=d*1.6+vec3(fbm3(d*1.3+2.), fbm3(d*1.3+7.), fbm3(d*1.3+13.))*1.4;
        float n=fbm3(w*1.4); float band=exp(-pow(d.y*2.2,2.));
        float smoke=smoothstep(.42,.95,n)*(.45+.55*band);
        float wisps=pow(smoothstep(.5,1.,fbm3(w*3.2+5.)),2.)*smoke;
        vec3 c=vec3(.62,.63,.66)*smoke*.12 + vec3(.75,.72,.68)*wisps*.12;
        gl_FragColor=vec4(c,1.); }` }));
  const s = new THREE.Scene(); s.add(q);
  renderer.setRenderTarget(skyRT); renderer.render(s, new THREE.Camera()); renderer.setRenderTarget(null);
  q.geometry.dispose(); q.material.dispose();
}
const skyU = { uSky: { value: skyRT.texture }, uSkyRot: { value: 0 }, uStarsOn: { value: look.lensSky ? 1 : 0 } };
export const sky = new THREE.Mesh(new THREE.SphereGeometry(20000, 48, 24), new THREE.ShaderMaterial({
  side: THREE.BackSide, depthWrite: false, uniforms: skyU,
  vertexShader: `varying vec3 vWorld; void main(){ vec4 w=modelMatrix*vec4(position,1.); vWorld=w.xyz; gl_Position=projectionMatrix*viewMatrix*w; }`,
  fragmentShader: `varying vec3 vWorld; ${GLSL_SKY}
    void main(){ vec3 d=normalize(vWorld-cameraPosition); gl_FragColor=vec4(skyColor(d, length(fwidth(d))),1.); }` }));
sky.renderOrder = -10; scene.add(sky);

// v2 background star points (only shown when the lensed sky is OFF: A/B)
const bgPts = (() => {
  const n = 4500, p = new Float32Array(n * 3), s = new Float32Array(n);
  for (let i = 0; i < n; i++) { const th = rnd() * TAU, ph = Math.acos(2 * rnd() - 1), r = 11000 + rnd() * 4000;
    p.set([r * Math.sin(ph) * Math.cos(th), r * Math.cos(ph), r * Math.sin(ph) * Math.sin(th)], i * 3); s[i] = rnd() < .04 ? 2.4 : .9 + rnd() * .9; }
  const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(p, 3)); g.setAttribute('size', new THREE.BufferAttribute(s, 1));
  const m = new THREE.ShaderMaterial({ transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, uniforms: { uPR: { value: PR } },
    vertexShader: `attribute float size; uniform float uPR; varying float vS; void main(){ vS=size; gl_PointSize=size*uPR*1.3; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.); }`,
    fragmentShader: `varying float vS; void main(){ float d=length(gl_PointCoord-.5)*2.; float a=exp(-d*d*4.)*min(vS,1.6)*.5; gl_FragColor=vec4(vec3(.9,.92,1.)*a,a); }` });
  const pts = new THREE.Points(g, m); pts.renderOrder = -9; pts.frustumCulled = false; pts.visible = !look.lensSky; scene.add(pts); return pts;
})();

/* ================================================================== galaxy dust */
export const dust = (() => {
  const n = 12000, p = new Float32Array(n * 3), s = new Float32Array(n), b = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const r = 340 + Math.pow(rnd(), .75) * 1400, arm = (i % 2) * Math.PI, a = arm + Math.log(r / 300) * 2.4 + gauss() * .6;
    const y = gauss() * (22 + r * .07);
    p.set([Math.cos(a) * r, y, Math.sin(a) * r], i * 3); s[i] = 1.1 + Math.pow(rnd(), 3) * 4; b[i] = .4 + rnd() * .6;
  }
  // shuffle order so a reduced draw range (quality ladder) thins evenly
  for (let i = n - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1));
    for (let k = 0; k < 3; k++) { const t = p[i*3+k]; p[i*3+k] = p[j*3+k]; p[j*3+k] = t; }
    let t = s[i]; s[i] = s[j]; s[j] = t; t = b[i]; b[i] = b[j]; b[j] = t; }
  const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(p, 3));
  g.setAttribute('size', new THREE.BufferAttribute(s, 1)); g.setAttribute('aB', new THREE.BufferAttribute(b, 1));
  const m = new THREE.ShaderMaterial({ transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: { ...fxu, uDustTime: { value: 0 }, uIntro: { value: 0 }, uPR: { value: PR }, uH: { value: innerHeight }, uGlow: { value: 1 } },
    vertexShader: `attribute float size, aB; uniform float uDustTime, uIntro, uPR, uH, uGlow;
      uniform vec4 uWell[4]; uniform vec4 uDustFx[4]; uniform vec4 uDustFxP[4];
      ${GLSL_FX}
      varying float vA;
      vec3 dustDisplace(vec3 p){
        vec3 o=vec3(0.);
        for(int i=0;i<4;i++){ float D=uWell[i].w; if(D<=0.) continue; vec3 d=uWell[i].xyz-p; float l=length(d); o+=d/max(l,1.)*D*exp(-l*l/96800.); }
        for(int i=0;i<4;i++){ vec4 P=uDustFxP[i]; if(P.x<.5) continue; float age=uT-uDustFx[i].w; if(age<0.||age>P.w) continue;
          vec3 d=uDustFx[i].xyz-p; float l=length(d); float k=age/P.w;
          if(P.x<1.5){ o+=d/max(l,1.)*P.z*sin(3.14159265*k)*smoothstep(P.y,0.,l); }
          else { float R=P.y*(1.-pow(1.-k,3.)), x=(l-R)/40.; o-=d/max(l,1.)*P.z*exp(-x*x)*(1.-k); } }
        return o; }
      void main(){ float r=length(position.xz); float w=uDustTime*.9/(r*.012+1.); float c=cos(w), s=sin(w);
        vec3 p=vec3(position.x*c-position.z*s, position.y, position.x*s+position.z*c);
        float k=clamp(uIntro*1.4-(r/1750.)*.5,0.,1.); k=1.-pow(1.-k,3.);
        p=mix(p*.15,p,k);
        p+=dustDisplace(p);
        float sh=sonarShell(p); p+=normalize(p+1e-3)*6.*sh;
        float fr=frontVal(p);
        vec3 lc; float L=min(lampSum(p,lc),1.);
        vec4 mv=modelViewMatrix*vec4(p,1.); float px=size*uH*.5/-mv.z*1.8;
        float coc=cocOf(px,-mv.z); float soft=coc/(px+coc+1e-3);
        vA=aB*k*clamp(px,0.,1.)*uGlow*(1.+.35*sh)*(1.+.5*fr)*(1.+L)*mix(1.,.55,soft)*smoothstep(60.,240.,-mv.z);
        gl_PointSize=clamp(px,1.,8.)*uPR; gl_Position=projectionMatrix*mv; }`,
    fragmentShader: `varying float vA; void main(){ float d=length(gl_PointCoord-.5)*2.; float a=exp(-d*d*5.)*vA; gl_FragColor=vec4(vec3(.86,.86,.9)*a,a); }` });
  const pts = new THREE.Points(g, m); pts.frustumCulled = false; scene.add(pts);
  return { points: pts, material: m, count: n };
})();

/* ================================================================== stars (memories, places) */
export const stars = (() => {
  const CAP = 16384;
  const pos = new Float32Array(CAP * 3), size = new Float32Array(CAP), bright = new Float32Array(CAP),
    color = new Float32Array(CAP * 3), rand = new Float32Array(CAP), spike = new Float32Array(CAP), shape = new Float32Array(CAP);
  const g = new THREE.BufferGeometry();
  const A = (arr, n) => { const a = new THREE.BufferAttribute(arr, n); a.setUsage(THREE.DynamicDrawUsage); return a; };
  const aPos = A(pos, 3), aSize = A(size, 1), aB = A(bright, 1), aCol = A(color, 3), aR = A(rand, 1), aSp = A(spike, 1), aSh = A(shape, 1);
  g.setAttribute('position', aPos); g.setAttribute('size', aSize); g.setAttribute('aB', aB); g.setAttribute('color', aCol);
  g.setAttribute('aR', aR); g.setAttribute('aSpike', aSp); g.setAttribute('aShape', aSh);
  g.setDrawRange(0, 0);
  const material = new THREE.ShaderMaterial({ transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: { ...fxu, uIntro: { value: 0 }, uPR: { value: PR }, uH: { value: innerHeight } },
    vertexShader: `attribute float size, aB, aR, aSpike, aShape; attribute vec3 color;
      uniform float uIntro, uPR, uH, uDrive, uAspect; uniform float uHov[4]; uniform sampler2D uEvent;
      ${GLSL_FX}
      varying vec3 vC; varying vec4 vP1; varying vec4 vP2; varying vec4 vP3; varying vec2 vDir;
      void main(){
        int id=gl_VertexID;
        vec4 ev=texelFetch(uEvent, ivec2(id & 127, id >> 7), 0);
        float st=ev.b, prm=ev.a, age=uT-ev.r;
        float act=step(.5,st)*step(0.,age);
        float on=act*smoothstep(0.,.18,age);
        float flash=act*exp(-6.*max(age,0.));
        float rel=max(uT-ev.g,0.);
        float warm=on*exp(-rel/1.6);
        float after=.35*on*exp(-rel/45.);
        float k=clamp((uIntro-aR*.4)/.6,0.,1.); k=1.-pow(1.-k,4.);
        vec3 p=mix(position*.05,position,k);
        float bMul=1., sMul=1., gold=0., cool=0., spk=1., ring=0., fill=0., stretch=0., alive=1., spikeLen=6.5, gap=0., loopA=0., ovK=0.;
        vec3 ov=vec3(1.);
        if(st>.5){
          if(st<1.5){                                   // USED (prm = rank)
            float u=max(age,0.);
            float I=mix(1.,1.7,1.-exp(-u/.04))+2.6*exp(-u/.18)*(1.-exp(-u/.03));
            I*=max(.6,1.-.08*(prm-1.));
            bMul=mix(1.,I,warm)+after; sMul=mix(1.,1.35-.07*prm,warm);
            spk=prm<1.5?mix(1.,1.6,warm):1.; gold=.35*warm;
          } else if(st<2.5){                            // TARGET (prm 0 warm, 1 cool)
            bMul=mix(1.,1.35,warm)+.9*flash+after; sMul=mix(1.,1.2,warm);
            if(prm>.5) cool=.3*warm; else gold=.25*warm;
          } else if(st<3.5){                            // TENTATIVE
            float wf=on*exp(-rel/.25); bMul=mix(1.,1.3,wf); cool=.08*wf;
          } else if(st<4.5){                            // BIRTH (tOn = ignition)
            float t=age; alive=step(0.,t);
            float z=.42, w=20., wd=w*sqrt(1.-z*z);
            float spr=1.-exp(-z*w*max(t,0.))*(cos(wd*t)+z*w/wd*sin(wd*t));
            sMul=max(spr,0.);
            bMul=1.25+1.75*exp(-max(t,0.)/.32);
            float ts=clamp((t-.06)/.6,0.,1.); ts=ts>=1.?1.:1.-pow(2.,-10.*ts);
            spikeLen=mix(70.,6.5,ts);
            float tau=max(t,0.)/2.6;
            ov=mix(mix(vec3(1.,.55,.2),vec3(1.,.82,.55),smoothstep(0.,.4,tau)),vec3(1.,.97,.93),smoothstep(.4,1.,tau));
            ovK=1.-smoothstep(.9,1.,tau);
          } else if(st<5.5){                            // FALLING (moved by fx on the CPU)
            float kf=clamp(age/2.4,0.,1.);
            alive=1.-smoothstep(.55,1.,kf); stretch=kf;
            float zz=1.-kf; ov=color*vec3(1.,.55+.45*zz,.3+.7*zz); ovK=1.;
          } else if(st<6.5){                            // OUTSIDE (prm 1 = page opened -> ring fills)
            bMul=mix(1.,1.4,warm)+after; cool=.25*warm;
            fill=step(.5,prm)*smoothstep(0.,.35,age);
          } else if(st<7.5){                            // ACTION
            bMul=mix(1.,1.6,warm)+1.2*flash+after;
          } else if(st<8.5){                            // CONSIDERED (hollow ring, 600 ms)
            ring=.25*smoothstep(0.,.08,age)*(1.-smoothstep(.35,.6,age));
          } else if(st<9.5){                            // CITED (pulses with her voice)
            bMul=mix(1.,1.3+1.2*uDrive,warm)+after; sMul=mix(1.,1.+.25*uDrive,warm); gold=.35*warm;
          } else if(st<10.5){                           // OPEN LOOP (tOff = closed at)
            loopA=1.; float done=step(ev.g,uT);
            gap=1.-done*clamp(rel/.6,0.,1.);
            float post=max(rel-.6,0.);
            bMul=mix(1., (1.+exp(-post/.1))*(1.-smoothstep(.2,1.4,post)), done);
          } else {                                      // PENDING approval (amber breath)
            float br=.85+.3*pow(sin(3.14159265*.5*uT),2.);
            bMul=br; ov=vec3(1.,.72,.35); ovK=.5*on;
          }
        }
        vec3 lc; float L=min(lampSum(p,lc),1.);
        float sh=sonarShell(p), fr=frontVal(p);
        float hv=0.; for(int i=0;i<4;i++) hv=max(hv,1.-step(.5,abs(float(id)-uHov[i])));
        float involved=clamp(warm+after*2.+fr+loopA*.0,0.,1.);
        float house=mix(1.,.51,uHouse*(1.-involved));
        float b=aB*bMul*house*(1.+.8*L)*(1.+1.4*fr+.5*sh)*(1.+hv)*(.88+.12*sin(uT*1.7+aR*40.));
        float sz=size*sMul*(1.+.2*sh)*(1.+hv*.6);
        vec4 mv=modelViewMatrix*vec4(p,1.);
        float px=sz*uH*.5/-mv.z;
        float minPx=aShape>.5?7.:1.5;
        float pxc=clamp(px,minPx,170.);
        float coc=cocOf(pxc,-mv.z);
        float soft=coc/(pxc+coc+1e-3);
        float focusA=max(pxc*pxc/((pxc+coc)*(pxc+coc)),.08);
        vec3 base=mix(color,ov,ovK);
        base=mix(base,vec3(1.,.85,.64),gold);
        base=mix(base,vec3(.80,.88,1.),cool);
        base=mix(base,lc,.25*L);
        vC=base;
        float vA=b*k*clamp(px/2.,.35,1.)*step(.001,aB)*alive*focusA;
        vP1=vec4(vA, max(aSpike*spk, 0.), aShape, pxc+coc);
        vP2=vec4(soft, ring, fill, stretch);
        vP3=vec4(spikeLen, gap, loopA, 0.);
        vec4 cp=projectionMatrix*mv; vec4 hc=projectionMatrix*viewMatrix*vec4(0.,0.,0.,1.);
        vec2 dd=(hc.xy/hc.w-cp.xy/cp.w)*vec2(uAspect,1.);
        vDir=dd/max(length(dd),1e-5);
        gl_PointSize=(pxc+coc)*(1.+2.*stretch)*uPR; gl_Position=cp; }`,
    fragmentShader: `varying vec3 vC; varying vec4 vP1; varying vec4 vP2; varying vec4 vP3; varying vec2 vDir;
      float sdBox(vec2 p,float b){ vec2 d=abs(p)-b; return length(max(d,0.))+min(max(d.x,d.y),0.); }
      float sdTri(vec2 p,float r){ const float k=1.7320508; p.x=abs(p.x)-r; p.y=p.y+r/k; if(p.x+k*p.y>0.) p=vec2(p.x-k*p.y,-k*p.x-p.y)/2.; p.x-=clamp(p.x,-2.*r,0.); return -length(p)*sign(p.y); }
      float sdHex(vec2 p,float r){ const vec3 k=vec3(-.866025404,.5,.577350269); p=abs(p); p-=2.*min(dot(k.xy,p),0.)*k.xy; p-=vec2(clamp(p.x,-k.z*r,k.z*r),r); return length(p)*sign(p.y); }
      void main(){
        float vA=vP1.x, vSp=vP1.y, shape=vP1.z, px=max(vP1.w,1.);
        float soft=vP2.x, ringA=vP2.y, fill=vP2.z, stretch=vP2.w;
        vec2 c=gl_PointCoord-.5; c.y=-c.y;
        if(stretch>.001){ float al=dot(c,vDir); vec2 pp=c-al*vDir; c=pp*(1.+2.*stretch)+vDir*al; }
        float d=length(c)*2.;
        float core=exp(-d*d*22.), halo=exp(-d*d*4.)*.22, a;
        if(shape<.5){
          float L=vP3.x;
          float sp=(exp(-abs(c.x)*70.)*exp(-abs(c.y)*L)+exp(-abs(c.y)*70.)*exp(-abs(c.x)*L))*vSp*.75;
          a=core*1.3+halo+sp*(1.-soft);
          if(vP3.z>.5){
            float ang=abs(mod(atan(c.y,c.x)-atan(vDir.y,vDir.x)+3.14159265,6.2831853)-3.14159265);
            float rr=abs(d-.62);
            a+=exp(-pow(rr*px*.5,2.))*step(.5236*vP3.y,ang)*.9;
          }
        } else if(shape<1.5){
          a=exp(-pow(sdBox(c,.30)*px,2.)*1.2)*.9+exp(-d*d*60.)*1.2;
        } else if(shape<2.5){
          float e=length(vec2(c.x,c.y*1.7))-.30;
          a=exp(-pow(e*px*.9,2.))*.95+fill*.45*step(e,0.)+exp(-d*d*80.)*.6;
        } else if(shape<3.5){
          a=exp(-pow(sdTri(c,.26)*px,2.)*1.2)*.9+exp(-d*d*60.)*1.1;
        } else {
          a=exp(-pow(sdHex(c,.27)*px,2.)*1.2)*.9+core*1.2+halo*.6;
        }
        if(ringA>0.) a+=ringA*exp(-pow((d-.55)*28.,2.));
        a=mix(a,exp(-d*d*3.)*.9,soft);
        a*=vA;
        vec3 col=mix(vC,vec3(1.),core*.6*(1.-step(.5,shape)));
        gl_FragColor=vec4(col*a,a); }` });
  const points = new THREE.Points(g, material); points.frustumCulled = false; scene.add(points);
  let count = 0;
  const active = new Set();
  // Dirty spans are merged and uploaded once per frame in _flush() (one range per attribute).
  const span = new Map();
  const range = (attr, i, n) => { const d = span.get(attr); if (d) { if (i < d[0]) d[0] = i; if (i > d[1]) d[1] = i; } else span.set(attr, [i, i, n]); };
  const api = {
    CAP, pos, size, bright, color, rand, spike, shape, points, material, geometry: g, active,
    get count() { return count; },
    /** Append a star: {pos:[x,y,z], size, bright, color:[r,g,b], spike:bool, shape:SHAPE, rand, born:true|seconds|false}. Returns index. */
    add(o) {
      if (count >= CAP) return -1;
      const i = count++;
      pos[i*3] = o.pos[0]; pos[i*3+1] = o.pos[1]; pos[i*3+2] = o.pos[2];
      size[i] = o.size ?? 10; bright[i] = o.bright ?? .8; rand[i] = o.rand ?? rnd(); spike[i] = o.spike ? 1 : 0; shape[i] = o.shape ?? 0;
      const c = o.color || COLORS.WHITE; color[i*3] = c[0]; color[i*3+1] = c[1]; color[i*3+2] = c[2];
      range(aPos, i, 3); range(aSize, i, 1); range(aB, i, 1); range(aCol, i, 3); range(aR, i, 1); range(aSp, i, 1); range(aSh, i, 1);
      g.setDrawRange(0, count);
      if (o.born !== undefined && o.born !== false && o.born !== -1 && o.born != null) api.setState(i, ST.BIRTH, typeof o.born === 'number' ? o.born : state.t, 1e9, 0);
      return i;
    },
    setPos(i, x, y, z) { pos[i*3] = x; pos[i*3+1] = y; pos[i*3+2] = z; range(aPos, i, 3); edges._starMoved(i); },
    setBright(i, b) { bright[i] = b; range(aB, i, 1); },
    setSize(i, s) { size[i] = s; range(aSize, i, 1); },
    setColor(i, r, gg, b) { color[i*3] = r; color[i*3+1] = gg; color[i*3+2] = b; range(aCol, i, 3); },
    setShape(i, s) { shape[i] = s; range(aSh, i, 1); },
    setSpike(i, on) { spike[i] = on ? 1 : 0; range(aSp, i, 1); },
    /** Write the event clock for star i: state (ST.*), tOn (s, state.t clock), tOff (1e9 = on), param (rank / flag). */
    setState(i, st, tOn = state.t, tOff = 1e9, prm = 0) {
      if (i < 0 || i >= count) return;
      const o = i * 4; evData[o] = tOn; evData[o+1] = tOff; evData[o+2] = st; evData[o+3] = prm; evDirty = true;
      if (st > 0) active.add(i); else active.delete(i);
    },
    stateOf(i) { return evData[i*4+2]; },
    tOnOf(i) { return evData[i*4]; },
    tOffOf(i) { return evData[i*4+1]; },
    paramOf(i) { return evData[i*4+3]; },
    /** Begin cooling (sets tOff if it is later than t). */
    release(i, t = state.t) { const o = i * 4; if (evData[o+2] > 0 && evData[o+1] > t) { evData[o+1] = t; evDirty = true; } },
    /** Compat: light a star as a TARGET; heat decays after holdMs. */
    ignite(i, amount = 1, holdMs = 0) { api.setState(i, ST.TARGET, state.t, state.t + Math.max(.35, holdMs / 1000), 0); },
    cool(i) { api.release(i); },
    coolAll(t = state.t) { for (const i of active) api.release(i, t); },
    isHot(i) { const o = i * 4; return evData[o+2] > .5 && state.t - evData[o+1] < 4 && state.t >= evData[o]; },
    hotList() { const out = []; for (const i of active) if (api.isHot(i)) out.push(i); return out; },
    hotCount() { let n = 0; for (const i of active) if (api.isHot(i)) n++; return n; },
    hotAt(k) { let n = 0; for (const i of active) if (api.isHot(i)) { if (n === k) return i; n++; } return -1; },
    /** Upload this frame's dirty spans (called by the loop before drawing). */
    _flush() { for (const [attr, d] of span) { if (started) attr.addUpdateRange(d[0] * d[2], (d[1] - d[0] + 1) * d[2]); attr.needsUpdate = true; } span.clear(); },
    _sweep() {   // forget long-finished states so the active set stays small
      for (const i of active) {
        const o = i * 4, st = evData[o+2];
        if (st === ST.LOOP || st === ST.PENDING) continue;
        if (st === ST.BIRTH) { if (state.t - evData[o] > 6) api.setState(i, 0, 0, 0, 0); continue; }
        if (st === ST.FALLING) continue;
        if (state.t - evData[o+1] > 240) api.setState(i, 0, 0, 0, 0);
      }
    },
  };
  return api;
})();

/* ================================================================== edges (constellation lines) */
export const edges = (() => {
  const CAP = 24000;
  const pos = new Float32Array(CAP * 6), ends = new Float32Array(CAP * 4), tAt = new Float32Array(CAP * 2),
    alpha = new Float32Array(CAP * 2), rand = new Float32Array(CAP * 2), born = new Float32Array(CAP * 2).fill(-1),
    from = new Float32Array(CAP * 2), die = new Float32Array(CAP * 2).fill(1e9), dieEnd = new Float32Array(CAP * 2);
  const ea = new Int32Array(CAP), eb = new Int32Array(CAP);
  const ofStar = new Map();
  const g = new THREE.BufferGeometry();
  const A = (arr, n) => { const a = new THREE.BufferAttribute(arr, n); a.setUsage(THREE.DynamicDrawUsage); return a; };
  const aPos = A(pos, 3), aEnds = A(ends, 2), aT = A(tAt, 1), aA = A(alpha, 1), aR = A(rand, 1), aBorn = A(born, 1), aFrom = A(from, 1), aDie = A(die, 1), aDieEnd = A(dieEnd, 1);
  g.setAttribute('position', aPos); g.setAttribute('aEnds', aEnds); g.setAttribute('aT', aT); g.setAttribute('aA', aA); g.setAttribute('aR', aR);
  g.setAttribute('aBorn', aBorn); g.setAttribute('aFrom', aFrom); g.setAttribute('aDie', aDie); g.setAttribute('aDieEnd', aDieEnd);
  g.setDrawRange(0, 0);
  const material = new THREE.ShaderMaterial({ transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: { ...fxu, uIntro: { value: 0 }, uDim: { value: 0 } },
    vertexShader: `attribute vec2 aEnds; attribute float aT, aA, aR, aBorn, aFrom, aDie, aDieEnd;
      uniform float uIntro, uDim; uniform sampler2D uEvent;
      ${GLSL_FX}
      varying float vA, vH, vT, vBorn, vFrom, vDie, vDieEnd;
      float warmOf(float fid){
        int id=int(fid+.5); vec4 ev=texelFetch(uEvent, ivec2(id & 127, id >> 7), 0);
        float st=ev.b, age=uT-ev.r; if(st<.5||age<0.) return 0.;
        bool glowy=(st<2.5)||(st>5.5&&st<7.5)||(st>8.5&&st<9.5);
        if(!glowy) return 0.;
        float on=smoothstep(0.,.18,age), rel=max(uT-ev.g,0.);
        return on*exp(-rel/1.6)+.17*on*exp(-rel/45.); }
      void main(){
        float wa=warmOf(aEnds.x), wb=warmOf(aEnds.y);
        float gw=.6*min(wa,wb)+.4*max(wa,wb);
        float k=clamp((uIntro-.45-aR*.3)/.25,0.,1.);
        vec3 lc; float L=min(lampSum(position,lc),1.);
        vH=min(gw,1.);
        vA=(aA*(1.-uDim*.7)*(1.-.49*uHouse)+vH*.5)*(1.+L)*k;
        vT=aT; vBorn=aBorn; vFrom=aFrom; vDie=aDie; vDieEnd=aDieEnd;
        gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.); }`,
    fragmentShader: `uniform float uT; varying float vA, vH, vT, vBorn, vFrom, vDie, vDieEnd;
      void main(){
        if(vBorn>-.5){ float pr=clamp((uT-vBorn)/.42,0.,1.); float t=vFrom>.5?1.-vT:vT; if(t>pr) discard; }
        if(vDie<1e8){ float q=clamp((uT-vDie)/.5,0.,1.); float td=vDieEnd>.5?1.-vT:vT; if(td<q) discard; }
        gl_FragColor=vec4(mix(vec3(.85,.87,.92),vec3(1.,.88,.66),vH)*vA,vA); }` });
  const lines = new THREE.LineSegments(g, material); lines.frustumCulled = false; scene.add(lines);
  let count = 0;
  const span = new Map();
  const r1 = (attr, e, per) => { const d = span.get(attr); if (d) { if (e < d[0]) d[0] = e; if (e > d[1]) d[1] = e; } else span.set(attr, [e, e, per]); };
  function writePos(e) {
    const a = ea[e] * 3, b = eb[e] * 3, P = stars.pos;
    pos[e*6] = P[a]; pos[e*6+1] = P[a+1]; pos[e*6+2] = P[a+2]; pos[e*6+3] = P[b]; pos[e*6+4] = P[b+1]; pos[e*6+5] = P[b+2];
    r1(aPos, e, 6);
  }
  return {
    CAP, lines, material, get count() { return count; },
    /** Connect two stars. opts {drawOn: seconds (state.t) to draw it in over 420 ms, from: star index it grows out of}. */
    add(a, b, al = .1, opts = null) {
      if (count >= CAP || a < 0 || b < 0) return -1;
      const e = count++; ea[e] = a; eb[e] = b; writePos(e);
      ends[e*4] = ends[e*4+2] = a; ends[e*4+1] = ends[e*4+3] = b; r1(aEnds, e, 4);
      tAt[e*2] = 0; tAt[e*2+1] = 1; r1(aT, e, 2);
      alpha[e*2] = alpha[e*2+1] = al; r1(aA, e, 2);
      rand[e*2] = rand[e*2+1] = rnd(); r1(aR, e, 2);
      const dOn = opts && opts.drawOn != null ? opts.drawOn : -1;
      born[e*2] = born[e*2+1] = dOn; r1(aBorn, e, 2);
      from[e*2] = from[e*2+1] = opts && opts.from === b ? 1 : 0; r1(aFrom, e, 2);
      die[e*2] = die[e*2+1] = 1e9; r1(aDie, e, 2);
      for (const s of [a, b]) (ofStar.get(s) || ofStar.set(s, []).get(s)).push(e);
      g.setDrawRange(0, count * 2); return e;
    },
    /** Re-draw an existing line in from star `fromStar` starting at t (s). */
    drawOn(e, t = state.t, fromStar = -1) { born[e*2] = born[e*2+1] = t; from[e*2] = from[e*2+1] = fromStar === eb[e] ? 1 : 0; r1(aBorn, e, 2); r1(aFrom, e, 2); },
    /** Unthread a line from the dying star's end over 500 ms, starting at t. */
    unthread(e, t = state.t, dyingStar = -1) { die[e*2] = die[e*2+1] = t; dieEnd[e*2] = dieEnd[e*2+1] = dyingStar === eb[e] ? 1 : 0; r1(aDie, e, 2); r1(aDieEnd, e, 2); },
    setAlpha(e, al) { alpha[e*2] = alpha[e*2+1] = al; r1(aA, e, 2); },
    of(i) { return ofStar.get(i) || []; },
    ends(e) { return [ea[e], eb[e]]; },
    endA(e) { return ea[e]; }, endB(e) { return eb[e]; },
    _starMoved(i) { const l = ofStar.get(i); if (l) for (const e of l) writePos(e); },
    _flush() { for (const [attr, d] of span) { if (started) attr.addUpdateRange(d[0] * d[2], (d[1] - d[0] + 1) * d[2]); attr.needsUpdate = true; } span.clear(); },
  };
})();

/* ================================================================== black hole (Nova) */
export const voice = (() => {
  // 32 x 1 history of her voice drive, written at 10 Hz; the disk reads it with a radial lag.
  const data = new Uint8Array(32);
  const tex = new THREE.DataTexture(data, 32, 1, THREE.RedFormat, THREE.UnsignedByteType);
  tex.minFilter = tex.magFilter = THREE.LinearFilter; tex.wrapS = THREE.RepeatWrapping; tex.needsUpdate = true;
  let head = 0, lastWrite = 0;
  return {
    tex, data,
    /** Write one sample (0..1) -- call at ~10 Hz. */
    write(v) { head = (head + 1) % 32; data[head] = Math.max(0, Math.min(255, Math.round(v * 255))); tex.needsUpdate = true; lastWrite = performance.now(); },
    /** Continuous head position for the shader (texel units / 32). */
    headPos(now) { return (head + Math.min(1, (now - lastWrite) / 100) + .5) / 32; },
  };
})();
export const bh = (() => {
  const uniforms = {
    uCam: { value: new THREE.Vector3() }, uT: fxu.uT, uDiskPhase: { value: 0 }, uPV: { value: new THREE.Matrix4() }, uRs: { value: RS },
    uIntro: { value: 0 }, uBright: { value: 1 }, uGlow: { value: 1 },
    uTempExp: { value: 1.35 }, uRingGain: { value: 1 }, uRingSharp: { value: 16 }, uRingR: { value: 1.53 },
    uRingTint: { value: new THREE.Vector3(1, .93, .84) }, uTintCol: { value: new THREE.Vector3(1, .72, .35) }, uTintA: { value: 0 },
    uExpo: { value: 1 }, uFeed: { value: 0 }, uWince: { value: 0 },
    uVoiceHist: { value: voice.tex }, uHistHead: { value: 0 },
    uBand: { value: new THREE.Vector2(-1e9, 0) }, uHot: { value: new THREE.Vector4(0, -1e9, 0, .4) },
    uBHpx: { value: new THREE.Vector3(0, 0, 0) }, uIgnite: { value: -1 },
    uEmit: { value: new THREE.Vector4(0, 0, 0, 0) }, uEmitC: { value: new THREE.Vector3(1, .94, .86) },
    uLens: { value: look.lensSky ? 1 : 0 }, ...skyU,
  };
  const material = new THREE.ShaderMaterial({ transparent: true, depthWrite: true, depthTest: true, side: THREE.BackSide, uniforms,
    vertexShader: `varying vec3 vW; void main(){ vec4 w=modelMatrix*vec4(position,1.); vW=w.xyz; gl_Position=projectionMatrix*viewMatrix*w; }`,
    fragmentShader: `uniform vec3 uCam; uniform float uT, uDiskPhase, uRs, uIntro, uBright, uGlow;
      uniform float uTempExp, uRingGain, uRingSharp, uRingR, uTintA, uExpo, uFeed, uWince, uHistHead, uIgnite, uLens;
      uniform vec3 uRingTint, uTintCol, uEmitC, uBHpx; uniform vec2 uBand; uniform vec4 uHot, uEmit;
      uniform sampler2D uVoiceHist; uniform mat4 uPV; varying vec3 vW;
      ${GLSL_NOISE}
      ${GLSL_SKY}
      float n2(vec2 p){ return noise3(vec3(p, 3.7)); }
      float fbm2(vec2 p){ float a=.5,s=0.; for(int i=0;i<4;i++){ s+=a*n2(p); p=p*2.1+vec2(3.1,1.7); a*=.5; } return s; }
      const float RIN=2.6, ROUT=9.5, R=17.0;
      float voiceAt(float r){ float lag=max(r-RIN,0.)/3.; return texture2D(uVoiceHist, vec2(fract(uHistHead-lag*10./32.), .5)).r; }
      vec3 disk(vec3 q, float r, vec3 v, out float a){
        float lv=voiceAt(r);
        float rn=r+.15*lv;
        float ang=atan(q.z,q.x);
        float sw=ang+uDiskPhase*1.4/pow(rn,1.5);
        vec2 lp=vec2(cos(sw),sin(sw));
        float n=fbm2(lp*1.7+vec2(rn*.55,0.));
        float streak=fbm2(vec2(rn*3.2,0.)+lp*.9);
        float edge=smoothstep(RIN,RIN+.5,r)*(1.-smoothstep(ROUT*.45,ROUT,r));
        if(uIgnite>=0.){ float k=clamp((uT-uIgnite)/.8,0.,1.); k=1.-pow(1.-k,3.); float thr=mix(RIN,ROUT+1.,k); edge*=smoothstep(thr,thr-.6,r); }
        float temp=pow(RIN/r,uTempExp);
        vec3 hot=vec3(1.,.93,.82), warm=vec3(1.,.66,.32), cool=vec3(.55,.28,.11);
        vec3 col=mix(cool,warm,smoothstep(.15,.55,temp)); col=mix(col,hot,smoothstep(.55,.95,temp));
        vec3 tang=normalize(vec3(-q.z,0.,q.x));
        float beta=clamp(sqrt(.5/r),0.,.6);
        float g=pow(1./(1.-beta*dot(tang,-normalize(v))),2.4);
        float dens=(.35+.9*n*n)*(.55+.6*streak);
        a=clamp(edge*(dens*1.5+.25*temp),0.,1.);
        vec3 e=col*temp*g*dens*3.6*uBright;
        e*=1.+.45*lv*exp(-(r-RIN)*.22);
        if(uBand.y>.5){ float bk=(uT-uBand.x)/1.2; if(bk>0.&&bk<1.){ float da=abs(mod(ang-bk*6.2831853+3.14159265,6.2831853)-3.14159265);
          e+=vec3(1.,.85,.64)*exp(-pow(da/.28,2.))*sin(3.14159265*bk)*temp*2.2; } }
        return e; }
      void main(){
        vec3 ro=uCam/uRs, rd=normalize(vW/uRs-ro);
        float pxImg=length(fwidth(rd));
        float b=dot(ro,rd), c=dot(ro,ro)-R*R, h=b*b-c; if(h<0.) discard;
        float t0=max(-b-sqrt(h),0.);
        vec3 p=ro+rd*t0, v=rd; vec3 Lv=cross(p,v); float h2=dot(Lv,Lv); float b0=sqrt(h2);
        vec3 col=vec3(0.); float al=0.; vec3 hitP=vec3(0.); bool hasHit=false; float minR=1e3;
        bool emit=uEmit.w>0.;
        for(int i=0;i<180;i++){
          float r2=dot(p,p), r=sqrt(r2); minR=min(minR,r);
          if(r<1.){ if(!hasHit){hitP=p;hasHit=true;} al=1.; break; }
          float dt=clamp(.07*r,.025,.9);
          vec3 pn=p+v*dt; v+=-1.5*h2*p/(r2*r2*r)*dt;
          if(p.y*pn.y<0.){ float f=p.y/(p.y-pn.y); vec3 q=mix(p,pn,f); float rq=length(q.xz);
            if(rq>RIN*.9 && rq<ROUT){ float a; vec3 dc=disk(q,rq,v,a); col+=dc*a*(1.-al); if(!hasHit && a>.35){hitP=q;hasHit=true;} al+=a*(1.-al); } }
          if(emit){ vec3 de=p-uEmit.xyz; col+=uEmitC*exp(-dot(de,de)*6.)*uEmit.w*dt*(1.-al); }
          p=pn;
          if(r>R+.5 && dot(p,v)>0.) break;
          if(al>.985) break;
        }
        float ring=exp(-pow((minR-uRingR)*uRingSharp,2.))*(1.-al);
        float hot=0.;
        if(uHot.z>0.){ float hk=(uT-uHot.y)/max(uHot.w,.01);
          if(hk>0.&&hk<1.){ vec2 sd=gl_FragCoord.xy-uBHpx.xy; float a2=atan(sd.y,sd.x);
            float da=abs(mod(a2-uHot.x+3.14159265,6.2831853)-3.14159265); hot=uHot.z*exp(-pow(da/.35,2.))*(1.-hk); } }
        col+=mix(uRingTint,uTintCol,uTintA)*ring*1.1*uRingGain*(1.+hot); al=max(al,ring*.8);
        float glow=exp(-max(minR-1.6,0.)*1.4)*.05*(1.-al)*uGlow;
        col+=vec3(1.,.82,.6)*glow; al+=glow*.6;
        col*=uExpo*(1.+uFeed);
        if(uWince>0.){ float lum=dot(col,vec3(.3,.59,.11)); col=mix(col,vec3(lum),.6*uWince)*(1.-.3*uWince); }
        col*=uIntro; al*=uIntro;
        // remaining weak-field bend after the march, for the lensed sky
        vec3 vh=normalize(v); float s=dot(p,vh); vec3 pv=p-s*vh; float bb=max(length(pv),1e-3), pl=length(p);
        float alpha=(2.-s*(2.*s*s+3.*bb*bb)/(pl*pl*pl))/(2.*bb);
        vec3 dir=normalize(vh*cos(alpha)-(pv/bb)*sin(alpha));
        float pxA=length(fwidth(dir));
        // 1/magnification: area of a pixel on the sky after vs before the bend
        float invMu=clamp(length(cross(dFdx(dir),dFdy(dir)))/max(length(cross(dFdx(rd),dFdy(rd))),1e-12),0.,1.);
        vec4 clip=uPV*vec4((hasHit?hitP:p)*uRs,1.);
        float depth=(hasHit && al>.5) ? clamp((clip.z/clip.w)*.5+.5,0.,1.) : 1.;
        if(uLens>.5){
          float w=1.-smoothstep(10.,16.5,b0);
          // stars: lensed (arcs) only where lensing is strong; plain points further out
          vec3 st=vec3(0.);
          if(uStarsOn>.5 && al<.999){
            float strong=1.-smoothstep(4.5,8.,b0);
            if(strong>0.) st+=skyStars(dir,pxA)*pow(invMu,1.1)*strong;
            if(strong<1.) st+=skyStars(rd,pxImg)*(1.-strong);
          }
          vec3 sk=al<.999 ? (skyTex(dir)+st)*w*(1.-al) : vec3(0.);
          float A=al+w*(1.-al);
          if(A<.004) discard;
          gl_FragDepth=depth;
          gl_FragColor=vec4(col*al+sk, A);
        } else {
          if(al<.01) discard;
          gl_FragDepth=depth;
          gl_FragColor=vec4(col,al);
        }
      }` });
  const mesh = new THREE.Mesh(new THREE.SphereGeometry(BH_R, 48, 32), material);
  mesh.renderOrder = -5; scene.add(mesh);
  function setLens(on) {
    uniforms.uLens.value = on ? 1 : 0;
    material.blending = on ? THREE.CustomBlending : THREE.NormalBlending;
    material.blendSrc = THREE.OneFactor; material.blendDst = THREE.OneMinusSrcAlphaFactor;
    material.blendSrcAlpha = THREE.OneFactor; material.blendDstAlpha = THREE.OneMinusSrcAlphaFactor;
    material.needsUpdate = true;
  }
  setLens(look.lensSky);
  return { mesh, material, uniforms, setLens };
})();
/** A/B toggle of the lensed sky (C-4). */
export function setLensSky(on) {
  look.lensSky = !!on; bh.setLens(look.lensSky); skyU.uStarsOn.value = look.lensSky ? 1 : 0; bgPts.visible = !look.lensSky; setHalation(look.lensSky);
}

/* ================================================================== comet arcs (ambience) */
const arcs = [];
{
  const NA = 9, headGeo = new THREE.BufferGeometry(), headPos = new Float32Array(NA * 3);
  headGeo.setAttribute('position', new THREE.BufferAttribute(headPos, 3));
  for (let i = 0; i < NA; i++) {
    const R = 1250 + rnd() * 950, seg = 360, pos = new Float32Array(seg * 3), u = new Float32Array(seg);
    for (let k = 0; k < seg; k++) { const a = k / (seg - 1) * TAU; pos.set([Math.cos(a) * R, 0, Math.sin(a) * R], k * 3); u[k] = k / (seg - 1); }
    const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(pos, 3)); g.setAttribute('aU', new THREE.BufferAttribute(u, 1));
    const m = new THREE.ShaderMaterial({ transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      uniforms: { uHead: { value: rnd() }, uLen: { value: .12 + rnd() * .22 }, uA: { value: .25 + rnd() * .35 } },
      vertexShader: `attribute float aU; varying float vU; void main(){ vU=aU; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.); }`,
      fragmentShader: `uniform float uHead, uLen, uA; varying float vU; void main(){ float d=fract(uHead-vU); if(d>uLen) discard; float a=pow(1.-d/uLen,2.2)*uA; gl_FragColor=vec4(vec3(a),a); }` });
    const line = new THREE.Line(g, m); line.frustumCulled = false;
    line.rotation.set((rnd() - .5) * .9, rnd() * TAU, (rnd() - .5) * .7); line.updateMatrixWorld();
    scene.add(line); arcs.push({ line, R, sp: (.012 + rnd() * .03) * (rnd() < .5 ? -1 : 1), m });
  }
  const headMat = new THREE.ShaderMaterial({ transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, uniforms: { uPR: { value: PR } },
    vertexShader: `uniform float uPR; void main(){ gl_PointSize=6.*uPR; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.); }`,
    fragmentShader: `void main(){ float d=length(gl_PointCoord-.5)*2.; float a=exp(-d*d*8.)*.9; gl_FragColor=vec4(vec3(a),a); }` });
  const heads = new THREE.Points(headGeo, headMat); heads.frustumCulled = false; scene.add(heads);
  arcs.headGeo = headGeo; arcs.headPos = headPos; arcs.headMat = headMat; arcs.heads = heads;
}
const arcV = new THREE.Vector3();
function setComets(on) { for (const a of arcs) a.line.visible = on; arcs.heads.visible = on; }

/* ================================================================== projection */
export const PV = new THREE.Matrix4();
/** World -> CSS px. Writes [x, y, depth] into out. False when behind the camera. */
export function project(x, y, z, out) {
  const e = PV.elements, w = e[3]*x + e[7]*y + e[11]*z + e[15];
  if (w <= 0) return false;
  out[0] = ((e[0]*x + e[4]*y + e[8]*z + e[12]) / w * .5 + .5) * innerWidth;
  out[1] = (.5 - (e[1]*x + e[5]*y + e[9]*z + e[13]) / w * .5) * innerHeight;
  out[2] = w; return true;
}
/** Projected pixel radius of the black hole's 510 u sphere at camera distance d (SPEC 4.0 coverage rule). */
export function holeRadiusPx(d) {
  if (d <= BH_R) return 1e6;
  return (innerHeight / 2) * Math.tan(Math.asin(BH_R / d)) / Math.tan(camera.fov * Math.PI / 360);
}
/** Minimum camera distance that keeps the hole's disc <= 30 % of the screen (and >= 640 u). */
export function minHoleDistance() {
  const maxR = Math.sqrt(.30 * innerWidth * innerHeight / Math.PI);
  const tanHalf = Math.tan(camera.fov * Math.PI / 360);
  const s = Math.atan(maxR / (innerHeight / 2) * tanHalf);
  return Math.max(640, BH_R / Math.sin(s));
}

/* ================================================================== overlay (DOM pinned to 3D, Typed OM) */
const TYPED = typeof CSSTranslate !== 'undefined' && typeof CSS !== 'undefined' && CSS.px && document.body.attributeStyleMap;
export const overlay = (() => {
  const layer = document.getElementById('overlay');
  const items = [];
  const tmp = [0, 0, 0];
  return {
    layer, items,
    /** Pin el to a star (idx) / point (pos:[x,y,z]) / function returning [x,y,z].
     *  opts {idx, pos, dx, dy, near:160, far:1e9, opacity:()=>n, anchor:'center'|'left'|'none'} -> handle {el, o, remove()} */
    add(el, opts = {}) {
      el.style.position = 'absolute'; el.style.left = '0'; el.style.top = '0'; el.style.willChange = 'transform,opacity';
      layer.appendChild(el);
      const o = { dx: 0, dy: 0, near: 160, far: 1e9, anchor: 'center', ...opts };
      const h = { el, o, op: -1, x: -1e9, y: -1e9, remove() { const i = items.indexOf(h); if (i >= 0) items.splice(i, 1); el.remove(); } };
      if (TYPED) {
        h.tx = CSS.px(0); h.ty = CSS.px(0);
        const parts = [new CSSTranslate(h.tx, h.ty)];
        if (o.anchor === 'center') parts.push(new CSSTranslate(CSS.percent(-50), CSS.percent(-50)));
        else if (o.anchor === 'left') parts.push(new CSSTranslate(CSS.px(0), CSS.percent(-50)));
        h.tv = new CSSTransformValue(parts); h.opv = CSS.number(0);
      }
      items.push(h); return h;
    },
    _tick() {
      for (let n = 0; n < items.length; n++) {
        const h = items[n], o = h.o; let x, y, z;
        if (o.idx != null) { const i = o.idx * 3; x = stars.pos[i]; y = stars.pos[i+1]; z = stars.pos[i+2]; }
        else if (typeof o.pos === 'function') { const p = o.pos(); x = p[0]; y = p[1]; z = p[2]; }
        else { x = o.pos[0]; y = o.pos[1]; z = o.pos[2]; }
        let op = 0;
        if (project(x, y, z, tmp)) {
          op = Math.min(1, Math.max(0, (tmp[2] - o.near) / 260)) * (tmp[2] < o.far ? 1 : Math.max(0, 1 - (tmp[2] - o.far) / 600));
          if (o.opacity) op *= o.opacity();
          if (op > .005) {
            const px = tmp[0] + o.dx, py = tmp[1] + o.dy;
            if (Math.abs(px - h.x) > .1 || Math.abs(py - h.y) > .1) {
              h.x = px; h.y = py;
              if (TYPED) { h.tx.value = px; h.ty.value = py; h.el.attributeStyleMap.set('transform', h.tv); }
              else h.el.style.transform = `translate3d(${px.toFixed(1)}px,${py.toFixed(1)}px,0)` + (o.anchor === 'center' ? ' translate(-50%,-50%)' : o.anchor === 'left' ? ' translateY(-50%)' : '');
            }
          }
        }
        if (Math.abs(op - h.op) > .01) {
          h.op = op;
          if (TYPED) { h.opv.value = Math.round(op * 100) / 100; h.el.attributeStyleMap.set('opacity', h.opv); }
          else h.el.style.opacity = op.toFixed(2);
          h.el.style.visibility = op > .005 ? 'visible' : 'hidden';
        }
      }
    },
  };
})();

/* ================================================================== camera primitives */
export const HOME_DIR = new THREE.Vector3(0, Math.sin(11.3 * Math.PI / 180), Math.cos(11.3 * Math.PI / 180)).normalize();
export const HOME_DIST = 2300;
export const cam = (() => {
  let flight = null;
  const v1 = new THREE.Vector3(), v2 = new THREE.Vector3();
  const api = {
    /** Glide target+camera. dir = unit vector from target to camera (default: keep current). */
    flyTo(target, dist, dur = 1400, dir = null, easeName = 'inOutCubic', onDone = null) {
      const d = dir ? dir.clone().normalize() : camera.position.clone().sub(controls.target).normalize();
      if (d.y < .1) { d.y = .18; d.normalize(); }
      flight = { t0: performance.now(), dur: Math.max(1, dur), fP: camera.position.clone(), fT: controls.target.clone(),
        tT: target.clone(), tP: target.clone().add(d.multiplyScalar(dist)), ease: easings[easeName] || easings.inOutCubic, onDone };
      wake();
    },
    /** Frame world points ([x,y,z]) keeping the viewing direction. */
    frame(points, { pad = 1.35, min = 380, max = 3600, dur = 1300, include0 = false, dir = null, easeName = 'inOutCubic' } = {}) {
      if (!points.length) return;
      v1.set(0, 0, 0); let n = 0;
      for (const p of points) { v1.x += p[0]; v1.y += p[1]; v1.z += p[2]; n++; }
      if (include0) n++;
      v1.divideScalar(n);
      let r = 0; for (const p of points) r = Math.max(r, v2.set(p[0], p[1], p[2]).distanceTo(v1));
      if (include0) r = Math.max(r, v1.length());
      const dist = Math.min(max, Math.max(min, (r * pad) / Math.sin(camera.fov * Math.PI / 360)));
      api.flyTo(v1.clone(), dist, dur, dir, easeName);
    },
    home(dur = 1800) { api.flyTo(new THREE.Vector3(), HOME_DIST, dur, HOME_DIR); },
    stop() { flight = null; },
    get flying() { return !!flight; },
    get distance() { return camera.position.distanceTo(controls.target); },
    userRecent(ms = 6000) { return performance.now() - state.userActiveAt < ms; },
    _tick(now) {
      if (!flight) return;
      const k = Math.min(1, (now - flight.t0) / flight.dur), e = flight.ease(k);
      camera.position.lerpVectors(flight.fP, flight.tP, e); controls.target.lerpVectors(flight.fT, flight.tT, e);
      if (k >= 1) { const f = flight; flight = null; if (f.onDone) f.onDone(); }
    },
  };
  return api;
})();

/* ================================================================== picking + pointer */
export const pointer = { x: -1e4, y: -1e4, over: false, down: null, hover: -1 };
const ptmp = [0, 0, 0];
/** Screen-space pick over every visible star. Returns star index or -1. */
export function pick(mx, my, filter = null) {
  let best = -1, bs = 1e9; const P = stars.pos, B = stars.bright, S = stars.size;
  for (let i = 0; i < stars.count; i++) {
    if (B[i] <= .001 || (filter && !filter(i))) continue;
    if (stars.stateOf(i) === ST.FALLING) continue;
    if (!project(P[i*3], P[i*3+1], P[i*3+2], ptmp)) continue;
    const big = S[i] >= 20, r = big ? 16 : 9, dx = ptmp[0] - mx, dy = ptmp[1] - my, d2 = dx*dx + dy*dy;
    if (d2 > r*r) continue;
    const s = Math.sqrt(d2) / r + (big ? 0 : .4) + ptmp[2] * 1e-5;
    if (s < bs) { bs = s; best = i; }
  }
  return best;
}
function userInput() { state.userActiveAt = performance.now(); wake(); }
canvas.addEventListener('pointermove', e => { pointer.x = e.clientX; pointer.y = e.clientY; pointer.over = true; pointer.moved = true; wake(); });
canvas.addEventListener('pointerleave', () => { pointer.over = false; });
canvas.addEventListener('pointerdown', e => { pointer.down = [e.clientX, e.clientY]; userInput(); if (!state.director) controls.autoRotate = false; cam.stop(); bus.emit('user:grab', {}); });
canvas.addEventListener('pointerup', e => {
  const d = pointer.down; pointer.down = null; userInput();
  if (!d || Math.hypot(e.clientX - d[0], e.clientY - d[1]) > 5 || !state.introDone) return;
  const i = pick(e.clientX, e.clientY);
  bus.emit(i >= 0 ? 'click' : 'click:empty', { idx: i, x: e.clientX, y: e.clientY });
});
canvas.addEventListener('wheel', () => { cam.stop(); if (!state.director) controls.autoRotate = false; userInput(); bus.emit('user:grab', {}); }, { passive: true });

/* ================================================================== host (panel) messages */
window.addEventListener('message', e => {
  if (e.origin !== location.origin) return;
  const m = e.data; if (!m || typeof m !== 'object' || !m.nova) return;
  if (m.nova === 'visible') { state.visible = !!m.on; if (state.visible) wake(); }
  else if (m.nova === 'inset') { state.insetRight = Math.max(0, +m.right || 0); }
  bus.emit('host:' + m.nova, m);
});
/** Tell the hosting panel something (no-op when standalone). */
export function toHost(msg) { if (window.parent !== window) window.parent.postMessage({ brain: true, ...msg }, location.origin); }
addEventListener('focus', () => { state.focused = true; wake(); });
addEventListener('blur', () => { state.focused = false; });

/* ================================================================== frame hooks */
const frameFns = [];
/** Register fn(dt, t, now) for every RENDERED frame, ascending order. Never allocate inside. */
export function onFrame(fn, order = 0) { frameFns.push({ fn, order }); frameFns.sort((a, b) => a.order - b.order); }

/* ================================================================== sizing / quality */
let dustN = dust.count, bloomHalf = false;
function applySize() {
  camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix();
  renderer.setPixelRatio(PR); renderer.setSize(innerWidth, innerHeight);
  composer.setPixelRatio(PR); composer.setSize(innerWidth, innerHeight);
  if (bloomHalf) bloom.setSize(Math.round(innerWidth * PR / 2), Math.round(innerHeight * PR / 2));
  for (const m of [stars.material, dust.material]) { m.uniforms.uPR.value = PR; m.uniforms.uH.value = innerHeight; }
  arcs.headMat.uniforms.uPR.value = bgPts.material.uniforms.uPR.value = PR;
  finish.uniforms.uPR.value = PR; finish.uniforms.uRes.value.set(innerWidth * PR, innerHeight * PR);
  fxu.uAspect.value = innerWidth / innerHeight;
  bus.emit('resize', { w: innerWidth, h: innerHeight, pr: PR });
  wake();
}
addEventListener('resize', applySize);
export function pixelRatio() { return PR; }
const LADDER = ['full', 'halfBloom', 'pr', 'dust', 'calm'];
function applyQuality(q) {
  state.quality = q;
  bloomHalf = q >= 1 || state.game;
  PR = Math.max(1, Math.min(prCap, (q >= 2 ? prCap - .25 * (q - 1) : prCap)));
  if (state.game) PR = 1;
  dustN = q >= 3 ? 7000 : dust.count; dust.points.geometry.setDrawRange(0, dustN);
  applySize();
}
export function setGame(on) { if (state.game === !!on) return; state.game = !!on; setComets(!on && look.comets); applyQuality(state.quality); }

/* ================================================================== GPU timer (quality ladder) */
const gl = renderer.getContext();
const tq = gl.getExtension('EXT_disjoint_timer_query_webgl2');
let tqPending = null; const gpuSamples = []; let goodSince = 0;
function timerBegin() { if (!tq || tqPending) return null; const q = gl.createQuery(); gl.beginQuery(tq.TIME_ELAPSED_EXT, q); return q; }
function timerEnd(q) { if (!q) return; gl.endQuery(tq.TIME_ELAPSED_EXT); tqPending = q; }
function timerPoll() {
  if (!tqPending) return;
  if (!gl.getQueryParameter(tqPending, gl.QUERY_RESULT_AVAILABLE)) return;
  if (!gl.getParameter(tq.GPU_DISJOINT_EXT)) { const ms = gl.getQueryParameter(tqPending, gl.QUERY_RESULT) / 1e6; gpuSamples.push(ms); if (gpuSamples.length > 60) gpuSamples.shift(); state.gpuMs = ms; }
  gl.deleteQuery(tqPending); tqPending = null;
}
const cpuSamples = [];
function ladder(now) {
  const src = tq ? gpuSamples : cpuSamples; if (src.length < 60) return;
  const sorted = src.slice().sort((a, b) => a - b), p90 = sorted[Math.floor(sorted.length * .9)];
  const hi = tq ? 2.4 : state.vsync * tierFrames() * 1.35, lo = tq ? 1.4 : state.vsync * tierFrames() * 1.05;
  if (p90 > hi && state.quality < LADDER.length - 1) { applyQuality(state.quality + 1); src.length = 0; goodSince = now; }
  else if (p90 < lo) { if (!goodSince) goodSince = now; if (now - goodSince > 10000 && state.quality > 0) { applyQuality(state.quality - 1); src.length = 0; goodSince = now; } }
  else goodSince = 0;
}
export const perf = { gpuSamples, cpuSamples, frameTimes: [], get timerQuery() { return !!tq; } };

/* ================================================================== governor + loop (C-2) */
let running = false, rafTick = 0, lastRaf = 0, lastRender = 0, forceRender = true;
const rafDeltas = [];
function tierFrames() {
  const v = state.vsync;
  if (state.tier === 'DEEP') return Math.max(1, Math.ceil(47 / v));
  if (state.tier === 'CALM') return Math.max(1, Math.ceil(31 / v));
  return Math.max(1, Math.ceil(15.5 / v));
}
function pickTier(now) {
  const quiet = now - Math.max(state.activeAt, state.userActiveAt);
  let tier = 'ACTIVE';
  if (!state.turn && !cam.flying && !pointer.down && quiet > 10000) tier = 'CALM';
  if (tier === 'CALM' && !state.focused && quiet > 120000) tier = 'DEEP';
  if ((state.game || state.quality >= 4) && tier === 'ACTIVE' && !state.turn) tier = 'CALM';
  state.tier = tier;
}
function canRender() { return state.visible && !document.hidden && !state.paused; }
/** Make sure the loop is running (called on any input/event). Rendering resumes on the very next frame. */
export function wake() { forceRender = true; if (!running && started && canRender()) { running = true; requestAnimationFrame(loop); } }
document.addEventListener('visibilitychange', () => { if (!document.hidden) wake(); });

const lerpK = (rate, dt) => 1 - Math.exp(-rate * dt);
let introStart = 0, introSkip = false, viewOff = 0, started = false, ambSpinNow = 1, dustSpinNow = 1, diskPhase = 0, dustTime = 0, comets = 0;
let lastSweep = 0, lastRebase = 0;
const ez = { bright: 1, ring: 1, glow: 1, ringSharp: 16, expo: 1, dust: 1, house: 0 };   // eased moods
function loop(now) {
  if (!canRender()) { running = false; return; }
  requestAnimationFrame(loop);
  // vsync estimate: median of the last 30 rAF deltas
  if (lastRaf) { const d = now - lastRaf; if (d > 2 && d < 100) { rafDeltas.push(d); if (rafDeltas.length > 30) rafDeltas.shift(); if (rafDeltas.length >= 10 && (rafTick & 31) === 0) { const s = rafDeltas.slice().sort((a, b) => a - b); state.vsync = s[s.length >> 1]; } } }
  lastRaf = now; rafTick++;
  pickTier(now);
  if (!forceRender && rafTick % tierFrames() !== 0) return;
  forceRender = false;
  render(now);
}

function render(now) {
  const dt = FIXED_DT || Math.min(lastRender ? (now - lastRender) / 1000 : 1 / 60, .1);
  const cpu0 = performance.now();
  lastRender = now;
  state.dt = dt; state.now = now; state.t += dt; state.frame++;
  fxu.uT.value = state.t;
  if (lastRender && perf.frameTimes.length < 4000) perf.frameTimes.push(dt * 1000);

  const intro = introSkip ? 1 : Math.min(1, (now - introStart) / 3400);
  if (intro >= 1 && !state.introDone) { state.introDone = true; bus.emit('intro:done', {}); }
  stars.material.uniforms.uIntro.value = dust.material.uniforms.uIntro.value = edges.material.uniforms.uIntro.value = intro;
  bh.uniforms.uIntro.value = Math.min(1, intro * 1.6);

  // eased looks (dt-correct)
  const k6 = lerpK(6, dt), k3 = lerpK(3.2, dt);
  edges.material.uniforms.uDim.value += (look.dim - edges.material.uniforms.uDim.value) * k3;
  ez.house += (look.house - ez.house) * lerpK(1 / .45 * 1.6, dt);
  ez.dust += (look.dust - ez.dust) * k3;
  const B = bh.uniforms, L = look.bh;
  ez.bright += (L.bright - ez.bright) * k6; ez.ring += (L.ring - ez.ring) * k6;
  ez.glow += (L.glow - ez.glow) * k6; B.uTempExp.value += (L.tempExp - B.uTempExp.value) * k6;
  ez.ringSharp += (L.ringSharp - ez.ringSharp) * k6; B.uRingR.value += (L.ringR - B.uRingR.value) * k6;
  ez.expo += (L.expo - ez.expo) * k6; B.uTintA.value += (L.tintA - B.uTintA.value) * k6;
  B.uTintCol.value.set(look.tintCol[0], look.tintCol[1], look.tintCol[2]);
  state.ts += (look.ts - state.ts) * lerpK(1 / .35, dt);
  ambSpinNow += (L.spin - ambSpinNow) * lerpK(1.5, dt);
  dustSpinNow += (look.dustSpin - dustSpinNow) * lerpK(1.5, dt);
  renderer.toneMappingExposure += (look.exposure - renderer.toneMappingExposure) * k3;
  fxu.uDrive.value = state.drive;

  // ambient clocks (time-scaled: disk, dust, comets, sky)
  const ad = dt * state.ts;
  state.ta += ad; diskPhase += ad * ambSpinNow; dustTime += ad * dustSpinNow; comets += ad;
  B.uDiskPhase.value = diskPhase; dust.material.uniforms.uDustTime.value = dustTime;
  skyU.uSkyRot.value += ad * .004;
  B.uHistHead.value = voice.headPos(now);

  for (const f of frameFns) { try { f.fn(dt, state.t, now); } catch (e) { console.error('[frame]', e); } }
  B.uBright.value = ez.bright * (1 + pulse.bright); B.uRingGain.value = ez.ring * (1 + pulse.ring);
  B.uGlow.value = ez.glow * (1 + pulse.glow); B.uRingSharp.value = ez.ringSharp * (1 + pulse.ringSharp);
  B.uExpo.value = ez.expo * (1 + pulse.expo);
  dust.material.uniforms.uGlow.value = ez.dust * (1 + pulse.dust);
  fxu.uHouse.value = Math.min(1, Math.max(0, ez.house + pulse.house));
  pulse.ring = pulse.ringSharp = pulse.bright = pulse.expo = pulse.glow = pulse.dust = pulse.house = 0;

  if (!state.director) {
    cam._tick(now);
    if (!controls.autoRotate && !REDUCED && !state.paused && !cam.flying && !state.turn && now - state.userActiveAt > 9000) controls.autoRotate = true;
  }
  controls.update(dt);
  // view offset: the director owns it when present; otherwise centre left of the panel column
  let ox, oy;
  if (state.viewOffOwned) { ox = state.viewOff[0]; oy = state.viewOff[1]; }
  else { const want = state.insetRight > 0 && innerWidth > 760 ? state.insetRight / 2 : 0; viewOff += (want - viewOff) * lerpK(5, dt); ox = viewOff; oy = 0; }
  if (Math.abs(ox) > .25 || Math.abs(oy) > .25) camera.setViewOffset(innerWidth, innerHeight, ox, oy, innerWidth, innerHeight);
  else if (camera.view && camera.view.enabled) camera.clearViewOffset();
  camera.updateMatrixWorld();
  PV.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);

  // black hole screen position + projected radius
  const hp = state.bhPx;
  if (project(0, 0, 0, hp)) { hp[2] = holeRadiusPx(camera.position.length()); }
  B.uBHpx.value.set(hp[0] * PR, (innerHeight - hp[1]) * PR, hp[2] * PR);
  B.uCam.value.copy(camera.position); B.uPV.value.copy(PV);
  finish.uniforms.uTime.value = state.t;

  if (arcs.heads.visible) {
    for (let i = 0; i < arcs.length; i++) { const a = arcs[i], hu = a.m.uniforms.uHead;
      hu.value = (hu.value + a.sp * ad + 1) % 1; const ang = hu.value * TAU;
      arcV.set(Math.cos(ang) * a.R, 0, Math.sin(ang) * a.R).applyMatrix4(a.line.matrixWorld);
      arcs.headPos[i*3] = arcV.x; arcs.headPos[i*3+1] = arcV.y; arcs.headPos[i*3+2] = arcV.z; }
    arcs.headGeo.attributes.position.needsUpdate = true;
  }

  if (evDirty) { eventTex.needsUpdate = true; evDirty = false; }
  stars._flush(); edges._flush();
  overlay._tick();

  if (pointer.over && !pointer.down && state.introDone && (pointer.moved || (state.frame & 3) === 0)) {
    pointer.moved = false;
    const h = pick(pointer.x, pointer.y);
    if (h !== pointer.hover) { pointer.hover = h; fxu.uHov.value[0] = h; canvas.style.cursor = h >= 0 ? 'pointer' : 'grab'; bus.emit('hover', { idx: h }); }
  } else if (pointer.hover !== -1 && !pointer.over) { pointer.hover = -1; fxu.uHov.value[0] = -1; bus.emit('hover', { idx: -1 }); }

  const q = (state.frame % 10 === 0) ? timerBegin() : null;
  composer.render();
  timerEnd(q); timerPoll();
  const cpu = performance.now() - cpu0;
  if (!tq) { cpuSamples.push(dt * 1000); if (cpuSamples.length > 60) cpuSamples.shift(); }
  perf.lastCpuMs = cpu;
  ladder(now);
  if (now - lastSweep > 5000) { lastSweep = now; stars._sweep(); }
  if (state.t > 1800 && !state.turn && now - state.activeAt > 60000 && now - lastRebase > 60000) rebase(1800, now);
  state.fps = state.fps * .95 + (1 / Math.max(dt, 1e-3)) * .05;
}

/** Shift every clock back by `shift` seconds (keeps float precision over long sessions). */
function rebase(shift, now) {
  lastRebase = now;
  state.t -= shift; fxu.uT.value = state.t;
  for (let i = 0; i < EW * EH; i++) { const o = i * 4; if (evData[o+2] > 0) { evData[o] -= shift; if (evData[o+1] < 1e8) evData[o+1] -= shift; } }
  evDirty = true;
  fxu.uSonar.value.x -= shift; fxu.uFrontC.value.w -= shift;
  for (const v of fxu.uDustFx.value) v.w -= shift;
  bh.uniforms.uBand.value.x -= shift; bh.uniforms.uHot.value.y -= shift; if (bh.uniforms.uIgnite.value >= 0) bh.uniforms.uIgnite.value -= shift;
  bus.emit('clock:rebase', { shift });
}

/** Begin rendering (after data is loaded). Pools exist already; shaders are compiled first. */
export async function start() {
  const today = new Date().toISOString().slice(0, 10);
  let seen = ''; try { seen = localStorage.getItem('novaBrain.intro') || ''; } catch { /* private */ }
  introSkip = seen === today && !params.has('intro');
  try { localStorage.setItem('novaBrain.intro', today); } catch { /* private */ }
  try { if (renderer.compileAsync) await renderer.compileAsync(scene, camera); } catch (e) { console.warn('[brain] compileAsync', e); }
  introStart = performance.now(); lastRender = 0;
  if (introSkip) { camera.position.copy(HOME_DIR).multiplyScalar(HOME_DIST); controls.target.set(0, 0, 0); }
  else cam.flyTo(new THREE.Vector3(), HOME_DIST, 4400, HOME_DIR);
  started = true; state.activeAt = performance.now();
  if (canRender()) { running = true; requestAnimationFrame(loop); }
  bus.emit('start', {});
}

state.focused = document.hasFocus();
applySize();

export const debug = { get fps() { return state.fps; }, get PR() { return PR; }, get quality() { return LADDER[state.quality]; }, get gpuMs() { return state.gpuMs; }, get tier() { return state.tier; } };
window.__brain = Object.assign(window.__brain || {}, { THREE, scene, camera, controls, renderer, composer, stars, edges, bh, cam, state, look, pulse, bus, debug, project, fxu, finish, perf, voice, setLensSky, ST, SHAPE });
