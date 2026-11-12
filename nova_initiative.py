#!/usr/bin/env python3
"""
nova_initiative.py -- the parts of Nova that notice things without being asked.

nova_events.py owns the loop and the manners; this file is what the loop
looks at. Everything here is a plain function or a small class with no
threads of its own, so each piece can be tested without a bridge.

  Presence   is Sir at the desk? (keyboard and mouse idle time, no camera)
  Orders     standing orders: "keep an eye on X", "tell me when Y"
  Context    what "this" means: the window in front, the clipboard, the screen
  Watchers   the headless PC and its tunnel, the GPU, pull requests
  Jobs       what a background worker is doing right now
  Loops      things left unfinished, so "where did we leave off" has an answer

    python nova_initiative.py idle
    python nova_initiative.py context "what is this error"
    python nova_initiative.py orders
    python nova_initiative.py gpu
    python nova_initiative.py prs owner/repo
"""
from __future__ import annotations

import ctypes
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path.home() / ".nova"
ORDERS_FILE = ROOT / "orders.json"
WATCH_STATE = ROOT / "watch-state.json"
NO_WINDOW = 0x08000000

sys.path.insert(0, str(ROOT))


# ----------------------------------------------------------------- presence

class _LASTINPUT(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def idle_seconds() -> float:
    """Seconds since the last key press or mouse movement, on any window."""
    try:
        info = _LASTINPUT()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        tick = ctypes.windll.kernel32.GetTickCount()
        return max(0.0, ((tick - info.dwTime) & 0xFFFFFFFF) / 1000.0)
    except Exception:                                 # noqa: BLE001
        return 0.0


def clipboard_seq() -> int:
    try:
        return int(ctypes.windll.user32.GetClipboardSequenceNumber())
    except Exception:                                 # noqa: BLE001
        return 0


class Presence:
    """Whether Sir is at the desk, from input idle time alone.

    step() is called every few seconds and returns "left" or "returned" on the
    tick where that changes, else "". It also notes when the clipboard last
    changed, which is how a stale clipboard is kept out of "what is this".
    """

    def __init__(self, away_minutes: float = 10, idle=idle_seconds, seq=clipboard_seq) -> None:
        self.away_after = float(away_minutes) * 60
        self._idle, self._seq = idle, seq
        self.away = False
        self.away_since = 0.0
        self.last_away = 0.0               # how long the absence that just ended lasted
        self.clip_seq = seq()
        self.clip_changed_at = 0.0

    def step(self, now: float | None = None) -> str:
        now = time.time() if now is None else now
        seq = self._seq()
        if seq != self.clip_seq:
            self.clip_seq, self.clip_changed_at = seq, now
        if self.away_after <= 0:
            return ""
        idle = self._idle()
        if not self.away and idle >= self.away_after:
            self.away, self.away_since = True, now - idle
            return "left"
        if self.away and idle < 5:
            self.away, self.last_away = False, now - self.away_since
            return "returned"
        return ""


def spoken_duration(seconds: float) -> str:
    mins = max(1, round(seconds / 60))
    if mins < 60:
        return f"{mins} minute{'s' if mins != 1 else ''}"
    hours = round(mins / 60, 1)
    hours = int(hours) if hours == int(hours) else hours
    return f"{hours} hour{'s' if hours != 1 else ''}"


# ------------------------------------------------------------------ context

# "This" only means the screen when it is the object of the sentence. A bare
# "that" is usually a conjunction, so the patterns are verbs and nouns around
# the pointer, not the pointer alone.
DEICTIC = re.compile(
    r"\bwhat(?:'s| is| are| does| do)?\s+(?:this|that|these|those)\b"
    r"|\b(?:fix|explain|read|summari[sz]e|translate|rewrite|check|debug|solve|answer|improve|"
    r"shorten|proofread|describe|identify|look at|help (?:me )?with|make sense of|reply to|"
    r"tell me about)\s+(?:this|that|these|those|it)\b"
    r"|\b(?:this|that)\s+(?:error|window|page|file|code|message|tab|video|image|picture|photo|"
    r"game|line|email|thing|chart|graph|article|post|site|website|document|doc|screen|text|"
    r"question|problem|popup|dialog|warning|crash|bug|function|script|sentence|paragraph)\b"
    r"|\bon (?:my|the) screen\b|\b(?:i'?m|i am|am i) (?:looking at|seeing|reading|watching)\b"
    r"|\bwhat am i (?:looking at|doing|working on|reading|watching)\b"
    r"|\b(?:selected|highlighted|clipboard|copied|pasted)\b|\bright here\b|\bin front of me\b",
    re.IGNORECASE)
CLIP_ONLY = re.compile(r"\b(?:clipboard|copied|pasted)\b", re.IGNORECASE)

# Anything shaped like a credential stays out of the prompt, whatever was asked.
SECRET = re.compile(
    r"sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----|\b[MN][A-Za-z0-9_-]{23,}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{20,}"
    r"|\b(?:password|passwd|secret|api[_-]?key|token)\s*[:=]\s*\S{6,}", re.IGNORECASE)


def looks_secret(text: str) -> bool:
    t = text.strip()
    if SECRET.search(t):
        return True
    # One long unbroken run of mixed characters is a key far more often than prose.
    return bool(re.fullmatch(r"[A-Za-z0-9_\-+/=.]{28,}", t)
                and re.search(r"\d", t) and re.search(r"[A-Za-z]", t))


def clipboard_text(limit: int = 1500) -> str:
    """The clipboard, if it holds text. Empty on anything else or on any failure."""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.GetClipboardData.restype = ctypes.c_void_p
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    text = ""
    for _ in range(3):                                # another app may hold it for a moment
        if user32.OpenClipboard(None):
            try:
                handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
                if handle:
                    ptr = kernel32.GlobalLock(handle)
                    if ptr:
                        try:
                            text = ctypes.wstring_at(ptr)
                        finally:
                            kernel32.GlobalUnlock(handle)
            finally:
                user32.CloseClipboard()
            break
        time.sleep(0.03)
    text = text.strip()
    return text[:limit] + (" ..." if len(text) > limit else "")


def front_window() -> dict:
    try:
        import nova_tools
        return json.loads(nova_tools.foreground_app())
    except Exception:                                 # noqa: BLE001
        return {}


def grab_screen() -> str:
    """Save a grab of every monitor to ~/.nova/captures and return its path."""
    folder = ROOT / "captures"
    folder.mkdir(exist_ok=True)
    path = folder / time.strftime("screen-%Y%m%d-%H%M%S.png")
    try:
        import mss
        import mss.tools
        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[0])
            mss.tools.to_png(shot.rgb, shot.size, output=str(path))
    except ImportError:
        from PIL import ImageGrab
        ImageGrab.grab(all_screens=True).save(str(path))
    return str(path)


