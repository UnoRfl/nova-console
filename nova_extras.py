#!/usr/bin/env python3
"""
nova_extras.py -- the things that make her act before being asked.

  briefing   a spoken summary when Sir sits down, put together with NO model:
             date, weather, classes, reminders, mail, finished jobs, loose ends
  health     memory, processor and drives watched quietly; a word only when a
             line is crossed
  rules      "when X happens, do Y": an app opening or closing, a service
             going down, a time of day, him coming back
  projects   one look across his GitHub repositories (pull requests, CI, last push)
  follow-up  an old loose end raised at a quiet moment
  screen     the text on screen by local OCR, so she reads instead of looking
  desk       clipboard, quick notes, recent files
  notes      record a meeting or lecture, transcribe it locally, summarise it
             with the local model. No Claude usage at all.

Everything here is free and runs on this PC, except the weather (open-meteo,
no key) and the project board (his own GitHub CLI).

    python nova_extras.py briefing | health | projects | screen | rules
"""
from __future__ import annotations

import ctypes
import json
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import wave
from pathlib import Path

ROOT = Path.home() / ".nova"
CONFIG_FILE = ROOT / "config.json"
RULES_FILE = ROOT / "rules.json"
STATE_FILE = ROOT / "extras-state.json"
NOTES_DIR = Path.home() / "Documents" / "Nova Notes"
NO_WINDOW = 0x08000000

sys.path.insert(0, str(ROOT))

DEFAULTS = {
    "briefing": "arrival",        # "arrival" = when he sits down for the day; "" = off
    "briefing_from": "05:00",     # never before this
    "briefing_gap_hours": 3,      # away this long, then back = a new sitting
    "briefing_gmail": True,       # count unread mail (opens her Chrome for a moment)
    "weather": {},                # {"lat": .., "lon": .., "place": ".."}; empty = look the city up once
    "watch_health": True,
    "ram_warn_percent": 90,
    "cpu_warn_percent": 92,
    "follow_ups": True,           # raise an old loose end at a quiet moment
    "follow_up_age_hours": 18,
    "follow_up_gap_hours": 4,
    "follow_ups_per_day": 2,
    "projects": [],               # ["owner/repo", ...] for the project board
}


def cfg() -> dict:
    out = dict(DEFAULTS)
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        ev = raw.get("events") or {}
        out.update({k: v for k, v in ev.items() if k in DEFAULTS})
        if isinstance(raw.get("projects"), list):
            out["projects"] = raw["projects"]
        out["_city"] = str((raw.get("school") or {}).get("city") or "")
        out["_mic"] = (raw.get("ears") or {}).get("input_device")
        out["_local"] = str(raw.get("local_model") or "")
        out["_watch_prs"] = list(ev.get("watch_prs") or [])
    except (OSError, ValueError, AttributeError):
        pass
    return out


def load_state() -> dict:
    try:
        s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, indent=1), encoding="utf-8")
    except OSError:
        pass


def _get_json(url: str, timeout: float = 8):
    req = urllib.request.Request(url, headers={"User-Agent": "nova-console"})
    for attempt in (1, 2):                            # the first connection of the day is often slow
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except OSError:
            if attempt == 2:
                raise


# ================================================================== briefing

ORDINAL = {1: "st", 2: "nd", 3: "rd", 21: "st", 22: "nd", 23: "rd", 31: "st"}
WMO = [((0,), "clear"), ((1, 2), "partly cloudy"), ((3,), "overcast"), ((45, 48), "foggy"),
       ((51, 53, 55, 56, 57), "drizzling"), ((61, 63, 80, 81), "raining"), ((65, 82), "raining hard"),
       ((71, 73, 75, 77, 85, 86), "snowing"), ((95, 96, 99), "stormy")]


def spoken_date(now: float | None = None) -> str:
    lt = time.localtime(time.time() if now is None else now)
    return time.strftime("%A the ", lt) + f"{lt.tm_mday}{ORDINAL.get(lt.tm_mday, 'th')}" + time.strftime(" of %B", lt)


def greeting(now: float | None = None) -> str:
    h = time.localtime(time.time() if now is None else now).tm_hour
    return "Good morning" if h < 12 else "Good afternoon" if h < 18 else "Good evening"


def weather_line(conf: dict | None = None, get=_get_json) -> str:
    """One sentence on today's weather, or "" (offline, or no place known)."""
    conf = conf or cfg()
    w = dict(conf.get("weather") or {})
    state = load_state()
    if not (w.get("lat") and w.get("lon")):
        w = state.get("weather_place") or {}
    if not (w.get("lat") and w.get("lon")):
        city = conf.get("_city") or ""
        if not city:
            return ""
        try:
            hit = (get("https://geocoding-api.open-meteo.com/v1/search?count=1&name="
                       + urllib.parse.quote(city)).get("results") or [None])[0]
        except Exception:                             # noqa: BLE001
            return ""
        if not hit:
            return ""
        w = {"lat": hit["latitude"], "lon": hit["longitude"], "place": hit.get("name", city)}
        state["weather_place"] = w
        save_state(state)
    try:
        d = get("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&timezone=auto"
                "&current=temperature_2m,weather_code&daily=temperature_2m_max,precipitation_probability_max"
                "&forecast_days=1" % (w["lat"], w["lon"]))
        now_c = round(d["current"]["temperature_2m"])
        code = int(d["current"]["weather_code"])
        high = round(d["daily"]["temperature_2m_max"][0])
        rain = int(d["daily"]["precipitation_probability_max"][0] or 0)
    except Exception:                                 # noqa: BLE001
        return ""
    sky = next((name for codes, name in WMO if code in codes), "")
    line = f"It is {now_c} degrees" + (f" and {sky}" if sky else "") + f", with a high of {high}"
    if rain >= 60:
        line += f" and rain likely, {rain} percent"
    elif rain >= 30:
        line += f" and a {rain} percent chance of rain"
    return line + "."


