#!/usr/bin/env python3
"""
events_test.py -- reminders, manners, routines, on a private silent bridge.
Changes nothing on the PC: routines are matched but only state-only ones run.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import nova_events as E  # noqa: E402

checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("  ok   " if ok else "  FAIL ") + name + (f"   {detail}" if detail else ""))


# ---- when-parsing
base = time.mktime((2026, 11, 12, 10, 0, 0, 0, 0, -1))
w = E.parse_when("remind me to stretch in 20 minutes", base)
check("'in 20 minutes'", w and abs(w[0] - (base + 1200)) < 1 and w[1] == "")
w = E.parse_when("at 5 pm call mum", base)
check("'at 5 pm' is today 17:00", w and time.localtime(w[0])[3:5] == (17, 0))
w = E.parse_when("at 9:30 check the server", base)
check("'at 9:30' already past -> tomorrow", w and w[0] - base > 80000)
w = E.parse_when("every day at 08:00 brief me", base)
check("'every day at 08:00' is daily", w and w[1] == "08:00")
check("no time given -> not a reminder", E.parse_when("remind me to stretch", base) is None)

# ---- quiet hours
at = lambda h, m: time.struct_time((2026, 11, 12, h, m, 0, 3, 316, -1))
check("quiet hours across midnight", E.in_quiet_hours("23:30-07:00", at(2, 0))
      and not E.in_quiet_hours("23:30-07:00", at(12, 0)) and not E.in_quiet_hours("", at(2, 0)))

# ---- routine matching: the phrase itself, not a mention of it
check("'game mode' triggers", E.match_routine("Nova, game mode") == "game-mode")
check("'activate work mode please' triggers", E.match_routine("activate work mode please") == "work-mode")
check("'good night' triggers", E.match_routine("Good night.") == "good-night")
check("a sentence mentioning it does not", E.match_routine("what does game mode do") is None)

# ---- manners: held while gaming, delivered afterwards
said = []
ev = E.Events(announce=said.append)
ev.state["game_mode"] = True
r1 = ev.announce("the render finished.")
r2 = ev.announce("the server went down.", urgent=True)
check("non-urgent is held during a game, urgent is spoken", r1.startswith("held") and r2 == "spoken"
      and said == ["the server went down."])
ev.state["game_mode"] = False
if not E.gaming():
    ev.flush_held()
    check("held items are delivered afterwards", len(said) == 2 and "render finished" in said[1], said[-1][:60])

# ---- reminders: due, and daily rescheduling
import tempfile
E.REMINDERS_FILE = Path(tempfile.mkdtemp()) / "reminders.json"
rem = E.Reminders()
rem.add("stretch", time.time() - 1)
rem.add("brief me", time.time() - 1, daily="08:00")
rem.add("later", time.time() + 3600)
due = rem.due()
left = rem.list()
check("two are due, the daily one is rescheduled", len(due) == 2 and len(left) == 2
      and any(i["text"] == "brief me" and i["at"] > time.time() for i in left))
check("cancel by words", len(rem.cancel("later")) == 1 and len(rem.list()) == 1)

# ---- through the bridge: a routine and a reminder answer instantly, no model
PORT = 8796
TEST_DIR = Path(tempfile.mkdtemp(prefix="nova-test-"))
env = dict(os.environ, NOVA_TEST_DIR=str(TEST_DIR), NOVA_CLAUDE_STUB=str(ROOT / "setup" / "claude_stub.py"),
           NOVA_VOICE_VOLUME="0", NOVA_TEST="1", PYTHONIOENCODING="utf-8")
proc = subprocess.Popen(creationflags=0x08000000, args=[sys.executable, str(ROOT / "nova_bridge.py"), "--port", str(PORT)],
                        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
token = (ROOT / "token").read_text(encoding="utf-8").strip()


def api(path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data,
                                 headers={"X-Nova-Token": token, "Content-Type": "application/json"},
                                 method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


real = ROOT / "reminders.json"
before = real.read_text(encoding="utf-8") if real.exists() else None
try:
    for _ in range(80):
        try:
            api("/api/voice", timeout=2)
            break
        except Exception:
            time.sleep(0.25)
    t = time.time()
    r = api("/api/ask", {"prompt": "Nova, remind me to stretch in 20 minutes", "speak": False})
    check("reminder set instantly, with no model turn", r.get("turns") == 0 and "remind you" in r["text"]
          and time.time() - t < 1.5, r["text"])
    items = api("/api/reminders")["reminders"]
    check("it is stored with the right text", any(i["text"] == "stretch" for i in items), str([i["text"] for i in items]))
    api("/api/reminders/cancel", {"which": "stretch"})
    check("routines are listed", "game-mode" in api("/api/routines")["routines"])
    r = api("/api/ask", {"prompt": "remind me what we did yesterday", "speak": False})
    check("'remind me what we did' is a question, not a reminder", r.get("turns") == 1, r["text"][:50])
finally:
    proc.kill()
    if before is None:
        real.unlink(missing_ok=True)
    else:
        real.write_text(before, encoding="utf-8")

print("RESULT:", "PASS" if all(checks) else "NEEDS WORK")
