#!/usr/bin/env python3
"""
nova_orb.py -- Nova's presence on screen: the HUD's living core, in the corner.

This is the same core the full panel draws (hud.html, "THE LIVING CORE"),
ported line for line: five ticked rings that lean on their own slow orbits and
turn in alternating directions, a bloom, a four-armed star flare turning
against them, and a white-hot centre. Same radii, tick counts, speeds, weights
and state colours. Nothing here is a new design.

    idle        violet, four rings, slow
    listening   green, five rings, faster
    thinking    magenta, five rings, fast
    speaking    pale violet, the rim bars move with her actual voice
    approval    amber: she is waiting for your yes or no
    offline     red and slow: the bridge is not running

Every quantity eases toward its target, and a change of state makes the rings
surge and settle -- exactly as in the panel.

By default the orb is DISPLAY ONLY: every click passes straight through to
whatever is behind it. Control it by voice ("Nova, hide" / "come back"),
Ctrl+Alt+N anywhere in Windows (toggles hidden/shown), or `Nova.ps1 orb ...`. With "clickable": true in orb.json (`Nova.ps1 orb clickable`)
left-click opens the full panel and right-click opens a small menu:

    Lock in place   the orb cannot be dragged; it stays exactly where it is
    Auto-hide       the orb fades out and lets clicks through, and comes back
                    whenever she is called (listening, thinking, speaking,
                    waiting for a yes or no), then leaves again a few seconds
                    after she has finished

Both are kept in orb.json next to the position, and the orb re-reads that file
while it runs, so the bridge ("Nova, hide" / "Nova, come back") and
`Nova.ps1 orb hide|show|lock|unlock` can flip them too.

    pythonw nova_orb.py
    python  nova_orb.py --preview out.png     # render every state to an image
"""
from __future__ import annotations

import ctypes
import json
import math
import os
import subprocess
import sys
import threading
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path.home() / ".nova"
POS_FILE = ROOT / "orb.json"
W, H = 200, 226                      # window
CX, CY = W / 2, 100.0                # centre of the core
R = 84.0                             # the HUD's R: half the core's side
SS = 3                               # supersampling for the line work
LINGER = 6.0                         # auto-hide: seconds she stays after going quiet

# Straight from hud.html -------------------------------------------------------
RINGS = [
    {"r": 0.97, "w": 1.0, "speed": -0.06, "ticks": 72, "tickLen": 0.030, "alpha": 0.28},
    {"r": 0.88, "w": 1.5, "speed": 0.14, "ticks": 24, "tickLen": 0.055, "alpha": 0.52},
    {"r": 0.76, "w": 1.0, "speed": -0.22, "ticks": 12, "tickLen": 0.040, "alpha": 0.38},
    {"r": 0.63, "w": 2.0, "speed": 0.34, "ticks": 6, "tickLen": 0.075, "alpha": 0.78},
    {"r": 0.52, "w": 1.0, "speed": -0.48, "ticks": 36, "tickLen": 0.045, "alpha": 0.42},
]
ORBIT = 0.20                          # how far the rings lean
MID = (0x8b, 0x46, 0xe8)              # --nova-mid, the bloom's outer colour

STATES = {
    "idle":      {"accent": (0xc0, 0x8c, 0xff), "spin": 1.0, "rings": 4},
    "listening": {"accent": (0x4d, 0xff, 0xc3), "spin": 1.6, "rings": 5},
    "thinking":  {"accent": (0xff, 0x4f, 0xd8), "spin": 3.4, "rings": 5},
    "speaking":  {"accent": (0xe6, 0xcc, 0xff), "spin": 1.3, "rings": 4},
    "approval":  {"accent": (0xff, 0xc4, 0x4d), "spin": 2.8, "rings": 5},   # the panel's "tool" amber
    "offline":   {"accent": (0xff, 0x4d, 0x6d), "spin": 0.6, "rings": 3},
}
CAPTION = {"listening": "LISTENING", "thinking": "THINKING", "speaking": "SPEAKING",
           "approval": "YOUR CALL", "offline": "OFFLINE", "idle": ""}

