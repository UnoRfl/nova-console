# Nova Brain — module contract (v3)

Design spec: `SPEC.md` (read the sections for your module). This file is the API you build against.

## Serving
The bridge serves:
- `GET /brain?token=…[&embed=1][&demo=1][&replay=NAME&at=S][&bench=1][&reduced][&nolens][&intro]` → `brain.html` (token injected as `window.NOVA_TOKEN`)
- `GET /brain/app/*.js`, `GET /brain/lib/**.js` → ES modules, NO token (so: no memory text or secrets in any .js file, fixtures included)
- `GET /api/brain?since=ID` (token) → `{rows:[{id,kind,topic,source,at,text}], count, max, skills?, profile?}` (skills/profile only when since=0)
- `GET /api/brain/match?q=…` (token) → `{ids:[rowId…], near:[rowId…], terms:[…]}` — what her `recall` tool would get
- `GET /api/brain/trail?days=30` (token) → places she touched (files/sites/tools) aggregated from history
- `GET /api/activity?since=SEQ[&wait=S≤8]` (token) → `{seq, events:[{seq,at,kind,text,detail,id?,input?,output?,ids?,near?,days?,terms?,skill?}]}` (long-poll with wait)
- `GET /api/orb?panel=1` (token) → `{mode, flavour, level, saying, game, pending?, brain, jobs, turn, last:{user,reply}}`
- `POST /api/dev/activity` (token, ONLY on a NOVA_TEST bridge) → push a synthetic activity event `{kind,text,detail,...}`

Test bridge `http://127.0.0.1:8799` (NOVA_TEST, copy of her memory). Never POST `/api/ask` anywhere (real money).
Headless check: `python <scratchpad>/brain-dev/shot.py OUT.png --query "demo=1" --wait 10 --frames 3 [--steps "js1;;js2"] [--js "expr"]`.

## Files (one owner each — never edit a file you do not own)
| file | owner | role |
|---|---|---|
| `app/core.js` | lead | renderer, event clock, shaders, black hole, camera prims, overlay, picking, bus, governor |
| `app/data.js` | lead (layout agent) | memory → graph, deterministic incremental layout, places, provisional births |
| `app/main.js` | lead | boot: data.load → fx → director → ui → feed → core.start |
| `app/fx.js` (+ `app/fx-*.js`) | FX | gaze legs/heads/ribbons, recall reveal, fronts, lamps+focus driving, citations, outer world, jets, births, genesis, endings |
| `app/director.js` | DIRECTOR | spring camera rig, handoff composition, shot planner, moods/time/voice, set pieces |
| `app/ui.js` + `brain.html` | UI | words + karaoke, trail, pop-up cards + letterbox, labels + detail card, dock, font |
| `app/feed.js` (+ `app/feed-*.js`) | FEED | transport, conductor, resolver → normalized events, demo/replay/record/bench |
| `../hud.html` | HUD | the panel transform, match cut, relay & hygiene |

Every module exports `async function init({core, data})` and may import `./core.js` / `./data.js` directly.
Modules talk ONLY through the bus + the core/data APIs below. Expose anything another module needs as a bus event or on `window.__brain.modules.<name>`.

## core.js exports
Constants: `THREE, TAU, FIXED_DT (?fixed → 1/60), RS (30), BH_R (510), DISK_IN (78), DISK_OUT (285), COLORS {WHITE, GOLD, HOT, COOL, AMBER, EMBER, VIOLET}, ST, SHAPE, params, TOKEN, EMBED, DEMO, REDUCED, HOME_DIR, HOME_DIST (2300)`.
- `ST` star states: `NONE 0, USED 1 (prm=rank), TARGET 2 (prm 0 warm / 1 cool), TENTATIVE 3, BIRTH 4 (tOn = ignition time; size spring + colour ramp built in), FALLING 5 (fx moves it; shader reddens/fades over 2.4 s, stretches toward the hole), OUTSIDE 6 (prm 1 = page opened → ring fills), ACTION 7, CONSIDERED 8 (hollow ring, 600 ms), CITED 9 (pulses with state.drive), LOOP 10 (C-ring, gap faces the hole; tOff = closed-at → gap closes, flash, fade), PENDING 11 (amber breath)`.
- `SHAPE`: `STAR 0, SQUARE 1, RING 2, TRIANGLE 3, HEXAGON 4`.
- Shader clock: `state.t` (seconds, real time). All event times use it. Envelopes in the star shader: `on = smoothstep(0,.18,t−tOn)`, `flash = e^(−6(t−tOn))`, `warm = on·e^(−max(t−tOff,0)/1.6)`, `after = .35·on·e^(−max(t−tOff,0)/45)`.
- `api(path)`, `bus.on/off/emit` (`'*'` = all), `toHost(msg)`, `wake()` (resume rendering now), `onFrame(fn(dt,t,now), order)` (rendered frames only; no allocation inside).
- `state`: `{mode, flavour, level, drive, game, turn, embed, insetRight, visible, paused, focused, userActiveAt, activeAt, now, t, dt, frame, ta (ambient clock), ts (time scale), introDone, director, viewOff:[x,y], viewOffOwned, bhPx:[x,y,rPx], tier, quality, gpuMs, vsync, fps}`.
  - Set `state.director = true` to own the camera (core then skips its cam glides + autoRotate restarts). Set `state.viewOffOwned = true` and write `state.viewOff` each frame to own the view offset (else core centres left of `insetRight`).
