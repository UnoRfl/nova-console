#!/usr/bin/env python3
"""
nova_presence.py -- Nova showing up outside the screen.

  Case lighting   follows what she is doing: green when she hears you, magenta
                  while thinking, violet while speaking, amber when she needs
                  a yes. When she goes idle, your own lighting comes back
                  exactly as it was.
  Pocket Nova     gets a short note when she needs your approval or has
                  something to tell you.

Both are best-effort. If the RGB Panel or the Pocket Nova panel is not
running, nothing happens and nothing is logged as an error.

Turn either off in config.json:  "presence": {"rgb": false, "pocket": false}
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path.home() / ".nova"
CONFIG_FILE = ROOT / "config.json"
RGB_URL = "http://127.0.0.1:8781"          # ~/RGBPanel/server.py
POCKET_URL = "http://127.0.0.1:47800"      # ~/PocketNovaPanel

BASELINE_FILE = ROOT / "lights-baseline.json"   # Sir's lighting, while Nova is borrowing the lights

COLOURS = {"listening": "#39e58c", "thinking": "#ff4fd8", "speaking": "#9a6bff", "approval": "#ffb000"}


def cfg() -> dict:
    out = {"rgb": True, "pocket": True}
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        out.update({k: bool(v) for k, v in (raw.get("presence") or {}).items() if k in out})
    except (OSError, ValueError, AttributeError):
        pass
    return out


def _http(url: str, body: dict | None = None, headers: dict | None = None, timeout: float = 2) -> dict | None:
    try:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        req = urllib.request.Request(url, data=data, headers=h, method="POST" if body is not None else "GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except Exception:                                 # noqa: BLE001 -- best effort by design
        return None


def pocket_note(text: str) -> bool:
    """A short line on the Pocket Nova's screen."""
    if not cfg()["pocket"]:
        return False
    r = _http(POCKET_URL + "/api/cmd", {"cmd": "alert", "kind": "note", "text": str(text)[:40]},
              headers={"X-PocketNova": "1"})
    return bool(r and r.get("sent"))


class Lights:
    """Drives the case lighting from Nova's state, and always puts it back."""

    def __init__(self, mode_fn) -> None:
        self.mode_fn = mode_fn             # () -> "idle" | "listening" | "thinking" | "speaking" | "approval"
        self.baseline: dict | None = None  # Sir's own lighting, captured before we touch it
        self.shown = "idle"
        self.last_change = 0.0
        # If Nova was killed while the lights were hers, the file is still
        # there. Put Sir's lighting back before doing anything else; without
        # this, the next start would mistake her colour for his.
        try:
            saved = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
            if isinstance(saved, dict) and self._set(saved):
                BASELINE_FILE.unlink(missing_ok=True)
            else:
                self.baseline = saved if isinstance(saved, dict) else None
                self.shown = "unknown"            # forces a restore on the first idle step
        except (OSError, ValueError):
            pass

    def _set(self, state: dict) -> bool:
        return _http(RGB_URL + "/api/set", state) is not None

    def step(self) -> None:
        mode = self.mode_fn()
        if mode == self.shown or time.time() - self.last_change < 0.35:
            return
        if not cfg()["rgb"]:
            if self.baseline:                          # switched off mid-flight: restore and stop
                self._set(self.baseline)
                self.baseline, self.shown = None, "idle"
            return
        if mode == "idle":
            if self.baseline:
                if not self._set(self.baseline):
                    return                             # panel busy: try again next step
                self.baseline = None
                BASELINE_FILE.unlink(missing_ok=True)
            self.shown = "idle"
            self.last_change = time.time()
            return
        if self.baseline is None:
            got = _http(RGB_URL + "/api/state")
            if not got or not got.get("connected") or not isinstance(got.get("state"), dict):
                return                                 # panel not running: leave the lights alone
            self.baseline = got["state"]
            try:                                       # survive being killed mid-colour
                BASELINE_FILE.write_text(json.dumps(self.baseline), encoding="utf-8")
            except OSError:
                pass
        if self._set({"mode": "static", "color": COLOURS.get(mode, "#9a6bff"),
                      "brightness": self.baseline.get("brightness", 100)}):
            self.shown = mode
            self.last_change = time.time()

    def restore(self) -> None:
        if self.baseline and self._set(self.baseline):
            self.baseline, self.shown = None, "idle"
            BASELINE_FILE.unlink(missing_ok=True)

    def loop(self) -> None:
        while True:
            try:
                self.step()
            except Exception:                         # noqa: BLE001
                pass
            time.sleep(0.15)

    def start(self) -> None:
        threading.Thread(target=self.loop, daemon=True).start()