# Flavour on top of the mode (named by nova_mood in the bridge): the tool she
# is holding while she thinks, or the tone of the sentence she is saying. Each
# has its own colour AND its own motion, so a change is seen, not just noticed.
#   spin     multiplies the mode's spin       bloom  size of the inner glow
#   pulse    beat | double | breathe | tick | ""   (how `drive` throbs)
#   move     sweep | bob | shake | droop | iris | orbit | ""
FLAVOURS = {
    # while she works
    "searching": {"accent": (0x4d, 0xd8, 0xff), "spin": 1.5, "bloom": 1.0, "pulse": "", "move": "sweep",
                  "caption": "SEARCHING"},
    "music":     {"accent": (0x3d, 0xf5, 0xa0), "spin": 1.0, "bloom": 1.1, "pulse": "beat", "move": "",
                  "caption": "ON IT"},
    "hands":     {"accent": (0x5b, 0x8c, 0xff), "spin": 1.2, "bloom": 1.0, "pulse": "tick", "move": "",
                  "caption": "WORKING"},
    "memory":    {"accent": (0xa6, 0x7b, 0xff), "spin": 0.7, "bloom": 1.15, "pulse": "breathe", "move": "",
                  "caption": "REMEMBERING"},
    "looking":   {"accent": (0xd8, 0xf0, 0xff), "spin": 0.8, "bloom": 1.0, "pulse": "", "move": "iris",
                  "caption": "LOOKING"},
    "talking":   {"accent": (0xff, 0xa8, 0x6b), "spin": 1.1, "bloom": 1.0, "pulse": "", "move": "orbit",
                  "caption": "MESSAGING"},
    "fault":     {"accent": (0xff, 0x5a, 0x5a), "spin": 0.9, "bloom": 0.9, "pulse": "", "move": "shake",
                  "caption": "HMM"},
    # while she speaks
    "playful":   {"accent": (0xff, 0x6e, 0xc7), "spin": 1.5, "bloom": 1.05, "pulse": "", "move": "bob"},
    "warm":      {"accent": (0xff, 0xb0, 0x8a), "spin": 0.7, "bloom": 1.25, "pulse": "breathe", "move": ""},
    "sorry":     {"accent": (0x7a, 0xa6, 0xff), "spin": 0.55, "bloom": 0.8, "pulse": "", "move": "droop"},
    "alert":     {"accent": (0xff, 0x8a, 0x2e), "spin": 1.6, "bloom": 1.05, "pulse": "double", "move": ""},
    "done":      {"accent": (0xff, 0xe0, 0x6b), "spin": 1.2, "bloom": 1.15, "pulse": "", "move": ""},
}
FADE_S = 0.32            # colour crossfade: smooth, but over in a third of a second


def _srgb_to_oklab(c):
    lin = [((v / 255.0 + 0.055) / 1.055) ** 2.4 if v / 255.0 > 0.04045 else v / 255.0 / 12.92 for v in c]
    r, g, b = lin
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def _oklab_to_srgb(lab):
    L, a, b = lab
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    lin = (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
           -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
           -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)
    out = []
    for v in lin:
        v = max(0.0, min(1.0, v))
        out.append(255.0 * (1.055 * v ** (1 / 2.4) - 0.055 if v > 0.0031308 else 12.92 * v))
    return out


def look_for(mode: str, flavour: str) -> dict:
    """The mode's look with the flavour laid over it."""
    base = dict(STATES.get(mode, STATES["idle"]))
    base.update({"bloom": 1.0, "pulse": "", "move": "", "flavour": ""})
    fl = FLAVOURS.get(flavour) if mode in ("thinking", "speaking") else None
    if fl:
        base["accent"] = fl["accent"]
        base["spin"] = base["spin"] * fl["spin"]
        base.update({"bloom": fl["bloom"], "pulse": fl["pulse"], "move": fl["move"], "flavour": flavour})
    return base


def caption_for(mode: str, flavour: str) -> str:
    if mode == "thinking" and flavour in FLAVOURS and FLAVOURS[flavour].get("caption"):
        return FLAVOURS[flavour]["caption"]
    return CAPTION.get(mode, "")

# ------------------------------------------------------------------ rendering

_yy, _xx = np.mgrid[0:H, 0:W].astype(np.float32)
DX, DY = _xx - CX, _yy - CY
DIST = np.sqrt(DX * DX + DY * DY)

# Layers that never change between frames, computed once.
_g = np.clip((R * 1.16 - DIST) / (R * 0.20), 0, 1)
GLASS = (_g * _g * (3 - 2 * _g)).astype(np.float32)
GLASS_RGB = np.array([0.035, 0.015, 0.075], dtype=np.float32)
PLATE = np.exp(-(((_xx - W / 2) / 48.0) ** 4 + ((_yy - (H - 15)) / 9.5) ** 4)).astype(np.float32)
PLATE_RGB = np.array([0.04, 0.02, 0.09], dtype=np.float32)
MID_F = np.array(MID, dtype=np.float32) / 255.0

_font_cache: dict = {}
_caption_cache: dict = {}


def caption_mask(text: str) -> np.ndarray | None:
    if not text:
        return None
    if text in _caption_cache:
        return _caption_cache[text]
    if "f" not in _font_cache:
        try:
            _font_cache["f"] = ImageFont.truetype("segoeuib.ttf", 10)
        except OSError:
            _font_cache["f"] = ImageFont.load_default()
    img = Image.new("L", (W, H), 0)
    draw = ImageDraw.Draw(img)
    spaced = " ".join(text)
    width = draw.textlength(spaced, font=_font_cache["f"])
    draw.text(((W - width) / 2, H - 22), spaced, fill=255, font=_font_cache["f"])
    mask = np.asarray(img, dtype=np.float32) / 255.0
    _caption_cache[text] = mask
    return mask