- `pulse` additive one-frame impulses `{ring, ringSharp, bright, expo, glow, dust, house}`: add inside `onFrame` (e.g. `pulse.ring += .25*env`); core applies `final = eased look × (1 + pulse)` after all frame hooks and zeroes them. Use this for transient kicks so you never fight the director's moods.
- `look` (core eases toward these): `dim, house (0..1 house lights down), dust (gain), ts (ambient time scale, may be negative), exposure, dustSpin, bh:{spin, bright, ring (gain), glow, tempExp (1.35), ringSharp (16), ringR (1.53), expo, tintA}, tintCol:[r,g,b], lensSky, comets`.
- `fxu` shared uniforms (write `.value`): `uT` (auto), `uHouse` (auto from look.house), `uSonar` Vec4(t0, amp, on), `uFrontC` Vec4(centre, t0), `uFrontN` Vec4(dir, extent; 0 = off), `uLight[4]` Vec4(pos, intensity), `uLightCol[4]` Vec3, `uFocus` Vec2(focus view-depth, aperture 0..1), `uWell[4]` Vec4(pos, depth u), `uDustFx[4]` Vec4(pos, t0) + `uDustFxP[4]` Vec4(kind 1 lean-in | 2 shove-ring, radius, strength u, duration s), `uHov[4]` (star ids drawn ×2), `uDrive` (auto from state.drive), `uEvent` (the clock texture).
- `stars`: `CAP 12000, count, pos/size/bright/color/rand/spike/shape (typed arrays), active (Set)`; `add({pos,size,bright,color,spike,shape,rand,born})→idx` (`born:true` = BIRTH now, or a number = BIRTH at that time); `setPos(i,x,y,z)` (moves its lines), `setBright, setSize, setColor(i,r,g,b), setShape, setSpike`; `setState(i, st, tOn=state.t, tOff=1e9, prm=0)`, `stateOf(i), tOnOf, tOffOf, paramOf`, `release(i, t=state.t)` (start cooling), `ignite(i)` / `cool(i)` / `coolAll()` (compat), `isHot(i), hotList(), hotCount(), hotAt(k)`. `bright=0` hides a star. One event-texture upload per frame, only when something changed.
- `edges`: `add(a,b,alpha,{drawOn:t, from:starIdx})→e` (draws in over 420 ms from `from`), `drawOn(e,t,fromStar)`, `unthread(e,t,dyingStar)` (500 ms), `setAlpha(e,a)`, `of(i)→[e]`, `ends(e)`, `endA(e)`, `endB(e)`. Lines glow automatically from the event clock of their two stars.
- `bh`: `{mesh, material, uniforms, setLens}`; event uniforms you may drive: `uFeed` (0..0.15 gold intake), `uWince` (0..1 fault), `uHot` Vec4(screen angle rad, t0, gain, duration) (ring hot spot), `uBand` Vec2(t0, on) (remember band, 1.2 s), `uIgnite` (t0 of the wake ignition, −1 off), `uEmit` Vec4(pos/RS xyz, strength; w=0 off) + `uEmitC` Vec3 (a light the ray-march sees → lensed arcs of the gaze head), `uBHpx` (auto). Mood values go through `look.bh`.
- `voice`: `write(v 0..1)` at ~10 Hz → the disk's voice history (rings travel outward). `state.drive` = smoothed drive (director owns it).
- `finish.uniforms`: `uLift (≤.08), uIris (0 → +.8 vignette), uGlass Vec4(x0,y0,x1,y1 css px) + uGlassK (0..1 frost), uSpike Vec4(x,y css px, long-arm px, angle rad) + uSpikeA + uSpikeCol, uEdge Vec4(x,y css px, strength)`.
- `camera, controls, scene, renderer, composer, bloom, dust {points, material}, sky, PV, project(x,y,z,out)→bool` (css px + depth), `holeRadiusPx(d)`, `minHoleDistance()` (coverage rule: hole disc ≤ 30 % of screen, ≥ 640 u).
- `cam`: `flyTo(vec3, dist, ms, dir?, ease?, onDone?)`, `frame(points,{pad,min,max,dur,include0,dir,easeName})`, `home(ms)`, `stop()`, `flying`, `distance`, `userRecent(ms)`. (Used when no director is present; the director may reuse it.)
- `easings`: `linear, inOutCubic, outCubic, inCubic, outQuart, inQuad, outQuad, outExpo, inOutQuint, outBack, inOutSine, EASE` (cubic-bezier(.2,.8,.2,1) LUT); `bezierLUT(x1,y1,x2,y2,n=64)`; `Spring(x,w,z)` (`.target`, `.step(dt)` at 120 Hz substeps, `.snap(x)`); `springResponse(t,w,z)`; `mulberry32(seed)`, `hash01(str)`, `rnd()`, `gauss()`.
- `overlay.add(el,{idx | pos:[x,y,z] | pos:()=>[x,y,z], dx, dy, near, far, opacity:()=>n, anchor:'center'|'left'|'none'})→{el, remove()}` (Typed OM, writes only on change).
- `pointer`, `pick(mx,my,filter?)→idx`. Bus: `'hover' {idx}`, `'click' {idx,x,y}`, `'click:empty'`, `'user:grab'` (pointerdown/wheel), `'resize'`, `'start'`, `'intro:done'`, `'clock:rebase' {shift}` (subtract shift from any absolute times you keep).
- Host → brain postMessages arrive on the bus as `'host:<name>'` (`visible {on}`, `inset {right}`, plus whatever the HUD sends: `wake`, `glide`, `collapse`, `retarget`, `evt`, `orb`, `card`, `hover`, …). Core itself handles `visible` and `inset`.
- `setGame(on)`, `setLensSky(on)`, `perf {gpuSamples, cpuSamples, frameTimes, timerQuery}`, `debug {fps, PR, quality, gpuMs, tier}`, `pixelRatio()`.

