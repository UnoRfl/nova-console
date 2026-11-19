# Nova Brain v3 · Final build spec

There are 35 must-build items: FX 11, DIRECTOR 5, UI 5, FEED 5, HUD 3, CORE 6. Owners are listed in `~/.nova/brain/CONTRACT.md`, and the lead owns all bridge changes. Everything here extends the current v3 `core.js` and `data.js`. The v2 base look stays frozen: bloom 0.7/0.42/0.7, ACES, the disk palette, grain, vignette and star spikes. Anything that changes how the black hole looks goes behind the A/B toggle.

## 1. North star

Nova's mind is a real place: a monochrome, Interstellar-quality galaxy around her black hole, where nothing moves unless she does and every light on screen is a true event of her turn, landing within a frame of it. The signature moment plays on every question: as she says "One moment, Sir", the four-arm star from her panel peels off Gargantua's photon ring, slingshots behind her with its light bending into Einstein arcs, and lands on the exact memory, file or page she is reading, while the memories she was handed pour gold into her disk and the one she is quoting pulses with her voice. Gold always means "in her head right now", the orb and the black hole behave as one creature, and the whole view costs under 3 ms a frame beside Valorant.

## 2. The turn, beat by beat

The panel is open in orb view. Uno asks: "Nova, what did I decide about the Orbit pitch deck, and look up the latest on React 19". T is milliseconds after the wake word, as seen on screen. Each event plays about 60 ms after the bridge stamps it.

- **T 0 · `listening` (wake)**
  - **HUD:** the reactor flares as it does today.
  - **Brain appears:** the brain was preloaded and paused; it resumes on the same frame. Its opacity goes 0→1 over 280 ms (outCubic) while the reactor's spikes fade 1→0 on the same curve.
  - **Framing:** the look target snaps to the hole; nobody sees the snap because the canvas is still at opacity 0. The view offset puts the hole exactly under the orb.
  - **Spikes:** the finish pass draws spikes on the hole with the reactor's live length and angle, cross-fading from violet to (1,.94,.86). This is the only place purple ever touches the brain.
  - **Disk:** it ignites from the inner edge outward (800 ms, ring first).
  - **Listening mood** (springs, ω 6): spin 0.8, ring sharpness 16→22, ring gain 1.10, disk exposure 1.08, ambient time scale uTS→0.55 (τ 350 ms).
  - **Camera (LEAN_IN):** distance 2300→1978 and elevation 11.3°→6°, settling in about 1.1 s.
  - **False wake:** if no `heard` arrives within 400 ms of listening ending, the brain fades out over 280 ms and pauses again.
- **T 3400 · `heard` + `thinking`**
  - **HUD:** the column winds up for 100 ms (translateX −6 px, scale .985, cubic-bezier(.4,0,.6,1)), then slides right over 620 ms (cubic-bezier(.2,.8,.2,1)). It posts `glide` with t0.
  - **Brain:** the view offset follows the same 64-entry easing table, keyed to the same t0, so the hole glides into the centre of the free area in lockstep with the column. The finish pass frosts the column rectangle on that same table. The hole's spikes fade over 400 ms starting at T 4520.
  - **Words:** Uno's question appears top-left (240 ms outCubic, 4 px rise, 12.5 px, 62% ink).
  - **Thinking mood:** spin 2.0, temperature exponent 1.35→1.15 so the inner disk runs hotter, ring gain breathing ±8% at 0.8 Hz, uTS→1.
- **T 3460 · `recall`** (5 handed ids, 9 near ids, search terms)
  - **Trail row 1** wipes in over 280 ms: [star] Searching memory · orbit pitch deck.
  - **Search terms:** six terms lift off the question 40 ms apart and fall into the hole (700 ms inCubic, scale 1→.4). Each adds +3% ring gain, capped at +15%. Nothing waits for them.
  - **T 3460–3580 pinch:** ring sharpness ×1.6 and gain +25% (inQuad).
  - **T 3580 sonar:** the front grows R 0→2000 u over 1100 ms (outQuart). It is visible only as dust brightening and stars swelling 20% as it passes.
  - **T 3600 near-misses:** the 9 near stars get faint hollow rings (alpha .25) that are gone by T 4200.
  - **T 3733 ignition:** as the front crosses the Orbit cluster (r≈900), the 5 handed rows ignite gold. Rank 1 is brightest with longer spikes; brightness peaks at 3.3× at +70 ms and settles to 1.7×.
  - **T 3700–4150 house lights:** stars not involved drop to 51% and dust to 60%. Lamp 0 opens, gold, on the centroid of the hits.
  - **T 3873 labels:** labels for ranks 1–3 rise in, with no numbers.
  - **Camera:** the hits are already in frame, so it only drifts.
- **T 4680–6490 · gold pours:** ranks 1–5 each pour a 96-particle stream that spirals into the disk. They start 140 ms apart and take about 1250 ms each, heating from white to gold. Each arrival warms the disk by 3%, capped at 15%.
- **T 6150 · `say` "Of course, Sir. One moment."** Her sentence appears (Inter 300, about 24 px) at 38% ink. Words turn white as she speaks them, once the voice level passes .04.
- **T 6200/6215 · Read(orbit-pitch-deck.md) + web_search("React 19 latest release")**, one fan-out beat:
  - **Trail rows 2 and 3**, 120 ms apart: [square] Opening · orbit-pitch-deck.md and [ring] Searching the web · react 19 latest release.
  - **Leg A slingshots:** the white-gold four-arm head leaves the ring and swings 180° behind the hole at 51 u from T 6200 to 6520. The ray-march draws its light as Einstein arcs, and the ring gets a +30% hot spot at the departure angle for 400 ms.
  - **Leg B** leaves at T 6290 as a cool silver ribbon with an 850 ms flight.
  - **Camera (GROUP):** frames the hole, Orbit and the Web query node together. Azimuth changes at most 35°, settling in about 1.3 s.
  - **Lamps 2 and 3** ride the two heads.
- **T 6600 · A done** (2,400 chars), before its head has arrived:
  - The remaining flight is re-timed to 250 ms, so the head lands at T 6850 on the Orbit Pitch Deck star: it snaps 45°→0° in 180 ms (outBack) and the star flashes ×1.6.
  - It touches for 120 ms, then a bead (size ×3.5 for the amount read) rides home from T 6970 to 7420 (inCubic). On arrival the ring gains +25% for 220 ms and one soft ring rolls out through the disk.
  - Trail row 2 gets its tick at T 6970.
- **T 7140 · B locks on** the query node. While it runs, dashes of light flow home along the tether.
- **T 8400 · B done** (5 URLs): five result rings fall into the Web region (600 ms outCubic, 60 ms apart), the bead returns from T 8400 to 8850, and row 3 ticks.
- **T 8650 · web_open(react.dev)**: row 4 [ring] Reading · react.dev. Leg C hops 420 ms from the query node and lands at T 9070 as its ring lands. The ring fills over 350 ms and lamp 0 moves there in cool white. The camera doesn't move: the target is in frame and the current shot hasn't been held long enough.
- **T 10400 · C done:** its bead returns home.
- **T 10600 `say` answer · T 10900 speaking**
  - **Rest:** nothing new starts between T 10850 and 11050.
  - **Camera (REVEAL)** from T 11050: frames the hole plus the 3 stops, picks the azimuth within ±30° that spreads them widest, elevation 18°, ω 2.4.
  - **Recap glint:** from T 11200 to 12700 one glint runs the route A→B→C (inOutSine). This shows the order without numbers.
  - **Voice:** syllable rings travel outward through the disk, taking about 2.3 s to cross. Ring gain is 1+.25·drive, and the ring thickens with her voice.
  - **Words:** the old line slides up 10 px and fades; the answer reveals word by word as she speaks.
  - **Citations:** "You settled on scroll, intro, then the manual deck, Sir" cites the Orbit Pitch Deck star (score 5). It pulses ×(1.3+1.2·drive), gets lamp 0, and its trail row brightens. "React 19 is stable…" cites react.dev (score 2). Used stars that are not cited pulse faintly.
