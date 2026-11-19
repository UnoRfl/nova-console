#!/usr/bin/env python3
"""
brain_test.py -- the bridge side of the brain view (brain/SPEC.md 4.4 F-4).

  1. Honest recall. recall_block's text is byte-identical to the version
     before the brain view (backups/nova_memory.py.pre-brain) for 50 real
     questions from the history and a few made-up ones, both modules reading
     the same copy of her memory -- and it now reports exactly the rows it
     handed over (ids), the near misses (near), the diary days (days) and the
     words it looked for (terms).
  2. The pieces in the bridge: the recall beat, the orb's new fields, the
     trail of places she has been, and the long-poll on the activity feed.
  3. Over HTTP, against a TEST bridge of its own on a free port (a copy of
     her memory, a made-up history, the Claude stub): the long-poll wakes
     within 0.2 s of a push and comes back empty when the wait runs out,
     /api/brain/match and /api/brain/trail answer in the agreed shape, and
     /api/dev/activity only exists on a test bridge.

Nothing is asked of the model, nothing real is written: the live memory and
history are only ever copied (read-only), and nothing from them is printed.

    venv/Scripts/python.exe setup/brain_test.py
"""
import gc
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import types
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="nova-brain-test-"))
os.environ["NOVA_TEST"] = "1"
os.environ["NOVA_TEST_DIR"] = str(TMP / "inproc")
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True          # loading the backup must not leave a .pyc in backups/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import nova_bridge as nb  # noqa: E402  -- after NOVA_TEST: every path it writes is in TMP
import nova_history as H  # noqa: E402
import nova_memory as new  # noqa: E402
import nova_skills as S  # noqa: E402

TOKEN = (ROOT / "token").read_text(encoding="utf-8").strip()      # sent, never printed
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("  ok   " if ok else "  FAIL ") + name + (f"   {detail}" if detail else ""))


