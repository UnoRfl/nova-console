"""Speaker gate inside vs outside a listening window.

After a verified "Nova" (or during her follow-up) his next sentence often
scores 0.36-0.45; it used to be thrown away as "not your voice". Inside a
window the bar is speaker_threshold_window; outside, the strict bars stay.
Offline: speaker score and transcription are stubbed.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import nova_ears as E  # noqa: E402

fails = 0


def check(name, cond):
    global fails
    print(("  ok    " if cond else "  FAIL  ") + name)
    fails += 0 if cond else 1


class FakeSpeaker:
    print = object()

    def __init__(self):
        self.value = 0.0

    def score(self, _):
        return self.value


class FakeWhisper:
    name, device = "stub", "cpu"

    def __init__(self):
        self.text = ""

    def transcribe(self, _):
        return self.text, 0.0


ears = E.Ears.__new__(E.Ears)
cfg = E.load_cfg()
ears.cfg = cfg
ears.debug = False
ears.speaker, ears.whisper = FakeSpeaker(), FakeWhisper()
ears.armed_until = ears.followup_until = ears.engaged_until = 0.0
ears.nova_speaking = ears.approval_pending = False
ears.held = None
ears.stats = {"segments": 0, "not_you": 0, "no_wake": 0, "requests": 0}

clip = np.zeros(int(E.RATE * 1.5), dtype=np.float32)   # 1.5 s: the long-clip bar
NOW = 1_000_000.0
print(f"thresholds: long {cfg['speaker_threshold']}, short {cfg['speaker_threshold_short']}, "
      f"window {cfg['speaker_threshold_window']}")


def reset():
    ears.armed_until = ears.followup_until = 0.0
    ears.held = None


# Outside any window: 0.40 is still rejected, so strangers can't wake her.
reset(); ears.speaker.value = 0.40; ears.whisper.text = "nova open chrome"
check("outside a window, 0.40 is still not you",
      ears.decide(clip, now=NOW)["why"] == "not your voice")

# After a bare "Nova": the request at 0.40 / 0.44 (the scores from the log) gets through.
for s in (0.378, 0.443):
    reset(); ears.armed_until = NOW + 5; ears.speaker.value = s
    ears.whisper.text = "what's the weather like"
    d = ears.decide(clip, now=NOW)
    check(f"after 'Nova', a {s} request is accepted", d["action"] == "request")

# During a follow-up window.
reset(); ears.followup_until = NOW + 5; ears.speaker.value = 0.36
ears.whisper.text = "and tomorrow?"
check("in a follow-up, 0.36 is accepted", ears.decide(clip, now=NOW)["action"] == "request")

# A clearly different voice inside the window is still refused.
reset(); ears.armed_until = NOW + 5; ears.speaker.value = 0.20
ears.whisper.text = "thanks for watching"
check("in a window, a 0.20 voice (TV) is still refused",
      ears.decide(clip, now=NOW)["why"] == "not your voice")

# Window already closed when he started speaking: strict bar again.
reset(); ears.armed_until = NOW - 10; ears.speaker.value = 0.40
ears.whisper.text = "hello"
check("after the window closed, 0.40 is refused again",
      ears.decide(clip, now=NOW)["why"] == "not your voice")

print("\nRESULT:", "PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
