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
BASE_PROMPT = "Hey Nova. Nova, what time is it?"
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
    words = vocabulary()
    return base + (" Words Sir uses: " + ", ".join(words) + "." if words else "")


def fix(text: str) -> str:
    """Apply hear_as corrections, whole words only, keeping everything else."""
    table = _cfg().get("hear_as") or {}
    for wrong, right in table.items():
        if not isinstance(wrong, str) or not isinstance(right, str) or not wrong.strip():
            continue
        text = re.sub(r"(?<![\w'])" + re.escape(wrong.strip()) + r"(?![\w'])", right, text, flags=re.IGNORECASE)
    return text
