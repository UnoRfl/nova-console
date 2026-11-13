#!/usr/bin/env python3
"""
nova_harness.py -- what turns Nova from one chat session into a harness.

Three pieces, used by nova_bridge.py:

  SentenceStreamer   turns the model's token stream into whole sentences, so
                     speech can start before the reply has finished.
  Usage              a per-day tally per engine, with spoken warnings before a
                     limit is hit rather than silent failures after.
  Jobs               background workers. Long tasks run in their own engine
                     process -- Claude Code or Codex -- while Nova stays free
                     to talk, and she announces when each one finishes.

    python nova_harness.py engines      # which brains are available here
    python nova_harness.py jobs         # the job table
    python nova_harness.py usage        # today's tally
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from shutil import which

ROOT = Path.home() / ".nova"
JOBS_FILE = ROOT / "jobs.json"
USAGE_FILE = ROOT / "usage.json"
JOBS_DIR = ROOT / "jobs"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# ------------------------------------------------------------ sentence stream

BLOCK_START = re.compile(r"::(?:CAD|MODEL|HOLO|BUILD)\b|\[\[\s*(?:remember|loop|done)\b", re.IGNORECASE)
SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]?\s+")


class SentenceStreamer:
    """Feed it text deltas; it calls `emit` with each completed sentence.

    Everything from a projector block (::CAD ...) or a memory marker
    ([[remember: ...]]) onwards is withheld: those are for the screen and the
    database, never for the voice.
    """

    def __init__(self, emit) -> None:
        self.emit = emit
        self.buf = ""
        self.muted = False
        self.count = 0

    def push(self, delta: str) -> None:
        if self.muted or not delta:
            return
        self.buf += delta
        m = BLOCK_START.search(self.buf)
        if m:
            self._out(self.buf[:m.start()])
            self.buf, self.muted = "", True
            return
        # Hold back a tail that might be the start of a marker ("::" or "[[").
        safe_end = len(self.buf)
        for lead in ("::", "[[", ":", "["):
            if self.buf.endswith(lead):
                safe_end = len(self.buf) - len(lead)
                break
        head = self.buf[:safe_end]
        last = None
        for hit in SENTENCE_END.finditer(head):
            last = hit
        if last:
            self._out(head[:last.end()])
            self.buf = self.buf[last.end():]

    def flush(self) -> None:
        if not self.muted:
            self._out(self.buf)
        self.buf = ""

    def _out(self, text: str) -> None:
        text = text.strip()
        if text:
            self.count += 1
            self.emit(text)


# --------------------------------------------------------------------- usage

class Usage:
    """How much each engine has been used today.

    The subscriptions do not expose a remaining balance, so this counts what
    Nova herself spends: requests, and the dollar-equivalent the Claude CLI
    reports per turn. The ceilings are yours to set in config.json under
    "budget"; crossing 80%, 95% and 100% is announced once each per day. Past
    100% the bridge stops sending requests to Claude and routes to Codex or
    the local model instead (Backend.over_budget), rather than refusing.
    """

    DEFAULT = {"claude_daily_usd": 6.0, "codex_daily_jobs": 20}

    def __init__(self, cfg: dict | None = None) -> None:
        self.limits = dict(self.DEFAULT)
        self.limits.update((cfg or {}).get("budget") or {})
        self.lock = threading.Lock()

    def _load(self) -> dict:
        try:
            data = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        today = time.strftime("%Y-%m-%d")
        if data.get("date") != today:
            data = {"date": today, "engines": {}, "warned": []}
        return data

    def add(self, engine: str, cost: float = 0.0, requests: int = 1) -> str | None:
        """Record use. Returns a sentence to say aloud if a threshold was just crossed."""
        with self.lock:
            data = self._load()
            e = data["engines"].setdefault(engine, {"requests": 0, "usd": 0.0})
            e["requests"] += requests
            e["usd"] = round(e["usd"] + float(cost or 0), 4)
            warn = None
            if engine == "claude":
                frac = e["usd"] / max(0.01, float(self.limits["claude_daily_usd"]))
                for mark in (1.0, 0.95, 0.8):
                    key = f"claude-{int(mark * 100)}"
                    if frac >= mark and key not in data["warned"]:
                        data["warned"] += [f"claude-{m}" for m in (80, 95, 100) if m <= mark * 100
                                           and f"claude-{m}" not in data["warned"]]
                        warn = ("Sir, today's Claude allowance is used up. I will carry on with the "
                                "other brains until midnight; they are slower at real work."
                                if mark == 1.0 else
                                f"Sir, I have used about {int(frac * 100)} percent of "
                                "today's Claude allowance.")
                        break
            try:
                USAGE_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")
            except OSError:
                pass
            return warn

    def today(self) -> dict:
        with self.lock:
            data = self._load()
        data["limits"] = self.limits
        return data

    def over(self, engine: str = "claude") -> bool:
        e = self.today()["engines"].get(engine, {})
        return engine == "claude" and e.get("usd", 0) >= float(self.limits["claude_daily_usd"])


# ------------------------------------------------------------------- engines

def claude_exe() -> str | None:
    npm = Path.home() / "AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
    if npm.is_file():
        return str(npm)
    found = which("claude")
    return found if found and found.lower().endswith(".exe") else None


def codex_exe() -> list[str] | None:
    """Codex CLI as an argv prefix. Run via node so no unsigned shim is needed."""
    js = Path.home() / "AppData/Roaming/npm/node_modules/@openai/codex/bin/codex.js"
    node = which("node")
    if js.is_file() and node:
        return [node, str(js)]
    found = which("codex")
    return [found] if found and found.lower().endswith(".exe") else None


def clean_env() -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.upper().startswith(("ANTHROPIC_", "CLAUDE_CODE_")):
            env.pop(key, None)
    return env


OLLAMA = "http://127.0.0.1:11434"
_local_cache = {"at": 0.0, "model": None}
_serve_tried = {"at": 0.0}


def ensure_ollama() -> bool:
    """Start `ollama serve` if Ollama is installed but not answering.

    Without it the router's free "chat" tier silently falls back to Claude,
    so small talk spends tokens. The tray app is not always running after a
    reboot, so Nova starts the server herself (hidden, detached), at most
    once every five minutes. Returns True if a start was attempted."""
    import urllib.request
    if time.time() - _serve_tried["at"] < 300:
        return False
    try:
        urllib.request.urlopen(OLLAMA + "/api/version", timeout=1.5).close()
        return False                                  # already up
    except Exception:                                 # noqa: BLE001
        pass
    exe = which("ollama") or str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe")
    if not Path(exe).is_file():
        return False
    _serve_tried["at"] = time.time()
    try:
        subprocess.Popen([exe, "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True,
                         creationflags=0x08000000 | 0x00000008 | 0x00000200)  # no window, detached, new group
    except OSError:
        return False
    _local_cache["at"] = 0.0                          # look again on the next request
    return True


def local_model() -> str | None:
    """The local model to use, or None if Ollama is not running or has no models.
    config.json "local_model" picks one; otherwise the first installed."""
    import urllib.request
    if time.time() - _local_cache["at"] < 60:
        return _local_cache["model"]
    model = None
    try:
        with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=2) as r:
            names = [m["name"] for m in json.loads(r.read().decode()).get("models", [])]
        want = None
        try:
            want = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig")).get("local_model")
        except (OSError, ValueError):
            pass
        model = want if want in names else (names[0] if names else None)
    except Exception:                                 # noqa: BLE001
        model = None
    _local_cache.update(at=time.time(), model=model)
    if model is None and not os.environ.get("NOVA_TEST"):
        ensure_ollama()                               # free brain down: bring it up for next time
    return model


def ollama_chat(model: str, messages: list[dict], on_delta) -> str | None:
    """Stream a chat reply from the local model. Returns an error string, or None."""
    import urllib.request
    body = json.dumps({"model": model, "messages": messages, "stream": True,
                       "keep_alive": "30m", "options": {"temperature": 0.6, "num_predict": 220}})
    req = urllib.request.Request(OLLAMA + "/api/chat", data=body.encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            for line in r:
                if not line.strip():
                    continue
                msg = json.loads(line.decode("utf-8"))
                piece = (msg.get("message") or {}).get("content") or ""
                if piece:
                    on_delta(piece)
                if msg.get("done"):
                    break
    except Exception as exc:                          # noqa: BLE001
        return f"local model error: {type(exc).__name__}: {exc}"
    return None


def engines() -> dict:
    return {
        "claude": {"available": bool(claude_exe()), "how": "Claude Code CLI, your Claude subscription"},
        "codex": {"available": bool(codex_exe()), "how": "Codex CLI, your ChatGPT subscription"},
        "local": {"available": bool(local_model()), "how": f"Ollama on this PC ({local_model() or 'no model installed'}); talks, cannot act"},
    }


# ---------------------------------------------------------------------- jobs

class Jobs:
    """Background workers, one engine process each."""

    MAX_RUNNING = 2

    def __init__(self, on_done=None, default_dir: str | None = None) -> None:
        self.on_done = on_done
        self.default_dir = default_dir or str(Path.home())
        self.lock = threading.Lock()
        self.procs: dict[str, subprocess.Popen] = {}
        self.jobs: dict[str, dict] = {}
        try:
            self.jobs = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.jobs = {}
        # Anything still marked running belongs to a previous bridge process.
        for j in self.jobs.values():
            if j["state"] == "running":
                j["state"], j["result"] = "lost", "Nova was restarted while this was running."
        self._save()

    def _save(self) -> None:
        try:
            keep = dict(sorted(self.jobs.items(), key=lambda kv: kv[1]["started"])[-40:])
            JOBS_FILE.write_text(json.dumps(keep, indent=1), encoding="utf-8")
        except OSError:
            pass

    def command(self, engine: str, task: str, model: str) -> list[str] | None:
        if engine == "claude":
            exe = claude_exe()
            if not exe:
                return None
            # Full access, same as Nova herself (Sir, 2026-11-13): edits go
            # through, and everything else (shell included) is put to Nova's
            # own gate, which only refuses the never-list. acceptEdits alone
            # silently refused every shell command in a -p job.
            # stream-json, not json: one event per line as the worker goes,
            # which is what lets Nova say how far along it is. The last line
            # is still the result object _watch reads.
            args = [exe, "-p", task, "--output-format", "stream-json", "--verbose",
                    "--model", model or "sonnet"]
            mcp = ROOT / "mcp.json"
            if mcp.is_file():
                # Edits go through approve() too (it allows them: ask_first is off),
                # so the never-list and the taint hold apply to job edits as well.
                # acceptEdits used to wave every Write/Edit in $HOME straight past it.
                args += ["--mcp-config", str(mcp), "--strict-mcp-config",
                         "--permission-prompt-tool", "mcp__nova__approve"]
            else:
                args += ["--permission-mode", "acceptEdits"]
            return args
        if engine == "codex":
            exe = codex_exe()
            if not exe:
                return None
            # Codex's default exec sandbox is read-only. Full access, with the
            # never-list and the Meta rule stated up front (Codex has no gate).
            rules = ("Rules: never wipe or format a disk, touch diskpart or bcdedit, delete a "
                     "drive root or the home folder, or edit SSH keys, tokens, passwords or "
                     "voiceprint files. Anything by Meta (Facebook, Messenger, Instagram, "
                     "Threads, WhatsApp) is read-only: never send, post, reply or change anything there.\n\n")
            return exe + ["exec", "--skip-git-repo-check", "-s", "danger-full-access", rules + task]
        return None

    def start(self, task: str, engine: str = "claude", model: str = "sonnet",
              folder: str | None = None, title: str = "") -> dict:
        task = " ".join(str(task).split())
        if not task:
            return {"error": "No task given."}
        with self.lock:
            running = [j for j in self.jobs.values() if j["state"] == "running"]
            if len(running) >= self.MAX_RUNNING:
                return {"error": f"{self.MAX_RUNNING} jobs are already running. Wait or cancel one."}
            cwd = folder or self.default_dir
            if not Path(cwd).is_dir():
                return {"error": f"No such folder: {cwd}"}
            cmd = self.command(engine, task, model)
            if not cmd:
                return {"error": f"The {engine} engine is not installed or not signed in."}
            ident = time.strftime("j%m%d-%H%M%S")
            JOBS_DIR.mkdir(exist_ok=True)
            out_path = JOBS_DIR / f"{ident}.out"
            job = {"id": ident, "engine": engine, "model": model if engine == "claude" else "",
                   "task": task, "title": title or task[:60], "folder": cwd,
                   "state": "running", "started": time.time(), "ended": None,
                   "result": "", "out": str(out_path)}
            try:
                fh = out_path.open("w", encoding="utf-8", errors="replace")
                proc = subprocess.Popen(cmd, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
                                        env=clean_env())
            except OSError as exc:
                return {"error": f"Could not start the job: {exc}"}
            self.jobs[ident] = job
            self.procs[ident] = proc
            self._save()
        threading.Thread(target=self._watch, args=(ident, proc, fh), daemon=True).start()
        return job

    def _watch(self, ident: str, proc: subprocess.Popen, fh) -> None:
        code = proc.wait()
        fh.close()
        with self.lock:
            job = self.jobs.get(ident)
            if not job:
                return
            raw = ""
            try:
                raw = Path(job["out"]).read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
            result, cost = raw.strip()[-4000:], 0.0
            try:                                     # Claude prints one JSON object
                obj = json.loads(raw.strip().splitlines()[-1])
                result = str(obj.get("result") or result)[-4000:]
                cost = float(obj.get("total_cost_usd") or 0)
                if obj.get("is_error"):
                    code = code or 1
            except (ValueError, IndexError, AttributeError, TypeError):
                pass
            if job["state"] == "running":
                job["state"] = "done" if code == 0 else "failed"
            job["result"], job["ended"], job["cost"] = result, time.time(), cost
            self.procs.pop(ident, None)
            self._save()
        if self.on_done:
            try:
                self.on_done(dict(job))
            except Exception:                         # noqa: BLE001
                pass

    def cancel(self, ident: str) -> dict:
        with self.lock:
            proc, job = self.procs.get(ident), self.jobs.get(ident)
            if not job:
                return {"error": "No such job."}
            if not proc or job["state"] != "running":
                return {"error": f"That job is already {job['state']}."}
            job["state"] = "cancelled"
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, creationflags=NO_WINDOW)
        except OSError:
            proc.kill()
        return {"ok": True, "id": ident}

    def list(self, limit: int = 10) -> list[dict]:
        with self.lock:
            jobs = sorted(self.jobs.values(), key=lambda j: j["started"], reverse=True)[:limit]
            return [{k: (v if k != "result" else v[:300]) for k, v in j.items()} for j in jobs]

    def get(self, ident: str) -> dict | None:
        with self.lock:
            j = self.jobs.get(ident)
            return dict(j) if j else None


def main() -> int:
    cmd = (sys.argv[1:] or ["engines"])[0]
    if cmd == "engines":
        print(json.dumps(engines(), indent=1))
    elif cmd == "jobs":
        print(json.dumps(Jobs().list(), indent=1))
    elif cmd == "usage":
        print(json.dumps(Usage().today(), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