def deictic_context(prompt: str, clip_changed_at: float = 0.0, now: float | None = None,
                    window=front_window, clip=clipboard_text, grab=grab_screen) -> str:
    """What "this" refers to, to travel with a request that points at something.

    Returns "" for any request that does not point. Otherwise: the window in
    front, the clipboard if it changed in the last ten minutes (or was asked
    for by name) and does not look like a credential, and the path of a fresh
    screenshot she may Read if the answer is on screen.
    """
    if not DEICTIC.search(prompt or ""):
        return ""
    now = time.time() if now is None else now
    lines = []
    w = window() or {}
    if w.get("app"):
        lines.append(f"- Window in front: {w['app']}" + (f" -- \"{str(w.get('title'))[:120]}\"" if w.get("title") else ""))
    asked_clip = bool(CLIP_ONLY.search(prompt))
    if asked_clip or (clip_changed_at and now - clip_changed_at < 600):
        try:
            text = clip()
        except Exception:                             # noqa: BLE001
            text = ""
        if text and looks_secret(text):
            lines.append("- Clipboard: holds something that looks like a password or key; it was "
                         "not passed to you. Say so if he asks about it.")
        elif text:
            lines.append("- Clipboard (he copied this recently):\n" + text)
    if not asked_clip or not lines:
        try:
            lines.append(f"- Screenshot of his screen right now: {grab()} -- Read it if what he "
                         "means is on screen.")
        except Exception:                             # noqa: BLE001
            pass
    if not lines:
        return ""
    return ("[What he may be pointing at -- use what fits, ignore the rest, never read this list out]\n"
            + "\n".join(lines))