def copy_db(src: Path, dst: Path) -> bool:
    """A consistent copy of a live SQLite file; the source is opened read-only."""
    if not src.is_file():
        return False
    s = sqlite3.connect(src.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        s.close()
        d.close()
    return True


def load_backup():
    """The pre-brain nova_memory, imported under another name."""
    path = ROOT / "backups" / "nova_memory.py.pre-brain"
    loader = importlib.machinery.SourceFileLoader("nova_memory_pre_brain", str(path))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return mod


# ================================================================ 1. recall
print("honest recall: the text she is handed is unchanged")
MEM = TMP / "memory.db"
HIST = TMP / "history-copy.db"
check("copied her memory (read-only)", copy_db(ROOT / "memory.db", MEM))
have_hist = copy_db(ROOT / "history.db", HIST)
old = load_backup()
old.DB_FILE = new.DB_FILE = MEM
check("the backup is the version before the brain view",
      not hasattr(old, "LAST_NEAR") and "near" not in old.search.__code__.co_varnames)

real: list[str] = []
if have_hist:
    h = sqlite3.connect(HIST)
    seen_q = set()
    for (asked,) in h.execute("SELECT asked FROM turns ORDER BY id DESC"):
        key = " ".join(str(asked or "").lower().split())
        if key and key not in seen_q:
            seen_q.add(key)
            real.append(str(asked))
        if len(real) >= 50:
            break
    h.close()
check("50 real questions from the history (a copy)", len(real) == 50, f"{len(real)} found")
SYNTH = ["where did we leave off on the deck?", "what did I do yesterday", "today", "", "hello",
         "catch me up", "what was I working on last time", "any open loops?",
         "restart the cobblemon server", "what did we do earlier on the orbit pitch deck",
         "nova console wake word voice panel memory", "ghp_" + "a1" * 12]

mem = sqlite3.connect(MEM)


def text_of(i):
    r = mem.execute("SELECT text FROM memory WHERE id=?", (i,)).fetchone()
    return r[0] if r else None


same, honest, shape, problems = 0, 0, 0, []
date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
for k, p in enumerate(real + SYNTH):
    a = old.recall_block(p)
    info: dict = {}
    b = new.recall_block(p, info=info)
    which = f"{'real' if k < len(real) else 'made-up'} #{k} ({len(p)} chars)"
    if a == b:
        same += 1
    else:
        problems.append("text differs: " + which)
    ids, near, days, terms = info.get("ids"), info.get("near"), info.get("days"), info.get("terms")
    lines = b.split("\n")[1:] if b else []
    hit_lines = [x for x in lines if not x.startswith("- (log) ")]
    log_lines = [x for x in lines if x.startswith("- (log) ")]
    ok_honest = (len(hit_lines) == len(ids)
                 and all(text_of(i) is not None and text_of(i)[:80] in b for i in ids)
                 and (bool(days) == bool(log_lines)))
    honest += ok_honest
    if not ok_honest:
        problems.append("ids/days do not match the block: " + which)
    ok_shape = (info == {"ids": new.LAST_RECALL, "near": new.LAST_NEAR, "days": new.LAST_DAYS,
                         "terms": new.LAST_TERMS}
                and all(isinstance(i, int) for i in ids + near) and not set(ids) & set(near)
                and len(near) <= 12 and len(terms) <= 8 and len(set(terms)) == len(terms)
                and set(terms) <= set(new._terms(p)) and all(date_re.match(d) for d in days)
                and days == sorted(set(days)))
    shape += ok_shape
    if not ok_shape:
        problems.append("ids/near/days/terms malformed: " + which)
n = len(real) + len(SYNTH)
check(f"recall_block text byte-identical to the backup ({len(real)} real + {len(SYNTH)} made-up)",
      same == n, f"{same}/{n}")
check("LAST_RECALL is exactly the rows in the text, LAST_DAYS only with diary lines",
      honest == n, f"{honest}/{n}")
check("near/days/terms are well formed (near <= 12 and never handed over, terms <= 8)",
      shape == n, f"{shape}/{n}")
for line in problems[:6]:
    print("         " + line)
check("search() with a near list returns the same as without it, and as before",
      all(new.search(q, near=[]) == new.search(q) == old.search(q)
          for q in ["cobblemon server", "orbit pitch deck", "hello", ""]))
check("day() and trail() return what they always did",
      new.day(time.strftime("%Y-%m-%d")) == old.day(time.strftime("%Y-%m-%d"))
      and new.trail("deck") == old.trail("deck") and new.trail("nova voice") == old.trail("nova voice"))
mem.close()

print("honest recall: a budget cut, near misses and diary days (made-up memory)")
SYN = TMP / "synthetic.db"
old.DB_FILE = new.DB_FILE = SYN
filler = "the quick brown fox jumps over the lazy dog while it rains on the hills " * 6
full = [new.add(f"zebra quartz lantern note {i}: " + filler[:330]) for i in range(6)]    # 359 chars each
partial = [new.add(f"zebra crossing number {i} near the station") for i in range(3)]
check("made-up rows stored", all(full) and all(partial))


def syn_text(i):
    with sqlite3.connect(SYN) as db:
        r = db.execute("SELECT text FROM memory WHERE id=?", (i,)).fetchone()
    db.close()
    return r[0] if r else ""
q = "zebra quartz lantern"
order = [h["id"] for h in new.search(q, limit=5, kinds=("fact", "project", "loop"))]
a = old.recall_block(q)
info = {}
b = new.recall_block(q, info=info)
check("same text as before", a == b and b.count("\n- ") == 3, f"{b.count(chr(10) + '- ')} lines")
check("only the 3 rows that fit the 1400-character budget count as recalled",
      new.LAST_RECALL == order[:3], f"{new.LAST_RECALL} vs order {order}")
check("the 2 the budget cut come first in near, then the rows the coverage test turned away",
      new.LAST_NEAR[:2] == order[3:5] and set(new.LAST_NEAR[2:]) == set(partial),
      f"{new.LAST_NEAR}")
check("terms are the words searched for", new.LAST_TERMS == ["zebra", "quartz", "lantern"], str(new.LAST_TERMS))
check("the caller's own copy matches", info == {"ids": order[:3], "near": new.LAST_NEAR,
                                                 "days": [], "terms": ["zebra", "quartz", "lantern"]})
yday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
tday = time.strftime("%Y-%m-%d")
new.add("He asked: zebra plan for the stall -- She answered: noted", kind="day", source="log",
        topic=yday, at=f"{yday} 10:00:00")
new.add("He asked: quartz lamp idea -- She answered: fine", kind="day", source="log",
        topic=tday, at=f"{tday} 00:00:01")
new.add("finish the zebra stall sign", kind="loop", source="voice")
for prompt, want in [("what did I do yesterday", [yday]), ("what did we do today", [tday]),
                     ("where did we leave off on the zebra plan", [yday])]:
    a = old.recall_block(prompt)
    b = new.recall_block(prompt)
    check(f"days for {prompt!r}", a == b and new.LAST_DAYS == want and "(log)" in b, str(new.LAST_DAYS))
check("an open loop handed over for 'leave off' counts as recalled",
      any("zebra stall sign" in syn_text(i) for i in new.LAST_RECALL), str(new.LAST_RECALL))
b = new.recall_block("hello there zorblax")             # "there" is a stop word
check("nothing found: no text, no ids, but the words are kept", b == "" and new.LAST_RECALL == []
      and new.LAST_DAYS == [] and new.LAST_TERMS == ["hello", "zorblax"], str(new.LAST_TERMS))
m = new.match(q)
check("match(): ids, near (no overlap, <= 12) and terms",
      set(m) == {"ids", "near", "terms"} and m["ids"] == [h["id"] for h in new.search(q, limit=8, kinds=(
          "fact", "project", "loop"))] and not set(m["ids"]) & set(m["near"]) and len(m["near"]) <= 12
      and m["terms"] == ["zebra", "quartz", "lantern"], str(m))
check("match_ids() is still match()'s ids", new.match_ids(q) == m["ids"])
new.DB_FILE = MEM

# ======================================================== 2. bridge pieces
print("bridge pieces")
act = nb.Activity()
check("an empty recall is not worth a beat", nb.push_recall(act, {}) is False and act.seq == 0)
nb.push_recall(act, {"ids": [3, 1], "near": [9], "days": [yday], "terms": ["deck", "ghp_" + "a1" * 12]})
e = act.since(0)["events"][-1]
check("the recall beat carries ids, near, days and terms (secret-shaped words dropped)",
      e["kind"] == "recall" and e["ids"] == [3, 1] and e["near"] == [9] and e["days"] == [yday]
      and e["terms"] == ["deck"], json.dumps({k: e[k] for k in ("ids", "near", "days", "terms")}))
nb.push_recall(act, {"ids": [], "near": [], "days": [], "terms": ["hello"]})
e = act.since(0)["events"][-1]
check("words alone still make a beat (an empty recall)", e["kind"] == "recall" and e["ids"] == []
      and e["terms"] == ["hello"])

speaking = types.SimpleNamespace(speaking=True, active=True, saying="Of course, Sir. " * 40)
check("saying: the sentence she is saying, at most 200 characters", len(nb.orb_saying(speaking)) == 200)
check("saying: masked", "hunter2secret" not in nb.orb_saying(types.SimpleNamespace(
    speaking=True, active=True, saying="the password=hunter2secret is set")))
check("saying: empty when she is quiet", nb.orb_saying(types.SimpleNamespace(
    speaking=False, active=False, saying="left over")) == "" and nb.orb_saying(None) == "")

calls = []
real_gaming = nb.nova_events.gaming
nb.nova_events.gaming = lambda: calls.append(1) or True
nb._GAME["at"] = -1e9
got = [nb.gaming_now() for _ in range(5)]
nb._GAME["at"] -= 2.1
got.append(nb.gaming_now())
nb.nova_events.gaming = real_gaming
check("game: nova_events.gaming(), asked at most every 2 s", got == [True] * 6 and len(calls) == 2, str(len(calls)))
nb._GAME["at"] = -1e9
check("game: the real check answers a plain bool", isinstance(nb.gaming_now(), bool))

pend = nb.pending_target([{"tool": "Bash", "detail": "run the command: set password=hunter2secret", "at": 5},
                          {"tool": "Write", "detail": "create or overwrite the file a.txt in docs", "at": 2}])
check("pending: the oldest waiting action as {tool, target}",
      pend == {"tool": "Write", "target": "create or overwrite the file a.txt in docs"}, str(pend))
pend = nb.pending_target([{"tool": "Bash", "detail": "run the command: set password=hunter2secret " + "x" * 300}])
check("pending: target masked and short", "hunter2secret" not in pend["target"] and len(pend["target"]) <= 120)
check("pending: None when nothing waits", nb.pending_target([]) is None)

# the long-poll itself, in-process
a2 = nb.Activity()
t = time.perf_counter()
r = a2.since(0, wait=0.3)
dt = time.perf_counter() - t
check("long-poll: nothing happens -> empty after the wait", r == {"seq": 0, "events": []} and 0.28 <= dt < 0.6,
      f"{dt:.2f}s")
for junk in ("junk", float("nan"), -3, None):
    t = time.perf_counter()
    a2.since(0, wait=junk)
    if time.perf_counter() - t > 0.1:
        break
else:
    junk = "ok"
check("long-poll: a junk, negative or NaN wait does not wait", junk == "ok", str(junk))
a2.MAX_WAIT = 0.4
t = time.perf_counter()
a2.since(0, wait=100)
dt = time.perf_counter() - t
check("long-poll: the wait is capped (MAX_WAIT, 8 s in the bridge)", 0.38 <= dt < 0.8 and nb.Activity.MAX_WAIT == 8.0,
      f"{dt:.2f}s")
a2.MAX_WAIT = 8.0
held = {}


def wait_in_thread():
    t0 = time.perf_counter()
    held["r"] = a2.since(0, wait=5)
    held["t"] = time.perf_counter() - t0


th = threading.Thread(target=wait_in_thread)
th.start()
time.sleep(0.3)
free = a2.lock.acquire(timeout=0.1)
if free:
    a2.lock.release()
check("long-poll: the lock is free while it waits", free)
tp = time.perf_counter()
a2.push("tool", "Read", "notes.md")
th.join(5)
check("long-poll: a push wakes it at once", held.get("r", {}).get("seq") == 1
      and time.perf_counter() - tp < 0.2 and held["t"] < 1.0, f"{held.get('t', 0):.2f}s")
t = time.perf_counter()
r = a2.since(5, wait=8)
check("long-poll: a marker from before a restart is answered at once (so the panel catches up)",
      time.perf_counter() - t < 0.1 and r["seq"] == 1)

# the trail
lines = {
    "read": S.describe_call("Read", {"file_path": str(Path.home() / "Downloads" / "deck" / "notes.md")}),
    "gitbash": S.describe_call("Read", {"file_path": "/c/Users/Someone/x.py"}),
    "grep": S.describe_call("Grep", {"pattern": "TODO", "path": str(Path.home() / "Downloads")}),
    "glob": S.describe_call("Glob", {"pattern": "C:\\Work\\src\\**\\*.py"}),
    "glob_rel": S.describe_call("Glob", {"pattern": "**/*.py"}),
    "web": S.describe_call("mcp__nova__web_open", {"url": "https://www.React.dev/learn/?token=abc123def456"}),
    "bare": S.describe_call("mcp__nova__web_open", {"url": "react.dev"}),
    "bash": S.describe_call("Bash", {"command": "dir"}),
    "skip": S.describe_call("ToolSearch", {"query": "select:x"}),
    "failed": S.describe_call("Read", {"file_path": "D:\\gone\\missing.txt"}) + "  -> FAILED: no such file",
    "cut": 'Read {"file_path": "C:\\\\Users\\\\pc\\\\very\\\\long\\\\pa',
    "tail": "ad ...",
}
places = {k: nb.trail_places(v) for k, v in lines.items()}
check("trail: a Read is a file, home as ~", places["read"] == [("file", "~/downloads/deck/notes.md",
                                                                 "~/Downloads/deck/notes.md")], str(places["read"]))
check("trail: Git Bash paths become drive paths", places["gitbash"][0][2] == "C:/Users/Someone/x.py",
      str(places["gitbash"]))
check("trail: Grep is its folder, Glob the fixed start of its pattern",
      places["grep"] == [("folder", "~/downloads", "~/Downloads")] and places["glob"][0][2] == "C:/Work/src"
      and places["glob_rel"] == [], str(places["glob"]))
check("trail: a page is its site and host+path, never the query string",
      places["web"] == [("site", "react.dev", "react.dev"), ("page", "react.dev/learn", "react.dev/learn")]
      and places["bare"] == [("site", "react.dev", "react.dev")], str(places["web"]))
check("trail: other tools are tools; bookkeeping and cut-off lines are nothing",
      places["bash"] == [("tool", "Bash", "Bash")] and places["skip"] == [] and places["cut"] == []
      and places["tail"] == [], str([places["skip"], places["cut"], places["tail"]]))
check("trail: a failed Read still went somewhere", places["failed"][0][:2] == ("file", "d:/gone/missing.txt"))
H.DB_FILE = TMP / "no-history-here.db"
check("trail: no history -> []", nb.brain_trail(30) == [])

# ================================================================ 3. HTTP
print("over HTTP, a test bridge of its own")


def free_port() -> int:
    while True:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        if port not in (8765, 8798, 8799):
            return port


def call(port, path, body=None, token=TOKEN, timeout=20):
    """(status, parsed JSON or text). A POST when body is given."""
    h = {"Content-Type": "application/json"}
    if token:
        h["X-Nova-Token"] = token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, headers=h,
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as err:
        status, raw = err.code, err.read()
    try:
        return status, json.loads(raw.decode("utf-8"))
    except ValueError:
        return status, raw.decode("utf-8", "replace")


# A made-up history for /api/brain/trail (stored unmasked on purpose: the
# trail must mask on its own).
BDIR = TMP / "bridge"
BDIR.mkdir()
copy_db(ROOT / "memory.db", BDIR / "memory.db")
H.DB_FILE = BDIR / "history.db"
deck = Path.home() / "Downloads" / "deck" / "notes.md"


def ago(seconds):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - seconds))


