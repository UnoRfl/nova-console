#!/usr/bin/env python3
"""ears_bench.py -- how well does she hear? Compare Whisper settings on the same clips.

Speech is synthesised with edge-tts (Filipino-English and US voices, plus Tagalog),
then made harder the way a real desk is: room noise, a quiet mic, fast speech.
It is not Sir's own voice -- for that, run it over the clips her ears keep:

    venv\\Scripts\\python setup\\ears_bench.py                 # synthetic set, every setting
    venv\\Scripts\\python setup\\ears_bench.py --heard         # his real requests in ~/.nova/heard
    venv\\Scripts\\python setup\\ears_bench.py --only small:1,large-v3-turbo:5

Prints word error rate (lower is better), wrong sentences, speed and GPU memory.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")
import numpy as np  # noqa: E402

import nova_ears  # noqa: E402
import nova_words  # noqa: E402

ROOT = Path.home() / ".nova"
CLIPS = ROOT / "setup" / "bench-clips"
HEARD = ROOT / "heard"

# (voice, rate, what is said)
SAY = [
    ("en-PH-JamesNeural", "+0%", "Yo Nova, how you been?"),
    ("en-PH-JamesNeural", "+15%", "Nova, can you check the latest post on the server?"),
    ("en-PH-JamesNeural", "+0%", "Nova, open Valorant."),
    ("en-US-GuyNeural", "+10%", "Nova, unmute my speakers."),
    ("en-PH-JamesNeural", "+0%", "Nova, turn the volume up."),
    ("en-PH-JamesNeural", "+15%", "Nova, play my liked songs on Spotify."),
    ("en-US-GuyNeural", "+0%", "Do you have any updates for me?"),
    ("en-PH-JamesNeural", "+10%", "Do you think it's better if I use Opera or Google Chrome for my RAM usage?"),
    ("en-PH-JamesNeural", "+0%", "Nova, start the Cobblemon server."),
    ("en-US-GuyNeural", "+15%", "Nova, what's the status of the pull request?"),
    ("en-PH-JamesNeural", "+0%", "Okay, can you sing me a song?"),
    ("en-PH-JamesNeural", "+10%", "Nova, remind me to submit my activity at nine tonight."),
    ("en-US-GuyNeural", "+0%", "Nova, open Roblox Studio."),
    ("en-PH-JamesNeural", "+0%", "Nova, check my Gmail."),
    ("en-PH-JamesNeural", "+15%", "Nova, what's my schedule tomorrow?"),
    ("en-US-GuyNeural", "+0%", "Nova, is the Minecraft server up?"),
    ("en-PH-JamesNeural", "+0%", "Nova, open Blender and the project folder."),
    ("en-PH-JamesNeural", "+10%", "Nova, push the Orbit changes to GitHub."),
    ("en-US-GuyNeural", "+0%", "What are you talking about?"),
    ("en-PH-JamesNeural", "+0%", "Never mind."),
    ("en-PH-JamesNeural", "+0%", "Thank you, Nova."),
    ("en-PH-JamesNeural", "+0%", "Nova, describe everything on my screen."),
    ("en-PH-JamesNeural", "+15%", "Nova, I'm tired. I've been coding since this morning."),
    ("en-US-GuyNeural", "+0%", "Nova, ask Codex to review the Supabase migration."),
    ("en-PH-JamesNeural", "+0%", "Nova, open Discord and Claude Code."),
    ("fil-PH-AngeloNeural", "+0%", "Nova, anong oras na?"),
    ("fil-PH-AngeloNeural", "+0%", "Nova, hinaan mo yung volume."),
    ("fil-PH-AngeloNeural", "+10%", "Nova, buksan mo yung Discord."),
    ("fil-PH-AngeloNeural", "+0%", "Nova, kumusta ka na?"),
    ("fil-PH-AngeloNeural", "+0%", "Nova, i-check mo nga yung Gmail ko."),
]

# name -> what it does to a clean clip
def _noise(x: np.ndarray, snr_db: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(len(x) + 8).astype(np.float32)
    hum = np.convolve(white, np.ones(8, dtype=np.float32) / 8, mode="valid")[:len(x)]   # low-heavy, like a fan
    p = float(np.mean(x * x)) or 1e-9
    hum *= np.sqrt(p / (10 ** (snr_db / 10)) / (float(np.mean(hum * hum)) or 1e-9))
    return np.clip(x + hum, -1, 1).astype(np.float32)


CONDITIONS = {
    "clean": lambda x, i: x,
    "fan noise": lambda x, i: _noise(x, 12, i),
    "loud room": lambda x, i: _noise(x, 4, i),
    "quiet mic": lambda x, i: _noise(x * 0.08, 14, i),
}


def norm(text: str) -> list[str]:
    t = text.lower().replace("'", "")
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return [{"okay": "ok", "9": "nine"}.get(w, w) for w in t.split()]


def wer(ref: list[str], hyp: list[str]) -> int:
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r != h))
    return d[-1]


def decode(buf: bytes) -> np.ndarray:
    import av
    chunks = []
    with av.open(io.BytesIO(buf)) as box:
        rs = av.AudioResampler(format="flt", layout="mono", rate=16000)
        for frame in box.decode(audio=0):
            chunks += [f.to_ndarray().reshape(-1) for f in rs.resample(frame)]
        chunks += [f.to_ndarray().reshape(-1) for f in rs.resample(None)]
    return np.concatenate(chunks).astype(np.float32)


async def _speak(text: str, voice: str, rate: str) -> bytes:
    import edge_tts
    buf = b""
    async for chunk in edge_tts.Communicate(text, voice, rate=rate).stream():
        if chunk["type"] == "audio":
            buf += chunk["data"]
    return buf


def synthetic() -> list[tuple[str, str, np.ndarray]]:
    """[(condition, reference text, samples)] -- clips are cached on disk.
    His own names (config "vocabulary") each get a sentence too, so the set
    tests the words he actually says without those words living in this file."""
    CLIPS.mkdir(exist_ok=True)
    out = []
    own = [("en-PH-JamesNeural", "+0%", f"Nova, tell me about {w}.") for w in nova_words.vocabulary()[:24]]
    for i, (voice, rate, text) in enumerate(SAY + own):
        f = CLIPS / (hashlib.sha1(f"{voice}{rate}{text}".encode()).hexdigest()[:16] + ".mp3")
        if not f.exists():
            for attempt in range(3):
                try:
                    f.write_bytes(asyncio.run(asyncio.wait_for(_speak(text, voice, rate), 20)))
                    break
                except Exception:                     # noqa: BLE001
                    if attempt == 2:
                        raise
        clean = decode(f.read_bytes())
        clean = np.concatenate([np.zeros(3200, np.float32), clean, np.zeros(3200, np.float32)])
        for name, fn in CONDITIONS.items():
            out.append((name, text, fn(clean, i)))
    return out


def heard() -> list[tuple[str, str, np.ndarray]]:
    """His real requests. The reference is the corrected text if he fixed one
    (heard/<clip>.json "said"), else these clips only show how settings DIFFER."""
    out = []
    for f in sorted(HEARD.glob("*.npy")):
        meta = {}
        try:
            meta = json.loads(f.with_suffix(".json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        out.append(("his voice", str(meta.get("said") or meta.get("text") or ""),
                    np.load(f).astype(np.float32) / (32768.0 if np.load(f).dtype == np.int16 else 1.0)))
    return out


def vram_mb() -> int:
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=5, creationflags=0x08000000)
        return int(r.stdout.strip().splitlines()[0])
    except Exception:                                 # noqa: BLE001
        return -1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--heard", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--show", action="store_true", help="print every wrong sentence")
    a = ap.parse_args()

    clips = heard() if a.heard else synthetic()
    if not clips:
        print("no clips")
        return 1
    setups = [s.split(":") for s in (a.only.split(",") if a.only else
              ["small:1", "small:5", "large-v3-turbo:1", "large-v3-turbo:5"])]
    print(f"{len(clips)} clips\n")
    rows = []
    for spec in setups:
        name, beam = spec[0], int(spec[1])
        ctype = spec[2] if len(spec) > 2 else None
        before = vram_mb()
        w = nova_ears.Whisper(name, a.device, compute_type=ctype) if ctype else nova_ears.Whisper(name, a.device)
        w.beam = beam
        w.transcribe(clips[0][2])                      # warm
        used = vram_mb() - before
        per: dict[str, list[int]] = {}
        wrong: list[str] = []
        took = []
        for cond, ref, x in clips:
            t = time.time()
            text, _ = w.transcribe(x)
            took.append(time.time() - t)
            r, h = norm(ref), norm(text)
            e = wer(r, h)
            c = per.setdefault(cond, [0, 0, 0])
            c[0] += e
            c[1] += len(r)
            c[2] += e > 0
            if e:
                wrong.append(f"   [{cond}] {ref!r} -> {text!r}")
        label = f"{name} beam {beam}" + (f" {ctype}" if ctype else "")
        tot_e = sum(c[0] for c in per.values())
        tot_w = sum(c[1] for c in per.values()) or 1
        rows.append(label)
        print(f"== {label}   WER {100 * tot_e / tot_w:.1f}%   wrong sentences {sum(c[2] for c in per.values())}/{len(clips)}"
              f"   {1000 * float(np.median(took)):.0f} ms median, {1000 * max(took):.0f} ms worst   +{used} MiB GPU")
        for cond, c in per.items():
            print(f"     {cond:10s} WER {100 * c[0] / (c[1] or 1):5.1f}%   wrong {c[2]}")
        if a.show:
            print("\n".join(wrong))
        del w
        import gc
        gc.collect()
        time.sleep(1.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
