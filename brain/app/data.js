// Nova Brain -- data: Nova's memory (GET /api/brain) as a galaxy of stars.
//
// Layout (SPEC 4.6 C-6) is STABLE. Every star owns a numbered slot in its
// group -- a cluster for main stars, a parent for fragments -- and a slot's
// position is a pure function of (group, slot). Slot numbers are kept in
// localStorage (novaBrain.slots.v1) and never compacted, so a reload, even
// after rows were deleted, puts every star back exactly where it was.
// A memory row's slot key comes from its text (lowercase alphanumerics of the
// first 80 chars), not its id: `nova_memory.py import` renumbers every project
// row, and a provisional birth is bound to its row seconds later -- both keep
// their slot. Places she touched (files, folders, sites, pages, tools) keep
// their computed position in novaBrain.places.v1.
// The one sanctioned layout motion: the diary arm slides one notch when a new
// day appears.
//
// Colour carries no cluster hue (SPEC 4.0): main stars warm white, fragments
// cool white. stars.add({shape}): 0 star = memories (and past conversations),
// 1 square = files and folders, 2 ring = sites and pages, 3 triangle = tools,
// 4 hexagon = skills.
// Rows deleted while the page is open (pollNew) are released, not erased:
// node.gone = true, listed in pollNew().gone and bus 'graph:gone'; a row
// re-added with the same text binds back to its old star (a re-index).

import * as core from './core.js';
import { stars, edges, overlay, api, hash01, TAU, state, bus, onFrame, params, DEMO } from './core.js';

/* ------------------------------------------------------------------ constants */
const DEG = Math.PI / 180;
const GOLDEN = 137.508 * DEG;              // sunflower angle
const WHITE = [1, .97, .93];               // every main star (SPEC 4.0)
const FRAG = [.86, .89, .96];              // fragments: cool white
const MIN_R = 380;                         // nothing nearer the hole than this
const DAY_MS = 864e5;
const PLACE_CAP = 400;                     // places remembered (least recently used dropped)
const STALE_MS = 14 * DAY_MS;              // unused this long -> 25 % brightness
const KNOT_D = 31.5;                       // diary days >= 30 knot up around this arm position
const SECTOR_TH = [18, 54, 90, 162, 198, 342];
const SECTOR_R = 1080;
export const SHAPES = { STAR: 0, SQUARE: 1, RING: 2, TRIANGLE: 3, HEXAGON: 4 };
const SHAPE_OF = { file: 1, folder: 1, site: 2, page: 2, tool: 3, skill: 4 };   // past conversations are memory: a star (SPEC 4.0 glyphs)
const PLACE_KINDS = new Set(['file', 'folder', 'site', 'page', 'tool']);
// ?replay / ?bench must be deterministic: no stored layout in, nothing out.
// ?demo plays fixtures over the real galaxy: reads the layout, writes nothing.
const READ_STORE = !params.has('replay') && !params.has('bench');
const WRITE_STORE = READ_STORE && !DEMO;
const K_SLOTS = 'novaBrain.slots.v1', K_SECTORS = 'novaBrain.sectors.v1', K_PLACES = 'novaBrain.places.v1', K_OLD = 'novaBrain.dyn.v1';

/* ------------------------------------------------------------------ clusters */
// inner = a crescent beside Nova (r 400-540); mid ring = knowledge; outer = the
// world she touches. tint/color stay on the records for other modules; stars
// never use them.
export const CLUSTERS = [
  { id: 'you',     label: 'You',        color: '#e8f3ff', r: 470,  a: 200, y: 30, inner: true },
  { id: 'loops',   label: 'Open loops', color: '#ffd9a8', r: 470,  a: 20,  y: -25, inner: true },
  { id: 'nova',    label: 'Nova',       color: '#b46cff', r: 760,  a: 0 },
  { id: 'orbit',   label: 'Orbit',      color: '#3fd0ff', r: 900,  a: 36 },
  { id: 'school',  label: 'School',     color: '#ff5c7a', r: 760,  a: 72 },
  { id: 'tpn',     label: 'TPN',        color: '#ffb547', r: 900,  a: 108 },
  { id: 'mc',      label: 'Minecraft',  color: '#5dff9b', r: 760,  a: 144 },
  { id: 'roblox',  label: 'Roblox',     color: '#ff6b3d', r: 900,  a: 180 },
  { id: 'machine', label: 'Machine',    color: '#5c8bff', r: 760,  a: 216 },
  { id: 'studio',  label: 'Studio',     color: '#ff7ad9', r: 900,  a: 252 },
  { id: 'diary',   label: 'Diary',      color: '#d8d2c4', r: 820,  a: 288 },
  { id: 'skills',  label: 'Skills',     color: '#c9f2ff', r: 900,  a: 324 },
  { id: 'files',   label: 'Files',      color: '#e6e6e6', r: 1380, a: 128, y: 170, place: true, sp: 48 },
  { id: 'web',     label: 'Web',        color: '#a9c8ff', r: 1420, a: 308, y: -150, place: true },
  { id: 'hands',   label: 'Actions',    color: '#d7c9ff', r: 1330, a: 232, y: 210, place: true, ring: true },
];
const TOPIC_RULES = [
  ['you', ['uno-working-style', 'uno-prefers', 'uno-design', 'uno-essay', 'agent-security', 'claude-code-resources']],
  ['nova', ['nova-', 'm5-atom', 'aesthetic-spotify', 'discord-', 'nexus-bot', 'neural-index']],
  ['orbit', ['orbit-', 'livedeck']],
  ['school', ['school-', 'presentation-deck']],
  ['tpn', ['tpn-']],
  ['mc', ['mc-', 'minecraft-', 'rpg-', 'cobblemon']],
  ['roblox', ['roblox-', 'nopixel', 'urban-by-uno', 'steal-a-spellbook', 'spellbook-', 'unoryx']],
  ['machine', ['headless', 'new-pc', 'desktop-app', 'live-wallpaper', 'pc-', 'windows-wmi', 'wmi-', 'msbuild', 'pcmaint', 'smart-app', 'rea-', 'red-alert']],
];
const topicLow = t => String(t || 'misc').trim().toLowerCase() || 'misc';
/** The family a topic belongs to by name, or null. */
function familyOf(t) {
  for (const [c, pats] of TOPIC_RULES) if (pats.some(p => t.startsWith(p) || t.includes(p))) return c;
  return null;
}
/** Cluster id of a topic: its family, the sector it opened (C-6.4), or 'studio'. */
export function clusterOfTopic(topic) {
  const t = topicLow(topic);
  const fam = familyOf(t); if (fam) return fam;
  const s = sectors[t];
  return s != null && s >= 0 && graph.clusters.has('sec:' + t) ? 'sec:' + t : 'studio';
}

/* ------------------------------------------------------------------ graph */
export const graph = {
  nodes: [],                 // every node, in creation order
  byKey: new Map(),          // 'topic:orbit-project' -> node ('prov:…' stays an alias after binding)
  byRow: new Map(),          // memory row id -> node (the star that IS that row)
  starNode: [],              // star index -> node
  clusters: new Map(),       // id -> cluster record (center, labelEl, mains[], places[])
  maxRow: 0, total: 0, skills: [], profile: '',
};
CLUSTERS.forEach((c, i) => {
  const ang = c.a * DEG;
  const center = [Math.cos(ang) * c.r, c.y ?? Math.sin(i * 1.9) * 110, Math.sin(ang) * c.r];
  graph.clusters.set(c.id, { ...c, center, tint: hexRGB(c.color), mains: [], places: [], seed: hash01(c.id), axes: discAxes(center, .4) });
});
function hexRGB(h) { const n = parseInt(String(h).slice(1), 16) || 0xffffff; return [(n >> 16 & 255) / 255, (n >> 8 & 255) / 255, (n & 255) / 255]; }