H.record("an old one", "Done.", at=ago(60 * 86400),
         tools=[S.describe_call("Read", {"file_path": str(Path.home() / "old" / "thing.md")})])
H.record("open my deck notes", "Done.", at=ago(3600), tools=[
    S.describe_call("Read", {"file_path": str(deck).upper()}),
    S.describe_call("mcp__nova__web_open", {"url": "https://react.dev/learn?token=abcdef123456"}),
    S.describe_call("ToolSearch", {"query": "select:x"}),
    S.describe_call("Bash", {"command": "dir"})])
H.record("look again", "Done.", at=ago(60), tools=[
    S.describe_call("Read", {"file_path": str(deck)}),
    S.describe_call("Grep", {"pattern": "TODO", "path": str(deck.parent)}),
    S.describe_call("WebFetch", {"url": "https://www.example.com/a/b/", "prompt": "summarise"}),
    S.describe_call("mcp__nova__web_open", {"url": "https://example.org/login/password=hunter2secret"}),
    S.describe_call("mcp__nova__spotify_play", {"query": "lofi"}),
    S.describe_call("Read", {"file_path": "D:\\gone\\missing.txt"}) + "  -> FAILED: no such file"])
local_trail = nb.brain_trail(30)

PORT = free_port()
env = dict(os.environ, NOVA_TEST="1", NOVA_TEST_DIR=str(BDIR), NOVA_VOICE_VOLUME="0", PYTHONIOENCODING="utf-8",
           NOVA_CLAUDE_STUB=str(ROOT / "setup" / "claude_stub.py"))
