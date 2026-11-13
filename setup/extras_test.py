#!/usr/bin/env python3
"""
extras_test.py -- briefing, arrival, health, rules, follow-ups, project CI,
quick commands and the desk helpers. Offline: no bridge, no network, no model,
no sound, no microphone; every outside thing is faked and files go to a
throwaway folder.
    python setup/extras_test.py
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")
import nova_extras as X  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="nova-extras-"))
X.STATE_FILE = TMP / "state.json"
X.RULES_FILE = TMP / "rules.json"
fails = []


def check(name, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + name + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        fails.append(name)


def at(text):                                     # "2026-11-16 08:00" -> epoch
    return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M"))


CONF = dict(X.DEFAULTS)

print("briefing")
t = at("2026-11-16 08:10")
text = X.compose_briefing({"weather": "It is 28 degrees.", "classes": ["Physics at 9", "Math at 1"],
                           "reminders": ["Dentist at 15:00"], "unread": 3,
                           "jobs": [("the build", "done"), ("the deploy", "failed")],
                           "down": ["the playit tunnel"], "loops": "One thing is still open: the deck."}, t)
check("greets by time of day and date", text.startswith("Good morning, Sir. It is Monday the 16th of November."), text)
check("classes, reminder, mail", "2 classes today: Physics at 9; Math at 1." in text and "One reminder today: Dentist at 15:00." in text
      and "3 unread emails" in text, text)
check("jobs and outages", "The job the build finished" in text and "The job the deploy failed." in text
      and "the playit tunnel is down" in text, text)
check("an empty day is just the greeting", X.compose_briefing({}, at("2026-11-16 19:30"))
      == "Good evening, Sir. It is Monday the 16th of November.")
check("no school replaces classes", "There is no school today." in X.compose_briefing(
    {"classes": ["Physics"], "no_school": "There is no school today."}, t))
check("weather offline is silent", X.weather_line({"weather": {"lat": 1, "lon": 2}}, get=lambda u: 1 / 0) == "")
w = X.weather_line({"weather": {"lat": 1, "lon": 2}}, get=lambda u: {
    "current": {"temperature_2m": 28.4, "weather_code": 61},
    "daily": {"temperature_2m_max": [33.6], "precipitation_probability_max": [70]}})
check("weather sentence", w == "It is 28 degrees and raining, with a high of 34 and rain likely, 70 percent.", w)

print("arrival")
a = X.Arrival()
check("sitting down at 2 a.m. is not a morning", not a.step(5, at("2026-11-16 02:00"), CONF, ""))
for hh in ("03:00", "04:00", "04:59"):                # he never left the keyboard
    a.step(5, at("2026-11-16 " + hh), CONF, "")
check("still up at 5 a.m.: no briefing", not a.step(5, at("2026-11-16 05:10"), CONF, ""))
check("back at 9 after hours away: briefing", a.step(5, at("2026-11-16 09:00"), CONF, ""))
check("already briefed today: nothing", not a.step(5, at("2026-11-16 09:01"), CONF, "2026-11-16"))
a2 = X.Arrival()
check("first look in the afternoon counts", a2.step(5, at("2026-11-16 14:00"), CONF, ""))
check("but not while he is idle", not X.Arrival().step(900, at("2026-11-16 14:00"), CONF, ""))
a3 = X.Arrival()
a3.step(5, at("2026-11-16 10:00"), CONF, "2026-11-16")
check("a short break is not an arrival", not a3.step(5, at("2026-11-16 11:00"), CONF, ""))
check("switched off", not X.Arrival().step(5, at("2026-11-16 09:00"), {**CONF, "briefing": ""}, ""))

print("health")
ram = [50.0]
cpu = [(0, 0)]
h = X.Health(ram=lambda: ram[0], cpu=lambda: cpu[0], disks=lambda: [("C", 2, 5), ("D", 5, 40), ("E", 60, 500)],
             top=lambda by: "chrome")
B = 2_000_000                                       # a real-sized clock: "an hour since" needs one
out = h.step(CONF, B + 1000)
check("a full second drive is mentioned once, C is left to the old check",
      out == ["Sir, drive D is down to 5 percent free, about 40 gigabytes."], str(out))
check("not twice the same day", h.step(CONF, B + 1200) == [])
ram[0] = 95.0
check("one high reading is not news", h.step(CONF, B + 1400) == [])
out = h.step(CONF, B + 1600)
check("two in a row is", out == ["Sir, memory is at 95 percent; chrome is using the most."], str(out))
check("then quiet for an hour", h.step(CONF, B + 1800) == [] and h.step(CONF, B + 3000) == [])
check("and again after the hour", len(h.step(CONF, B + 1600 + 3700)) == 1)
ram[0] = 40.0
cpu[0] = (100, 1000)
h.step(CONF, B + 9000)
cpu[0] = (105, 2000)                                # 5 idle of 1000 = 99.5% busy
h.step(CONF, B + 9100)
cpu[0] = (110, 3000)
out = h.step(CONF, B + 9200)
check("sustained processor load", len(out) == 1 and "processor has been at 99 percent" in out[0], str(out))

print("rules")
check("bad kind refused", "error" in X.add_rule("x", "moon_phase", "", [{"say": "hi"}]))
check("time needs HH:MM", "error" in X.add_rule("x", "time", "noon", [{"say": "hi"}]))
check("needs something to do", "error" in X.add_rule("x", "back", "", []))
r = X.add_rule("Studio bridge", "app_open", "RobloxStudioBeta.exe", [{"ask": "start the bridge"}], cooldown_minutes=5)
check("saved, name normalised", r.get("id") == "studio-bridge" and r["when"]["value"] == "robloxstudiobeta", str(r))
X.add_rule("Tunnel fix", "service_down", "tunnel", [{"ask": "restart the tunnel"}], say="The tunnel dropped; fixing it.")
X.add_rule("Evening", "time", "21:30", [{"routine": "good-night"}])
X.add_rule("Hello", "back", "", [], say="Welcome back.")
check("described in plain words", X.describe_rule(r) == "when robloxstudiobeta opens")
procs = [set()]
R = X.Rules(procs=lambda: procs[0])
t0 = at("2026-11-16 21:00")
procs[0] = {"robloxstudiobeta", "explorer"}
check("first look only learns", R.step({"tunnel": False}, "", t0) == [])
procs[0] = {"explorer"}
R.step({"tunnel": True}, "", t0 + 10)
procs[0] = {"explorer", "robloxstudiobeta"}
f = R.step({"tunnel": True}, "", t0 + 20)
check("app opening fires its rule", [x["id"] for x in f] == ["studio-bridge"], str(f))
check("staying open does not fire again", R.step({"tunnel": True}, "", t0 + 30) == [])
procs[0] = {"explorer"}
R.step({"tunnel": True}, "", t0 + 40)
procs[0] = {"explorer", "robloxstudiobeta"}
check("reopened inside the cooldown: quiet", R.step({"tunnel": True}, "", t0 + 50) == [])
f = R.step({"tunnel": False}, "", t0 + 60)
check("service going down fires", [x["id"] for x in f] == ["tunnel-fix"], str(f))
check("still down: not again", R.step({"tunnel": False}, "", t0 + 70) == [])
f = R.step({"tunnel": False}, "returned", t0 + 80)
check("coming back fires", [x["id"] for x in f] == ["hello"], str(f))
f = R.step({"tunnel": False}, "", at("2026-11-16 21:30") + 5)
check("time of day fires once", [x["id"] for x in f] == ["evening"], str(f))
check("not twice in the same minute", R.step({"tunnel": False}, "", at("2026-11-16 21:30") + 15) == [])
check("delete by words", [g["id"] for g in X.delete_rule("tunnel")] == ["tunnel-fix"] and len(X.load_rules()) == 3)

print("follow-ups")
now = at("2026-11-16 15:00")
loops = [{"id": 1, "text": "Fix the Opera cookies.", "at": "2026-11-13 15:09:46"},
         {"id": 2, "text": "Send the deck", "at": "2026-11-16 14:30:00"}]
p = X.pick_loop(loops, {}, CONF, now)
check("oldest loose end first", p and p["id"] == 1)
check("spoken with its day", X.loop_line(p, now) == "Sir, a loose end from Friday: Fix the Opera cookies. Tell me if it is done.",
      X.loop_line(p, now))
check("a fresh one is left alone", X.pick_loop(loops[1:], {}, CONF, now) is None)
check("not within four hours of the last", X.pick_loop(loops, {"9": now - 3600}, CONF, now) is None)
check("not the same one inside three days", X.pick_loop(loops, {"1": now - 2 * 86400}, CONF, now) is None)
check("two a day at most", X.pick_loop(loops, {"8": now - 5 * 3600, "9": now - 10 * 3600}, CONF, now) is None)

print("project CI")
board = {"repos": [{"repo": "a/x", "name": "x", "ci": "FAILURE", "last": "break it"},
                   {"repo": "a/y", "name": "y", "ci": "SUCCESS", "last": "fix"}]}
check("first look says nothing", X.ci_changes(None, board) == [])
check("newly failing is said", X.ci_changes({"a/x": False, "a/y": False}, board) == ["Sir, CI has started failing on x: break it."])
check("recovery is said", X.ci_changes({"a/x": True, "a/y": True}, board) == ["Sir, CI on y is passing again."])
check("board text", "x: 0 open PRs, CI FAILING" in X.board_text({"repos": [dict(board["repos"][0], prs=0, issues=0, when="")]}))

print("quick commands")
Q = X.quick_command
check("start notes with a title", Q("Nova, start taking notes on the physics lecture.") == ("notes_start", "the physics lecture"))
check("start notes bare", Q("start recording notes") == ("notes_start", ""))
check("stop notes", Q("Stop the notes, please") == ("notes_stop", "") and Q("stop recording") == ("notes_stop", ""))
check("brief me", Q("brief me") == ("brief", "") and Q("Nova, what did I miss?") == ("brief", ""))
for other in ["stop the minecraft server", "start the notes app and open my essay", "take a note that milk is out",
              "brief me on the tpn pull request", "stop", "note"]:
    check(f"left to her: {other!r}", Q(other) is None)

print("desk")
path = X.save_note("Buy cables.", "shopping", folder=TMP / "notes", now=at("2026-11-16 15:00"))
body = Path(path).read_text(encoding="utf-8")
check("note appended under a dated heading", path.endswith("notes-2026-11.md") and "## Mon 16 Nov, 15:00 -- shopping" in body
      and "Buy cables." in body, body)
(TMP / "dl").mkdir()
(TMP / "dl" / "report-final.pdf").write_text("x")
(TMP / "dl" / "old.txt").write_text("x")
import os  # noqa: E402
os.utime(TMP / "dl" / "old.txt", (time.time() - 9 * 86400,) * 2)
rows = X.recent_files(str(TMP / "dl"), days=2)
check("recent files: new one only", [r["name"] for r in rows] == ["report-final.pdf"], str(rows))
check("recent files: by name", X.recent_files(str(TMP / "dl"), days=30, contains="OLD")[0]["name"] == "old.txt")
check("a missing folder is empty, not an error", X.recent_files(str(TMP / "nope")) == [])
check("process list is real and quick", "explorer" in X.process_names())

print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED: " + "; ".join(fails))
sys.exit(1 if fails else 0)
