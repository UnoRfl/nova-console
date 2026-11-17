#!/usr/bin/env python3
"""cancel_test.py -- "Nova, stop" while she is thinking (2026-11-17).

A turn waiting on a Claude process must give up at once when cancel bumps
CANCEL["gen"] and kills the process -- and must NOT retry on a fresh process
or fall through to another engine. Offline: the "Claude process" is a Python
that reads stdin and never answers.

    python setup/cancel_test.py
"""
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import nova_bridge as nb                                            # noqa: E402

bad = 0


def check(ok, what, extra=""):
    global bad
    bad += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {what}  {extra}")


b = nb.Backend({"model": "haiku", "workdir": str(Path.home())}, nb.Activity())
spawned = []
b.spawn = lambda *a, **k: spawned.append(1) or False     # a retry would show up here
b.proc = subprocess.Popen([sys.executable, "-c", "import sys,time; sys.stdin.readline(); time.sleep(120)"],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          text=True, creationflags=nb.NO_WINDOW)
b.alive = lambda: b.proc is not None and b.proc.poll() is None
b.stale = lambda: ""
got = {}


def turn():
    t = time.time()
    got["out"] = b.ask_fixed("open blender", None, False, timeout=60)
    got["s"] = time.time() - t


th = threading.Thread(target=turn)
th.start()
time.sleep(0.8)
check(b.lock.locked(), "the turn is in flight (lock held)")
nb.CANCEL["gen"] += 1
b.kill()
th.join(10)
check(not th.is_alive(), "the turn returned after the cancel", f"{got.get('s', 0):.2f}s")
check(got.get("out", {}).get("error") == "cancelled", "it says cancelled, not an error to speak", str(got.get("out")))
check(not spawned, "no retry on a fresh process")
check(got.get("s", 99) < 3, "and quickly")

print("\nALL PASS" if not bad else f"\n{bad} FAILED")
sys.exit(1 if bad else 0)