# ------------------------------------------------------------------- orders

ORDER_PROMPT = """[A standing order Sir gave you {ago} ago. He is not asking now; you are checking on your own.]
The order: {what}

Check it now with your tools. Then reply with EXACTLY one of these and nothing else:
QUIET
TELL: one or two spoken sentences he should hear now (you carry on watching)
DONE: one or two spoken sentences -- the thing he was waiting for has happened, or the order is complete (you stop watching)

Say QUIET unless something has actually changed or needs him. {last}"""


class Orders:
    """Standing orders, kept on disk so a restart does not forget them."""

    MAX = 6
    MAX_RUNS = 200

    def __init__(self, path: Path = ORDERS_FILE) -> None:
        self.path = path
        self.lock = threading.Lock()

    def _load(self) -> list[dict]:
        try:
            items = json.loads(self.path.read_text(encoding="utf-8"))
            return items if isinstance(items, list) else []
        except (OSError, ValueError):
            return []

    def _save(self, items: list[dict]) -> None:
        try:
            self.path.write_text(json.dumps(items, indent=1), encoding="utf-8")
        except OSError:
            pass

    def add(self, what: str, every_minutes: float = 10, hours: float = 12,
            until_back: bool = False, min_minutes: float = 5, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        what = " ".join(str(what).split())[:400]
        if not what:
            return {"error": "Nothing to watch for."}
        with self.lock:
            items = self._load()
            if len(items) >= self.MAX:
                return {"error": f"I am already keeping {self.MAX} watches. Cancel one first."}
            every = max(float(min_minutes), float(every_minutes or 10)) * 60
            item = {"id": f"w{int(now * 10) % 1000000}", "what": what, "every": every,
                    "until": now + max(0.25, min(72.0, float(hours or 12))) * 3600,
                    "until_back": bool(until_back), "created": now, "last": now,
                    "runs": 0, "told": 0, "said": ""}
            items.append(item)
            self._save(items)
            return item

    def list(self) -> list[dict]:
        with self.lock:
            return self._load()

    def cancel(self, which: str) -> list[dict]:
        key = str(which).lower().strip()
        with self.lock:
            items = self._load()
            gone = [i for i in items if i["id"] == key or key in ("all", "everything")
                    or (key and key in i["what"].lower())]
            self._save([i for i in items if i not in gone])
            return gone

    def expire(self, now: float | None = None) -> list[dict]:
        """Drop orders that have run out of time or of checks."""
        now = time.time() if now is None else now
        with self.lock:
            items = self._load()
            gone = [i for i in items if i["until"] <= now or i["runs"] >= self.MAX_RUNS]
            if gone:
                self._save([i for i in items if i not in gone])
            return gone

    def end_until_back(self) -> list[dict]:
        """He is back: the orders that were only for while he was away are over."""
        with self.lock:
            items = self._load()
            gone = [i for i in items if i.get("until_back")]
            if gone:
                self._save([i for i in items if i not in gone])
            return gone

    def due(self, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        with self.lock:
            return [i for i in self._load() if now - i["last"] >= i["every"] and i["until"] > now]

    def mark(self, ident: str, now: float | None = None, said: str = "", done: bool = False) -> None:
        now = time.time() if now is None else now
        with self.lock:
            items = self._load()
            for i in items:
                if i["id"] == ident:
                    i["last"], i["runs"] = now, i["runs"] + 1
                    if said:
                        i["told"], i["said"] = i["told"] + 1, said[:300]
            if done:
                items = [i for i in items if i["id"] != ident]
            self._save(items)


def order_prompt(item: dict, now: float | None = None) -> str:
    now = time.time() if now is None else now
    last = (f"The last thing you told him about this was: \"{item['said']}\" -- do not repeat it."
            if item.get("said") else "")
    return ORDER_PROMPT.format(ago=spoken_duration(now - item["created"]), what=item["what"], last=last)


def parse_order_reply(text: str) -> tuple[str, bool]:
    """(what to say aloud or "", whether the order is finished)."""
    t = " ".join(str(text or "").split())
    m = re.search(r"\b(TELL|DONE)\s*:\s*(.+)$", t)
    if not m:
        return "", False                              # QUIET, or anything unrecognised: stay silent
    said = re.sub(r"\b(?:QUIET|TELL:|DONE:)\s*", "", m.group(2)).strip()
    return said[:400], m.group(1) == "DONE"


# ----------------------------------------------------------------- watchers

def gpu_temp() -> int | None:
    try:
        p = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=8, creationflags=NO_WINDOW,
                           stdin=subprocess.DEVNULL)
        return int(p.stdout.strip().splitlines()[0])
    except Exception:                                 # noqa: BLE001
        return None


STACK_LINE = re.compile(r"^\s*(minecraft|panel|playit tunnel)\b.*\b(UP|DOWN)\s*$", re.I | re.M)


def stack_state(status_text: str) -> dict:
    """{'minecraft': True, 'panel': True, 'tunnel': False} from `autostart.py --status`.
    Empty when the headless PC did not answer at all."""
    out = {}
    for name, state in STACK_LINE.findall(status_text or ""):
        out["tunnel" if "tunnel" in name.lower() else name.lower()] = state.upper() == "UP"
    return out


def gh_exe() -> str | None:
    from shutil import which
    fixed = Path(r"C:\Program Files\GitHub CLI\gh.exe")
    return str(fixed) if fixed.is_file() else which("gh")


def pr_snapshot(repo: str) -> dict | None:
    """{number: {title, state, comments, reviews}} for a repo's recent pull requests."""
    gh = gh_exe()
    if not gh or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo or ""):
        return None
    try:
        p = subprocess.run([gh, "pr", "list", "--repo", repo, "--state", "all", "--limit", "15",
                            "--json", "number,title,state,comments,reviews"],
                           capture_output=True, text=True, timeout=25, creationflags=NO_WINDOW,
                           stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace")
        rows = json.loads(p.stdout or "null")
    except Exception:                                 # noqa: BLE001
        return None
    if not isinstance(rows, list):
        return None
    return {str(r["number"]): {"title": str(r.get("title") or "")[:90], "state": r.get("state"),
                               "comments": len(r.get("comments") or []),
                               "reviews": len(r.get("reviews") or [])} for r in rows}


def pr_changes(old: dict | None, new: dict, repo: str) -> list[str]:
    """What is worth saying about a repo's pull requests since the last look."""
    if old is None:
        return []                                     # first look: learn, say nothing
    name = repo.split("/")[-1]
    out = []
    for num, pr in new.items():
        was = old.get(num)
        label = f"pull request {num} on {name}, {pr['title']},"
        if was is None:
            if pr["state"] == "OPEN":
                out.append(f"There is a new {label} waiting.")
            continue
        if was["state"] != pr["state"]:
            out.append(f"Sir, {label} has been " + {"MERGED": "merged", "CLOSED": "closed without merging",
                                                     "OPEN": "reopened"}.get(pr["state"], "updated") + ".")
        elif pr["reviews"] > was["reviews"]:
            out.append(f"Sir, {label} has a new review.")
        elif pr["comments"] > was["comments"]:
            out.append(f"Sir, {label} has a new comment.")
    return out[:3]


def load_state() -> dict:
    try:
        s = json.loads(WATCH_STATE.read_text(encoding="utf-8"))
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    try:
        WATCH_STATE.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------- jobs

VERBS = {"Edit": "editing", "Write": "writing", "MultiEdit": "editing", "NotebookEdit": "editing",
         "Read": "reading", "Grep": "searching the code", "Glob": "looking through the files",
         "Bash": "running a command", "WebSearch": "searching the web", "WebFetch": "reading a web page",
         "Task": "handing part of it to a helper", "Agent": "handing part of it to a helper",
         "TodoWrite": "updating its plan"}


def job_progress(out_path: str, tail_bytes: int = 400_000) -> dict:
    """{'steps': tool calls so far, 'doing': what the worker is on, 'said': its last words}
    read from a Claude worker's stream-json output. Empty for anything else."""
    try:
        with open(out_path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - tail_bytes))
            raw = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return {}
    steps, doing, said, fails = 0, "", "", 0
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        blocks = (ev.get("message") or {}).get("content") or []
        if ev.get("type") == "assistant":
            for b in blocks if isinstance(blocks, list) else []:
                if b.get("type") == "tool_use":
                    steps += 1
                    name = str(b.get("name") or "")
                    verb = VERBS.get(name, "using " + name.split("__")[-1].replace("_", " "))
                    target = (b.get("input") or {}).get("file_path") or ""
                    doing = verb + (f" {Path(str(target)).name}" if target and name in
                                    ("Edit", "Write", "MultiEdit", "Read", "NotebookEdit") else "")
                elif b.get("type") == "text" and str(b.get("text") or "").strip():
                    said = " ".join(str(b["text"]).split())
        elif ev.get("type") == "user":
            for b in blocks if isinstance(blocks, list) else []:
                if b.get("type") == "tool_result" and b.get("is_error"):
                    fails += 1
    if not steps and not said:
        return {}
    first = re.split(r"(?<=[.!?])\s", said)[0][:160] if said else ""
    return {"steps": steps, "doing": doing, "said": first, "fails": fails}