class Core:
    """The HUD's core object: crossfaded colour, eased spin, integrated angles, surge,
    plus the flavour layer (its own motion, and a ripple whenever the look changes)."""

    def __init__(self) -> None:
        self.rgb = [192.0, 140.0, 255.0]
        self.fade_from = _srgb_to_oklab(self.rgb)
        self.fade_to = self.fade_from
        self.fade_t = 1.0                                   # 0..1 through the current crossfade
        self.target = tuple(int(v) for v in self.rgb)
        self.spin = 1.0
        self.phase = [i * 1.7 for i in range(len(RINGS))]   # each ring tilts on its own clock
        self.ring = [1.0] * len(RINGS)                      # presence, 0..1
        self.angle = [0.0] * len(RINGS)                     # accumulated rotation
        self.surge = 0.0                                    # spikes on a state change, then decays
        self.burst = 0.0                                    # the star's own counter-rotation
        self.last = 0.0
        self.mode = ""
        self.flavour = ""
        self.speak_env = 0.0
        self.bloom = 1.0
        self.fx = {"sweep": 0.0, "bob": 0.0, "shake": 0.0, "droop": 0.0, "iris": 0.0, "orbit": 0.0}
        self.pulse_w = {"beat": 0.0, "double": 0.0, "breathe": 0.0, "tick": 0.0}
        self.ripple = 1.0                                   # 0 = just changed, 1 = gone
        self.ripple_rgb = np.array(self.rgb, dtype=np.float32) / 255.0
        self.sweep_ang = 0.0

    def drive(self, mode: str, t: float, level: float) -> float:
        """How hard the core is being driven, per state -- the panel's numbers."""
        if mode == "listening":
            return 0.34 + 0.22 * math.sin(t * 0.009)
        if mode == "speaking":
            # speakEnv in the panel: a floor of 0.25 while she talks, pushed up by her voice
            self.speak_env = max(0.25, self.speak_env * 0.94, min(1.0, 0.25 + level * 5))
            return self.speak_env
        if mode == "thinking":
            return 0.30 + 0.16 * math.sin(t * 0.006)
        if mode == "approval":
            return 0.38 + 0.24 * math.sin(t * 0.014)
        return 0.10 + 0.045 * math.sin(t * 0.0013)

    def pulse(self, t: float) -> float:
        """Extra drive from the flavour's rhythm, each weighted by how present it is."""
        w = self.pulse_w
        out = 0.0
        if w["beat"] > 0.01:                                # ~120 bpm, a sharp kick
            out += w["beat"] * 0.30 * max(0.0, math.sin(t * 0.01257)) ** 6
        if w["double"] > 0.01:                              # da-dum ... da-dum
            ph = (t % 1100) / 1100
            out += w["double"] * 0.32 * (math.exp(-((ph - 0.05) / 0.04) ** 2) + math.exp(-((ph - 0.22) / 0.04) ** 2))
        if w["breathe"] > 0.01:
            out += w["breathe"] * 0.14 * (0.5 + 0.5 * math.sin(t * 0.0021))
        if w["tick"] > 0.01:                                # a small click four times a second
            out += w["tick"] * 0.12 * math.exp(-((t % 250) / 40))
        return out

    def frame(self, t: float, mode: str, level: float, caption: str = "", caption_alpha: float = 0.0,
              backing: float = 0.84, flavour: str = "") -> np.ndarray:
        """One frame as premultiplied BGRA (H, W, 4) uint8. t is in milliseconds."""
        look = look_for(mode, flavour)
        if mode != self.mode or look["flavour"] != self.flavour:
            if self.mode:
                self.surge = max(self.surge, 1.0 if mode != self.mode else 0.7)  # wind up, then settle
                self.ripple = 0.0
                if look["flavour"] == "done":
                    self.surge = 1.2
            self.mode, self.flavour = mode, look["flavour"]

        dt = max(1.0, min(80.0, t - (self.last or t)))
        self.last = t
        ease = 1 - math.exp(-dt / 190)
        quick = 1 - math.exp(-dt / 140)

        # ---- colour: a timed crossfade through OKLab, so violet -> cyan never
        # passes through grey, and it is always over in FADE_S.
        if tuple(look["accent"]) != self.target:
            self.target = tuple(look["accent"])
            self.fade_from = _srgb_to_oklab(self.rgb)
            self.fade_to = _srgb_to_oklab(self.target)
            self.fade_t = 0.0
            self.ripple_rgb = np.array(self.target, dtype=np.float32) / 255.0
        if self.fade_t < 1.0:
            self.fade_t = min(1.0, self.fade_t + dt / (FADE_S * 1000))
            k = self.fade_t * self.fade_t * (3 - 2 * self.fade_t)
            lab = [a + (b - a) * k for a, b in zip(self.fade_from, self.fade_to)]
            self.rgb = _oklab_to_srgb(lab)
        else:
            self.rgb = [float(v) for v in self.target]

        self.spin += (look["spin"] - self.spin) * ease
        self.bloom += (look["bloom"] - self.bloom) * quick
        for name in self.fx:
            self.fx[name] += ((1.0 if look["move"] == name else 0.0) - self.fx[name]) * quick
        for name in self.pulse_w:
            self.pulse_w[name] += ((1.0 if look["pulse"] == name else 0.0) - self.pulse_w[name]) * quick
        self.surge *= math.exp(-dt / 430)
        self.ripple = min(1.0, self.ripple + dt / 520)
        rush = 1 + self.surge * 2.4
        drive = min(1.2, self.drive(mode, t, level) + self.pulse(t))
        accent = np.array(self.rgb, dtype=np.float32) / 255.0

        # ---- motion: where the centre sits this frame
        fx = self.fx
        ox = fx["shake"] * 2.6 * math.sin(t * 0.09) * (0.6 + 0.4 * math.sin(t * 0.013))
        oy = (fx["bob"] * 3.2 * abs(math.sin(t * 0.0105)) * -1
              + fx["droop"] * 3.0
              + fx["shake"] * 1.2 * math.sin(t * 0.071))
        ccx, ccy = CX + ox, CY + oy
        if abs(ox) > 0.01 or abs(oy) > 0.01:
            dx, dy = _xx - ccx, _yy - ccy
            dist = np.sqrt(dx * dx + dy * dy)
        else:
            dx, dy, dist = DX, DY, DIST

        # ---- the line work (rings, ticks, leading arcs, rim bars), supersampled
        lines = Image.new("L", (W * SS, H * SS), 0)
        d = ImageDraw.Draw(lines)
        cx, cy = ccx * SS, ccy * SS
        iris = 1 + fx["iris"] * 0.08 * math.sin(t * 0.0035)       # looking: the rings focus in and out

        for i, ring in enumerate(RINGS):
            wanted = 1.0 if i < look["rings"] else 0.0
            self.ring[i] += (wanted - self.ring[i]) * ease
            self.angle[i] += ring["speed"] * self.spin * rush * dt * 0.0011
            present = self.ring[i]
            if present < 0.015:
                continue

            ring_iris = iris if i % 2 else 2 - iris
            rr = R * ring["r"] * (1 + drive * 0.030) * ring_iris * SS
            # the orbit: each ring leans through its own slow ellipse
            squash = 1 - ORBIT * (0.5 + 0.5 * math.sin(t * 0.00026 + self.phase[i]))
            ry = rr * squash
            ang = self.angle[i]

            # the ring itself
            a = ring["alpha"] * (0.5 + drive * 0.5) * present
            width = max(1, round(ring["w"] * SS * 0.8))
            d.ellipse([cx - rr, cy - ry, cx + rr, cy + ry], outline=int(255 * min(1, a)), width=width)

            # its ticks, pointing inward
            ta = int(255 * min(1, ring["alpha"] * 0.85 * present))
            inner = 1 - ring["tickLen"]
            for k in range(ring["ticks"]):
                th = ang + (k / ring["ticks"]) * math.tau
                c, s = math.cos(th), math.sin(th)
                d.line([cx + c * rr * inner, cy + s * ry * inner, cx + c * rr, cy + s * ry],
                       fill=ta, width=max(1, SS - 1))

            # the bright leading arc: it lengthens while the ring is winding up
            span = math.pi * (0.34 + self.surge * 0.5)
            steps = max(8, int(span * 14))
            pts = [(cx + math.cos(ang + span * j / steps) * rr, cy + math.sin(ang + span * j / steps) * ry)
                   for j in range(steps + 1)]
            d.line(pts, fill=int(255 * present), width=max(2, round((ring["w"] + 1.2) * SS * 0.8)), joint="curve")

        # searching: a radar sweep -- a bright wedge of short spokes racing round
        if fx["sweep"] > 0.02:
            self.sweep_ang += dt * 0.0062
            for j in range(18):
                th = self.sweep_ang - j * 0.045
                a = fx["sweep"] * (1 - j / 18)
                r0, r1 = R * 0.40 * SS, R * 0.95 * SS
                d.line([cx + math.cos(th) * r0, cy + math.sin(th) * r0,
                        cx + math.cos(th) * r1, cy + math.sin(th) * r1],
                       fill=int(255 * 0.55 * a), width=SS)

        # messaging: three sparks chasing each other round the outer ring
        if fx["orbit"] > 0.02:
            for j in range(3):
                th = t * 0.0042 + j * 0.42
                px, py = cx + math.cos(th) * R * 0.97 * SS, cy + math.sin(th) * R * 0.97 * 0.86 * SS
                rad = (3.2 - j * 0.7) * SS
                d.ellipse([px - rad, py - rad, px + rad, py + rad], fill=int(255 * fx["orbit"] * (1 - j * 0.25)))

        # rim bars: her voice, around the outside
        if mode == "speaking":
            for i in range(64):
                v = drive * (0.35 + 0.65 * abs(math.sin(i * 0.7 + t * 0.01)))
                if v < 0.02:
                    continue
                th = (i / 64) * math.tau - math.pi / 2
                r0, r1 = R * 0.99 * SS, (R * 0.99 + v * R * 0.10) * SS
                d.line([cx + math.cos(th) * r0, cy + math.sin(th) * r0,
                        cx + math.cos(th) * r1, cy + math.sin(th) * r1],
                       fill=int(255 * min(1, 0.30 + v * 0.7)), width=2 * SS - 2)

        line_i = np.asarray(lines.resize((W, H), Image.LANCZOS), dtype=np.float32) / 255.0

        # ---- compose
        # Every layer is blended in place, and only inside the box it can
        # actually cover: the star's arms and the heart are a fraction of the
        # window, so painting them across all of it was most of the frame time.
        rgb = np.zeros((H, W, 3), dtype=np.float32)        # premultiplied
        alpha = np.zeros((H, W), dtype=np.float32)

        def box(radius):
            y0, y1 = max(0, int(ccy - radius - 2)), min(H, int(ccy + radius + 3))
            x0, x1 = max(0, int(ccx - radius - 2)), min(W, int(ccx + radius + 3))
            return (slice(y0, y1), slice(x0, x1))

        def blend(sl, m, colour):
            r, a = rgb[sl], alpha[sl]
            r += (colour - r) * m[..., None]
            a += m * (1 - a)

        full = (slice(0, H), slice(0, W))

        # The panel draws this on near-black. On the desktop there is no such
        # guarantee, so it carries its own dark glass behind it, fading out
        # softly at the rim; without it the thin lines vanish on a light wallpaper.
        if backing > 0:
            blend(full, GLASS * backing, GLASS_RGB)

        # the change ripple: a ring of the NEW colour that runs out from the core
        if self.ripple < 1.0:
            p = self.ripple
            p_e = 1 - (1 - p) ** 3
            rad = R * (0.30 + 0.85 * p_e)
            sl = box(rad + 8)
            band = np.clip(1 - np.abs(dist[sl] - rad) / (2.0 + 5.0 * p), 0, 1)
            blend(sl, band * ((1 - p) ** 1.5 * 0.9), self.ripple_rgb)

        # bloom
        core_r = R * 0.34 * (1 + drive * 0.18) * self.bloom
        sl = box(core_r * 2.4)
        x = np.clip(dist[sl] / (core_r * 2.4), 0, 1)
        bloom_a = np.interp(x, [0, 0.20, 0.55, 1.0], [1.0, 0.80, 0.25, 0.0]).astype(np.float32)
        mix = np.clip((x - 0.20) / 0.35, 0, 1)[..., None]
        bloom_c = accent + (MID_F - accent) * mix
        blend(sl, bloom_a * (0.5 + drive * 0.5), bloom_c)

        # the rings sit over the bloom
        blend(full, line_i, accent)

        # the burst: four arms, long and short alternating, turning against the rings
        self.burst -= self.spin * rush * dt * 0.00028
        wobble = fx["bob"] * 0.25 * math.sin(t * 0.0105)       # playful: the star waggles
        radius = core_r * 1.5
        white = np.array([1.0, 1.0, 1.0], dtype=np.float32)
        sl = box(radius * (1 + drive * 0.55))
        bdx, bdy = dx[sl], dy[sl]
        for k in range(4):
            th = self.burst + wobble + (k / 4) * math.tau
            long = k % 2 == 0
            length = radius * (1.0 if long else 0.52) * (1 + drive * 0.55)
            half = radius * (0.13 if long else 0.085)
            c, s = math.cos(th), math.sin(th)
            u = bdx * c + bdy * s                             # along the arm
            v = -bdx * s + bdy * c                            # across it
            f = u / length
            cover = np.clip(half * (1 - f) - np.abs(v) + 0.6, 0, 1) * (u >= -0.5) * (f <= 1)
            fade = np.clip(1 - (f - 0.25) / 0.75, 0, 1)       # accent -> transparent past a quarter
            whiten = np.clip(1 - f / 0.25, 0, 1)[..., None]   # white -> accent in the first quarter
            colour = accent + (white - accent) * whiten
            blend(sl, cover * fade * (0.95 if long else 0.6), colour)

        # white-hot centre
        sl = box(core_r * 0.42)
        hx = np.clip(dist[sl] / (core_r * 0.42), 0, 1)
        heart_a = np.interp(hx, [0, 0.6, 1.0], [1.0, 1.0, 0.0]).astype(np.float32) * (0.85 + drive * 0.15)
        hmix = np.clip(hx / 0.6, 0, 1)[..., None]
        blend(sl, heart_a, white + (accent - white) * hmix)

        # caption, on its own small dark plate
        cap = caption_mask(caption)
        if cap is not None and caption_alpha > 0.02:
            sl = (slice(H - 32, H), slice(0, W))
            blend(sl, PLATE[sl] * (0.72 * caption_alpha), PLATE_RGB)
            blend(sl, cap[sl] * caption_alpha, np.clip(accent * 1.1, 0, 1))

        out = np.empty((H, W, 4), dtype=np.uint8)
        pm = np.clip(rgb, 0, 1) * 255.0
        out[..., 0], out[..., 1], out[..., 2] = pm[..., 2], pm[..., 1], pm[..., 0]
        out[..., 3] = np.clip(alpha, 0, 1) * 255.0
        return out


