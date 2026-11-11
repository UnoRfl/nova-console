#!/usr/bin/env python3
"""
nova_events.py -- the part of Nova that speaks first, and the routines she runs.

Used by nova_bridge.py, which gives it a way to speak (`announce`) and a way
to think (`ask`).

  Reminders   "remind me in 20 minutes", "every day at 08:00 ..."
  Watchers    things she checks on her own: the Minecraft server going down
              or coming back, the disk filling up.
  Manners     she does not talk over a game or during quiet hours; anything
              that is not urgent waits and is delivered afterwards.
  Routines    named sequences ("game mode", "good night") that run instantly,
              without a round trip to a model.
  Self-check  on start-up she verifies her own parts and only speaks if
              something is wrong.

    python nova_events.py reminders
    python nova_events.py routines
    python nova_events.py selfcheck
"""
from __future__ import annotations

import ctypes
import json
import re
import shutil
import sys
import threading
import time
from pathlib import Path

ROOT = Path.home() / ".nova"
REMINDERS_FILE = ROOT / "reminders.json"
ROUTINES_DIR = ROOT / "routines"
CONFIG_FILE = ROOT / "config.json"

sys.path.insert(0, str(ROOT))

DEFAULTS = {
    "quiet_hours": "",            # e.g. "23:30-07:00"; empty = never quiet
    "watch_minecraft": True,
    "watch_disk": True,
    "disk_warn_percent": 8,
    "briefing_time": "",          # e.g. "08:00"; empty = no morning briefing
}

GAMES = {"robloxplayerbeta", "javaw", "valorant-win64-shipping", "valorant", "gamemd",
         "ra2", "fivem", "minecraft", "cs2", "mlbb"}


def cfg() -> dict:
    out = dict(DEFAULTS)
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        out.update({k: v for k, v in (raw.get("events") or {}).items() if k in DEFAULTS})
    except (OSError, ValueError, AttributeError):
        pass
    return out


# ------------------------------------------------------------------ manners

def foreground_process() -> str:
    try:
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
        h = kernel32.OpenProcess(0x1000, False, pid.value)
        if not h:
            return ""
        buf, size = ctypes.create_unicode_buffer(520), ctypes.c_ulong(520)
        ok = kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        kernel32.CloseHandle(h)
        return Path(buf.value).stem.lower() if ok else ""
    except Exception:                                 # noqa: BLE001
        return ""


def gaming() -> bool:
    return foreground_process() in GAMES


def in_quiet_hours(spec: str, now: time.struct_time | None = None) -> bool:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", spec or "")
    if not m:
        return False
    now = now or time.localtime()
    cur = now.tm_hour * 60 + now.tm_min
    start, end = int(m[1]) * 60 + int(m[2]), int(m[3]) * 60 + int(m[4])
    return (start <= cur < end) if start <= end else (cur >= start or cur < end)


# ---------------------------------------------------------------- reminders

def parse_when(text: str, now: float | None = None) -> tuple[float, str] | None:
    """'in 20 minutes' / 'at 14:30' / 'at 8 am' -> (epoch, daily 'HH:MM' or '')."""
    now = time.time() if now is None else now
    t = text.lower()
    m = re.search(r"\bin\s+(\d+|an?|half an)\s*(second|sec|minute|min|hour|hr)s?\b", t)
    if m:
        n = {"a": 1, "an": 1, "half an": 0.5}.get(m[1], None)
        n = float(m[1]) if n is None else n
        unit = {"s": 1, "m": 60, "h": 3600}[m[2][0]]
        return now + n * unit, ""
    m = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", t)
    if m:
        hour, minute = int(m[1]), int(m[2] or 0)
        if m[3] == "pm" and hour < 12:
            hour += 12
        if m[3] == "am" and hour == 12:
            hour = 0
        if hour > 23 or minute > 59:
            return None
        lt = time.localtime(now)
        target = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hour, minute, 0, 0, 0, -1))
        if target <= now:
            target += 86400
        daily = f"{hour:02d}:{minute:02d}" if re.search(r"\b(every ?day|daily|each day|every morning|every night)\b", t) else ""
        return target, daily
    return None


