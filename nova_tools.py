#!/usr/bin/env python3
"""
nova_tools.py -- one tool layer for every brain.

An MCP server. Claude Code loads it with --mcp-config, Codex with its own
config entry, so whichever model is answering has the same hands.

    python nova_tools.py                 # serve over stdio (what the engines launch)
    python nova_tools.py --list          # print every tool and its tier
    python nova_tools.py --call pc_status
    python nova_tools.py --call recall query="minecraft server"

TIERS is the single table the approval gate reads:

    free      looking, listening, reading: never asks
    announce  small and reversible: done, then said aloud
    confirm   Sir would notice and could not easily undo it: asks first

A tool that is not in TIERS is treated as "confirm".
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path.home() / ".nova"
TOOLS = ROOT / "tools"
NO_WINDOW = 0x08000000

sys.path.insert(0, str(ROOT))
import nova_memory  # noqa: E402

TIERS = {
    "pc_status": "free", "list_windows": "free", "foreground_app": "free",
    "capture_screen": "free", "volume_status": "free",
    "recall": "free", "day_log": "free",
    "server_status": "free", "minecraft_players": "free",
    "say": "free",
    "set_volume": "announce", "mute": "announce", "media_key": "announce",
    "focus_window": "announce", "remember": "announce",
    "minecraft_say": "announce", "minecraft_save": "announce",
    "forget": "confirm", "close_window": "confirm",
    "lock_pc": "confirm", "sleep_pc": "confirm", "display_off": "announce",
    "minecraft_restart": "confirm", "minecraft_stop": "confirm",
    "minecraft_start": "announce",
    "list_reminders": "free", "list_routines": "free",
    "remind": "announce", "cancel_reminder": "announce", "run_routine": "announce",
    "save_routine": "confirm",    # a new standing behaviour: Sir approves it first
    "list_jobs": "free", "job_result": "free", "engines_available": "free",
    "cancel_job": "announce",
    "start_job": "confirm",       # spends usage and may edit files: Sir says yes first
}

MC_DIR = r"C:\Users\You\Downloads\mcserver"      # on the headless PC


# ------------------------------------------------------------------ helpers

def run_ps(script: str, *args: str, timeout: int = 40) -> str:
    cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
           "-File", str(TOOLS / script), *args]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           creationflags=NO_WINDOW, stdin=subprocess.DEVNULL,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return f"{script} timed out after {timeout}s"
    out = (p.stdout or "").strip()
    return out if out else (p.stderr or "").strip()[:600] or "(no output)"


def run_headless(command: str, timeout: int = 30) -> str:
    """Run one command on the headless PC over SSH (key auth, never prompts)."""
    try:
        p = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", "headless", command],
            capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW,
            stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return "The headless PC did not answer in time."
    except OSError as exc:
        return f"ssh is not available: {exc}"
    out = (p.stdout or "").strip() or (p.stderr or "").strip()
    return out[:3000] or "(no output)"


def bridge(path: str, body: dict | None = None, timeout: float = 60) -> dict:
    try:
        token = (ROOT / "token").read_text(encoding="utf-8").strip()
        m = re.search(r":(\d+)/", (ROOT / "url.txt").read_text(encoding="utf-8"))
        port = int(m.group(1)) if m else 8765
        if os.environ.get("NOVA_PORT"):                # tests use a private bridge
            port = int(os.environ["NOVA_PORT"])
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", data=data,
            headers={"X-Nova-Token": token, "Content-Type": "application/json"},
            method="POST" if body is not None else "GET")
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8") or "{}")
    except Exception as exc:                          # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


# -------------------------------------------------------------------- tools
# Plain functions first, so they can be tested without any MCP machinery;
# they are registered with the server at the bottom of the file.

def pc_status() -> str:
    """CPU load, memory, disks, GPU, uptime and the heaviest processes on this PC."""
    return run_ps("Get-System.ps1")


def list_windows() -> str:
    """Every visible window with its app, state and memory use."""
    return run_ps("Get-Windows.ps1")


def foreground_app() -> str:
    """Which window Sir is looking at right now, and whether it is full screen (a game)."""
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(300)
    user32.GetWindowTextW(hwnd, buf, 300)
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    name = "unknown"
    try:
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)
        if h:
            pbuf = ctypes.create_unicode_buffer(520)
            size = ctypes.c_ulong(520)
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, pbuf, ctypes.byref(size)):
                name = Path(pbuf.value).stem
            ctypes.windll.kernel32.CloseHandle(h)
    except OSError:
        pass

    class RECT(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    full = (r.r - r.l) >= user32.GetSystemMetrics(0) and (r.b - r.t) >= user32.GetSystemMetrics(1)
    games = {"robloxplayerbeta", "javaw", "valorant-win64-shipping", "gamemd", "ra2", "fivem", "minecraft"}
    gaming = name.lower() in games
    return json.dumps({"app": name, "title": buf.value, "fullscreen": bool(full), "game": gaming})


def capture_screen(window: str = "") -> str:
    """Take a screenshot and return its file path. Read that path to actually see it.
    Pass a window name (for example "discord") to capture just that window."""
    return run_ps("Get-Screen.ps1", *(["-Window", window] if window else ["-Primary"]))


def volume_status() -> str:
    """Current volume and mute state."""
    return run_ps("Set-System.ps1")


def set_volume(percent: int) -> str:
    """Set the system volume, 0 to 100."""
    return run_ps("Set-System.ps1", "-Volume", str(max(0, min(100, int(percent)))))


def mute(on: bool = True) -> str:
    """Mute or unmute the system sound."""
    return run_ps("Set-System.ps1", "-Mute" if on else "-Unmute")


def media_key(action: str) -> str:
    """Press a media key: PlayPause, Next, Previous or Stop."""
    action = {"playpause": "PlayPause", "next": "Next", "previous": "Previous",
              "stop": "Stop"}.get(str(action).lower().replace(" ", ""))
    return run_ps("Set-System.ps1", "-Media", action) if action else "Unknown media key."


def focus_window(name: str) -> str:
    """Bring a window to the front by part of its title or app name."""
    return run_ps("Get-Windows.ps1", "-Focus", name)


def close_window(name: str) -> str:
    """Ask a window to close (it may still prompt about unsaved work)."""
    return run_ps("Get-Windows.ps1", "-Close", name)


def display_off() -> str:
    """Blank the monitors."""
    return run_ps("Set-System.ps1", "-DisplayOff")


def lock_pc() -> str:
    """Lock the workstation."""
    return run_ps("Set-System.ps1", "-Lock")


def sleep_pc() -> str:
    """Put the PC to sleep."""
    return run_ps("Set-System.ps1", "-Sleep")


def remember(fact: str) -> str:
    """Store a durable fact about Sir, his projects or his preferences."""
    ident = nova_memory.add(fact, source="tool")
    return f"Stored as memory {ident}." if ident else "Already known, or empty."


def recall(query: str) -> str:
    """Search long-term memory and the project notes."""
    hits = nova_memory.search(query, limit=6)
    if not hits:
        return "Nothing relevant in memory."
    return "\n".join(f"- [{h['kind']}{' ' + h['topic'] if h['topic'] else ''}] {h['text'][:500]}"
                     for h in hits)


def forget(query: str) -> str:
    """Delete the best-matching remembered fact."""
    gone = nova_memory.forget_matching(query)
    return f"Forgot: {gone[0]}" if gone else "Nothing matching to forget."


def day_log(date: str = "") -> str:
    """What was asked and answered on a date (YYYY-MM-DD; default today)."""
    import time
    lines = nova_memory.day(date or time.strftime("%Y-%m-%d"))
    return "\n".join(lines) if lines else "Nothing logged for that day."


def say(text: str) -> str:
    """Speak a line aloud through Nova's voice, outside the normal reply."""
    r = bridge("/api/speak", {"text": text})
    return "Spoken." if r.get("ok") else f"Could not speak: {r.get('error')}"


