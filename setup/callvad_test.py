#!/usr/bin/env python3
"""
callvad_test.py -- on a noisy phone line, two questions with a one-second pause
between them must come out as TWO utterances, fed 10 ms at a time as a real
call delivers them. (Loudness alone heard one 20-second blob.)

Offline: uses a voice sample from ~/.nova/voice-samples with line noise added.

    python setup/callvad_test.py
"""
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import nova_phoneline as p                                          # noqa: E402

sample = sorted((ROOT / "voice-samples").glob("*.wav"))
if not sample:
    print("no voice sample to test with; skipped")
    sys.exit(0)
with wave.open(str(sample[0]), "rb") as w:
    rate = w.getframerate()
    a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
    if w.getnchannels() == 2:
        a = a.reshape(-1, 2).mean(axis=1)
n = int(len(a) * p.RATE / rate)
speech = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32)
speech = speech[: p.RATE * 3] * (0.5 / (np.abs(speech).max() or 1))            # three seconds of talking

rng = np.random.default_rng(7)
bad = 0
for noise in (0.0, 0.02, 0.04):                                               # 0.04 is far above the old 0.012 gate
    gap = lambda s: rng.normal(0, noise, int(p.RATE * s)).astype(np.float32)  # noqa: E731
    line = np.concatenate([gap(1), speech + gap(3), gap(1.0), speech + gap(3), gap(1.5)])
    pcm = (np.clip(line, -1, 1) * 32767).astype("<i2").tobytes()
    for name, det in (("neural", p.SpeechDetector()), ("loudness (old)", p.Detector())):
        found = []
        for i in range(0, len(pcm), 960):                                      # 10 ms pieces
            out = det.feed(pcm[i:i + 960])
            if out:
                found.append(len(out) / (det.rate * 2))
        ok = len(found) == 2
        if name == "neural":
            bad += not ok
        print(f"  {'ok  ' if ok else ('FAIL' if name == 'neural' else 'note')} noise {noise:.2f}  {name:15} "
              f"-> {len(found)} utterance(s) {[round(x, 1) for x in found]}")
print("RESULT:", "PASS" if not bad else "FAIL")
sys.exit(1 if bad else 0)