- **T 19000 · `turn:end`**
  - Gold cools to white over about 4 s, with a white afterglow that fades over about 2 min.
  - The route stays as a long exposure with about a 62 s half-life.
  - House lights come back up from T 21500 over 1.8 s.
  - The camera eases home to ESTABLISH at T 22500 over about 2.2 s.
  - Trail rows fade at T 25000.
  - **Collapse at T 29000** (10 s idle, pointer not over the brain): the column slides back over 700 ms (inOutCubic) while the hole glides back under the orb. Spikes fade in on the hole and hand back to the reactor (white-gold→violet). The iframe fades out over the last 280 ms, the core glows +30% for 180 ms, and rendering stops.

## 3. Learning moments

**(a) One fact: "remember my main monitor runs at 165 Hz now."** T is the "Remembered …" event.

- **T 0:** the new star's slot in You is computed from the event text, and the animation starts immediately. The database row is attached when it arrives (within 3 s).
  - **Remember mood:** a gold band rolls once around the disk (1.2 s) and exposure gets +0.2 that decays with τ 400 ms.
  - **Camera (BIRTH):** target = mix(hole, slot, .6), distance at least 1700.
- **T 150–1250 · spark:** a gold spark with 48 motes leaves the disk's bright side at r 135. It flows out along a dust arm's spiral and curves onto the slot (inOutCubic).
- **T 1150–1330 · gather:** the motes gather into a point while dust within 140 u leans in.
- **T 1330 · ignition:**
  - Size springs in with a 23% overshoot (ω 20, ζ .42). Spikes unfold from +60 ms (600 ms outExpo). Brightness 3→1.25 (τ 320 ms).
  - The photon ring dips to 70% and recovers over 500 ms.
  - A faint ripple pushes 320 u through the dust.
  - The pop-up card grows out of the star and settles top-centre (520 ms): "New memory" / "Main monitor runs at 165 Hz".
- **T 1580–2300 · links:** lines draw to the 3 nearest You memories, 130 ms apart; each blinks ×1.8 as it connects.
- **Settle:** gold cools to white (τ 2.6 s). The card folds back into the star at T 5850. The camera returns at T 6300.

**(b) Skill learned: "Learned how to open a named app and set its volume"** (4 steps).

- Same spark as (a), aimed at Skills (r≈900). The camera uses FOCUS at 900 u from the outward side.
- **T 1330:** the hexagon skill star ignites and its six spikes unfold 50 ms apart.
- **T 1650–2010:** four step stars, 18 u apart on an arc, ignite 120 ms apart as each link reaches them.
- **Card:** "Learned skill" / "Open an app and set its volume".
- **Later:**
  - `read_skill` relights the chain in order and turns the spikes 30° (300 ms outBack).
  - "Improved skill" adds one link and a permanent +8% brightness.

**(c) A 40-row repo study: "study the Tira repo".** `tira` matches no known family, so it opens a new sector (C-6).

- **T 0–600:** letterbox bars ease in to 9% of the height each (outCubic) and uTS→0.3. The camera frames the hole and the empty slot, three-quarter view at 20° elevation, ω 2.4.
- **T 300–1900:** a gold filament unspools along a 0.75-turn spiral from the disk to the slot, lighting the dust it passes. The look target trails the head over the last third.
- **T 1900–4300:** the 40 stars ignite in the order she read them. The gaps between ignitions shrink from 90 to 40 ms, and each star overshoots 16% with a 3× flash. The tree of lines draws itself as each line's second end lights.
- **T 2000–5600:** a hero orbit of 40° around the slot (inOutSine, peak 17.5°/s). It ends with Gargantua behind the new constellation.
- **T 4600–5200:** the figure pulses ×1.5 and back while cooling from gold to white. "Tira" assembles letter by letter (900 ms outExpo), and the card reads "New topic" / "Tira · 40" with the count rolling up from 0.
- **T 5600–7400:** a slow pull-back to the wide shot. The dust now leans permanently toward the new constellation (well depth 0→18 u).
- **T 6400–6900:** letterbox out, uTS→1.
- **Variants:**
  - Panel unfocused or a game running: a 2.0 s version with no letterbox and no camera move.
  - A click or a new question collapses whatever remains to 600 ms.
  - More rows of the same topic within 60 s ignite through the still-open filament, and the finale plays again 3 s after the last one.

## 4. Module requirements

### 4.0 Shared constants

- **World:**
  - RS=30 u. The ray-march sphere is 17 RS = 510 u.
  - The disk runs from 78 to 285 u.
  - Legs depart from the photon ring at 78 u (the edge of the visible shadow).
  - FOV 42°. Home: 2300 u at 11.3° elevation, idle drift 1.08°/s.
- **Composition:** column width Wc = `state.insetRight` (396 px). The hole sits at the centre of the free area (view offset x = Wc/2).
- **Coverage rule:** whenever the hole is in view, the projected disc of the ray-march sphere, r_px = (H/2)·tan(asin(510/d))/tan(fov/2), must satisfy π·r_px² ≤ 0.30·W·H. That means about 1700 u with the hole centred. The camera also stays at least 640 u from the hole.
- **Colours** carry meaning only:
  - WHITE (1,.97,.93) by default.
  - GOLD (1,.85,.64) means in her head now.
  - HOT GOLD (1,.66,.32) is matter falling into the disk.
  - COOL (.80,.88,1.0) is the outside world.
  - AMBER (1,.72,.35) is used only while waiting for approval.
  - EMBER (.95,.52,.40) marks an error, for 400 ms only.
  - Violet appears only during the handoff.
  - Saturation stays ≤ 12%, with no hue per cluster or per tool.
- **Clocks:**
  - `uT = state.t`. Event effects are f(uT−t0) and always run in real time.
  - Ambient clocks (disk, dust, comets, sky) advance by dt·uTS.
- **Easings:** `core.easings` adds outCubic, inCubic, outQuart, inQuad, outQuad and EASE (cubic-bezier(.2,.8,.2,1) as a 64-entry table). Springs follow x'' = ω²(x*−x) − 2ζω·x'.
- **Glyphs** (map and trail, all white): memory = four-point star, file = square, web = ring, action = triangle, skill = hexagon.

### 4.1 FX (fx.js)

1. **X-1 · The gaze: flight and head.**
   - **Head:** a 30 px white-gold four-arm star, drawn as an astroid SDF |x|^.66+|y|^.66=r^.66 (AA via fwidth) plus an exp halo. Pools: 8 heads, 32 legs.
   - **Path texture:** RGBA32F, 64×32 (32 KB). Each row is one leg: 64 Catmull-Rom samples (xyz plus arc fraction), filled once per leg when the event arrives. Shaders read 4 texels per point.
   - **Leg shape:**
     - P0 is the previous waypoint, or else the photon-ring point: hole + 78·normalize(target − (target·v̂)v̂), where v̂ is the view direction toward the hole.
     - P1 and P2 = lerp(P0,P3,⅓,⅔) + up·.12L − n̂mid·.35L·clamp(900/|mid|,0,1.5).
     - If a chord between waypoints passes within 600 u of the hole, it is routed around on the 1.3× circle.
   - **Flight:**
     - T = clamp(.30 + L/2600, .42, .85) s, ×0.7 when 2 or more legs are queued (minimum .34 s). The curve is inOutCubic and the head spins .8 rad/s.
     - Lock-on: the head snaps 45°→0° in 180 ms (outBack) and the target flashes ×1.6.
     - While the tool runs, the head rests on the target and breathes ±10% at 1.2 Hz (no circling). After 3 s its halo grows .04/s, up to +.3.
   - **Slingshot**, on each turn's first leg only:
     - For 320 ms the head swings 180° behind the hole at 51 u.
     - Until it is 510 u out, fx writes `uEmit`=(pos/RS, 1.2) every frame so the ray-march renders the lensed arcs.
     - The sprite's alpha is smoothstep(470, 510, |pos|), so it is never drawn twice.
   - **Parallel calls** launch 90 ms apart, bending to alternate sides.
   - **start_job:** the head parks on a 620 u orbit tilted 12° (.35 rad/s, at most 4 satellites). When the job ends it spirals into the disk over 900 ms.
   - **Unknown tools:** a 260 u arc to the Actions anchor, never to a memory.
   - **Test:** `?replay=orbit-turn&at=6.85` puts the head within 3 px of the deck star. At 6.35 there is no sprite but `uEmit.w`>0. The geometry count stays constant.