def _count(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def compose_briefing(parts: dict, now: float | None = None) -> str:
    """The briefing as speech, from facts already gathered. No model, no I/O.

    parts: weather(str) classes(list of str) no_school(str) reminders(list of str)
           unread(int|None) jobs(list of (title, state)) down(list of str)
           loops(str) projects(list of str)
    """
    out = [f"{greeting(now)}, Sir. It is {spoken_date(now)}."]
    if parts.get("weather"):
        out.append(parts["weather"])
    classes = parts.get("classes") or []
    if parts.get("no_school"):
        out.append(parts["no_school"])
    elif classes:
        out.append(("You have " + _count(len(classes), "class", "classes") + " today: "
                    + "; ".join(classes[:4]) + "."))
    rem = parts.get("reminders") or []
    if rem:
        out.append(("One reminder today: " if len(rem) == 1 else f"{len(rem)} reminders today: ")
                   + "; ".join(r.rstrip(".") for r in rem[:3]) + ".")
    unread = parts.get("unread")
    if unread:
        out.append(f"You have {_count(int(unread), 'unread email')}.")
    jobs = parts.get("jobs") or []
    done = [t for t, s in jobs if s == "done"]
    failed = [t for t, s in jobs if s == "failed"]
    if done:
        out.append(("The job " + done[0] + " finished" if len(done) == 1
                    else f"{len(done)} background jobs finished") + " while you were away.")
    if failed:
        out.append("The job " + failed[0] + " failed." if len(failed) == 1
                   else f"{len(failed)} background jobs failed.")
    down = parts.get("down") or []
    if down:
        out.append("Note that " + " and ".join(down) + (" is" if len(down) == 1 else " are") + " down.")
    for line in (parts.get("projects") or [])[:2]:
        out.append(line)
    if parts.get("loops"):
        out.append(parts["loops"])
    return " ".join(out)


def gather_briefing(events=None, since: float = 0.0, conf: dict | None = None) -> dict:
    """Collect the facts for a briefing. Every source is optional and may fail quietly."""
    conf = conf or cfg()
    now = time.time()
    parts: dict = {"weather": weather_line(conf)}
    try:
        import nova_schedule
        data = nova_schedule.load()
        lt = time.localtime(now)
        if nova_schedule.active_on(data, lt):
            parts["classes"] = [nova_schedule.describe(c) for c in nova_schedule.today(now)]
        elif nova_schedule.classes_on(data, lt.tm_wday):
            parts["no_school"] = "There is no school today."
    except Exception:                                 # noqa: BLE001
        pass
    if events is not None:
        try:
            end = time.mktime(time.strptime(time.strftime("%Y-%m-%d 23:59:59"), "%Y-%m-%d %H:%M:%S"))
            parts["reminders"] = [(r.get("title") or r["text"]) + time.strftime(" at %H:%M", time.localtime(r["at"]))
                                  for r in events.reminders.list()
                                  if now <= r["at"] <= end and not r.get("daily")]
        except Exception:                             # noqa: BLE001
            pass
        try:
            parts["jobs"] = [(j["title"], j["state"]) for j in (events.jobs.list() if events.jobs else [])
                             if j.get("ended") and j["ended"] > since and j["state"] in ("done", "failed")]
        except Exception:                             # noqa: BLE001
            pass
        names = {"minecraft": "the Minecraft server", "panel": "the server panel", "tunnel": "the playit tunnel"}
        parts["down"] = [names.get(k, k) for k, up in (getattr(events, "stack", {}) or {}).items() if not up]
        try:
            parts["loops"] = events.ni.loops_line()
        except Exception:                             # noqa: BLE001
            pass
    if conf.get("briefing_gmail"):
        try:
            import nova_tools
            r = nova_tools._hands("gmail_inbox", timeout=45, limit=1, unread_only=True)
            total = str(r.get("unread_total") or "")
            if total.isdigit():
                parts["unread"] = int(total)
        except Exception:                             # noqa: BLE001
            pass
    try:
        parts["projects"] = [f"CI is failing on {p['name']}." for p in project_board(conf).get("repos", [])
                             if p.get("ci") == "FAILURE"]
    except Exception:                                 # noqa: BLE001
        pass
    return parts


class Arrival:
    """Decides when Sir has sat down for the day. He has, when he is active now
    after `gap_hours` without touching anything (or this is the first look
    since she started) and it is past `briefing_from`. Once a day."""

    def __init__(self) -> None:
        self.last_active = 0.0
        self.pending = False

    def step(self, idle: float, now: float, conf: dict, briefed: str) -> bool:
        today = time.strftime("%Y-%m-%d", time.localtime(now))
        early = time.strftime("%H:%M", time.localtime(now)) < str(conf.get("briefing_from") or "05:00")
        active = idle < 60
        if active:
            gap = now - self.last_active if self.last_active else 1e9
            if gap >= float(conf.get("briefing_gap_hours", 3)) * 3600:
                # Sitting down at two in the morning is the end of yesterday,
                # not the start of today: it does not count, and it must not
                # turn into a "good morning" at five while he is still up.
                self.pending = not early
            self.last_active = now
        if briefed == today or conf.get("briefing") != "arrival" or early:
            if briefed == today:
                self.pending = False
            return False
        return bool(self.pending and active)


# ==================================================================== health

class _MEMSTAT(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def ram_percent() -> float:
    m = _MEMSTAT()
    m.dwLength = ctypes.sizeof(m)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return float(m.dwMemoryLoad)


def _cpu_times() -> tuple[int, int]:
    idle, kern, user = ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong()
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
    return idle.value, kern.value + user.value


def drives() -> list[tuple[str, float, float]]:
    """(letter, percent free, GB free) for every fixed drive."""
    import shutil
    out = []
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for i in range(26):
        if mask & (1 << i):
            root = f"{chr(65 + i)}:\\"
            if ctypes.windll.kernel32.GetDriveTypeW(root) == 3:      # DRIVE_FIXED
                try:
                    u = shutil.disk_usage(root)
                    out.append((root[0], u.free * 100 / u.total, u.free / 2**30))
                except OSError:
                    continue
    return out


def top_process(by: str = "ram") -> str:
    """The name of the heaviest process right now, or ""."""
    prop = "WorkingSet64" if by == "ram" else "CPU"
    try:
        p = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                            f"(Get-Process | Sort-Object {prop} -Descending | Select-Object -First 1).ProcessName"],
                           capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW,
                           stdin=subprocess.DEVNULL)
        return (p.stdout or "").strip().splitlines()[0][:40]
    except Exception:                                 # noqa: BLE001
        return ""