# --------------------------------------------------------- layered window API

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_byte)]


class Surface:
    """A per-pixel-alpha bitmap pushed to a window with UpdateLayeredWindow."""

    def __init__(self, hwnd: int) -> None:
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        u, g = self.user32, self.gdi32
        u.GetDC.restype = wintypes.HDC
        u.GetDC.argtypes = [wintypes.HWND]
        u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        g.CreateCompatibleDC.restype = wintypes.HDC
        g.CreateCompatibleDC.argtypes = [wintypes.HDC]
        g.CreateDIBSection.restype = wintypes.HBITMAP
        g.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                       ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
        g.SelectObject.restype = wintypes.HGDIOBJ
        g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
        u.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.c_void_p,
                                          ctypes.POINTER(wintypes.SIZE), wintypes.HDC,
                                          ctypes.POINTER(wintypes.POINT), wintypes.DWORD,
                                          ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
        self.hwnd = hwnd
        # layered + tool window (no taskbar button) + never takes focus
        style = u.GetWindowLongW(hwnd, -20) | 0x00080000 | 0x00000080 | 0x08000000
        u.SetWindowLongW(hwnd, -20, style)

        screen = u.GetDC(None)
        self.dc = g.CreateCompatibleDC(screen)
        u.ReleaseDC(None, screen)
        header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), W, -H, 1, 32, 0, W * H * 4, 0, 0, 0, 0)
        self.bits = ctypes.c_void_p()
        self.bitmap = g.CreateDIBSection(self.dc, ctypes.byref(header), 0, ctypes.byref(self.bits), None, 0)
        g.SelectObject(self.dc, self.bitmap)
        self.size = wintypes.SIZE(W, H)
        self.origin = wintypes.POINT(0, 0)
        self.blend = BLENDFUNCTION(0, 0, 255, 1)
        self.ghost = False
        self.always_ghost = False             # display only: clicks always pass through

    def presence(self, v: float) -> None:
        """Fade the whole window, 0..1. At zero it also stops catching the mouse."""
        self.blend.SourceConstantAlpha = int(255 * max(0.0, min(1.0, v)))
        ghost = v <= 0.0 or self.always_ghost
        if ghost != self.ghost:
            self.ghost = ghost
            style = self.user32.GetWindowLongW(self.hwnd, -20)
            style = (style | 0x20) if ghost else (style & ~0x20)      # WS_EX_TRANSPARENT
            self.user32.SetWindowLongW(self.hwnd, -20, style)

    def show(self, bgra: np.ndarray) -> None:
        ctypes.memmove(self.bits, bgra.ctypes.data, W * H * 4)
        self.user32.UpdateLayeredWindow(self.hwnd, None, None, ctypes.byref(self.size), self.dc,
                                        ctypes.byref(self.origin), 0, ctypes.byref(self.blend), 2)


