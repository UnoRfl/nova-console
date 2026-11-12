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
    # Which synthesiser speaks. Each falls back to the next when it cannot:
    #   "elevenlabs"  best voice; needs your key in ~/.nova/elevenlabs.key
    #   "edge"        Microsoft's neural voices: free, no key, needs internet
    #   "kokoro"      fully offline, on this PC
    "engine": "edge",
    "edge_voice": "en-GB-SoniaNeural",          # Libby, Maisie; Ryan and Thomas are male
    "eleven_voice": "Xb7hH8MSUJpSbSDYk0k2",     # a voice id from your ElevenLabs voice library
    "eleven_model": "eleven_flash_v2_5",
    "voice": "bf_emma",       # the Kokoro voice (offline fallback)
    "speed": 1.05,
    "lang": "en-gb",
    "volume": 0.9,
    "output_device": None,    # None = Windows default output
    "say_as": {},             # your own respellings: {"Unoryx": "you no ricks"}
}

ELEVEN_KEY_FILE = ROOT / "elevenlabs.key"

# Words a synthesiser reads wrongly, respelt the way they should sound. Whole
# words only, case-insensitive. config voice.say_as adds to or overrides these.
SAY_AS = {
    "PWA": "P W A", "RLS": "R L S", "CSP": "C S P",
    "playit": "play it", "datapack": "data pack", "WMI": "W M I", "SSH": "S S H",
    "RGB": "R G B", "GPU": "G P U", "CPU": "C P U", "SSD": "S S D", "API": "A P I",
    "PR": "pull request", "PRs": "pull requests", "MC": "Minecraft", "vs": "versus",
    "etc": "et cetera", "approx": "approximately",
}
UNITS = [(r"(\d)\s?GB\b", r"\1 gigabytes"), (r"(\d)\s?MB\b", r"\1 megabytes"),
         (r"(\d)\s?KB\b", r"\1 kilobytes"), (r"(\d)\s?TB\b", r"\1 terabytes"),
         (r"(\d)\s?ms\b", r"\1 milliseconds"), (r"(\d)\s?fps\b", r"\1 frames per second"),
         (r"(\d)\s?°\s?C\b", r"\1 degrees"), (r"(\d)\s?%", r"\1 percent"),
         (r"(\d)\s?GHz\b", r"\1 gigahertz"), (r"(\d)\s?Mbps\b", r"\1 megabits per second")]

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
    t = re.sub(r"\[\[\s*(?:remember|loop|done)\s*:.*?\]\]", " ", t, flags=re.I | re.S)
    t = re.sub(r"```[\s\S]*?```", " code block omitted ", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"https?://\S+", "a link", t)
    t = re.sub(r"[A-Za-z]:\\[^\s]+", "a file path", t)
    t = re.sub(r"^[\s>#*\-+]+", "", t, flags=re.M)
    t = re.sub(r"[*_~#]", "", t)
    t = re.sub(r"\s*(?:--+|[–—])\s*", ", ", t)       # a dash is a pause, not a word
    t = t.replace("&", " and ").replace("e.g.", "for example").replace("i.e.", "that is")
    for pat, rep in UNITS:
        t = re.sub(pat, rep, t)
    return re.sub(r"\s{2,}", " ", t).strip()


_say_as: tuple[float, re.Pattern | None, dict] = (0.0, None, {})


def pronounce(text: str) -> str:
    """Respell the words a synthesiser gets wrong. Applied to every engine."""
    global _say_as
    if time.time() - _say_as[0] > 30:                  # config edits apply without a restart
        words = dict(SAY_AS)
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
            words.update({str(k): str(v) for k, v in ((raw.get("voice") or {}).get("say_as") or {}).items()})
        except (OSError, ValueError, AttributeError):
            pass
        table = {k.lower(): v for k, v in words.items() if k and v}
        pat = (re.compile(r"(?<![A-Za-z0-9])(" + "|".join(sorted(map(re.escape, table), key=len, reverse=True))
                          + r")(?![A-Za-z0-9])", re.I) if table else None)
        _say_as = (time.time(), pat, table)
    _, pat, table = _say_as
    return pat.sub(lambda m: table.get(m.group(1).lower(), m.group(1)), text) if pat else text


