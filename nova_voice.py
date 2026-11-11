#!/usr/bin/env python3
"""
nova_voice.py -- Nova's voice. Replaces the browser's speechSynthesis.

Local neural speech (Kokoro), so it is free, works offline, and works with the
HUD closed.

    python nova_voice.py "Good evening, Sir."          # say something
    python nova_voice.py --voice bm_george "Hello."    # try a voice
    python nova_voice.py --voices                      # list voices
    python nova_voice.py --samples                     # write one WAV per British voice
    python nova_voice.py --earcon wake                 # play a tone

Three things make it feel quick rather than merely sound good:

  * Sentence streaming. The first sentence starts playing while the rest are
    still being synthesised, so the delay before sound is one short sentence,
    not the whole reply.
  * Interruptible. stop() cuts playback within a few milliseconds -- that is
    what lets Sir talk over her.
  * Cached acknowledgements. "Right away, Sir" and friends are synthesised
    once at start-up and play instantly.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path.home() / ".nova"
MODELS = ROOT / "models"
CONFIG_FILE = ROOT / "config.json"

DEFAULTS = {
    "voice": "bf_emma",       # British female; bm_george / bm_fable are the Jarvis-leaning ones
    "speed": 1.05,
    "lang": "en-gb",
    "volume": 0.9,
    "output_device": None,    # None = Windows default output
}

ACKS = {
    "ack":      ["Right away, Sir.", "On it, Sir.", "Very good, Sir.", "Of course, Sir."],
    "working":  ["Still on it, Sir.", "One moment more, Sir.", "Nearly there, Sir."],
    "yes":      ["Yes, Sir?", "Sir?"],
    "cancel":   ["Very well. I shall leave it.", "Cancelled, Sir."],
    "error":    ["I am afraid that did not work, Sir."],
}


def load_cfg() -> dict:
    cfg = dict(DEFAULTS)
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        cfg.update({k: v for k, v in (raw.get("voice") or {}).items() if k in DEFAULTS})
    except (OSError, ValueError, AttributeError):
        pass
    if os.environ.get("NOVA_VOICE_VOLUME"):            # tests run silently
        cfg["volume"] = float(os.environ["NOVA_VOICE_VOLUME"])
    return cfg


# ----------------------------------------------------------------- text prep

def speakable(text: str) -> str:
    """Strip what should never be read aloud. Mirrors speakable() in hud.html."""
    t = str(text or "")
    t = re.sub(r"::(MODEL|BUILD|CAD|HOLO)[\s\S]*?::END", " ", t)
    t = re.sub(r"::(MODEL|BUILD|CAD|HOLO)[\s\S]*$", " ", t)
    t = re.sub(r"\[\[\s*remember\s*:.*?\]\]", " ", t, flags=re.I | re.S)
    t = re.sub(r"```[\s\S]*?```", " code block omitted ", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"https?://\S+", "a link", t)
    t = re.sub(r"[A-Za-z]:\\[^\s]+", "a file path", t)
    t = re.sub(r"^[\s>#*\-+]+", "", t, flags=re.M)
    t = re.sub(r"[*_~#]", "", t)
    return re.sub(r"\s{2,}", " ", t).strip()


def sentences(text: str) -> list[str]:
    """Split into speakable chunks: sentences, with over-long ones cut at commas."""
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'(\d])", text.strip())
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) > 220:
            out += [c.strip() for c in re.split(r"(?<=[,;:])\s+", p) if c.strip()]
        else:
            out.append(p)
    # A very short opener ("Sir.") is merged forward so it does not stand alone.
    merged: list[str] = []
    for p in out:
        if merged and len(merged[-1]) < 12:
            merged[-1] = merged[-1] + " " + p
        else:
            merged.append(p)
    return merged


# ------------------------------------------------------------------ earcons

def _tone(freqs, dur=0.09, rate=24000, vol=0.22) -> np.ndarray:
    out = []
    for f in freqs:
        n = int(rate * dur)
        t = np.arange(n) / rate
        env = np.minimum(1, np.minimum(t / 0.008, (dur - t) / 0.03)).clip(0, 1)
        out.append((np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t)) * env * vol)
    return np.concatenate(out).astype(np.float32)


EARCONS = {
    "wake":    lambda: _tone([660, 990]),                 # rising: I am listening
    "heard":   lambda: _tone([990], dur=0.06),            # got it, thinking
    "done":    lambda: _tone([784, 1047, 1319], dur=0.07),
    "error":   lambda: _tone([330, 247], dur=0.14),       # falling
    "ask":     lambda: _tone([880, 880, 1175], dur=0.08), # needs your yes
    "sleep":   lambda: _tone([990, 660]),                 # falling: stopped listening
}


# ------------------------------------------------------------------- engine

class Voice:
    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = cfg or load_cfg()
        self.k = None
        self.rate = 24000
        self.error: str | None = None
        self.lock = threading.Lock()          # one utterance at a time
        self.load_lock = threading.Lock()
        self.stop_flag = threading.Event()
        self.speaking = False
        self.level = 0.0                      # live loudness, for the HUD's visuals
        self.cache: dict[str, np.ndarray] = {}
        self._ack_i = 0

    # ---------------------------------------------------------- loading
    def load(self) -> bool:
        with self.load_lock:
            if self.k is not None:
                return True
            try:
                from kokoro_onnx import Kokoro
                self.k = Kokoro(str(MODELS / "kokoro-v1.0.onnx"), str(MODELS / "voices-v1.0.bin"))
                self.error = None
                return True
            except Exception as exc:                  # noqa: BLE001
                self.error = f"{type(exc).__name__}: {exc}"
                return False

    def warm(self) -> None:
        """Load the model and pre-synthesise the stock lines, off the main thread."""
        def work():
            if not self.load():
                return
            for group in ACKS.values():
                for line in group:
                    try:
                        self.cache[line] = self.synth(line)
                    except Exception:                 # noqa: BLE001
                        pass
        threading.Thread(target=work, daemon=True).start()

    def status(self) -> dict:
        return {"engine": "kokoro" if self.k is not None else ("loading" if not self.error else "error"),
                "voice": self.cfg["voice"], "speaking": self.speaking,
                "level": round(self.level, 3), "error": self.error}

    def voices(self) -> list[str]:
        return sorted(self.k.get_voices()) if self.load() else []

    # -------------------------------------------------------- synthesis
    def synth(self, text: str, voice: str | None = None) -> np.ndarray:
        if text in self.cache and voice in (None, self.cfg["voice"]):
            return self.cache[text]
        samples, rate = self.k.create(text, voice=voice or self.cfg["voice"],
                                      speed=float(self.cfg["speed"]), lang=self.cfg["lang"])
        self.rate = rate
        return np.asarray(samples, dtype=np.float32)

    # --------------------------------------------------------- playback
    def _play(self, q: "queue.Queue[np.ndarray | None]") -> bool:
        """Play chunks from q until None. Returns True if interrupted."""
        import sounddevice as sd
        block = 1024
        vol = float(self.cfg["volume"])
        with sd.OutputStream(samplerate=self.rate, channels=1, dtype="float32",
                             device=self.cfg.get("output_device"), blocksize=block) as out:
            while True:
                chunk = q.get()
                if chunk is None:
                    return False
                for i in range(0, len(chunk), block):
                    if self.stop_flag.is_set():
                        return True
                    piece = chunk[i:i + block] * vol
                    self.level = float(np.sqrt(np.mean(piece ** 2))) if len(piece) else 0.0
                    out.write(piece.reshape(-1, 1))
                # A breath between sentences.
                out.write(np.zeros((int(self.rate * 0.12), 1), dtype=np.float32))

    def open_stream(self, voice: str | None = None) -> "SpeechStream":
        """Start speaking text that has not finished arriving yet.

        feed() it sentences as the model produces them, close() when the reply
        is complete, wait() for the audio to finish. This is what lets her
        start talking before she has finished thinking.
        """
        return SpeechStream(self, voice)

    def say(self, text: str, voice: str | None = None) -> dict:
        """Speak text. Blocks until finished or interrupted."""
        if not speakable(text):
            return {"ok": True, "spoken": 0, "interrupted": False}
        st = self.open_stream(voice)
        st.feed(text)
        st.close()
        return st.wait()

    def stop(self) -> bool:
        """Cut speech off now. Returns True if something was actually playing."""
        was = self.speaking
        if was:
            self.stop_flag.set()
        return was

    def ack(self, kind: str = "ack") -> dict:
        lines = ACKS.get(kind) or ACKS["ack"]
        self._ack_i += 1
        return self.say(lines[self._ack_i % len(lines)])

    def earcon(self, name: str) -> bool:
        make = EARCONS.get(name)
        if not make:
            return False
        try:
            import sounddevice as sd
            sd.play(make() * float(self.cfg["volume"]), 24000,
                    device=self.cfg.get("output_device"))
            return True
        except Exception:                             # noqa: BLE001
            return False


class SpeechStream:
    """One spoken reply, fed a piece at a time. See Voice.open_stream()."""

    def __init__(self, voice: Voice, voice_name: str | None = None) -> None:
        self.v = voice
        self.voice_name = voice_name
        self.texts: "queue.Queue[str | None]" = queue.Queue()
        self.done = threading.Event()
        self.result: dict = {}
        self.fed = 0
        threading.Thread(target=self._run, daemon=True).start()

    def feed(self, text: str) -> None:
        clean = speakable(text)
        if clean:
            self.fed += 1
            self.texts.put(clean)

    def close(self) -> None:
        self.texts.put(None)

    def wait(self, timeout: float | None = None) -> dict:
        self.done.wait(timeout)
        return self.result or {"ok": True, "spoken": 0, "interrupted": False, "pending": True}

    def _run(self) -> None:
        v = self.v
        if not v.load():
            self.result = {"error": "voice_unavailable", "detail": v.error or ""}
            self.done.set()
            return

        v.stop()                                      # a new reply replaces the old one
        with v.lock:
            v.stop_flag.clear()
            v.speaking = True
            began = time.time()
            first_audio = [None]
            spoken = [0]
            audio: queue.Queue = queue.Queue(maxsize=3)

            def produce() -> None:
                try:
                    while not v.stop_flag.is_set():
                        try:
                            text = self.texts.get(timeout=0.1)
                        except queue.Empty:
                            continue
                        if text is None:
                            break
                        for part in sentences(text):
                            if v.stop_flag.is_set():
                                break
                            chunk = v.synth(part, self.voice_name)
                            if first_audio[0] is None:
                                first_audio[0] = time.time() - began
                            spoken[0] += 1
                            while not v.stop_flag.is_set():
                                try:
                                    audio.put(chunk, timeout=0.1)
                                    break
                                except queue.Full:
                                    continue
                finally:
                    while True:                       # the sentinel must always land
                        try:
                            audio.put_nowait(None)
                            break
                        except queue.Full:
                            try:
                                audio.get_nowait()
                            except queue.Empty:
                                pass

            t = threading.Thread(target=produce, daemon=True)
            t.start()
            try:
                interrupted = v._play(audio)
                self.result = {"ok": True, "spoken": spoken[0], "interrupted": interrupted,
                               "first_audio_s": round(first_audio[0] or 0, 2),
                               "total_s": round(time.time() - began, 2)}
            except Exception as exc:                  # noqa: BLE001
                self.result = {"error": "playback_failed", "detail": f"{type(exc).__name__}: {exc}"}
            v.stop_flag.set()                         # release the producer either way
            t.join(timeout=5)
            v.speaking = False
            v.level = 0.0
        self.done.set()


_shared: Voice | None = None


def shared() -> Voice:
    global _shared
    if _shared is None:
        _shared = Voice()
    return _shared


# --------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="Nova's voice")
    ap.add_argument("text", nargs="*")
    ap.add_argument("--voice")
    ap.add_argument("--voices", action="store_true")
    ap.add_argument("--samples", action="store_true", help="write a WAV per British voice to ~/.nova/voice-samples")
    ap.add_argument("--earcon")
    args = ap.parse_args()
    v = Voice()

    if args.voices:
        print("\n".join(v.voices()))
        return 0

    if args.earcon:
        ok = v.earcon(args.earcon)
        time.sleep(0.6)
        return 0 if ok else 1

    if args.samples:
        out = ROOT / "voice-samples"
        out.mkdir(exist_ok=True)
        line = ("Good evening, Sir. The Minecraft server is up with three players online, "
                "and your render finished about ten minutes ago. Shall I open it?")
        for name in [n for n in v.voices() if n.startswith("b")]:
            audio = v.synth(line, name)
            with wave.open(str(out / f"{name}.wav"), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(v.rate)
                w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
            print(f"wrote {name}.wav ({len(audio)/v.rate:.1f}s)")
        return 0

    text = " ".join(args.text) or "Good evening, Sir. All systems are in order."
    print(json.dumps(v.say(text, args.voice)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