2. **X-2 · Tethers and route.**
   - **Ribbon:** one instanced ribbon (129×2 vertices, 32 instances, 1 draw). It renders after the black hole with depthTest on and depthWrite off, so the hole's own depth hides legs passing behind it.
     - Screen-constant width: w = wpx·(−mv.z)·2/(uH·P[1][1]), with wpx 1.2 on the body, 2.4 at the head, never below 1 px.
     - Profile across the ribbon: a = e^(−6s²) + .18e^(−1.5s²).
     - Colour: warm (1,.86,.62) for memory legs, COOL for outside legs.
     - a ×= 1/(1 + .15·activeLegs).
   - **Tail:** smoothstep(h−.35, h, u), then the thread settles at .22.
   - **Running:** dashes flow home, d = fract(u·L/26 − uT·1.6), adding .45·smoothstep(0,.12,d)·smoothstep(.42,.12,d). They fade out below 1.3 px wide.
   - **Done (ok):**
     - A bead travels u = 1 − inCubic((uT−tDone)/.45), with width ×(1 + 2.5·yield), where yield = clamp(log10(chars+1)/3.4).
     - On arrival it emits `absorb`: ring +25% for 220 ms and a .5 voice impulse. At most one absorb per 400 ms.
   - **Error:**
     - The leg breaks mid-way and both halves recoil: P += tangent·sign(u−.5)·45·outBack(k) over 400 ms.
     - EMBER tint for 400 ms; the target dims to 30% for 2 s.
   - **Approval:**
     - The head parks at 85% of the leg with static dashes and an AMBER halo breathing at 0.5 Hz in real time.
     - Allow completes it in 260 ms (outBack). Deny retracts it into the hole over 600 ms (inCubic).
   - **Timing guarantee:** "done" shows no earlier than arrival + 250 ms. A tool that finishes early re-times the remaining flight to ≤ 250 ms, touches for 120 ms, then returns. The done visual is never more than 450 ms after the real event.
   - **Long exposure:**
     - At turn end, legs cool to white over 4 s and fade as .55·e^(−Δt/90).
     - Older turns are capped at .12, and slots recycle oldest first.
   - **Recap glint:**
     - Each leg stores the fraction of the route it covers (s0, s1).
     - uRecap runs 0→1 over 1500 ms (inOutSine), starting 300 ms after speaking begins.
     - It adds 1.2·e^(−((mix(s0,s1,u) − uRecap)/.04)²).
     - No numbers in 3D.
   - **Test:** a 0.2 s tool still shows launch, arrival and bead. An error leaves nothing visible 400 ms later. 24 legs cost ≤ .04 ms.
3. **X-3 · Recall reveal.**
   - **Pinch:** 120 ms inQuad, ring sharpness ×1.6 and gain +25%.
   - **Sonar** (no geometry):
     - k = (uT−t0)/1.1, R = 2000(1−(1−k)⁴), width w = 60+140k.
     - shell = e^(−((d−R)/w)²)·(1−.7k)·amp.
     - Dust brightness ×(1+.35·shell) and position nudge p += n̂·6·shell. Star size ×(1+.2·shell).
     - amp = .5 if the previous turn started less than 10 s ago.
   - **Used memories** (state 1):
     - tOn = t0 + 1.1(1 − (1 − min(d/2000, 1))^.25), so each lights exactly as the front crosses it.
     - Brightness I(u) = mix(1, 1.7, 1−e^(−u/.04)) + 2.6e^(−u/.18)(1−e^(−u/.03)), times max(.6, 1−.08(rank−1)).
     - Size ×(1.35 − .07·rank). Rank 1 gets spikes ×1.6. GOLD tint at .35.
   - **Considered** (near ids, up to 12, state 8): a ring at .25·e^(−((d−.55)·28)²) for 600 ms.
   - **Days** in `days` light as used.
   - **House lights:** `look.dim`→.7 and dust ×.6 over 450 ms (outCubic). They restore 2.5 s after speaking ends, over 1.8 s.
   - **Pours** (one-shot, never continuous rivers):
     - The top 6 by rank, 140 ms apart, starting at t0+1.1 s. 8 streams × 96 points.
     - s = clamp((t−t0)/dur − .18u); r = mix(r0, 87, s³); θ = θ0 + 2.2·ln(r0/r); y = y0(1−s)².
     - Colour WHITE→HOT GOLD, alpha .6(1−u)², size 2.2→1.2 px.
     - dur = clamp(1100·√(r0/700), 900, 1400).
     - Each arrival adds uFeed += .03 (cap .15, τ 1.4 s).
   - **Empty recall:** the wave runs at .5 amplitude, exposure drops to .9 for 600 ms, and nothing lights.
   - **Ids not loaded yet:** fetch `since=max` first, then ignite them without wave timing.
   - **Test:** texel writes equal handed + near ids exactly. An empty recall writes zero texels.
4. **X-4 · Search fronts** (`recall(query)`, `search_past`, Grep, Glob).
   - **The front** is a straight sweep shown only through stars and dust lighting up.
     - n = normalize(camRight·.85 + up·.15), fixed at launch so it reads left to right.
     - s = mix(−ext, ext, inOutSine((uT−t0)/.9)).
     - front = e^(−((dot(p−c, n) − s)/18)²)·step(|p−c|, 1.2·ext).
     - Stars +1.4·front, dust +.5·front.
   - **recall(query):** the `/api/brain/match` candidates light as tentative (state 3) at their crossing time. Ids confirmed by the output turn gold; the rest fade over 600 ms.
   - **search_past:** the front runs along the diary arm. Matched days glow at .5 for the rest of the turn.
   - **Grep/Glob:** the front sweeps the folder's subtree. `path:line` matches (up to 6) spark 60 ms apart. At most 24 candidates light.
   - **Test:** sparks appear in sweep order.
5. **X-5 · Lamps and rack focus.**
   - **Lamps:** `uLight[4]` and `uLightCol[4]`.
     - Slots: subject, the hole (GOLD .25 during turns), head A, and head B or a lag light 250 ms behind head A.
     - Falloff L = Σ I·R²/(R²+d²) with R = 220.
     - Dust ×(1 + min(L, 1)); stars ×(1 + .8·min(L, 1)), tinted .25·min(L, 1) toward the lamp. Lines with both ends inside R ×(1 + 2L).
     - Colour follows meaning, never the panel's flavour colour. Attack 180 ms (outCubic), release τ 1.4 s. No glow cards.
   - **Rack focus** (stars only):
     - uFocusDist follows the subject on a spring (ω 6).
     - Aperture is 1 for FOCUS, BIRTH and the hero orbit, and 0 otherwise (400 ms in, 800 ms out).
     - Blur coc = clamp(ap·|1 − fd/viewZ|·26, 0, 10) px.
     - Point size grows by coc, with alpha ×max(px²/(px+coc)², .08). The shape blends to a soft disc, and spikes are multiplied by (1−k).
     - Dust only fades, ×mix(1, .55, soft).
   - **Test:** ≤ .08 ms extra at the Orbit cluster, and the subject's size is unchanged.
