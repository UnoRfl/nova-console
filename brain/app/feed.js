// Nova Brain -- feed: what Nova is doing, as beats on the bus (SPEC 4.4 F-1..F-3, CONTRACT.md "Normalized events v2").
//
//   transport  F-1  embedded: the HUD is the only poller and relays raw events ('host:evt' {e}) and orb frames
//                   ('host:orb' {o}); a watchdog self-polls when the orb is busy and nothing came for 15 s.
//                   Standalone: one long-poll on /api/activity (fast-forwarded at boot, never replayed) plus
//                   /api/orb?panel=1 every 100 ms while active, 1 s idle. Hidden: beats wait; on show, late beats
//                   jump to their end state (payload.jump) and stale ones are dropped.
//   conductor  F-2  each event plays at at + skew + 60 ms (skew = min(now - at) over the last 16 events);
//                   a pool of 64 beats; launches >= 140 ms apart, tools within 50 ms (or queued behind a launch)
//                   share one fan-out of <= 4 legs 90 ms apart; > 500 ms behind folds same-region tools;
//                   never faster than x1.25; > 3 s behind jumps to end states. Turn log in localStorage.
//   resolver   F-3  tool -> region/targets/glyph/verb/object through data.*; outputs -> urls, path:line hits,
//                   dates and the memory rows a recall really returned (step:done.results.rowIds).
//
// Extra payload fields beyond the contract: every beat may carry `late` (ms behind real time) and `jump`
// (true: show the end state, no flight); replays carry `replay: true`. Nothing here is memory text: all text
// shown comes from the token-gated API at runtime.

import * as core from './core.js';
import * as data from './data.js';

const { bus, state, params, api, DEMO, mulberry32 } = core;
// ?selfpoll: embedded, but poll the bridge ourselves (the panel only shows/hides us)
const EMBED = core.EMBED && !params.has('selfpoll');
const graph = data.graph;

/* ================================================================== constants */
const DELAY = 60;                 // ms after the bridge stamp an event plays
const SKEW_N = 16, SKEW_JUMP = 2000;
const POOL = 64;                  // beats in flight
const LAUNCH_GAP = 140, FAN_GAP = 90, FAN_MAX = 4, PAR_MS = 50, JOIN_MS = 200;
const LAG_FOLD = 500, LAG_JUMP = 3000, FOLD_WIN = 1500, MAX_SPEED = 1.25;
const READY_WAIT = 160;           // ms a beat may wait past its time for its prefetch (match / new rows)
const STALE_DROP = 120000;        // hidden this long: a beat's visuals have faded, drop it (learns are kept)
const WATCHDOG_MS = 15000, ORB_ACTIVE_MS = 100, ORB_IDLE_MS = 1000;
const POLL_IDLE = 15000, POLL_TURN = 3000;
const END_SPOKE = 1200, END_SILENT = 4000;
const HOLD_REUSE = 30000;
const GEN_MIN = 8, GEN_WIN = 4000, GEN_END = 3000, GEN_MAX = 60000;
const LOG_KEY = 'novaBrain.turns.v1', LOG_TURNS = 20, LOG_BEATS = 64, LOG_BYTES = 40960;
const DETERMINISTIC = params.has('replay') || params.has('bench');
const LIVE_DEFAULT = !(DEMO || DETERMINISTIC);
const STORE = !DEMO && !DETERMINISTIC;
const nowE = () => Date.now();

/* ================================================================== small helpers */
const clamp = (x, a, b) => x < a ? a : x > b ? b : x;
const arr = v => Array.isArray(v) ? v : [];
const str = v => v == null ? '' : String(v);
/** 'mcp__nova__recall' -> 'recall'; 'Read' -> 'Read'. */
export function shortName(n) { const s = str(n).trim(); const i = s.lastIndexOf('__'); return i >= 0 ? s.slice(i + 2) : s; }
/** nova_skills.slug(): a skill's file stem. */
function slug(name) { return str(name).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60) || 'skill'; }
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
function fmtDay(d) { const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(str(d)); return m ? MON[+m[2] - 1] + ' ' + (+m[3]) : str(d); }
function todayStr() { const d = new Date(); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
function baseName(p) { const s = str(p).replace(/[\\/]+$/, ''); const i = Math.max(s.lastIndexOf('/'), s.lastIndexOf('\\')); return i >= 0 ? s.slice(i + 1) : s; }
function cut(s, n) { s = str(s).replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n - 1).trimEnd() + '…' : s; }
function hostOf(u) {
  let s = str(u).trim(); if (!s || /\s/.test(s)) return '';
  if (!s.includes('://')) s = 'https://' + s;
  try { const x = new URL(s); return /^https?:$/.test(x.protocol) ? x.hostname.toLowerCase().replace(/^www\./, '') : ''; } catch { return ''; }
}
function hash32(s) { s = str(s); let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }
const posOf = idx => idx >= 0 ? [core.stars.pos[idx * 3], core.stars.pos[idx * 3 + 1], core.stars.pos[idx * 3 + 2]] : null;
const clusterCenter = id => { const c = graph.clusters.get(id); return c ? c.center.slice() : null; };
const hiddenNow = () => document.hidden || !state.visible || state.paused;