## data.js exports (see the file for details)
`graph {nodes, byKey, byRow, starNode, clusters, maxRow, total, skills, profile}`; node `{idx, key, type:'main'|'frag', kind, cluster, label, text, rowId, at, parent, kids[], members[], pos[3], dynamic}`;
`CLUSTERS, SHAPES, clusterOfTopic, pretty, shortText, normText, normPath, createNode, addRow, ensure, fileNode, folderNode, webNode, toolNode, skillNode, historyNode, seedPlaces, hintTopic, nodeOf, starsOfRows, neighbours, matchRows, searchLocal, refreshCounts, load, pollNew (→{added, forgotten, gone}), slotFor, provisional, diaryArm, snapshot`.
Data bus events: `graph:loaded`, `graph:node {node}`, `graph:bind {node, revived}` (an arriving row bound to a provisional / returning star), `graph:gone {nodes}` (rows deleted: FX should let them fall), `graph:cluster {cluster}` (a new topic sector), `graph:diary` (day rollover glide).
Memory rows are slotted by their TEXT (first 80 alphanumerics), so a provisional birth binds to its row only if the event text matches the start of the row text.

## Normalized events v2 (FEED emits; everyone else listens)
| event | payload |
|---|---|
| `mode` | `{mode, prev}` |
| `orb` | `{mode, flavour, drive, level, jobs, game, mic, pending}` (~10 Hz while active) |
| `turn:start` | `{heard}` · `heard` `{text}` · `turn:end` `{}` |
| `recall` | `{ids, near, days, terms, stars:[idx], nearStars:[idx], dayStars:[idx], t0}` |
| `step` | `{id, tool, region, kind, targets:[idx], primary, temp:'warm'|'cool', glyph, verb, object, group, input, t0}` |
| `step:done` | `{id, ok, yield (0..1), results:{urls, hits, dates}, output}` |
| `step:hold` / `step:allow` / `step:deny` | `{id}` (approval) |
| `say` `{text}` · `speak:chunk` `{text}` | her words (a chunk = what she is saying now) |
| `learn` | `{kind, text, nodes, stars, topicCreated, topic, count, provisional}` |
| `forget` | `{text, stars, silent}` |
| `genesis` | `{topic, phase:'start'|'row'|'end', stars}` |

Performance: ≤ 3 ms GPU/frame at 1080p total (core alone ≈ 0.3 ms today); zero per-frame allocations; uploads only on events; no new materials/geometries after boot (pool at init); DOM writes only on change; respect `state.visible`.
Taste: monochrome cinematic base; colours carry meaning only (SPEC 4.0); Inter-style small type; minimal words; no neon, no pixel art, no HUD clutter, no counters/numbers in 3D, no spinning rings; vary shapes.