6. **X-6 · Spoken citations.**
   - **Scoring:** each `speak:chunk` scores the up-to-30 stars lit this turn as |chunk terms ∩ row terms|, +2 for a topic or cluster name, +1 for a file name or host. Terms use a JS port of the bridge's `_terms()`/STOP with 5-character prefix matching.
   - **Citing:**
     - Cite the best star only if its score is ≥ 2, hold it at least 600 ms, and never cite ahead of the audio.
     - The cited star (state 9) gets ×(1.3+1.2·drive) brightness and ×(1+.25·drive) size, plus lamp 0, and emits `cite`.
   - **No citation:** the used stars pulse ×(1+.15·drive).
   - **Test:** "React 19 is stable" cites react.dev; "Very good, Sir" cites nothing.
7. **X-7 · Outer world.**
   - **Shapes** via `aShape` (branchless SDF):
     - star
     - square: a 1 px outline plus a pinpoint
     - ring: a tilted ellipse with a bright rim, filled once the page is opened
     - triangle
     - hexagon
   - **Files:**
     - New path segments grow 70 ms apart with a spark at the tip. The file at the end flares ×3→1 over 300 ms; Edit and Write flare twice.
     - On Read, the leg walks through each ancestor folder (≤ 120 ms per hop) before landing.
   - **Web:**
     - Up to 5 search results fall in from 700 u above (600 ms outCubic, 60 ms apart).
     - `web_open` fills the ring over 350 ms. `web_click` presses it 1→.85→1 over 120 ms.
   - **Mail reads** drop up to 5 points into the Mail node.
   - **Test:** `C:\…\Orbit\src\app.js` and `c:/…/orbit/src/app.js` resolve to one node, growing out of the Orbit cluster.
8. **X-8 · Action jets and the iris.** Thought travels; action fires.
   - **Geometry:** two open cylinders (20×24 segments) tilted 12° off +Y. Radius r(v) = mix(3, 14, v^.6), height y = ±v·uJetLen.
   - **Shading:**
     - I = (1−v)^1.4·(.55 + .45·noise)·|N·V|^1.6.
     - Plus three knots: Σ .8·e^(−((v − fract(1.1t + k/3))·L/18)²).
     - Colour (1,.92,.8), additive, alpha ≤ .35.
   - **Timing:**
     - Length 0→520 over 280 ms (outExpo), with ±4% flicker.
     - On success: hold 300 ms, then fade with τ 450 ms. On error: 3 stutters of 70 ms each.
   - **Triggers:** gmail_send, spotify, open_app, set_volume, tv, minecraft_*, and Bash after it has run 600 ms. At most one ignition per 2 s.
   - **Target:** the action's node ignites (state 7) and gets lamp 0 in white, with one label at the jet tip.
   - **Screen and camera capture:** the iris only. No jets, no flash.
   - **Test:** a 400 ms Bash shows no jets; a 2 s Bash shows jets from +600 ms.
9. **X-9 · Births** (a fact, a skill, a loop left open).
   - **Start immediately:** the slot comes from the event text (`data.slotFor`). The row is bound by text hash within 3 s. Never wait on the database.
   - **Origin:** r = 135 u on the Doppler-bright side (best of 16 samples).
   - **Spark** (T+150, 1100 ms, inOutCubic):
     - A head plus 48 motes. Pool of 2048 points = 32 births × 64.
     - The path follows the dust-arm spiral θ = θ0 + 2.4·ln(r/r0), then blends into a Bézier onto the slot with the control point lifted 220 u.
   - **Gather** (T+1150): motes collapse onto the slot; dust within 140 u leans in up to 10 u.
   - **Ignition** (T+1330):
     - Size spring ω 20, ζ .42. Brightness 3→1.25 (τ 320 ms). Spike falloff 70→6.5 over 600 ms (outExpo).
     - Colour: τ = (t−born)/2.6; c = mix(mix((1,.55,.2), (1,.82,.55), smoothstep(0,.4,τ)), WHITE, smoothstep(.4,1,τ)).
     - The ring dips to .7 and recovers over 500 ms. A dust shove travels R = 320·outCubic over 900 ms, pushing 12 u.
     - No ring sprites, flashes or bloom changes.
   - **Links** (T+1580): 1–3 lines draw on over 420 ms, 130 ms apart, to `/api/brain/match` hits within the cluster (or the nearest stars). Each target blinks ×1.8.
   - **Skill:** the hexagon's spikes unfold 50 ms apart, then 3–6 step stars 18 u apart ignite 120 ms apart. "Improved" adds +8% brightness permanently (cap 1.6×).
   - **Left-open loop:** a C-ring draws in over 500 ms and stays static, with its gap facing the hole.
   - **Spacing and catch-up:** births are at least 900 ms apart. Births that happened while hidden queue (up to 12) and replay at 700 ms each.
   - **Test:** at `?replay=fact&at=1.5` the star is ≥ 1.2× its rest size. At 4.0 it is within 3% of white.
10. **X-10 · Learning sequence.**
    - **Trigger:** 8 or more rows of one topic within 4 s (not diary rows).
    - **Filament:** one path-texture row drawn as the X-2 ribbon in gold, 2.5→1 px wide, over 1600 ms. While rows keep streaming (up to 60 s) it stays at alpha .25, then fades over 1.2 s.
    - **Cascade:**
      - Stars ignite in row-id order. The gap between ignitions goes 90→40 ms, with the whole cascade capped at 2.4 s (above 60 rows, 2 per tick).
      - Each: a spring (ω 22, ζ .5), brightness 3→1 (τ 250 ms), and 8 motes.
      - Each line of the nearest-neighbour tree (breadth-first in read order) draws in 120 ms when its second end lights.
    - **Finale:** a ×1.5 pulse over 600 ms (inOutSine), then each star cools with τ 2.6 s.
    - **Dust well:** `uWell[4]` grows 0→18 u over 2 s (σ 220), permanent for the 4 newest constellations.
    - **Variants:** focused 7.4 s; unfocused or in game 2.0 s; a click collapses it to 600 ms.
    - **Test:** 40 ignitions in order, the last at 4.3±.05 s, with stable positions across reloads.
11. **X-11 · Endings.**
    - **Forget** (an event or the forget tool; deletions found only by polling fall too, but with no card and no camera move):
      - Lines unthread from the star's end over 500 ms.
      - From +.5 s the star falls with τ = uT − t0: r = 84 + (r0−84)·e^(−1.8τ), φ += 2(1−e^(−1.2τ)), y = y0·e^(−2τ).
      - It reddens as it falls: colour ×(1, .55+.45z, .3+.7z).
      - Alpha reaches 0 by 2.4 s. The sprite stretches up to 3× toward the hole.
      - At the end: one ring flicker (+40%, 300 ms), uFeed −.05, and its slot is freed.
    - **Loop finished:** the 60° gap closes over 600 ms, the ring doubles for 200 ms and fades over 1.2 s. The star then moves to today's diary node over 1.6 s.
    - **open_loops:** the rings pulse ×1.8 in turn, 120 ms apart.
    - **Each exchange** adds a pearl at today's diary node with a 300 ms spark.
    - **Never:** rotation, sparks or shake.
    - **Test:** the falling star never renders inside 84 u.

### 4.2 DIRECTOR (director.js)

1. **D-1 · Spring rig.**
   - **Springs:** spherical, around the look target. Target ω 4.5; azimuth, elevation and distance ω 3.2; FOV ω 4; all ζ 1. Fixed 120 Hz substeps (up to 12 per frame) with no allocation.
   - **Caps:**
     - Azimuth ≤ 25°/s (15°/s in a game), elevation ≤ 15°/s, distance changes ≤ 1.5× per second.
     - Camera at least 640 u from the hole, and the coverage rule always holds.
     - Path guard: if a move would pass within 700 u of the hole, route around the 1.3× circle.
   - **While driving:**
     - Write `camera.position` and `controls.target` directly, with damping and autoRotate off.
     - The director owns idle drift: 1.08°/s in ESTABLISH, and 1.5°/s plus a ±.6% distance breath (9 s period) while holding. No drift in a game.
     - Remove core's 9 s autoRotate restart.
   - **Uno grabs the view:** on pointerdown or wheel the director parks and OrbitControls takes over (damping .045, `update(dt)`). The director resumes 8 s after the last input, re-seeded from the real camera with ω ramping .6→full over 2 s. With Follow off it never resumes.
   - **Never:** cuts, roll, shake, FOV punches or dolly-zoom.
   - **Test:** at 30 vs 144 fps, camera positions at 5 s agree within .5 u. After a pointerdown, zero director writes for 8 s.
