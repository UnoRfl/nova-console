#!/usr/bin/env python3
"""
pronounce_test.py -- does she say it the way it is written?

Nobody can listen to a voice in a test, so this does the next best thing:
each engine speaks a set of lines, local Whisper transcribes the audio back,
and the words that did not survive the round trip are reported. It is how
"Sir" coming out as "say" was caught and measured rather than argued about.

    python setup/pronounce_test.py                 # every available engine
    python setup/pronounce_test.py edge            # just one
    python setup/pronounce_test.py edge "Any line you like, Sir."

Nothing is played aloud. Needs the internet for edge / elevenlabs.
"""
from __future__ import annotations

import io
import re
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path.home() / ".nova"
sys.path.insert(0, str(ROOT))
import nova_stt    # noqa: E402
import nova_voice  # noqa: E402

LINES = [
    "Right away, Sir.",
    "Yes, Sir?",
    "Good evening, Sir. The server is up with three players online.",
    "Very good, Sir. I shall remind you at half past nine.",
    "Sir, the main drive is nearly full again.",
    "The background job has finished after about four minutes, Sir.",
    "Welcome back, Sir. Two things happened while you were away.",
]


def wav_bytes(audio: np.ndarray, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def run(engine: str, lines: list[str]) -> tuple[int, int]:
    v = nova_voice.Voice()
    good = sirs = sir_total = 0
    for line in lines:
        try:
            audio = v.synth(line, engine=engine)
        except Exception as exc:                      # noqa: BLE001
            print(f"  {engine:10} FAILED  {line!r}: {exc}")
            continue
        heard = nova_stt.transcribe(wav_bytes(audio)).get("text", "")
        want, got = words(line), words(heard)
        lost = [w for w in want if w not in got]
        if "sir" in want:
            sir_total += 1
            sirs += "sir" in got
        good += not lost
        print(f"  {engine:10} {'ok  ' if not lost else 'LOST'} {line!r}\n"
              f"             heard {heard!r}" + (f"   missing {lost}" if lost else ""))
    print(f"  {engine}: {good}/{len(lines)} lines intact, 'Sir' heard as 'Sir' {sirs}/{sir_total}\n")
    return good, len(lines)


def main() -> int:
    args = sys.argv[1:]
    engines = [args[0]] if args else ["kokoro", "edge"] + (["elevenlabs"] if nova_voice.eleven_key() else [])
    lines = args[1:] or LINES
    bad = 0
    for e in engines:
        good, total = run(e, lines)
        bad += total - good
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
