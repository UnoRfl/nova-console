"""Her ears on Tagalog, Taglish and English, through the real nova_ears.Whisper.

Speech is synthesised with edge-tts (needs internet), so this checks the
language choice and the wording, not his own voice or a noisy room.

    venv\\Scripts\\python setup\\tagalog_ears_test.py [cpu|cuda|auto]
"""
import asyncio
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")
import av  # noqa: E402
import edge_tts  # noqa: E402
import numpy as np  # noqa: E402

import nova_ears  # noqa: E402
import nova_words  # noqa: E402

# (voice, what is said, language she must choose, words that must come out)
CASES = [
    ("fil-PH-AngeloNeural", "Nova, anong oras na?", "tl", ["nova", "anong", "oras"]),
    ("fil-PH-BlessicaNeural", "Nova, buksan mo yung Spotify tapos patugtugin mo yung playlist ko.", "tl",
     ["nova", "buksan", "spotify", "playlist"]),
    ("fil-PH-AngeloNeural", "Nova, i-check mo nga kung gumagana pa yung Minecraft server.", "tl",
     ["nova", "minecraft", "server"]),
    ("fil-PH-BlessicaNeural", "Nova, hinaan mo yung volume.", "tl", ["nova", "hinaan", "volume"]),
    ("fil-PH-AngeloNeural", "Salamat Nova.", "tl", ["salamat", "nova"]),
    ("en-PH-JamesNeural", "Nova, what time is it?", "en", ["nova", "what", "time"]),
    ("en-US-GuyNeural", "Nova, open Spotify and play my liked songs.", "en", ["nova", "open", "spotify", "songs"]),
    ("en-PH-JamesNeural", "Turn the volume down.", "en", ["turn", "volume", "down"]),
    ("en-US-GuyNeural", "Nova.", "en", ["nova"]),
    ("en-PH-JamesNeural", "Nova, game mode.", "en", ["nova", "game", "mode"]),
    ("en-US-GuyNeural", "Stop.", "en", ["stop"]),
    # his vocabulary still steers the spelling, and words are not glued together
    ("en-US-GuyNeural", "Nova, is the NVIDIA driver up to date?", "en", ["nova", "nvidia", "driver"]),
    ("en-PH-JamesNeural", "Nova, start the Cobblemon server and open Discord.", "en",
     ["nova", "cobblemon", "server", "open", "discord"]),
    ("fil-PH-BlessicaNeural", "Nova, i-open mo yung Discord tsaka yung Roblox.", "tl", ["nova", "discord", "roblox"]),
]


async def speak(text: str, voice: str) -> np.ndarray:
    buf = b""
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk["type"] == "audio":
            buf += chunk["data"]
    chunks = []
    with av.open(io.BytesIO(buf)) as box:
        rs = av.AudioResampler(format="flt", layout="mono", rate=16000)
        for frame in box.decode(audio=0):
            chunks += [f.to_ndarray().reshape(-1) for f in rs.resample(frame)]
        chunks += [f.to_ndarray().reshape(-1) for f in rs.resample(None)]
    return np.concatenate(chunks).astype(np.float32)


def main() -> int:
    cfg = nova_ears.load_cfg()
    device = sys.argv[1] if len(sys.argv) > 1 else "cpu"      # cpu by default: leaves the card to his game
    w = nova_ears.Whisper(cfg["whisper_model"], device)
    print(f"model {w.name} on {w.device}")
    if w.name.endswith(".en"):
        print("SKIP: an English-only model is configured")
        return 0
    bad = 0
    for voice, said, want, must in CASES:
        audio = asyncio.run(speak(said, voice))
        lang = nova_words.language_for(w.model, audio, w.name)
        text, took = w.transcribe(audio)
        heard = re.sub(r"[^a-z ]", " ", text.lower()).split()     # whole words: "openspotify" is a miss
        missing = [m for m in must if m not in heard]
        good = lang == want and not missing
        bad += not good
        print(f"{'ok  ' if good else 'MISS'} {want}>{lang} {took * 1000:5.0f}ms  {said}  =>  {text}"
              + ("" if good else f"   (missing {missing})"))
    # An English-only model never tries Tagalog, whatever the audio.
    only_en = nova_words.language_for(w.model, np.zeros(16000, dtype=np.float32), "small.en") == "en"
    print(("ok  " if only_en else "MISS"), "small.en always answers en")
    bad += not only_en
    print("ALL PASS" if not bad else f"{bad} MISSED")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