2. **D-2 · Handoff composition.**
   - **Ownership:** the director owns `state.viewOff` and core only applies it.
   - **On wake:** the target snaps to the hole, then the offset is set to (W/2 − orb.x, H/2 − orb.y).
   - **Glide:** p = LUT((now − t0Local)/620), with t0Local = t0Epoch − performance.timeOrigin. The offset is mix(orb, (Wc/2, 0), p). The glass rectangle uses the same p, and closing uses a 700 ms inOutCubic table.
   - **Edge cases:** if the panel is already expanded, play LEAN_IN only. On resize, recompute the endpoints and keep t0.
   - **Test:** at frame 0 the hole is within .5 px of the orb, and the glass edge stays within 1 px of the column throughout.
3. **D-3 · Shot planner.**

   | Shot | When | Framing |
   |---|---|---|
   | ESTABLISH | idle | hole, 2300 u, 11.3° |
   | LEAN_IN | listening | distance ×.86, elevation 6° |
   | SEARCH | recall | bounding sphere of the hole and hits, 85th-percentile trim, d = R/sin(fovEff/2)·1.2; moves only if more than 30% of it is off-screen |
   | TWO_SHOT | one leg | centre mix(hole, target, .55); side perpendicular to the hole→target line, at most 35° per move |
   | GROUP | parallel tools, or targets within 2.5 s | union of the hole and targets; targets spread over more than 120° around the hole fall back to ESTABLISH |
   | FOCUS | click / lock-on / push | 640–900 u from the subject, hole out of frame or coverage ≤ 30% |
   | HOLD | approval | frozen |
   | REVEAL | speaking | hole and all stops; azimuth of maximum spread among current ±0/10/20/30°; 18°; ω 2.4 |
   | HOME | 3.5 s after speaking | ESTABLISH over about 2.2 s |

   - fovEff = min(vertical FOV, horizontal FOV of the free area).
   - Each shot holds at least 1.2 s. Bursts merge into one move.
   - **Priority:** user > genesis > birth > tool > recall > idle.
   - Off-screen targets get a soft edge light (C-5), never arrows.
   - **Test:** 8 tools cause at most 2 moves, and the coverage rule is never broken.
4. **D-4 · Moods, time and voice.**

   | Mode | Spin | Temp exp | Ring gain | Ring sharpness | Disk expo | uTS |
   |---|---|---|---|---|---|---|
   | idle | 1 | 1.35 | 1 | 16 | 1 | 1 |
   | listening | .8 | 1.35 | 1.10 | 22 | 1.08 | .55 |
   | thinking | 2.0 | 1.15 | 1.10 ±8% at .8 Hz | 16 | 1.05 | 1 |
   | tool | 1.4 | 1.25 | 1.10, +30% hot spot at departure | 16 | 1 | 1 |
   | speaking | 1.1 | 1.30 | 1+.25·drive | 16/(1+.33·drive) | 1+.12·drive | 1 |
   | remember | 1 | 1.35 | 1 | 16 | +.2 impulse, plus a gold band 1.2 s | .6 |
   | approval | .3 | 1.35 | .85+.3·sin²(π·.5t), real time | 18 | .9 | .12, AMBER tint at 25% |

   - **Learning:** uTS .3.
   - **Fault:** a 120 ms wince, ×.7 and desaturated, recovering over 900 ms.
   - **Looking:** iris plus ring radius ×.95 (a pupil tightening).
   - All values move on springs (ζ 1, ω 6) and stay within ±15% luminance.
   - **uTS:** τ 350 ms. It scales only the ambient clocks; negative values play them in reverse.
   - **Drive (voice level):** in the panel, read `parent.__novaCore.speakEnv`. Standalone, use clamp(.25 + level·5) with an ×.94^(dt·60) decay, smoothed by a spring with ω 18.
   - **Voice history:** a 32×1 R8 texture written at 10 Hz, read at a continuous head position. `absorb` and remember add .5 / .7 impulses.
   - **Game:** impulses ×.6.
   - **Test:** mode thrash every 300 ms never changes any uniform by more than 5% in one frame.
5. **D-5 · Set pieces.**
   - **BIRTH:** frame mix(hole, slot, .6) at d = max(fit·1.2, coverage limit). Slots ≥ 900 u from the hole use FOCUS from the outward side instead. Hold until +4.8 s, then return. Skipped while Uno holds the view, Follow is off, or another set piece is running.
   - **GENESIS:** shots as in §3c, plus `ui.letterbox()`. No camera move when unfocused or in a game.
   - **REWIND:**
     - Triggered by day_log or a recall with `days`, once per turn, never in a game or with reduced motion.
     - uTS goes 1→−.8 over 300 ms (inOutSine), holds for .4 + .02·days s (max 1.0), then rises to 1.3 over 400 ms and settles to 1 over 600 ms.
     - The camera dollies along the diary arm, 180 u out and 120 u up, over 900 + 15·days ms (max 1400), while the day's pearls ignite 45 ms apart.
   - **Test:** genesis runs 7.4 s focused and 2.0 s unfocused. Reverse time never exceeds 1.0 s.

### 4.3 UI (ui.js + brain.html)

1. **U-1 · Words.**
   - **Layout:** top-left of the free area, 28 px from the left edge and 8 px below `var(--top)`, max width min(560, Wfree−56). Text-shadow 0 1px 14px #000. No glass and no backdrop-filter.
   - **Uno's question:** 12.5 px at 62%, with curly quotes. 240 ms entry, dims to 35% after 1.2 s, removed when she starts speaking.
   - **Search terms:** up to 8 spans, animated with WAAPI toward the hole's screen point (700 ms cubic-bezier(.55,0,1,.45), scale 1→.4, 40 ms stagger). Each emits `termLand`. They never gate anything.
   - **Her sentence:**
     - Inter 300, clamp(20px, 1.8vw, 28px), max 2 lines, from a pool of 80 spans.
     - Karaoke by colour only: spoken words 100% ink, the rest 38%.
     - Reveal at 14.5 chars/s while drive > .04; pause after 120 ms below it.
     - Resync on each `say` or `speak:chunk` if off by more than 300 ms, and never get ahead of the audio.
     - A new line pushes the old one up 10 px and fades it over 300 ms.
   - **Idle:** nothing at all.
   - **Test:** no reflow while speaking.
2. **U-2 · Trail.**
   - **Rows:** at most 4 visible (pool of 6), at translateY(k·18 px), 12 px text.
   - **Row contents:** glyph, verb (Inter 500), " · ", object (≤ 28 chars, middle-ellipsis for paths), status.
   - **Status:** running = arc spinner (900 ms per turn); done = tick (160 ms draw); error = dash, with the row at 45%. No timers, numbers or counts.
   - **Motion:**
     - Rows enter with a clip-path wipe (280 ms) plus translateX −8→0 (cubic-bezier(.22,1,.36,1)).
     - Older rows fade to .55, then .3; the oldest leaves over 200 ms.
     - Updates are coalesced to at most one per 120 ms.
   - **Three-way hover:** row, star (`uHov[4]` ×2) and HUD card (`{nova:'hover', id}`) all highlight each other.
   - **Leaders:**
     - WebGL screen-space ribbons, pool of 6: the row anchor in px plus the star's world position, projected on the GPU.
     - 1 px at .35 alpha, drawing on over 300 ms.
     - HUD card positions arrive as `{nova:'card', id, y, h}`.
   - **End of turn:** rows fade 6 s after the turn ends.
   - **Test:** 8 tools show at most 4 rows, with layout shift CLS = 0.