export const pretty = slug => String(slug).replace(/[-_]+/g, ' ').replace(/\b\w/g, c => c.toUpperCase()).replace(/\bTpn\b/g, 'TPN').replace(/\bMc\b/g, 'MC').replace(/\bPc\b/g, 'PC').replace(/\bRea\b/g, 'REA');
export const shortText = (t, n = 6) => { const w = String(t || '').replace(/\s+/g, ' ').trim().split(' '); return w.slice(0, n).join(' ') + (w.length > n ? '…' : ''); };
const fmtDay = d => { try { return new Date(d + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' }); } catch { return d; } };
const pad2 = n => String(n).padStart(2, '0');
const today = () => { const d = new Date(); return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`; };
const nowStamp = () => { const d = new Date(); return `${today()} ${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`; };
/** Milliseconds of a row stamp ('2026-11-19 02:04:42', local time) or a number; 0 when unknown. */
function tsOf(at) {
  if (!at) return 0;
  if (typeof at === 'number') return at;
  const t = Date.parse(String(at).trim().replace(' ', 'T'));
  return Number.isFinite(t) ? t : 0;
}
/** C-6.6: newer memories burn a little brighter. */
const recency = ts => ts ? 1 + .3 * Math.exp(-Math.max(0, Date.now() - ts) / DAY_MS / 5) : 1;

/** Lowercase alphanumerics of the first 80 chars: what a provisional birth and its row share. */
export function normText(t) { return String(t || '').replace(/\s+/g, ' ').trim().slice(0, 80).toLowerCase().replace(/[^a-z0-9]/g, ''); }
function fnv(s) { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }
const thash = s => fnv(s).toString(36) + fnv('~' + s).toString(36);

/* ------------------------------------------------------------------ persistence */
// g: groupId -> {n: next slot, k: {slotKey: slot}, d?: first bulk-disc slot, o?: {twin key: row id holding it}}
// days: every diary day this browser has seen (a day whose rows were deleted keeps its notch)
const slots = { v: 1, g: {}, days: [] };
const sectors = {};                        // topic -> sector index 0..5, or -1 = decided: studio
const inUse = new Map();                   // groupId -> Map(slotKey -> row id | null) held by a node this session
const dirty = { slots: false, sectors: false, places: false };
let saveTimer = 0;
function readJSON(key) { if (!READ_STORE) return null; try { const s = localStorage.getItem(key); return s ? JSON.parse(s) : null; } catch { return null; } }
function writeJSON(key, v) { if (!WRITE_STORE) return; try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* full or blocked: the layout still works this session */ } }
function schedule() { if (WRITE_STORE && !saveTimer) saveTimer = setTimeout(flush, 400); }
const markSlots = () => { dirty.slots = true; schedule(); };
const markSectors = () => { dirty.sectors = true; schedule(); };
const markPlaces = () => { dirty.places = true; schedule(); };
function flush() {
  saveTimer = 0;
  if (dirty.slots) saveSlots();
  if (dirty.sectors) saveSectors();
  if (dirty.places) savePlaces();
}
addEventListener('pagehide', () => { if (saveTimer) { clearTimeout(saveTimer); flush(); } });

function loadStores() {
  const s = readJSON(K_SLOTS);
  if (s && s.g && typeof s.g === 'object') {
    for (const [gid, g] of Object.entries(s.g)) {
      if (!g || typeof g.k !== 'object' || !g.k) continue;
      // Two tabs can hand out the same slot: the first key keeps it, the other gets a fresh one when it shows up.
      const seen = new Set(), k = {}; let n = Number.isInteger(g.n) ? g.n : 0;
      for (const [key, v] of Object.entries(g.k)) {
        if (!Number.isInteger(v) || v < 0 || seen.has(v)) continue;
        seen.add(v); k[key] = v; if (v >= n) n = v + 1;
      }
      slots.g[gid] = { n, k };
      if (Number.isInteger(g.d)) slots.g[gid].d = g.d;
      if (g.o && typeof g.o === 'object') slots.g[gid].o = { ...g.o };
    }
    if (Array.isArray(s.days)) slots.days = s.days.filter(d => /^\d{4}-\d{2}-\d{2}$/.test(d));
  }
  const sec = readJSON(K_SECTORS);
  const taken = new Set();
  if (sec && typeof sec === 'object') for (const [t, v] of Object.entries(sec)) {
    if (!Number.isInteger(v) || v < -1 || v >= SECTOR_TH.length) continue;
    if (v >= 0 && taken.has(v)) { sectors[t] = -1; continue; }
    sectors[t] = v; if (v >= 0) taken.add(v);
  }
}
function saveSlots() {
  dirty.slots = false;
  const cur = readJSON(K_SLOTS);   // keep what another tab added meanwhile
  if (cur && cur.g && typeof cur.g === 'object') {
    for (const [gid, sg] of Object.entries(cur.g)) {
      if (!sg || typeof sg.k !== 'object' || !sg.k) continue;
      const g = grp(gid); let used = null;
      for (const [key, v] of Object.entries(sg.k)) {
        if (g.k[key] != null || !Number.isInteger(v)) continue;
        used = used || new Set(Object.values(g.k));
        if (!used.has(v)) { g.k[key] = v; used.add(v); if (v >= g.n) g.n = v + 1; }
      }
      if (Number.isInteger(sg.n) && sg.n > g.n) g.n = sg.n;
      if (g.d == null && Number.isInteger(sg.d)) g.d = sg.d;
      if (sg.o && typeof sg.o === 'object') for (const [key, id] of Object.entries(sg.o)) if (!g.o || g.o[key] == null) (g.o || (g.o = {}))[key] = id;
    }
    if (Array.isArray(cur.days)) for (const d of cur.days) if (!slots.days.includes(d)) slots.days.push(d);
  }
  writeJSON(K_SLOTS, slots);
}
function saveSectors() {
  dirty.sectors = false;
  const cur = readJSON(K_SECTORS);
  if (cur && typeof cur === 'object') {
    const taken = new Set(Object.values(sectors).filter(v => v >= 0));
    for (const [t, v] of Object.entries(cur)) if (sectors[t] == null && Number.isInteger(v) && (v < 0 || !taken.has(v))) { sectors[t] = v; if (v >= 0) taken.add(v); }
  }
  writeJSON(K_SECTORS, sectors);
}

/* ------------------------------------------------------------------ slots */
const grp = gid => slots.g[gid] || (slots.g[gid] = { n: 0, k: {} });
/** The slot `key` holds in group `gid`, handing out the next free number the first time. Never reused. */
function takeSlot(gid, key, rowId = null) {
  const g = grp(gid);
  let k = g.k[key];
  if (k == null) { k = g.n++; g.k[key] = k; markSlots(); }
  // a recorded holder of a twin key that is gone hands the key to this row
  if (rowId != null && g.o && g.o[key] != null && g.o[key] !== rowId) { g.o[key] = rowId; markSlots(); }
  let u = inUse.get(gid); if (!u) inUse.set(gid, u = new Map());
  u.set(key, rowId);
  return k;
}
/** The slot `key` would get, without taking it. */
function peekSlot(gid, key) { const g = slots.g[gid]; if (!g) return 0; return g.k[key] ?? g.n; }
const slotFree = (gid, key) => { const u = inUse.get(gid); return !u || !u.has(key); };
let loadIds = null;   // row ids of the load in progress
const present = id => id != null && (loading && loadIds ? loadIds.has(id) : graph.byRow.has(id));
/**
 * Slot key of a memory row: from its text, so it survives renumbering. Twins (the same opening in the same
 * group, e.g. a question asked twice in a day): the first keeps the plain key, every other one gets
 * key#<its row id>, so deleting any twin moves no other star. commit=false: a dry run (slotFor).
 */
function rowKey(letter, text, gid, id, commit = true) {
  const nt = normText(text);
  if (!nt) return 'id|' + id;
  const base = letter + '|' + thash(nt), own = base + '#' + id, g = slots.g[gid];
  if (g && g.k[own] != null) return own;
  if (slotFree(gid, base)) {
    const owner = g && g.o ? g.o[base] : undefined;
    return owner == null || owner === id || !present(owner) ? base : orphanTwin(g, gid, base, own, commit);
  }
  const holder = inUse.get(gid).get(base);
  if (commit && typeof holder === 'number') { const gg = grp(gid); if (!gg.o) gg.o = {}; if (gg.o[base] !== holder) { gg.o[base] = holder; markSlots(); } }
  return orphanTwin(slots.g[gid], gid, base, own, commit);
}
/**
 * A twin with no slot yet takes over the twin key of a row that is gone (a re-index renumbered it while the page
 * was closed) instead of a fresh slot, so it lands where its predecessor stood. commit=false (slotFor) only peeks.
 */
function orphanTwin(g, gid, base, own, commit) {
  if (!g) return own;
  const pre = base + '#', u = inUse.get(gid);
  let best = null;
  for (const key in g.k) {
    if (!key.startsWith(pre) || key === own || (u && u.has(key))) continue;
    const x = key.slice(pre.length);
    if (/^\d+$/.test(x) && present(+x)) continue;
    if (best == null || g.k[key] < g.k[best]) best = key;
  }
  if (best == null) return own;
  if (!commit) return best;
  g.k[own] = g.k[best]; delete g.k[best]; markSlots();
  return own;
}
const LETTER = { project: 'p', day: 'd', loop: 'l' };
const letterOf = kind => LETTER[kind] || 'f';

/* ------------------------------------------------------------------ geometry */
function radialXZ(p) { const l = Math.hypot(p[0], p[2]) || 1; return [p[0] / l, p[2] / l]; }
/** In-plane axes of a disc at c whose normal is normalize(up + lean * radial): e1 tangent, e2 outward (dipping). */
function discAxes(c, lean) {
  const [rx, rz] = radialXZ(c), nl = Math.hypot(1, lean);
  return [[-rz, 0, rx], [rx / nl, -lean / nl, rz / nl]];
}
function discXY(c, axes, x, y) {
  const a = axes[0], b = axes[1];
  return [c[0] + a[0] * x + b[0] * y, c[1] + a[1] * x + b[1] * y, c[2] + a[2] * x + b[2] * y];
}
const discPoint = (c, axes, rho, phi) => discXY(c, axes, Math.cos(phi) * rho, Math.sin(phi) * rho);
/** Keep a point out of the hole's sphere: push it radially to >= MIN_R. */
function guard(p, salt) {
  const d = Math.hypot(p[0], p[1], p[2]);
  if (d >= MIN_R + 12) return p;
  const s = (MIN_R + 12 + 30 * hash01('g:' + salt)) / Math.max(d, 1);
  return [p[0] * s, p[1] * s, p[2] * s];
}
/** Main star k of a cluster: a Fermat disc; the inner clusters bend it into a crescent at r 400-540. */
function mainPos(cl, k) {
  if (cl.sector != null) return k === 0 ? cl.center.slice() : discPoint(cl.center, cl.axes, 22 * Math.sqrt(k), k * GOLDEN);
  const sp = cl.sp || 36, rr = sp * Math.sqrt(k + .7), ang = k * 2.39996 + cl.seed * TAU;
  const y = cl.center[1] + (hash01(cl.id + '#' + k) - .5) * (40 + rr * .55);
  if (cl.inner) {
    const u = 70 * Math.tanh(Math.cos(ang) * rr / 70), th = cl.a * DEG + Math.sin(ang) * rr / cl.r, r = cl.r + u;
    return guard([Math.cos(th) * r, y, Math.sin(th) * r], cl.id + k);
  }
  return guard([cl.center[0] + Math.cos(ang) * rr, y, cl.center[2] + Math.sin(ang) * rr], cl.id + k);
}
// R2 low-discrepancy sequence: fragment j's direction depends on its slot only.
const R2A = 0.7548776662466927, R2B = 0.5698402909980532;
function ballPos(c, j, rot) {
  const d = 17 + 8.5 * Math.sqrt(j + 1);
  const u = (.5 + j * R2A) % 1, v = (.5 + j * R2B) % 1;
  const th = u * TAU + rot, z = 1 - 2 * v, s = Math.sqrt(Math.max(0, 1 - z * z));
  return [c[0] + s * Math.cos(th) * d, c[1] + z * d * .55, c[2] + s * Math.sin(th) * d];
}
/** Diary arm (C-6.3): day rank d (0 = newest). Ranks past the tail clamp to the archive knot. */
export function diaryArm(d) {
  d = Math.min(Number.isFinite(+d) ? +d : 0, KNOT_D);
  const r = 560 + 22 * d, th = (290 - .8 * d) * DEG;
  return [Math.cos(th) * r, 20 - 1.2 * d, Math.sin(th) * r];
}
const pearlPos = (day, j) => discPoint(day.pos, discAxes(day.pos, .3), (day.archived ? 2.4 : 4.2) * Math.sqrt(j + 1), j * GOLDEN);
// Relaxed sector disc (C-6.4): r = 22 sqrt(k), k * 137.508 deg, 40 repulsion passes. Computed once for a
// FIXED set of slots (independent of how many rows exist), so a slot's spot never changes; the outer band
// stays pinned so slots past the table continue the plain spiral without a seam.
const RELAX_N = 192, RELAX_MOVE = 150;
let relaxed = null;
function relaxTable() {
  if (relaxed) return relaxed;
  const N = RELAX_N, P = new Float64Array(N * 2), F = new Float64Array(N * 2), R0 = 38;
  for (let k = 0; k < N; k++) { const r = 22 * Math.sqrt(k), a = k * GOLDEN; P[2 * k] = Math.cos(a) * r; P[2 * k + 1] = Math.sin(a) * r; }
  for (let it = 0; it < 40; it++) {
    F.fill(0);
    for (let a = 0; a < N; a++) for (let b = a + 1; b < N; b++) {
      const dx = P[2 * a] - P[2 * b], dy = P[2 * a + 1] - P[2 * b + 1], d2 = dx * dx + dy * dy;
      if (d2 >= R0 * R0 || d2 < 1e-9) continue;
      const d = Math.sqrt(d2), f = (R0 - d) / d * .5;
      F[2 * a] += dx * f; F[2 * a + 1] += dy * f; F[2 * b] -= dx * f; F[2 * b + 1] -= dy * f;
    }
    for (let k = 1; k < RELAX_MOVE; k++) { P[2 * k] += F[2 * k] * .25; P[2 * k + 1] += F[2 * k + 1] * .25; }
  }
  return (relaxed = P);
}
function sectorFragPos(cl, j) {
  const k = j + 1;
  if (k < RELAX_MOVE) { const T = relaxTable(); return discXY(cl.center, cl.axes, T[2 * k], T[2 * k + 1]); }
  return discPoint(cl.center, cl.axes, 22 * Math.sqrt(k), k * GOLDEN);
}
// guarded: a topic of the inner You crescent (r ~400) would otherwise reach into the hole's 380 u
const bulkDiscPos = (parent, j) => guard(discPoint(parent.pos, discAxes(parent.pos, .4), 16 * Math.sqrt(j + 1), (j + 1) * GOLDEN), parent.key + '#b' + j);
const isSector = n => graph.clusters.get(n.cluster)?.sector != null;
/** Fragment slot j around its parent. */
function fragPos(parent, j) {
  if (parent.kind === 'day') return pearlPos(parent, j);
  if (parent.kind === 'topic' && isSector(parent)) return sectorFragPos(graph.clusters.get(parent.cluster), j);
  const g = slots.g['p:' + parent.key];
  if (g && g.d != null && j >= g.d) return bulkDiscPos(parent, j);
  return guard(ballPos(parent.pos, j, hash01(parent.key) * TAU), parent.key + '#' + j);
}
/** Nearest star in `list` to pos (a released star is never linked to). */
function nearest(list, pos) {
  let best = -1, bd = 1e18; const P = stars.pos;
  for (const i of list) { if (graph.starNode[i]?.gone) continue; const dx = P[i*3] - pos[0], dy = P[i*3+1] - pos[1], dz = P[i*3+2] - pos[2], d = dx*dx + dy*dy + dz*dz; if (d < bd) { bd = d; best = i; } }
  return best;
}

/* ------------------------------------------------------------------ diary arm */
const diary = { set: new Set(), desc: [] };
function addKnownDay(day) {
  if (diary.set.has(day)) return false;
  diary.set.add(day); diary.desc = [...diary.set].sort().reverse();
  if (!slots.days.includes(day)) { slots.days.push(day); markSlots(); }
  return true;
}
/** A day's place: on the arm by rank, or in the archive knot by how many days are older (so the knot never churns). */
function dayPos(day) {
  const i = diary.desc.indexOf(day), d = i < 0 ? 0 : i;
  if (d < 30) return { pos: diaryArm(d), archived: false };
  const m = diary.desc.length - 1 - d, c = diaryArm(KNOT_D);
  return { pos: discPoint(c, discAxes(c, .3), 9 * Math.sqrt(m + .5), m * GOLDEN), archived: true };
}
function dayOf(o) {
  const t = String(o.topic || '');
  if (/^\d{4}-\d{2}-\d{2}$/.test(t)) return t;
  const a = String(o.at || '').slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(a) ? a : today();
}
// Day rollover: every diary star whose spot changed glides there over 1.2 s (stars.setPos moves its lines).
const glide = { on: false, t0: 0, n: 0, idx: new Int32Array(0), from: new Float32Array(0), to: new Float32Array(0), nodes: [] };
function reflowDiary(except) {
  const moved = [];
  for (const day of diary.desc) {
    const n = graph.byKey.get('day:' + day);
    if (!n || n === except) continue;
    const dp = dayPos(day);
    if (Math.abs(dp.pos[0] - n.pos[0]) + Math.abs(dp.pos[1] - n.pos[1]) + Math.abs(dp.pos[2] - n.pos[2]) < 1e-6 && dp.archived === !!n.archived) continue;
    n.pos[0] = dp.pos[0]; n.pos[1] = dp.pos[1]; n.pos[2] = dp.pos[2]; n.archived = dp.archived;
    moved.push(n);
    for (const kid of n.kids) {
      if (kid.slot < 0) continue;
      const q = pearlPos(n, kid.slot); kid.pos[0] = q[0]; kid.pos[1] = q[1]; kid.pos[2] = q[2];
      moved.push(kid);
    }
  }
  if (moved.length) startGlide(moved);
}
function startGlide(nodes) {
  const set = new Map();
  if (glide.on) for (const n of glide.nodes) set.set(n.idx, n);   // a glide still running carries on from where it is
  for (const n of nodes) set.set(n.idx, n);
  const N = set.size, idx = new Int32Array(N), from = new Float32Array(N * 3), to = new Float32Array(N * 3), P = stars.pos;
  let i = 0;
  for (const [s, n] of set) { idx[i] = s; for (let c = 0; c < 3; c++) { from[i * 3 + c] = P[s * 3 + c]; to[i * 3 + c] = n.pos[c]; } i++; }
  Object.assign(glide, { on: true, t0: performance.now(), n: N, idx, from, to, nodes: [...set.values()] });
  core.wake?.();
  bus.emit('graph:diary', { moved: N });
}
function glideFrame(dt, t, now) {
  if (!glide.on) return;
  let k = ((typeof now === 'number' ? now : performance.now()) - glide.t0) / 1200;
  if (k >= 1) k = 1; else if (k < 0) k = 0;
  const e = k < .5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
  const I = glide.idx, F = glide.from, T = glide.to;
  for (let i = 0; i < glide.n; i++) {
    const o = i * 3;
    stars.setPos(I[i], F[o] + (T[o] - F[o]) * e, F[o + 1] + (T[o + 1] - F[o + 1]) * e, F[o + 2] + (T[o + 2] - F[o + 2]) * e);
  }
  if (k >= 1) { glide.on = false; glide.nodes = []; }
}

/* ------------------------------------------------------------------ sectors (new topics, C-6.4) */
function sectorCenter(t, i) { const th = SECTOR_TH[i] * DEG; return [Math.cos(th) * SECTOR_R, (hash01(t + '~y') * 2 - 1) * 60, Math.sin(th) * SECTOR_R]; }
function freeSector(t) {
  const taken = new Set(Object.values(sectors).filter(v => v >= 0));
  const free = SECTOR_TH.map((_, i) => i).filter(i => !taken.has(i));
  return free.length ? free[Math.floor(hash01(t) * free.length)] : -1;
}
function ensureSector(t, topic, i) {
  const id = 'sec:' + t;
  let cl = graph.clusters.get(id);
  if (cl) return cl;
  const center = sectorCenter(t, i);
  cl = { id, label: pretty(topic), color: '#f2efe9', r: SECTOR_R, a: SECTOR_TH[i], y: center[1], center, tint: hexRGB('#f2efe9'),
    mains: [], places: [], seed: hash01(id), axes: discAxes(center, .4), sector: i, topic: t };
  graph.clusters.set(id, cl);
  if (labelsOn) { addClusterLabel(cl); refreshCounts(); }
  bus.emit('graph:cluster', { cluster: cl });
  return cl;
}
// Rows of one topic "arriving together": consecutive arrivals < 4 s apart add up; the verdict holds 60 s.
const arrivals = new Map(), hints = new Map();
function noteArrivals(t, n) { const now = performance.now(), a = arrivals.get(t); if (a && now - a.t < 4000) { a.n += n; a.t = now; } else arrivals.set(t, { n, t: now }); }
/** Tell the layout a topic is about to get `count` rows (a study): it opens a sector / bulk disc for them. */
export function hintTopic(topic, count) { hints.set(topicLow(topic), { n: +count || 0, t: performance.now() }); }
function topicCount(t) {
  const now = performance.now(), a = arrivals.get(t), h = hints.get(t); let n = 0;
  if (a && now - a.t < 60000) n = a.n;
  if (h && now - h.t < 60000 && h.n > n) n = h.n;
  return n;
}
let loading = false, loadCounts = null;
/** Where a NEW topic goes; a family-less topic's verdict (sector or studio) is kept for good. */
function clusterForTopic(topic, o) {
  const t = topicLow(topic), fam = familyOf(t);
  if (fam) return fam;
  let s = sectors[t];
  if (s == null) {
    const n = loading ? (loadCounts.get(t) || 0) : Math.max(topicCount(t), o.bulk ? 8 : 0, o.count | 0);
    s = n >= 8 ? freeSector(t) : -1;
    sectors[t] = s; markSectors();
  }
  return s >= 0 ? ensureSector(t, topic, s).id : 'studio';
}

/* ------------------------------------------------------------------ node creation */
/** Where a new star goes: {pos, gid, slot, archived?}. Takes a slot when the spot is slot-based. */
function placeOf(spec, cl, parent) {
  const kind = spec.kind || '';
  if (!parent) {
    if (kind === 'day') return { ...dayPos(spec.day || String(spec.key).slice(4)), gid: '', slot: -1 };
    if (kind === 'history') return { pos: diaryArm(-1.5), gid: '', slot: -1 };
    if (spec.place && kind === 'site' && cl.id === 'web') return { pos: spaced(a => hostPos(spec.key, a)), gid: '', slot: -1 };
    if (spec.place && !cl.place && (kind === 'file' || kind === 'folder')) {
      const gid = 'a:' + cl.id, slot = takeSlot(gid, spec.key);
      return { pos: spaced(a => anchorPos(cl, slot, a)), gid, slot };
    }
    const gid = 'c:' + cl.id, slot = takeSlot(gid, spec.slotKey || spec.key, spec.rowId ?? null);
    return { pos: cl.ring ? ringPos(cl, slot) : mainPos(cl, slot), gid, slot };
  }
  if (parent.place && (kind === 'file' || kind === 'folder')) {
    const h = spec.depth ?? ((parent.depth | 0) + 1);
    return { pos: spaced(a => childPos(parent, h, spec.key, a)), gid: '', slot: -1 };
  }
  if (parent.place && (kind === 'page' || kind === 'site')) return { pos: spaced(a => satellitePos(parent, spec.key, a)), gid: '', slot: -1 };
  const gid = 'p:' + parent.key, slot = takeSlot(gid, spec.slotKey || spec.key, spec.rowId ?? null);
  return { pos: fragPos(parent, slot), gid, slot };
}
/** A main star's brightness: base x recency of its newest row x 25 % when a place went stale. */
function settleBright(n) { stars.setBright(n.idx, n.baseBright * recency(n.latest) * (n.dimmed ? .25 : 1)); }
let loaded = false;

/**
 * Create a node + its star. spec = {key, type:'main'|'frag', kind, cluster, label, text, rowId, at, parent(node),
 * born:bool, size, bright, pos?, shape?, slotKey?}. Returns the node (or the existing one for that key).
 */
export function createNode(spec) {
  const had = graph.byKey.get(spec.key); if (had) return had;
  const cl = graph.clusters.get(spec.cluster) || graph.clusters.get('studio');
  const parent = spec.type === 'main' ? null : (spec.parent || null);
  const isMain = !parent, kind = spec.kind || '';
  const where = Array.isArray(spec.pos) ? { pos: spec.pos.slice(0, 3), gid: '', slot: -1 } : placeOf(spec, cl, parent);
  // a place is saved with its position: rounded now, the saved copy restores it exactly
  const pos = spec.dynamic ? where.pos.map(r2) : where.pos;
  const shape = spec.shape ?? SHAPE_OF[kind] ?? SHAPES.STAR;
  const size = spec.size ?? (isMain ? 26 : 8.5 + hash01(spec.key + 's') * 4.5);
  const base = spec.bright ?? (isMain ? 1.2 : .72 + hash01(spec.key + 'b') * .35);
  const at = tsOf(spec.at);
  const idx = stars.add({ pos, size, bright: base * recency(at), color: isMain ? WHITE : FRAG, spike: isMain && (shape === 0 || shape === 4) ? 1 : 0,
    shape, born: spec.born ? state.t : -1, rand: hash01(spec.key + 'r') });
  if (idx < 0) return null;
  const node = { idx, key: spec.key, type: isMain ? 'main' : 'frag', kind, cluster: cl.id, label: spec.label || '', text: spec.text || '',
    rowId: spec.rowId ?? null, at: spec.at || '', parent, kids: [], members: [idx], pos, dynamic: !!spec.dynamic,
    shape, slot: where.slot, gid: where.gid, slotKey: spec.slotKey || spec.key, baseBright: base, latest: at };
  if (where.archived != null) node.archived = where.archived;
  if (spec.born) node.born = true;
  graph.nodes.push(node); graph.byKey.set(node.key, node); graph.starNode[idx] = node;
  if (node.rowId != null) graph.byRow.set(node.rowId, node);
  if (isMain) {
    // a file root inside a memory cluster hangs off its nearest memory; it is not one of the cluster's memories
    const outside = spec.place && !cl.place;
    const prev = nearest(cl.mains, pos);
    if (prev >= 0) edges.add(prev, idx, outside ? .05 : .06);
    (outside ? cl.places : cl.mains).push(idx);
  } else {
    const to = spec.place || parent.place ? parent.idx : nearest(parent.members, pos);
    edges.add(to >= 0 ? to : parent.idx, idx, .13);
    parent.kids.push(node); parent.members.push(idx);
    // a topic grows as it learns more
    if (!parent.place && parent.type === 'main' && !spec.size) stars.setSize(parent.idx, Math.min(46, (parent.baseSize || (parent.baseSize = stars.size[parent.idx])) + Math.min(parent.kids.length, 24) * .8));
    if (at > (parent.latest || 0)) { parent.latest = at; if (loaded) settleBright(parent); }
  }
  bus.emit('graph:node', { node });
  return node;
}

/** The star (node) that holds a memory row, creating its topic/day parent as needed. */
export function addRow(row, { born = false, bulk = false, _noted = false } = {}) {
  if (!row || row.id == null) return { node: null, created: [], row };
  const id = +row.id;
  const had = graph.byRow.get(id);
  if (had) return { node: had, created: [], row };
  if (id > graph.maxRow) graph.maxRow = id;
  const prov = provisionalFor(row);
  if (prov) { bind(prov, row); return { node: prov, created: [], row, bound: true }; }
  const back = goneFor(row);
  if (back) { bind(back, row); return { node: back, created: [], row, rebound: true }; }
  const kind = row.kind || 'fact';
  if (kind === 'project' && born && !_noted) noteArrivals(topicLow(row.topic), 1);
  const created = [];
  const node = placeRow({ kind, text: row.text || '', topic: row.topic, at: row.at, id, key: 'row:' + id, born, bulk, created });
  return { node, created, row };
}
/** Build the star(s) for a memory row or a provisional birth. o = {kind, text, topic, at, id|null, key, born, bulk, created} */
function placeRow(o) {
  const { kind, text, born, created, key } = o;
  const rowId = o.id ?? null;
  const mk = s => { const n = createNode({ ...s, born }); if (n) created.push(n); return n; };
  if (kind === 'project') {
    const topic = o.topic || 'misc';
    let parent = graph.byKey.get('topic:' + topic);
    if (!parent) parent = mk({ key: 'topic:' + topic, type: 'main', kind: 'topic', cluster: clusterForTopic(topic, o), label: pretty(topic), text, at: o.at });
    if (!parent) return null;
    const gid = 'p:' + parent.key;
    // a bulk learn into a family lays its rows on a 16 sqrt(k) disc around the topic star (kept for good)
    if (!loading && !isSector(parent) && (o.bulk || topicCount(topicLow(topic)) >= 8)) { const g = grp(gid); if (g.d == null) { g.d = g.n; markSlots(); } }
    return mk({ key, type: 'frag', kind: 'project', cluster: parent.cluster, label: shortText(text, 8), text, rowId, at: o.at, parent, slotKey: rowKey('p', text, gid, rowId ?? key) });
  }
  if (kind === 'day') {
    const day = dayOf(o);
    let parent = graph.byKey.get('day:' + day);
    if (!parent) {
      const isNew = addKnownDay(day);
      parent = mk({ key: 'day:' + day, type: 'main', kind: 'day', cluster: 'diary', label: fmtDay(day), text: 'Diary · ' + day, at: o.at, day, size: 18, bright: 1 });
      if (parent && isNew && loaded) reflowDiary(parent);
    }
    if (!parent) return null;
    const gid = 'p:' + parent.key;
    return mk({ key, type: 'frag', kind: 'day', cluster: 'diary', label: shortText(text, 8), text, rowId, at: o.at, parent, bright: .5 + hash01(key) * .3, slotKey: rowKey('d', text, gid, rowId ?? key) });
  }
  if (kind === 'loop') return mk({ key, type: 'main', kind: 'loop', cluster: 'loops', label: shortText(text, 5), text, rowId, at: o.at, size: 22, slotKey: rowKey('l', text, 'c:loops', rowId ?? key) });
  return mk({ key, type: 'main', kind, cluster: 'you', label: shortText(text, 5), text, rowId, at: o.at, size: 22, slotKey: rowKey('f', text, 'c:you', rowId ?? key) });
}

/* ------------------------------------------------------------------ provisional births (X-9) */
const provByNorm = new Map();   // normalised text -> unbound provisional node
/** Where a new memory WOULD go: {cluster, pos, parentKey|null}. Creates nothing, takes no slot. */
export function slotFor({ kind = 'fact', text = '', topic = '', count = 0 } = {}) {
  kind = String(kind || 'fact');
  if (kind === 'project') {
    const tp = topic || 'misc', parent = graph.byKey.get('topic:' + tp);
    if (parent) {
      const gid = 'p:' + parent.key, j = peekSlot(gid, rowKey('p', text, gid, 'peek', false));
      const g = slots.g[gid], bulk = !isSector(parent) && (count >= 8 || topicCount(topicLow(tp)) >= 8) && (!g || g.d == null);
      return { cluster: parent.cluster, pos: bulk ? bulkDiscPos(parent, j) : fragPos(parent, j), parentKey: parent.key };
    }
    const t = topicLow(tp), fam = familyOf(t);
    let s = fam ? -1 : sectors[t];
    if (!fam && s == null) s = Math.max(count | 0, topicCount(t)) >= 8 ? freeSector(t) : -1;
    if (s >= 0) return { cluster: 'sec:' + t, pos: graph.clusters.get('sec:' + t)?.center.slice() || sectorCenter(t, s), parentKey: null, newTopic: true, sector: true };
    const cl = graph.clusters.get(fam || 'studio');
    return { cluster: cl.id, pos: mainPos(cl, peekSlot('c:' + cl.id, 'topic:' + tp)), parentKey: null, newTopic: true, sector: false };
  }
  if (kind === 'day') {
    const day = dayOf({ topic }), parent = graph.byKey.get('day:' + day);
    if (parent) { const gid = 'p:' + parent.key; return { cluster: 'diary', pos: pearlPos(parent, peekSlot(gid, rowKey('d', text, gid, 'peek', false))), parentKey: parent.key }; }
    let d = 0; for (const x of diary.desc) if (x > day) d++;
    return { cluster: 'diary', pos: diaryArm(Math.min(d, 29)), parentKey: null, newDay: true };
  }
  if (kind === 'skill') {
    const n = findSkill(String(text).toLowerCase().trim());
    if (n) return { cluster: n.cluster, pos: n.pos.slice(), parentKey: null };
    const cl = graph.clusters.get('skills');
    return { cluster: 'skills', pos: mainPos(cl, peekSlot('c:skills', 'skill:' + String(text).toLowerCase().trim())), parentKey: null };
  }
  const cid = kind === 'loop' ? 'loops' : 'you', gid = 'c:' + cid;
  return { cluster: cid, pos: mainPos(graph.clusters.get(cid), peekSlot(gid, rowKey(letterOf(kind), text, gid, 'peek', false))), parentKey: null };
}
/** Create the star for a memory that is being born right now, before its row exists. The row is bound to it on arrival. */
export function provisional({ kind = 'fact', text = '', topic = '', count = 0 } = {}) {
  kind = String(kind || 'fact');
  const nt = normText(text);
  const key = 'prov:' + thash(nt || 'empty:' + performance.now());
  const had = graph.byKey.get(key); if (had) return had;
  if (kind === 'skill') {
    const n = skillNode(text, { born: true });
    if (n) { n.born = true; graph.byKey.set(key, n); }
    return n;
  }
  if (kind === 'project' && count) hintTopic(topic || 'misc', count);
  const node = placeRow({ kind, text, topic, at: nowStamp(), id: null, key, born: true, created: [] });
  if (!node) return null;
  node.prov = true; node.born = true; node.norm = nt;
  if (nt) provByNorm.set(nt, node);
  return node;
}
function provisionalFor(row) {
  if (!provByNorm.size) return null;
  const nt = normText(row.text);
  if (!nt) return null;
  let n = provByNorm.get(nt);
  if (!n && nt.length >= 12) for (const [k, v] of provByNorm) if (k.length >= 12 && (nt.startsWith(k) || k.startsWith(nt))) { n = v; break; }
  return n && n.kind === (row.kind || 'fact') ? n : null;
}
/** Attach a row to a star that already stands: a provisional birth, or a star whose row came back under a new id. */
function bind(node, row) {
  const id = +row.id, oldKey = node.key, revived = !!node.goneSaid;
  if (node.rowId != null && graph.byRow.get(node.rowId) === node) graph.byRow.delete(node.rowId);
  if (oldKey.startsWith('row:') && graph.byKey.get(oldKey) === node) graph.byKey.delete(oldKey);   // that id may return as another memory
  node.rowId = id; node.prov = false; node.gone = false; node.goneSaid = false;
  if (row.at) { node.at = row.at; const ts = tsOf(row.at); if (ts > (node.latest || 0)) node.latest = ts; }
  if (row.text) { node.text = row.text; node.label = shortText(row.text, node.type === 'main' ? 5 : 8); }
  if (node.norm != null && provByNorm.get(node.norm) === node) provByNorm.delete(node.norm);
  node.key = 'row:' + id;
  graph.byKey.set(node.key, node);       // a 'prov:…' key stays as an alias
  graph.byRow.set(id, node);
  rekeySlot(node, row);
  // a star already reported gone (and maybe fallen) comes back to its own slot
  if (revived) { stars.setPos(node.idx, node.pos[0], node.pos[1], node.pos[2]); settleBright(node); }
  bus.emit('graph:bind', { node, row, key: oldKey, revived });
}
/**
 * After a bind, the slot the node holds must answer to the key its row asks for on the next load (see rowKey).
 * The key is RENAMED, never aliased: loadStores keeps one key per slot number, so a second key on the same slot
 * would be dropped on reload and the row would get a fresh slot (the star would jump).
 */
function rekeySlot(node, row) {
  const g = node.gid ? slots.g[node.gid] : null;
  if (!g || node.slot < 0) return;
  const id = +row.id, nt = normText(row.text), base = nt ? letterOf(node.kind) + '|' + thash(nt) : 'id|' + id;
  let u = inUse.get(node.gid); if (!u) inUse.set(node.gid, u = new Map());
  let key = node.slotKey;
  if (key === base) {
    if (g.o && g.o[base] != null && g.o[base] !== id) { g.o[base] = id; markSlots(); }   // the twin record names this row now
  } else if (key !== base + '#' + id) {
    const nk = g.k[base] == null ? base : base + '#' + id;   // the plain key if free, else the row's own twin key
    if (g.k[key] === node.slot) delete g.k[key];
    g.k[nk] = node.slot; u.delete(key); key = node.slotKey = nk; markSlots();
  }
  u.set(key, id);
}

/* ------------------------------------------------------------------ forgotten rows */
// When pollNew finds rows were deleted, the stars that lost their row are released: out of byRow (and byKey),
// node.gone = true, their slot key free for this session. A row re-added with the same text in the same group
// within 60 s -- `nova_memory.py import` deletes and re-adds every project row under new ids -- is bound back to
// its old star instead of being born again, so a re-index moves nothing and spawns nothing.
const goneIdx = new Map();   // group \n text key -> [released nodes], oldest first
let goneAt = 0, goneLater = [];   // goneLater: released while a re-index was landing, reported on the next poll
const goneKey = (gid, kind, text) => { const nt = normText(text); return gid && nt ? gid + '\n' + letterOf(kind) + '|' + thash(nt) : ''; };
function groupOfRow(row, kind) {
  if (kind === 'project') { const p = graph.byKey.get('topic:' + (row.topic || 'misc')); return p ? 'p:' + p.key : ''; }
  if (kind === 'day') return 'p:day:' + dayOf(row);
  return kind === 'loop' ? 'c:loops' : 'c:you';
}
function release(n) {
  if (graph.byRow.get(n.rowId) === n) graph.byRow.delete(n.rowId);
  if (graph.byKey.get(n.key) === n) graph.byKey.delete(n.key);
  const u = inUse.get(n.gid); if (u && u.get(n.slotKey) === n.rowId) u.delete(n.slotKey);
  n.gone = true;
  const k = goneKey(n.gid, n.kind, n.text); if (!k) return;
  const l = goneIdx.get(k); if (l) l.push(n); else goneIdx.set(k, [n]);
}
function goneFor(row, take = true) {
  if (!goneIdx.size) return null;
  if (performance.now() - goneAt > 60000) { goneIdx.clear(); return null; }
  const kind = row.kind || 'fact', k = goneKey(groupOfRow(row, kind), kind, row.text), l = k ? goneIdx.get(k) : null;
  if (!l) return null;
  const n = take ? l.shift() : l[0];
  if (take && !l.length) goneIdx.delete(k);
  return n && n.gone && n.kind === kind ? n : null;
}

/* ------------------------------------------------------------------ places she touches (C-6.5) */
let placeN = 0;
const r2 = v => Math.round(v * 100) / 100;
function farEnough(p, min) {
  const P = stars.pos, n = stars.count, m2 = min * min;
  for (let i = 0; i < n; i++) {
    const dx = P[i * 3] - p[0]; if (dx > min || dx < -min) continue;
    const dy = P[i * 3 + 1] - p[1], dz = P[i * 3 + 2] - p[2];
    if (dx * dx + dy * dy + dz * dz < m2) return false;
  }
  return true;
}
/** First candidate at least 14 u from every star (later attempts reach a little further out). */
function spaced(fn) {
  let p = null;
  for (let a = 0; a < 10; a++) { p = fn(a); if (farEnough(p, 14)) break; }
  return [r2(p[0]), r2(p[1]), r2(p[2])];
}
/** A child folder/file: parent + dir * (34 + 18h), dir away from the hole, jittered +-35 deg / +-20 deg. */
function childPos(parent, h, key, a) {
  const p = parent.pos, base = Math.atan2(p[2], p[0]);
  const az = base + (hash01(key + '|az' + a) - .5) * 70 * DEG, el = (hash01(key + '|el' + a) - .5) * 40 * DEG;
  const d = 34 + 18 * h + 7 * a;
  return [p[0] + Math.cos(el) * Math.cos(az) * d, p[1] + Math.sin(el) * d, p[2] + Math.cos(el) * Math.sin(az) * d];
}
/** A keyword root (…/Orbit, ~/.nova) on the outer rim of its memory cluster. */
function anchorPos(cl, k, a) {
  const s = k === 0 ? 0 : (k % 2 ? 1 : -1) * Math.ceil(k / 2);
  const th = cl.a * DEG + (s * 14 + a * 5) * DEG, r = cl.r + 165 + 6 * (k % 3) + 9 * a;
  return [Math.cos(th) * r, cl.center[1] + 45 + 12 * ((k >> 1) % 3), Math.sin(th) * r];
}
function hostPos(key, a) {
  const th = (308 + (hash01(key + '|th' + a) - .5) * 36) * DEG, r = 1350 + hash01(key + '|r' + a) * 200;
  return [Math.cos(th) * r, -150 + (hash01(key + '|y' + a) - .5) * 120, Math.sin(th) * r];
}
function satellitePos(parent, key, a) {
  const th = hash01(key + '|u' + a) * TAU, z = 2 * hash01(key + '|v' + a) - 1, s = Math.sqrt(1 - z * z), d = 22 + 18 * hash01(key + '|w' + a);
  const p = parent.pos;
  return [p[0] + s * Math.cos(th) * d, p[1] + z * d * .7, p[2] + s * Math.sin(th) * d];
}
/** Actions: 16 slots on a 90 u ring around the anchor; more rings outward. */
function ringPos(cl, k) {
  const ring = Math.floor(k / 16), i = k % 16;
  return discPoint(cl.center, cl.axes, 90 + 34 * ring, (i + (ring % 2) * .5) * TAU / 16);
}
function placeSize(kind, isRoot) {
  switch (kind) {
    case 'folder': return isRoot ? 15 : 10.5;
    case 'file': return 9.5;
    case 'site': return 15;
    case 'page': return 9;
    case 'tool': return 14;
    case 'history': return 16;
    case 'skill': return 24;
    default: return isRoot ? 20 : 10;
  }
}
function touchPlace(node) {
  const now = Date.now();
  node.uses = (node.uses | 0) + 1;
  for (let n = node; n && n.dynamic; n = n.parent) {
    if (now > (n.last || 0)) n.last = now;
    if (n.dimmed) { n.dimmed = false; settleBright(n); }
  }
  markPlaces();
}
function bumpLast(node, ts) { for (let n = node; n && n.dynamic; n = n.parent) if (ts > (n.last || 0)) n.last = ts; }
function applyStale() {
  const now = Date.now();
  for (const n of graph.nodes) if (n.place && n.last && !n.dimmed && now - n.last > STALE_MS) { n.dimmed = true; settleBright(n); }
}
/**
 * A place outside memory that she touched: a file, a folder, a site, a page, a tool, past conversations.
 * spec = {key, kind, cluster, label, text, parentKey?, pos?}. An existing key counts as a use (touch).
 */
export function ensure(spec, { born = true, persist = true, touch = true, last = 0 } = {}) {
  const had = graph.byKey.get(spec.key);
  if (had) { if (touch) touchPlace(had); else if (last) bumpLast(had, last); return had; }
  const parent = spec.parentKey ? graph.byKey.get(spec.parentKey) || null : null;
  const kind = spec.kind || '', isPlace = PLACE_KINDS.has(kind);
  const node = createNode({ ...spec, type: parent ? 'frag' : 'main', parent, born, dynamic: true, place: isPlace,
    size: spec.size ?? placeSize(kind, !parent), bright: spec.bright ?? (isPlace ? .95 : undefined) });
  if (!node) return null;
  node.place = isPlace; node.persist = persist;
  node.depth = spec.depth ?? (parent ? (parent.depth | 0) + 1 : 0);
  node.uses = (spec.uses | 0) + (touch ? 1 : 0);
  node.last = touch ? Date.now() : (last || 0);
  if (isPlace && persist) placeN++;
  if (isPlace && node.last && Date.now() - node.last > STALE_MS) { node.dimmed = true; settleBright(node); }
  if (persist) markPlaces();
  return node;
}

/** C:\Users\You\X, c:/users/pc/x, /c/Users/pc/x -> ~/X. Slashes forward, home as ~, no trailing slash. */
export function normPath(p) {
  let s = String(p ?? '').trim().replace(/^["']+|["']+$/g, '').replace(/\\/g, '/');
  if (!s) return '';
  let m;
  if ((m = s.match(/^\/mnt\/([a-zA-Z])(?=\/|$)/)) || (m = s.match(/^\/([a-zA-Z])(?=\/|$)/))) s = m[1] + ':' + s.slice(m[0].length);
  s = s.slice(0, 1) + s.slice(1).replace(/\/{2,}/g, '/');
  if (/^[a-z]:/.test(s)) s = s[0].toUpperCase() + s.slice(1);
  if (/^[A-Z]:$/.test(s)) s += '/';
  s = s.replace(/^[A-Z]:\/users\/(?!(?:public|default|default user|all users)(?:\/|$))[^/]+(?=\/|$)/i, '~');
  if (s.length > 1 && s.endsWith('/') && !/^[A-Z]:\/$/.test(s)) s = s.slice(0, -1);
  return s;
}
function splitPath(s) {
  let root, rest;
  if (s.startsWith('//')) { root = (s.match(/^\/\/[^/]+/) || ['//'])[0]; rest = s.slice(root.length); }
  else if (/^[A-Z]:/.test(s)) { root = s.slice(0, 2); rest = s.slice(2); }
  else if (s[0] === '~') { root = '~'; rest = s.slice(1); }
  else if (s[0] === '/') { root = '/'; rest = s; }
  else { root = '.'; rest = s; }
  return { root, segs: rest.split('/').filter(Boolean) };
}
const joinPath = (root, segs, n) => !n ? root : root === '/' ? '/' + segs.slice(0, n).join('/') : root === '.' ? segs.slice(0, n).join('/') : root + '/' + segs.slice(0, n).join('/');
const ANCHORS = [['orbit', /orbit/], ['tpn', /tpn/], ['nova', /^\.?nova/], ['mc', /mcserver|minecraft|cobblemon/], ['roblox', /roblox|spellbook|nopixel|unoryx/], ['school', /school|midterm/]];
function anchorOf(segs) {
  for (let i = 0; i < segs.length; i++) for (const [c, re] of ANCHORS) if (re.test(segs[i].toLowerCase())) return { cluster: c, at: i };
  return null;
}
/** The chain anchor -> … -> leaf for a path; every link is a place (square). */
function pathNode(path, isFile, { born = true, touch = true, last = 0 } = {}) {
  const norm = normPath(path); if (!norm) return null;
  const { root, segs } = splitPath(norm);
  if (!segs.length) isFile = false;
  const an = anchorOf(segs);
  const start = an ? an.at + 1 : 0, cluster = an ? an.cluster : 'files';
  const stamp = touch ? Date.now() : last;
  let parent = null, node = null;
  for (let i = start; i <= segs.length; i++) {
    const display = joinPath(root, segs, i), leaf = i === segs.length, file = leaf && isFile;
    const key = (file ? 'file:' : 'dir:') + display.toLowerCase();
    node = graph.byKey.get(key) || ensure({ key, kind: file ? 'file' : 'folder', cluster,
      label: i === 0 ? (root === '~' ? 'Home' : root) : segs[i - 1], text: display, parentKey: parent ? parent.key : undefined, depth: i - start },
      { born, persist: true, touch: false, last: stamp });
    if (!node) return null;
    parent = node;
  }
  if (touch) touchPlace(node); else if (last) bumpLast(node, last);
  return node;
}
export function fileNode(path, opts) { return pathNode(path, true, opts); }
export function folderNode(path, opts) { return pathNode(path, false, opts); }
function splitUrl(s) {
  let u = String(s || '').trim();
  if (!u || /\s/.test(u)) return null;
  if (!u.includes('://')) u = 'https://' + u;
  try {
    const x = new URL(u), host = x.hostname.toLowerCase().replace(/^www\./, '');
    if (!/^https?:$/.test(x.protocol) || !host || (!host.includes('.') && host !== 'localhost')) return null;
    return { host, path: x.pathname.replace(/\/+$/, '') };
  } catch { return null; }
}
const midCut = (s, n) => s.length <= n ? s : s.slice(0, Math.ceil(n / 2) - 1) + '…' + s.slice(s.length - Math.floor(n / 2) + 1);
/** A site (ring) or a page orbiting it; isQuery: a web search, orbiting the Search node. */
export function webNode(urlOrQuery, isQuery = false, opts = {}) {
  const o = { born: opts.born ?? true, touch: opts.touch ?? true, last: opts.last || 0 };
  const sub = { born: o.born, persist: true, touch: false, last: o.touch ? Date.now() : o.last };
  const u = isQuery ? null : splitUrl(urlOrQuery);
  let node;
  if (u) {
    const site = ensure({ key: 'site:' + u.host, kind: 'site', cluster: 'web', label: u.host, text: u.host }, sub);
    node = !u.path || !site ? site : ensure({ key: 'page:' + (u.host + u.path).toLowerCase(), kind: 'page', cluster: 'web', label: midCut(u.host + u.path, 36), text: u.host + u.path, parentKey: site.key }, sub);
  } else {
    const q = String(urlOrQuery || '').replace(/\s+/g, ' ').trim();
    if (!q) return null;
    ensure({ key: 'site:search', kind: 'site', cluster: 'web', label: 'Search', text: 'Web searches' }, sub);
    node = ensure({ key: 'page:q:' + q.toLowerCase().slice(0, 120), kind: 'page', cluster: 'web', label: '“' + shortText(q, 6) + '”', text: q, parentKey: 'site:search' }, sub);
  }
  if (node) { if (o.touch) touchPlace(node); else if (o.last) bumpLast(node, o.last); }
  return node;
}
export function toolNode(name, label, opts) {
  const n = String(name || '').trim();
  return n ? ensure({ key: 'tool:' + n, kind: 'tool', cluster: 'hands', label: label || pretty(n), text: n }, opts) : null;
}
export function historyNode() {
  return ensure({ key: 'history', kind: 'history', cluster: 'diary', label: 'Past conversations', text: 'Every exchange with Uno, word for word' }, { persist: false, born: false });
}
function findSkill(k) {
  if (!k) return null;
  let n = graph.byKey.get('skill:' + k);
  if (!n) for (const [key, node] of graph.byKey) if (key.startsWith('skill:') && (key.includes(k) || node.label.toLowerCase().includes(k))) { n = node; break; }
  return n || null;
}
export function skillNode(name, opts) {
  const k = String(name || '').toLowerCase().trim();
  if (!k) return null;
  return findSkill(k) || ensure({ key: 'skill:' + k, kind: 'skill', cluster: 'skills', label: pretty(k), text: 'Skill: ' + name }, opts);
}
function trailKey(it) {
  const t = String(it.target || '');
  if (it.kind === 'file' || it.kind === 'folder') { const p = normPath(t); return p ? (it.kind === 'file' ? 'file:' : 'dir:') + p.toLowerCase() : ''; }
  if (it.kind === 'site' || it.kind === 'page') { const u = splitUrl(t); return u ? (u.path && it.kind === 'page' ? 'page:' + (u.host + u.path).toLowerCase() : 'site:' + u.host) : ''; }
  if (it.kind === 'tool') return 'tool:' + t.trim();
  return '';
}
/**
 * Merge where she has been (GET /api/brain/trail: [{kind, target, uses, last}] or {items:[…]}) into the places.
 * Newest first; past PLACE_CAP only places already on the map are updated. Returns how many were created.
 */
export function seedPlaces(items) {
  const list = (Array.isArray(items) ? items : Array.isArray(items?.items) ? items.items : [])
    .filter(it => it && typeof it.kind === 'string' && typeof it.target === 'string' && it.target.trim())
    .map(it => ({ it, ts: tsOf(it.last) })).sort((a, b) => (b.ts - a.ts) || ((b.it.uses | 0) - (a.it.uses | 0)));
  let made = 0;
  for (const { it, ts } of list) {
    const key = trailKey(it); if (!key) continue;
    let node = graph.byKey.get(key);
    if (!node) {
      if (placeN >= PLACE_CAP) continue;
      const o = { born: false, touch: false, last: ts };
      if (it.kind === 'file') node = fileNode(it.target, o);
      else if (it.kind === 'folder') node = folderNode(it.target, o);
      else if (it.kind === 'site' || it.kind === 'page') node = webNode(it.target, false, o);
      else if (it.kind === 'tool') node = toolNode(it.target, null, o);
      if (!node) continue;
      made++;
    }
    node.uses = Math.max(node.uses | 0, it.uses | 0);
    if (ts) bumpLast(node, ts);
  }
  applyStale();
  if (list.length) markPlaces();
  return made;
}
function savePlaces() {
  dirty.places = false;
  let list = graph.nodes.filter(n => n.dynamic && n.persist !== false);
  if (list.length > PLACE_CAP) {
    const keep = new Set(list.slice().sort((a, b) => ((b.last || 0) - (a.last || 0)) || ((a.depth | 0) - (b.depth | 0))).slice(0, PLACE_CAP));
    list = list.filter(n => keep.has(n));
  }
  const recs = list.map(n => ({ k: n.key, kind: n.kind, c: n.cluster, l: n.label, t: String(n.text || '').slice(0, 300), p: n.parent ? n.parent.key : null,
    pos: [r2(n.pos[0]), r2(n.pos[1]), r2(n.pos[2])], u: n.uses | 0, at: Math.round(n.last || 0), d: n.depth | 0 }));
  const cur = readJSON(K_PLACES);   // keep what another tab added that this one never loaded
  if (cur && Array.isArray(cur.list)) for (const r of cur.list) if (recs.length < PLACE_CAP && r && typeof r.k === 'string' && !graph.byKey.has(r.k)) recs.push(r);
  writeJSON(K_PLACES, { v: 1, list: recs });
}
function restorePlaces() {
  const s = readJSON(K_PLACES);
  if (!s || !Array.isArray(s.list)) { migrateOld(); return; }
  let recs = s.list.filter(r => r && typeof r.k === 'string' && typeof r.kind === 'string' && Array.isArray(r.pos) && r.pos.length === 3 && r.pos.every(Number.isFinite));
  if (recs.length > PLACE_CAP) recs = recs.slice().sort((a, b) => (+b.at || 0) - (+a.at || 0)).slice(0, PLACE_CAP);
  recs = recs.map((r, i) => [r, i]).sort((a, b) => ((a[0].d | 0) - (b[0].d | 0)) || (a[1] - b[1])).map(x => x[0]);   // parents first
  for (const r of recs) {
    if (graph.byKey.has(r.k)) continue;
    ensure({ key: r.k, kind: r.kind, cluster: r.c, label: r.l, text: r.t, parentKey: r.p && graph.byKey.has(r.p) ? r.p : undefined, pos: r.pos, depth: r.d | 0, uses: r.u | 0 },
      { born: false, persist: true, touch: false, last: +r.at || 0 });
  }
}
/** Places saved by the old layout (novaBrain.dyn.v1) are re-made through today's rules, once. */
function migrateOld() {
  const old = readJSON(K_OLD);
  if (!Array.isArray(old)) return;
  const o = { born: false, touch: false, last: 0 };
  for (const s of old) {
    if (!s || typeof s.key !== 'string') continue;
    try {
      if (s.kind === 'file') fileNode(s.text, o);
      else if (s.kind === 'folder') folderNode(s.text, o);
      else if (s.kind === 'site') webNode(s.text, false, o);
      else if (s.kind === 'page') webNode(s.text, s.key.startsWith('page:q:'), o);
      else if (s.kind === 'tool') toolNode(s.text, s.label, o);
      else if (s.kind === 'skill') skillNode(String(s.text || '').replace(/^Skill:\s*/, ''), o);
    } catch { /* one bad record never blocks the rest */ }
  }
  if (WRITE_STORE) { try { localStorage.removeItem(K_OLD); } catch { /* blocked */ } }
  markPlaces();
}
function fetchTrail() {
  if (!READ_STORE) return Promise.resolve([]);
  return Promise.race([
    api('/api/brain/trail?days=30').then(t => Array.isArray(t) ? t : Array.isArray(t?.items) ? t.items : []),
    new Promise(res => setTimeout(() => res([]), 2500)),
  ]).catch(() => []);
}

/* ------------------------------------------------------------------ queries */
export const nodeOf = idx => graph.starNode[idx] || null;
export function starsOfRows(ids) { const out = []; for (const id of ids || []) { const n = graph.byRow.get(+id); if (n) out.push(n.idx); } return out; }
/** Stars related to a star: parent, children, and anything a constellation line touches. */
export function neighbours(idx) {
  const n = nodeOf(idx), out = new Set();
  if (!n) return [];
  if (n.parent) out.add(n.parent.idx);
  for (const k of n.kids) out.add(k.idx);
  for (const e of edges.of(idx)) { const [a, b] = edges.ends(e); out.add(a === idx ? b : a); }
  out.delete(idx); return [...out];
}
/** Cheap local text search (fallback when the bridge is unreachable). */
export function searchLocal(query, limit = 8) {
  const terms = String(query || '').toLowerCase().match(/[a-z0-9][a-z0-9'_-]{2,}/g) || [];
  if (!terms.length) return [];
  const scored = [];
  for (const n of graph.nodes) {
    if (n.gone) continue;
    const hay = (n.label + ' ' + n.text).toLowerCase();
    let s = 0; for (const t of terms) if (hay.includes(t.slice(0, 6))) s++;
    if (s >= Math.max(1, Math.ceil(terms.length * .6))) scored.push([s, n.idx]);
  }
  return scored.sort((a, b) => b[0] - a[0]).slice(0, limit).map(x => x[1]);
}
export async function matchRows(query) {
  try { return starsOfRows((await api('/api/brain/match?q=' + encodeURIComponent(String(query).slice(0, 300)))).ids); }
  catch { return searchLocal(query); }
}

/* ------------------------------------------------------------------ labels */
let labelsOn = false;
function addClusterLabel(cl) {
  if (cl.labelEl) return;
  const el = document.createElement('div'); el.className = 'cl'; el.dataset.cluster = cl.id;
  const b = document.createElement('b'), span = document.createElement('span');
  b.textContent = cl.label; el.append(b, span);
  cl.labelEl = el; cl.countEl = span;
  cl.handle = overlay.add(el, { pos: () => labelPos(cl), near: 220, opacity: () => (state.introDone ? 1 : 0) * (cl.mains.length ? 1 : 0) });
}
const lp = [0, 0, 0];
function labelPos(cl) {
  if (!cl.mains.length) { lp[0] = cl.center[0]; lp[1] = cl.center[1] + 110; lp[2] = cl.center[2]; return lp; }
  let x = 0, y = -1e9, z = 0; const P = stars.pos;
  for (const i of cl.mains) { x += P[i*3]; z += P[i*3+2]; y = Math.max(y, P[i*3+1]); }
  lp[0] = x / cl.mains.length; lp[1] = y + 70; lp[2] = z / cl.mains.length; return lp;
}
export function refreshCounts() {
  for (const cl of graph.clusters.values()) {
    if (!cl.countEl) continue;
    let n = 0;
    for (const i of cl.mains) { const nd = graph.starNode[i]; if (!nd || nd.dynamic || nd.gone) continue; n++; for (const k of nd.kids) if (!k.dynamic && !k.gone) n++; }
    const txt = n ? n + (n === 1 ? ' memory' : ' memories') : '';
    if (cl.countEl.textContent !== txt) cl.countEl.textContent = txt;
  }
}

/* ------------------------------------------------------------------ loading */
let frameOn = false;
export async function load() {
  loadStores();
  const trail = fetchTrail();                         // in parallel; awaited once the memories stand
  const d = await api('/api/brain?since=0');
  graph.total = d.count; graph.skills = d.skills || []; graph.profile = d.profile || '';
  const rows = (d.rows || []).slice().sort((a, b) => a.id - b.id);
  // every day this browser has seen counts for the ranks, so a deleted day leaves its notch empty
  for (const day of slots.days) diary.set.add(day);
  for (const r of rows) if (r.kind === 'day') { const day = dayOf(r); if (!diary.set.has(day)) { diary.set.add(day); slots.days.push(day); markSlots(); } }
  diary.desc = [...diary.set].sort().reverse();
  loadCounts = new Map();
  for (const r of rows) if (r.kind === 'project') { const t = topicLow(r.topic); loadCounts.set(t, (loadCounts.get(t) || 0) + 1); }
  loadIds = new Set(rows.map(r => +r.id));
  loading = true;
  // You: profile sections first (stable), then facts
  const secs = String(graph.profile).split(/\n(?=#{1,3}\s)/).map(s => s.trim()).filter(s => s.length > 20);
  secs.forEach((s, i) => {
    const title = (s.match(/^#{1,3}\s+(.+)/) || [, 'Profile'])[1].trim();
    createNode({ key: 'profile:' + i, type: 'main', kind: 'profile', cluster: 'you', label: title, text: s.replace(/^#{1,3}\s+.+\n?/, '').trim(), size: 24 });
  });
  for (const r of rows) addRow(r);
  for (const s of graph.skills) {
    const name = (s.text.match(/^name:\s*(.+)$/m) || [, pretty(s.name)])[1].trim();
    const when = (s.text.match(/^when:\s*(.+)$/m) || [, ''])[1].trim();
    createNode({ key: 'skill:' + s.name.toLowerCase(), type: 'main', kind: 'skill', cluster: 'skills', label: name, text: when || s.text, size: 24 });
  }
  loading = false; loadCounts = null; loadIds = null;
  for (const n of graph.nodes) if (n.kids.length && n.latest) settleBright(n);
  historyNode();
  restorePlaces();
  seedPlaces(await trail);
  loaded = true;
  for (const cl of graph.clusters.values()) addClusterLabel(cl);
  labelsOn = true;
  refreshCounts();
  if (!frameOn) { frameOn = true; onFrame(glideFrame, -50); }
  bus.emit('graph:loaded', { graph });
  return graph;
}

/**
 * Fetch rows newer than what we have. Returns {added:[{node, created, row, bound?}], forgotten:boolean, gone:[node]}.
 * forgotten: fewer rows than we had plus the new ones. Then one full read finds which stars lost their row: they are
 * released (node.gone, out of byRow) and listed in `gone` + bus 'graph:gone' so they can fall -- except the ones a
 * re-added row with the same text binds back to (a re-index): those stay put and are in neither list.
 */
export async function pollNew() {
  const before = graph.total;
  let d = await api('/api/brain?since=' + graph.maxRow);
  let rows = (d.rows || []).slice().sort((a, b) => a.id - b.id);
  const forgotten = d.count < before + rows.length;
  let gone = [];
  if (forgotten) {
    try {
      const all = await api('/api/brain?since=0');
      const live = new Set((all.rows || []).map(r => +r.id));
      gone = [...graph.byRow.values()].filter(n => !live.has(n.rowId));
      for (const n of gone) release(n);
      if (gone.length) goneAt = performance.now();
      d = all;
      rows = (all.rows || []).filter(r => r && r.id != null && !graph.byRow.has(+r.id)).sort((a, b) => a.id - b.id);
    } catch { /* bridge busy: the new rows still arrive, nothing is released */ }
  }
  const per = new Map();
  for (const r of rows) if (r.kind === 'project' && !goneFor(r, false)) { const t = topicLow(r.topic); per.set(t, (per.get(t) || 0) + 1); }
  for (const [t, n] of per) noteArrivals(t, n);      // the whole batch counts before its first row is placed
  const added = []; let rebound = 0;
  for (const r of rows) { const x = addRow(r, { born: true, _noted: true }); if (x.rebound) rebound++; else if (x.created.length || x.bound) added.push(x); }
  graph.total = d.count;
  // stars whose row is still missing are reported now -- or, while a re-index is landing (rows just came back),
  // on the next poll, so a re-index split across two polls never drops stars that come straight back
  let said = goneLater.filter(n => n.gone); goneLater = [];
  const fresh = gone.filter(n => n.gone);
  if (rebound) goneLater = fresh; else said = said.concat(fresh);
  for (const n of said) n.goneSaid = true;
  if (said.length) bus.emit('graph:gone', { nodes: said });
  if (added.length || said.length || rebound) refreshCounts();
  return { added, forgotten, gone: said };
}

/** Test/debug: every node's live star position by key. */
export function snapshot() {
  const out = {}, P = stars.pos;
  for (const n of graph.nodes) out[n.key] = [P[n.idx * 3], P[n.idx * 3 + 1], P[n.idx * 3 + 2]];
  return out;
}

window.__brain = Object.assign(window.__brain || {}, { graph, data: { addRow, ensure, createNode, fileNode, folderNode, webNode, toolNode, skillNode, historyNode,
  starsOfRows, neighbours, nodeOf, matchRows, pollNew, slotFor, provisional, diaryArm, normPath, normText, seedPlaces, hintTopic, clusterOfTopic, snapshot } });
