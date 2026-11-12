#!/usr/bin/env python3
"""
reinforce_test.py -- the cut-off and wrong-indicator bugs, pinned down offline.

No microphone, no speakers, no models: the ears' decisions are driven with a
fake clock and stand-in engines, and the voice is driven with a stand-in
synthesiser and a silent output.

    python setup/reinforce_test.py
"""
from __future__ import annotations

import queue
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import nova_ears as E   # noqa: E402
import nova_voice as V  # noqa: E402

fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not ok else ""))
    if not ok:
        fails.append(name)


# ------------------------------------------------------------------- ears

class FakeSpeaker:
    print = np.ones(4, dtype=np.float32)
    value = 0.9

    def score(self, samples):
        return self.value


class FakeWhisper:
    text = ""

    def transcribe(self, samples):
        return self.text, 0.01


class FakeBridge:
    def __init__(self):
        self.sent = []

    def wake(self, phrase, utterance=""):
        self.sent.append((phrase, utterance))

    def answer(self, allow): pass
    def stop_speech(self): pass


def ears() -> E.Ears:
    e = E.Ears.__new__(E.Ears)
    e.cfg = dict(E.DEFAULTS)
    e.debug = False
    e.bridge = FakeBridge()
    e.speaker = FakeSpeaker()
    e.whisper = FakeWhisper()
    e.armed_until = e.followup_until = e.engaged_until = 0.0
    e.nova_speaking = e.approval_pending = False
    e.held = None
    e.stats = {"segments": 0, "not_you": 0, "no_wake": 0, "requests": 0}
    return e


def clip(seconds: float) -> np.ndarray:
    return np.zeros(int(E.RATE * seconds), dtype=np.float32)


E.log = lambda *a, **k: None          # keep the real ears.log clean
QUIET = np.zeros(E.RATE, dtype=np.float32)

print("ears: listening windows")
e = ears()
T = 1000.0
e.followup_until = T + 8
e.whisper.text = "Can you open the project folder and show me what changed since yesterday morning?"
d = e.decide(clip(10.0), now=T + 2 + 10.0 + 0.6)       # began 2 s in, ended 4.6 s after the window
check("a long follow-up begun inside the window is kept", d["action"] == "request", d["why"])

e = ears()
e.armed_until = T + 8
e.whisper.text = "Tell me how much disk space is left on this computer right now."
d = e.decide(clip(9.0), now=T + 3 + 9.0 + 0.6)
check("a long request after a bare 'Nova' is kept", d["action"] == "request", d["why"])

e = ears()
e.followup_until = T + 8
e.whisper.text = "I was talking to somebody else entirely."
d = e.decide(clip(2.0), now=T + 20 + 2.0 + 0.6)        # began 12 s after the window shut
check("speech begun after the window is still ignored", d["action"] == "ignore", d["why"])

print("ears: a pause mid-sentence")
check("'open the' sounds unfinished", E.unfinished("Open the"))
check("'...and' sounds unfinished", E.unfinished("Start the server and."))
check("a whole sentence sounds finished", not E.unfinished("What time is it?"))

e = ears()
e.whisper.text = "Nova, open my project folder and"
e.take(e.decide(clip(2.0), now=T), now=T)
check("an unfinished request is held, not sent", e.held is not None and not e.bridge.sent)
check("the indicator stays on listening while held", e.window_left(T) >= 1.0)
e.release_due(False, 0, QUIET, now=T + 0.5)
check("still held half a second later", e.held is not None and not e.bridge.sent)
e.whisper.text = "show me what changed today."
e.take(e.decide(clip(2.0), now=T + 3.0), now=T + 3.0)
e.release_due(False, 0, QUIET, now=T + 3.1)
check("the rest is joined on, not sent yet", e.held is not None and not e.bridge.sent)
e.release_due(False, 0, QUIET, now=T + 3.4)
want = "open my project folder and show me what changed today."
check("one whole request goes out", e.bridge.sent == [("nova", want)], repr(e.bridge.sent))
check("counted as one request", e.stats["requests"] == 1, str(e.stats))

