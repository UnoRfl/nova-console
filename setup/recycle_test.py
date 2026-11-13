"""Chat recycling: a refreshed chat keeps the thread and answers at warm speed.

Uses the real Claude CLI (Haiku, a few cents). Writes nothing to the live
install: NOVA_TEST points log/memory/usage at a temp folder.
Run:  venv\\Scripts\\python.exe setup\\recycle_test.py
"""
import os
import sys
import time
from pathlib import Path

os.environ["NOVA_TEST"] = "1"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import nova_bridge as nb  # noqa: E402

ok = True


def check(name, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  ({extra})" if extra else ""))


cfg = nb.load_config()
cfg["model"] = "haiku"
b = nb.Backend(cfg, nb.Activity())


def turn(text):
    t0 = time.time()
    out = b.ask_fixed(text, "haiku", False, timeout=120)
    ms = int((time.time() - t0) * 1000)
    if "error" not in out:
        b.record(text, out.get("text", ""), "fixed")
    return out, ms


# 1. cold start, plant a fact only the conversation knows
out, cold_ms = turn("For this chat only, the code word is PELICAN-42. Do not save it to memory. Reply with just: noted")
check("first turn answered", "error" not in out, out.get("error", ""))
check("context size tracked", b.ctx_tokens > 0 and b.ctx_base > 0, f"base {b.ctx_base}, now {b.ctx_tokens}")
out, warm_ms = turn("Reply with just the word: ready")
check("small chat is not stale", b.stale() == "", b.stale())

# 2. idle with history -> housekeeping swaps in a warm spare
b.cfg["session"] = {"idle_minutes": 0, "idle_extra_tokens": 0, "max_extra_tokens": 10**9}
b.last_used = time.time() - 5
old_pid = b.proc.pid
b.housekeep()
check("idle chat refreshed", b.proc is not None and b.proc.pid != old_pid and b.carry_due)
check("spare already running before he speaks", b.alive())
time.sleep(4)                         # he is idle; the spare finishes booting meanwhile
b.cfg["session"] = {}                 # back to defaults so the next turn is not refreshed again

out, spare_ms = turn("What was the code word I gave you? Reply with just the code word.")
check("thread carried into fresh chat", "PELICAN-42" in out.get("text", "").upper(), out.get("text", "")[:80])
check("carry consumed", not b.carry_due)
check("fresh chat is small again", b.ctx_tokens - b.ctx_base < 4000, f"extra {b.ctx_tokens - b.ctx_base}")

# 3. size cap trips between turns
b.cfg["session"] = {"max_extra_tokens": 1}
out, _ = turn("Reply with just the word: one")
why = b.stale()
check("size cap detected", "grew" in why, why)
b.cfg["session"] = {}

print(f"\nlatency: cold first turn {cold_ms} ms, warm turn {warm_ms} ms, first turn on warm spare {spare_ms} ms")
check("warm spare is not a cold start", spare_ms < cold_ms, f"{spare_ms} vs {cold_ms}")
b.kill()
print("\nALL PASS" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
