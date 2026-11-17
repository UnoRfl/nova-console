"""Repeat guard (2026-11-17): the same unprompted line is not said twice.
Run: python setup/repeat_test.py   (no bridge needed, nothing is spoken)"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["NOVA_TEST"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import nova_events as E  # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + name + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails.append(name)


said = []
ev = E.Events(announce=said.append)
ev.should_hold = lambda: ""
ev.recent = E.Recent(Path(tempfile.mkdtemp()) / "announced.json")

ev.announce("Sir, memory is at 94 percent; Memory Compression is using the most.")
ev.announce("Sir, memory is at 97 percent; Memory Compression is using the most.")
check("drifting numbers count as the same line", len(said) == 1, str(said))

ev.announce("Sir, Science, Technology and Society starts at 4 PM. That is in 10 minutes.", urgent=True)
ev.announce("Sir, Science, Technology and Society starts at 4 PM. That is in 10 minutes.", urgent=True)
check("urgent lines are guarded too", len(said) == 2, str(said))

ev.announce("Sir, the headless PC has stopped answering.", urgent=True)
ev2 = E.Events(announce=said.append)
ev2.should_hold = lambda: ""
ev2.recent = E.Recent(ev.recent.path)
ev2.announce("Sir, the headless PC has stopped answering.", urgent=True)
check("a restart does not forget what was said", said.count("Sir, the headless PC has stopped answering.") == 1)

ev.announce("Sir, the playit tunnel is back up.")
check("a different line still goes out", said[-1] == "Sir, the playit tunnel is back up.")

t = time.time()
ev.recent.items = [dict(i, at=i["at"] - 4 * 3600) for i in ev.recent.items]
ev.announce("Sir, memory is at 95 percent; Memory Compression is using the most.")
check("after the window it may be said again", said[-1].startswith("Sir, memory is at 95"))

check("dedupe keeps one of two wordings",
      len(E.dedupe(["Sir, the overnight swimming vacation with the boys is today.",
                    "Sir, the overnight swimming trip with the boys is today."], 0.75)) == 1)

# reminders: creation merges a re-worded one-shot, keeps the daily
tmp = Path(tempfile.mkdtemp()) / "reminders.json"
E.REMINDERS_FILE = tmp
r = E.Reminders()
at = time.time() + 3600
r.add("Sir, the overnight swimming vacation with the boys is today.", at)
r.add("Sir, the overnight swimming trip with the boys is today.", at + 60)
r.add("Take a break, Sir.", at, daily="18:00")
items = r.list()
check("re-worded one-shot replaces the old one", len([i for i in items if not i["daily"]]) == 1, str(items))
check("daily one untouched", any(i["daily"] for i in items))
r.add("Call the dentist.", at)
check("unrelated reminder kept", len(r.list()) == 3)

print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED: " + ", ".join(fails))
sys.exit(1 if fails else 0)