e = ears()
e.whisper.text = "Nova, what time is it?"
e.take(e.decide(clip(1.5), now=T), now=T)
e.release_due(False, 0, QUIET, now=T + 0.1)
check("a finished request waits only a moment", not e.bridge.sent)
e.release_due(False, 0, QUIET, now=T + 0.35)
check("...then goes", e.bridge.sent == [("nova", "what time is it")], repr(e.bridge.sent))

e = ears()
e.whisper.text = "Nova, what time is it?"
e.take(e.decide(clip(1.5), now=T), now=T)
e.speaker.value = 0.05                                  # the television starts up
e.release_due(True, 30, QUIET, now=T + 0.4)
check("someone else talking does not hold the request back", len(e.bridge.sent) == 1)

e = ears()
e.whisper.text = "Nova, what time is it?"
e.take(e.decide(clip(1.5), now=T), now=T)
e.release_due(True, 30, QUIET, now=T + 0.4)             # his own voice carries on
check("his own voice carrying on keeps it held", e.held is not None and not e.bridge.sent)

# ------------------------------------------------------------------ voice

print("voice: speaking means audible")


class SilentOut:
    def __init__(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def write(self, data): time.sleep(len(data) / 24000)
    def abort(self): pass


class FakeSd:
    OutputStream = SilentOut


sys.modules["sounddevice"] = FakeSd()


def voice() -> V.Voice:
    v = V.Voice({"voice": "x", "speed": 1.0, "lang": "en-gb", "volume": 0.0, "output_device": None})
    v.load = lambda: True
    v.synth = lambda text, voice=None: np.zeros(int(24000 * 0.5), dtype=np.float32)
    return v


v = voice()
st = v.open_stream()
time.sleep(0.3)
check("an open reply with nothing said is active", v.active)
check("...but not speaking", not v.speaking)
check("a voice cannot stop a reply that has not started", v.stop() is False)
st.feed("Here is the answer, Sir.")
time.sleep(0.25)
check("speaking once sound is coming out", v.speaking)
st.close()
r = st.wait(5)
check("the reply was spoken in full", r.get("spoken") == 1 and r.get("interrupted") is False, str(r))
check("neither active nor speaking afterwards", not v.active and not v.speaking)

v = voice()
st = v.open_stream()
st.feed("One. Two. Three. Four. Five. Six. Seven. Eight.")
st.close()
time.sleep(0.4)
began = time.time()
check("talk-over stops audible speech", v.stop() is True)
r = st.wait(3)
check("...within a quarter second", r.get("interrupted") is True and time.time() - began < 0.25, str(r))

print("voice: who may cut whom off")
v = voice()
reply = v.open_stream()
reply.feed("This is the reply to what he asked, and it runs for a little while.")
reply.close()
time.sleep(0.2)
note = v.open_stream(replace=False)                      # an announcement, or "may I ...?"
note.feed("Sir, a reminder.")
note.close()
r1, r2 = reply.wait(5), note.wait(5)
check("an announcement does not cut a reply off", r1.get("interrupted") is False, str(r1))
check("...and is still spoken afterwards", r2.get("spoken") == 1 and r2.get("interrupted") is False, str(r2))

v = voice()
old = v.open_stream()
old.feed("An old reply that is still going. And going. And going. And going.")
old.close()
time.sleep(0.2)
new = v.open_stream()                                    # the answer to a new request
new.feed("The new answer.")
new.close()
r1, r2 = old.wait(5), new.wait(5)
check("a new reply does replace the old one", r1.get("interrupted") is True, str(r1))
check("...and is spoken in full", r2.get("spoken") == 1 and r2.get("interrupted") is False, str(r2))

v = voice()
calls = [0]


def flaky(text, voice=None):
    calls[0] += 1
    if calls[0] == 2:
        raise RuntimeError("synth choked")
    return np.zeros(2400, dtype=np.float32)


v.synth = flaky
r = v.say("First sentence here. Second sentence breaks. Third sentence survives.")
check("one bad sentence does not silence the rest", r.get("spoken") == 2, str(r))

print()
print("RESULT:", "PASS" if not fails else f"FAIL ({len(fails)}): " + "; ".join(fails))
sys.exit(1 if fails else 0)