3. **U-3 · Pop-up cards and letterbox.**
   - **Card:** pool of 2. Notched card, 300 px wide, 12 px radius, with 6 px mask notches at mid-height. Fill rgba(16,16,19,.78) with a 1 px edge at .08. A 14 px SVG star whose spikes extend at the moment of ignition.
   - **Content:** an 11 px caps title (600 weight, .08em tracking, 38% ink), one of New memory · New topic · Learned skill · Skill improved · Loop closed · Left open · Forgot, plus one line of at most 64 chars at 13 px. Middle dots, no em dashes.
   - **Motion:** grows out of the star's screen point to top-centre over 520 ms with a leader line, holds 4.5 s, then folds back into the star over 380 ms (inCubic).
   - **Queueing:** at most 2 visible, extras merge into "+N more", one card per bulk learn, and no cards for polled deletions.
   - **Letterbox:** two scaleY bars to 9% each (600 ms in, 500 ms out). `ui.letterbox()` asserts it is only called by the learning sequence.
   - **Test:** genesis produces exactly one card.
4. **U-4 · Labels and detail card.**
   - **3D labels:** at most 3 at 11 px. Priority: the active target, then the cited star, then recall ranks 1–3. They appear 140 ms after ignition (260 ms, 4 px rise) and hide after 6 s or when the gaze moves on.
   - **Cluster labels:** 40% during a turn. A new topic's letters slide together with transforms only (900 ms outExpo, 28 ms stagger).
   - **Overlay positioning:** use Typed OM with a preallocated CSSTranslate, mutated in place and set only when the value changes. No string building.
   - **Click:** opens a detail card (3 lines, matched terms in bold), triggers a FOCUS shot, and ignites neighbours with state 2. This replaces `buildRays`.
   - **Test:** never more than 3 labels.
5. **U-5 · Dock and accessibility.**
   - **Dock squircles** (30 px, radius 32%):
     - Follow (dot status).
     - A/B (look compare).
     - Replay, shown after a turn: plays the recorded beats at real speed with gaps clipped to 400 ms, then a REVEAL with the recap glint. No database or birth side effects; a live turn aborts it.
   - **Font:** Inter variable (Latin, weights 300–600) as base64 in `app/font-inter.js`, loaded with FontFace. Fallback Segoe UI Variable. No network fonts.
   - **Embed:** `--top` comes from the HUD.
   - **Reduced motion:** camera stays in ESTABLISH; heads fade in at their targets (200 ms) with no flight; no sonar, pours, term drift, letterbox or rewind.
   - CSS animations are paused outside turns.
   - **Test:** buttons have aria-labels; reduced motion produces zero camera movement.

### 4.4 FEED (feed.js + bridge)

1. **F-1 · Transport.**
   - **Bridge:** add `/api/activity?since=N&wait≤8`, a long-poll using a `threading.Condition` on the existing Activity lock. `push()` calls `notify_all()`; waits run in .5 s slices. No streaming handler, and the token stays in the header.
   - **Panel:** the HUD is the only poller. Whenever the panel is open it long-polls and relays each event (`{nova:'evt'}`) plus each orb frame (`{nova:'orb'}`, every 100 ms while active, 1 s when idle) into the iframe.
   - **Standalone:** feed polls for itself.
   - **Watchdog:** if the orb is not idle and nothing has been relayed for 15 s, feed self-polls.
   - **Boot:** fast-forward the sequence number; never replay history.
   - **Hidden:** events fold into state; on show, late beats jump to their end state.
   - **Test:** push to bus ≤ 30 ms p95 standalone, ≤ 40 ms relayed.
2. **F-2 · Conductor.**
   - **Clock:** skew = min over the last 16 events of (now − at), reset on a jump of more than 2 s. Each event is due at at + skew + .06 s.
   - **Beats:** a pool of 64. Each beat fires once, and fx, director and ui all react in the same frame.
   - **Spacing:** launches at least 140 ms apart. Tools arriving within 50 ms form one fan-out with a 90 ms stagger.
   - **Lag:** when more than 500 ms behind, fold same-region beats within 1.5 s into fan-outs of up to 4. Never play faster than ×1.25. More than 3 s behind: jump to end states.
   - **Determinism:** randomness comes from mulberry32(seq).
   - **Turn log:** the last 20 turns × 64 beats, kept in localStorage (≤ 40 KB).
   - **Test:** 8 tools in 600 ms produce ≤ 4 launches spaced ≥ 140 ms, with done visuals ≤ 450 ms late.
3. **F-3 · Resolver and normalized events.** Tools are paired with their results by tool_use_id. Paths are normalized: backslashes become slashes, case is folded, the home folder becomes `~`, and only file names are shown. Output parsing takes up to 5 URLs, up to 6 `path:line` hits and any dates.

   | Tool | Target · motion | Glyph, temperature | Trail |
   |---|---|---|---|
   | auto-recall | X-3 | star, warm | Searching memory · <topic> |
   | recall(q) | match ids · X-4 | star, warm | Searching memory · <q> |
   | search_past | diary arm · X-4 | star, warm | Searching past chats · <q> |
   | day_log | day node · rewind | star, warm | Reading diary · <date> |
   | open_loops | loop stars | star, warm | Checking loops |
   | read_skill / save_skill | skill star · chain or birth | hexagon, warm | Following / Writing skill · <name> |
   | Read / Edit / Write | file (a memory note goes to its topic star) | square, cool (warm for notes) | Opening / Editing · <file> |
   | Grep / Glob | folder · front | square, cool | Scanning · <folder> |
   | Bash | Machine · jets after 600 ms | triangle | Running · <first word> |
   | web_search | query node · results | ring, cool | Searching the web · <q> |
   | web_open / read / click | host and page | ring, cool | Reading / Clicking · <host> |
   | gmail_inbox / read | Mail node · items fall in | triangle, cool | Checking mail |
   | send, spotify, open_app, volume, tv, minecraft_* | Actions or MC · jets | triangle | Sending mail / Playing · <track> / Opening · <app> / … |
   | capture_screen / camera | iris | triangle | Looking at screen / Looking |
   | start_job | satellite | triangle | Starting a job |
   | unknown | Actions anchor · 260 u arc | triangle | <tool name> |

   **Normalized events v2:**
   - `recall` {ids, near, days, terms, stars}
   - `step` {id, tool, region, targets, temp, glyph, verb, object, group}
   - `step:done` {id, ok, yield, results}
   - `step:hold`, `step:allow`, `step:deny`. Permission events reuse the held leg when the same tool and input hash arrive within 30 s.
   - `speak:chunk` {text}
   - `orb` {mode, flavour, drive, level, jobs, game, mic, pending}
   - `learn` {…, provisional}
   - `forget` {…, silent}
   - `genesis` {topic, phase}

   **Test:** 40 sample inputs map to the expected rows.
4. **F-4 · Bridge changes** (lead; test bridge first; restart only after `/api/orb` has been idle and the log quiet for 40 s; never POST `/api/ask`).
   1. The long-poll from F-1.
   2. **Honest recall.** `recall_block` sets LAST_RECALL to the ids actually emitted, inside the budget loop. Today it is set before the 1400-char budget loop, so it lights hits she never received.
      - LAST_NEAR (≤ 12): hits cut by the budget plus rows `search()` dropped at its coverage test (via a new optional `near` out-list).
      - LAST_DAYS: the dates of `extra` log lines.
      - LAST_TERMS: up to 8 terms.
      - The push carries ids, near, days and terms. `/api/brain/match` returns {ids, near, terms}.
      - A golden test checks that `recall_block` output is byte-identical for 50 history prompts.
   3. `/api/orb` adds `saying` (≤ 200 chars), `game` (`nova_events.gaming()`, cached 2 s), `pending` {tool, target} during approval, and `mic` only if the ears already expose it.
   4. `GET /api/brain/trail?days=30` (token required): top 300 {kind, target, uses, last} aggregated from `turns.tools`, with `mask_secrets` applied. Never in publish.py's whitelist.
   5. `POST /api/dev/activity`, only when NOVA_TEST is set, for fixture playback.
