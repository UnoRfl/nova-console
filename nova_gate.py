#!/usr/bin/env python3
"""
nova_gate.py -- decides which actions Nova may take without asking.

This only ever makes Nova MORE careful. It replaces the old "power: full"
setting, which auto-approved every tool with no prompt at all.

Three tiers:

    free      looking, reading, searching            -> allowed silently
    announce  small and reversible                   -> allowed, she says what she did
    confirm   anything Sir would notice and could
              not easily undo                        -> she asks first; silence means no

The decision is made here, by rule, not by the model. Anything this file does
not recognise is "confirm".

    python nova_gate.py Bash "git status"
    python nova_gate.py Bash "rm -rf build"
    python nova_gate.py Write "C:\\Users\\pc\\Downloads\\notes.txt"
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path.home() / ".nova"
CONFIG_FILE = ROOT / "config.json"

READ_ONLY_TOOLS = {"Read", "Glob", "Grep", "WebFetch", "WebSearch", "TodoWrite", "ToolSearch",
                   "ListMcpResourcesTool", "ReadMcpResourceTool"}
EDIT_TOOLS = {"Write", "Edit", "NotebookEdit", "MultiEdit"}

# Folders Nova may edit files in without asking. Everything else needs a yes.
DEFAULT_PROJECT_DIRS = [str(Path.home() / "Downloads"), str(Path.home() / "Documents"),
                        str(ROOT / "builds"), str(ROOT / "jobs")]

# Never editable without asking, even inside a project folder.
SENSITIVE = re.compile(
    r"(\\|/)(\.ssh|\.aws|\.gnupg|\.codex|\.claude|AppData|Windows|Program Files)(\\|/)"
    r"|\.env(\.|$)|(^|\\|/)token$|credentials|id_rsa|\.pem$|\.key$|voiceprint\.npy"
    r"|(\\|/)\.nova(\\|/)(?!builds|jobs)", re.IGNORECASE)

# Shell commands that only look. Matched against each piece of a compound command.
SAFE_SHELL = re.compile(
    r"^\s*(?:"
    r"ls|dir|pwd|cd|echo|cat|type|head|tail|wc|find|where|which|whoami|hostname|date|ver|tree|"
    r"git\s+(?:status|log|diff|show|branch|remote|rev-parse|describe|ls-files|blame|stash\s+list)|"
    r"gh\s+(?:pr|issue|repo|run)\s+(?:list|view|status|checks)|"
    r"(?:get|test|select|measure|sort|where|format|out|convertto|convertfrom|resolve|split|join|compare)-[a-z]+|"
    r"python(?:3|\.exe)?\s+(?:--version|-V)|node\s+--version|npm\s+(?:ls|list|view|outdated)|pip\s+(?:list|show)|"
    r"ipconfig|ping|nslookup|tasklist|systeminfo|nvidia-smi|ssh\s+headless\s+[\"']?(?:dir|type|hostname|tasklist)"
    r")\b", re.IGNORECASE)

# Shell commands that must always be confirmed, whatever else is on the line.
DANGEROUS_SHELL = re.compile(
    r"\b(?:rm|rmdir|del|erase|rd|format|mkfs|dd|shutdown|restart-computer|stop-computer|"
    r"remove-item|clear-content|set-content|out-file|move-item|rename-item|copy-item|new-item|"
    r"stop-process|taskkill|kill|reg|regedit|sc|net|netsh|schtasks|bcdedit|diskpart|cipher|"
    r"set-executionpolicy|invoke-expression|iex|invoke-webrequest|iwr|curl|wget|start-process|"
    r"pip\s+install|npm\s+(?:install|i|publish|uninstall)|winget|choco|msiexec|"
    r"git\s+(?:push|reset|clean|checkout|rebase|merge|commit|rm|restore|stash\s+(?:drop|pop|clear))|"
    r"gh\s+(?:pr\s+(?:merge|close|create)|repo\s+(?:delete|create)|release))\b"
    r"|>\s*[^&|]|\|\s*(?:sh|bash|powershell|pwsh|cmd)\b", re.IGNORECASE)


# The only things Nova refuses outright when she is not asking first: wiping
# disks or boot config, recursive deletes of a drive root or the home folder,
# and touching keys/secrets. Everything else just happens.
NEVER_SHELL = re.compile(
    r"\b(?:format(?:-volume)?\s+(?:-driveletter\s+)?[a-z]:|(?:mkfs|diskpart|bcdedit|clear-disk|initialize-disk|cipher\s+/w)\b)"
    r"|\b(?:rm|remove-item|del|rd|rmdir)\b[^\n;&|]*?(?:-r|-rf|-fr|/s|-recurse)[^\n;&|]*?"
    r"(?:\s|[\"'])(?:/|~|[a-z]:[\\/]?|\$home|%userprofile%|c:[\\/]users[\\/][^\\/\s\"']+[\\/]?)(?:[\s\"']|$)",
    re.IGNORECASE)
NEVER_PATH = re.compile(
    r"(\\|/)(\.ssh|\.gnupg|\.aws)(\\|/)|(^|\\|/)token$|credentials|id_rsa|\.pem$|\.key$|voiceprint\.npy",
    re.IGNORECASE)


def never(tool: str, params: dict) -> str:
    """Reason string if this is on the never list, else ''."""
    params = params if isinstance(params, dict) else {}
    if tool == "Bash" and NEVER_SHELL.search(str(params.get("command", ""))):
        return "wipes a disk, boot config, a drive root or the home folder"
    if tool in EDIT_TOOLS and NEVER_PATH.search(str(params.get("file_path") or params.get("notebook_path") or "")):
        return "keys or secrets"
    return ""


def ask_first() -> bool:
    """config.ask_first: true = old behaviour (she asks before confirm-tier
    actions). Default false: she just does it. NOVA_ASK_FIRST=1 forces it
    (approval_test.py uses that)."""
    import os
    if os.environ.get("NOVA_ASK_FIRST") == "1":
        return True
    try:
        return bool(json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig")).get("ask_first", False))
    except (OSError, ValueError):
        return False


def project_dirs() -> list[str]:
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        dirs = cfg.get("project_dirs")
        if isinstance(dirs, list) and dirs:
            return [str(d) for d in dirs]
    except (OSError, ValueError):
        pass
    return DEFAULT_PROJECT_DIRS


def inside(path: str, folders: list[str]) -> bool:
    try:
        target = Path(path).resolve()
    except (OSError, ValueError):
        return False
    for folder in folders:
        try:
            target.relative_to(Path(folder).resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


def describe(tool: str, params: dict) -> str:
    """One short spoken-friendly line saying what is about to happen."""
    if tool == "Bash":
        cmd = " ".join(str(params.get("command", "")).split())
        return "run the command: " + (cmd if len(cmd) <= 90 else cmd[:87] + "...")
    if tool in EDIT_TOOLS:
        p = str(params.get("file_path") or params.get("notebook_path") or "")
        verb = "create or overwrite" if tool == "Write" else "edit"
        return f"{verb} the file {Path(p).name} in {Path(p).parent.name or 'the root'}"
    if tool.startswith("mcp__nova__"):
        name = tool.split("__")[-1].replace("_", " ")
        args = ", ".join(f"{k} {v}" for k, v in list(params.items())[:3] if v not in ("", None))
        return name + (f" ({args[:80]})" if args else "")
    return f"use {tool}"


def classify(tool: str, params: dict | None = None) -> dict:
    """{'tier': free|announce|confirm, 'why': ..., 'detail': ...}"""
    params = params if isinstance(params, dict) else {}
    detail = describe(tool, params)

    def out(tier: str, why: str) -> dict:
        return {"tier": tier, "why": why, "detail": detail}

    if tool in READ_ONLY_TOOLS:
        return out("free", "read-only")

    if tool.startswith("mcp__nova__"):
        try:
            import nova_tools
            return out(nova_tools.tier(tool), "from Nova's tool table")
        except Exception:                             # noqa: BLE001
            return out("confirm", "tool table unavailable")

    if tool in EDIT_TOOLS:
        path = str(params.get("file_path") or params.get("notebook_path") or "")
        if not path:
            return out("confirm", "no path given")
        if SENSITIVE.search(path):
            return out("confirm", "a sensitive location")
        if inside(path, project_dirs()):
            return out("announce", "inside a project folder")
        return out("confirm", "outside the project folders")

    if tool == "Bash":
        cmd = str(params.get("command", ""))
        if not cmd.strip():
            return out("confirm", "empty command")
        if DANGEROUS_SHELL.search(cmd):
            return out("confirm", "changes, deletes, installs or sends something")
        pieces = [p for p in re.split(r"&&|\|\||;|\|", cmd) if p.strip()]
        if pieces and all(SAFE_SHELL.match(p) for p in pieces):
            return out("free", "only looks")
        return out("confirm", "not on the known-safe list")

    if tool in ("Task", "Agent"):
        return out("announce", "delegates to a sub-agent, which is gated the same way")

    return out("confirm", "unrecognised tool")


# What counts as a spoken yes or no while she is waiting for an answer.
# A yes is ONLY affirmations, start to finish ("yes", "yes, go ahead", "okay do
# it please"). Anything with other words in it is not a yes -- "yes but first
# delete everything" must not approve whatever happened to be pending.
YES = re.compile(r"^\s*(?:(?:yes|yeah|yep|yup|sure|ok|okay|go ahead|do it|proceed|confirm(?:ed)?|"
                 r"approved?|affirmative|please do|go for it|allow(?: it)?|please|nova|sir)\b[\s,.!]*)+$",
                 re.IGNORECASE)
NO = re.compile(r"^\s*(?:no|nope|nah|stop|cancel|don'?t|do not|deny|denied|negative|abort|"
                r"never ?mind|leave it|hold on|wait)\b.*$", re.IGNORECASE)


# People do not answer with a bare "yes". "Accept", "yes open it", "sure go
# ahead and do that" are all a yes -- as long as nothing in it adds a condition
# or a different instruction.
AFFIRM_START = re.compile(r"^\s*(?:yes|yeah|yep|yup|sure|ok|okay|accept(?:ed)?|allow(?:ed)?|approve[d]?|"
                          r"go ahead|do it|go for it|of course|absolutely|definitely|confirm(?:ed)?|"
                          r"please do|proceed|affirmative|alright|all right|fine)\b", re.IGNORECASE)
NOT_A_PLAIN_YES = re.compile(r"\b(?:but|except|first|instead|before|after|then|also|and then|delete|remove|"
                             r"erase|format|kill|stop|not|no|don'?t|never|wait|hold|cancel|unless|only|"
                             r"rather|actually|what|why|how)\b", re.IGNORECASE)


def spoken_answer(text: str) -> bool | None:
    """True = yes, False = no, None = neither (keep waiting)."""
    t = " ".join(str(text or "").split())
    if NO.match(t):
        return False
    if YES.match(t) and not re.fullmatch(r"[\s,.!]*(?:please|nova|sir)[\s,.!]*", t, re.IGNORECASE):
        return True
    words = re.findall(r"[a-z']+", t.lower())
    if AFFIRM_START.match(t) and len(words) <= 6 and not NOT_A_PLAIN_YES.search(t):
        return True
    return None


if __name__ == "__main__":
    tool = sys.argv[1] if len(sys.argv) > 1 else "Bash"
    arg = " ".join(sys.argv[2:])
    params = {"command": arg} if tool == "Bash" else {"file_path": arg}
    print(json.dumps(classify(tool, params), indent=1))
