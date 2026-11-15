#!/usr/bin/env python3
"""care_test.py -- breaks, late nights, habits and the daily question, on a fake clock.

Uses a throwaway database: nothing here touches ~/.nova/habits.db.
"""
import random
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")
import nova_care as C  # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        fails.append(name)


def at(day: int, hour: int, minute: int = 0) -> float:
    return time.mktime((2026, 11, day, hour, minute, 0, 0, 0, -1))


def fresh():
    d = Path(tempfile.mkdtemp(prefix="nova-care-"))
    return C.Care(C.Store(d / "habits.db"), random.Random(7))


def run(care, start, minutes, app="code", idle=0.0, hold=False, music="", every=10, next_class=None):
    """Tick through `minutes`; return [(minutes in, line)] for everything she said."""
    said = []
    t = start
    while t < start + minutes * 60:
        line = care.tick(now=t, idle=idle, app=app, music=music, hold=hold, next_class=next_class)
        if line:
            said.append((round((t - start) / 60), line))
        t += every
    return said, t


C.cfg = lambda: dict(C.DEFAULTS)                      # his own config must not steer the test

# 1. a long stretch of work: one remark at two hours, another ninety minutes on
care = fresh()
said, t = run(care, at(10, 14), 250)
check("silent for the first two hours", not [m for m, _ in said if m < 119], str([m for m, _ in said]))
check("speaks at two hours", any(119 <= m <= 121 for m, _ in said), str([m for m, _ in said]))
check("and again ninety minutes later, no sooner", [m for m, _ in said] == [120, 210], str([m for m, _ in said]))
check("says how long it has been", "two hours" in said[0][1].lower(), said[0][1])
check("starts with a capital", said[0][1][:1].isupper() and said[1][1][:1].isupper())
check("the second remark is worded differently", said[0][1] != said[1][1])

# 2. a real break resets it; a pause to read something does not
care = fresh()
said, t = run(care, at(10, 14), 100)
_, t = run(care, t, 4, idle=200)                      # four minutes away: not a break
said2, t = run(care, t, 30)
check("a short pause does not reset the clock", any("hour" in line for _, line in said2), str(said2))
_, t = run(care, t, 12, idle=600)                     # twelve minutes away: a break
said3, t = run(care, t, 100)
check("a real break resets the clock", not said3, str(said3))
check("the sitting was written down", len(care.store.rows("select * from sessions")) == 1)

# 3. she does not talk over a game; she says it when he tabs out
care = fresh()
said, t = run(care, at(10, 14), 170, app="valorant-win64-shipping", hold=True)
check("silent during the game", not said, str(said))
said, t = run(care, t, 2, app="chrome", hold=False)
check("speaks when he tabs out, about the game", len(said) == 1 and "Valorant" in said[0][1], str(said))

# 3b. except once, briefly, after four hours inside one
care = fresh()
said, t = run(care, at(10, 12), 300, app="valorant-win64-shipping", hold=True)
check("one short word after four hours in a game", len(said) == 1 and 239 <= said[0][0] <= 241, str(said))

# 4. late night: once per night, mentions his morning class, notices a streak
care = fresh()
said, t = run(care, at(11, 0, 30), 90)
late = [line for _, line in said if "morning" in line]
check("one remark when the night passes one o'clock", len(late) == 1, str(said))
check("says the time the way a person would", "one in the morning" in late[0], late[0])
care = fresh()
said, t = run(care, at(11, 0, 50), 20, next_class=("Data Structures", "eight in the morning"))
check("mentions the class waiting for him", any("Data Structures" in line for _, line in said), str(said))
care = fresh()
for day in (11, 12, 13):
    said, t = run(care, at(day, 0, 55), 15)
    run(care, t, 15, idle=900)
check("notices three late nights running", any("third" in line or "3 late" in line for _, line in said), str(said))
care = fresh()
said, t = run(care, at(11, 1, 30), 30, hold=True)
check("late remark waits while she must hold", not said, str(said))

# 5. habits: hours per app and what he listens to
care = fresh()
_, t = run(care, at(10, 20), 60, app="code", music="Daniel Caesar - Best Part")
_, t = run(care, t, 30, app="valorant-win64-shipping", music="Daniel Caesar - Best Part", hold=True)
_, t = run(care, t, 1, app="chrome", music="Keshi - LIMBO")           # 60 s: counts
_, t = run(care, t, 0.2, app="chrome", music="Skipped - Song")        # 12 s: skipped past
for _ in range(5):
    _, t = run(care, t, 1, app="chrome", music="Keshi - LIMBO")
    _, t = run(care, t, 1, app="chrome", music="Daniel Caesar - Best Part")
s = C.summary(care.store, now=t)
apps = dict(s["apps_week"])
check("hours per app are kept", abs(apps.get("VS Code", 0) - 3600) < 30 and abs(apps.get("Valorant", 0) - 1800) < 30, str(apps))
artists = dict(s["artists"])
check("plays are counted by artist", artists.get("Daniel Caesar", 0) >= 5 and artists.get("Keshi", 0) >= 5, str(artists))
check("a skipped song is not a play", "Skipped" not in artists)
rep = C.report(care.store, now=t, care=care)
check("the habits tool reports it", "VS Code" in rep and "Keshi" in rep and "Current sitting" in rep)
check("window titles are never stored", not any("title" in str(r).lower() for r in care.store.rows("select sql from sqlite_master")))

# 6. her notes to the model
care = fresh()
_, t = run(care, at(10, 14), 95)
line = care.state_line(now=t)
check("a long sitting rides along with his request", "an hour and a half" in line and "do not nag" in line, line)
care = fresh()
_, t = run(care, at(10, 14), 20)
check("nothing rides along when all is normal", care.state_line(now=t) == "")
q1 = care.curiosity(now=at(10, 15))
q2 = care.curiosity(now=at(10, 18))
q3 = care.curiosity(now=at(11, 15))
check("one question a day", bool(q1) and q2 == "" and bool(q3))
check("a different topic the next day", q1 != q3)

# 7. it survives a restart of the bridge mid-sitting
care = fresh()
_, t = run(care, at(10, 14), 110)
again = C.Care(care.store, random.Random(1))
said, t = run(again, t, 15)
check("a restart does not reset the clock", len(said) == 1 and "two hours" in said[0][1].lower(), str(said))

print("\nRESULT:", "PASS" if not fails else f"FAILED ({len(fails)}): " + ", ".join(fails))
sys.exit(1 if fails else 0)
