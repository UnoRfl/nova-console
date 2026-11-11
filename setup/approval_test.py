#!/usr/bin/env python3
"""
approval_test.py -- the approval gate end to end, silently, on a private bridge.

Calls the same `approve` function the Claude CLI calls, and answers through
the same route the panel button and the listener use.
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8798
os.environ["NOVA_PORT"] = str(PORT)
sys.path.insert(0, str(ROOT))
import nova_tools as T  # noqa: E402

checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("  ok   " if ok else "  FAIL ") + name + (f"   {detail}" if detail else ""))


env = dict(os.environ, NOVA_CLAUDE_STUB=str(ROOT / "setup" / "claude_stub.py"),
           NOVA_VOICE_VOLUME="0", NOVA_TEST="1", PYTHONIOENCODING="utf-8")
proc = subprocess.Popen([sys.executable, str(ROOT / "nova_bridge.py"), "--port", str(PORT)],
                        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
token = (ROOT / "token").read_text(encoding="utf-8").strip()


def api(path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data,
                                 headers={"X-Nova-Token": token, "Content-Type": "application/json"},
                                 method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def answer_after(seconds, allow):
    def work():
        time.sleep(seconds)
        api("/api/permission/answer", {"allow": allow, "by": "test"})
    threading.Thread(target=work, daemon=True).start()


try:
    for _ in range(80):
        try:
            api("/api/voice", timeout=2)
            break
        except Exception:
            time.sleep(0.25)

    t = time.time()
    r = json.loads(T.approve("Read", {"file_path": "C:\\x.txt"}))
    check("reading is allowed at once, no question", r["behavior"] == "allow" and time.time() - t < 1)

    r = json.loads(T.approve("mcp__nova__set_volume", {"percent": 20}))
    check("small reversible action is allowed without asking", r["behavior"] == "allow")

    answer_after(1.5, True)
    t = time.time()
    seen = {}
    threading.Timer(0.8, lambda: seen.update(api("/api/listen"))).start()
    r = json.loads(T.approve("Bash", {"command": "rm -rf build"}))
    check("risky action waits, then is allowed on yes", r["behavior"] == "allow" and time.time() - t > 1.2,
          f"{time.time() - t:.1f}s")
    check("the listener is told an answer is awaited", seen.get("approval") is True)
    check("input is passed through unchanged", r.get("updatedInput") == {"command": "rm -rf build"})

    answer_after(1.0, False)
    r = json.loads(T.approve("mcp__nova__sleep_pc", {}))
    check("a no refuses it", r["behavior"] == "deny", r.get("message", "")[:70])

    t = time.time()
    r = json.loads(T.approve("Write", {"file_path": "C:\\Windows\\x.txt"}))
    waited = time.time() - t
    check("silence refuses it after the wait", r["behavior"] == "deny" and 28 < waited < 40, f"{waited:.0f}s")
    check("nothing is left pending", api("/api/listen").get("approval") is False)

    r = json.loads(T.approve("mcp__nova__some_new_tool", {}))  # nobody answers
    check("an unknown tool is refused by default", r["behavior"] == "deny")
finally:
    proc.kill()

print("RESULT:", "PASS" if all(checks) else "NEEDS WORK")
