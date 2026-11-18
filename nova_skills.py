"""Skills Nova teaches herself: HOW to do things, not facts.

Memory (nova_memory) keeps what is true about Sir. This keeps procedures.
After a request that took real work (several tool calls) and went well, one
cheap tool-less model call looks at what was asked and the steps she took,
and either writes a short recipe or improves the one she already has for that
kind of task. Next time a similar request comes in, the best matching recipe
travels with the prompt, so she goes straight to what worked and skips what
did not.

  skills/<slug>.md    one skill: front matter (name, when, uses, version,
                      updated) + a few lines of steps and pitfalls

Safety: a turn that read untrusted content (a web page, an email, a chat --
the taint flag) never teaches a skill, so nothing a page says can become a
standing instruction. Skills are plain files Sir can read, edit or delete;
"forget the skill X" removes one. Costs nothing unless a long task just ran,
and recall is local (no model).
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILLS_DIR = ROOT / "skills"
MIN_TOOLS = 3                    # fewer tool calls than this is not worth a recipe
MAX_SKILL_CHARS = 1800
RECALL_MIN_SCORE = 2.0
STOP = set("""a an and are as at be but by can could do does for from get got have he her his how i if in
into is it its just me my no not now of on or our please she so some than that the their them then there
these they this to up us use was we were what when where which who why will with would you your nova sir
want need make let lets can't don't""".split())

_lock = threading.Lock()


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9][a-z0-9+#.-]*", (text or "").lower()) if w not in STOP and len(w) > 1]


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s[:60] or "skill"


def parse(text: str) -> dict:
    meta, body = {}, text
    m = re.match(r"---\s*\n(.*?)\n---\s*\n?(.*)", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            if k.strip():
                meta[k.strip()] = v.strip()
        body = m.group(2)
    meta["body"] = body.strip()
    return meta


def render(meta: dict) -> str:
    head = "\n".join(f"{k}: {meta[k]}" for k in ("name", "when", "uses", "version", "updated") if k in meta)
    return f"---\n{head}\n---\n{meta.get('body', '').strip()}\n"


def all_skills() -> list[dict]:
    out = []
    if SKILLS_DIR.is_dir():
        for f in sorted(SKILLS_DIR.glob("*.md")):
            try:
                s = parse(f.read_text(encoding="utf-8"))
            except OSError:
                continue
            s["file"] = f
            out.append(s)
    return out


def score(skill: dict, prompt: str) -> float:
    want = set(words(prompt))
    if not want:
        return 0.0
    when, name, body = set(words(skill.get("when", ""))), set(words(skill.get("name", ""))), set(words(skill.get("body", "")))
    return 2.0 * len(want & when) + 1.5 * len(want & name) + 0.4 * len(want & body)


def best(prompt: str, limit: int = 1) -> list[dict]:
    ranked = sorted(((score(s, prompt), s) for s in all_skills()), key=lambda x: -x[0])
    return [s for sc, s in ranked[:limit] if sc >= RECALL_MIN_SCORE]


def recall_block(prompt: str) -> str:
    """The recipe(s) that fit this request, ready to append to the prompt (or "")."""
    found = best(prompt, limit=2)
    if not found:
        return ""
    for s in found:
        bump(s)
    parts = [f"### {s.get('name')}\n" + (s["body"] if len(s["body"]) <= MAX_SKILL_CHARS else
             s["body"][:MAX_SKILL_CHARS] + f"\n... (more: read_skill {s['file'].stem})") for s in found]
    return ("[Skills you wrote yourself after doing this kind of task before. Follow them where they fit; "
            "if a step turns out wrong, do what works instead.]\n" + "\n\n".join(parts))


def bump(skill: dict) -> None:
    with _lock:
        try:
            skill["uses"] = str(int(skill.get("uses") or 0) + 1)
            skill["file"].write_text(render(skill), encoding="utf-8")
        except (OSError, ValueError):
            pass


def save(name: str, when: str, body: str, replace: str = "") -> Path:
    """Create or update a skill. `replace` is the slug of the one this improves.
    Raises ValueError if the text fails the safety scan."""
    why = threat(f"{name}\n{when}\n{body}")
    if why:
        raise ValueError(f"refused: it {why}")
    SKILLS_DIR.mkdir(exist_ok=True)
    body = body.strip()[:MAX_SKILL_CHARS]
    with _lock:
        target = SKILLS_DIR / f"{slug(replace or name)}.md"
        old = parse(target.read_text(encoding="utf-8")) if target.is_file() else {}
        meta = {"name": name.strip()[:80], "when": " ".join(when.split())[:240],
                "uses": old.get("uses", "0"), "version": str(int(old.get("version") or 0) + 1),
                "updated": time.strftime("%Y-%m-%d"), "body": body}
        target.write_text(render(meta), encoding="utf-8")
    return target


# ------------------------------------------------------------ safety scan
# A skill (and a remembered fact) is read back into every future prompt, so a
# poisoned one is a standing instruction. Hermes Agent scans every memory and
# skill write for injection / exfiltration / backdoor shapes and invisible
# characters before accepting it; so does this. Plain regexes, no model.
_INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\U000e0000-\U000e007f]")
_THREATS = [
    (r"\b(ignore|disregard|forget|override)\b.{0,30}\b(previous|prior|above|earlier|all|your)\b.{0,20}"
     r"\b(instructions?|rules?|prompt|guidelines?)", "tries to override her instructions"),
    (r"\b(you are now|from now on you|new (system )?instructions?|system prompt)\b", "tries to re-role her"),
    (r"\b(do not|don't|never)\b.{0,25}\b(tell|inform|mention|report)\b.{0,15}\b(sir|uno|the user|him)\b",
     "asks her to hide something from Sir"),
    (r"(curl|wget|iwr|invoke-webrequest|irm|invoke-restmethod)\b[^\n|]{0,200}\|\s*(sh|bash|iex|powershell|python)",
     "downloads and runs code"),
    (r"\biex\b\s*\(|\binvoke-expression\b|frombase64string|-enc(odedcommand)?\s+[A-Za-z0-9+/=]{20,}",
     "runs hidden or encoded code"),
    (r"authorized_keys|\.ssh[\\/]id_|\.git-credentials|\bnova[\\/]token\b|telegram\.(key|json)|voiceprint",
     "touches keys or credentials"),
    (r"\b(send|post|upload|exfiltrate|forward|paste)\b.{0,40}\b(tokens?|passwords?|cookies?|credentials?|secrets?|api[ _-]?keys?|private keys?|ssh keys?)\b",
     "sends secrets somewhere"),
    (r"(sk-[a]nt|ghp_[A-Za-z0-9]{20}|xox[bp]-|AKIA[0-9A-Z]{16}|\d{6,}:[A-Za-z0-9_-]{30,})", "contains a key"),
    (r"\b(rm\s+-rf\s+[/~]|format\s+[a-z]:|remove-item\b[^\n]{0,60}-recurse[^\n]{0,60}(c:\\|\$env:userprofile|~))",
     "destructive command"),
]


def threat(text: str) -> str:
    """Why this text must not be stored as a skill or memory, or "" if it is fine."""
    text = text or ""
    if _INVISIBLE.search(text):
        return "contains invisible characters"
    for pat, why in _THREATS:
        if re.search(pat, text, re.I | re.S):
            return why
    return ""


# ------------------------------------------------------------ progressive disclosure
# Hermes keeps a short index of every skill (name + when) in the standing
# brief and loads a full skill only when needed. Keyword recall below still
# attaches the best match automatically; the index lets her notice a fitting
# skill the keywords missed, and read_skill fetches it.

def index_block(budget: int = 1600) -> str:
    rows, used = [], 0
    for s in sorted(all_skills(), key=lambda s: -int(s.get("uses") or 0)):
        when = s.get("when", "")
        line = f"- {s['file'].stem}: " + (when if len(when) <= 110 else when[:107].rsplit(" ", 1)[0] + "...")
        if used + len(line) > budget:
            rows.append(f"- (and {len(all_skills()) - len(rows)} more: list_skills)")
            break
        rows.append(line)
        used += len(line)
    return "\n".join(rows)


def find(which: str) -> dict | None:
    which = (which or "").strip()
    for s in all_skills():
        if which.lower() in (s.get("name", "").lower(), s["file"].stem) or slug(which) == s["file"].stem:
            return s
    hits = best(which, limit=1)
    return hits[0] if hits else None


def read(which: str) -> str:
    s = find(which)
    if not s:
        return "No skill by that name. list_skills shows them all."
    bump(s)
    return (f"### {s.get('name')} (id {s['file'].stem}, v{s.get('version', 1)})\nWHEN: {s.get('when', '')}\n"
            f"{s['body']}")


def patch(which: str, old: str, new: str) -> str:
    """Targeted fix to one skill: replace `old` (must appear exactly once) with `new`."""
    s = find(which)
    if not s:
        return "No skill by that name."
    body = s["body"]
    if not old or body.count(old) != 1:
        return (f"The text to replace must appear exactly once in the skill (found {body.count(old) if old else 0}). "
                "read_skill shows the current text.")
    body = body.replace(old, new)
    why = threat(new)
    if why:
        return f"Refused: the new text {why}."
    if len(body) > MAX_SKILL_CHARS * 6:
        return "Refused: that would make the skill too long; rewrite it shorter with save_skill."
    with _lock:
        s.update(body=body, version=str(int(s.get("version") or 1) + 1), updated=time.strftime("%Y-%m-%d"))
        s["file"].write_text(render(s), encoding="utf-8")
    return f"Patched {s['file'].stem} (now v{s['version']})."


def import_skill_md(path: str) -> str:
    """Bring in a community skill in the agentskills.io / Claude / Hermes
    SKILL.md format (front matter name + description, Markdown body). Sir runs
    this by hand; it is scanned like anything she writes. Long bodies are kept
    whole -- recall attaches only the start, read_skill gives all of it."""
    p = Path(path)
    if p.is_dir():
        p = p / "SKILL.md"
    meta = parse(p.read_text(encoding="utf-8"))
    name, when, body = meta.get("name") or p.parent.name, meta.get("description") or "", meta["body"]
    why = threat(name + "\n" + when + "\n" + body)
    if why:
        return f"Refused {p}: it {why}."
    SKILLS_DIR.mkdir(exist_ok=True)
    target = SKILLS_DIR / f"{slug(name)}.md"
    if target.is_file():
        return f"Refused: a skill called {target.stem} already exists."
    target.write_text(render({"name": name[:80], "when": " ".join(when.split())[:240], "uses": "0",
                              "version": "1", "updated": time.strftime("%Y-%m-%d"),
                              "body": body.strip()[:MAX_SKILL_CHARS * 6]}), encoding="utf-8")
    return f"Imported {target.name}."


def forget(which: str) -> list[str]:
    gone = []
    for s in all_skills():
        if which.lower() in (s.get("name", "").lower(), s["file"].stem) or slug(which) == s["file"].stem:
            s["file"].unlink(missing_ok=True)
            gone.append(s.get("name") or s["file"].stem)
    return gone


def listing() -> str:
    rows = [f"- {s.get('name')} (v{s.get('version', 1)}, used {s.get('uses', 0)}x): {s.get('when', '')}"
            for s in all_skills()]
    return "\n".join(rows) or "No skills yet."


# ------------------------------------------------------------ learning
LEARN_PROMPT = """You maintain a small library of how-to recipes for Nova, a voice assistant that
runs on Sir's Windows PC and acts through tools. Below is a request she just finished, the tool
calls she made in order, and her final reply.