def decode_audio(data: bytes, rate: int = 24000) -> np.ndarray:
    """Compressed audio (mp3) -> mono float32 at `rate`, via PyAV. No ffmpeg needed."""
    import io
    import av
    out = []
    with av.open(io.BytesIO(data)) as box:
        resampler = av.AudioResampler(format="flt", layout="mono", rate=rate)
        for frame in box.decode(audio=0):
            for f in resampler.resample(frame):
                out.append(f.to_ndarray().reshape(-1))
        for f in resampler.resample(None):
            out.append(f.to_ndarray().reshape(-1))
    return np.concatenate(out).astype(np.float32) if out else np.zeros(0, dtype=np.float32)


def _trim(audio: np.ndarray, rate: int = 24000) -> np.ndarray:
    """Cloud voices pad each clip with silence; stacked per sentence it drags."""
    loud = np.flatnonzero(np.abs(audio) > 0.004)
    if not len(loud):
        return audio
    return audio[max(0, loud[0] - rate // 50): loud[-1] + rate // 12]


def eleven_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        try:
            key = ELEVEN_KEY_FILE.read_text(encoding="utf-8-sig").strip()
        except OSError:
            key = ""
    return key


def synth_eleven(text: str, cfg: dict, timeout: float = 12) -> np.ndarray:
    import urllib.request
    body = json.dumps({"text": text, "model_id": cfg["eleven_model"],
                       "voice_settings": {"stability": 0.5, "similarity_boost": 0.8,
                                          "speed": max(0.7, min(1.2, float(cfg["speed"])))}}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{cfg['eleven_voice']}?output_format=mp3_44100_128",
        data=body, method="POST",
        headers={"xi-api-key": eleven_key(), "Content-Type": "application/json", "Accept": "audio/mpeg"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return _trim(decode_audio(res.read()))


def synth_edge(text: str, cfg: dict, voice: str | None = None, timeout: float = 10) -> np.ndarray:
    import asyncio
    import edge_tts
    pct = round((float(cfg["speed"]) - 1.0) * 100)

    async def run() -> bytes:
        com = edge_tts.Communicate(text, voice or cfg["edge_voice"], rate=f"{pct:+d}%")
        data = b""
        async for chunk in com.stream():
            if chunk["type"] == "audio":
                data += chunk["data"]
        return data

    data = asyncio.run(asyncio.wait_for(run(), timeout))
    if not data:
        raise RuntimeError("edge returned no audio")
    return _trim(decode_audio(data))


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
        # Two different things, and confusing them was a real bug:
        #   active    a reply is open. She may be silent -- still thinking,
        #             or waiting for the next sentence to be synthesised.
        #   speaking  sound is coming out of the speakers right now.
        # Talk-over and the on-screen indicator must use `speaking`. When it
        # was true for the whole reply, Sir finishing his own sentence while
        # she was still thinking counted as "talking over her" and silently
        # killed the answer before she had said a word.
        self.active = False
        self.speaking = False
        self.level = 0.0                      # live loudness, for the HUD's visuals
        self.cache: dict[str, np.ndarray] = {}
        self._ack_i = 0
        self.down: dict[str, float] = {}      # cloud engine -> do not retry before this time
        self.last_engine = ""                 # which engine actually produced the last sentence
        self.last_fault = ""

    # ---------------------------------------------------------- engines
    def chain(self) -> list[str]:
        """The engines to try for a sentence, best first. Kokoro is always last."""
        want = str(self.cfg.get("engine") or "kokoro").lower()
        order = {"elevenlabs": ["elevenlabs", "edge", "kokoro"],
                 "edge": ["edge", "kokoro"]}.get(want, ["kokoro"])
        now = time.time()
        return [e for e in order
                if e == "kokoro" or (now >= self.down.get(e, 0) and (e != "elevenlabs" or eleven_key()))]

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
                # Without the offline model she can still speak through a
                # cloud voice; only report the voice as broken if neither works.
                if str(self.cfg.get("engine")).lower() in ("edge", "elevenlabs"):
                    self.last_fault = f"kokoro: {type(exc).__name__}: {exc}"
                    return True
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
        chain = self.chain()
        engine = self.last_engine or chain[0]
        if engine == "kokoro" and self.k is None:
            engine = "loading" if not self.error else "error"
        return {"engine": engine, "wanted": self.cfg.get("engine"), "chain": chain,
                "voice": {"edge": self.cfg["edge_voice"], "elevenlabs": self.cfg["eleven_voice"]}.get(
                    engine, self.cfg["voice"]),
                "speaking": self.speaking, "active": self.active,
                "level": round(self.level, 3), "error": self.error, "fault": self.last_fault}

    def voices(self) -> list[str]:
        return sorted(self.k.get_voices()) if self.load() and self.k is not None else []

    # -------------------------------------------------------- synthesis
    def synth(self, text: str, voice: str | None = None, engine: str | None = None) -> np.ndarray:
        """One sentence -> audio at 24 kHz, from the best engine that answers.

        `voice` names a voice for whichever engine it belongs to (an Edge
        voice has "Neural" in it); `engine` forces one engine, for samples.
        """
        if text in self.cache and voice is None and engine is None:
            return self.cache[text]
        said = pronounce(text)
        for eng in ([engine] if engine else self.chain()):
            try:
                if eng == "elevenlabs":
                    audio = synth_eleven(said, self.cfg)
                elif eng == "edge":
                    audio = synth_edge(said, self.cfg, voice if voice and "Neural" in voice else None)
                else:
                    if self.k is None and not (self.load() and self.k is not None):
                        raise RuntimeError(self.error or self.last_fault or "kokoro is not loaded")
                    name = voice if voice and "Neural" not in voice else self.cfg["voice"]
                    samples, rate = self.k.create(said, voice=name, speed=float(self.cfg["speed"]),
                                                  lang=self.cfg["lang"])
                    self.rate = rate
                    audio = np.asarray(samples, dtype=np.float32)
                if not len(audio):
                    raise RuntimeError("no audio")
                self.last_engine = eng
                return audio
            except Exception as exc:                  # noqa: BLE001
                self.last_fault = f"{eng}: {type(exc).__name__}: {str(exc)[:140]}"
                if eng != "kokoro" and not engine:
                    # Offline, out of credit, or a bad key: stop asking for a
                    # while so every sentence does not pay the timeout again.
                    code = getattr(exc, "code", 0)
                    self.down[eng] = time.time() + (900 if code in (401, 402, 403, 429) else 60)
        raise RuntimeError(self.last_fault or "no voice engine could speak")

    # --------------------------------------------------------- playback
    def _play(self, q: "queue.Queue[np.ndarray | None]") -> bool:
        """Play chunks from q until None. Returns True if interrupted."""
        import sounddevice as sd
        block = 1024
        vol = float(self.cfg["volume"])
        with sd.OutputStream(samplerate=self.rate, channels=1, dtype="float32",
                             device=self.cfg.get("output_device"), blocksize=block) as out:
            waited = 0.0
            while True:
                if self.stop_flag.is_set():
                    out.abort()                       # drop what is buffered: stop means now
                    return True
                try:
                    chunk = q.get(timeout=0.1)
                except queue.Empty:
                    # Nothing to say yet. A short gap between sentences still
                    # counts as speaking (so "stop" works mid-reply and the
                    # indicator does not flicker); a long one does not.
                    waited += 0.1
                    if waited >= 0.6 and self.speaking:
                        self.speaking = False
                        self.level = 0.0
                    continue
                waited = 0.0
                if chunk is None:
                    return False
                self.speaking = True
                for i in range(0, len(chunk), block):
                    if self.stop_flag.is_set():
                        out.abort()
                        return True
                    piece = chunk[i:i + block] * vol
                    self.level = float(np.sqrt(np.mean(piece ** 2))) if len(piece) else 0.0
                    out.write(piece.reshape(-1, 1))
                # A breath between sentences.
                out.write(np.zeros((int(self.rate * 0.12), 1), dtype=np.float32))
                self.level = 0.0

    def open_stream(self, voice: str | None = None, replace: bool = True) -> "SpeechStream":
        """Start speaking text that has not finished arriving yet.

        feed() it sentences as the model produces them, close() when the reply
        is complete, wait() for the audio to finish. This is what lets her
        start talking before she has finished thinking.

        replace=True is for the answer to a new request: it cuts off whatever
        she was saying before. replace=False is for things she says on her own
        (an announcement, a reminder, "may I ...?"): those wait their turn and
        never cut a reply off mid-sentence.
        """
        return SpeechStream(self, voice, replace)

    def say(self, text: str, voice: str | None = None, replace: bool = True) -> dict:
        """Speak text. Blocks until finished or interrupted."""
        if not speakable(text):
            return {"ok": True, "spoken": 0, "interrupted": False}
        st = self.open_stream(voice, replace)
        st.feed(text)
        st.close()
        return st.wait()

    def stop(self, force: bool = False) -> bool:
        """Cut speech off now. Returns True if something was actually stopped.

        By default only audible speech is stopped, so a voice heard while she
        is still thinking cannot cancel a reply she has not started. force=True
        (the panel's stop button) also cancels a reply that is open but silent.
        """
        was = self.speaking or (force and self.active)
        if was:
            self.stop_flag.set()
        return was

    def cancel(self) -> None:
        """Make way for a new reply: end the open one, audible or not."""
        if self.active:
            self.stop_flag.set()

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

    def __init__(self, voice: Voice, voice_name: str | None = None, replace: bool = True) -> None:
        self.v = voice
        self.voice_name = voice_name
        self.replace = replace
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

        if self.replace:
            v.cancel()                                # a new reply replaces the old one
        with v.lock:
            v.stop_flag.clear()
            v.active = True                           # `speaking` waits for actual sound
            began = time.time()
            first_audio = [None]
            spoken = [0]
            audio: queue.Queue = queue.Queue(maxsize=3)
            # This reply's own "stop". A cloud voice cannot be interrupted in
            # the middle of fetching a sentence, so the producer is not waited
            # for when she is cut off -- and the shared stop_flag is cleared by
            # the next reply, which would set a leftover producer going again.
            dead = threading.Event()

            def stopped() -> bool:
                return v.stop_flag.is_set() or dead.is_set()

            def produce() -> None:
                try:
                    while not stopped():
                        try:
                            text = self.texts.get(timeout=0.1)
                        except queue.Empty:
                            continue
                        if text is None:
                            break
                        for part in sentences(text):
                            if stopped():
                                break
                            try:
                                chunk = v.synth(part, self.voice_name)
                            except Exception:         # noqa: BLE001
                                # One sentence the synthesiser chokes on must
                                # not silence the rest of the reply.
                                continue
                            if first_audio[0] is None:
                                first_audio[0] = time.time() - began
                            spoken[0] += 1
                            while not stopped():
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
            dead.set()                                # release the producer either way
            v.stop_flag.set()
            t.join(timeout=0.05)                      # it ends on its own; do not wait on the network
            v.speaking = False
            v.active = False
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
        todo = [(n, "edge") for n in ("en-GB-SoniaNeural", "en-GB-LibbyNeural", "en-GB-MaisieNeural",
                                      "en-GB-RyanNeural", "en-GB-ThomasNeural")]
        todo += [(n, "kokoro") for n in v.voices() if n.startswith("b")]
        if eleven_key():
            todo.append(("elevenlabs", "elevenlabs"))
        for name, eng in todo:
            try:
                audio = v.synth(line, name, engine=eng)
            except Exception as exc:                  # noqa: BLE001
                print(f"skipped {name}: {exc}")
                continue
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
