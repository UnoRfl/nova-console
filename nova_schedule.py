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
    until = data.get("until") or ""
    return not (until and date > until)


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
