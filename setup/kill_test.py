#!/usr/bin/env python3
"""
kill_test.py -- a turn waiting on a Claude process that is killed from another
thread must be released at once (it used to wait out the whole timeout), and
the process's children must die with it.

Offline: the "Claude process" is a Python that starts a child and sleeps.

    python setup/kill_test.py
"""
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import nova_bridge as nb                                            # noqa: E402

bad = 0


def check(ok: bool, what: str, extra: str = "") -> None:
    global bad
    bad += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {what}  {extra}")


b = nb.Backend({"model": "haiku", "workdir": str(Path.home())}, nb.Activity())
child_code = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); print(p.pid, flush=True); time.sleep(120)"
b.proc = subprocess.Popen([sys.executable, "-c", child_code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, text=True, creationflags=nb.NO_WINDOW)
parent = b.proc
grandchild = int(parent.stdout.readline().strip())
b.done.clear()

waited = {}


def turn() -> None:
    t = time.time()
    waited["released"] = b.done.wait(30)
    waited["s"] = time.time() - t


th = threading.Thread(target=turn)
th.start()
time.sleep(0.5)
t0 = time.time()
b.kill()                                   # another thread: "new chat", the idle reaper
th.join(35)
check(waited.get("released") is True and waited.get("s", 99) < 5, "the waiting turn is released by kill()",
      f"{waited.get('s', 99):.1f}s (was the full timeout)")
check(time.time() - t0 < 8, "kill() itself returns quickly", f"{time.time() - t0:.1f}s")
time.sleep(0.5)
check(parent.poll() is not None, "the process is dead")
alive = subprocess.run(["tasklist", "/FI", f"PID eq {grandchild}", "/NH"], capture_output=True, text=True,
                       creationflags=nb.NO_WINDOW).stdout
check(str(grandchild) not in alive, "its child process died with it (no orphan)")
check(b.proc is None and not b.alive(), "the backend knows it has no process")
b.kill()
check(True, "killing again is harmless")

print("RESULT:", "PASS" if not bad else "FAIL")
sys.exit(1 if bad else 0)