/** A tool's input preview (JSON with indent, Bash: the bare command; maybe cut short) as an object. */
export function parseArgs(input, short = '') {
  if (input && typeof input === 'object') return input;
  const s = str(input);
  if (!s) return {};
  try { const v = JSON.parse(s); if (v && typeof v === 'object' && !Array.isArray(v)) return v; } catch { /* cut short or a bare command */ }
  if (short === 'Bash' || short === 'PowerShell') return { command: s.replace(/\n\.\.\. \(\d+ more characters\)\s*$/, '') };
  const out = {}, re = /"([A-Za-z_]\w*)"\s*:\s*("(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?|true|false|null)/g;
  let m;
  while ((m = re.exec(s))) if (!(m[1] in out)) { try { out[m[1]] = JSON.parse(m[2]); } catch { /* skip */ } }
  if (/"urls"\s*:/.test(s) && !out.urls) out.urls = (s.match(/https?:\/\/[^"\\\s]+/g) || []).slice(0, 8);
  return out;
}

/** The program a shell command runs: 'cd x && npm run build' -> 'npm'. */
export function firstWord(cmd) {
  const segs = str(cmd).split(/&&|\|\||;|\||\n/);
  let fallback = '';
  for (let seg of segs) {
    const words = seg.trim().replace(/^[({]+\s*/, '').split(/\s+/).filter(Boolean);
    while (words.length && /^[A-Za-z_][A-Za-z0-9_]*=/.test(words[0])) words.shift();
    if (!words.length) continue;
    let w = words[0].replace(/^["'&]+|["']+$/g, '');
    w = w.split(/[\\/]/).pop().replace(/\.(exe|cmd|bat|ps1)$/i, '');
    if (!w) continue;
    if (/^(cd|pushd|popd|set|export|source|\.|echo|sleep|true|false|clear)$/i.test(w)) { fallback = fallback || w; continue; }
    return w.slice(0, 24);
  }
  return fallback.slice(0, 24);
}

/* ================================================================== resolver (F-3): description, no side effects */
const SKIP = new Set(['ToolSearch', 'TodoWrite', 'TodoRead', 'ExitPlanMode', 'EnterPlanMode']);
const FILE_VERB = { Read: 'Opening', NotebookRead: 'Opening', Edit: 'Editing', MultiEdit: 'Editing', NotebookEdit: 'Editing', Write: 'Writing' };
const FOLDER_TOOLS = new Set(['Grep', 'Glob', 'LS']);
const WEB_SEARCH = new Set(['WebSearch', 'web_search', 'web_find', 'web_research']);
const WEB_OPEN = new Set(['WebFetch', 'web_open', 'web_text', 'watch_page', 'web_extract']);
const WEB_PAGE = { web_read: 'Reading', web_scroll: 'Reading', web_back: 'Reading', web_tabs: 'Reading', web_show: 'Reading',
  web_screenshot: 'Reading', web_keys: 'Typing', web_type: 'Typing', web_click: 'Clicking' };
const MAIL_READ = new Set(['gmail_inbox', 'gmail_read']);
const MESSAGES = new Set(['messenger_chats', 'messenger_read', 'whatsapp_chats', 'whatsapp_read']);
const EYES = { capture_screen: 'Looking at screen', look_at_this: 'Looking at screen', read_screen: 'Looking at screen',
  desktop_look: 'Looking at screen', look_through_camera: 'Looking' };
const ACTIONS = {
  gmail_send: 'Sending mail', gmail_reply: 'Replying to mail', spotify_play: 'Playing', open_app: 'Opening', set_volume: 'Setting volume',
  mute: 'Muting', media_key: 'Media', tv: 'TV', focus_window: 'Switching to', close_window: 'Closing', type_text: 'Typing', say: 'Speaking',
  call_me: 'Calling', lock_pc: 'Locking PC', sleep_pc: 'Sleeping PC', display_off: 'Display off', desktop_click: 'Clicking',
  desktop_type: 'Typing', desktop_keys: 'Pressing keys', desktop_click_at: 'Clicking', desktop_scroll: 'Scrolling',
  open_settings: 'Opening settings', remind: 'Setting a reminder', run_routine: 'Running routine', clipboard_write: 'Copying',
  save_note: 'Saving a note', schedule_meeting: 'Scheduling', nova_panel: 'Panel',
};
let lastPage = null;              // the page her browser is on (web_open), for web_read / web_click

function actionObject(short, a) {
  switch (short) {
    case 'spotify_play': return cut(a.query, 40);
    case 'open_app': case 'focus_window': case 'close_window': case 'run_routine': return cut(a.name, 28);
    case 'set_volume': return a.percent != null && a.percent !== '' ? Math.round(+a.percent || 0) + '%' : '';
    case 'media_key': return cut(a.action, 20);
    case 'tv': return cut(data.pretty(str(a.action)), 24);
    case 'open_settings': return cut(a.page, 24);
    case 'nova_panel': return cut(a.action, 20);
    default: return '';
  }
}
/** A skill node by name, slug or label (read-only: never creates one). */
function findSkill(name) {
  const n = str(name).trim(); if (!n) return null;
  const low = n.toLowerCase(), s = slug(n);
  let hit = graph.byKey.get('skill:' + s) || graph.byKey.get('skill:' + low);
  if (hit && !hit.gone) return hit;
  for (const [key, node] of graph.byKey) {
    if (!key.startsWith('skill:') || node.gone) continue;
    if (key.slice(6) === s || str(node.label).toLowerCase() === low) return node;
  }
  for (const [key, node] of graph.byKey) if (key.startsWith('skill:') && !node.gone && s.length >= 6 && (key.includes(s) || s.includes(key.slice(6)))) return node;
  return null;
}
function skillLabel(name) { const n = findSkill(name); return n ? cut(n.label, 40) : cut(data.pretty(slug(name)), 40); }
/** A memory note (…/memory/<topic>.md) whose topic is on the map -> that topic slug, else ''. */
function noteTopic(path) {
  const b = baseName(data.normPath(path));
  if (!/\.md$/i.test(b)) return '';
  const t = b.slice(0, -3).toLowerCase();
  return t && graph.byKey.has('topic:' + t) ? t : '';
}
/** Glob with no path: the fixed start of an absolute pattern (as the bridge's trail does). */
function globHead(pattern) {
  const head = str(pattern).split(/[*?[{]/)[0].replace(/\\/g, '/');
  return head.includes('/') && /^([A-Za-z]:|~|\/)/.test(head) ? head.slice(0, head.lastIndexOf('/')) || '/' : '';
}
function folderLabel(folder) {
  const n = data.normPath(folder);
  if (!n) return '';
  if (n === '~') return 'Home';
  return baseName(n) || n;
}

/**
 * What a tool call is, without touching the map: {kind, glyph, temp, verb, object, objFree, tgt}.
 * kind: memory | diary | loops | skill | file | folder | machine | web | mail | eyes | job | action.
 * tgt: how materialize() finds the target star(s) when the beat fires.
 */
export function describeTool(name, input, e = null) {
  const short = shortName(name);
  if (SKIP.has(short)) return null;
  const a = typeof input === 'object' && input ? input : parseArgs(input, short);
  const S = (kind, glyph, temp, verb, object, tgt, objFree = false) => ({ kind, glyph, temp, verb, object: str(object), objFree, tgt, short });
  switch (short) {
    case 'recall': { const q = str(a.query || (e && e.detail)); return S('memory', 'star', 'warm', 'Searching memory', cut(q, 60), { t: 'match', q }, true); }
    case 'search_past': { const q = str(a.query); return S('diary', 'star', 'warm', 'Searching past chats', q ? cut(q, 60) : (a.around ? 'an earlier chat' : 'recent chats'), { t: 'history', q }, !!q); }
    case 'day_log': { const d = /^\d{4}-\d{2}-\d{2}$/.test(str(a.date)) ? str(a.date) : todayStr(); return S('diary', 'star', 'warm', 'Reading diary', fmtDay(d), { t: 'day', date: d }); }
    case 'open_loops': return S('loops', 'star', 'warm', 'Checking loops', '', { t: 'loops' });
    case 'close_loop': return S('loops', 'star', 'warm', 'Closing a loop', cut(a.which, 40), { t: 'loop', text: str(a.which) }, true);
    case 'remember': return S('memory', 'star', 'warm', 'Remembering', cut(a.fact, 40), { t: 'slot', kind: 'fact', text: str(a.fact) }, true);
    case 'forget': return S('memory', 'star', 'warm', 'Forgetting', cut(a.query, 40), { t: 'match', q: str(a.query), forget: true }, true);
    case 'read_skill': case 'save_skill': case 'patch_skill': case 'forget_skill': {
      const nm = str(a.name);
      const verb = { read_skill: 'Following skill', save_skill: 'Writing skill', patch_skill: 'Fixing skill', forget_skill: 'Dropping skill' }[short];
      return S('skill', 'hexagon', 'warm', verb, skillLabel(nm), { t: 'skill', name: nm, birth: short === 'save_skill' });
    }
    case 'list_skills': return S('skill', 'hexagon', 'warm', 'Checking skills', '', { t: 'cluster', id: 'skills' });
    case 'Bash': case 'PowerShell': return S('machine', 'triangle', 'cool', 'Running', firstWord(a.command), { t: 'tool', name: 'Bash', label: 'Terminal' });
    case 'start_job': return S('job', 'triangle', 'cool', 'Starting a job', cut(a.title, 28), { t: 'none', cluster: 'hands' });
  }
  if (FILE_VERB[short] || (!WEB_OPEN.has(short) && typeof (a.file_path || a.notebook_path) === 'string' && !FOLDER_TOOLS.has(short))) {
    let path = str(a.file_path || a.notebook_path);
    if (!path && e && e.detail && !/^\.\.\./.test(e.detail)) path = str(e.detail);
    const note = noteTopic(path);
    return S('file', 'square', note ? 'warm' : 'cool', FILE_VERB[short] || 'Opening', baseName(data.normPath(path)) || 'a file', { t: 'file', path, note });
  }
  if (FOLDER_TOOLS.has(short)) {
    const folder = str(a.path) || (short === 'Glob' ? globHead(a.pattern) : '');
    return S('folder', 'square', 'cool', 'Scanning', folderLabel(folder) || cut(a.pattern || a.glob, 28) || 'files', { t: 'folder', path: folder, pattern: str(a.pattern) }, !folder);
  }
  if (WEB_SEARCH.has(short)) { const q = str(a.query || a.question); return S('web', 'ring', 'cool', 'Searching the web', cut(q, 60), { t: 'query', q }, true); }
  if (WEB_OPEN.has(short)) { const url = str(a.url || arr(a.urls)[0] || ''); return S('web', 'ring', 'cool', 'Reading', hostOf(url) || cut(url, 28) || 'a page', { t: 'url', url }); }
  if (WEB_PAGE[short]) return S('web', 'ring', 'cool', WEB_PAGE[short], lastPage ? str(lastPage.text).split('/')[0] : 'the page', { t: 'page' });
  if (MAIL_READ.has(short)) return S('mail', 'triangle', 'cool', 'Checking mail', '', { t: 'tool', name: 'gmail_inbox', label: 'Mail' });
  if (MESSAGES.has(short)) return S('mail', 'triangle', 'cool', 'Checking messages', '', { t: 'tool', name: short, label: 'Messages' });
  if (EYES[short]) return S('eyes', 'triangle', 'cool', EYES[short], '', { t: 'none', cluster: 'hands' });
  if (short.startsWith('minecraft_')) return S('action', 'triangle', 'cool', 'Minecraft', data.pretty(short.slice(10)), { t: 'tool', name: short });
  if (ACTIONS[short]) return S('action', 'triangle', 'cool', ACTIONS[short], actionObject(short, a), { t: 'tool', name: short });
  return S('action', 'triangle', 'cool', data.pretty(short) || 'Tool', '', { t: 'tool', name: short });
}

/** The node key a description's target resolves to (what materialize() would land on), '' when none / not yet known. */
export function targetKey(spec) {
  if (!spec) return '';
  const T = spec.tgt;
  switch (T.t) {
    case 'file': return T.note ? 'topic:' + T.note : (data.normPath(T.path) ? 'file:' + data.normPath(T.path).toLowerCase() : '');
    case 'folder': return T.path && data.normPath(T.path) ? 'dir:' + data.normPath(T.path).toLowerCase() : '';
    case 'query': { const q = str(T.q).replace(/\s+/g, ' ').trim(); return q ? 'page:q:' + q.toLowerCase().slice(0, 120) : ''; }
    case 'url': {
      let s = str(T.url).trim(); if (!s) return '';
      if (!s.includes('://')) s = 'https://' + s;
      try { const x = new URL(s), host = x.hostname.toLowerCase().replace(/^www\./, ''), p = x.pathname.replace(/\/+$/, ''); return p ? 'page:' + (host + p).toLowerCase() : 'site:' + host; } catch { return ''; }
    }
    case 'tool': return 'tool:' + T.name;
    case 'history': return 'history';
    case 'day': return 'day:' + T.date;
    case 'skill': { const n = findSkill(T.name); return n ? n.key : 'skill:' + slug(T.name); }
    default: return '';
  }
}

/* ================================================================== text matching (outputs -> rows) */
const ntCache = new WeakMap();
const ntOf = n => { let v = ntCache.get(n); if (v === undefined || v.t !== n.text) { v = { t: n.text, nt: data.normText(n.text) }; ntCache.set(n, v); } return v.nt; };
function sameText(a, b) { return a && b && (a === b || (a.length >= 12 && b.startsWith(a)) || (b.length >= 12 && a.startsWith(b))); }
/** The memory node whose text starts like `text` (normText prefix), searching `list` or every row. */
function findByText(text, list = null, kinds = null) {
  const a = data.normText(text); if (a.length < 6) return null;
  let best = null;
  for (const n of list || graph.byRow.values()) {
    if (!n || n.gone || n.rowId == null) continue;
    if (kinds && !kinds.includes(n.kind)) continue;
    const b = ntOf(n);
    if (b === a) return n;
    if (!best && sameText(a, b)) best = n;
  }
  return best;
}
const loopNodes = () => graph.nodes.filter(n => n.kind === 'loop' && !n.gone && n.rowId != null);
/** recall / forget output: "- [kind topic] text" lines -> rows. */
function recallRows(out) {
  const ids = [];
  for (const line of str(out).split('\n')) {
    const m = /^- \[([a-z]+)(?: ([^\]]*))?\] (.+)$/.exec(line.trim());
    if (!m) continue;
    const kind = m[1], topic = str(m[2]).trim(), text = m[3];
    let list = null;
    if (kind === 'project' && topic) { const t = graph.byKey.get('topic:' + topic); list = t ? t.kids : null; }
    const n = findByText(text, list, [kind]) || (list ? findByText(text, null, [kind]) : null);
    if (n && !ids.includes(n.rowId)) ids.push(n.rowId);
  }
  return ids;
}
/** day_log output: "HH:MM  text" lines of one date -> rows. */
function dayRows(out, date) {
  const day = graph.byKey.get('day:' + date), ids = [];
  for (const line of str(out).split('\n')) {
    const m = /^\d{1,2}:\d{2}\s+(.+)$/.exec(line.trim()); if (!m) continue;
    const n = findByText(m[1], day ? day.kids : null, ['day']);
    if (n && !ids.includes(n.rowId)) ids.push(n.rowId);
  }
  return ids;
}
/** open_loops output: "- (date) text" lines -> rows. */
function loopRows(out) {
  const ids = [], loops = loopNodes();
  for (const line of str(out).split('\n')) {
    const m = /^- \(\d{4}-\d{2}-\d{2}\) (.+)$/.exec(line.trim()); if (!m) continue;
    const n = findByText(m[1], loops, ['loop']);
    if (n && !ids.includes(n.rowId)) ids.push(n.rowId);
  }
  return ids;
}

/* ================================================================== stats (latency proof) */
const RING = 256;
function ring() { return { a: new Float64Array(RING), n: 0, i: 0 }; }
function ringPush(r, v) { r.a[r.i] = v; r.i = (r.i + 1) % RING; if (r.n < RING) r.n++; }
function ringPct(r, p) { if (!r.n) return null; const s = Array.from(r.a.subarray(0, r.n)).sort((x, y) => x - y); return +s[Math.min(s.length - 1, Math.floor(s.length * p))].toFixed(2); }
export const stats = {
  transport: ring(),     // arrival - bridge stamp (ms)
  pushToBus: ring(),     // bus emit - bridge stamp - 60 ms (what the conductor adds beyond its deliberate delay)
  conductor: ring(),     // bus emit - due time (ms)
  beats: 0, launches: 0, fanouts: 0, jumps: 0, dropped: 0, relayed: 0, polled: 0,
  summary() {
    const f = r => ({ n: r.n, p50: ringPct(r, .5), p95: ringPct(r, .95), max: ringPct(r, 1) });
    return { transport: f(this.transport), pushToBus: f(this.pushToBus), conductor: f(this.conductor), beats: this.beats, launches: this.launches,
      fanouts: this.fanouts, jumps: this.jumps, dropped: this.dropped, relayed: this.relayed, polled: this.polled, skew, live, selfPoll, embed: EMBED };
  },
  reset() { for (const k of ['transport', 'pushToBus', 'conductor']) this[k] = ring(); this.beats = this.launches = this.fanouts = this.jumps = this.dropped = this.relayed = this.polled = 0; },
};

/* ================================================================== clock (F-2) */
const skewBuf = new Float64Array(SKEW_N);
let skewN = 0, skewI = 0, skew = 0, skewHigh = 0, skewHighAt = 0;
function pushSkew(s) {
  skewBuf[skewI] = s; skewI = (skewI + 1) % SKEW_N; if (skewN < SKEW_N) skewN++;
  let m = Infinity; for (let i = 0; i < skewN; i++) if (skewBuf[i] < m) m = skewBuf[i];
  skew = m;
}
/** skew = min(now - at) over the last 16 events. A jump of > 2 s resets it: at once downward; upward only after
 * three separate deliveries agree (one stalled batch is lateness, not a new clock). */
function noteSkew(s, now) {
  if (!skewN) { pushSkew(s); return; }
  if (s < skew - SKEW_JUMP) { skewN = skewI = skewHigh = 0; pushSkew(s); return; }
  if (s > skew + SKEW_JUMP) {
    if (now - skewHighAt > 250) { skewHigh++; skewHighAt = now; }
    if (skewHigh >= 3) { skewN = skewI = skewHigh = 0; pushSkew(s); }
    return;
  }
  skewHigh = 0; pushSkew(s);
}

/* ================================================================== conductor (F-2) */
function mkBeat() {
  return { used: false, tok: 0, type: '', at: 0, due: 0, ordered: true, launch: false, fired: false, firedAt: 0, ready: true, waitUntil: 0,
    rec: null, dep: false, pay: null, keep: false, region: '', joined: false, jump: false, late: 0, live: false, sync: false };
}
const pool = []; for (let i = 0; i < POOL; i++) pool.push(mkBeat());
const queue = [];
let tokN = 0, pumping = false, pumpAgain = false, timer = 0, timerAt = Infinity;
let fan = null, fanId = 0;
const lastOrd = { at: 0, firedAt: 0 };
let syncDepth = 0;

function release(b) { b.used = false; b.rec = null; b.pay = null; b.fired = true; }
function take() {
  for (const b of pool) if (!b.used) return b;
  // pool full: hidden -> drop the oldest droppable beat; visible -> play the oldest at its end state
  const hid = hiddenNow();
  for (let i = 0; i < queue.length; i++) {
    const b = queue[i]; if (b.fired) continue;
    if (hid && b.keep) continue;
    if (hid) dropBeat(b); else { b.jump = true; fire(b, nowE()); }
    compact();
    break;
  }
  for (const b of pool) if (!b.used) return b;
  if (queue.length) { const b = queue[0]; if (!b.fired) dropBeat(b); compact(); }
  for (const b of pool) if (!b.used) return b;
  return null;
}
/**
 * Queue a beat. o = {at (epoch ms), ordered, launch, rec, dep (waits for rec's launch), pay, keep, region, immediate, ready, live}.
 * Activity beats are due at at + skew + 60 ms; immediate ones (orb / host / polled) now.
 */
function enqueue(type, o = {}) {
  const b = take(); if (!b) return null;
  const now = nowE();
  b.used = true; b.tok = ++tokN; b.type = type; b.at = o.at ?? now; b.fired = false; b.firedAt = 0;
  b.sync = syncDepth > 0;
  b.due = o.immediate || b.sync ? now : b.at + skew + DELAY;
  b.ordered = o.ordered !== false; b.launch = !!o.launch; b.rec = o.rec || null; b.dep = !!o.dep; b.pay = o.pay || null;
  b.keep = !!o.keep; b.region = o.region || ''; b.joined = false; b.jump = false; b.late = 0; b.live = !!o.live;
  b.ready = o.ready !== false; b.waitUntil = b.due + READY_WAIT;
  if (b.rec && b.launch) b.rec.beat = b;
  queue.push(b);
  pump();
  return b;
}
function arm(t) {
  if (timer && t >= timerAt) return;
  if (timer) clearTimeout(timer);
  timerAt = t; timer = setTimeout(onTimer, Math.max(0, t - nowE()));
}
function onTimer() { timer = 0; timerAt = Infinity; pump(); }
function compact() {
  let w = 0;
  for (let i = 0; i < queue.length; i++) { const b = queue[i]; if (b.fired) { if (b.used) release(b); } else queue[w++] = b; }
  queue.length = w;
}
function dropBeat(b) {
  stats.dropped++;
  if (b.rec && b.launch) { b.rec.launched = true; b.rec.dropped = true; }
  b.fired = true; b.firedAt = nowE();
}
/** Where a launch goes: join the current fan-out (parallel calls, a queued burst, or a late same-region fold) or open a new one. */
function launchTime(b, t, now) {
  b.joined = false;
  const f = fan;
  if (!f) return t;
  const lateBy = Math.max(now, t) - b.due;
  const canJoin = f.n < FAN_MAX && (b.at - f.at0 <= PAR_MS || b.due - f.lastAt < JOIN_MS ||
    (lateBy > LAG_FOLD && b.region === f.region && b.at - f.at0 <= FOLD_WIN));
  if (canJoin) { b.joined = true; return Math.max(b.due, f.lastAt + FAN_GAP, lastOrd.firedAt); }
  return Math.max(t, f.lastAt + LAUNCH_GAP);
}
/** Earliest time an ordered beat may fire (in order, spaced, never faster than x1.25). */
function whenOrdered(b, now) {
  if (b.sync) return now;
  if (now - b.due > LAG_JUMP) { b.jump = true; return now; }
  let t = b.due;
  if (!b.ready && now < b.waitUntil) return Math.max(t, b.waitUntil);
  if (lastOrd.firedAt) {
    const dAt = b.at - lastOrd.at;
    t = Math.max(t, dAt > 0 ? lastOrd.firedAt + dAt / MAX_SPEED : lastOrd.firedAt);
  }
  if (b.launch) t = launchTime(b, t, now);
  return t;
}
function pump() {
  if (pumping) { pumpAgain = true; return; }
  pumping = true;
  try {
    do {
      pumpAgain = false;
      if (hiddenNow() && !syncDepth) break;
      const now = nowE();
      let next = Infinity, blocked = false;
      for (let i = 0; i < queue.length; i++) {
        const b = queue[i];
        if (b.fired) continue;
        if (!b.keep && now - b.due > STALE_DROP) { dropBeat(b); continue; }
        if (b.rec && b.rec.dropped && !b.launch) { dropBeat(b); continue; }
        if (b.ordered) {
          if (blocked) continue;
          const t = whenOrdered(b, now);
          if (t <= now + 1) fire(b, now); else { if (t < next) next = t; blocked = true; }
        } else {
          if (b.dep && b.rec && !b.rec.launched) continue;
          const t = b.sync ? now : Math.max(b.due, b.dep && b.rec ? b.rec.launchedAt : 0);
          if (t <= now + 1 || now - b.due > LAG_JUMP) { if (now - b.due > LAG_JUMP) b.jump = true; fire(b, now); }
          else if (t < next) next = t;
        }
      }
      compact();
      if (next < Infinity) arm(next);
    } while (pumpAgain);
  } finally { pumping = false; }
}
function fire(b, now) {
  b.fired = true; b.firedAt = now; b.late = Math.max(0, now - b.due);
  if (b.ordered) { lastOrd.at = Math.max(lastOrd.at, b.at); lastOrd.firedAt = now; }
  if (b.launch && b.rec) {
    if (b.jump) { b.rec.group = ++fanId; b.rec.gi = 0; fan = null; }
    else if (b.joined && fan) { fan.n++; fan.lastAt = now; b.rec.group = fan.id; b.rec.gi = fan.n - 1; }
    else { fan = { id: ++fanId, n: 1, at0: b.at, lastAt: now, region: b.region }; b.rec.group = fan.id; b.rec.gi = 0; stats.launches++; }
    if (!b.jump && !b.joined) stats.fanouts = fanId;
  }
  if (b.jump) stats.jumps++;
  stats.beats++;
  if (b.live) { ringPush(stats.pushToBus, now - b.at - DELAY); ringPush(stats.conductor, now - b.due); }
  try { dispatch(b); } catch (err) { console.error('[feed] beat', b.type, err); }
}
/** A prefetch landed: the beat may go now. */
function readyBeat(b, tok) { if (b && b.used && b.tok === tok && !b.fired) { b.ready = true; pump(); } }

/* ================================================================== emitting */
function emitN(type, p, b = null) {
  if (b) { if (b.late > 0) p.late = Math.round(b.late); if (b.jump) p.jump = true; }
  bus.emit(type, p);
}

/* ================================================================== turns */
const turn = { open: false, id: 0, openedAt: 0, heard: '', spoke: false, idleSince: 0, endTimer: 0, lastActivity: 0 };
let turnId = 0;
function openTurn(reason, heard = '') {
  if (replay.on) abortReplay();
  if (turn.open) closeTurn();
  turn.open = true; turn.id = ++turnId; turn.openedAt = nowE(); turn.heard = heard; turn.spoke = false; turn.idleSince = 0; turn.lastActivity = nowE();
  clearTimeout(turn.endTimer);
  enqueue('turnstart', { immediate: true, pay: { heard, reason }, keep: false });
  if (heard) enqueue('heard', { immediate: true, pay: { text: heard } });
  schedulePoll();
  wakeOrb();
}
function closeTurn() {
  if (!turn.open) return;
  turn.open = false; turn.idleSince = 0; clearTimeout(turn.endTimer);
  enqueue('turnend', { immediate: true, pay: {} });
  schedulePoll();
}
function ensureTurn(reason) { if (!turn.open) openTurn(reason); turn.lastActivity = nowE(); }
function onHeard(text) {
  text = str(text).trim();
  if (turn.open && !turn.heard && nowE() - turn.openedAt < 6000) {
    turn.heard = text;
    if (text) enqueue('heard', { immediate: true, pay: { text } });
    return;
  }
  openTurn('heard', text);
}
function armEnd() {
  clearTimeout(turn.endTimer);
  if (!turn.open || !turn.idleSince) return;
  const need = turn.spoke ? END_SPOKE : END_SILENT;
  turn.endTimer = setTimeout(() => {
    if (!turn.open || !turn.idleSince) return;
    if (nowE() - turn.idleSince >= (turn.spoke ? END_SPOKE : END_SILENT) - 5) closeTurn(); else armEnd();
  }, Math.max(10, turn.idleSince + need - nowE() + 5));
}

/* ================================================================== orb frames */
let lastSaying = '', lastOrbTurn = null, lastOrbSig = '', orbFrames = 0;
function ingestOrb(o, src) {
  if (!o || typeof o !== 'object') return;
  if (src !== 'inject' && !live) return;
  const now = nowE();
  lastOrbAt = now; orbFrames++;
  if (src === 'relay') { lastRelayAt = now; stats.relayed++; if (selfPoll) stopTransport(); }
  if (tapFn) { try { tapFn('orb', o); } catch { /* recorder */ } }
  const mode = str(o.mode || 'idle');
  lastOrbMode = mode;
  state.flavour = str(o.flavour); state.level = Number.isFinite(+o.level) ? +o.level : 0;
  if (typeof o.game === 'boolean' && o.game !== state.game) core.setGame(o.game);
  const prev = state.mode;
  if (mode !== prev) {
    state.mode = mode;
    bus.emit('mode', { mode, prev });
    if (mode === 'thinking' && !turn.open && !replay.on) openTurn('thinking');
  }
  // the orb frame itself: ~10 Hz while active, on change only while idle (idle frames must not keep the renderer awake)
  const active = mode !== 'idle' || turn.open;
  const sig = mode + '|' + state.flavour + '|' + (o.jobs | 0) + '|' + !!o.game + '|' + (o.pending ? str(o.pending.tool) + str(o.pending.target) : '');
  if (active || sig !== lastOrbSig) {
    bus.emit('orb', { mode, flavour: state.flavour, drive: state.drive, level: state.level, jobs: o.jobs | 0, game: !!o.game,
      mic: o.mic ?? null, pending: o.pending || null });
  }
  lastOrbSig = sig;
  // her words as they come out of the speakers
  const saying = str(o.saying).trim();
  if (saying && saying !== lastSaying) {
    if (!turn.open && !replay.on) openTurn('speaking');
    turn.spoke = true;
    if (!hiddenNow()) enqueue('chunk', { immediate: true, ordered: false, pay: { text: saying } });
  }
  lastSaying = saying;
  if (mode === 'speaking' && turn.open) turn.spoke = true;
  // a finished exchange we never saw start (booted mid-turn, or a very fast reply)
  if (typeof o.turn === 'number') {
    if (lastOrbTurn != null && o.turn > lastOrbTurn && !turn.open && !replay.on) openTurn('orb');
    lastOrbTurn = o.turn;
  }
  // turn end: idle (or the follow-up window) for 1.2 s after speaking, 4 s without
  if (turn.open) {
    const idle = mode === 'idle' || mode === 'listening';
    if (idle) { if (!turn.idleSince) { turn.idleSince = now; armEnd(); } }
    else if (turn.idleSince) { turn.idleSince = 0; clearTimeout(turn.endTimer); }
  }
}

/* ================================================================== raw activity events */
const steps = new Map();          // tool_use id -> step record
const heldSynth = new Map();      // short tool -> a step made from a permission event (no tool event seen yet)
let synthN = 0, tapFn = null;

function newRec(id, name, short, args, input, spec, atMs, seq) {
  return { id, name, short, args, input: str(input), spec, at: atMs, seq: seq | 0, beat: null, launched: false, launchedAt: 0, dropped: false,
    done: false, ok: true, output: '', targets: [], primary: -1, pos: null, via: null, region: '', group: 0, gi: 0, t0: 0,
    match: null, ready: true, held: false, synthetic: false, auto: false, node: null };
}

function ingest(e, src) {
  if (!e || typeof e !== 'object') return;
  const now = nowE();
  const injected = src === 'inject';
  if (!injected) {
    if (!live) return;
    const seq = +e.seq || 0;
    if (seq) {
      if (seq <= lastSeq) {
        // a seq far behind with a newer stamp: the bridge restarted
        if (seq < lastSeq - 5 && (+e.at || 0) * 1000 > lastAtMs + 1000) lastSeq = seq - 1; else return;
      }
      lastSeq = seq;
    }
    if (src === 'relay') { lastRelayAt = now; stats.relayed++; if (selfPoll) stopTransport(); } else stats.polled++;
    lastEventAt = now;
  }
  if (tapFn) { try { tapFn('evt', e); } catch { /* recorder */ } }
  const atMs = Number.isFinite(+e.at) && +e.at > 0 ? +e.at * 1000 : now;
  if (!injected) { ringPush(stats.transport, now - atMs); lastAtMs = Math.max(lastAtMs, atMs); }
  noteSkew(now - atMs, now);
  wakeOrb();
  try { route(e, atMs, !injected); } catch (err) { console.error('[feed] event', e.kind, err); }
}

function route(e, atMs, liveEv) {
  const kind = str(e.kind);
  switch (kind) {
    case 'tool': return onTool(e, atMs, liveEv);
    case 'tool_done': return onToolDone(e, atMs, liveEv);
    case 'permission': return onPermission(e, atMs, liveEv);
    case 'allowed': case 'denied': return onAnswer(e, atMs, liveEv, kind === 'allowed');
    case 'recall': return str(e.text) === 'Skill' || (e.skill && !e.ids) ? onSkillRecall(e, atMs, liveEv) : onRecall(e, atMs, liveEv);
    case 'remember': return onRemember(e, atMs, liveEv);
    case 'text': {
      let text = str(e.text).trim(); if (!text) return;
      text = text.replace(/^\(call\)\s*/, '');
      ensureTurn('text');
      enqueue('say', { at: atMs, live: liveEv, pay: { text } });
      return;
    }
    case 'thinking': if (!turn.open && nowE() - lastOrbAt > 3000 && !replay.on) openTurn('thinking'); return;
    case 'heard': return onHeard(e.text);
    default: return;   // system, exit, …: not beats
  }
}

function onTool(e, atMs, liveEv) {
  const name = str(e.text), short = shortName(name);
  if (!short || SKIP.has(short)) return;
  const args = parseArgs(e.input, short);
  const id = str(e.id) || ('t' + (e.seq || ++synthN));
  if (steps.has(id)) return;
  // a permission prompt for this tool arrived first: its held leg is this call
  const held = heldSynth.get(short);
  if (held && !held.real && nowE() - held.heldAt < HOLD_REUSE) {
    held.real = true; held.args = args; held.input = str(e.input); steps.set(id, held); heldSynth.delete(short);
    return;
  }
  const spec = describeTool(name, args, e);
  if (!spec) return;
  ensureTurn('tool');
  const rec = newRec(id, name, short, args, e.input, spec, atMs, e.seq);
  steps.set(id, rec); trimSteps();
  const b = enqueue('step', { at: atMs, launch: true, rec, region: spec.kind, live: liveEv, ready: !needsPrefetch(spec) });
  if (b && !b.ready) prefetch(rec, b);
}

function onToolDone(e, atMs, liveEv) {
  const rec = steps.get(str(e.id));
  if (!rec || rec.done) return;
  rec.done = true; rec.ok = str(e.text) !== 'error'; rec.output = str(e.output);
  enqueue('done', { at: atMs, ordered: false, dep: true, rec, live: liveEv });
}

function onPermission(e, atMs, liveEv) {
  const short = shortName(e.text);
  let rec = null;
  for (const r of steps.values()) if (r.short === short && !r.done && !r.held && atMs - r.at < HOLD_REUSE && (!rec || r.at > rec.at)) rec = r;
  if (!rec) {
    // no tool event (yet): launch a leg for it now; the tool event that follows reuses it
    const spec = describeTool(e.text, {}, null); if (!spec) return;
    const detail = str(e.detail).replace(/^[a-z]+:\s*/, '');
    if (!spec.object && detail) { spec.object = cut(detail, 40); spec.objFree = true; }
    ensureTurn('permission');
    rec = newRec('perm' + (e.seq || ++synthN), str(e.text), short, {}, '', spec, atMs, e.seq);
    rec.synthetic = true; rec.heldAt = nowE();
    steps.set(rec.id, rec); heldSynth.set(short, rec);
    enqueue('step', { at: atMs, launch: true, rec, region: spec.kind, live: liveEv });
  }
  rec.held = true;
  enqueue('hold', { at: atMs, ordered: false, dep: true, rec, live: liveEv });
}
function onAnswer(e, atMs, liveEv, allowed) {
  const short = shortName(e.text);
  let rec = null;
  for (const r of steps.values()) if (r.short === short && r.held && !r.answered && (!rec || r.at > rec.at)) rec = r;
  if (!rec) return;
  rec.answered = true;
  enqueue(allowed ? 'allow' : 'deny', { at: atMs, ordered: false, dep: true, rec, live: liveEv });
}

function onSkillRecall(e, atMs, liveEv) {
  const name = str(e.skill || e.detail).trim(); if (!name) return;
  const spec = describeTool('read_skill', { name }, null);
  ensureTurn('skill');
  const rec = newRec('skill' + (e.seq || ++synthN), 'read_skill', 'read_skill', { name }, '', spec, atMs, e.seq);
  rec.auto = true; rec.done = true; rec.ok = true;
  steps.set(rec.id, rec);
  enqueue('step', { at: atMs, launch: true, rec, region: spec.kind, live: liveEv });
  enqueue('done', { at: atMs + 450, ordered: false, dep: true, rec, live: false });
}

function onRecall(e, atMs, liveEv) {
  const pay = { ids: arr(e.ids).map(Number), near: arr(e.near).map(Number), days: arr(e.days).map(String), terms: arr(e.terms).map(String) };
  ensureTurn('recall');
  const unknown = pay.ids.concat(pay.near).some(id => !graph.byRow.has(id));
  const b = enqueue('recall', { at: atMs, pay, live: liveEv, ready: !unknown });
  if (b && unknown) { const tok = b.tok; b.waitUntil = b.due + 400; pollNewSafe().finally(() => readyBeat(b, tok)); }
}

function onRemember(e, atMs, liveEv) {
  const what = str(e.text), detail = str(e.detail).trim();
  if (!detail) return;
  const L = (mode, kind) => enqueue('learn', { at: atMs, keep: true, live: liveEv, pay: { mode, kind, text: detail } });
  switch (what) {
    case 'Remembered': return L('prov', 'fact');
    case 'Learned how': return L('prov', 'skill');
    case 'Improved skill': return L('improved', 'skill-improved');
    case 'Left open': return L('prov', 'loop');
    case 'Finished': return L('closed', 'loop-closed');
    case 'Forgot': return enqueue('forget', { at: atMs, keep: true, live: liveEv, pay: { text: detail } });
    default: return;
  }
}

function trimSteps() {
  if (steps.size <= 200) return;
  for (const [id, r] of steps) { if (steps.size <= 150) break; if (r.done || nowE() - r.at > 120000) steps.delete(id); }
}

/* ================================================================== prefetch + materialize (side effects at fire time) */
const needsPrefetch = spec => spec && spec.tgt.t === 'match' && !!spec.tgt.q;
function prefetch(rec, b) {
  const tok = b.tok, q = rec.spec.tgt.q;
  api('/api/brain/match?q=' + encodeURIComponent(q.slice(0, 300))).then(async m => {
    const all = arr(m.ids).map(Number), near = arr(m.near).map(Number);
    if (all.concat(near).some(id => !graph.byRow.has(id))) { try { await pollNewSafe(); } catch { /* bridge busy */ } }
    // her recall tool gets <= 6 fact/project rows; /api/brain/match also ranks loops and keeps 8
    const ids = all.filter(id => { const n = graph.byRow.get(id); return n && (n.kind === 'fact' || n.kind === 'project'); }).slice(0, 6);
    rec.match = { ids, near: near.filter(id => graph.byRow.has(id)).slice(0, 12), terms: arr(m.terms).map(String) };
  }).catch(() => { rec.match = null; }).finally(() => readyBeat(b, tok));
}
function ancestors(node) {
  const out = [];
  for (let n = node && node.parent; n; n = n.parent) out.unshift(n.idx);
  return out;
}
function materialize(rec) {
  const T = rec.spec.tgt;
  let node = null, nodes = null, pos = null;
  switch (T.t) {
    case 'match': {
      let ids = rec.match ? rec.match.ids : null;
      if (ids) nodes = ids.map(id => graph.byRow.get(id)).filter(Boolean);
      else nodes = data.searchLocal(T.q, 12).map(i => data.nodeOf(i)).filter(n => n && n.rowId != null && (n.kind === 'fact' || n.kind === 'project')).slice(0, 6);
      rec.rowIds = nodes.map(n => n.rowId);
      if (rec.match) { rec.near = data.starsOfRows(rec.match.near); rec.terms = rec.match.terms; }
      if (!nodes.length) pos = clusterCenter('you');
      break;
    }
    case 'file': node = T.note ? graph.byKey.get('topic:' + T.note) || null : (T.path ? data.fileNode(T.path) : null); if (node && !T.note) rec.via = ancestors(node); if (!node) pos = clusterCenter('files'); break;
    case 'folder': node = T.path ? data.folderNode(T.path) : null; if (node) rec.via = ancestors(node); else pos = clusterCenter('files'); break;
    case 'query': node = T.q ? data.webNode(T.q, true) : null; if (!node) pos = clusterCenter('web'); break;
    case 'url': node = T.url ? data.webNode(T.url) : null; if (node) lastPage = node; else pos = clusterCenter('web'); break;
    case 'page': node = lastPage && !lastPage.gone ? lastPage : null; if (node) data.ensure({ key: node.key }); else pos = clusterCenter('web'); break;
    case 'tool': node = data.toolNode(T.name, T.label); break;
    case 'history': node = data.historyNode(); break;
    case 'day': node = graph.byKey.get('day:' + T.date) || null; if (node) rec.pearls = node.kids.filter(k => !k.gone).map(k => k.idx); else pos = data.diaryArm(0); break;
    case 'loops': nodes = loopNodes(); if (!nodes.length) pos = clusterCenter('loops'); break;
    case 'loop': node = findByText(T.text, loopNodes(), ['loop']); if (!node) { nodes = loopNodes(); pos = clusterCenter('loops'); } break;
    case 'skill': node = findSkill(T.name); if (!node) { try { pos = data.slotFor({ kind: 'skill', text: slug(T.name) }).pos; } catch { pos = clusterCenter('skills'); } rec.slot = pos; } break;
    case 'slot': try { pos = data.slotFor({ kind: T.kind, text: T.text }).pos; } catch { pos = clusterCenter('you'); } rec.slot = pos; break;
    case 'cluster': pos = clusterCenter(T.id); break;
    default: pos = clusterCenter(T.cluster || 'hands'); break;
  }
  if (node) nodes = [node];
  nodes = (nodes || []).filter(n => n && n.idx >= 0);
  rec.node = nodes[0] || null;
  rec.targets = nodes.map(n => n.idx);
  rec.primary = rec.targets.length ? rec.targets[0] : -1;
  rec.pos = rec.primary >= 0 ? posOf(rec.primary) : (pos ? pos.slice(0, 3) : null);
  rec.region = rec.node ? rec.node.cluster : (T.cluster || T.id || ({ memory: 'you', diary: 'diary', loops: 'loops', skill: 'skills', file: 'files', folder: 'files', web: 'web' }[rec.spec.kind] || 'hands'));
}

/** step:done results: urls (<= 5), path:line hits (<= 6), dates, and the memory rows the output really holds. */
function results(rec) {
  const out = rec.output, short = rec.short, kind = rec.spec.kind;
  const r = { urls: [], hits: [], dates: [], rowIds: [], stars: [], dayStars: [] };
  let chars = out.length; const more = /\((\d+) more characters\)\s*$/.exec(out); if (more) chars += +more[1] - more[0].length;
  // urls
  const seen = new Set();
  for (const m of out.matchAll(/\bhttps?:\/\/[^\s<>"'`)\]}]+/gi)) {
    const url = m[0].replace(/[.,;:!?]+$/, ''), host = hostOf(url);
    if (!host || seen.has(host + url)) continue; seen.add(host + url);
    r.urls.push({ url, host, idx: -1 });
    if (r.urls.length >= 5) break;
  }
  if (kind === 'web' && WEB_SEARCH.has(short) && rec.ok) {
    // search results fall into the Web region: one ring per host
    const hosts = new Set();
    for (const u of r.urls) { if (hosts.has(u.host)) continue; hosts.add(u.host); try { const n = data.webNode('https://' + u.host); if (n) u.idx = n.idx; } catch { /* odd host */ } }
  }
  // path:line hits (Grep -n) or listed paths (Glob)
  if (kind === 'folder' && rec.ok) {
    const base = rec.spec.tgt.path ? data.normPath(rec.spec.tgt.path) : '';
    for (const raw of out.split('\n')) {
      const line = raw.trim(); if (!line || /^(\.\.\.|Found \d+|No (files|matches))/i.test(line)) continue;
      let m = /^(.+?):(\d+)[:-]/.exec(line), path = '', ln = 0;
      if (m && !/^[a-z]+:\/\//i.test(line)) { path = m[1]; ln = +m[2]; }
      else if (/^([A-Za-z]:[\\/]|~[\\/]|\/|\.{1,2}[\\/])/.test(line) || /[\\/][^\\/\s]+\.[A-Za-z0-9]{1,6}$/.test(line)) path = line;
      if (!path || path.length > 400) continue;
      if (!/^([A-Za-z]:|~|\/|\\\\)/.test(path) && base) path = base + '/' + path.replace(/^\.[\\/]/, '');
      let idx = -1; try { const n = data.fileNode(path); if (n) idx = n.idx; } catch { /* odd path */ }
      r.hits.push({ path: baseName(data.normPath(path)), line: ln, idx });
      if (r.hits.length >= 6) break;
    }
  }
  // dates
  for (const m of out.matchAll(/\b(20\d\d-[01]\d-[0-3]\d)\b/g)) { if (!r.dates.includes(m[1])) r.dates.push(m[1]); if (r.dates.length >= 8) break; }
  for (const d of r.dates) { const n = graph.byKey.get('day:' + d); if (n) r.dayStars.push(n.idx); }
  // memory rows really returned
  if (rec.ok) {
    if (short === 'recall' || short === 'forget') r.rowIds = recallRows(out);
    else if (short === 'day_log') r.rowIds = dayRows(out, rec.spec.tgt.date);
    else if (short === 'open_loops') r.rowIds = loopRows(out);
  }
  r.stars = data.starsOfRows(r.rowIds);
  return { r, chars };
}

/* ================================================================== dispatch: beat -> normalized event */
function dispatch(b) {
  switch (b.type) {
    case 'turnstart': {
      state.turn = true;
      emitN('turn:start', { heard: str(b.pay.heard) }, b);
      logOpen();
      return;
    }
    case 'turnend': state.turn = false; emitN('turn:end', {}, b); logClose(); return;
    case 'heard': emitN('heard', { text: str(b.pay.text) }, b); return;
    case 'say': emitN('say', { text: str(b.pay.text) }, b); logPush(['W']); return;
    case 'chunk': emitN('speak:chunk', { text: str(b.pay.text) }, b); return;
    case 'recall': return fireRecall(b);
    case 'step': return fireStep(b);
    case 'done': return fireDone(b);
    case 'hold': case 'allow': case 'deny': {
      const rec = b.rec; if (!rec) return;
      emitN(b.type === 'hold' ? 'step:hold' : b.type === 'allow' ? 'step:allow' : 'step:deny', { id: rec.id, tool: rec.short }, b);
      logPush([b.type === 'hold' ? 'H' : b.type === 'allow' ? 'A' : 'X', rec.id]);
      return;
    }
    case 'learn': return fireLearn(b);
    case 'forget': return fireForget(b);
    case 'genesis': emitN('genesis', b.pay, b); return;
    case 'fn': if (typeof b.pay === 'function') b.pay(b); return;
    default: return;
  }
}

function fireRecall(b) {
  const p = b.pay;
  const stars = data.starsOfRows(p.ids), nearStars = data.starsOfRows(p.near).slice(0, 12);
  const dayStars = []; for (const d of p.days) { const n = graph.byKey.get('day:' + d); if (n) dayStars.push(n.idx); }
  const top = p.ids.length ? graph.byRow.get(p.ids[0]) : null;
  const topic = top ? (top.kind === 'project' && top.parent ? top.parent.label : top.kind === 'day' ? 'diary' : top.label) : '';
  emitN('recall', { ids: p.ids, near: p.near, days: p.days, terms: p.terms, stars, nearStars, dayStars, t0: state.t,
    glyph: 'star', verb: 'Searching memory', object: cut(topic || p.terms.slice(0, 4).join(' '), 60), empty: !stars.length }, b);
  logPush(['R', p.ids.slice(0, 12), p.near.slice(0, 12), p.days.slice(0, 8)]);
}

function fireStep(b) {
  const rec = b.rec;
  try { materialize(rec); } catch (err) { console.error('[feed] target', rec.short, err); rec.targets = []; rec.primary = -1; rec.region = rec.region || 'hands'; }
  rec.launched = true; rec.launchedAt = nowE(); rec.t0 = state.t;
  const s = rec.spec;
  const p = { id: rec.id, tool: rec.short, name: rec.name, region: rec.region, kind: s.kind, targets: rec.targets, primary: rec.primary, pos: rec.pos,
    temp: s.temp, glyph: s.glyph, verb: s.verb, object: s.object, group: rec.group, gi: rec.gi, input: rec.input, args: rec.args,
    t0: state.t, seq: rec.seq, rand: mulberry32(rec.seq || hash32(rec.id))() };
  if (rec.via && rec.via.length) p.via = rec.via;
  if (rec.near) p.near = rec.near;
  if (rec.terms) p.terms = rec.terms;
  if (rec.slot) p.slot = rec.slot;
  if (rec.pearls) p.pearls = rec.pearls;
  if (rec.auto) p.auto = true;
  if (rec.synthetic) p.synthetic = true;
  if (rec.spec.tgt.birth) p.birth = true;
  emitN('step', p, b);
  logPush(['T', rec.id, rec.short, s.kind, s.glyph, s.verb, s.objFree ? '' : s.object, (rec.node ? [rec.node] : rec.targets.map(i => data.nodeOf(i))).filter(Boolean).slice(0, 8).map(n => n.key), rec.group]);
}

function fireDone(b) {
  const rec = b.rec; if (!rec) return;
  let res = { r: { urls: [], hits: [], dates: [], rowIds: [], stars: [], dayStars: [] }, chars: rec.output.length };
  try { res = results(rec); } catch (err) { console.error('[feed] results', rec.short, err); }
  const y = clamp(Math.log10(res.chars + 1) / 3.4, 0, 1);
  emitN('step:done', { id: rec.id, tool: rec.short, ok: rec.ok, yield: +y.toFixed(3), chars: res.chars, results: res.r, output: rec.output, t0: state.t }, b);
  logPush(['D', rec.id, rec.ok ? 1 : 0, +y.toFixed(2), res.r.rowIds.slice(0, 8)]);
  if (rec.ok && !b.jump) afterDone(rec);
  else if (rec.ok && b.jump) afterDone(rec);
}
/** Tools that change memory: the birth starts the moment the tool reports success (no raw 'remember' event for them). */
function afterDone(rec) {
  const out = rec.output.trim(), a = rec.args || {};
  const L = (mode, kind, text) => { if (text) enqueue('learn', { immediate: true, ordered: false, keep: true, pay: { mode, kind, text } }); };
  switch (rec.short) {
    case 'remember': if (/^Stored as memory/i.test(out)) L('prov', 'fact', str(a.fact).trim()); return;
    case 'save_skill': { const m = /^Saved skill (.+?)\.?$/i.exec(out); if (m) L('prov', 'skill', m[1] || str(a.name)); return; }
    case 'patch_skill': if (!/^(no |not |could not|failed)/i.test(out)) L('improved', 'skill-improved', str(a.name)); return;
    case 'close_loop': { const m = /^Closed: (.+)$/s.exec(out); if (m) L('closed', 'loop-closed', m[1]); return; }
    case 'forget': { const m = /^Forgot: (.+)$/s.exec(out); if (m) enqueue('forget', { immediate: true, ordered: false, keep: true, pay: { text: m[1].trim() } }); return; }
    default: return;
  }
}

/* ================================================================== learning (births, bulk learns, endings) */
const recentForget = new Map();   // star idx -> epoch ms an explicit forget was played
const closedLoops = new Map();    // star idx -> epoch ms a loop was finished (its row deletion is no forget)
const provAt = new Map();         // kind -> epoch ms of the last provisional birth still waiting for its row
function learnKindOf(rowKind) { return rowKind === 'loop' ? 'loop-open' : rowKind === 'project' ? 'project' : rowKind === 'day' ? 'day' : rowKind === 'skill' ? 'skill' : 'fact'; }

function fireLearn(b) {
  const p = b.pay;
  if (p.mode === 'polled') { emitN('learn', p.out, b); return; }
  const now = nowE();
  if (p.mode === 'prov') {
    const isSkill = p.kind === 'skill';
    const text = isSkill ? slug(p.text) : p.text;
    // already a star with this text (a memory she already had): no second birth
    const known = isSkill ? findSkill(p.text) : findByText(p.text, null, [p.kind]);
    if (known && !known.prov) return;
    let node = null; try { node = data.provisional({ kind: p.kind, text }); } catch (err) { console.error('[feed] provisional', err); }
    if (!node) return;
    provAt.set(p.kind, now);
    emitN('learn', { kind: isSkill ? 'skill' : learnKindOf(p.kind), text: p.text, nodes: [node], stars: [node.idx], topicCreated: false, topic: '',
      count: 1, provisional: true, pos: node.pos.slice(), cluster: node.cluster }, b);
    bindPolls();
    return;
  }
  if (p.mode === 'improved') {
    const node = findSkill(p.text);
    emitN('learn', { kind: 'skill-improved', text: p.text, nodes: node ? [node] : [], stars: node ? [node.idx] : [], topicCreated: false, topic: '',
      count: 1, provisional: false, pos: node ? node.pos.slice() : null, cluster: 'skills' }, b);
    return;
  }
  if (p.mode === 'closed') {
    const node = findByText(p.text, loopNodes(), ['loop']);
    if (node) closedLoops.set(node.idx, now);
    emitN('learn', { kind: 'loop-closed', text: p.text, nodes: node ? [node] : [], stars: node ? [node.idx] : [], topicCreated: false, topic: '',
      count: 1, provisional: false, pos: node ? node.pos.slice() : null, cluster: 'loops' }, b);
    bindPolls();
  }
}
function fireForget(b) {
  const p = b.pay;
  if (p.silent) { emitN('forget', { text: '', stars: p.stars, nodes: p.nodes || [], silent: true }, b); return; }
  const node = findByText(p.text, null, null);
  const now = nowE();
  if (node) recentForget.set(node.idx, now);
  emitN('forget', { text: p.text, stars: node ? [node.idx] : [], nodes: node ? [node] : [], silent: false }, b);
  bindPolls();
}

// rows that arrived by polling
const topicArr = new Map();       // topic -> [{t, idx}] arrivals inside the 4 s window
const genesis = new Map();        // topic -> {stars, at0, last, timer, centre, cluster}
function onPolled(res) {
  if (!res) return;
  const now = nowE();
  const byTopic = new Map();
  for (const x of res.added || []) {
    if (!x || x.bound || !x.node) continue;           // a provisional birth got its row: nothing new to show
    const row = x.row || {}, node = x.node, kind = row.kind || node.kind || 'fact';
    if (kind === 'project') {
      const t = str(row.topic || 'misc');
      let l = byTopic.get(t); if (!l) byTopic.set(t, l = []);
      l.push(x);
      continue;
    }
    // a row whose provisional twin is still unbound (texts differ): the birth already played
    const pa = provAt.get(kind); if (pa && now - pa < 6000 && kind !== 'day') { provAt.delete(kind); continue; }
    const out = { kind: learnKindOf(kind), text: str(node.text), nodes: x.created && x.created.length ? x.created : [node], stars: [node.idx], topicCreated: false,
      topic: str(row.topic), count: 1, provisional: false, pos: node.pos.slice(), cluster: node.cluster };
    if (kind === 'day') { out.quiet = true; if (/^finished:/i.test(str(node.text))) { for (const [idx, t] of closedLoops) if (now - t < 60000) { out.closedLoop = idx; break; } } }
    enqueue('learn', { immediate: true, ordered: false, keep: true, pay: { mode: 'polled', out } });
  }
  for (const [topic, list] of byTopic) projectRows(topic, list, now);
}
function projectRows(topic, list, now) {
  const g = genesis.get(topic);
  const stars = list.map(x => x.node.idx);
  if (g) {
    for (const i of stars) g.stars.push(i);
    g.last = now; clearTimeout(g.timer); g.timer = setTimeout(() => endGenesis(topic), GEN_END);
    enqueue('genesis', { immediate: true, ordered: false, keep: true, pay: { topic, phase: 'row', stars, centre: g.centre, cluster: g.cluster, count: g.stars.length } });
    return;
  }
  let hist = topicArr.get(topic); if (!hist) topicArr.set(topic, hist = []);
  for (let i = hist.length - 1; i >= 0; i--) if (now - hist[i].t > GEN_WIN) hist.splice(i, 1);
  for (const i of stars) hist.push({ t: now, idx: i });
  const topicNode = graph.byKey.get('topic:' + topic);
  const created = list.some(x => (x.created || []).some(n => n.kind === 'topic'));
  if (hist.length >= GEN_MIN) {
    const cl = topicNode ? graph.clusters.get(topicNode.cluster) : null;
    const centre = cl && cl.sector != null ? cl.center.slice() : topicNode ? topicNode.pos.slice() : (list[0].node.pos.slice());
    const all = hist.map(h => h.idx);
    const gg = { stars: all.slice(), at0: now, last: now, timer: 0, centre, cluster: topicNode ? topicNode.cluster : '' };
    genesis.set(topic, gg); topicArr.delete(topic);
    gg.timer = setTimeout(() => endGenesis(topic), GEN_END);
    gg.cap = setTimeout(() => endGenesis(topic), GEN_MAX);
    enqueue('genesis', { immediate: true, ordered: false, keep: true, pay: { topic, label: topicNode ? topicNode.label : data.pretty(topic), phase: 'start', stars: all, centre,
      cluster: gg.cluster, count: all.length, topicCreated: created || (cl && cl.sector != null) } });
    return;
  }
  for (const x of list) {
    const node = x.node;
    enqueue('learn', { immediate: true, ordered: false, keep: true, pay: { mode: 'polled', out: { kind: 'project', text: str(node.text), nodes: x.created && x.created.length ? x.created : [node],
      stars: [node.idx], topicCreated: (x.created || []).some(n => n.kind === 'topic'), topic, count: 1, provisional: false, pos: node.pos.slice(), cluster: node.cluster } } });
  }
}
function endGenesis(topic) {
  const g = genesis.get(topic); if (!g) return;
  clearTimeout(g.timer); clearTimeout(g.cap); genesis.delete(topic);
  enqueue('genesis', { immediate: true, ordered: false, keep: true, pay: { topic, phase: 'end', stars: g.stars, centre: g.centre, cluster: g.cluster, count: g.stars.length } });
}
function onGone(ev) {
  const nodes = arr(ev && ev.nodes), now = nowE();
  const fall = nodes.filter(n => n && n.idx >= 0 && !(recentForget.has(n.idx) && now - recentForget.get(n.idx) < 60000) && !(closedLoops.has(n.idx) && now - closedLoops.get(n.idx) < 60000));
  if (!fall.length) return;
  enqueue('forget', { immediate: true, ordered: false, keep: true, pay: { silent: true, stars: fall.map(n => n.idx), nodes: fall } });
}

/* ================================================================== polling the memory (new rows from any source) */
let pollP = null, pollAgain = false, pollTimer = 0;
function pollNewSafe() {
  if (pollP) { pollAgain = true; return pollP; }
  pollP = (async () => {
    try { onPolled(await data.pollNew()); }
    catch { /* bridge busy or down: try again later */ }
    finally { pollP = null; if (pollAgain) { pollAgain = false; pollNewSafe(); } }
  })();
  return pollP;
}
function bindPolls() {
  if (!live) return;
  pollNewSafe();
  setTimeout(pollNewSafe, 1500);
  setTimeout(pollNewSafe, 3000);
}
function schedulePoll() {
  clearTimeout(pollTimer);
  if (!live) return;
  pollTimer = setTimeout(() => { if (!hiddenNow()) pollNewSafe(); schedulePoll(); }, turn.open ? POLL_TURN : POLL_IDLE);
}

/* ================================================================== transport (F-1) */
let live = LIVE_DEFAULT;
let lastSeq = 0, lastAtMs = 0, lastRelayAt = 0, lastOrbAt = 0, lastEventAt = 0, lastOrbMode = null;
let selfPoll = false, transportGen = 0, actAbort = null, orbWakeFn = null;
const sleep = ms => new Promise(r => setTimeout(r, ms));
function wakeOrb() { if (orbWakeFn) orbWakeFn(); }
function orbActive() { return state.mode !== 'idle' || turn.open || nowE() - lastEventAt < 3000; }

/** Self-polling: the long-poll + the orb. ff = fast-forward first (boot); else catch up from the last seq seen. */
function startTransport(ff) {
  if (selfPoll) return;
  selfPoll = true;
  const gen = ++transportGen;
  activityLoop(gen, ff);
  orbLoop(gen);
}
function stopTransport() {
  if (!selfPoll) return;
  selfPoll = false; transportGen++;
  if (actAbort) { try { actAbort.abort(); } catch { /* gone */ } actAbort = null; }
  wakeOrb();
}
async function activityLoop(gen, ff) {
  let back = 500;
  if (ff || !lastSeq) {
    for (;;) {
      if (gen !== transportGen) return;
      try { const r = await api('/api/activity?since=999999999'); if (gen !== transportGen) return; lastSeq = Math.max(lastSeq, +r.seq || 0); break; }
      catch { await sleep(back); back = Math.min(back * 2, 5000); }
    }
  }
  back = 500;
  while (gen === transportGen) {
    actAbort = new AbortController();
    try {
      const r = await api('/api/activity?since=' + lastSeq + '&wait=8', { signal: actAbort.signal });
      if (gen !== transportGen) return;
      const seq = +r.seq || 0;
      if (seq < lastSeq) { lastSeq = seq; continue; }      // the bridge restarted: fast-forward, never replay
      for (const e of arr(r.events)) ingest(e, 'poll');
      if (seq > lastSeq) lastSeq = seq;
      back = 500;
    } catch {
      if (gen !== transportGen) return;
      await sleep(back); back = Math.min(back * 2, 5000);
    }
  }
}
async function orbLoop(gen) {
  while (gen === transportGen) {
    const t0 = nowE();
    try { const o = await api('/api/orb?panel=1'); if (gen !== transportGen) return; ingestOrb(o, 'poll'); }
    catch { /* bridge down: keep trying at the idle rate */ }
    if (gen !== transportGen) return;
    const period = orbActive() && !document.hidden ? ORB_ACTIVE_MS : ORB_IDLE_MS;
    const wait = Math.max(0, period - (nowE() - t0));
    if (wait > 0) await new Promise(res => { const tm = setTimeout(() => { orbWakeFn = null; res(); }, wait); orbWakeFn = () => { clearTimeout(tm); orbWakeFn = null; res(); }; });
  }
}

/* ================================================================== hidden / housekeeping */
let wasHidden = hiddenNow();
function onShow() {
  pump();                          // late beats jump to their end state, stale ones drop
  if (live) pollNewSafe();
}
function housekeeping() {
  const now = nowE();
  const hid = hiddenNow();
  if (wasHidden && !hid) onShow();
  wasHidden = hid;
  // embedded watchdog: the orb is busy (or never heard from) and the HUD has relayed nothing for 15 s
  if (EMBED && live && !selfPoll && now - lastRelayAt > WATCHDOG_MS && lastOrbMode !== 'idle') startTransport(false);
  // a self-poll in the panel that only sees idle while hidden can rest until the HUD speaks again
  if (EMBED && selfPoll && hid && lastOrbMode === 'idle' && now - lastEventAt > 60000 && now - lastRelayAt > 60000) stopTransport();
  // a turn with no orb at all ends on silence
  if (turn.open && now - lastOrbAt > 5000 && now - turn.lastActivity > 20000) closeTurn();
  for (const m of [recentForget, closedLoops, provAt]) for (const [k, t] of m) if (now - t > 120000) m.delete(k);
  for (const [k, r] of heldSynth) if (now - r.heldAt > HOLD_REUSE) heldSynth.delete(k);
}

/* ================================================================== turn log (F-2) + replay (U-5) */
export const turns = [];
let logCur = null, logSaveTimer = 0;
function logOpen() {
  if (replay.on || !STORE && !params.has('record')) { logCur = null; return; }
  logCur = { at: nowE(), b: [['S', 0]] };
  turns.push(logCur);
  while (turns.length > LOG_TURNS) turns.shift();
}
function logPush(rec) {
  if (!logCur || replay.on || logCur.b.length >= LOG_BEATS) return;
  rec.splice(1, 0, nowE() - logCur.at);
  logCur.b.push(rec);
}
function logClose() {
  if (!logCur) return;
  if (logCur.b.length < LOG_BEATS) logCur.b.push(['E', nowE() - logCur.at]);
  logCur = null;
  if (!STORE) return;
  clearTimeout(logSaveTimer);
  logSaveTimer = setTimeout(saveLog, 300);
}
function saveLog() {
  try {
    let s = JSON.stringify({ v: 1, turns });
    while (s.length > LOG_BYTES && turns.length > 1) { turns.shift(); s = JSON.stringify({ v: 1, turns }); }
    localStorage.setItem(LOG_KEY, s);
  } catch { /* storage full or blocked: the log lives this session only */ }
}
function loadLog() {
  if (!STORE) return;
  try {
    const d = JSON.parse(localStorage.getItem(LOG_KEY) || 'null');
    if (d && Array.isArray(d.turns)) for (const t of d.turns.slice(-LOG_TURNS)) if (t && Array.isArray(t.b) && typeof t.at === 'number') turns.push(t);
  } catch { /* corrupt: start fresh */ }
}

const replay = { on: false, timers: [], n: 0 };
/** Play the last turn again at real speed, gaps clipped to 400 ms. No births, no memory writes, no node creation. */
export function replayLast() {
  if (replay.on || turn.open) return false;
  const t = turns[turns.length - (logCur ? 2 : 1)];
  if (!t || !t.b || t.b.length < 2) return false;
  replay.on = true; replay.n++;
  const tag = 'r' + replay.n + ':';
  let clock = 0, prev = 0, ended = false;
  const say = (fn, at) => replay.timers.push(setTimeout(fn, at));
  for (const bt of t.b) {
    const dt = +bt[1] || 0;
    clock += Math.min(400, Math.max(0, dt - prev)); prev = dt;
    const k = bt[0];
    if (k === 'E') ended = true;
    say(() => replayBeat(bt, tag), clock);
  }
  if (!ended) say(() => replayBeat(['E', 0], tag), clock + 400);
  return true;
}
function replayBeat(bt, tag) {
  if (!replay.on) return;
  const k = bt[0], R = { replay: true };
  switch (k) {
    case 'S': state.turn = true; bus.emit('turn:start', { heard: '', ...R }); return;
    case 'E': state.turn = false; replay.on = false; replay.timers = []; bus.emit('turn:end', { ...R }); return;
    case 'W': return;
    case 'R': {
      const ids = arr(bt[2]), near = arr(bt[3]), days = arr(bt[4]);
      const dayStars = []; for (const d of days) { const n = graph.byKey.get('day:' + d); if (n) dayStars.push(n.idx); }
      bus.emit('recall', { ids, near, days, terms: [], stars: data.starsOfRows(ids), nearStars: data.starsOfRows(near), dayStars, t0: state.t, glyph: 'star', verb: 'Searching memory', object: '', ...R });
      return;
    }
    case 'T': {
      const [, , id, tool, kind, glyph, verb, object, keys, group] = bt;
      const nodes = arr(keys).map(key => graph.byKey.get(key)).filter(n => n && !n.gone);
      const targets = nodes.map(n => n.idx), primary = targets.length ? targets[0] : -1;
      bus.emit('step', { id: tag + id, tool, name: tool, region: nodes[0] ? nodes[0].cluster : '', kind, targets, primary, pos: posOf(primary), glyph, verb, object: str(object),
        temp: kind === 'memory' || kind === 'diary' || kind === 'loops' || kind === 'skill' ? 'warm' : 'cool', group: 'r' + group, gi: 0, input: '', t0: state.t, seq: 0, rand: mulberry32(hash32(id))(), ...R });
      return;
    }
    case 'D': bus.emit('step:done', { id: tag + bt[2], ok: !!bt[3], yield: +bt[4] || 0, results: { urls: [], hits: [], dates: [], rowIds: arr(bt[5]), stars: data.starsOfRows(arr(bt[5])), dayStars: [] }, output: '', t0: state.t, ...R }); return;
    case 'H': bus.emit('step:hold', { id: tag + bt[2], ...R }); return;
    case 'A': bus.emit('step:allow', { id: tag + bt[2], ...R }); return;
    case 'X': bus.emit('step:deny', { id: tag + bt[2], ...R }); return;
    default: return;
  }
}
function abortReplay() {
  if (!replay.on) return;
  for (const t of replay.timers) clearTimeout(t);
  replay.timers = []; replay.on = false; state.turn = false;
  bus.emit('turn:end', { replay: true, aborted: true });
}

/* ================================================================== api (FEED-DEMO, tests) */
/** Run a raw bridge-shaped event through the same conductor/resolver as live ones (at = now when missing).
 * opts.sync: play it this instant (no 60 ms delay, no spacing) -- for frame-driven fixtures. Pseudo kind 'heard' {text}. */
export function inject(raw, opts = {}) {
  if (!raw || typeof raw !== 'object') return;
  const e = Object.assign({}, raw);
  if (!(+e.at > 0)) e.at = nowE() / 1000;
  if (opts.sync) syncDepth++;
  try { ingest(e, 'inject'); } finally { if (opts.sync) { syncDepth--; } }
}
/** Run an orb frame ({mode, flavour, level, saying, game, pending, jobs, turn}). */
export function injectOrb(o) { ingestOrb(o, 'inject'); }
/** Pause / resume the live transport (relays are ignored while paused). */
export function setLive(on) {
  live = !!on;
  if (!live) { stopTransport(); clearTimeout(pollTimer); return; }
  lastRelayAt = nowE();
  if (!EMBED) startTransport(true);
  schedulePoll();
}
/** Clear turn state: pending beats, steps, fan-outs, the clock; an open turn ends. */
export function reset() {
  abortReplay();
  for (const b of queue) { b.fired = true; }
  compact();
  steps.clear(); heldSynth.clear(); fan = null; lastOrd.at = lastOrd.firedAt = 0;
  skewN = skewI = skewHigh = 0; skew = 0;
  for (const g of genesis.values()) { clearTimeout(g.timer); clearTimeout(g.cap); }
  genesis.clear(); topicArr.clear(); lastPage = null;
  if (turn.open) { turn.open = false; clearTimeout(turn.endTimer); state.turn = false; bus.emit('turn:end', { reset: true }); logClose(); }
  turn.idleSince = 0; lastSaying = '';
}
/** Recorder hook: fn('evt' | 'orb', raw) for every raw event and orb frame (live or injected). */
export function tap(fn) { tapFn = typeof fn === 'function' ? fn : null; }

export const API = { inject, injectOrb, setLive, reset, turns, tap, replayLast, describeTool, targetKey, parseArgs, firstWord, shortName, stats,
  get live() { return live; }, get skew() { return skew; }, get queued() { return queue.length; } };

/* ================================================================== init */
export async function init() {
  loadLog();
  // embedded: the HUD relays; standalone: poll for ourselves
  bus.on('host:evt', m => { if (m && m.e) ingest(m.e, 'relay'); });
  bus.on('host:orb', m => { if (m && m.o) ingestOrb(m.o, 'relay'); });
  bus.on('host:heard', m => { if (!live || !m) return; lastRelayAt = nowE(); onHeard(m.text); });
  bus.on('host:visible', m => { if (m && m.on) setTimeout(() => { wasHidden = false; onShow(); }, 0); });
  bus.on('graph:gone', onGone);
  bus.on('replay:request', () => replayLast());
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { wasHidden = false; onShow(); } });
  setInterval(housekeeping, 1000);
  lastRelayAt = nowE();
  if (live) {
    if (!EMBED) startTransport(true);
    schedulePoll();
  }
  window.__brain = Object.assign(window.__brain || {}, { feed: API });
  window.__brain.modules = Object.assign(window.__brain.modules || {}, { feed: API });
  if (params.get('selftest') === 'feed') {
    import('./feed-selftest.js').then(m => m.run(API)).catch(err => console.error('[feed] selftest', err));
  }
  if (DEMO || params.has('replay') || params.has('bench') || params.has('record')) {
    try {
      const m = await import('./feed-demo.js');
      if (m && typeof m.start === 'function') {
        // bounded: a demo that waits for the first frame must not hold up core.start()
        await Promise.race([Promise.resolve(m.start(API)), sleep(2000)]);
      }
    } catch (err) { console.warn('[feed] feed-demo.js unavailable', err); }
  }
}