def progress_line(title: str, prog: dict, minutes: float) -> str:
    """One spoken sentence on a running job."""
    bits = [f"Sir, the job, {title}, is {prog['steps']} step{'s' if prog['steps'] != 1 else ''} in "
            f"after {spoken_duration(minutes * 60)}"]
    if prog.get("doing"):
        bits.append(f", currently {prog['doing']}")
    line = "".join(bits) + "."
    if prog.get("fails", 0) >= 3:
        line += f" It has hit {prog['fails']} errors along the way."
    return line


# -------------------------------------------------------------------- loops

LOOP_PATTERN = re.compile(r"\[\[\s*loop\s*:\s*(.+?)\s*\]\]", re.IGNORECASE | re.DOTALL)
DONE_PATTERN = re.compile(r"\[\[\s*done\s*:\s*(.+?)\s*\]\]", re.IGNORECASE | re.DOTALL)


def harvest_loops(reply: str, store: bool = True) -> tuple[str, list[str], list[str]]:
    """Pull [[loop: ...]] and [[done: ...]] markers out of a reply.

    Returns the reply without them, what was opened and what was closed. With
    store=False the markers are only stripped (a channel that may not write).
    """
    import nova_memory
    opened, closed = [], []
    for text in LOOP_PATTERN.findall(reply or ""):
        text = " ".join(text.split())
        if store and nova_memory.add(text, kind="loop", source="voice", topic=time.strftime("%Y-%m-%d")):
            opened.append(text)
    for text in DONE_PATTERN.findall(reply or ""):
        if store:
            closed += nova_memory.close_loop(" ".join(text.split()))
    cleaned = DONE_PATTERN.sub("", LOOP_PATTERN.sub("", reply or "")).strip()
    return cleaned, opened, closed


