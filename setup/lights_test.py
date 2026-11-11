#!/usr/bin/env python3
"""
lights_test.py -- the case lighting always goes back to Sir's own setting,
including after Nova is killed mid-colour. Uses a fake RGB panel; the real
lights are not touched.
"""
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import nova_presence as P  # noqa: E402

panel = {"state": {"mode": "rainbow", "color": "#112233", "brightness": 80}}


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send({"state": panel["state"], "connected": True})

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        panel["state"] = {**panel["state"], **data}
        self._send({"ok": True})


srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
P.RGB_URL = f"http://127.0.0.1:{srv.server_address[1]}"
P.BASELINE_FILE = Path(tempfile.mkdtemp()) / "lights-baseline.json"
P.cfg = lambda: {"rgb": True, "pocket": False}
MINE = dict(panel["state"])

checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("  ok   " if ok else "  FAIL ") + name + (f"   {detail}" if detail else ""))


mode = {"v": "idle"}
lights = P.Lights(lambda: mode["v"])


def step(m):
    mode["v"] = m
    lights.last_change = 0
    lights.step()


step("idle");      check("idle: lights untouched", panel["state"] == MINE)
step("speaking");  check("speaking: her colour, his brightness",
                         panel["state"]["color"] == P.COLOURS["speaking"] and panel["state"]["brightness"] == 80)
check("his setting is saved to disk while she has the lights", json.loads(P.BASELINE_FILE.read_text()) == MINE)
step("approval");  check("approval: amber", panel["state"]["color"] == P.COLOURS["approval"])
step("idle");      check("idle again: exactly his setting, file gone",
                         panel["state"] == MINE and not P.BASELINE_FILE.exists(), str(panel["state"]))

# killed mid-colour, then a fresh start
step("thinking")
check("mid-colour before the kill", panel["state"]["color"] == P.COLOURS["thinking"])
lights2 = P.Lights(lambda: "idle")              # a new process: no memory of the old one
check("a fresh start restores his setting at once", panel["state"] == MINE and not P.BASELINE_FILE.exists(),
      str(panel["state"]))
mode["v"] = "idle"
lights2.step()
lights2.last_change = 0
mode2 = "speaking"
lights2.mode_fn = lambda: mode2
lights2.step()
check("and her colour is never mistaken for his afterwards", lights2.baseline == MINE, str(lights2.baseline))

print("RESULT:", "PASS" if all(checks) else "NEEDS WORK")