py = ROOT / "venv" / "Scripts" / "python.exe"
proc = subprocess.Popen([str(py if py.is_file() else sys.executable), str(ROOT / "nova_bridge.py"), "--port", str(PORT)],
                        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=0x08000000)
try:
    up = False
    for _ in range(240):
        try:
            up = call(PORT, "/api/activity?since=0", timeout=2)[0] == 200
            if up:
                break
        except OSError:
            pass
        time.sleep(0.25)
    check("the test bridge came up", up, f"port {PORT}")
    # let it settle: nothing of its own in the feed for a second and a half
    last, still, began = -1, time.time(), time.time()
    while time.time() - still < 1.5 and time.time() - began < 15:
        seq = call(PORT, "/api/activity?since=0")[1]["seq"]
        if seq != last:
            last, still = seq, time.time()
        time.sleep(0.25)

    # --- the long-poll
    seq0 = call(PORT, "/api/activity?since=0")[1]["seq"]
    t = time.perf_counter()
    st, r = call(PORT, f"/api/activity?since={seq0}")
    check("activity without wait answers at once, as before", st == 200 and r["events"] == []
          and time.perf_counter() - t < 0.5)
    got = {}

    def poll(key, path):
        got[key] = call(PORT, path, timeout=15)
        got[key + "_t"] = time.perf_counter()

    waiters = [threading.Thread(target=poll, args=(f"w{i}", f"/api/activity?since={seq0}&wait=8")) for i in range(3)]
    for w in waiters:
        w.start()
    time.sleep(0.8)
    t = time.perf_counter()
    st, orb = call(PORT, "/api/orb")
    check("other requests are answered while long-polls wait", st == 200 and time.perf_counter() - t < 1.0,
          f"{time.perf_counter() - t:.2f}s")
    tp = time.perf_counter()
    st, pushed = call(PORT, "/api/dev/activity", {"kind": "tool", "text": "Read", "detail": "notes.md",
                                                  "id": "toolu_test1", "input": "{}", "seq": 999, "at": 1})
    for w in waiters:
        w.join(12)
    lags = [got.get(f"w{i}_t", 1e9) - tp for i in range(3)]
    ev = (got.get("w0") or (0, {}))[1].get("events") or [{}]
    check("long-poll returns within 0.2 s of a push (3 waiters, all woken)",
          all(0 <= x < 0.2 for x in lags), " ".join(f"{x:.3f}s" for x in lags))
    check("...with the pushed event, whose seq and at are the bridge's own",
          ev[-1].get("id") == "toolu_test1" and ev[-1].get("kind") == "tool" and ev[-1]["seq"] == pushed.get("seq")
          and ev[-1]["seq"] != 999 and ev[-1]["at"] > 1e9, str(ev[-1].get("seq")))
    seq1 = call(PORT, "/api/activity?since=0")[1]["seq"]
    t = time.perf_counter()
    st, r = call(PORT, f"/api/activity?since={seq1}&wait=1.5")
    dt = time.perf_counter() - t
    check("long-poll with nothing happening returns empty after the wait", st == 200 and r["events"] == []
          and r["seq"] == seq1 and 1.4 <= dt < 2.2, f"{dt:.2f}s")
    t = time.perf_counter()
    st, r = call(PORT, f"/api/activity?since={seq0}&wait=8")
    check("long-poll with newer events already there does not wait", r["events"] and time.perf_counter() - t < 0.5)
    t = time.perf_counter()
    st, r = call(PORT, "/api/activity?since=999999&wait=8")
    check("long-poll with a marker from before a restart answers at once",
          r["seq"] == seq1 and time.perf_counter() - t < 0.5)

    # --- /api/dev/activity
    st, r = call(PORT, "/api/dev/activity", {"kind": "recall", "text": "Memory", "detail": "2 memories",
                                             "ids": [1, 2], "near": [3], "days": [yday], "terms": ["deck"]})
    st2, r2 = call(PORT, f"/api/activity?since={r.get('seq', 1) - 1}")
    e = (r2.get("events") or [{}])[-1] if isinstance(r2, dict) else {}
    check("dev: a fixture event is replayed into the feed, extras and all",
          st == 200 and e.get("kind") == "recall" and e.get("ids") == [1, 2] and e.get("near") == [3]
          and e.get("terms") == ["deck"], str(st))
    check("dev: needs a kind", call(PORT, "/api/dev/activity", {"text": "x"})[0] == 400
          and call(PORT, "/api/dev/activity", ["tool"])[0] == 400)
    check("dev: needs the token", call(PORT, "/api/dev/activity", {"kind": "tool"}, token="")[0] == 403)

    # --- /api/orb
    st, orb = call(PORT, "/api/orb")
    check("orb: saying (str, <= 200), game (bool), pending (None while nothing waits), no mic",
          st == 200 and isinstance(orb.get("saying"), str) and len(orb["saying"]) <= 200
          and isinstance(orb.get("game"), bool) and "pending" in orb and orb["pending"] is None
          and "mic" not in orb and {"mode", "flavour", "level", "brain", "jobs"} <= set(orb),
          json.dumps({k: orb.get(k) for k in ("mode", "saying", "game", "pending")}))

    # --- /api/brain/match
    new.DB_FILE = BDIR / "memory.db"
    for qq in ["restart the cobblemon server", "orbit pitch deck intro", "zzzz qqqq"]:
        st, r = call(PORT, "/api/brain/match?q=" + urllib.parse.quote(qq))
        want = new.match(qq)
        check(f"match {qq!r}: {{ids, near, terms}} and exactly what recall would get",
              st == 200 and set(r) == {"ids", "near", "terms"} and r == want
              and all(isinstance(i, int) for i in r["ids"] + r["near"]) and not set(r["ids"]) & set(r["near"])
              and len(r["near"]) <= 12 and len(r["terms"]) <= 8,
              f"{len(r.get('ids', []))} ids, {len(r.get('near', []))} near, {len(r.get('terms', []))} terms")
    check("match: needs the token", call(PORT, "/api/brain/match?q=deck", token="")[0] == 403)

    # --- /api/brain/trail
    st, tr = call(PORT, "/api/brain/trail?days=30")
    by = {(x.get("kind"), x.get("target")): x for x in tr} if isinstance(tr, list) else {}
    blob = json.dumps(tr)
    check("trail: a list of {kind, target, uses, last}, most used first",
          st == 200 and isinstance(tr, list) and tr and all(set(x) == {"kind", "target", "uses", "last"} for x in tr)
          and all(x["kind"] in ("file", "folder", "site", "page", "tool") for x in tr)
          and [x["uses"] for x in tr] == sorted((x["uses"] for x in tr), reverse=True) and len(tr) <= 300, str(st))
    check("trail: the deck file twice (any case), home as ~, newest spelling",
          by.get(("file", "~/Downloads/deck/notes.md"), {}).get("uses") == 2 and tr[0]["target"] == "~/Downloads/deck/notes.md")
    check("trail: folder, sites, pages and tools",
          ("folder", "~/Downloads/deck") in by and ("site", "react.dev") in by and ("page", "react.dev/learn") in by
          and ("site", "example.com") in by and ("page", "example.com/a/b") in by and ("tool", "Bash") in by
          and ("tool", "spotify_play") in by and ("file", "D:/gone/missing.txt") in by, str(sorted(by))[:300])
    check("trail: no query strings, secrets masked, no bookkeeping tools",
          "?" not in blob and "abcdef123456" not in blob and "hunter2secret" not in blob and "[hidden]" in blob
          and "ToolSearch" not in blob, "")
    check("trail: only the last N days", ("file", "~/old/thing.md") not in by
          and ("file", "~/old/thing.md") in {(x["kind"], x["target"]) for x in call(PORT, "/api/brain/trail?days=90")[1]})
    check("trail: same as the function in-process", tr == local_trail)
    st, r = call(PORT, "/api/brain/trail?days=abc")
    check("trail: a junk days= falls back to 30", st == 200 and r == tr)
    check("trail: needs the token", call(PORT, "/api/brain/trail", token="")[0] == 403)
    check("the bridge ran as a test bridge (its log is in the test folder)", (BDIR / "bridge.log").is_file())
