"""Router decisions on a fixed set of requests. Uses the local model if Ollama is up.

    venv\\Scripts\\python setup\\router_test.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")
import nova_harness  # noqa: E402
import nova_router  # noqa: E402

# request -> acceptable tiers
CASES = {
    "hello": {"chat"},
    "Nova, thanks": {"chat"},
    "tell me a joke about cats": {"chat"},
    "how are you today": {"chat"},
    "who painted the mona lisa": {"chat", "normal"},
    "what do you think about pineapple on pizza": {"chat"},
    "what time is it": {"normal"},
    "is the minecraft server up": {"normal"},
    "open spotify": {"normal"},
    "turn the volume down": {"normal"},
    "how much space is on my c drive": {"normal"},
    "what's the weather tomorrow": {"normal"},
    "remind me to call mum at 6": {"normal"},
    "plan my revision for my three exams next week": {"hard"},
    "why does my pc stutter when I alt tab out of roblox and what should I change": {"hard"},
    "compare the rtx 5060 ti and the 4070 for blender rendering": {"hard"},
    "write me an essay intro about climate change": {"hard", "normal"},
    "can you open up a notepad and type in hello inside the notepad": {"normal"},
    "open spotify and play my liked songs": {"normal"},
    "type hello world": {"normal"},
    "open a new python script that renames files": {"code"},
    "make a python script that renames my screenshots by date": {"code"},
    "fix the bug in my discord bot": {"code"},
    "build a website for my portfolio": {"code"},
    "think really hard about how to structure my roblox game's save system": {"expert"},
    "use opus for this: what should I name my cat": {"expert"},
    "ask codex to explain this repo": {"codex"},
    "use the local model, what's two plus two": {"chat"},
}

local = nova_harness.local_model()
print("classifier model:", local or "none (heuristics only)")
r = nova_router.Router({})
bad = 0
for text, ok in CASES.items():
    t0 = time.time()
    tier, how = r.tier(text, local)
    ms = int((time.time() - t0) * 1000)
    good = tier in ok
    bad += not good
    print(f"{'ok  ' if good else 'MISS'} {tier:7} {how:20} {ms:4}ms  {text}" + ("" if good else f"   (want {ok})"))

# follow-up stickiness
r.remember("hard")
tier, how = r.tier("now do the same for the other exam", local)
print(("ok  " if tier == "hard" else "MISS"), "follow-up after hard ->", tier, how)
bad += tier != "hard"
tier, how = r.tier("thanks", local)
print(("ok  " if tier == "chat" else "MISS"), "thanks after hard ->", tier, how)
bad += tier != "chat"

# engine plans
plan = r.plan("hard", claude_ok=False, codex_ok=True, local_ok=True)
print(("ok  " if plan[0] == ("codex", "medium") else "MISS"), "hard, Claude down ->", plan)
bad += plan[0] != ("codex", "medium")
plan = r.plan("chat", claude_ok=True, codex_ok=True, local_ok=True)
print(("ok  " if plan[0] == ("local", "") else "MISS"), "chat ->", plan)
bad += plan[0] != ("local", "")
plan = r.plan("normal", claude_ok=True, codex_ok=True, local_ok=True)
print(("ok  " if plan[0] == ("claude", "haiku") else "MISS"), "normal ->", plan)
bad += plan[0] != ("claude", "haiku")

print("ALL PASS" if not bad else f"{bad} MISSED")
sys.exit(1 if bad else 0)
