#!/usr/bin/env python3
"""
nova_ears.py -- Nova's hearing. Replaces nova-wake.ps1 and Record-Utterance.ps1.

Everything runs on this machine; no audio leaves it.

    python nova_ears.py                  # listen forever (what Nova.ps1 starts)
    python nova_ears.py --enroll         # teach Nova your voice (about 30 seconds)
    python nova_ears.py --file a.wav     # run the same decisions over a recording
    python nova_ears.py --check "hey nova what time is it"   # test the wake rule only
    python nova_ears.py --devices        # list microphones

How a sound becomes a request, in order:

  1. VAD      Silero decides where speech starts and stops. Silence, music and
              game noise never get past this.
  2. Speaker  The segment's voice is compared with the enrolled voiceprint.
              Somebody else -- a video, a stream, a housemate -- stops here,
              before anything is transcribed.
  3. Whisper  The segment is transcribed locally.
  4. Wake     It only counts if "Nova" is in the first few words (or Nova has
              just spoken and is waiting for a follow-up). "Nova, what's the
              time" works in one breath; the words after the name are the
              request.

The old listener matched a 66-phrase word list with the Windows recogniser and
fired on television audio hundreds of times. Requiring the right VOICE and the
right WORD, both judged by proper models, is what fixes that.

Segments that are not for Nova are never written to disk as text: the log
records only that something was ignored, how long it was and why.
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
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path.home() / ".nova"
MODELS = ROOT / "models"
VOICEPRINT = ROOT / "voiceprint.npy"
LOG_FILE = ROOT / "ears.log"
CONFIG_FILE = ROOT / "config.json"
PID_FILE = ROOT / "wake.pid"          # same name the old listener used, so
                                      # Nova.ps1 and the watchdog track it unchanged
RATE = 16000
WINDOW = 512                          # Silero's frame at 16 kHz

DEFAULTS = {
    "whisper_model": "small.en",
    "whisper_device": "auto",         # auto = GPU if it loads, else CPU
    "speaker_threshold": 0.45,        # cosine similarity needed to count as you
    "speaker_threshold_short": 0.38,  # for clips under 1.2 s, which score lower
    "end_silence": 0.7,               # seconds of quiet that end a sentence
    "followup_seconds": 8.0,          # how long Nova keeps listening after she speaks
    "armed_seconds": 8.0,             # how long after a bare "Nova" a request may follow
    "max_sentence": 25.0,
    "input_device": None,             # None = Windows default input
}


# ------------------------------------------------------------------ helpers

def load_cfg() -> dict:
    cfg = dict(DEFAULTS)
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        ears = raw.get("ears") or {}
        cfg.update({k: v for k, v in ears.items() if k in DEFAULTS})
        if raw.get("whisper_model"):
            cfg["whisper_model"] = raw["whisper_model"]
    except (OSError, ValueError):
        pass
    return cfg


def log(msg: str, quiet: bool = False) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    if not quiet:
        try:
            print(line, flush=True)
        except (OSError, ValueError):
            pass
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- wake rule

# What Whisper writes when it hears the name. "Nova" is usually exact; these
# are the mishearings worth accepting. Deliberately NOT here: "no", "over",
# "november", "novel" -- the words that made the old listener fire on anything.
NAME = r"(?:nova|novah|nova's|noah|nava|nover|no va|know va|noba)"
GREETING = r"(?:hey|hi|hello|okay|ok|yo|oi|hay|a|eh)"
WAKE_RE = re.compile(
    rf"^\s*(?:{GREETING}[\s,]+)?{NAME}\b[\s,.!?:;-]*(.*)$",
    re.IGNORECASE | re.DOTALL,
)
# Things said to nobody in particular that happen to start with the name.
NOT_WAKE = re.compile(r"^\s*(?:nova scotia|noah'?s ark|noah and)\b", re.IGNORECASE)

# Whisper invents these on near-silence or noise. Never a request.
HALLUCINATIONS = {
    "", "you", "thank you", "thanks for watching", "thank you for watching",
    "bye", "bye bye", "so", "oh", "uh", "um", "hmm", "okay", "the",
    "i'll see you in the next video", "see you next time",
    "subtitles by the amara.org community", "please subscribe",
}


def clean(text: str) -> str:
    return " ".join(str(text or "").split()).strip()


def is_hallucination(text: str) -> bool:
    t = re.sub(r"[^a-z' ]", "", clean(text).lower()).strip()
    return t in HALLUCINATIONS


def parse_wake(text: str) -> tuple[bool, str]:
    """(is this addressed to Nova, the request that followed her name)."""
    text = clean(text)
    if not text or NOT_WAKE.match(text):
        return False, ""
    m = WAKE_RE.match(text)
    if not m:
        return False, ""
    rest = clean(m.group(1)).strip(" ,.!?")
    return True, rest


# ------------------------------------------------------------------ engines

def add_cuda_dll_dirs() -> None:
    """Make the pip-installed NVIDIA libraries findable before ctranslate2 loads."""
    site = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    if not site.is_dir():
        return
    for sub in site.glob("*/bin"):
        try:
            os.add_dll_directory(str(sub))
        except OSError:
            pass
        os.environ["PATH"] = str(sub) + os.pathsep + os.environ.get("PATH", "")


class Whisper:
    def __init__(self, name: str, device: str) -> None:
        add_cuda_dll_dirs()
        from faster_whisper import WhisperModel

        self.device = "cpu"
        self.model = None
        tried = []
        order = (["cuda", "cpu"] if device == "auto" else [device])
        for dev in order:
            try:
                ctype = "float16" if dev == "cuda" else "int8"
                model = WhisperModel(name, device=dev, compute_type=ctype,
                                     cpu_threads=max(4, (os.cpu_count() or 8) // 2))
                # Loading succeeds even when a CUDA library is missing; only
                # running it proves the GPU path actually works.
                segs, _ = model.transcribe(np.zeros(RATE, dtype=np.float32),
                                           language="en", beam_size=1)
                list(segs)
                self.model, self.device = model, dev
                break
            except Exception as exc:                  # noqa: BLE001
                tried.append(f"{dev}: {type(exc).__name__}: {str(exc)[:120]}")
        if self.model is None:
            raise RuntimeError("Whisper could not start -- " + " | ".join(tried))
        self.name = name
        self.notes = tried

    def transcribe(self, samples: np.ndarray) -> tuple[str, float]:
        began = time.time()
        segs, _ = self.model.transcribe(
            samples, language="en", beam_size=1,
            condition_on_previous_text=False,
            # Bias the spelling of her name; without it "Nova" comes out as
            # "Noah" or "no va" often enough to matter.
            initial_prompt="Hey Nova. Nova, what time is it?",
            no_speech_threshold=0.6,
        )
        parts = []
        for s in segs:
            if getattr(s, "no_speech_prob", 0.0) > 0.8:
                continue
            parts.append(s.text.strip())
        return clean(" ".join(parts)), time.time() - began


class Speaker:
    """Voice matching against one enrolled voiceprint."""

    def __init__(self) -> None:
        import sherpa_onnx
        model = MODELS / "3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx"
        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(model), num_threads=2, provider="cpu")
        self.ext = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
        self.print: np.ndarray | None = None
        if VOICEPRINT.is_file():
            try:
                self.print = np.load(VOICEPRINT).astype(np.float32)
            except (OSError, ValueError):
                self.print = None

    def embed(self, samples: np.ndarray) -> np.ndarray:
        st = self.ext.create_stream()
        st.accept_waveform(sample_rate=RATE, waveform=samples)
        st.input_finished()
        e = np.asarray(self.ext.compute(st), dtype=np.float32)
        n = float(np.linalg.norm(e))
        return e / n if n else e

    def score(self, samples: np.ndarray) -> float | None:
        if self.print is None:
            return None
        return float(np.dot(self.embed(samples), self.print))


def make_vad(cfg: dict):
    import sherpa_onnx
    vc = sherpa_onnx.VadModelConfig()
    vc.silero_vad.model = str(MODELS / "silero_vad.onnx")
    vc.silero_vad.threshold = 0.5
    vc.silero_vad.min_silence_duration = float(cfg["end_silence"])
    vc.silero_vad.min_speech_duration = 0.25
    vc.silero_vad.max_speech_duration = float(cfg["max_sentence"])
    vc.silero_vad.window_size = WINDOW
    vc.sample_rate = RATE
    vc.num_threads = 1
    return sherpa_onnx.VoiceActivityDetector(vc, buffer_size_in_seconds=60)


# ------------------------------------------------------------------- bridge

class Bridge:
    """The two signals the HUD already understands, unchanged from the old listener."""

    def __init__(self, dry: bool = False) -> None:
        self.dry = dry
        self.token = ""
        self.port = 8765
        try:
            self.token = (ROOT / "token").read_text(encoding="utf-8").strip()
            m = re.search(r":(\d+)/", (ROOT / "url.txt").read_text(encoding="utf-8"))
            if m:
                self.port = int(m.group(1))
        except OSError:
            pass

    def _call(self, path: str, body: dict | None = None, timeout: float = 4) -> dict:
        if self.dry or not self.token:
            return {}
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data,
            headers={"X-Nova-Token": self.token, "Content-Type": "application/json"},
            method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return json.loads(res.read().decode("utf-8") or "{}")
        except Exception:                             # noqa: BLE001 - bridge may be down
            return {}

    def wake(self, phrase: str, utterance: str = "") -> None:
        self._call("/api/wake", {"phrase": phrase, "utterance": utterance})

    def poll(self) -> dict:
        """{pending: a follow-up was asked for, speaking: she is talking right now}"""
        return self._call("/api/listen", timeout=2)

    def answer(self, allow: bool) -> None:
        self._call("/api/permission/answer", {"allow": allow, "by": "voice"})

    def stop_speech(self) -> None:
        self._call("/api/stop-speech", {"by": "voice"})


# ------------------------------------------------------------------ the ear

class Ears:
    def __init__(self, cfg: dict, dry: bool = False, debug: bool = False) -> None:
        self.cfg = cfg
        self.debug = debug
        self.bridge = Bridge(dry=dry)
        self.speaker = Speaker()
        self.whisper = Whisper(cfg["whisper_model"], cfg["whisper_device"])
        self.armed_until = 0.0        # a bare "Nova" was heard; a request may follow
        self.followup_until = 0.0     # Nova just spoke; no wake word needed
        self.nova_speaking = False    # she is talking; your voice now means "stop"
        self.approval_pending = False # she asked "shall I ...?" and is waiting
        self.stats = {"segments": 0, "not_you": 0, "no_wake": 0, "requests": 0}

    # One decision per speech segment. Returns a dict describing what happened,
    # which the replay test reads and the live loop logs.
    def decide(self, samples: np.ndarray, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        dur = len(samples) / RATE
        self.stats["segments"] += 1
        out = {"dur": round(dur, 2), "action": "ignore", "why": "", "text": "",
               "score": None, "request": ""}

        if dur < 0.3:
            out["why"] = "too short"
            return out

        # 2. Is it you?
        score = self.speaker.score(samples)
        out["score"] = None if score is None else round(score, 3)
        if score is not None:
            need = (self.cfg["speaker_threshold_short"] if dur < 1.2
                    else self.cfg["speaker_threshold"])
            if score < need:
                self.stats["not_you"] += 1
                out["why"] = "not your voice"
                return out

        # 3. What was said?
        text, took = self.whisper.transcribe(samples)
        out["stt_s"] = round(took, 2)
        if is_hallucination(text):
            out["why"] = "nothing intelligible"
            return out

        # She is waiting for a yes or no. Only an ENROLLED voice may answer:
        # without a voiceprint anyone -- or any video -- could say "yes".
        if self.approval_pending and score is not None:
            import nova_gate
            verdict = nova_gate.spoken_answer(text)
            if verdict is not None:
                out.update(action="answer", text=text, allow=verdict,
                           why="approved" if verdict else "refused")
                return out

        # 4. Was it for Nova?
        listening = now < self.armed_until or now < self.followup_until
        woke, rest = parse_wake(text)

        if woke and len(rest) >= 2:
            out.update(action="request", request=rest, text=text, why="wake + request")
        elif woke:
            self.armed_until = now + float(self.cfg["armed_seconds"])
            out.update(action="armed", text=text, why="name only -- waiting for the request")
        elif listening:
            out.update(action="request", request=text, text=text,
                       why="follow-up" if now < self.followup_until else "after name")
        else:
            self.stats["no_wake"] += 1
            out["why"] = "your voice, but not addressed to Nova"
            if self.debug:
                out["text"] = text
            return out

        if out["action"] == "request":
            self.armed_until = 0.0
            self.followup_until = 0.0
            self.stats["requests"] += 1
        return out

    def act(self, d: dict) -> None:
        if d["action"] == "answer":
            self.bridge.answer(d["allow"])
            self.approval_pending = False
            log(f"ANSWER: {'yes' if d['allow'] else 'no'} (score {d['score']})")
        elif d["action"] == "armed":
            self.bridge.wake("listening")
            log(f"WAKE (name only) score={d['score']} -- listening for the request")
        elif d["action"] == "request":
            self.bridge.wake("nova", d["request"])
            log(f"REQUEST [{d['why']}] score={d['score']} stt={d.get('stt_s')}s: {d['request']!r}")
        else:
            # No transcript on purpose: this was not said to Nova.
            log(f"ignored {d['dur']}s -- {d['why']}"
                + (f" (score {d['score']})" if d["score"] is not None else "")
                + (f" text={d['text']!r}" if self.debug and d.get("text") else ""),
                quiet=not self.debug)

    # -------------------------------------------------------------- live
    def listen_forever(self) -> None:
        import sounddevice as sd

        vad = make_vad(self.cfg)
        q: queue.Queue = queue.Queue()

        def on_audio(indata, frames, t, status):      # audio thread: just hand off
            q.put(indata[:, 0].copy())

        def poll_followup() -> None:
            # The HUD asks for a follow-up when it finishes speaking.
            while True:
                state = self.bridge.poll()
                self.nova_speaking = bool(state.get("speaking"))
                self.approval_pending = bool(state.get("approval"))
                if state.get("pending"):
                    self.followup_until = time.time() + float(self.cfg["followup_seconds"])
                    log("follow-up window open", quiet=True)
                time.sleep(0.25)

        threading.Thread(target=poll_followup, daemon=True).start()

        dev = self.cfg.get("input_device")
        info = sd.query_devices(dev, "input")
        log(f"ears up -- mic '{info['name']}', whisper {self.whisper.name} on "
            f"{self.whisper.device}, voiceprint "
            f"{'enrolled' if self.speaker.print is not None else 'NOT ENROLLED (anyone can wake her)'}")
        for note in self.whisper.notes:
            log(f"  note: {note}")

        buf = np.zeros(0, dtype=np.float32)
        recent = np.zeros(0, dtype=np.float32)       # the last second, for talk-over
        speech_run = 0                                # consecutive frames of speech
        barged = False
        with sd.InputStream(samplerate=RATE, channels=1, dtype="float32",
                            blocksize=WINDOW, device=dev, callback=on_audio):
            while True:
                buf = np.concatenate([buf, q.get()])
                while len(buf) >= WINDOW:
                    frame = buf[:WINDOW]
                    vad.accept_waveform(frame)
                    buf = buf[WINDOW:]
                    recent = np.concatenate([recent, frame])[-RATE:]
                    if vad.is_speech_detected():
                        speech_run += 1
                    else:
                        speech_run, barged = 0, False

                    # Talk-over. While she is speaking, about 0.6 s of YOUR
                    # voice stops her mid-sentence, and whatever you are saying
                    # is taken as the next request with no wake word. It needs
                    # the voiceprint: without one, her own voice coming back
                    # through the speakers would interrupt her.
                    if (self.nova_speaking and not self.approval_pending
                            and not barged and speech_run >= 19
                            and self.speaker.print is not None):
                        score = self.speaker.score(recent[-int(RATE * 0.8):])
                        if score is not None and score >= self.cfg["speaker_threshold_short"]:
                            barged = True
                            self.bridge.stop_speech()
                            self.followup_until = time.time() + float(self.cfg["followup_seconds"])
                            log(f"talk-over: stopped her (score {score:.2f})")
                while not vad.empty():
                    seg = np.asarray(vad.front.samples, dtype=np.float32)
                    vad.pop()
                    try:
                        self.act(self.decide(seg))
                    except Exception as exc:          # noqa: BLE001 - never die on one clip
                        log(f"segment failed: {type(exc).__name__}: {exc}")

    # -------------------------------------------------------------- files
    def run_file(self, path: Path) -> list[dict]:
        """Feed a recording through exactly the same decisions, as if it were live."""
        samples = read_audio(path)
        vad = make_vad(self.cfg)
        results = []
        clock = 0.0                                   # a fake clock that follows the audio
        for i in range(0, len(samples) - WINDOW + 1, WINDOW):
            vad.accept_waveform(samples[i:i + WINDOW])
            clock = i / RATE
            while not vad.empty():
                seg = np.asarray(vad.front.samples, dtype=np.float32)
                vad.pop()
                results.append(self.decide(seg, now=clock))
        vad.flush()
        while not vad.empty():
            seg = np.asarray(vad.front.samples, dtype=np.float32)
            vad.pop()
            results.append(self.decide(seg, now=clock))
        return results


def read_audio(path: Path) -> np.ndarray:
    """Any audio file -> 16 kHz mono float32, via PyAV (no ffmpeg needed)."""
    import av
    out = []
    with av.open(str(path)) as c:
        rs = av.AudioResampler(format="s16", layout="mono", rate=RATE)
        for frame in c.decode(audio=0):
            for f in rs.resample(frame):
                out.append(f.to_ndarray().reshape(-1))
        for f in rs.resample(None):
            out.append(f.to_ndarray().reshape(-1))
    if not out:
        return np.zeros(0, dtype=np.float32)
    return (np.concatenate(out).astype(np.float32) / 32768.0)


# --------------------------------------------------------------- enrolment

ENROL_LINES = [
    "Hey Nova, what time is it?",
    "Nova, how much disk space is left on this computer?",
    "Hey Nova, start the Minecraft server and tell me when it is ready.",
    "Nova, open my project folder and show me what changed today.",
    "Hey Nova.",
    "Nova.",
    "Yes, go ahead and do that.",
    "No, cancel that please.",
]


def enroll(cfg: dict) -> int:
    import sounddevice as sd

    speaker = Speaker()
    vad = make_vad(cfg)
    q: queue.Queue = queue.Queue()

    def on_audio(indata, frames, t, status):
        q.put(indata[:, 0].copy())

    dev = cfg.get("input_device")
    print(f"\nMicrophone: {sd.query_devices(dev, 'input')['name']}")
    print("Read each line aloud in your normal voice, the way you would actually say it.\n")

    embeds = []
    buf = np.zeros(0, dtype=np.float32)
    with sd.InputStream(samplerate=RATE, channels=1, dtype="float32",
                        blocksize=WINDOW, device=dev, callback=on_audio):
        for n, line in enumerate(ENROL_LINES, 1):
            while not q.empty():                      # drop anything said between lines
                q.get()
            vad.reset() if hasattr(vad, "reset") else None
            print(f"  {n}/{len(ENROL_LINES)}  say:  \"{line}\"", flush=True)
            deadline = time.time() + 15
            got = None
            while time.time() < deadline and got is None:
                try:
                    buf = np.concatenate([buf, q.get(timeout=0.5)])
                except queue.Empty:
                    continue
                while len(buf) >= WINDOW:
                    vad.accept_waveform(buf[:WINDOW])
                    buf = buf[WINDOW:]
                while not vad.empty():
                    seg = np.asarray(vad.front.samples, dtype=np.float32)
                    vad.pop()
                    if len(seg) / RATE >= 0.35:
                        got = seg
            if got is None:
                print("        (heard nothing -- skipped)")
                continue
            embeds.append(speaker.embed(got))
            print(f"        got {len(got)/RATE:.1f}s")

    if len(embeds) < 4:
        print("\nNot enough clean samples. Check the microphone and run it again.")
        return 1

    mean = np.mean(np.stack(embeds), axis=0)
    mean = mean / np.linalg.norm(mean)
    np.save(VOICEPRINT, mean.astype(np.float32))
    scores = [float(np.dot(e, mean)) for e in embeds]
    print(f"\nVoiceprint saved from {len(embeds)} samples.")
    print("How closely each sample matches it (1.0 is identical): "
          + ", ".join(f"{s:.2f}" for s in scores))
    print(f"Lowest {min(scores):.2f}. Nova will accept anything above "
          f"{cfg['speaker_threshold']:.2f} ({cfg['speaker_threshold_short']:.2f} for very short clips).")
    log(f"voiceprint enrolled from {len(embeds)} samples, min self-score {min(scores):.2f}", quiet=True)
    print("NOVA-ENROL-DONE")
    return 0


# --------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="Nova's hearing")
    ap.add_argument("--enroll", action="store_true", help="record your voiceprint")
    ap.add_argument("--file", nargs="+", help="run the pipeline over audio files and print JSON")
    ap.add_argument("--check", nargs="+", help="test the wake rule on typed phrases")
    ap.add_argument("--devices", action="store_true", help="list input devices")
    ap.add_argument("--debug", action="store_true", help="log ignored speech with its text")
    ap.add_argument("--no-voiceprint", action="store_true", help="ignore the enrolled voice (testing)")
    args = ap.parse_args()
    cfg = load_cfg()

    if args.check:
        for phrase in args.check:
            woke, rest = parse_wake(phrase)
            print(f"  {phrase!r:48} -> {'WAKE' if woke else 'ignored'}"
                  + (f"   request: {rest!r}" if woke else ""))
        return 0

    if args.devices:
        import sounddevice as sd
        print(sd.query_devices())
        return 0

    if args.enroll:
        return enroll(cfg)

    if args.file:
        ears = Ears(cfg, dry=True, debug=True)
        if args.no_voiceprint:
            ears.speaker.print = None
        report = {}
        for name in args.file:
            ears.armed_until = ears.followup_until = 0.0
            report[name] = ears.run_file(Path(name))
        print(json.dumps({"device": ears.whisper.device, "results": report}, indent=1))
        return 0

    try:
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass
    try:
        Ears(cfg, debug=args.debug).listen_forever()
    except KeyboardInterrupt:
        pass
    finally:
        log("ears stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