class Health:
    """Call step() every couple of minutes. It returns lines worth saying:
    a threshold has to hold for two looks in a row, and each kind of warning
    then stays quiet for an hour (a drive, for the day)."""

    def __init__(self, ram=ram_percent, cpu=_cpu_times, disks=drives, top=top_process) -> None:
        self._ram, self._cpu, self._disks, self._top = ram, cpu, disks, top
        self.prev_cpu: tuple[int, int] | None = None
        self.hot = {"ram": 0, "cpu": 0}
        self.said: dict[str, float] = {}
        self.last = {"ram": 0.0, "cpu": 0.0}

    def step(self, conf: dict | None = None, now: float | None = None) -> list[str]:
        conf = conf or cfg()
        now = time.time() if now is None else now
        out: list[str] = []
        ram = self._ram()
        cur = self._cpu()
        cpu = 0.0
        if self.prev_cpu:
            d_idle, d_busy = cur[0] - self.prev_cpu[0], cur[1] - self.prev_cpu[1]
            cpu = max(0.0, 100.0 * (1 - d_idle / d_busy)) if d_busy > 0 else 0.0   # kernel time includes idle
        self.prev_cpu = cur
        self.last = {"ram": round(ram), "cpu": round(cpu)}
        for key, value, limit, name in (("ram", ram, float(conf["ram_warn_percent"]), "Memory"),
                                        ("cpu", cpu, float(conf["cpu_warn_percent"]), "The processor")):
            self.hot[key] = self.hot[key] + 1 if value >= limit else 0
            if self.hot[key] >= 2 and now - self.said.get(key, 0) > 3600:
                self.said[key] = now
                hog = self._top(key)
                out.append(f"Sir, {name.lower() if key == 'cpu' else 'memory'} "
                           f"{'has been' if key == 'cpu' else 'is'} at {int(value)} percent"
                           + (f"; {hog} is using the most." if hog else "."))
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        for letter, pct, gb in self._disks():
            if letter == "C":
                continue                              # the main drive is nova_events.check_disk's
            if pct < 8 and self.said.get("disk" + letter) != day:
                self.said["disk" + letter] = day      # type: ignore[assignment]
                out.append(f"Sir, drive {letter} is down to {int(pct)} percent free, about {int(gb)} gigabytes.")
        return out


# ===================================================================== rules

class _PE32(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong), ("th32ProcessID", ctypes.c_ulong),
                ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", ctypes.c_ulong),
                ("cntThreads", ctypes.c_ulong), ("th32ParentProcessID", ctypes.c_ulong),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", ctypes.c_ulong),
                ("szExeFile", ctypes.c_wchar * 260)]


def process_names() -> set[str]:
    """Lower-case names (no .exe) of everything running. A few milliseconds."""
    k = ctypes.windll.kernel32
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k.Process32FirstW.argtypes = k.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PE32)]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    snap = k.CreateToolhelp32Snapshot(2, 0)
    out: set[str] = set()
    if not snap or snap == ctypes.c_void_p(-1).value:
        return out
    try:
        e = _PE32()
        e.dwSize = ctypes.sizeof(e)
        ok = k.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out.add(re.sub(r"\.exe$", "", e.szExeFile.lower()))
            ok = k.Process32NextW(snap, ctypes.byref(e))
    finally:
        k.CloseHandle(snap)
    return out


