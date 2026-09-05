# The projector

Nova can append **one** machine-readable block to the end of a reply. The
block never reaches the transcript and is never spoken — it is stripped out,
compiled, and drawn on the stage. Her prose has to stand on its own.

There are four kinds.

---

## `::CAD` — geometry as code

The main one. She writes JavaScript against [JSCAD](https://github.com/jscad/OpenJSCAD.org),
which runs in a Web Worker with a twelve-second budget.

```
::CAD title="Bungalow" unit="cm"
const W = 620, D = 420, H = 280, T = 20;

const shell = subtract(
  cuboid({ size: [W, D, H], center: [0, 0, H/2] }),
  cuboid({ size: [W - 2*T, D - 2*T, H], center: [0, 0, H/2] })
);
const walls = subtract(shell, union(
  cuboid({ size: [95, T*4, 210], center: [0, D/2, 105] }),
  cuboid({ size: [130, T*4, 110], center: [-200, D/2, 150] })
));

return [
  { label: "Walls", note: "A hollow shell with the openings cut out.", geom: walls }
];
::END
```

**Rules the kernel enforces:**

- Return an **array of parts**, each `{ label, note, geom }`. Each becomes a
  separately labelled, clickable, explodable piece. Never one merged lump.
- **Z is up**, as in OpenSCAD. Angles are radians. The compiler converts to the
  renderer's Y-up on import.
- Solids take **three** numbers (`size`, `center`); flat shapes take **two**.
- You can never boolean a solid with a flat shape — extrude it first.

**Mistakes the kernel absorbs rather than failing on:**

| Mistake | What happens |
|---|---|
| `center: [x, y]` on a solid | Corrected silently — a missing Z unambiguously means the origin plane. |
| `size: [x, y]` on a solid | Filled in **and reported** in the audit, because a body that is quietly the wrong depth looks plausible. |
| `subtract(solid, flatShape)` | Refused with an error naming which argument is 2D and how to fix it. |
| A variable named after an API function (`arc`, `line`, `text`) | Fine. The API is a scope object, so an inner `const` shadows it normally. |

---

## `::MODEL` — declarative shapes

The quick path for a handful of plain stacked shapes. **You do no arithmetic** —
you say what a part sits on and the compiler works out where that is.

```
::MODEL
{"title":"Side table","unit":"cm","parts":[
 {"id":"top","label":"Table top","shape":"box","size":[160,6,90],"note":"..."},
 {"id":"leg","label":"Leg","shape":"cylinder","size":[4,4,74],
  "on":"top","face":"bottom","align":"left back","offset":[8,0,8],
  "repeat":{"count":4,"step":[136,0,0]},"note":"..."}]}
::END
```

- `on` / `in` — which part it rests against, or sits inside.
- `face` — `top` (default), `bottom`, `left`, `right`, `front`, `back`.
- `align` — which edges it lines up with, e.g. `"left front"`. Unstated axes centre.
- `offset` — a nudge afterwards.
- `openings` — cuts **real holes** in a box part (piers, sill and lintel), so a
  window is a hole rather than a box stuck to a wall.
- `repeat` — `{count, step}` for legs, floors, fence posts.

Here **Y is up**. Shapes: `box, cylinder, sphere, cone, torus, plane, prism,
pyramid` — `prism` is a gable roof, `pyramid` a hip roof.

**The compiler audits the result** and says what it had to fix: parts left
hanging in air are dropped onto what's beneath them, interpenetrations are
reported rather than silently nudged, and anything too small to see is called
out. To change an assembly already on the stage she sends `::MODEL patch` with
`add` / `set` / `remove` instead of rebuilding it.

---

## `::HOLO` — flat schematics

For structure that is about connection rather than shape.

```
::HOLO
{"title":"How it hangs together","kind":"graph","nodes":[
 {"id":"a","label":"Wake word","note":"Offline matcher.","tag":"Input"}],
 "links":[{"from":"a","to":"b"}]}
::END
```

`kind` is one of `graph` (things that connect), `flow` (ordered steps),
`stack` (layers), `radial` (a hub and its surroundings), `bars` (comparison —
each node then needs a numeric `value`).

Hovering a node reads out its note and lights its edges; clicking asks her
about that node.

---

## `::BUILD` — a working file

A complete, self-contained HTML file, rendered live in a sandboxed iframe with
**Code** / **Run** and **Save**.

```
::BUILD name="visualiser.html"
<!doctype html>
<html>… the entire working file …</html>
::END
```

Inline the CSS and JS; it must run with no build step and no network.

---

## Eyes

The **Look** button captures the current projection and hands it back to her
as an image, so she can judge her own work.

- A 3D assembly is read straight off the WebGL canvas.
- A schematic is rasterised from its SVG.
- A generated page runs in a sandbox this document isn't allowed to read, so
  that one falls back to a screen capture.

The capture forces the assembly into its finished state and aims the camera
explicitly before reading back, rather than trusting the animation loop to
have run — a panel that isn't the foreground window gets no frames at all, and
photographing an animation that never started shows a model scaled to a
thousandth of its size sitting on an empty grid.