class Reminders:
    def __init__(self) -> None:
        self.lock = threading.Lock()

    def _load(self) -> list[dict]:
        try:
            return json.loads(REMINDERS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _save(self, items: list[dict]) -> None:
        try:
            REMINDERS_FILE.write_text(json.dumps(items, indent=1), encoding="utf-8")
        except OSError:
            pass

    def add(self, text: str, at: float, daily: str = "", kind: str = "say") -> dict:
        """kind 'say' speaks the text; 'ask' runs it through Nova as a request."""
        with self.lock:
            items = self._load()
            item = {"id": f"r{int(time.time() * 10) % 1000000}", "text": " ".join(text.split()),
                    "at": at, "daily": daily, "kind": kind}
            items.append(item)
            self._save(items)
            return item

    def cancel(self, ident_or_text: str) -> list[dict]:
        key = ident_or_text.lower().strip()
        with self.lock:
            items = self._load()
            gone = [i for i in items if i["id"] == key or (key and key in i["text"].lower())]
            self._save([i for i in items if i not in gone])
            return gone

    def list(self) -> list[dict]:
        with self.lock:
            return sorted(self._load(), key=lambda i: i["at"])

    def due(self, now: float | None = None) -> list[dict]:
        """Pop everything that is due; daily ones are rescheduled for tomorrow."""
        now = time.time() if now is None else now
        with self.lock:
            items = self._load()
            ready = [i for i in items if i["at"] <= now]
            if not ready:
                return []
            keep = [i for i in items if i["at"] > now]
            for i in ready:
                if i.get("daily"):
                    nxt = dict(i)
                    while nxt["at"] <= now:
                        nxt["at"] += 86400
                    keep.append(nxt)
            self._save(keep)
            return ready


# ----------------------------------------------------------------- routines

BUILT_IN = {
    "game-mode": {
        "say": "Game mode, Sir. I shall keep quiet unless it is urgent.",
        "triggers": ["game mode", "gaming mode", "i'm going to play", "time to play"],
        "steps": [{"set": {"game_mode": True}}, {"tool": "set_volume", "args": {"percent": 70}}],
    },
    "work-mode": {
        "say": "Work mode, Sir.",
        "triggers": ["work mode", "focus mode", "time to work"],
        "steps": [{"set": {"game_mode": False}}, {"tool": "set_volume", "args": {"percent": 35}}],
    },
    "good-night": {
        "say": "Good night, Sir. Screens are going dark.",
        "triggers": ["good night", "goodnight", "i'm going to bed", "going to sleep"],
        "steps": [{"tool": "media_key", "args": {"action": "Stop"}}, {"tool": "display_off", "args": {}}],
    },
    "status-report": {
        "say": "",
        "triggers": ["status report", "systems check", "how are we looking"],
        "steps": [{"ask": "Give me a short spoken status report: this PC's load and free disk, "
                          "whether the Minecraft server is up and who is on it, and any background "
                          "jobs. Use your tools. Three sentences at most."}],
    },
}


def load_routines() -> dict:
    """Built-ins, overridden or extended by JSON files in ~/.nova/routines."""
    out = {k: dict(v) for k, v in BUILT_IN.items()}
    if ROUTINES_DIR.is_dir():
        for f in ROUTINES_DIR.glob("*.json"):
            try:
                r = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(r.get("steps"), list):
                    out[f.stem] = r
            except (OSError, ValueError):
                continue
    return out


def match_routine(prompt: str) -> str | None:
    """A request that IS a routine's trigger phrase (not merely mentions it)."""
    t = re.sub(r"[^a-z' ]", "", prompt.lower()).strip()
    t = re.sub(r"^(?:nova|hey nova|please|okay|ok)\s+", "", t)
    t = re.sub(r"\s+(?:please|nova|now)$", "", t)
    t = re.sub(r"^(?:activate|start|run|enter|switch to|go to|do)\s+", "", t)
    for name, r in load_routines().items():
        for trig in r.get("triggers", []):
            if t == trig or t == name.replace("-", " "):
                return name
    return None


def save_routine(name: str, triggers: list[str], steps: list[dict], say: str = "") -> str:
    name = re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")
    if not name or not steps:
        return ""
    ROUTINES_DIR.mkdir(exist_ok=True)
    (ROUTINES_DIR / f"{name}.json").write_text(
        json.dumps({"say": say, "triggers": triggers, "steps": steps}, indent=1), encoding="utf-8")
    return name


# ----------------------------------------------------------------- the loop

class Events:
    """Owns the background loop. The bridge supplies the callbacks."""

    def __init__(self, announce, ask=None, activity=None) -> None:
        self._announce = announce          # (text) -> None, speaks now
        self._ask = ask                    # (prompt) -> None, runs a request and speaks the reply
        self.activity = activity
        self.reminders = Reminders()
        self.state = {"game_mode": False}
        self.held: list[str] = []          # things waiting for a quiet moment
        self.mc_up: bool | None = None
        self.disk_warned = ""
        self.briefed = ""
        self.lock = threading.Lock()

    # -- speaking, with manners
    def should_hold(self) -> str:
        c = cfg()
        if self.state.get("game_mode") or gaming():
            return "a game is running"
        if in_quiet_hours(c["quiet_hours"]):
            return "quiet hours"
        return ""

    def announce(self, text: str, urgent: bool = False) -> str:
        """Say it now, or hold it if this is a bad moment. Returns what happened."""
        why = "" if urgent else self.should_hold()
        if why:
            with self.lock:
                self.held.append(text)
            if self.activity:
                self.activity.push("system", "Held", f"{why}: {text[:120]}")
            return f"held ({why})"
        self._announce(text)
        return "spoken"

    def flush_held(self) -> None:
        with self.lock:
            if not self.held or self.should_hold():
                return
            items, self.held = self.held, []
        if len(items) == 1:
            self._announce("While you were busy, Sir: " + items[0])
        else:
            self._announce(f"While you were busy, Sir, {len(items)} things. " + " ".join(items))

    # -- routines
    def run_routine(self, name: str, collect_asks: bool = False) -> dict:
        """Run a routine. With collect_asks, "ask" steps are returned to the
        caller (who is already answering a request) instead of being run here."""
        r = load_routines().get(name)
        if not r:
            return {"error": "No such routine."}
        import nova_tools
        funcs = {f.__name__: f for f in nova_tools.ALL}
        done, skipped, asks = [], [], []
        for step in r["steps"]:
            if "set" in step:
                self.state.update(step["set"])
                done.append("state")
            elif "tool" in step:
                fn = funcs.get(step["tool"])
                # A routine may only use tools that never need a yes. Anything
                # confirm-tier has to go through Nova herself, where it is asked.
                if not fn or nova_tools.tier(step["tool"]) == "confirm":
                    skipped.append(step["tool"])
                    continue
                try:
                    fn(**(step.get("args") or {}))
                    done.append(step["tool"])
                except Exception as exc:              # noqa: BLE001
                    skipped.append(f"{step['tool']} ({type(exc).__name__})")
            elif "ask" in step:
                if collect_asks:
                    asks.append(step["ask"])
                elif self._ask:
                    self._ask(step["ask"])
                done.append("ask")
            elif "say" in step:
                self._announce(step["say"])
                done.append("say")
        return {"ok": True, "name": name, "say": r.get("say", ""), "done": done,
                "skipped": skipped, "asks": asks}

    # -- watchers
    def check_minecraft(self) -> None:
        import nova_tools
        out = nova_tools.server_status().lower()
        if "minecraft" not in out:
            return                                    # headless PC unreachable: say nothing
        m = re.search(r"minecraft[^\n]*\b(up|down)\b", out)
        if not m:
            return
        up = m[1] == "up"
        if self.mc_up is not None and up != self.mc_up:
            self.announce("Sir, the Minecraft server has " + ("come back up." if up else "gone down."),
                          urgent=not up)
        self.mc_up = up

    def check_disk(self) -> None:
        today = time.strftime("%Y-%m-%d")
        if self.disk_warned == today:
            return
        usage = shutil.disk_usage("C:\\")
        pct = usage.free * 100 / usage.total
        if pct < float(cfg()["disk_warn_percent"]):
            self.disk_warned = today
            self.announce(f"Sir, the main drive is down to about {int(pct)} percent free, "
                          f"roughly {int(usage.free / 2**30)} gigabytes.")

    def check_briefing(self) -> None:
        when = cfg()["briefing_time"]
        today = time.strftime("%Y-%m-%d")
        if not when or self.briefed == today or not self._ask:
            return
        if time.strftime("%H:%M") >= when and not self.should_hold():
            self.briefed = today
            self._ask("Give Sir his morning briefing, spoken, four sentences at most: the date, "
                      "whether the Minecraft server is up, any background jobs that finished, and "
                      "anything in your memory he asked to be reminded of today. Use your tools.")

    def loop(self) -> None:
        tick = 0
        while True:
            try:
                for item in self.reminders.due():
                    if item.get("kind") == "ask" and self._ask:
                        self._ask(item["text"])
                    else:
                        self.announce("Sir, a reminder: " + item["text"], urgent=True)
                self.flush_held()
                c = cfg()
                if tick % 12 == 0:                    # every two minutes
                    if c["watch_minecraft"]:
                        self.check_minecraft()
                    if c["watch_disk"]:
                        self.check_disk()
                    self.check_briefing()
            except Exception as exc:                  # noqa: BLE001 -- never let the loop die
                if self.activity:
                    self.activity.push("system", "Events error", f"{type(exc).__name__}: {exc}"[:160])
            tick += 1
            time.sleep(10)

    def start(self) -> None:
        threading.Thread(target=self.loop, daemon=True).start()


# --------------------------------------------------------------- self-check

def self_check() -> list[str]:
    """Problems worth mentioning at start-up. An empty list means all is well."""
    problems = []
    for name in ("silero_vad.onnx", "3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx",
                 "kokoro-v1.0.onnx", "voices-v1.0.bin"):
        if not (ROOT / "models" / name).is_file():
            problems.append(f"the model file {name} is missing")
    if not (ROOT / "voiceprint.npy").is_file():
        problems.append("your voice is not enrolled, so I cannot tell you from anyone else")
    try:
        import sounddevice as sd
        sd.query_devices(kind="input")
    except Exception:                                 # noqa: BLE001
        problems.append("I cannot find a microphone")
    try:
        import nova_harness
        if not nova_harness.claude_exe():
            problems.append("the Claude command line tool is not installed")
    except Exception:                                 # noqa: BLE001
        pass
    return problems


if __name__ == "__main__":
    cmd = (sys.argv[1:] or ["selfcheck"])[0]
    if cmd == "reminders":
        for r in Reminders().list():
            print(time.strftime("%Y-%m-%d %H:%M", time.localtime(r["at"])), r.get("daily") or "once", r["text"])
    elif cmd == "routines":
        for name, r in load_routines().items():
            print(f"  {name:14} triggers: {', '.join(r.get('triggers', []))}")
    else:
        print(self_check() or "all well")
