"""When is "the 16th of November at 9"? A small, dependable date/time reader
for reminders and meetings. No model, no internet, no paid anything.

parse(text, now) -> dict or None:
    {"at": epoch, "daily": "HH:MM" or "", "weekly": 0-6 or None,
     "dated": bool (a day was named), "timed": bool (a time was named)}

Understands, in any order and mixed with other words:
  dates   2026-11-16 · 16 November · November 16th · 16th of Nov 2026 · the 16th
          (this month, or next if it has passed) · today · tonight · tomorrow ·
          day after tomorrow · in 3 days / in 2 weeks · next week · Monday /
          this Friday / next Monday · Filipino months (Nobyembre) and
          bukas / mamaya / ngayon
  times   9 · 9am · 9:30 pm · 21:30 · noon · midnight · morning (8) ·
          afternoon (14) · evening (18) · tonight / night (20)
  relative "in 20 minutes", "in an hour and a half" (from now)
  repeats every day / daily -> daily, every Monday -> weekly

A date with no time gets DEFAULT_TIME (9:00). A time with no date is the next
time that clock time comes round. describe() gives the exact day back in words,
so whoever set it can hear what was actually stored.
"""
from __future__ import annotations

import re
import time

DEFAULT_TIME = (9, 0)
MONTHS = {}
for i, names in enumerate([
        ("january", "jan", "enero"), ("february", "feb", "pebrero"), ("march", "mar", "marso"),
        ("april", "apr", "abril"), ("may", "mayo"), ("june", "jun", "hunyo"), ("july", "jul", "hulyo"),
        ("august", "aug", "agosto"), ("september", "sep", "sept", "setyembre"),
        ("october", "oct", "oktubre"), ("november", "nov", "nobyembre"),
        ("december", "dec", "disyembre")]):
    for n in names:
        MONTHS[n] = i + 1
WEEKDAYS = {}
for i, names in enumerate([("monday", "mon", "lunes"), ("tuesday", "tue", "tues", "martes"),
                           ("wednesday", "wed", "miyerkules"), ("thursday", "thu", "thur", "thurs", "huwebes"),
                           ("friday", "fri", "biyernes"), ("saturday", "sat", "sabado"),
                           ("sunday", "sun", "linggo")]):
    for n in names:
        WEEKDAYS[n] = i
MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))
DAY_RE = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
ORD = r"(\d{1,2})(?:st|nd|rd|th)?"
WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
         "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
         "thirty": 30, "forty": 40, "forty-five": 45, "half an": 0.5, "a couple of": 2, "a few": 3}
NUM = r"(\d+(?:\.\d+)?|half an|a couple of|a few|forty-five|" + "|".join(
    k for k in WORDS if " " not in k and k != "forty-five") + r")"


def _num(s: str) -> float:
    s = s.strip()
    return WORDS[s] if s in WORDS else float(s)


def _clean(text: str) -> str:
    t = " " + str(text or "").lower().replace("’", "'") + " "
    t = re.sub(r"[,;!?]", " ", t)
    t = re.sub(r"(\d)\s*\.\s*(\d{2})\s*(am|pm|a\.m\.|p\.m\.)", r"\1:\2 \3", t)     # 9.30 pm
    t = t.replace("a.m.", "am").replace("p.m.", "pm")
    return " ".join(t.split())


def _mk(y: int, mo: int, d: int, h: int = 0, mi: int = 0) -> float:
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


def _valid(y: int, mo: int, d: int) -> bool:
    try:
        lt = time.localtime(_mk(y, mo, d, 12))
        return (lt.tm_year, lt.tm_mon, lt.tm_mday) == (y, mo, d)
    except (OverflowError, ValueError):
        return False


def _relative(t: str, now: float) -> float | None:
    """'in 20 minutes', 'in an hour and a half', 'in 2 hours 15 minutes' -> epoch."""
    m = re.search(r"\bin\s+" + NUM + r"\s*(second|sec|minute|min|hour|hr)s?\b"
                  r"(?:\s*(?:and\s+)?(?:" + NUM + r"\s*(minute|min|second|sec)s?|(a half)))?", t)
    if not m:
        return None
    total = _num(m.group(1)) * {"s": 1, "m": 60, "h": 3600}[m.group(2)[0]]
    if m.group(3):
        total += _num(m.group(3)) * {"s": 1, "m": 60}[m.group(4)[0]]
    elif m.group(5):
        total += 0.5 * {"s": 1, "m": 60, "h": 3600}[m.group(2)[0]]
    return now + total


def _time_of_day(t: str) -> tuple[int, int] | None:
    m = re.search(r"\b(\d{1,2}):(\d{2})\s*(am|pm)?\b", t)
    if not m:
        m = re.search(r"\b(?:at|by|around|@)\s+(\d{1,2})()\s*(am|pm)?\b(?!\s*(?:" + MONTH_RE + r"|st|nd|rd|th|days?|weeks?|minutes?|mins?|hours?|hrs?)\b)", t)
    if not m:
        m = re.search(r"\b(\d{1,2})()\s*(am|pm)\b", t)
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        if h > 23 or mi > 59:
            return None
        # "at 7 tonight" / "at 5 in the afternoon"
        if not ap and h < 12 and re.search(r"\b(?:tonight|evening|afternoon|night|pm|gabi|hapon)\b", t):
            h += 12
        return h, mi
    for word, hm in (("noon", (12, 0)), ("midday", (12, 0)), ("tanghali", (12, 0)), ("midnight", (0, 0)),
                     ("morning", (8, 0)), ("umaga", (8, 0)), ("afternoon", (14, 0)), ("hapon", (14, 0)),
                     ("evening", (18, 0)), ("tonight", (20, 0)), ("night", (20, 0)), ("gabi", (20, 0))):
        if re.search(r"\b" + word + r"\b", t):
            return hm
    return None


