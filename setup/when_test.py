"""nova_when: dates and times for reminders and meetings. Run: venv python setup/when_test.py"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")
import nova_when as W  # noqa: E402

# Friday 13 November 2026, 16:30 -- the afternoon the 16th went to the 14th.
NOW = time.mktime((2026, 11, 13, 16, 30, 0, 0, 0, -1))
fails = 0


def check(text, want, daily="", weekly=None):
    global fails
    r = W.parse(text, NOW)
    got = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["at"])) if r else None
    ok = got == want and (not r or (r["daily"] == daily and r["weekly"] == weekly))
    fails += not ok
    print(("  ok   " if ok else "  FAIL ") + f"{text!r:58} -> {got}" + ("" if ok else f"   want {want}"))


check("at 09:00 on Monday 16 November", "2026-11-16 09:00")
check("at 09:00 on the 16th of November 2026", "2026-11-16 09:00")
check("the 16th of November", "2026-11-16 09:00")
check("November 16th at 3pm", "2026-11-16 15:00")
check("Nov 16 9:30 pm", "2026-11-16 21:30")
check("2026-11-16 09:00", "2026-11-16 09:00")
check("2026-12-06 09:00", "2026-12-06 09:00")
check("on December 6 at 09:00", "2026-12-06 09:00")
check("16 Nobyembre", "2026-11-16 09:00")
check("on the 16th", "2026-11-16 09:00")
check("the 5th", "2026-12-05 09:00")
check("Monday at 09:00", "2026-11-16 09:00")
check("next Monday at 7", "2026-11-16 07:00")
check("this Friday at 8pm", "2026-11-13 20:00")
check("Friday at 10am", "2026-11-20 10:00")
check("tomorrow", "2026-11-14 09:00")
check("tomorrow at 7 tonight", "2026-11-14 19:00")
check("bukas ng 3pm", "2026-11-14 15:00")
check("day after tomorrow at noon", "2026-11-15 12:00")
check("in 3 days at 09:00", "2026-11-16 09:00")
check("in 2 weeks", "2026-11-27 09:00")
check("tonight", "2026-11-13 20:00")
check("in 20 minutes", "2026-11-13 16:50")
check("in an hour and a half", "2026-11-13 18:00")
check("at 5 pm call mum", "2026-11-13 17:00")
check("at 9:30 check the server", "2026-11-14 09:30")
check("every day at 08:00", "2026-11-14 08:00", daily="08:00")
check("daily at 6pm", "2026-11-13 18:00", daily="18:00")
check("every Monday at 7am", "2026-11-16 07:00", weekly=0)
check("January 5", "2027-01-05 09:00")
check("remind me to stretch", None)
check("remind me to buy 2 eggs", None)
check("call the boys about the trip", None)
check("February 30", None)
print("FAILURES:", fails)
print("describe:", W.describe(W.parse("the 16th of November", NOW)["at"], NOW))
sys.exit(1 if fails else 0)
