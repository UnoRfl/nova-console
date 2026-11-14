"""Sir's own words, for the speech-to-text side.

Whisper spells unfamiliar names by sound: "NVIDIA" came out as "Envije",
and Nova then searched for a company that does not exist. Two fixes, both
read from config.json so he can add words without touching code:

  "vocabulary": ["NVIDIA", "Cobblemon", ...]
      Fed to Whisper as its initial prompt, which biases spelling toward
      these words. Kept short: a long prompt makes Whisper hallucinate it.
  "hear_as": {"envije": "NVIDIA", ...}
      Whole-word, case-insensitive replacements applied to the transcript,
      for the mishearings the prompt alone does not cure.

The say_as keys (words she pronounces specially) are added to the
vocabulary automatically, since those are his words too.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

CONFIG_FILE = Path.home() / ".nova" / "config.json"
BASE_PROMPT = "Hey Nova. Nova, what time is it? Nova, open Spotify and turn the volume down."
MAX_WORDS = 40
_cache = {"at": 0.0, "cfg": {}}


def _cfg() -> dict:
    if time.time() - _cache["at"] > 30:
        try:
            _cache["cfg"] = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            pass
        _cache["at"] = time.time()
    return _cache["cfg"] or {}


def vocabulary() -> list[str]:
    c = _cfg()
    words = list(c.get("vocabulary") or [])
    words += list(((c.get("voice") or {}).get("say_as") or {}).keys())
    seen, out = set(), []
    for w in words:
        if isinstance(w, str) and w.strip() and w.lower() not in seen:
            seen.add(w.lower())
            out.append(w.strip())
    return out[:MAX_WORDS]


def prompt(base: str = BASE_PROMPT) -> str:
    # Vocabulary first, ordinary sentences LAST: Whisper copies the style of the
    # end of its prompt. Ending on the list of CamelCase names made the
    # multilingual model write "Nova, OpenSpotify, and PlayMyLikeSongs."
    words = vocabulary()
    return ("Words Sir uses: " + ", ".join(words) + ". " if words else "") + base


TAGALOG_PROMPT = "Hey Nova. Nova, anong oras na? Nova, buksan mo yung Spotify tapos hinaan mo yung volume."


def language_for(model, audio, model_name: str = "") -> str:
    """Which language to transcribe this clip in: English unless it is clearly Tagalog.

    He speaks English, Tagalog and Taglish to her. Whisper told to write English
    turns Tagalog into nonsense ("books and my own Spotify"), and left to guess
    among all its languages it calls short clips Indonesian or Spanish, so the
    choice is only ever between the configured ones (config "stt_languages",
    default en + tl), and English wins unless the other is the likelier AND at
    least an even bet. An English-only model ("small.en") always gets "en".
    """
    if str(model_name).endswith(".en"):
        return "en"
    langs = [x for x in (_cfg().get("stt_languages") or ["en", "tl"]) if isinstance(x, str) and x]
    if len(langs) < 2:
        return (langs or ["en"])[0]
    try:
        if audio is None or len(audio) < 4800:        # under 0.3 s: nothing to judge
            return langs[0]
        _lang, _p, probs = model.detect_language(audio)
        probs = dict(probs)
    except Exception:                                 # noqa: BLE001 - never lose the sentence over this
        return langs[0]
    best = max(langs, key=lambda x: probs.get(x, 0.0))
    if best != langs[0] and probs.get(best, 0.0) < 0.5:
        return langs[0]
    return best


def prompt_for(lang: str) -> str:
    return prompt() if lang == "en" else prompt(TAGALOG_PROMPT)


def fix(text: str) -> str:
    """Apply hear_as corrections, whole words only, keeping everything else."""
    table = _cfg().get("hear_as") or {}
    for wrong, right in table.items():
        if not isinstance(wrong, str) or not isinstance(right, str) or not wrong.strip():
            continue
        text = re.sub(r"(?<![\w'])" + re.escape(wrong.strip()) + r"(?![\w'])", right, text, flags=re.IGNORECASE)
    return text
