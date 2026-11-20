"""Sir's weekly class timetable, and the reminders that come from it.

schedule.json holds the classes; nothing else needs editing:

    {"lead_minutes": 10, "late_nudge": true, "skip_dates": ["2026-12-08"],
     "until": "", "classes": [
        {"name": "Introduction to Computing", "day": "mon", "start": "07:00",
         "end": "08:30", "where": "Lab"}, ...]}

Events.check_classes() calls due() every ten seconds. Two kinds of nudge:
  "soon"  lead_minutes before a class starts
  "late"  two minutes after it starts, only if he is still at this PC
Each fires once per class per day, and only inside a short window, so a
restart cannot replay a morning's worth of reminders.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

ROOT = Path.home() / ".nova"
SCHEDULE_FILE = ROOT / "schedule.json"
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
WINDOW = 180          # seconds a nudge stays fireable after its moment
LATE_AFTER = 120      # "late" fires this long after the start

_fired: set[str] = set()
_lock = threading.Lock()


def load() -> dict:
    try:
        data = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {"classes": []}
    data.setdefault("classes", [])
    data.setdefault("lead_minutes", 10)
    data.setdefault("late_nudge", True)
    data.setdefault("skip_dates", [])
    data.setdefault("until", "")
    data.setdefault("from", "")
    data.setdefault("term_note", "")
    return data


def save(data: dict) -> None:
    SCHEDULE_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def spoken_time(hhmm: str) -> str:
    h, m = divmod(_minutes(hhmm), 60)
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}{'' if m == 0 else f':{m:02d}'} {suffix}"


def active_on(data: dict, lt: time.struct_time) -> bool:
    date = time.strftime("%Y-%m-%d", lt)
    if date in data.get("skip_dates", []):
        return False
    try:                                          # holidays and posted suspensions
        import nova_school
        if nova_school.no_school(date):
            return False
    except Exception:                             # noqa: BLE001 -- never let this stop a reminder
        pass
    return in_term(data, date)


# ------------------------------------------------------------------ the term
# Uno, 2026-11-20: he told her his finals were over and he was enrolling for the
# next term, and she still rang him for Friday's classes. The timetable now
# knows its term: "from" (first day, may be empty) to "until" (last day). Out
# of term nothing fires, and every reader says "between terms" instead of
# reciting the old classes.

def in_term(data: dict, date: str) -> bool:
    """Inside the term this timetable belongs to: from <= date <= until (either may be empty)."""
    first = data.get("from") or ""
    last = data.get("until") or ""
    return not ((first and date < first) or (last and date > last))


def term_status(now: float | None = None) -> str:
    """'' while the timetable is in force today; otherwise one plain line saying why not."""
    now = time.time() if now is None else now
    data = load()
    date = time.strftime("%Y-%m-%d", time.localtime(now))
    note = data.get("term_note") or ""
    tail = f" ({note})" if note else ""
    if not data["classes"]:
        return "No timetable is saved."
    if data.get("until") and date > data["until"]:
        return (f"Between terms: the last timetable ended on {data['until']}{tail}. Its classes are NOT in "
                "force: do not list them as his classes or remind him of them. Once he has enrolled, ask him "
                "for the new timetable and save it with new_term.")
    if data.get("from") and date < data["from"]:
        return f"The new term starts on {data['from']}{tail}; no classes before then."
    return ""


def _day(s: str) -> str:
    s = (s or "").strip()
    time.strptime(s, "%Y-%m-%d")          # ValueError when it is not a real date
    return s


def end_term(last_day: str, note: str = "") -> str:
    """The term is over: classes stop after last_day (YYYY-MM-DD)."""
    data = load()
    data["until"] = _day(last_day)
    if note:
        data["term_note"] = note.strip()[:300]
    save(data)
    return f"Timetable ended: no class reminders after {data['until']}."


def classes_off(first_day: str, last_day: str = "", reason: str = "") -> str:
    """No classes from first_day to last_day (inclusive), e.g. exam week or a trip; term goes on."""
    a = _day(first_day)
    b = _day(last_day) if last_day else a
    if b < a:
        a, b = b, a
    t0 = time.mktime(time.strptime(a, "%Y-%m-%d")) + 43200
    days = []
    while len(days) < 92:
        d = time.strftime("%Y-%m-%d", time.localtime(t0 + len(days) * 86400))
        if d > b:
            break
        days.append(d)
    data = load()
    data["skip_dates"] = sorted(set(data["skip_dates"]) | set(days))
    if reason:
        data.setdefault("skip_notes", {})[f"{a}..{b}"] = reason.strip()[:200]
    save(data)
    return f"No class reminders from {a} to {b} ({len(days)} days)."


def new_term(classes: list, first_day: str = "", last_day: str = "", note: str = "") -> str:
    """Replace the timetable with a new term's classes. The old one is kept in past_terms."""
    clean = []
    for c in classes or []:
        day = str(c.get("day", "")).strip().lower()[:3]
        if day not in DAYS:
            raise ValueError(f"bad day {c.get('day')!r}: use mon..sun")
        for k in ("start", "end"):
            _minutes(str(c.get(k, "")))
        clean.append({"name": str(c.get("name") or "class").strip(), "day": day,
                      "start": c["start"], "end": c["end"], "where": str(c.get("where") or "").strip()})
    if not clean:
        raise ValueError("no classes given")
    data = load()
    if data["classes"]:
        data.setdefault("past_terms", []).append({"classes": data["classes"], "from": data.get("from", ""),
                                                  "until": data.get("until", ""), "note": data.get("term_note", "")})
    today = time.strftime("%Y-%m-%d")
    data.update({"classes": clean, "from": _day(first_day) if first_day else "",
                 "until": _day(last_day) if last_day else "", "term_note": note.strip()[:300],
                 "skip_dates": [d for d in data["skip_dates"] if d >= today]})
    save(data)
    return (f"New timetable saved: {len(clean)} classes"
            + (f", from {data['from']}" if data["from"] else "") + (f" to {data['until']}" if data["until"] else "") + ".")