5. **F-5 · Demo, replay, bench.**
   - **`?demo=1`:** a deterministic 60 s loop:

     | Time (s) | Beat |
     |---|---|
     | 2 | wake |
     | 5.4 | the §2 question (fixed text) |
     | 5.46 | recall on topic:orbit-pitch-deck, falling back to the largest Orbit topic |
     | 8.2–12.4 | Read, web_search and web_open with synthetic outputs |
     | 12.6–21 | answer with a 5 Hz synthetic voice level |
     | 26 | fact birth |
     | 33 | skill birth |
     | 40 | 40-row demo-repo genesis |
     | 49 | a loop closes |
     | 54 | the demo fact falls into the hole; the other demo nodes fade over 600 ms |

     The demo has no POST and no database writes. Fixtures contain no memory text, because `/brain/app` is served without the token. In embed mode it posts demo beats to the HUD.
   - **`?replay=<name>&at=<s>`:** a frozen deterministic frame for screenshot diffs.
   - **`?record=1`:** logs a text-stripped fixture.
   - **`?bench=1`:** 60 s of fixtures at 1080p. Logs p50/p95/p99 frame ms, GPU ms (from the timer query, or n/a), long tasks and heap growth. Run it in headless system Chrome through Playwright with channel 'chrome' and the GPU on.

### 4.5 HUD (hud.html)

1. **H-1 · The transform.**
   - **Iframe:** `/brain?…&embed=1` is created at boot, full-window behind the column, with opacity 0 and `visible:false`. It is never resized.
   - **Opening:**
     - The transform runs on `heard` or a typed question, and only if the panel window is already visible. Never raise or focus the window.
     - Listening only pre-warms the brain (H-2 frame 0). A false wake fades it back out over 280 ms.
   - **Column motion:** FLIP with transforms only and `contain: layout paint`. A 100 ms wind-up, then a 620 ms slide (cubic-bezier(.2,.8,.2,1)). Post `{nova:'glide', t0Epoch: timeOrigin+now, slideMs, from, inset:396, glass}`.
   - **Closing:** 10 s after `turn:end`, with no pointer over the brain, no birth or genesis playing, and no new turn; or on Esc or a click on the orb. Reverse over 700 ms (inOutCubic) and post `collapse`.
   - **Edge cases:** if the panel is already expanded, there's no transform. On resize, post `retarget` with the same t0.
   - **Test:** the column and the hole stay within 1 px of each other on every frame.
2. **H-2 · Match cut.**
   - **Shared state:** `window.__novaCore = {x, y, R, drive, speakEnv, angle: burst+wobble, surge, accent}`, one object mutated each reactor frame.
   - **Wake:** post `{nova:'wake', orb, t0Epoch}`. The iframe fades 0→1 over 280 ms while the reactor's spikes fade out. The brain's spikes match the reactor's: long = R·.98·(.72 + .4·drive + .2·surge), short = .6× that, same angle. Colour goes accent→white-gold.
   - **After the glide:** +400 ms, then the brain's spikes fade over 400 ms.
   - **Close:** the same steps in reverse, ending with the core glowing +30% for 180 ms.
   - **Never** change the four-arm core.
   - **Test:** at frames 0–16 the spikes match within .5° and 1 px.
3. **H-3 · Relay and hygiene.**
   - **Polling:** replace the 350 ms activity interval with one always-on long-poll loop that relays in the same tick. The orb is polled every 100 ms while active and every 1 s when idle.
   - **Tool cards:** stamp `data-step=tool_use_id` on each `.act` card and post its rectangle when it is created, scrolled or resized (throttled to rAF). Hover messages go both ways. Draw a 1 px stub while a leader line is live.
   - **Glass:** while the brain is behind the column, drop backdrop-filter (fill rgba(16,16,19,.55)); the brain's finish pass draws the frost instead.
   - **Reactor:** 30 fps when collapsed and idle or when a game is running; stopped when hidden. Cache gradients per accent.
   - **Test:** over 10 s idle, ≤ 31 reactor frames per second, and no backdrop-filter layers over the iframe.

### 4.6 CORE (core.js + data.js)

1. **C-1 · GPU event clock.**
   - **Texture:** RGBA32F, 128×96, NearestFilter. Texel (i & 127, i >> 7) holds R = tOn, G = tOff (1e9 while active), B = state, A = rank or parameter.
   - **States:** 1 used, 2 target, 3 tentative, 4 birth, 5 falling, 6 outside, 7 action, 8 considered, 9 cited, 10 open loop, 11 pending approval.
   - **Vertex shader envelopes:**
     - on = smoothstep(0, .18, uT−R)
     - flash = step(R, uT)·e^(−6(uT−R))
     - warm = on·e^(−max(uT−G, 0)/1.6)
     - after = .35·on·e^(−max(uT−G, 0)/45)
   - **Edges:** a new `aEnds` attribute; glow = .6·min + .4·max of the two ends. Delete `edges._tick` and the per-frame heat uploads.
   - **API:** `ignite`, `cool` and `coolAll` keep their signatures but write texels, with at most one texture upload per frame.
   - **Appends:** use `addUpdateRange`; never re-upload the whole buffer (about 530 KB today).
   - **Rebase:** every 30 min while idle, subtract 1800 s.
   - **Test:** 300 lit stars cost the same as 0 (±.02 ms), and there are no buffer uploads on frames without events.
2. **C-2 · Governor.**
   - **vsync:** measure it as the median of 30 rAF deltas.
   - **Frame rates:** ACTIVE renders every n = ⌈15.5/vsync⌉ frames (60/48/55/60 fps on 60/144/165/240 Hz). CALM, after 10 s quiet, uses ⌈31/vsync⌉ (about 30). DEEP, after 120 s unfocused, uses ⌈47/vsync⌉ (about 20).
   - **Hidden or collapsed:** no rAF at all. The first frame on resume renders immediately.
   - **Timing:** real dt, clamped to .1. `controls.update(dt)`.
   - **Game:** CALM, half-resolution bloom, pixel ratio ≤ 1, no comets. Beats still play.
   - **Quality ladder:** a timer query every 10th frame. If the p90 of 60 samples exceeds 2.4 ms, step down: half bloom → pixel ratio −.25 → dust 7k → CALM. Step back up after 10 s under 1.4 ms. Never change the ray-march step count. Fall back to CPU frame time when timer queries are unavailable.
   - **Startup:** all pools exist at boot; call `compileAsync` before start. The 3.4 s intro plays only on the first open of the day; otherwise uIntro starts at 1.
   - **Test:** on 144 Hz, 48.0 and 28.8 fps; no callbacks while hidden.
3. **C-3 · Black-hole uniforms** (no extra march steps; defaults are bit-identical to v2):
   - `uDiskPhase`, integrated on the CPU.
   - `uTempExp`, `uRingGain`, `uRingSharp`, `uRingR`, `uRingTint`/`uTintA`.
   - `uExpo` + `uFeed` (≤ .15) + `uWince`.
   - `uVoiceHist`/`uHistHead`: lv = texture(head − lag·10/32) with lag = (r − RIN)/3. Emission ×(1 + .45·lv·e^(−(r−RIN)·.22)); noise sampled at r + .15·lv.
   - `uBand`: the remember band.
   - `uHot` + `uBHpx`: the departure hot spot.
   - `uIgnite`: the wake ignition. thr = mix(RIN, ROUT+1, outCubic(k)) over 800 ms, ring first.
   - `uEmit`/`uEmitC`: an emitter term inside the march loop behind `if(uEmit.w>0.)`.
   - Core computes `state.bhPx` once per frame. The lerps in `look.*` become dt-correct.
   - **Test:** pixel diff 0 against v2 with all defaults.
