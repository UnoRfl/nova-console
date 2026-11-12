#!/usr/bin/env python3
"""
initiative_test.py -- presence, standing orders, "this", watchers, job progress,
open loops and the trust ladder. No bridge, no network, no model, no sound:
every outside thing is faked, and memory goes to a throwaway database.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import nova_memory as M  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="nova-init-"))
M.DB_FILE = TMP / "memory.db"                    # never the real memory

import nova_events as E  # noqa: E402
import nova_gate as G  # noqa: E402
import nova_initiative as I  # noqa: E402

checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("  ok   " if ok else "  FAIL ") + name + (f"   {detail}" if detail else ""))


# ---- presence: away after ten idle minutes, back on the first key press
idle, seq = [0.0], [7]
p = I.Presence(10, idle=lambda: idle[0], seq=lambda: seq[0])
check("at the desk to begin with", p.step(1000) == "" and not p.away)
idle[0] = 601
check("ten idle minutes = left", p.step(2000) == "left" and p.away and abs(p.away_since - 1399) < 1)
check("still away says nothing new", p.step(2600) == "")
idle[0] = 1
check("first input = returned", p.step(5000) == "returned" and not p.away and abs(p.last_away - 3601) < 1)
seq[0] = 8
p.step(5100)
check("clipboard change is timestamped", p.clip_changed_at == 5100)
off = I.Presence(0, idle=lambda: 99999, seq=lambda: 1)
check("away_minutes 0 switches it off", off.step(10) == "" and not off.away)
check("durations read naturally", I.spoken_duration(95) == "2 minutes" and I.spoken_duration(5400) == "1.5 hours"
      and I.spoken_duration(7200) == "2 hours")

# ---- what "this" means
win = lambda: {"app": "Code", "title": "bot.py - nova-bot"}
shot = lambda: r"C:\caps\screen.png"
ctx = I.deictic_context("fix this error", 0, now=100, window=win, clip=lambda: "x", grab=shot)
check("'fix this error' gets window + screenshot", "Code" in ctx and "screen.png" in ctx and "Clipboard" not in ctx)
ctx = I.deictic_context("what does that mean", 90, now=100, window=win, clip=lambda: "TypeError: boom", grab=shot)
check("a fresh clipboard rides along", "TypeError: boom" in ctx)
ctx = I.deictic_context("explain this", 90, now=5000, window=win, clip=lambda: "old", grab=shot)
check("a stale clipboard does not", "old" not in ctx)
ctx = I.deictic_context("explain this", 90, now=100, window=win, clip=lambda: "sk-" + "live-AbCdEf0123456789xyzQRS", grab=shot)
check("a key on the clipboard is withheld", "AbCdEf" not in ctx and "not passed to you" in ctx)
for plain in ("what's the weather", "tell me that joke again", "is it true that cats purr",
              "remind me that the server needs a restart", "open spotify"):
    check(f"not pointing: {plain!r}", I.deictic_context(plain, 99, now=100, window=win, clip=lambda: "x", grab=shot) == "")
for pointing in ("what am I looking at", "summarize this page", "what's on my screen", "read this",
                 "translate what I copied"):
    check(f"pointing: {pointing!r}", bool(I.deictic_context(pointing, 0, now=100, window=win, clip=lambda: "hola", grab=shot)))
check("secrets are recognised", I.looks_secret("ghp_abcdefghijklmnopqrstuvwx1234") and I.looks_secret("password = hunter2222")
      and not I.looks_secret("The quick brown fox jumps over the lazy dog 42 times."))

# ---- standing orders
o = I.Orders(TMP / "orders.json")
a = o.add("Tell Sir when the render finishes", every_minutes=1, hours=2, now=1000)
check("an order is stored, floor of five minutes", a.get("every") == 300 and len(o.list()) == 1)
check("not due at once", o.due(1100) == [])
check("due after its interval", [d["id"] for d in o.due(1301)] == [a["id"]])
o.mark(a["id"], now=1301, said="Half way there.")
it = o.list()[0]
check("a check is counted and what she said is kept", it["runs"] == 1 and it["told"] == 1 and it["said"] == "Half way there.")
check("the next prompt forbids repeating herself", "Half way there." in I.order_prompt(it, 1400))
check("QUIET says nothing", I.parse_order_reply("QUIET") == ("", False))
check("chatter says nothing", I.parse_order_reply("I looked and all is fine, Sir.") == ("", False))
check("TELL speaks, keeps watching", I.parse_order_reply("TELL: The tunnel dropped twice.") == ("The tunnel dropped twice.", False))
check("DONE speaks and ends it", I.parse_order_reply("DONE: The render has finished, Sir.") == ("The render has finished, Sir.", True))
o.mark(a["id"], now=1700, done=True)
check("a finished order is gone", o.list() == [])
b = o.add("watch the door", until_back=True, now=1000)
c = o.add("watch the pot", hours=1, now=1000)
check("until-back orders end on return", [g["id"] for g in o.end_until_back()] == [b["id"]] and len(o.list()) == 1)
check("orders expire", [g["id"] for g in o.expire(1000 + 3601)] == [c["id"]] and o.list() == [])
for n in range(I.Orders.MAX):
    o.add(f"thing {n}", now=1000)
check("no more than six at once", "error" in o.add("one too many", now=1000))
check("cancel by words, and all", len(o.cancel("thing 2")) == 1 and len(o.cancel("all")) == I.Orders.MAX - 1)

# ---- the event loop's use of them, with a fake clock and fake everything
said, pushed = [], []
ev = E.Events(announce=said.append, check=lambda prompt: "TELL: It is at sixty percent.", notify=pushed.append)
ev.orders = I.Orders(TMP / "orders2.json")
E.gaming = lambda: False
item = ev.orders.add("Tell Sir how the render is going", every_minutes=5, now=time.time() - 400)
ev.orders.mark(item["id"], now=time.time() - 400)
ev.check_orders()
for _ in range(40):
    if not ev.checking:
        break
    time.sleep(0.05)
check("a due order is checked and spoken", said and said[-1] == "Sir, it is at sixty percent.", str(said[-1:]))

said.clear()
ev.presence.away = True
check("away: ordinary news is held", ev.announce("Sir, the job has finished.").startswith("held") and not said)
ev.announce("Sir, the tunnel has gone down.", urgent=True)
check("away: urgent is said, kept, and pushed to him", said == ["Sir, the tunnel has gone down."]
      and pushed == ["Sir, the tunnel has gone down."] and ev.missed)
ev.missed = [(at - 600, t) for at, t in ev.missed]     # it was said ten minutes before he came back
ev.presence.away, ev.returned = False, 5400
said.clear()
ev.flush_held()
check("back: one welcome with everything in it", len(said) == 1 and said[0].startswith("Welcome back, Sir. You were away 1.5 hours.")
      and "tunnel has gone down" in said[0] and "job has finished" in said[0], said[0] if said else "")
said.clear()
ev.flush_held()
check("and then nothing more", said == [])

# ---- the headless PC and its tunnel
UP = "Server stack:\n  minecraft (java 25565)     UP\n  panel    (http 8770)       UP\n  playit tunnel service      UP\n"
DOWN = UP.replace("playit tunnel service      UP", "playit tunnel service      DOWN")
check("status is parsed", I.stack_state(UP) == {"minecraft": True, "panel": True, "tunnel": True}
      and I.stack_state(DOWN)["tunnel"] is False and I.stack_state("The headless PC did not answer in time.") == {})
ev2 = E.Events(announce=said.append)
said.clear()
ev2.check_stack(UP, now=0)
check("first look says nothing", said == [])
ev2.check_stack(DOWN, now=100)
check("tunnel down is announced", said and "tunnel has gone down" in said[-1])
ev2.check_stack(UP, now=200)
ev2.check_stack(DOWN, now=300)
ev2.check_stack(UP, now=400)
ev2.check_stack(DOWN, now=500)
check("three drops in an hour is called flapping", "flapping" in said[-1], said[-1])
said.clear()
for _ in range(2):
    ev2.check_stack("ssh: connect to host timed out")
check("two missed answers is a blip", said == [])
ev2.check_stack("ssh: connect to host timed out")
check("three is news", said and "stopped answering" in said[-1])
ev2.check_stack(UP)
check("and it says when it is back", any("answering again" in s for s in said))

# ---- pull requests
old = {"19": {"title": "Move the address", "state": "OPEN", "comments": 0, "reviews": 0}}
check("first look at a repo is silent", I.pr_changes(None, old, "o/shop-os") == [])
new = {"19": {"title": "Move the address", "state": "MERGED", "comments": 0, "reviews": 0},
       "20": {"title": "Fix printer", "state": "OPEN", "comments": 0, "reviews": 0}}
lines = I.pr_changes(old, new, "o/shop-os")
check("a merge and a new PR are both reported", len(lines) == 2 and any("merged" in l for l in lines)
      and any("new pull request 20" in l for l in lines), str(lines))
new2 = {"19": {"title": "Move the address", "state": "OPEN", "comments": 2, "reviews": 0}}
check("a new comment is reported", "new comment" in I.pr_changes(old, new2, "o/shop-os")[0])
check("no change, no words", I.pr_changes(old, dict(old), "o/shop-os") == [])

# ---- what a worker is doing
out = TMP / "job.out"
rows = [{"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "I will start with the tests. Then the fix."}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "pytest"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "C:/x/bot.py"}}]}}]
out.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8")
prog = I.job_progress(str(out))
check("steps, current action and last words are read", prog.get("steps") == 2 and prog.get("doing") == "editing bot.py"
      and prog.get("said") == "I will start with the tests." and prog.get("fails") == 1, str(prog))
check("it makes one spoken sentence", I.progress_line("Fix the bot", prog, 6) ==
      "Sir, the job, Fix the bot, is 2 steps in after 6 minutes, currently editing bot.py.")
check("a missing file is not an error", I.job_progress(str(TMP / "nope.out")) == {})


class FakeJobs:
    def __init__(self):
        self.rows = [{"id": "j1", "title": "Fix the bot", "state": "running", "started": time.time() - 600, "out": str(out)}]

    def list(self):
        return self.rows


said.clear()
ev3 = E.Events(announce=said.append, jobs=FakeJobs())
ev3.narrate_jobs()
check("a long job is narrated", len(said) == 1 and "2 steps in" in said[0], str(said))
ev3.narrate_jobs(now=time.time() + 400)
check("but not again until it has moved on", len(said) == 1)

# ---- open loops
text, opened, closed = I.harvest_loops("Parked, Sir. [[loop: Pitch deck -- phone mode is still untested]]")
check("a loop marker is filed and stripped", text == "Parked, Sir." and opened and M.loops()[0]["text"].startswith("Pitch deck"))
check("stored loops are not written from a guest", I.harvest_loops("Hi [[loop: nope]]", store=False)[0] == "Hi" and len(M.loops()) == 1)
M.log_exchange("how is the pitch deck going", "The intro scroll is finished; phone mode is next.")
block = M.recall_block("where did we leave off on the pitch deck")
check("'where did we leave off' recalls the loop and the diary", "still open" in block and "phone mode is next" in block, block[:160])
check("the return greeting mentions it", "Pitch deck" in I.loops_line())
text, opened, closed = I.harvest_loops("Done, Sir. [[done: Pitch deck phone mode]]")
check("a done marker closes it", closed and M.loops() == [] and text == "Done, Sir.")
check("the closure lands in the diary", any("Finished: Pitch" in l for l in M.day(time.strftime("%Y-%m-%d"))))

# ---- trust ladder
G.TRUST_FILE = TMP / "trust.json"
commit = {"command": "git commit -m wip"}
check("nothing is trusted to begin with", not G.trusted("Bash", commit))
earned = [G.learn("Bash", commit, True) for _ in range(3)]
check("three yeses earn trust, announced once", earned == ["", "", "Bash:git commit"] and G.trusted("Bash", {"command": "git commit -m other"}))
check("trust is per kind", not G.trusted("Bash", {"command": "git tag v1"}))
G.learn("Bash", commit, False)
check("one no takes it back", not G.trusted("Bash", commit))
for cmd in ("git push origin main", "rm -rf build", "Remove-Item x -Recurse", "git commit -m x && git push",
            "taskkill /im foo.exe", "echo hi > file.txt"):
    for _ in range(4):
        G.learn("Bash", {"command": cmd}, True)
    check(f"never learned: {cmd}", not G.trusted("Bash", {"command": cmd}))
for _ in range(4):
    G.learn("mcp__nova__minecraft_stop", {}, True)
    G.learn("Edit", {"file_path": str(Path.home() / ".ssh" / "config")}, True)
check("nor stopping the server, nor editing keys", not G.trusted("mcp__nova__minecraft_stop", {})
      and not G.trusted("Edit", {"file_path": str(Path.home() / ".ssh" / "config")}))

print("RESULT:", "PASS" if all(checks) else f"NEEDS WORK ({checks.count(False)} of {len(checks)} failed)")
sys.exit(0 if all(checks) else 1)
