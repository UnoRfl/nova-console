#!/usr/bin/env python3
"""
live_test.py -- one real turn through the real Claude CLI, silently, on a
private bridge. Needs the CLI to be signed in. Uses a little subscription usage.
"""
import json, os, subprocess, sys, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8797
env = dict(os.environ, NOVA_VOICE_VOLUME="0", NOVA_TEST="1", PYTHONIOENCODING="utf-8")
env.pop("NOVA_CLAUDE_STUB", None)
proc = subprocess.Popen(creationflags=0x08000000, args=[sys.executable, str(ROOT / "nova_bridge.py"), "--port", str(PORT)],
                        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
token = (ROOT / "token").read_text(encoding="utf-8").strip()

def api(path, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data,
                                 headers={"X-Nova-Token": token, "Content-Type": "application/json"},
                                 method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

try:
    for _ in range(80):
        try:
            api("/api/voice", timeout=2); break
        except Exception:
            time.sleep(0.25)
    for _ in range(120):
        h = api("/api/health")
        if h["status"] != "unknown" and api("/api/voice")["engine"] == "kokoro":
            break
        time.sleep(0.5)
    print("health:", h["status"], h["model"], h["power"], h["tts"], h.get("detail", "")[:120])
    for q in sys.argv[1:] or ["Good evening Nova. In one sentence, what are you?"]:
        t = time.time()
        r = api("/api/ask", {"prompt": q, "speak": True})
        took = time.time() - t
        if "error" in r:
            print("ERROR:", r["error"], r.get("detail", "")[:300]); continue
        s = api("/api/speak", {"text": r["text"], "streamed": True}, timeout=180)
        print(f"\nQ: {q}\nA: {r['text'][:400]}\n   reply {took:.1f}s, first audio {s.get('first_audio_s')}s after the turn began, "
              f"{s.get('spoken')} sentences spoken, turns {r.get('turns')}, denials {r.get('denials')}, cost ${r.get('cost'):.4f}")
    acts = api("/api/activity?since=0")["events"]
    print("\ntools used:", [e["text"] + " " + e["detail"] for e in acts if e["kind"] == "tool"])
finally:
    proc.kill()
