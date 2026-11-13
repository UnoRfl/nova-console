#!/usr/bin/env python3
"""
nova_router.py -- decide how much brain a request deserves.

Every request gets a tier, then the tier gets the cheapest engine that can do
it well. Chit-chat never touches a subscription; only genuinely hard work
reaches the expensive models.

    chat     banter, jokes, opinions, general knowledge     -> local (Ollama, free)
    normal   anything that needs to look or act, or a real  -> Claude Haiku
             question with a short answer
    hard     planning, debugging, analysis, writing,        -> Claude Sonnet
             several steps
    expert   "think hard", big design or research calls     -> Claude Opus
    code     write / build / fix code                       -> Claude Sonnet (which can
                                                               hand big builds to Codex)

How a tier is picked, cheapest first:
  1. He named an engine ("use opus for this", "ask codex ...")       -> that
  2. A short follow-up right after a hard turn ("now do the other    -> same tier
     one too") inherits it, so a conversation is not dropped mid-task
  3. Clear patterns (greetings, thanks, "what time is it", "write a  -> rules
     script") are decided by rules, no model at all
  4. Everything else: the local model classifies it (~0.3 s, free)  -> classifier
  5. Local model down: length and keyword heuristics                 -> heuristics

The tier -> engine table lives in config.json under "routing", so it can be
changed without touching code. When Claude is not signed in, or today's Claude
budget is spent, Claude tiers fall back to Codex (ChatGPT subscription), then
to the local model.

    python nova_router.py "some request"     # show the decision
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request

TIERS = ("chat", "normal", "hard", "expert", "code")
RANK = {"chat": 0, "normal": 1, "code": 2, "hard": 2, "expert": 3}

DEFAULT_ROUTES = {
    "chat": "local",
    "normal": "claude:haiku",
    "hard": "claude:sonnet",
    "expert": "claude:opus",
    "code": "claude:sonnet",
}
# What to use for a tier when its Claude engine cannot be used right now.
CODEX_EFFORT = {"normal": "low", "code": "medium", "hard": "medium", "expert": "high", "chat": "low"}

STICKY_SECONDS = 120          # how long a hard turn's follow-ups inherit its tier

# ------------------------------------------------------------------ patterns

def _words(*items: str) -> str:
    return r"\b(?:" + "|".join(items) + r")\b"


# 1. He asked for an engine by name.
EXPLICIT = [
    ("expert", re.compile(r"\b(?:use|with|ask|switch to|get) (?:claude )?opus\b|\bthink (?:really )?hard\b|"
                          r"\b(?:deep|serious|careful) think\b|\bultrathink\b")),
    ("hard", re.compile(r"\b(?:use|with|ask|switch to|get) (?:claude )?sonnet\b")),
    ("normal", re.compile(r"\b(?:use|with|ask|switch to|get) (?:claude )?haiku\b")),
    ("codex", re.compile(r"\b(?:use|with|ask|have|get|let) (?:the )?(?:codex|chat ?gpt|gpt)\b|^codex\b")),
    ("chat", re.compile(r"\b(?:use|with|ask) (?:the )?(?:local|offline) (?:model|brain)\b")),
]

# 2. Follow-up openers.
FOLLOW_UP = re.compile(r"^(?:and|also|now|then|ok(?:ay)?|so|but|what about|how about|do (?:the )?same|"
                       r"same for|again|try again|keep going|continue|go on|next|another|"
                       r"(?:can|could) you (?:also|now)|it|that|this|those|them|fix (?:it|that))\b")

# 3a. Plainly conversation.
CHAT = re.compile(
    r"^(?:(?:hi|hello|hey|yo|sup|morning|good (?:morning|afternoon|evening|night)|"
    r"thanks?(?: you)?|thank you|cheers|nice|cool|great|awesome|lol|haha|ok(?:ay)?|got it|"
    r"never ?mind|bye|goodbye|see (?:you|ya)|love you|you'?re (?:the best|great|funny|smart))\b.{0,30}$|"
    r"how are you|how(?:'s| is) it going|what'?s up|who are you|what(?:'s| is) your name|"
    r"tell me (?:a )?(?:joke|story|fun fact|something (?:funny|interesting|cool))|"
    r"make me laugh|i(?:'m| am) (?:bored|tired|sad|happy|hungry)|"
    r"(?:do|would|could) you (?:like|love|enjoy|prefer|think)|what do you think (?:of|about)|"
    r"(?:who|which) is (?:better|the best)|rate my|roast me|compliment me)")

# 3b. Needs to look at or touch something real: never the local model.
TOOLS = re.compile(_words(
    r"what time", r"time is it", r"what(?:'s| is) the date", r"today'?s date", r"what day",
    r"weather", r"forecast", r"news", r"latest", r"price of", r"stock",
    r"open", r"close", r"launch", r"start", r"stop", r"restart", r"kill", r"run",
    r"play", r"pause", r"skip", r"volume", r"mute", r"louder", r"quieter",
    r"check", r"look up", r"search", r"google", r"find", r"show me", r"read",
    r"file", r"files", r"folder", r"downloads?", r"desktop", r"screenshot", r"screen",
    r"my pc", r"computer", r"cpu", r"gpu", r"ram", r"disk", r"storage", r"battery", r"temps?",
    r"server", r"minecraft", r"players?", r"online", r"discord", r"email", r"calendar",
    r"remind", r"reminder", r"timer", r"alarm", r"schedule",
    r"lights?", r"rgb", r"download", r"install", r"uninstall", r"update",
    r"send", r"message", r"post", r"website", r"url", r"link", r"github", r"repo",
    r"memory of", r"do you remember", r"what did i",
    r"messages", r"texted?", r"chats?", r"whatsapp", r"messenger", r"gmail", r"inbox", r"emails", r"mails?",
    r"mom", r"mommy", r"mum", r"sent me", r"replied", r"settings?", r"browser", r"chrome", r"tab", r"click", r"class(?:es)?",
    # Doing something to a window, a tab, the panel or a screen is an action:
    # the local chat model cannot act and must never get these.
    r"remove", r"hide", r"dismiss", r"minimi[sz]e", r"maximi[sz]e", r"move", r"delete",
    r"get rid of", r"take (?:it|that|this) (?:down|away|off)", r"put (?:it|that) away",
    r"tab", r"window", r"panel", r"monitor", r"display", r"do it", r"did you",
    # Putting something on his screen, or asking her to just do it.
    r"in front", r"to the front", r"bring (?:it|that|this|them)? ?(?:up|forward|back)", r"pull (?:it|that)? ?up",
    r"put (?:it|that|this) (?:up|on|in)", r"can'?t see", r"cannot see", r"don'?t see", r"show (?:it|that|this|the)",
    r"lyrics", r"automatically", r"for me", r"do (?:that|this)", r"handle it"))

# 3b'. Driving the PC: opening apps, typing, pressing keys. One or two tool
# calls -- Haiku's job, however many words it takes to say.
APP_CONTROL = re.compile(
    r"^(?:can you |could you |please )?(?:open|launch|start|bring up|pull up|close|switch to|go to|"
    r"type|press|paste)\b")
CODEY = re.compile(_words(r"script", r"program", r"code", r"function", r"class", r"bot", r"mod",
                          r"plugin", r"api", r"python", r"javascript", r"html", r"css", r"sql",
                          r"repo", r"bug", r"error", r"compile"))

# 3c. Writing or fixing code.
CODE = re.compile(
    r"\b(?:write|make|build|create|code|program|scaffold|generate|refactor|debug|fix|port|convert)\b"
    r"[^.?!]{0,40}\b(?:script|program|code|app|website|site|function|class|bot|mod|plugin|api|"
    r"bug|error|crash|exception|datapack|luau?|python|javascript|typescript|html|css|sql|"
    r"powershell|batch file|regex|component|endpoint|game)\b|"
    # "open a new python script that ..." starts writing code. Narrower noun
    # list on purpose: "start a new game" or "open a new tab" are not code.
    r"\b(?:open|start|begin|spin up)\s+(?:up\s+)?(?:a\s+)?(?:new|fresh|blank)\b[^.?!]{0,25}"
    r"\b(?:script|program|project|function|class|bot|mod|plugin|datapack|python|javascript|"
    r"typescript|powershell|luau?)\b|"
    r"\b(?:stack ?trace|traceback|syntax error|compile error|won'?t compile|pull request|merge conflict)\b")

# 5. Heuristic markers for when no classifier is available.
HARD = re.compile(_words(
    r"plan", r"design", r"architect\w*", r"analy[sz]e", r"analysis", r"compare", r"comparison",
    r"pros and cons", r"trade-?offs?", r"step by step", r"explain (?:how|why) .{0,40}works?",
    r"why (?:does|is|do|did|won'?t|isn'?t)", r"debug", r"diagnose", r"troubleshoot",
    r"research", r"investigate", r"essay", r"report", r"summari[sz]e", r"proposal",
    r"strategy", r"optimi[sz]e", r"review", r"audit", r"walk me through", r"break down",
    r"in detail", r"detailed", r"thorough(?:ly)?", r"comprehensive"))
EXPERT = re.compile(_words(r"think (?:really )?hard", r"deeply", r"very carefully", r"really carefully",
                           r"as thoroughly as", r"best possible", r"no mistakes", r"ultrathink",
                           r"whole architecture", r"from scratch .{0,30}(?:system|architecture|engine)"))

# ---------------------------------------------------------------- classifier

CLASSIFY_SYSTEM = """You sort requests for a voice assistant by how much thinking they need.
Answer with JSON only.

