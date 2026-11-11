#!/usr/bin/env python3
"""
harness_test.py -- the whole reply path against the stand-in CLI, silently.

Starts a private bridge on a spare port with NOVA_CLAUDE_STUB set and the
volume at zero, then checks: streamed speech starts before the reply ends,
projector blocks and memory markers are never spoken, a tool turn gets an
acknowledgement, usage is tallied, and the job table works.
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
import nova_harness as H  # noqa: E402

PORT = 8799
STARTED = time.strftime("%Y-%m-%d %H:%M:%S")
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("  ok   " if ok else "  FAIL ") + name + (f"   {detail}" if detail else ""))


# ---- unit: the sentence streamer
got = []
st = H.SentenceStreamer(got.append)
for piece in ["Hello, Sir. ", "The disk is ", "fine. Here it is. :", ":CAD title=x\nreturn [];\n::END"]:
    st.push(piece)
st.flush()
check("streamer splits sentences and withholds the block",
      " ".join(got) == "Hello, Sir. The disk is fine. Here it is.", str(got))
got2 = []
st = H.SentenceStreamer(got2.append)
st.push("Noted, Sir. [[remember: x is y]]")
st.flush()
check("streamer withholds memory markers", got2 == ["Noted, Sir."], str(got2))

# ---- integration: a private bridge on a spare port
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


try:
    for _ in range(80):
        try:
            api("/api/voice", timeout=2)
            break
        except Exception:
            time.sleep(0.25)
    for _ in range(60):                               # wait for the voice model
        if api("/api/voice")["engine"] == "kokoro":
            break
        time.sleep(0.5)
    check("bridge up with local voice", api("/api/voice")["engine"] == "kokoro")
    time.sleep(1.5)

    # 1. a plain reply: speech must start before the reply has finished arriving
    t0 = time.time()
    first_sound = None
    import threading
    result = {}
    th = threading.Thread(target=lambda: result.update(api("/api/ask", {"prompt": "hello there", "speak": True})))
    th.start()
    while th.is_alive():
        if first_sound is None and api("/api/voice")["speaking"]:
            first_sound = time.time() - t0
        time.sleep(0.05)
    reply_done = time.time() - t0
    check("reply returned", result.get("text", "").startswith("Good evening"), f"{reply_done:.2f}s")
    check("bridge reports it spoke", result.get("spoken") is True)
    spoke = api("/api/speak", {"text": result["text"], "streamed": True}, timeout=120)
    check("all four sentences were spoken", spoke.get("spoken") == 4, str(spoke))
    check("first audio came before the reply had finished arriving",
          0 < spoke.get("first_audio_s", 99) < reply_done,
          f"first audio {spoke.get('first_audio_s')}s, full reply {reply_done:.2f}s")

    # 2. a projector block is delivered but not spoken
    r = api("/api/ask", {"prompt": "show me a shape", "speak": True})
    spoke = api("/api/speak", {"text": r["text"], "streamed": True}, timeout=120)
    check("block reaches the panel", "::CAD" in r["text"])
    check("only the prose was spoken", spoke.get("spoken") == 1, str(spoke))

    # 3. memory marker: stored, stripped, not spoken
    r = api("/api/ask", {"prompt": "my favourite test colour", "speak": True})
    spoke = api("/api/speak", {"text": r["text"], "streamed": True}, timeout=120)
    check("marker stripped and stored", "[[" not in r["text"] and r.get("remembered"), str(r.get("remembered")))
    check("marker not spoken", spoke.get("spoken") == 2, str(spoke))
    import nova_memory
    nova_memory.forget_matching("favourite test colour teal")

    # 4. a tool turn gets an acknowledgement first
    r = api("/api/ask", {"prompt": "use a tool to check the pc", "speak": True})
    spoke = api("/api/speak", {"text": r["text"], "streamed": True}, timeout=120)
    check("tool turn: acknowledgement + answer spoken", spoke.get("spoken") == 3, str(spoke))

    # 5. speak:false means silence
    r = api("/api/ask", {"prompt": "hello again", "speak": False})
    check("muted turn is not spoken", r.get("spoken") is False)

    # 6. usage and jobs
    u = api("/api/usage")
    check("usage tallied", u["engines"].get("claude", {}).get("requests", 0) >= 4, json.dumps(u["engines"]))
    j = api("/api/jobs")
    check("job table and engine list", "claude" in j["engines"], json.dumps(j["engines"]))
    bad = api("/api/jobs", {"task": "x", "engine": "nonexistent"})
    check("unknown engine is refused cleanly", "error" in bad, bad.get("error", ""))
finally:
    proc.kill()
    # Leave no trace of the test in the real diary or the real usage tally.
    import sqlite3
    import nova_memory as M
    with M.connect() as db:
        db.execute("DELETE FROM memory WHERE kind='day' AND at >= ?", (STARTED,))
    try:
        usage = json.loads((ROOT / "usage.json").read_text(encoding="utf-8"))
        if abs(usage["engines"].get("claude", {}).get("usd", 0) - 0.0123 * usage["engines"]["claude"]["requests"]) < 1e-6:
            (ROOT / "usage.json").unlink()
    except (OSError, ValueError, KeyError):
        pass

print("RESULT:", "PASS" if all(checks) else "NEEDS WORK")