def server_status() -> str:
    """Is the headless PC up, and are the Minecraft server, its panel and the tunnel running?"""
    return run_headless(f'cd /d "{MC_DIR}" && python autostart.py --status')


def minecraft_players() -> str:
    """Who is online on the Minecraft server."""
    return run_headless(f'cd /d "{MC_DIR}" && python mcctl.py cmd "list"')


def minecraft_say(message: str) -> str:
    """Post a message in the Minecraft server chat."""
    safe = re.sub(r'["\r\n&|<>^%]', " ", message)[:200]
    return run_headless(f'cd /d "{MC_DIR}" && python mcctl.py cmd "say {safe}"')


def minecraft_save() -> str:
    """Flush the Minecraft world to disk."""
    return run_headless(f'cd /d "{MC_DIR}" && python mcctl.py save')


def minecraft_start() -> str:
    """Start whatever part of the Minecraft stack is down. Does nothing to what is already up."""
    return run_headless(f'cd /d "{MC_DIR}" && python autostart.py', timeout=90)


def minecraft_stop() -> str:
    """Save and cleanly stop the Minecraft server. Everyone online is disconnected."""
    return run_headless(f'cd /d "{MC_DIR}" && python mcctl.py stop', timeout=90)


def minecraft_restart() -> str:
    """Save, stop and start the Minecraft server. Everyone online is disconnected."""
    return run_headless(f'cd /d "{MC_DIR}" && python mcctl.py restart', timeout=180)