def loops_line(limit: int = 2) -> str:
    """A short spoken reminder of what is still open, or ""."""
    import nova_memory
    items = nova_memory.loops()
    if not items:
        return ""
    newest = [i["text"].rstrip(".") for i in items[-limit:]][::-1]
    if len(items) == 1:
        return f"One thing is still open: {newest[0]}."
    return f"{len(items)} things are still open, most recently: {newest[0]}."


# --------------------------------------------------------------------- main

def main() -> int:
    args = sys.argv[1:] or ["idle"]
    if args[0] == "idle":
        print(f"idle {idle_seconds():.0f}s, clipboard seq {clipboard_seq()}")
    elif args[0] == "context":
        print(deictic_context(" ".join(args[1:]) or "what is this", time.time()) or "(this request does not point at anything)")
    elif args[0] == "orders":
        for o in Orders().list():
            print(f"  {o['id']}  every {o['every'] / 60:.0f} min, {o['runs']} checks, "
                  f"until {time.strftime('%a %H:%M', time.localtime(o['until']))}: {o['what']}")
    elif args[0] == "gpu":
        print(gpu_temp())
    elif args[0] == "prs" and len(args) > 1:
        print(json.dumps(pr_snapshot(args[1]), indent=1))
    elif args[0] == "loops":
        print(loops_line() or "nothing open")
    return 0


if __name__ == "__main__":
    sys.exit(main())