4. **C-4 · Lensed sky** (A/B, on by default).
   - **Sky:** a new sky shader with dir = normalize(world − camera), sampling the existing skyRT through `uSkyRot`, plus procedural stars f(dir).
     - Cell = floor(dir·180), probability .011, 4% bright.
     - σ = .8 px from fwidth, energy-conserving.
     - Delete the 4.5k background points.
   - **Ray-march:** on escape, post-bend toward the hole by α = (2 − s(2s² + 3b²)/|p|³)/(2b), with s = p·v̂ and b = |p×v̂|.
     - w = 1 − smoothstep(10, 16.5, b0).
     - Add sky + stars × w(1−al).
     - Output premultiplied (v2 col·al + sky) with (ONE, ONE_MINUS_SRC_ALPHA).
   - **Halation:** `bloomTintColors[3]` and `[4]` set to (1,.9,.82), on the same toggle.
   - **Test:** with the toggle off, pixel-identical to v2. With it on, the seam is under 2/255 and the cost under .06 ms.
5. **C-5 · Finish pass.**
   - Delete `uAberr` (chromatic aberration).
   - `uLift` ≤ .08, applied as c += lift·(1−c), at most once per 2 s.
   - `uIris`: vignette .9→1.7→.9 over 650 ms.
   - `uGlass`: the column rectangle with a 12-tap Poisson blur (14 px), ×.55, plus a hairline.
   - `uSpike`: four arms, each e^(−(dPerp/1.1)²)·e^(−dPar/len), handoff only.
   - `uEdge`: off-screen target light, e^(−d²/(2·90²))·.12.
   - **Test:** lift is clamped in code.
6. **C-6 · Layout** (data.js).
   1. **Colour:** cluster tint mix → 0.
   2. **Stable slots:** a persisted `novaBrain.slots.v1` maps key→k. It never compacts, so stars never move.
   3. **Diary arm:**
      - Day rank d ≤ 29: r = 560 + 22d, θ = 290° − .8°d, y = 20 − 1.2d.
      - Entries: Fermat pearls at 4.2√(j+1), j·137.508°.
      - d ≥ 30: an archive knot at the tail.
      - At day rollover, every diary star moves one notch over 1.2 s. This is the only sanctioned layout motion.
   4. **New topics:**
      - A topic with no family and 8 or more rows takes a free sector, chosen by hash01(topic), from θ ∈ {18, 54, 90, 162, 198, 342}° at r 1080 (y ±60), and is persisted.
      - Its stars form a disc: r = 22√k, θ = k·137.508°, normal = normalize(up + .4·radial), relaxed once with 40 repulsion iterations.
      - A bulk learn into an existing family uses a 16√k disc around its main star.
   5. **Places:**
      - Files anchor by keyword: orbit, tpn, .nova/nova, mcserver/minecraft/cobblemon, roblox/spellbook/nopixel/unoryx, school/midterm; anything else goes to Files.
      - Each child sits at parent + dir·(34 + 18h), with dir away from the hole and jittered ±35°/±20° by FNV, keeping 14 u apart.
      - Web hosts sit at 308°±18°, r 1350–1550; page satellites at 22–40 u.
      - Actions get 16 slots on a 90 u ring.
      - Keep at most 400 places (least recently used dropped). Places unused for 14 days drop to 25% brightness.
      - Seed from `/api/brain/trail` on first load.
   6. **Recency:** brightness ×(1 + .3·e^(−age/5 days)).
   - `slotFor()` and `diaryArm()` are exported.
   - **Test:** deleting a row and reloading leaves every other position identical.

## 5. Performance rules

1. **Budget:** at 1080p (pixel ratio 1) on the RTX 5060 Ti, worst case ≤ 3.0 ms GPU per frame. Targets: p95 ≤ 1.2 ms idle, ≤ 2.2 ms during a turn, ≤ 2.4 ms during genesis. JS ≤ 1 ms p95 per frame, ≤ 2 ms per event batch.
2. **Estimated costs (ms):**

   | Item | Cost |
   |---|---|
   | v2 base | 1.0 |
   | Ray-march at the 30% coverage cap | +.45 |
   | Event clock | .02 |
   | Gaze legs | .04 |
   | Sonar, fronts and lamps | .05 |
   | Pours and motes | .03 |
   | Rack focus | .08 |
   | Lensed sky | .06 |
   | Emitter (≤ .7 s per turn) | .05 |
   | Jets | .03 |
   | Finish extras | .06 |
   | **Total** | **≈ 1.9** |

3. **Zero per-frame allocation:** scratch pools, Typed OM, no closures or template strings in `onFrame`.
4. **Uploads only on events:** `addUpdateRange`, at most one event-texture upload per frame. No new geometries or materials after boot.
5. **Caps:**
   - 32 legs, 8 heads, 768 pour particles, 2048 motes, 4 lamps, 2 jets, 3 labels, 2 cards, 6 leaders, 4 trail rows, 4 dust wells, 4 satellites.
   - 12 queued births.
   - New GPU memory ≤ 1 MB. No new render targets.
6. **Governor and coverage rules** as in C-2 and 4.0.
7. **No new passes:** no full-screen passes beyond the finish pass, no MSAA, no backdrop-filter over the canvas, and no bloom increases.
8. **DOM:** write only on change, never call `getBoundingClientRect` per frame, and pause CSS loops when idle.
9. **Proof:** measure with `?bench` in headless system Chrome, never the hidden preview pane. Use the test bridge only.
10. **Privacy:** show file names only. Memory text stays on the token-gated page. Fixtures contain no text, and publish.py never picks up brain data.

## 6. Cut list

**Look**

- The 48 Rs lensing sphere and the "mass" growth: full-screen march cost and reads as a zoom glitch.
- Black-hole spikes on every syllable: reads as lens flare.
- Circling crest, rotating loop gaps, skill orbit rings and beads, Mobius rings: Uno rejected spinning rings.
- Drawn sonar ring, shockwave sprites, radial sparks, elastic pops, 3–4× flashes, bloom pumps, photo flash: fanfare and strobing beside games.
- Lighthouse beam and radar wedge: radar tropes, and they light in angle order instead of rank.
- God rays, light shafts, mode colour grading.
- Lens bulge and Einstein ring at lock-on, "living sun" corona: they read as a reticle or a cartoon.
- Nebula and vortex billboards.
- Flavour hues, per-cluster tints, chromatic aberration.

**Motion**

- Dolly-zoom, FOV punches, a tilt-up per web search, close shots under 640 u, a chase cam, 200° orbits.
- Probes circling their targets: reads as a spinner.
- Web probe leaving to r 4000 with a sky ripple.
- Continuous rivers, synaptic sparks, idle dreaming, a woven web of speaking beams: fake or busy activity.
- 5.3 Hz syllable shimmer and per-syllable dust ripples.
- A satellite ring at r 390, Spotify breathing: constant motion near the hole.

**Text and UI**

- Numbers in 3D, "3 of 14", "x5", timers, "4 steps · 3.2 s": Uno vetoed counters.
- Scrambled decode text and typewriter effects, letter-particle question infall, a ghost-text plane, an SDF caption atlas.
- Idle greeting text.
- Rotating the core 45° on wake, or any change to the four-arm star.

**Tech**

- EventSource with the token in the URL; an SSE streaming handler.
- A 6 MB cubemap, GPGPU simulation, real DOF or SSAO passes, a second bloom pass.
- A Worker token index (1181 rows don't need it).

**Deferred to phase 2**

- A Today view of every route that day.
- Worn highways from co-use (needs per-turn id logging).
- A scrub ribbon.
- A `brain_show` tool so Nova can point the camera.
- Size scaled by how often a memory is relied on (`brain-usage.json`).
- An opt-in sound layer, off by default and silent unless the panel is focused.