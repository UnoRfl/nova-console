#!/usr/bin/env python3
"""
phoneline_test.py -- the main PC's end of Nova's real Telegram calls, against
a fake nova_caller (same wire format) on localhost. No Telegram, no model.
"""
import json
import socket
import struct
import sys
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import nova_phoneline as P  # noqa: E402

checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("  ok   " if ok else "  FAIL ") + name + (f"   {detail}" if detail else ""))


def speechy(seconds, rate=48000):
    t = np.arange(int(seconds * rate)) / rate
    env = 0.5 + 0.5 * np.sin(2 * np.pi * 3 * t)                 # syllable-ish wobble
    return (0.25 * env * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2").tobytes()


def silence(seconds, rate=48000):
    return (np.random.default_rng(1).normal(0, 0.002, int(seconds * rate)) * 32767).astype("<i2").tobytes()


# ---- the detector on its own
d = P.Detector()
got = [d.feed(silence(1.0))]
for i in range(0, 48000 * 2 * 2, 1920):
    pass
utt = d.feed(speechy(1.2)) or d.feed(silence(1.2))
check("detector: 1.2 s of speech then silence = one utterance (speech + the 0.9 s that ends it)",
      utt is not None and 1.9 < len(utt) / 96000 < 2.4,
      f"{len(utt) / 96000 if utt else 0:.2f}s")
check("detector: background noise alone is not speech", got == [None])
d2 = P.Detector()
check("detector: a 60 ms click is ignored", d2.feed(silence(.5) + speechy(.06) + silence(1.2)) is None)

# ---- a fake headless line
TOKEN = "test-token"
srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
PORT = srv.getsockname()[1]
seen = {"cmds": [], "audio": 0, "auth": None}
conn_holder = {}


def send(c, kind, payload):
    c.sendall(kind + struct.pack(">I", len(payload)) + payload)


def recv(c, n):
    b = bytearray()
    while len(b) < n:
        x = c.recv(n - len(b))
        if not x:
            raise ConnectionError
        b += x
    return bytes(b)


def fake_line():
    c, _ = srv.accept()
    conn_holder["c"] = c
    h = recv(c, 5); seen["auth"] = json.loads(recv(c, struct.unpack(">I", h[1:])[0]))
    send(c, b"J", json.dumps({"event": "ready"}).encode())
    try:
        while True:
            h = recv(c, 5); body = recv(c, struct.unpack(">I", h[1:])[0])
            if h[:1] == b"A":
                seen["audio"] += len(body)
            else:
                cmd = json.loads(body).get("cmd"); seen["cmds"].append(cmd)
                if cmd == "call" and seen.get("decline"):
                    send(c, b"J", json.dumps({"event": "ended", "reason": "declined", "answered": False}).encode())
                elif cmd == "call":
                    send(c, b"J", json.dumps({"event": "ringing"}).encode())
                    time.sleep(0.2)
                    send(c, b"J", json.dumps({"event": "answered", "incoming": False}).encode())
                elif cmd == "hangup":
                    send(c, b"J", json.dumps({"event": "ended", "reason": "nova hung up", "answered": True}).encode())
    except (ConnectionError, OSError):
        pass


threading.Thread(target=fake_line, daemon=True).start()
said, asked, missed, turns, logs = [], [], [], [], []
heard_texts = iter(["what is the weather like", "okay bye"])
line = P.PhoneLine({"host": "127.0.0.1", "port": PORT, "token": TOKEN},
                   ask=lambda p: (asked.append(p), {"text": "It is sunny, Sir."})[1],
                   transcribe=lambda wav: {"text": next(heard_texts, "")},
                   synth=lambda t: (said.append(t), np.zeros(int(24000 * 0.4), dtype=np.float32))[1],
                   log=logs.append, on_missed=missed.append, on_turn=lambda h, r: turns.append((h, r)))
line.det = P.Detector()      # this test talks in tones, which the neural detector rightly ignores (see callvad_test.py)
line.start()
for _ in range(50):
    if line.ready():
        break
    time.sleep(0.1)
check("connects and authenticates with the token", line.ready() and seen["auth"] == {"auth": TOKEN})
rang = line.call("Sir, the backup finished.")
for _ in range(30):
    if "call" in seen["cmds"]:
        break
    time.sleep(0.05)
check("call() rings", rang and "call" in seen["cmds"])
for _ in range(60):
    if said:
        break
    time.sleep(0.1)
time.sleep(0.9)
check("when he answers she says the message first", said[:1] == ["Sir, the backup finished."], str(said))
check("...as real audio into the call (24 kHz voice upsampled to 48 kHz)", seen["audio"] == int(24000 * 0.4) * 2 * 2,
      str(seen["audio"]))
check("then she is listening", line.state == "listening", line.state)


def talk(pcm):
    c = conn_holder["c"]
    for i in range(0, len(pcm), 1920 * 5):
        send(c, b"A", pcm[i:i + 1920 * 5]); time.sleep(0.05)


talk(silence(0.4) + speechy(1.3) + silence(1.3))
for _ in range(60):
    if turns:
        break
    time.sleep(0.1)
check("his sentence is heard, sent to her, and she answers", turns == [("what is the weather like", "It is sunny, Sir.")],
      str(turns))
check("the request tells her it is a live call", asked and asked[0].startswith("[Live phone call"))
time.sleep(1.0)
check("her answer is spoken into the call", said[-1] == "It is sunny, Sir.", str(said))
talk(speechy(1.0) + silence(1.3))
for _ in range(60):
    if "hangup" in seen["cmds"]:
        break
    time.sleep(0.1)
check("'okay bye' -> she says goodbye and hangs up", "Goodbye, Sir." in said and "hangup" in seen["cmds"])
time.sleep(0.3)
check("after the call the line is idle again", line.state == "idle", line.state)
check("nothing was treated as missed", missed == [])
seen["decline"] = True
line.call("Sir, the server is down.")
time.sleep(0.6)
check("a call he does not pick up falls back to the bot (message + voice note)",
      missed == ["Sir, the server is down."], str(missed))
check("she does not ring while already busy", (setattr(line, "state", "speaking"), line.call("x"))[1] is False)
line.state = "idle"
conn_holder["c"].close()
time.sleep(0.5)
check("a dropped line is noticed", not line.ready())

print(f"\n{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