def start_job(task: str, engine: str = "claude", folder: str = "", title: str = "") -> str:
    """Hand a long piece of work to a background worker and keep talking.
    engine is "claude" (Claude Code) or "codex" (ChatGPT). folder is where it works.
    Write the task as a complete, self-contained brief: the worker knows nothing
    about this conversation. Nova announces the result when it finishes."""
    r = bridge("/api/jobs", {"task": task, "engine": engine, "folder": folder or None, "title": title})
    if r.get("error"):
        return f"Could not start it: {r['error']}"
    return f"Started job {r['id']} on {r['engine']} in {r['folder']}."


def list_jobs() -> str:
    """Background jobs, newest first, with their state."""
    r = bridge("/api/jobs")
    jobs = r.get("jobs") or []
    if not jobs:
        return "No background jobs."
    return "\n".join(f"- {j['id']} [{j['state']}] {j['engine']}: {j['title']}" for j in jobs)


def job_result(job_id: str) -> str:
    """What a background job reported when it finished."""
    r = bridge("/api/jobs/result", {"id": job_id})
    if r.get("error"):
        return r["error"]
    return f"[{r['state']}] {r['title']}\n{r.get('result') or '(no output yet)'}"


def cancel_job(job_id: str) -> str:
    """Stop a running background job."""
    r = bridge("/api/jobs/cancel", {"id": job_id})
    return "Cancelled." if r.get("ok") else str(r.get("error"))


def engines_available() -> str:
    """Which brains Nova can hand work to right now."""
    r = bridge("/api/jobs")
    return json.dumps(r.get("engines") or {})


def remind(text: str, when: str, daily: bool = False, as_request: bool = False) -> str:
    """Set a reminder. when is like "in 20 minutes" or "at 17:30". With as_request,
    the text is run as a request to Nova at that time instead of merely being spoken
    (for example "check whether the render finished and tell me")."""
    r = bridge("/api/reminders", {"text": text, "when": when, "daily": daily, "as_request": as_request})
    if r.get("error"):
        return r["error"]
    import time
    return "Reminder set for " + time.strftime("%H:%M", time.localtime(r["at"])) + (" daily." if r.get("daily") else ".")