WHEN_KINDS = ("app_open", "app_close", "service_down", "service_up", "time", "back", "away")


def load_rules(path: Path | None = None) -> list[dict]:
    try:
        data = json.loads((path or RULES_FILE).read_text(encoding="utf-8"))
        return [r for r in data if isinstance(r, dict) and r.get("id")]
    except (OSError, ValueError):
        return []


def save_rules(rules: list[dict], path: Path | None = None) -> None:
    (path or RULES_FILE).write_text(json.dumps(rules, indent=1), encoding="utf-8")


def add_rule(name: str, when_kind: str, when_value: str, steps: list[dict], say: str = "",
             cooldown_minutes: float = 10, path: Path | None = None) -> dict:
    kind = (when_kind or "").strip().lower()
    if kind not in WHEN_KINDS:
        return {"error": "when_kind must be one of: " + ", ".join(WHEN_KINDS)}
    value = (when_value or "").strip().lower()
    if kind in ("app_open", "app_close"):
        value = re.sub(r"\.exe$", "", value)
    if kind == "time" and not re.fullmatch(r"\d{1,2}:\d{2}", value):
        return {"error": "for a time rule, when_value is HH:MM"}
    if kind in ("app_open", "app_close", "service_down", "service_up", "time") and not value:
        return {"error": "when_value is needed for this kind of rule"}
    steps = [s for s in (steps or []) if isinstance(s, dict)
             and any(k in s for k in ("tool", "ask", "say", "routine", "set", "brief"))]
    if not steps and not say:
        return {"error": "a rule needs at least one step or something to say"}
    rules = load_rules(path)
    ident = re.sub(r"[^a-z0-9]+", "-", (name or kind + "-" + value).lower()).strip("-")[:40] or "rule"
    rules = [r for r in rules if r["id"] != ident]
    rule = {"id": ident, "name": name or ident, "when": {"kind": kind, "value": value}, "steps": steps,
            "say": say, "cooldown_minutes": float(cooldown_minutes), "enabled": True, "last": 0.0,
            "made": time.time()}
    rules.append(rule)
    save_rules(rules, path)
    return rule


def delete_rule(which: str, path: Path | None = None) -> list[dict]:
    key = (which or "").lower().strip()
    rules = load_rules(path)
    gone = [r for r in rules if key and (r["id"] == key or key in r.get("name", "").lower() or key == "all")]
    if gone:
        save_rules([r for r in rules if r not in gone], path)
    return gone


def describe_rule(r: dict) -> str:
    w = r.get("when") or {}
    k, v = w.get("kind"), w.get("value")
    text = {"app_open": f"when {v} opens", "app_close": f"when {v} closes",
            "service_down": f"when {v} goes down", "service_up": f"when {v} comes back",
            "time": f"every day at {v}", "back": "when you come back", "away": "when you step away"}.get(k, str(k))
    return text