tier:
- "chat": small talk, jokes, feelings, opinions, simple general knowledge or trivia that needs no lookup.
- "normal": needs to check or do something on the computer or the internet, OR a real question with a short factual answer, OR a simple one-step task.
- "hard": several steps, planning, explaining how or why something works in depth, debugging, comparing options, writing more than a paragraph.
- "expert": large design, architecture or research decisions, or anything the user asks to think very hard about.
- "code": writing, building or fixing code, scripts, apps, mods or websites.

Examples:
"what's your favourite colour" -> {"tier":"chat"}
"who wrote hamlet" -> {"tier":"chat"}
"is the minecraft server up" -> {"tier":"normal"}
"how much space is left on my c drive" -> {"tier":"normal"}
"what's 15 percent of 240" -> {"tier":"normal"}
"why does my game stutter when I open chrome and what should I change" -> {"tier":"hard"}
"plan my revision for three exams next week" -> {"tier":"hard"}
"design the whole save system for my roblox game so it never loses data" -> {"tier":"expert"}
"make a python script that renames my screenshots by date" -> {"tier":"code"}"""

SCHEMA = {"type": "object", "properties": {"tier": {"type": "string", "enum": list(TIERS)}},
          "required": ["tier"]}


def classify_local(prompt: str, model: str, url: str = "http://127.0.0.1:11434",
                   timeout: float = 2.5) -> str | None:
    """Ask the local model for a tier. None if it is down, slow or says nonsense."""
    body = json.dumps({
        "model": model, "stream": False, "format": SCHEMA, "keep_alive": "30m",
        "options": {"temperature": 0, "num_predict": 20},
        "messages": [{"role": "system", "content": CLASSIFY_SYSTEM},
                     {"role": "user", "content": prompt[:600]}],
    }).encode("utf-8")
    req = urllib.request.Request(url + "/api/chat", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read().decode("utf-8"))
        tier = json.loads((out.get("message") or {}).get("content") or "{}").get("tier")
    except Exception:                                  # noqa: BLE001
        return None
    return tier if tier in TIERS else None


def heuristic(said: str) -> str:
    if EXPERT.search(said):
        return "expert"
    if HARD.search(said) or len(said.split()) > 45:
        return "hard"
    return "normal"


# -------------------------------------------------------------------- router

def _clean(prompt: str) -> str:
    said = " ".join((prompt or "").lower().split())
    return re.sub(r"^(?:(?:hey|okay|ok|yo) )?nova[,!.]?\s*", "", said).strip()


class Router:
    """Picks a tier for each request and an engine for each tier."""

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.last_tier = "normal"
        self.last_at = 0.0

    # ------------------------------------------------------------ settings
    def enabled(self) -> bool:
        r = self.cfg.get("routing")
        return not (isinstance(r, dict) and r.get("enabled") is False)

    def routes(self) -> dict:
        r = dict(DEFAULT_ROUTES)
        conf = self.cfg.get("routing")
        if isinstance(conf, dict):
            r.update({k: v for k, v in conf.items() if k in TIERS and isinstance(v, str)})
        return r

    # ------------------------------------------------------------- tiering
    def tier(self, prompt: str, local_model: str | None, classify=classify_local) -> tuple[str, str]:
        """(tier, how it was decided). 'codex' may come back as a tier when he asked for it."""
        said = _clean(prompt)
        if not said:
            return "chat", "empty"

        for tier, pattern in EXPLICIT:
            if pattern.search(said):
                return tier, "asked"

        recent = time.time() - self.last_at < STICKY_SECONDS
        if (recent and RANK.get(self.last_tier, 1) >= 2 and len(said.split()) <= 14
                and FOLLOW_UP.match(said)):
            return self.last_tier, "follow-up"

        if APP_CONTROL.search(said) and not HARD.search(said) and not CODEY.search(said):
            return "normal", "rules"                     # "open notepad and type hello"
        if CODE.search(said):
            return "code", "rules"
        needs_tools = bool(TOOLS.search(said))
        if CHAT.search(said) and not needs_tools:
            return "chat", "rules"
        if needs_tools and len(said.split()) <= 8 and not HARD.search(said):
            return "normal", "rules"                     # "open spotify", "volume down"

        guess = classify(said, local_model) if local_model else None
        # Shortly after real work, a reply that is not plain small talk is
        # about that work ("can't you do that for me?"): never the toolless model.
        working = time.time() - getattr(self, "last_work_at", 0.0) < STICKY_SECONDS
        if guess:
            if needs_tools and guess == "chat":
                return "normal", "classifier+tools"
            if working and guess == "chat" and not CHAT.search(said):
                return "normal", "classifier+follow-up"
            if guess == "expert" and not EXPERT.search(said) and len(said.split()) < 25:
                return "hard", "classifier (capped)"      # the 3B model over-calls expert
            return guess, "classifier"
        return heuristic(said), "heuristics"

    def remember(self, tier: str) -> None:
        if tier in TIERS:
            self.last_tier, self.last_at = tier, time.time()
            if RANK.get(tier, 0) >= 1:
                self.last_work_at = self.last_at

    # ------------------------------------------------------------- engines
    def plan(self, tier: str, claude_ok: bool, codex_ok: bool, local_ok: bool) -> list[tuple[str, str]]:
        """Engines to try for a tier, best first: [("claude","haiku"), ("codex","low"), ("local","")]."""
        if tier == "codex":
            chain = [("codex", CODEX_EFFORT["hard"])]
            tier = "hard"
        else:
            chain = []
        target = self.routes().get(tier, "claude:haiku")
        kind, _, model = target.partition(":")
        if kind == "claude":
            chain.append(("claude", model or "haiku"))
        elif kind == "codex":
            chain.append(("codex", model or CODEX_EFFORT.get(tier, "medium")))
        else:
            chain.append(("local", ""))
        # fallbacks, in cost order, for when the first choice cannot run
        if tier == "chat":
            chain += [("claude", "haiku"), ("codex", "low")]
        else:
            chain += [("codex", CODEX_EFFORT.get(tier, "medium")), ("claude", "haiku"), ("local", "")]
        ok = {"claude": claude_ok, "codex": codex_ok, "local": local_ok}
        seen, out = set(), []
        for engine, arg in chain:
            if ok.get(engine) and (engine, arg) not in seen:
                seen.add((engine, arg))
                out.append((engine, arg))
        return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    import nova_harness
    r = Router({})
    for text in sys.argv[1:] or ["tell me a joke", "is the server up", "plan my week"]:
        t0 = time.time()
        tier, how = r.tier(text, nova_harness.local_model())
        print(f"{tier:7} {how:22} {int((time.time() - t0) * 1000):4} ms  {text}")
        print("        ->", r.plan(tier, True, True, True))