def list_reminders() -> str:
    """Pending reminders."""
    import time
    items = bridge("/api/reminders").get("reminders") or []
    return "\n".join(f"- {time.strftime('%a %H:%M', time.localtime(i['at']))} "
                     f"{'(daily) ' if i.get('daily') else ''}{i['text']}" for i in items) or "No reminders."


def cancel_reminder(which: str) -> str:
    """Cancel a reminder by its id or by words from its text."""
    gone = bridge("/api/reminders/cancel", {"which": which}).get("cancelled") or []
    return f"Cancelled {len(gone)} reminder(s)." if gone else "No matching reminder."


def list_routines() -> str:
    """Named routines and the phrases that trigger them."""
    r = bridge("/api/routines").get("routines") or {}
    return "\n".join(f"- {k}: {', '.join(v)}" for k, v in r.items()) or "No routines."


def run_routine(name: str) -> str:
    """Run a named routine now."""
    r = bridge("/api/routines/run", {"name": name})
    return r.get("error") or f"Ran {name}: {', '.join(r.get('done') or [])}" + (
        f"; skipped {', '.join(r['skipped'])}" if r.get("skipped") else "")


def save_routine(name: str, triggers: list[str], steps: list[dict], say: str = "") -> str:
    """Teach Nova a new routine. steps is a list like
    [{"tool": "set_volume", "args": {"percent": 30}}, {"say": "Done."}, {"ask": "a request to run"}].
    Only tools that never need confirmation will run inside a routine."""
    r = bridge("/api/routines/save", {"name": name, "triggers": triggers, "steps": steps, "say": say})
    return f"Saved routine {r.get('name')}." if r.get("ok") else "Could not save that routine."


ALL = [remind, list_reminders, cancel_reminder, list_routines, run_routine, save_routine, start_job, list_jobs, job_result, cancel_job, engines_available, pc_status, list_windows, foreground_app, capture_screen, volume_status, set_volume,
       mute, media_key, focus_window, close_window, display_off, lock_pc, sleep_pc,
       remember, recall, forget, day_log, say,
       server_status, minecraft_players, minecraft_say, minecraft_save,
       minecraft_start, minecraft_stop, minecraft_restart]


def approve(tool_name: str, input: dict, tool_use_id: str = "") -> str:
    """Permission check. The CLI calls this before any action that is not plainly
    read-only. Not for the model to call."""
    import nova_gate
    verdict = nova_gate.classify(tool_name, input)
    if verdict["tier"] in ("free", "announce"):
        return json.dumps({"behavior": "allow", "updatedInput": input})
    r = bridge("/api/approve", {"tool": tool_name, "detail": verdict["detail"],
                                "why": verdict["why"]}, timeout=60)
    if r.get("allow"):
        return json.dumps({"behavior": "allow", "updatedInput": input})
    reason = r.get("reason") or r.get("error") or "No approval was given."
    return json.dumps({"behavior": "deny",
                       "message": f"Sir did not approve this ({reason}). Do not retry it; "
                                  "tell him plainly that it was not done."})


def tier(name: str) -> str:
    return TIERS.get(name.split("__")[-1], "confirm")


# --------------------------------------------------------------------- main

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = sys.argv[1:]

    if args[:1] == ["--list"]:
        for fn in ALL:
            doc = (fn.__doc__ or "").strip().splitlines()[0]
            print(f"  {tier(fn.__name__):8} {fn.__name__:20} {doc}")
        return 0

    if args[:1] == ["--call"] and len(args) >= 2:
        fn = {f.__name__: f for f in ALL}.get(args[1])
        if not fn:
            print("no such tool")
            return 1
        kwargs = {}
        for a in args[2:]:
            k, _, v = a.partition("=")
            kwargs[k] = (v.lower() == "true") if v.lower() in ("true", "false") else (int(v) if v.isdigit() else v)
        print(fn(**kwargs))
        return 0

    from mcp.server.mcpserver import MCPServer
    server = MCPServer("nova")
    for fn in ALL + [approve]:
        server.tool()(fn)
    server.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