finally:
    proc.kill()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        pass

# ---- /api/dev/activity does not exist on a real bridge: the same handler,
# in-process, with NOVA_TEST unset for the one request (main() never runs,
# so nothing real starts).
print("dev endpoint on a bridge that is not a test bridge")
srv = ThreadingHTTPServer(("127.0.0.1", 0), nb.Handler)
srv.daemon_threads = True
srv.token = "brain-test-token-" + "7" * 16
srv.activity = nb.Activity()
threading.Thread(target=srv.serve_forever, daemon=True).start()
port2 = srv.server_address[1]
saved = os.environ.pop("NOVA_TEST")
try:
    st_real, body_real = call(port2, "/api/dev/activity", {"kind": "tool", "text": "x"}, token=srv.token)
finally:
    os.environ["NOVA_TEST"] = saved
st_test, _ = call(port2, "/api/dev/activity", {"kind": "tool", "text": "x"}, token=srv.token)
srv.shutdown()
srv.server_close()
check("dev: refused (404, like any unknown route) without NOVA_TEST", st_real == 404 and body_real == "Not found.",
      str(st_real))
check("dev: ...and the same handler takes it with NOVA_TEST", st_test == 200 and srv.activity.seq == 1)

# The copies of her memory and history go too. Windows will not delete a file
# that is still open: let go of stray connections and give the killed bridge
# a moment to finish exiting.
for _ in range(20):
    gc.collect()
    shutil.rmtree(TMP, ignore_errors=True)
    if not TMP.exists():
        break
    time.sleep(0.5)
if TMP.exists():
    print(f"  note: could not remove the test folder {TMP}")
print(f"\n{sum(checks)}/{len(checks)} checks passed")
print("RESULT:", "PASS" if all(checks) else "NEEDS WORK")
sys.exit(0 if all(checks) else 1)
