#!/usr/bin/env python3
"""steady_voice_test.py -- she must not change voices mid-conversation.

No sound and no network: Microsoft's voice is replaced by a stand-in that can
be told to stall, fail or answer.
"""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import nova_voice as V  # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        fails.append(name)


EDGE = np.full(2400, 0.25, dtype=np.float32)          # what "her usual voice" returns
state = {"mode": "ok", "calls": 0}


def fake_edge(text, cfg, voice=None, timeout=5.0, tries=2):
    state["calls"] += 1
    if state["mode"] == "fail":
        raise TimeoutError("stalled")
    return EDGE


V.synth_edge = fake_edge
events = []
V.ON_EVENT = events.append

v = V.Voice()
v.cfg["engine"] = "edge"
v.cfg["volume"] = 0.0
v.probe_wait = 0.3
ACK = "Right away, Sir."

# 1. healthy: her usual voice, stock line remembered as that voice
a = v.synth(ACK)
check("healthy sentence comes from edge", v.last_engine == "edge" and a is EDGE)
check("stock line kept with its engine", v.cache.get(ACK, ("", None))[0] == "edge")

# 2. outage: falls to the offline voice ONCE, then stops asking edge inside replies
state["mode"], state["calls"] = "fail", 0
b = v.synth("The server is up.")
check("outage falls back to kokoro", v.last_engine == "kokoro" and len(b) > 0)
check("edge is marked out", "edge" in v.out and v.chain() == ["kokoro"])
n = state["calls"]
v.synth("Three players are online.")
v.synth("Nothing else to report.")
check("no more edge attempts inside the reply", state["calls"] <= n + 2, f"calls {n} -> {state['calls']}")

# 3. the stock line must not come back in the OTHER voice
c = v.synth(ACK)
check("stock line re-voiced to match during outage", c is not EDGE and v.cache[ACK][0] == "kokoro")

# 4. service returns while she is mid-reply: no change until the reply ends
v.active = True
state["mode"] = "ok"
time.sleep(1.2)
check("does not switch back mid-reply", "edge" in v.out)
v.active = False
time.sleep(1.2)
check("switches back between replies", "edge" not in v.out and v.chain()[0] == "edge")
d = v.synth(ACK)
check("stock line back in her usual voice", d is EDGE and v.cache[ACK][0] == "edge")
check("changes were logged", any("offline voice" in e for e in events) and any("answering again" in e for e in events),
      f"{len(events)} events")

# 5. a stalled connection is retried, not handed to the other voice
import importlib
R = importlib.reload(V)
import asyncio, edge_tts  # noqa: E401,E402
tries = {"n": 0}


class Stall:
    def __init__(self, *a, **k):
        tries["n"] += 1
        self.n = tries["n"]

    async def stream(self):
        if self.n == 1:
            await asyncio.sleep(30)                   # first connection hangs
        yield {"type": "audio", "data": b"x"}


edge_tts.Communicate = Stall
R.decode_audio = lambda data, rate=24000: np.ones(4800, dtype=np.float32) * 0.2
t = time.time()
out = R.synth_edge("Hello.", {"speed": 1.0, "edge_voice": "x"}, timeout=0.5)
check("stalled connection retried on a fresh one", tries["n"] == 2 and len(out) > 0 and time.time() - t < 3,
      f"{tries['n']} tries, {time.time() - t:.1f}s")

print("\nRESULT:", "PASS" if not fails else f"FAILED ({len(fails)}): " + ", ".join(fails))
sys.exit(1 if fails else 0)