def outline() -> str:
    """One short line per weekday, class names only (for the cross-check note)."""
    data = load()
    rows = []
    for i, day in enumerate(DAY_NAMES):
        cs = classes_on(data, i)
        if cs:
            rows.append(day[:3] + ": " + ", ".join(c["name"] for c in cs))
    return "; ".join(rows)


def classes_on(data: dict, weekday: int) -> list[dict]:
    day = DAYS[weekday]
    return sorted((c for c in data["classes"] if c.get("day", "").lower()[:3] == day),
                  key=lambda c: _minutes(c["start"]))


def describe(c: dict) -> str:
    where = f", {c['where']}" if c.get("where") else ""
    return f"{c['name']} {spoken_time(c['start'])} to {spoken_time(c['end'])}{where}"


def today(now: float | None = None) -> list[dict]:
    lt = time.localtime(time.time() if now is None else now)
    data = load()
    return classes_on(data, lt.tm_wday) if active_on(data, lt) else []


def next_class(now: float | None = None) -> tuple[dict, float] | None:
    """The next class that has not started yet, within a week, and its start epoch."""
    now = time.time() if now is None else now
    data = load()
    for ahead in range(8):
        lt = time.localtime(now + ahead * 86400)
        if not active_on(data, lt):
            continue
        midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
        for c in classes_on(data, lt.tm_wday):
            h, m = divmod(_minutes(c["start"]), 60)
            start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, 0, 0, -1))
            if start > now and start >= midnight:
                return c, start
    return None


def week_text() -> str:
    data = load()
    if not data["classes"]:
        return "No timetable is saved."
    lines = []
    status = term_status()
    if status:
        lines.append(status)
        lines.append("The old timetable, for reference only:")
    for i, day in enumerate(DAY_NAMES):
        cs = classes_on(data, i)
        if cs:
            lines.append(f"{day}: " + "; ".join(describe(c) for c in cs))
    return "\n".join(lines)


def due(now: float | None = None, here: bool = True) -> list[tuple[str, str]]:
    """Nudges whose moment has come: [(kind, spoken text)]. here = he is at this PC."""
    now = time.time() if now is None else now
    lt = time.localtime(now)
    data = load()
    if not active_on(data, lt):
        return []
    date = time.strftime("%Y-%m-%d", lt)
    lead = int(data.get("lead_minutes") or 10) * 60
    out = []
    for c in classes_on(data, lt.tm_wday):
        h, m = divmod(_minutes(c["start"]), 60)
        start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, 0, 0, -1))
        where = f", {c['where']}" if c.get("where") else ""
        moments = [("soon", start - lead,
                    f"Sir, {c['name']} starts at {spoken_time(c['start'])}{where}. "
                    f"That is in {lead // 60} minutes.")]
        if data.get("late_nudge"):
            moments.append(("late", start + LATE_AFTER,
                            f"Sir, {c['name']} started at {spoken_time(c['start'])}. "
                            "You appear to still be at your desk."))
        for kind, at, text in moments:
            key = f"{date}|{c['name']}|{c['start']}|{kind}"
            if not (at <= now < at + WINDOW):
                continue
            if kind == "late" and not here:
                continue
            with _lock:
                if key in _fired:
                    continue
                _fired.add(key)
            out.append((kind, text))
    return out