# ------------------------------------------------------------------- the orb

class Orb:
    def __init__(self) -> None:
        import tkinter as tk
        self.state = {"mode": "offline", "level": 0.0}
        self.token, self.port = "", 8765
        self.read_address()

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        prefs = self.read_prefs()
        self.locked = bool(prefs.get("locked", False))
        self.hidden = bool(prefs.get("hidden", False))
        self.prefs_mtime = self.mtime()
        x, y = self.load_pos()
        self.root.geometry(f"{W}x{H}+{x}+{y}")
        self.root.update_idletasks()
        self.hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
        self.surface = Surface(self.hwnd)
        # Display only, by default: the orb never catches a click, so whatever
        # is behind it stays usable. "clickable": true in orb.json brings back
        # click-to-open, drag and the right-click menu.
        self.surface.always_ghost = not bool(prefs.get("clickable", False))

        self.root.bind("<ButtonPress-1>", self.press)
        self.root.bind("<B1-Motion>", self.drag)
        self.root.bind("<ButtonRelease-1>", self.release)
        self.root.bind("<Button-3>", self.menu_open)
        self.moved, self.origin = False, (0, 0)

        self.v_locked = tk.BooleanVar(value=self.locked)
        self.v_hidden = tk.BooleanVar(value=self.hidden)
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_checkbutton(label="Lock in place", variable=self.v_locked,
                                  command=lambda: self.set_prefs(locked=self.v_locked.get()))
        self.menu.add_checkbutton(label="Auto-hide (comes back when you say Nova)", variable=self.v_hidden,
                                  command=lambda: self.set_prefs(hidden=self.v_hidden.get()))
        self.menu.add_separator()
        self.menu.add_command(label="Open panel", command=self.open_panel)
        self.menu.add_command(label="Close orb", command=self.root.destroy)

        self.vis = 0.0 if self.hidden else 1.0     # how present the window is, eased
        self.last_active = 0.0                     # when she was last doing something
        self.blank = False                         # a fully faded frame has been pushed
        self.frames = 0
        self.was_active = False
        self.raised_at = 0.0
        self._swp = ctypes.windll.user32.SetWindowPos
        self._swp.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT]

        self.core = Core()
        self.level = 0.0
        self.caption, self.caption_alpha = "", 0.0
        self.last = time.time()
        self.t0 = time.time()

        try:
            (ROOT / "orb.pid").write_text(str(os.getpid()), encoding="utf-8")
        except OSError:
            pass
        threading.Thread(target=self.poll, daemon=True).start()
        self.hotkey_hits = 0
        threading.Thread(target=self.hotkey, daemon=True).start()
        self.tick()

    def hotkey(self) -> None:
        """Ctrl+Alt+N anywhere in Windows flips the orb between shown and
        hidden. Runs its own message loop; tick() does the actual toggle so tk
        is only touched from its own thread."""
        user32 = ctypes.windll.user32
        MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, WM_HOTKEY = 0x1, 0x2, 0x4000, 0x0312
        if not user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("N")):
            print("orb: Ctrl+Alt+N is already taken by another program", file=sys.stderr)
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                self.hotkey_hits += 1

    # ------------------------------------------------------------ plumbing
    def read_address(self) -> None:
        try:
            self.token = (ROOT / "token").read_text(encoding="utf-8").strip()
            url = (ROOT / "url.txt").read_text(encoding="utf-8")
            self.port = int(url.split(":")[2].split("/")[0])
        except (OSError, ValueError, IndexError):
            pass

    @staticmethod
    def read_prefs() -> dict:
        try:
            p = json.loads(POS_FILE.read_text(encoding="utf-8-sig"))
            return p if isinstance(p, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def mtime() -> float:
        try:
            return POS_FILE.stat().st_mtime
        except OSError:
            return 0.0

    def set_prefs(self, **changes) -> None:
        """Change position / locked / hidden, keeping whatever else is in the file."""
        p = self.read_prefs()
        p.setdefault("x", self.root.winfo_x())
        p.setdefault("y", self.root.winfo_y())
        p.update(changes)
        p["v"] = 3
        try:
            tmp = POS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(p), encoding="utf-8")
            os.replace(tmp, POS_FILE)
        except OSError:
            pass
        self.prefs_mtime = self.mtime()
        self.apply_prefs(p)

    def apply_prefs(self, p: dict) -> None:
        self.locked = bool(p.get("locked", False))
        self.surface.always_ghost = not bool(p.get("clickable", False))
        hidden = bool(p.get("hidden", False))
        if hidden and not self.hidden:
            self.last_active = 0.0                  # told to go: go now, do not linger
        self.hidden = hidden
        self.v_locked.set(self.locked)
        self.v_hidden.set(self.hidden)

    def sync_prefs(self) -> None:
        """Pick up a change someone else made to orb.json (the bridge, Nova.ps1)."""
        m = self.mtime()
        if m == self.prefs_mtime:
            return
        self.prefs_mtime = m
        self.apply_prefs(self.read_prefs())

    def load_pos(self) -> tuple[int, int]:
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        try:
            p = self.read_prefs()
            if p.get("v") == 3 and 0 <= p["x"] < sw - 40 and 0 <= p["y"] < sh - 40:
                return int(p["x"]), int(p["y"])
        except (KeyError, TypeError):
            pass
        return sw - W - 8, sh - H - 46

    def poll(self) -> None:
        while True:
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/orb",
                                             headers={"X-Nova-Token": self.token})
                with urllib.request.urlopen(req, timeout=2) as r:
                    self.state = json.loads(r.read().decode())
            except Exception:                         # noqa: BLE001
                self.state = {"mode": "offline", "level": 0.0}
                self.read_address()                   # the bridge may have restarted
                time.sleep(2)
            time.sleep(0.08)

    # ------------------------------------------------------------- drawing
    def tick(self) -> None:
        now = time.time()
        dt, self.last = min(0.1, now - self.last), now
        mode = self.state.get("mode", "offline")
        if mode not in STATES:
            mode = "idle"
        want = float(self.state.get("level", 0.0)) if mode == "speaking" else 0.0
        self.level += (want - self.level) * min(1.0, dt * (22 if want > self.level else 8))

        flavour = str(self.state.get("flavour") or "")
        text = caption_for(mode, flavour)
        if text and text == self.caption:
            self.caption_alpha = min(1.0, self.caption_alpha + dt * 5)
        else:
            self.caption_alpha = max(0.0, self.caption_alpha - dt * 5)
            if self.caption_alpha == 0.0:
                self.caption = text

        # Auto-hide: she is on screen while she is doing something and for a
        # few seconds after, and otherwise out of the way. "Offline" does not
        # count as doing something -- a stopped bridge should not summon her.
        self.frames += 1
        if self.hotkey_hits:
            self.hotkey_hits = 0
            self.set_prefs(hidden=not self.hidden)
            self.apply_prefs(self.read_prefs())
        elif self.frames % 10 == 0:
            self.sync_prefs()
        active = mode not in ("idle", "offline")
        if active:
            self.last_active = now
        wanted = (not self.hidden) or now - self.last_active < LINGER
        # Stay above every other window. "-topmost" is set once at start, and
        # any other always-on-top window (the taskbar, a game, an overlay)
        # that comes forward afterwards lands on top of her. So: re-assert it
        # the moment she becomes active, and every second while she is shown.
        if wanted and (active and not self.was_active or now - self.raised_at > 1.0):
            self.raise_top()
            self.raised_at = now
        self.was_active = active
        if wanted:
            self.vis = min(1.0, self.vis + dt * 5.0)          # in: a fifth of a second
        else:
            self.vis = max(0.0, self.vis - dt * 1.6)          # out: a little slower
        self.surface.presence(self.vis)

        if self.vis > 0.0 or not self.blank:                  # nothing to draw while she is away
            try:
                self.surface.show(self.core.frame((now - self.t0) * 1000.0, mode, self.level,
                                                  self.caption, self.caption_alpha, flavour=flavour))
                self.blank = self.vis <= 0.0
            except Exception:                         # noqa: BLE001 -- never let a bad frame kill the orb
                pass
        # A steady 30 fps: wait out only what is left of the 33 ms slot, not a
        # whole 33 ms on top of the time the frame took to draw.
        spent = (time.time() - now) * 1000.0
        self.root.after(max(1, int(33 - spent)) if self.vis > 0.0 else 120, self.tick)

    def raise_top(self) -> None:
        """Back to the top of the always-on-top band, without taking focus."""
        HWND_TOPMOST = wintypes.HWND(-1)
        flags = 0x0001 | 0x0002 | 0x0010 | 0x0200          # NOSIZE | NOMOVE | NOACTIVATE | NOOWNERZORDER
        try:
            self._swp(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
        except Exception:                             # noqa: BLE001
            pass

    # ------------------------------------------------------------ pointer
    def press(self, e) -> None:
        self.moved, self.origin = False, (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def drag(self, e) -> None:
        if self.locked:
            return
        self.moved = True
        self.root.geometry(f"+{e.x_root - self.origin[0]}+{e.y_root - self.origin[1]}")

    def release(self, e) -> None:
        if self.moved:
            self.set_prefs(x=self.root.winfo_x(), y=self.root.winfo_y())
            return
        self.open_panel()

    def open_panel(self) -> None:
        subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                          str(ROOT / "Nova.ps1"), "open"], creationflags=0x08000000)

    def menu_open(self, e) -> None:
        # The window never takes focus, and a Windows popup menu only closes on
        # an outside click if its owner is in the foreground -- so ask for that.
        try:
            ctypes.windll.user32.SetForegroundWindow(self.hwnd)
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()


