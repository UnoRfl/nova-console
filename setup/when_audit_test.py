#!/usr/bin/env python3
"""
when_audit_test.py -- the everyday phrases the 2026-11-14 audit found nova_when
reading wrongly. Fixed clock: Saturday 14 November 2026, 14:00.

    python setup/when_audit_test.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import nova_when as w                                               # noqa: E402

NOW = time.mktime((2026, 11, 14, 14, 0, 0, 0, 0, -1))
MON_10 = time.mktime((2026, 11, 16, 10, 0, 0, 0, 0, -1))           # a Monday, 10:00
LATE = time.mktime((2026, 11, 14, 23, 0, 0, 0, 0, -1))

CASES = [
    # phrase, clock, expected "YYYY-MM-DD HH:MM" (None = must not be read as a reminder time)
    ("remind me at 5 to call mom", NOW, "2026-11-14 17:00"),
    ("at 9 check the oven", NOW, "2026-11-14 21:00"),
    ("at 3 take the laundry in", NOW, "2026-11-14 15:00"),
    ("at 5pm call mom", NOW, "2026-11-14 17:00"),
    ("at 5am wake me", NOW, "2026-11-15 05:00"),
    ("tonight at 12", NOW, "2026-11-15 00:00"),
    ("midnight tonight", NOW, "2026-11-15 00:00"),
    ("tonight at 1", LATE, "2026-11-15 01:00"),
    ("tonight at 8", NOW, "2026-11-14 20:00"),
    ("at 7 tonight", NOW, "2026-11-14 19:00"),
    ("tomorrow at half past 3", NOW, "2026-11-15 03:30"),
    ("tomorrow at half past 3 pm", NOW, "2026-11-15 15:30"),
    ("tomorrow at quarter to 5 pm", NOW, "2026-11-15 16:45"),
    ("tomorrow at 9.30", NOW, "2026-11-15 09:30"),
    ("tomorrow at 3 o'clock in the afternoon", NOW, "2026-11-15 15:00"),
    ("tomorrow at five in the afternoon", NOW, "2026-11-15 17:00"),
    ("on the 31st at 5pm", NOW, "2026-12-31 17:00"),
    ("friday the 27th at 9am", NOW, "2026-11-27 09:00"),
    ("next week monday at 9am", NOW, "2026-11-16 09:00"),
    ("monday next week at 9am", NOW, "2026-11-16 09:00"),
    ("next week friday at 3pm", NOW, "2026-11-20 15:00"),
    ("next month on the 5th at 9am", NOW, "2026-12-05 09:00"),
    ("november 16 pay 2000 pesos at 9am", NOW, "2026-11-16 09:00"),
    ("16 november 2027 at 9am", NOW, "2027-11-16 09:00"),
    ("tomorrow at 7 submit the afternoon report", NOW, "2026-11-15 07:00"),
    ("tomorrow at 7 in the evening", NOW, "2026-11-15 19:00"),
    ("every monday at 9", MON_10, "2026-11-23 09:00"),
    ("every weekday at 8", NOW, None),
    ("every other day at 8", NOW, None),
    ("at 6 put on sunscreen before the sun sets", NOW, "2026-11-14 18:00"),
    ("on sat at 10am", NOW, "2026-11-21 10:00"),
    ("monday at 9am", NOW, "2026-11-16 09:00"),
    ("in 20 minutes", NOW, "2026-11-14 14:20"),
    ("tomorrow", NOW, "2026-11-15 09:00"),
]

bad = 0
for phrase, clock, want in CASES:
    r = w.parse(phrase, clock)
    got = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["at"])) if r else None
    ok = got == want
    bad += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {phrase!r:48} -> {got}" + ("" if ok else f"   want {want}"))
print(f"{len(CASES)} cases, {bad} wrong")
print("RESULT:", "PASS" if not bad else "FAIL")
sys.exit(1 if bad else 0)