def _date(t: str, now: float) -> tuple[int, int, int] | None:
    lt = time.localtime(now)
    y0, m0, d0 = lt.tm_year, lt.tm_mon, lt.tm_mday

    def ahead(days: int) -> tuple[int, int, int]:
        x = time.localtime(_mk(y0, m0, d0, 12) + days * 86400)
        return x.tm_year, x.tm_mon, x.tm_mday

    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    year = re.search(r"\b(20\d{2})\b", t)
    m = (re.search(r"\b" + ORD + r"\s+(?:of\s+|ng\s+)?(" + MONTH_RE + r")\.?\b", t)
         or None)
    day = mon = None
    if m:
        day, mon = int(m.group(1)), MONTHS[m.group(2)]
    else:
        m = re.search(r"\b(" + MONTH_RE + r")\.?\s+(?:the\s+)?" + ORD + r"\b(?!\s*(?::|am|pm|minutes?|hours?))", t)
        if m:
            mon, day = MONTHS[m.group(1)], int(m.group(2))
    if day and mon:
        y = int(year.group(1)) if year else y0
        if not year and (mon, day) < (m0, d0):
            y += 1                                    # "January 5" said in November: next year
        return (y, mon, day) if _valid(y, mon, day) else None
    if re.search(r"\b(?:day after tomorrow|the day after tomorrow|samakalawa)\b", t):
        return ahead(2)
    if re.search(r"\b(?:tomorrow|tmr|tmrw|tomorow|bukas)\b", t):
        return ahead(1)
    if re.search(r"\b(?:today|tonight|this (?:morning|afternoon|evening)|mamaya|ngayon)\b", t):
        return ahead(0)
    m = re.search(r"\bin\s+" + NUM + r"\s*(day|week)s?\b", t)
    if m:
        return ahead(int(round(_num(m.group(1)) * (7 if m.group(2) == "week" else 1))))
    if re.search(r"\b(?:next week|in a week)\b", t):
        return ahead(7)
    m = re.search(r"\b(next|this|coming|on)?\s*(" + DAY_RE + r")\b", t)
    if m:
        want = WEEKDAYS[m.group(2)]
        diff = (want - lt.tm_wday) % 7
        if m.group(1) == "next" and diff == 0:
            diff = 7
        return ahead(diff)                            # today counts if the time is still ahead
    m = re.search(r"\b(?:on\s+)?the\s+" + r"(\d{1,2})(?:st|nd|rd|th)\b", t) or \
        re.search(r"\bon\s+(\d{1,2})(?:st|nd|rd|th)\b", t)
    if m:
        day = int(m.group(1))
        y, mo = y0, m0
        if day < d0:
            mo += 1
            if mo > 12:
                y, mo = y + 1, 1
        return (y, mo, day) if _valid(y, mo, day) else None
    return None


def parse(text: str, now: float | None = None) -> dict | None:
    now = time.time() if now is None else now
    t = _clean(text)
    if not t:
        return None
    rel = _relative(t, now)
    if rel is not None:
        return {"at": rel, "daily": "", "weekly": None, "dated": False, "timed": True}
    daily = bool(re.search(r"\b(?:every ?day|daily|each day|every morning|every night|every evening|araw-araw)\b", t))
    wk = re.search(r"\bevery\s+(" + DAY_RE + r")\b", t)
    weekly = WEEKDAYS[wk.group(1)] if wk else None
    date = _date(t, now)
    hm = _time_of_day(t)
    if date is None and hm is None:
        return None
    if date is None:                                  # a time alone: the next time it comes round
        lt = time.localtime(now)
        at = _mk(lt.tm_year, lt.tm_mon, lt.tm_mday, *hm)
        if at <= now:
            at = _mk(*time.localtime(at + 86400)[:3], *hm)
        dated = False
    else:
        h, mi = hm or DEFAULT_TIME
        at = _mk(*date, h, mi)
        dated = True
        # A weekday named with a time already gone today means next week.
        if at <= now and weekly is None and re.search(r"\b(?:" + DAY_RE + r")\b", t) \
                and not re.search(r"\b(?:today|tonight)\b", t):
            at += 7 * 86400
    hhmm = time.strftime("%H:%M", time.localtime(at))
    return {"at": at, "daily": hhmm if daily else "", "weekly": weekly, "dated": dated, "timed": hm is not None}


def from_fields(date: str = "", clock: str = "", now: float | None = None) -> dict | None:
    """The exact form: date "YYYY-MM-DD" (or words) and clock "HH:MM" (or "9am")."""
    now = time.time() if now is None else now
    return parse(f"{date} {('at ' + clock) if clock and ':' not in clock and not re.search('am|pm', clock.lower()) else clock}", now)


def describe(at: float, now: float | None = None) -> str:
    """'Monday 16 November 2026 at 09:00 (in 2 days, 16 hours)'."""
    now = time.time() if now is None else now
    lt = time.localtime(at)
    day = time.strftime("%A %d %B %Y", lt).replace(" 0", " ")
    secs = int(at - now)
    if secs < 0:
        away = "in the past"
    else:
        d, rem = divmod(secs, 86400)
        h, rem = divmod(rem, 3600)
        mi = rem // 60
        parts = [f"{d} day{'s' * (d != 1)}"] if d else []
        if h:
            parts.append(f"{h} hour{'s' * (h != 1)}")
        if not d and mi:
            parts.append(f"{mi} minute{'s' * (mi != 1)}")
        away = "in " + (", ".join(parts) or "under a minute")
    return f"{day} at {time.strftime('%H:%M', lt)} ({away})"