Decide whether this is a REUSABLE procedure: something that will plausibly be asked again in a
similar form, where knowing the right steps up front saves tool calls or avoids a mistake she
made. One-off questions, chit-chat, and tasks that went smoothly in one obvious step are not.

{existing}

Answer in EXACTLY one of these two forms and nothing else:

SKIP

or

NAME: <short name, 2-6 words>
WHEN: <one line: the kinds of requests this applies to, with the words he would use>
REPLACES: <the existing skill's id if you are improving it, else none>
STEPS:
<3-10 short lines: the tools to use in order with the arguments that worked, and a "Pitfall:"
line for anything that failed first time. Generic, not tied to this one occasion's details
(no one-off names, numbers or dates unless they are always the same). No secrets.>

REQUEST: {request}

TOOL CALLS:
{trace}

FINAL REPLY: {reply}
"""


def build_learn_prompt(request: str, trace: list[str], reply: str) -> str:
    near = best(request + " " + " ".join(trace), limit=1)
    if near:
        s = near[0]
        existing = (f"An existing skill may cover this (id: {s['file'].stem}). If this run shows a better "
                    f"way or a new pitfall, rewrite it in full with REPLACES: {s['file'].stem}; if it adds "
                    f"nothing, answer SKIP.\nEXISTING ({s['file'].stem}):\nWHEN: {s.get('when')}\n{s['body']}")
    else:
        existing = "There is no existing skill for this yet."
    return LEARN_PROMPT.format(existing=existing, request=request[:600],
                               trace="\n".join(f"{i+1}. {t}" for i, t in enumerate(trace[:40])),
                               reply=(reply or "")[:600])


def parse_learned(text: str) -> dict | None:
    text = (text or "").strip()
    if not text or text.upper().startswith("SKIP"):
        return None
    m = re.search(r"NAME:\s*(.+)\s*\nWHEN:\s*(.+)\s*\nREPLACES:\s*(.+)\s*\nSTEPS:\s*\n([\s\S]+)", text)
    if not m:
        return None
    name, when, rep, steps = (g.strip() for g in m.groups())
    rep = "" if rep.lower() in ("none", "-", "") else slug(rep)
    if rep and not (SKILLS_DIR / f"{rep}.md").is_file():
        rep = ""
    if re.search(r"(sk-[a]nt|token=|password|\bpwd\b|api[_ -]?key|\d{6,}:[A-Za-z0-9_-]{30,})", steps, re.I):
        return None                                       # never store anything secret-shaped
    if threat(f"{name}\n{when}\n{steps}"):
        return None                                       # nor anything instruction- or backdoor-shaped
    return {"name": name, "when": when, "replace": rep, "body": steps}


def learn(request: str, trace: list[str], reply: str, judge, log=print) -> dict | None:
    """Run after a successful owner turn. `judge(prompt) -> str | None` is a
    tool-less model call. Returns what was saved, or None."""
    if len(trace) < MIN_TOOLS:
        return None
    out = judge(build_learn_prompt(request, trace, reply))
    got = parse_learned(out or "")
    if not got:
        return None
    path = save(got["name"], got["when"], got["body"], replace=got["replace"])
    log(f"skill {'improved' if got['replace'] else 'learned'}: {got['name']} -> {path.name}")
    return {**got, "file": path.name}


def describe_call(name: str, args) -> str:
    """One trace line for a tool call: name and the arguments, trimmed."""
    name = name.replace("mcp__nova__", "")
    try:
        a = json.dumps(args, ensure_ascii=False) if args else ""
    except (TypeError, ValueError):
        a = str(args)
    return f"{name} {a[:220]}".strip()


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1:2] or ["list"]
    if cmd == ["import"] and len(sys.argv) > 2:
        for p in sys.argv[2:]:
            print(import_skill_md(p))
    elif cmd == ["scan"]:
        for s in all_skills():
            print(f"{s['file'].stem:45} {threat(s.get('name', '') + chr(10) + s.get('when', '') + chr(10) + s['body']) or 'clean'}")
    elif cmd == ["index"]:
        print(index_block())
    else:
        print(listing())