def preview(path: str) -> None:
    """Every state side by side on a dark and a light backdrop, for checking the design."""
    modes = ["idle", "listening", "thinking", "speaking", "approval", "offline"]
    sheet = Image.new("RGB", (W * len(modes), H * 2), (0, 0, 0))
    for row, backdrop in enumerate([(24, 26, 34), (226, 228, 232)]):
        for col, mode in enumerate(modes):
            core = Core()
            bgra = None
            for step in range(60):                    # let the easing settle, as it would on screen
                bgra = core.frame(4000 + step * 33.0, mode, 0.09 if mode == "speaking" else 0.0,
                                  CAPTION[mode], 1.0)
            bgra = bgra.astype(np.float32)
            a = bgra[..., 3:4] / 255.0
            rgb = bgra[..., [2, 1, 0]] + np.array(backdrop, dtype=np.float32) * (1 - a)
            sheet.paste(Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)), (col * W, row * H))
    sheet.save(path)
    print("wrote", path)

    # Every flavour, on the dark backdrop, plus a crossfade strip.
    names = list(FLAVOURS)
    cols = 6
    rows = (len(names) + cols - 1) // cols + 1
    sheet = Image.new("RGB", (W * cols, H * rows), (24, 26, 34))
    for n, name in enumerate(names):
        mode = "speaking" if name in ("playful", "warm", "sorry", "alert", "done") else "thinking"
        core = Core()
        for step in range(40):
            bgra = core.frame(4000 + step * 33.0, mode, 0.09 if mode == "speaking" else 0.0,
                              caption_for(mode, name) or name.upper(), 1.0, flavour=name)
        bgra = bgra.astype(np.float32)
        a = bgra[..., 3:4] / 255.0
        rgb = bgra[..., [2, 1, 0]] + np.array((24, 26, 34), dtype=np.float32) * (1 - a)
        sheet.paste(Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)), ((n % cols) * W, (n // cols) * H))
    # the change itself: thinking/searching -> speaking/playful, every 66 ms
    core = Core()
    for step in range(40):
        core.frame(1000 + step * 33.0, "thinking", 0.0, "", 0.0, flavour="searching")
    for c in range(cols):
        for _ in range(2):
            bgra = core.frame(core.last + 33.0, "speaking", 0.09, "", 0.0, flavour="playful")
        bgra = bgra.astype(np.float32)
        a = bgra[..., 3:4] / 255.0
        rgb = bgra[..., [2, 1, 0]] + np.array((24, 26, 34), dtype=np.float32) * (1 - a)
        sheet.paste(Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)), (c * W, (rows - 1) * H))
    alt = str(Path(path).with_name(Path(path).stem + "-flavours.png"))
    sheet.save(alt)
    print("wrote", alt)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--preview":
        preview(sys.argv[2])
    else:
        Orb().root.mainloop()
    sys.exit(0)
