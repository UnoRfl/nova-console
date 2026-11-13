#!/usr/bin/env python3
"""
replay_test.py -- prove the ears make the right call, with no microphone.

Generates spoken test clips with edge-tts (free neural voices), then feeds them
through exactly the decision code the live listener uses, and reports false
accepts and false rejects.

Two rounds:
  A. wake rule only (no voiceprint): does the right WORDING wake her?
  B. speaker gate: voice "owner" is enrolled from a few clips; the same wake
     phrases spoken by OTHER voices must be refused, the owner's accepted.

    python setup/replay_test.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import nova_ears as E  # noqa: E402

CLIPS = Path(__file__).resolve().parent / "replay-clips"
OWNER = "en-GB-RyanNeural"
OTHERS = ["en-US-GuyNeural", "en-US-JennyNeural", "en-AU-WilliamNeural", "en-GB-SoniaNeural"]

SHOULD_WAKE = [
    ("Hey Nova, what time is it?", "what time is it"),
    ("Nova, how much disk space is left?", "disk space"),
    ("Okay Nova, open the downloads folder.", "downloads"),
    ("Hello Nova, is the Minecraft server running?", "minecraft"),
    ("Nova, turn the volume down a little.", "volume"),
    ("Hey Nova, remind me to stretch in twenty minutes.", "stretch"),
]
# Lifted from what the old listener actually sent to Claude, plus the
# near-homophones it was most confused by.
SHOULD_IGNORE = [
    "I'll see you in the next video.",
    "That was it for today, thank you for watching, bye bye.",
    "It's all over now. Game over. No way.",
    "No, I do not know what November will bring.",
    "I read a novel last week about a trip to Nova Scotia.",
    "Did you know that? Did you know that?",
    "Deep in, close range, bottom blink, smoke, left click, right click.",
    "Moving over there now, hold on, never mind.",
    "Hello everyone and welcome back to the channel.",
    "Are you there? Wake up. Wake up.",
    "The new over the air update is out, and it is innovative.",
    "Know that nobody over here noticed.",
]


async def synth(text: str, voice: str, out: Path) -> None:
    import edge_tts
    if out.exists() and out.stat().st_size > 2000:
        return
    await edge_tts.Communicate(text, voice).save(str(out))


async def build() -> dict:
    CLIPS.mkdir(parents=True, exist_ok=True)
    jobs, index = [], {"wake": [], "ignore": []}
    for voice in [OWNER] + OTHERS:
        for i, (text, key) in enumerate(SHOULD_WAKE):
            p = CLIPS / f"wake-{voice}-{i}.mp3"
            jobs.append(synth(text, voice, p))
            index["wake"].append((p, voice, text, key))
        for i, text in enumerate(SHOULD_IGNORE):
            p = CLIPS / f"ignore-{voice}-{i}.mp3"
            jobs.append(synth(text, voice, p))
            index["ignore"].append((p, voice, text))
    sem = asyncio.Semaphore(6)

    async def guarded(j):
        async with sem:
            await j
    await asyncio.gather(*(guarded(j) for j in jobs))
    return index


def verdict(ears: E.Ears, path: Path) -> list[dict]:
    # Each clip is its own conversation: clear every window, including a held
    # request and the mid-sentence grace, or one clip's state leaks into the next.
    ears.armed_until = ears.followup_until = ears.engaged_until = 0.0
    ears.held = None
    return ears.run_file(path)


def main() -> int:
    index = asyncio.run(build())
    cfg = E.load_cfg()
    ears = E.Ears(cfg, dry=True, debug=True)
    print(f"whisper {ears.whisper.name} on {ears.whisper.device}; "
          f"{len(index['wake'])} wake clips, {len(index['ignore'])} ignore clips\n")

    # ---------------------------------------------------- round A: wording
    ears.speaker.print = None
    miss, false, stt = [], [], []
    for p, voice, text, key in index["wake"]:
        res = verdict(ears, p)
        stt += [r["stt_s"] for r in res if "stt_s" in r]
        req = " ".join(r["request"] for r in res if r["action"] == "request").lower()
        if key not in req:
            miss.append((voice, text, [(r["action"], r["text"]) for r in res]))
    for p, voice, text in index["ignore"]:
        res = verdict(ears, p)
        stt += [r["stt_s"] for r in res if "stt_s" in r]
        hits = [r for r in res if r["action"] != "ignore"]
        if hits:
            false.append((voice, text, [(r["action"], r["text"]) for r in hits]))

    print("ROUND A -- wake wording, any voice")
    print(f"  missed wakes : {len(miss)} of {len(index['wake'])}")
    for m in miss:
        print("     ", m)
    print(f"  false wakes  : {len(false)} of {len(index['ignore'])}")
    for f in false:
        print("     ", f)
    if stt:
        print(f"  transcription: median {sorted(stt)[len(stt)//2]:.2f}s, worst {max(stt):.2f}s per clip")

    # ---------------------------------------------- round B: whose voice
    owner_clips = [p for p, v, *_ in index["wake"] if v == OWNER]
    enrol, held_out = owner_clips[:3], owner_clips[3:]
    embeds = []
    for p in enrol:
        embeds.append(ears.speaker.embed(E.read_audio(p)))
    mean = np.mean(np.stack(embeds), axis=0)
    ears.speaker.print = (mean / np.linalg.norm(mean)).astype(np.float32)

    owner_ok = owner_total = 0
    owner_scores, other_scores = [], []
    for p in held_out:
        res = verdict(ears, p)
        owner_total += 1
        owner_ok += any(r["action"] == "request" for r in res)
        owner_scores += [r["score"] for r in res if r["score"] is not None]
    intruders = []
    other_total = 0
    for p, voice, text, key in index["wake"]:
        if voice == OWNER:
            continue
        other_total += 1
        res = verdict(ears, p)
        other_scores += [r["score"] for r in res if r["score"] is not None]
        if any(r["action"] != "ignore" for r in res):
            intruders.append((voice, text, [r["score"] for r in res]))

    print("\nROUND B -- speaker gate (owner enrolled from 3 clips)")
    print(f"  owner accepted on unseen clips : {owner_ok} of {owner_total}"
          f"   scores {min(owner_scores):.2f}..{max(owner_scores):.2f}")
    print(f"  other voices saying the wake   : {len(intruders)} accepted of {other_total}"
          f"   scores {min(other_scores):.2f}..{max(other_scores):.2f}")
    for i in intruders:
        print("     ", i)

    ok = not miss and not false and not intruders and owner_ok == owner_total
    print("\nRESULT:", "PASS" if ok else "NEEDS WORK")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