class Rules:
    """Edge-triggered: a rule fires on the change, never on the state, and the
    first look only learns what is already true."""

    def __init__(self, path: Path | None = None, procs=process_names) -> None:
        self.path = path
        self._procs = procs
        self.prev_procs: set[str] | None = None
        self.prev_stack: dict | None = None
        self.prev_minute = ""

    def step(self, stack: dict | None = None, presence_change: str = "", now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        rules = [r for r in load_rules(self.path) if r.get("enabled", True)]
        procs = self._procs() if any(r["when"]["kind"].startswith("app_") for r in rules) else set()
        minute = time.strftime("%H:%M", time.localtime(now))
        fired = []
        for r in rules:
            k, v = r["when"]["kind"], r["when"].get("value", "")
            hit = False
            if k == "app_open" and self.prev_procs is not None:
                hit = v in procs and v not in self.prev_procs
            elif k == "app_close" and self.prev_procs is not None:
                hit = v not in procs and v in self.prev_procs
            elif k in ("service_down", "service_up") and stack and self.prev_stack is not None:
                if v in stack and v in self.prev_stack and stack[v] != self.prev_stack[v]:
                    hit = stack[v] == (k == "service_up")
            elif k == "time":
                want = v if len(v) == 5 else "0" + v
                hit = minute == want and self.prev_minute != minute
            elif k == "back":
                hit = presence_change == "returned"
            elif k == "away":
                hit = presence_change == "left"
            if hit and now - float(r.get("last") or 0) >= float(r.get("cooldown_minutes", 10)) * 60:
                fired.append(r)
        if any(r["when"]["kind"].startswith("app_") for r in rules):
            self.prev_procs = procs
        else:
            self.prev_procs = None                    # nothing to compare against once a rule appears
        if stack:
            self.prev_stack = dict(stack)
        self.prev_minute = minute
        if fired:
            everything = load_rules(self.path)
            ids = {r["id"] for r in fired}
            for r in everything:
                if r["id"] in ids:
                    r["last"] = now
            save_rules(everything, self.path)
        return fired


# ================================================================== projects

_board = {"at": 0.0, "data": None, "lock": threading.Lock()}


def project_repos(conf: dict | None = None) -> list[str]:
    conf = conf or cfg()
    seen, out = set(), []
    for r in list(conf.get("projects") or []) + list(conf.get("_watch_prs") or []):
        if isinstance(r, str) and re.fullmatch(r"[\w.-]+/[\w.-]+", r) and r.lower() not in seen:
            seen.add(r.lower())
            out.append(r)
    return out[:12]


def project_board(conf: dict | None = None, fresh: bool = False, max_age: float = 600) -> dict:
    """{repos: [{name, repo, prs, issues, ci, last, when}], at}. One GraphQL call
    through his own GitHub CLI; cached for ten minutes."""
    repos = project_repos(conf)
    if not repos:
        return {"repos": [], "note": "No projects listed. Add \"projects\": [\"owner/repo\"] to config.json."}
    with _board["lock"]:
        if not fresh and _board["data"] and time.time() - _board["at"] < max_age:
            return _board["data"]
        import nova_initiative
        gh = nova_initiative.gh_exe()
        if not gh:
            return {"repos": [], "note": "The GitHub CLI is not installed."}
        fields = ("nameWithOwner pushedAt pullRequests(states:OPEN){totalCount} issues(states:OPEN){totalCount} "
                  "defaultBranchRef{target{... on Commit{messageHeadline committedDate statusCheckRollup{state}}}}")
        query = "query{" + " ".join(
            'r%d:repository(owner:"%s",name:"%s"){%s}' % (i, r.split("/")[0], r.split("/")[1], fields)
            for i, r in enumerate(repos)) + "}"
        try:
            p = subprocess.run([gh, "api", "graphql", "-f", "query=" + query], capture_output=True, text=True,
                               timeout=30, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL,
                               encoding="utf-8", errors="replace")
            data = (json.loads(p.stdout or "{}").get("data") or {})
        except Exception as exc:                      # noqa: BLE001
            return {"repos": [], "note": f"GitHub did not answer: {type(exc).__name__}"}
        rows = []
        for i, r in enumerate(repos):
            d = data.get(f"r{i}")
            if not d:
                rows.append({"repo": r, "name": r.split("/")[1], "missing": True})
                continue
            commit = ((d.get("defaultBranchRef") or {}).get("target") or {})
            rows.append({"repo": d.get("nameWithOwner") or r, "name": (d.get("nameWithOwner") or r).split("/")[1],
                         "prs": d["pullRequests"]["totalCount"], "issues": d["issues"]["totalCount"],
                         "ci": ((commit.get("statusCheckRollup") or {}).get("state") or ""),
                         "last": str(commit.get("messageHeadline") or "")[:80],
                         "when": commit.get("committedDate") or d.get("pushedAt") or ""})
        rows.sort(key=lambda x: x.get("when") or "", reverse=True)
        out = {"repos": rows, "at": time.time()}
        _board["data"], _board["at"] = out, time.time()
        return out


def ago(iso: str, now: float | None = None) -> str:
    try:
        import calendar
        t = calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return ""
    s = max(0, (time.time() if now is None else now) - t)
    if s < 3600:
        return f"{int(s // 60) or 1} min ago"
    if s < 86400:
        return f"{int(s // 3600)} h ago"
    return f"{int(s // 86400)} d ago"


def board_text(board: dict) -> str:
    if not board.get("repos"):
        return board.get("note") or "No projects."
    lines = []
    for p in board["repos"]:
        if p.get("missing"):
            lines.append(f"{p['name']}: not reachable")
            continue
        ci = {"SUCCESS": "CI passing", "FAILURE": "CI FAILING", "ERROR": "CI error", "PENDING": "CI running"}.get(p["ci"], "no CI")
        lines.append(f"{p['name']}: {_count(p['prs'], 'open PR')}, {ci}, last commit {ago(p['when'])}: {p['last']}")
    return "\n".join(lines)


def ci_changes(old: dict | None, board: dict) -> list[str]:
    """Lines to say when a project's CI has just started failing, or recovered."""
    if old is None:
        return []
    out = []
    for p in board.get("repos", []):
        was = old.get(p["repo"])
        now_bad = p.get("ci") in ("FAILURE", "ERROR")
        if was is None:
            continue
        if now_bad and not was:
            out.append(f"Sir, CI has started failing on {p['name']}: {p.get('last', '')}.")
        elif was and p.get("ci") == "SUCCESS":
            out.append(f"Sir, CI on {p['name']} is passing again.")
    return out[:3]


# ================================================================= follow-up

def pick_loop(loops: list[dict], raised: dict, conf: dict, now: float | None = None) -> dict | None:
    """The loose end to raise now, or None. Oldest first; only ones that have sat
    for `follow_up_age_hours`; never the same one twice inside three days."""
    now = time.time() if now is None else now
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    stamps = [t for t in raised.values() if isinstance(t, (int, float))]
    if sum(1 for t in stamps if time.strftime("%Y-%m-%d", time.localtime(t)) == today) >= int(conf["follow_ups_per_day"]):
        return None
    if stamps and now - max(stamps) < float(conf["follow_up_gap_hours"]) * 3600:
        return None
    for item in loops:
        try:
            born = time.mktime(time.strptime(str(item["at"])[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S"))
        except (ValueError, KeyError):
            born = 0.0
        if now - born < float(conf["follow_up_age_hours"]) * 3600:
            continue
        if now - float(raised.get(str(item["id"])) or 0) < 3 * 86400:
            continue
        return item
    return None


def loop_line(item: dict, now: float | None = None) -> str:
    now = time.time() if now is None else now
    try:
        born = time.mktime(time.strptime(str(item["at"])[:10], "%Y-%m-%d"))
        days = int((now - born) // 86400)
    except (ValueError, KeyError):
        days = 0
    when = "from yesterday" if days <= 1 else f"from {time.strftime('%A', time.localtime(born))}" if days < 7 else f"from {days} days ago"
    return f"Sir, a loose end {when}: {item['text'].rstrip('.')}. Tell me if it is done."


# ==================================================================== screen

OCR_PS = r"""
param([string]$Path)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if (-not $engine) { 'NO-OCR-LANGUAGE'; exit 2 }
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$result.Lines | ForEach-Object { $_.Text }
"""


def _front_rect() -> tuple[int, int, int, int] | None:
    """The foreground window in real pixels (left, top, right, bottom)."""
    class RECT(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    if not hwnd:
        return None
    r = RECT()
    ok = ctypes.windll.dwmapi.DwmGetWindowAttribute(ctypes.c_void_p(hwnd), 9, ctypes.byref(r), ctypes.sizeof(r)) == 0
    if not ok or r.r - r.l < 60 or r.b - r.t < 40:
        return None
    return r.l, r.t, r.r, r.b


def grab(whole: bool = False) -> str:
    """Save the window in front (or every screen) as a PNG; returns its path."""
    import mss
    import mss.tools
    folder = ROOT / "captures"
    folder.mkdir(exist_ok=True)
    path = folder / time.strftime("read-%Y%m%d-%H%M%S.png")
    with mss.mss() as sct:
        box = None if whole else _front_rect()
        if box:
            big = sct.monitors[0]
            left, top = max(box[0], big["left"]), max(box[1], big["top"])
            right = min(box[2], big["left"] + big["width"])
            bottom = min(box[3], big["top"] + big["height"])
            region = {"left": left, "top": top, "width": max(1, right - left), "height": max(1, bottom - top)}
        else:
            region = sct.monitors[0]
        shot = sct.grab(region)
        mss.tools.to_png(shot.rgb, shot.size, output=str(path))
    return str(path)


def ocr(path: str, timeout: int = 40) -> str:
    """Text in an image, by Windows' own offline OCR."""
    script = ROOT / "tools" / "Read-Text.ps1"
    try:
        if not script.is_file() or script.read_text(encoding="utf-8") != OCR_PS:
            script.parent.mkdir(exist_ok=True)
            script.write_text(OCR_PS, encoding="utf-8")
        p = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-File", str(script), "-Path", path], capture_output=True, timeout=timeout,
                           creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
    except Exception as exc:                          # noqa: BLE001
        return f"(could not read the screen: {type(exc).__name__})"
    text = (p.stdout or b"").decode("utf-8", "replace").strip()
    if p.returncode != 0 and not text:
        return "(could not read the screen: " + (p.stderr or b"").decode("utf-8", "replace").strip()[:200] + ")"
    return text


def read_screen(whole: bool = False, max_chars: int = 6000) -> dict:
    path = grab(whole)
    text = ocr(path)
    return {"text": text[:max_chars], "truncated": len(text) > max_chars, "image": path}


# ====================================================================== desk

def clipboard_write(text: str) -> bool:
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    data = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(data)
    for _ in range(4):
        if user32.OpenClipboard(None):
            try:
                user32.EmptyClipboard()
                h = kernel32.GlobalAlloc(0x0002, size)
                ptr = kernel32.GlobalLock(h)
                ctypes.memmove(ptr, data, size)
                kernel32.GlobalUnlock(h)
                return bool(user32.SetClipboardData(13, h))
            finally:
                user32.CloseClipboard()
        time.sleep(0.04)
    return False


def save_note(text: str, title: str = "", folder: Path | None = None, now: float | None = None) -> str:
    """Append a note to this month's file in Documents/Nova Notes; returns the path."""
    folder = folder or NOTES_DIR
    folder.mkdir(parents=True, exist_ok=True)
    lt = time.localtime(time.time() if now is None else now)
    path = folder / time.strftime("notes-%Y-%m.md", lt)
    head = time.strftime("## %a %d %b, %H:%M", lt) + (f" -- {title.strip()}" if title.strip() else "")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"\n{head}\n\n{text.strip()}\n")
    return str(path)


PLACES = {"downloads": "Downloads", "desktop": "Desktop", "documents": "Documents",
          "pictures": "Pictures", "videos": "Videos", "music": "Music"}


def recent_files(where: str = "downloads", days: float = 2, contains: str = "", limit: int = 15,
                 now: float | None = None) -> list[dict]:
    """Newest files in one of his folders (or a full path), newest first."""
    now = time.time() if now is None else now
    key = (where or "downloads").strip().lower()
    folder = Path.home() / PLACES[key] if key in PLACES else Path(where).expanduser()
    if not folder.is_dir():
        return []
    want = contains.lower().strip()
    rows = []
    try:
        for f in folder.iterdir():
            try:
                st = f.stat()
            except OSError:
                continue
            if now - st.st_mtime > days * 86400 or (want and want not in f.name.lower()):
                continue
            rows.append({"name": f.name + ("\\" if f.is_dir() else ""), "path": str(f),
                         "when": time.strftime("%a %H:%M", time.localtime(st.st_mtime)),
                         "mb": round(st.st_size / 2**20, 1), "at": st.st_mtime})
    except OSError:
        return []
    rows.sort(key=lambda r: r["at"], reverse=True)
    return rows[:limit]


# ===================================================================== notes

def local_summary(text: str, title: str = "", model: str = "", url: str = "http://127.0.0.1:11434") -> str:
    """Summarise with the local model (Ollama). Free; "" if it is not running."""
    model = model or cfg().get("_local") or "llama3.2:3b"

    def ask(prompt: str, n: int = 420) -> str:
        body = json.dumps({"model": model, "stream": False, "keep_alive": "10m",
                           "messages": [{"role": "user", "content": prompt}],
                           "options": {"temperature": 0.2, "num_predict": n}}).encode("utf-8")
        req = urllib.request.Request(url + "/api/chat", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            return ((json.loads(r.read().decode("utf-8")).get("message") or {}).get("content") or "").strip()

    ask_for = ("Below is a transcript of a recording%s. Reply in exactly two parts and nothing else.\n"
               "Key points:\n- every fact, definition or decision, one short bullet each\n"
               "Actions:\n- every task, homework, deadline, date or thing to bring or prepare, one bullet each, "
               "with its due date if one was said. Write '- none' only if there truly are none.\n"
               "Use only what is in the transcript. Do not add an introduction.\n\nTranscript:\n"
               % (f" titled '{title}'" if title else ""))
    try:
        chunks = [text[i:i + 7000] for i in range(0, len(text), 7000)][:12]
        parts = [ask(ask_for + c) for c in chunks]
        if len(parts) == 1:
            return parts[0]
        return ask("Merge these partial summaries of one recording into a single set of bullet points and one "
                   "'Actions:' line. Remove repeats.\n\n" + "\n\n".join(parts), 600)
    except Exception:                                 # noqa: BLE001
        return ""


class Notes:
    """Record the microphone to a WAV, then transcribe and summarise it here.

    One recording at a time. Her ears keep listening while this runs: Windows
    shares the microphone. Nothing leaves the PC and no Claude usage is spent.
    """

    RATE = 16000

    def __init__(self, announce=None, log=None, folder: Path | None = None) -> None:
        self.announce, self.log = announce, (log or (lambda _m: None))
        self.folder = folder or NOTES_DIR
        self.lock = threading.Lock()
        self.stream = None
        self.wav: wave.Wave_write | None = None
        self.path: Path | None = None
        self.title = ""
        self.started = 0.0
        self.working = ""                             # what is being processed after a stop
        self.last: dict = {}

    def status(self) -> dict:
        return {"recording": self.stream is not None, "title": self.title,
                "seconds": round(time.time() - self.started) if self.stream else 0,
                "working": self.working, "last": self.last}

    def start(self, title: str = "") -> dict:
        with self.lock:
            if self.stream is not None:
                return {"error": "Already recording " + (self.title or "notes") + "."}
            try:
                import sounddevice as sd
            except ImportError as exc:
                return {"error": f"sounddevice is not installed: {exc}"}
            self.folder.mkdir(parents=True, exist_ok=True)
            self.title = " ".join((title or "").split())[:80] or time.strftime("Notes %d %b %H.%M")
            safe = re.sub(r'[\\/:*?"<>|]+', " ", self.title).strip() or "notes"
            self.path = self.folder / (time.strftime("%Y-%m-%d %H.%M ") + safe + ".wav")
            wav = wave.open(str(self.path), "wb")
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.RATE)
            self.wav = wav

            def on_audio(indata, _frames, _time, _status):
                try:
                    wav.writeframes(bytes(indata))
                except Exception:                     # noqa: BLE001 -- closed under us on stop
                    pass

            try:
                self.stream = sd.RawInputStream(samplerate=self.RATE, channels=1, dtype="int16",
                                                device=cfg().get("_mic"), blocksize=1600, callback=on_audio)
                self.stream.start()
            except Exception as exc:                  # noqa: BLE001
                self.stream = None
                wav.close()
                self.path.unlink(missing_ok=True)
                return {"error": f"The microphone would not open: {type(exc).__name__}: {exc}"}
            self.started = time.time()
            self.log(f"notes: recording '{self.title}' to {self.path.name}")
            return {"ok": True, "title": self.title}

    def stop(self, keep_audio: bool = False) -> dict:
        with self.lock:
            if self.stream is None:
                return {"error": "I am not recording anything."}
            try:
                self.stream.stop()
                self.stream.close()
            finally:
                self.stream = None
            self.wav.close()
            path, title, secs = self.path, self.title, time.time() - self.started
            self.working = title
        threading.Thread(target=self._process, args=(path, title, secs, keep_audio), daemon=True).start()
        return {"ok": True, "title": title, "minutes": round(secs / 60, 1)}

    def _process(self, path: Path, title: str, secs: float, keep_audio: bool) -> None:
        try:
            text = transcribe_wav(path)
            if len(text.split()) < 4:
                # Nothing was said (or it was stopped at once): no file, no
                # memory, and no announcement about empty notes.
                path.unlink(missing_ok=True)
                self.last = {"title": title, "empty": True}
                self.log(f"notes: '{title}' was empty; discarded")
                if secs > 20 and self.announce:
                    self.announce(f"Sir, the recording for {title} had no speech in it, so I kept nothing.")
                return
            out = path.with_suffix(".md")
            summary = local_summary(text, title) if len(text) > 200 else ""
            mins = max(1, round(secs / 60))
            body = [f"# {title}", "", time.strftime("%A %d %B %Y, %H:%M") + f" -- about {mins} min", ""]
            if summary:
                body += ["## Summary", "", summary, ""]
            body += ["## Transcript", "", text or "(nothing was said)", ""]
            out.write_text("\n".join(body), encoding="utf-8")
            if not keep_audio:
                path.unlink(missing_ok=True)
            self.last = {"title": title, "file": str(out), "minutes": mins, "words": len(text.split()),
                         "summary": bool(summary)}
            self.log(f"notes: '{title}' done, {len(text.split())} words -> {out.name}")
            try:
                import nova_memory
                nova_memory.add(f"Notes from '{title}' ({time.strftime('%Y-%m-%d')}) are saved at {out}",
                                kind="fact", source="notes")
            except Exception:                         # noqa: BLE001
                pass
            if self.announce:
                self.announce(f"Sir, your notes on {title} are ready: about {mins} minute{'s' if mins != 1 else ''}, "
                              + ("with a summary, " if summary else "") + "saved in Nova Notes.")
        except Exception as exc:                      # noqa: BLE001
            self.log(f"notes: failed: {type(exc).__name__}: {exc}")
            if self.announce:
                self.announce(f"Sir, I could not finish the notes on {title}. The recording is kept.")
        finally:
            self.working = ""


def transcribe_wav(path: Path) -> str:
    """A whole recording to text with local Whisper: the GPU if it is free, else the CPU."""
    import numpy as np
    with wave.open(str(path), "rb") as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    if audio.size < 8000:
        return ""
    model = None
    try:
        import os
        import nova_stt
        import nova_words
        from faster_whisper import WhisperModel
        # The pip-installed NVIDIA libraries have to be findable before the GPU
        # model runs (same as the ears), and loading succeeds even when one is
        # missing: only a trial run proves the GPU path works.
        site = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
        for sub in (site.glob("*/bin") if site.is_dir() else []):
            try:
                os.add_dll_directory(str(sub))
            except OSError:
                pass
            if str(sub) not in os.environ.get("PATH", ""):
                os.environ["PATH"] = str(sub) + os.pathsep + os.environ.get("PATH", "")
        try:
            model = WhisperModel(nova_stt.config_model(), device="cuda", compute_type="float16")
            list(model.transcribe(np.zeros(16000, dtype=np.float32), language="en")[0])
        except Exception:                             # noqa: BLE001
            model = nova_stt.load()
        segments, _info = model.transcribe(audio, language="en", beam_size=1, vad_filter=True,
                                           vad_parameters={"min_silence_duration_ms": 600},
                                           condition_on_previous_text=False, initial_prompt=nova_words.prompt())
        paras, cur, last_end = [], [], 0.0
        for seg in segments:
            if cur and seg.start - last_end > 2.5:    # a real pause starts a new paragraph
                paras.append(" ".join(cur))
                cur = []
            cur.append(seg.text.strip())
            last_end = seg.end
        if cur:
            paras.append(" ".join(cur))
        return nova_words.fix("\n\n".join(paras))
    finally:
        del model


NOTES_START = re.compile(r"^(?:please )?(?:start|begin) (?:taking |recording |making )?(?:the |some |a )?"
                         r"(?:notes?|meeting notes|lecture notes|recording)(?: (?:on|for|of|about|called) (.+))?$")
NOTES_STOP = re.compile(r"^(?:please )?(?:stop|end|finish) (?:taking |recording |the |those )*"
                        r"(?:notes?|recording|lecture|meeting)$|^that is the end of the (?:lecture|meeting)$")
BRIEF_ME = re.compile(r"^(?:please )?(?:brief me|give me (?:my|the) (?:morning )?briefing|morning briefing|"
                      r"(?:my|the) briefing|what did i miss)$")


def quick_command(prompt: str) -> tuple[str, str] | None:
    """('notes_start', title) / ('notes_stop', '') / ('brief', '') for a request that
    is nothing but that, so it needs no model. None for everything else."""
    said = " ".join(re.sub(r"[^a-z0-9' ]+", " ", (prompt or "").lower().replace("’", "'")).split())
    said = re.sub(r"^(?:hey |okay |ok )?nova\s+", "", said)
    said = re.sub(r"\s+(?:please|nova|now)$", "", said)
    if not said or len(said) > 90:
        return None
    m = NOTES_START.match(said)
    if m:
        return "notes_start", (m.group(1) or "").strip()
    if NOTES_STOP.match(said):
        return "notes_stop", ""
    if BRIEF_ME.match(said):
        return "brief", ""
    return None


# ====================================================================== main

def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    what = sys.argv[1] if len(sys.argv) > 1 else "briefing"
    if what == "briefing":
        print(compose_briefing(gather_briefing(conf={**cfg(), "briefing_gmail": False})))
    elif what == "health":
        h = Health()
        h.step()
        time.sleep(1.5)
        print(h.step(), h.last, drives())
    elif what == "projects":
        print(board_text(project_board()))
    elif what == "screen":
        print(json.dumps(read_screen(), indent=1)[:3000])
    elif what == "rules":
        for r in load_rules():
            print(r["id"], "--", describe_rule(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
