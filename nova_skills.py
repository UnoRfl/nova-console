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
    parts = [f"### {s.get('name')}\n{s['body']}" for s in found]
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
    """Create or update a skill. `replace` is the slug of the one this improves."""
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
