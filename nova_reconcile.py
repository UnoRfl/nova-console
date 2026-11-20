"""Cross-check: when Sir tells Nova something about his own life, hand her what she
already holds on that subject, so she can see what his news makes out of date.

Uno, 2026-11-20: he told her his finals were over and he was enrolling for the next
term. She set the enrolment reminders and kept ringing him for the old classes,
because nothing put the two side by side. His ask: "check my previous information
and update it accordingly based on what I say ... and if that seems inaccurate,
update it or at least ask me if you're not sure."

The recalled memories already travel with every request (nova_memory.recall_block).
This adds the two stores recall does not search -- the timetable and the reminders
-- and, whenever he is stating something about himself, the instruction to
reconcile before answering. No model call; a few file reads.
"""
from __future__ import annotations

import re
import time

# He is telling her about himself or a change of plan (not asking her something).
LIFE = re.compile(
    r"\b(?:i(?:'m| am| was| will| won'?t| wont| have| had| just| already| no longer| don'?t| didn'?t| got| "
    r"finished| started| quit| moved| changed| stopped| switched| enrolled| passed| failed| graduated| "
    r"dropped| left| joined| need to| have to| gotta)|"
    r"my \w+(?: \w+)? (?:is|are|was|were|has|have|got|ended|ends|starts|started|changed|moved|finished)|"
    r"no (?:more )?(?:class(?:es)?|school|work|exams?)|not anymore|any ?more|from now on|"
    r"cancel(?:l?ed)?|postponed|moved to|rescheduled|called off|is over|are over)\b", re.I)

SCHOOL = re.compile(
    r"\b(?:school|class(?:es)?|semester|sem|term|finals?|exams?|midterms?|enrol\w*|enroll\w*|"
    r"timetable|schedule|subjects?|vacation|sem break|break|holidays?|graduat\w*|professor|prof|"
    r"first year|second year|1st year|2nd year|university|college|campus)\b", re.I)

STOP = set("""about after again also and any are back been before being but can could did does doing
don't done down each even ever from going gonna have having here into just know like make many more
most much need next only other over same should some still such than that their them then there these
they thing think this those through today tomorrow very want week what when where which while will
with won't would your yours you sir nova please thank thanks okay alright really actually""".split())


def is_life_update(prompt: str) -> bool:
    p = prompt.strip()
    if not p or len(p) > 1500:
        return False
    return bool(LIFE.search(p))


def _words(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in STOP}


def _when(at: float) -> str:
    return time.strftime("%a %d %b %H:%M", time.localtime(at)).replace(" 0", " ")


def block(prompt: str, reminders: list[dict] | None = None, now: float | None = None,
          recalled: bool = False) -> str:
    """The cross-check note for this request, or "" when there is nothing to compare.
    recalled = memories already travel with the request (then the note is worth it
    even with no timetable/reminder lines)."""
    if not is_life_update(prompt):
        return ""
    now = time.time() if now is None else now
    school = bool(SCHOOL.search(prompt))
    lines: list[str] = []

    if school:
        try:
            import nova_schedule as ns
            data = ns.load()
            status = ns.term_status(now)
            if status:
                lines.append("Timetable: " + status)
            elif data["classes"]:
                span = (f"from {data['from']} " if data.get("from") else "") + \
                       (f"until {data['until']}" if data.get("until") else "with NO end date set")
                lines.append(f"Timetable IN FORCE {span}; class nudges and calls fire from it: " + ns.outline())
                skips = [d for d in data.get("skip_dates", []) if d >= time.strftime("%Y-%m-%d", time.localtime(now))]
                if skips:
                    lines.append("Days already off: " + ", ".join(skips[:10]))
            else:
                lines.append("Timetable: none saved.")
        except Exception:                             # noqa: BLE001 -- a broken file must not stop a request
            pass

    want = _words(prompt)
    if school:
        want |= {"class", "schoo", "enrol", "term", "semes", "exams", "final"}
    hits = []
    for r in reminders or []:
        live = r.get("daily") or r.get("weekly") or r.get("at", 0) >= now - 3600
        if live and _words(r.get("title") or r.get("text") or "") & want:
            hits.append(r)
    for r in hits[:8]:
        rep = " (daily)" if r.get("daily") else " (weekly)" if r.get("weekly") else ""
        lines.append(f"Reminder {r.get('id', '')} {_when(r['at'])}{rep}: {(r.get('title') or r['text'])[:160]}")

    head = ("[CROSS-CHECK -- he is telling you something about his own life or plans. Before you answer, "
            "put it beside what you already hold: the lines below and any recalled memories. Reason out what "
            "his news implies (e.g. finals over and enrolling next term = the old timetable is finished). "
            "If something you hold is now wrong or stale, fix it in this same turn with your tools "
            "(end_term / classes_off / new_term, cancel_reminder, forget + remember) and tell him in one short "
            "clause what you changed. If you genuinely cannot tell whether it is out of date, ask him one short, "
            "specific question instead of guessing. If nothing conflicts, say nothing about this.]")
    if not lines:
        return head if recalled else ""
    return head + "\n" + "\n".join("- " + l for l in lines)
