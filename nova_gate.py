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


# ---------------------------------------------------------------- never-list
# The command is NORMALISED first (quotes and carets dropped, backslashes to
# slashes, every spelling of the home folder -> "<home>", /c/x -> c:/x, simple
# variable assignments substituted), then judged piece by piece with the
# working directory tracked. Matching the raw text only ever stopped the
# textbook spelling: `rm -rf ~` was refused while `cd ~ && rm -rf ./*`,
# `find ~ -delete` and `gci $HOME | Remove-Item -Recurse` all ran.
#
# This is still a pattern list, not a sandbox: a script she writes and then
# runs is not seen here. The taint rule in nova_tools.approve (no shell at all
# after reading outside content) is the control that matters against injected
# text; this list is the backstop against a model's own mistake.

_HOME = str(Path.home()).replace("\\", "/").lower()                 # c:/users/pc
_HOME_SPELLINGS = (
    r"\$\{home[^}]*\}", r"\$home\b", r"\$env:userprofile\b", r"%userprofile%",
    r"%homedrive%%homepath%", r"\$env:homedrive\$env:homepath",
    r"\[(?:system\.)?environment\]::getfolderpath\(\W*userprofile\W*\)",
    r"\$\(\s*echo\s+~\s*\)", r"(?<![\w/.:*?<>-])~(?=[/\s;|&)]|$)",
)
# A WHOLE protected target: home, a drive root, C:\Users or C:\Users\<name>, or
# the filesystem root, optionally followed by / * *.* or a lone dot.
_WHOLE = re.compile(
    r"(?:<home>|(?:(?<=[\s=(,:])|^)(?:[a-z]:(?:/users(?:/[^/\s*?]+)?)?|/(?:users)?))"
    r"/?(?:\*(?:\.\*)?|\.)?(?=[\s);,|]|$)")
# "everything here", dangerous when 'here' is home or a root
_ALL_HERE = re.compile(r"(?:(?<=\s)|^)(?:\./?\*?|\*(?:\.\*)?|\.\*)(?=[\s);,|]|$)")
_DELETE = re.compile(
    r"(?<![\w.-])(?:rm|remove-item|ri|del|erase|rd|rmdir|rimraf|shred)(?:\.exe)?(?![\w-])"
    r"|\bfind\b.*\s-delete\b|rmtree|::delete\(|\.delete\(|removedirs|rm_rf|fs\.rm"
    r"|\brobocopy\b.*/(?:mir|purge)\b|\bgit\b.*\bclean\b")
_CD = re.compile(r"^\s*(?:cd|chdir|set-location|sl|pushd)\s+(?:/d\s+)?(\S+)\s*$")
_DISK = re.compile(
    r"(?<![\w-])format(?:\.com|\.exe)?\s+(?:/\S+\s+)*[a-z]:"
    r"|\b(?:format-volume|clear-disk|initialize-disk|remove-partition|mkfs(?:\.\w+)?|diskpart|bcdedit)\b"
    r"|\bcipher\s+/w|\bdd\b.*\bof=(?:/dev/|.*physicaldrive)")
_ENCODED = re.compile(
    r"\b(?:powershell|pwsh)\b.*\s-(?:e|en|enc|enco|encod|encode|encoded\w*|ec)\s+[a-z0-9+/=]{16,}"
    r"|base64\s+(?:-d|--decode)\b.*\|\s*(?:sh|bash|zsh|powershell|pwsh|cmd|python\w*|node)\b"
    r"|frombase64string.*(?:iex|invoke-expression)|(?:iex|invoke-expression).*frombase64string")
# Keys and secrets: never read, copied, changed or deleted through the shell.
_SECRET = re.compile(
    r"(?<![\w-])\.(?:ssh|gnupg|aws)(?![\w-])|id_rsa|id_ed25519|id_ecdsa|authorized_keys"
    r"|voiceprint|voicep[*?]|\.git-credentials|telegram\.k|elevenlabs\.k|\.ke[*?]"
    r"|\.nova/+(?:tok|url\.txt)|(?:^|[\s/=])tok(?:en|[\w]*[*?][\w*?]*)(?=$|[\s;|&)>])")
_SECRET_FILE = re.compile(r"\.(?:key|pem|ppk)$")
# Files (edit / read tools): the same secrets, plus the gate itself.
_SECRET_PATH = re.compile(
    r"(?:^|/)\.(?:ssh|gnupg|aws)(?:/|$)|(?:^|/)token$|id_rsa|id_ed25519|id_ecdsa|authorized_keys"
    r"|\.git-credentials|(?:^|/)credentials(?:\.\w+)?$|\.(?:pem|ppk|key)$|voiceprint\.npy"
    r"|/\.nova/url\.txt$")
_GATE_FILE = re.compile(r"/\.nova/nova_gate\.py$")


def _norm(cmd: str) -> str:
    s = str(cmd).lower().replace("`", "").replace("^", "").replace("\\", "/")
    s = re.sub(r"[\"']", "", s)
    s = re.sub(r"//[?.]/", "", s)                       # \\?\C:\... and \\.\C:\...
    for pat in _HOME_SPELLINGS:
        s = re.sub(pat, "<home>", s)
    s = re.sub(r"(?<![\w:.<>])/([a-z])/", r"\1:/", s)   # Git Bash /c/Users -> c:/Users
    s = re.sub(r"/(?:\./)+", "/", s)
    s = re.sub(r"(?<!:)/{2,}", "/", s)
    s = s.replace(_HOME, "<home>")
    # $p = <home> ; Remove-Item $p      and      p=<home>; rm -rf $p
    for name, value in re.findall(r"(?:^|[\s;(])\$?([a-z_]\w*)\s*=\s*(<home>\S*|[a-z]:/?\S*|/)(?=[\s;)]|$)", s):
        s = re.sub(r"\$\{?" + re.escape(name) + r"\}?(?!\w)|%" + re.escape(name) + "%", value, s)
    return s


def _norm_path(path: str) -> str:
    p = str(path or "").strip().strip("\"'")
    p = re.sub(r"::\$\w+$", "", p)                       # NTFS stream suffix
    try:
        p = str(Path(p).expanduser().resolve())
    except (OSError, ValueError, RuntimeError):
        pass
    p = p.replace("\\", "/").lower()
    p = re.sub(r"^//[?.]/", "", p)
    return "/".join(part.rstrip(" .") or part for part in p.split("/"))   # Windows drops trailing dots/spaces


def never_shell(cmd: str) -> str:
    s = _norm(cmd)
    if _DISK.search(s):
        return "wipes a disk or the boot configuration"
    if _ENCODED.search(s):
        return "runs a hidden (encoded) command"
    words = [w.strip(",;)(") for w in s.split()]
    if _SECRET.search(s) or any(_SECRET_FILE.search(w) and not w.startswith(("$", "[", "{")) for w in words):
        return "keys or secrets"
    here_is_whole = True          # her shell starts in the home folder (config workdir)
    for seg in re.split(r"&&|\|\||[;\n]", s):
        # cmd's own switches (del /s /q) are not paths
        target = seg
        if re.search(r"(?<![\w-])(?:del|erase|rd|rmdir|robocopy)\b", seg):
            target = re.sub(r"(?<=\s)/[a-z]+(?=\s|$)", " ", seg)
        if _DELETE.search(seg) and (_WHOLE.search(target) or (here_is_whole and _ALL_HERE.search(target))):
            return "deletes a drive root or the home folder"
        cd = _CD.match(seg)
        if cd:
            where = cd.group(1)
            here_is_whole = bool(_WHOLE.fullmatch(where)) or (here_is_whole and where in (".", "./")) \
                or where.startswith("..")
    return ""


def never(tool: str, params: dict) -> str:
    """Reason string if this is on the never list, else ''."""
    params = params if isinstance(params, dict) else {}
    if tool in ("Bash", "PowerShell"):
        return never_shell(str(params.get("command", "")))
    if tool in EDIT_TOOLS or tool in ("Read", "Grep", "Glob"):
        path = _norm_path(params.get("file_path") or params.get("notebook_path") or params.get("path") or "")
        if path and _SECRET_PATH.search(path):
            return "keys or secrets"
        if tool in EDIT_TOOLS and _GATE_FILE.search(path):
            return "her own safety gate"
    return ""


# ------------------------------------------------------------ web addresses
# Her own services listen on loopback and trust whatever reaches them: the
# bridge (the panel page IS "Sir typing"), Chrome's debug port, the hands
# service, lights, Pocket Nova, Ollama. A page she is told to open must never
# be one of them, or text on a web page can steer her into typing as him.
OWN_PORTS = {8765, 8781, 8783, 8790, 9333, 11434, 47800}


def _loopback(hostname: str) -> bool:
    import ipaddress
    h = (hostname or "").strip("[]").lower().rstrip(".")
    if h in ("localhost", "") or h.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(h).is_loopback or h == "0.0.0.0"
    except ValueError:
        pass
    try:                                   # 2130706433, 0x7f000001, 127.1
        import socket
        return ipaddress.ip_address(socket.inet_aton(h)).is_loopback
    except (OSError, ValueError):
        return False


def blocked_url(url: str, strict: bool = False) -> str:
    """Why she may not open this address ('' = fine). strict (background
    readers that return text to the model): http/https to public hosts only."""
    import ipaddress
    import urllib.parse
    u = str(url or "").strip()
    try:
        p = urllib.parse.urlsplit(u)
        port = p.port
    except ValueError:
        return "not a valid address"
    scheme = p.scheme.lower()
    if scheme == "file":
        path = _norm_path(urllib.parse.unquote(p.path).lstrip("/") if re.match(r"^/+[a-z]:", p.path, re.I)
                          else urllib.parse.unquote(p.netloc + p.path))
        if strict or _SECRET_PATH.search(path) or re.search(r"/\.(?:nova|ssh|aws|gnupg|claude|codex)(?:/|$)", path):
            return "a private file"
        return ""
    if scheme in ("about",) and not strict:
        return ""
    if scheme not in ("http", "https"):
        return f"{scheme or 'that kind of'} addresses are not opened"
    host = p.hostname or ""
    if _loopback(host):
        if strict or (port or (443 if scheme == "https" else 80)) in OWN_PORTS:
            return "one of Nova's own local services"
        return ""
    if strict:
        try:
            ip = ipaddress.ip_address(host.strip("[]"))
            if ip.is_private or ip.is_link_local or ip.is_reserved:
                return "a private network address"
        except ValueError:
            if host.lower().endswith((".local", ".lan", ".internal", ".ts.net")):
                return "a private network address"
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
        if DANGEROUS_SHELL.search(cmd) or never_shell(cmd):
            return out("confirm", "changes, deletes, installs or sends something")
        # find can delete and write files; these looked like "only looks".
        if re.search(r"\s-(?:delete|fprintf?0?|fls)\b", cmd):
            return out("confirm", "find that deletes or writes")
        # The splitter below only knows && || ; |. A newline, a lone &, $( ),
        # backticks, find -exec or git --output smuggle a second command past
        # it ("echo a<newline>python evil.py" used to come out "free").
        if re.search(r"[\r\n`]|(?<!&)&(?!&)|\$\(|<\(|\s-exec\b|\s-execdir\b|--output\b|\s-ok\b", cmd):
            return out("confirm", "runs more than one thing")
        pieces = [p for p in re.split(r"&&|\|\||;|\|", cmd) if p.strip()]
        if pieces and all(SAFE_SHELL.match(p) for p in pieces):
            return out("free", "only looks")
        return out("confirm", "not on the known-safe list")

    if tool in ("Task", "Agent"):
        return out("announce", "delegates to a sub-agent, which is gated the same way")

    return out("confirm", "unrecognised tool")


# ------------------------------------------------------------- trust ladder
# Only matters when ask_first is on. Each kind of action Sir has said yes to
# is counted; after TRUST_AFTER yeses with no refusal in between, she stops
# asking for that kind. One "no" puts it back to asking. Anything destructive
# is never learned: it is asked every time, however often he has agreed.

TRUST_FILE = ROOT / "trust.json"
TRUST_AFTER = 3

NEVER_LEARN = re.compile(
    r"\b(?:rm|rmdir|del|erase|rd|remove-item|clear-content|format|mkfs|dd|diskpart|bcdedit|cipher|"
    r"shutdown|restart-computer|stop-computer|taskkill|stop-process|kill|reg|regedit|netsh|schtasks|"
    r"set-executionpolicy|invoke-expression|iex|msiexec|"
    r"git\s+(?:push|reset|clean|rebase|rm|stash\s+(?:drop|clear))|"
    r"gh\s+(?:pr\s+(?:merge|close)|repo\s+delete|release))\b|\|\s*(?:sh|bash|powershell|pwsh|cmd)\b",
    re.IGNORECASE)
NEVER_LEARN_TOOLS = {"forget", "lock_pc", "sleep_pc", "minecraft_stop", "minecraft_restart",
                     "close_window", "save_routine"}


def signature(tool: str, params: dict | None = None) -> str:
    """A name for the KIND of action, or '' if this kind must always be asked.

    Deliberately coarse for shell commands (the program and its subcommand,
    never the arguments) and by folder for file edits, so "git commit" is one
    thing he can come to trust and "git push" a different one.
    """
    params = params if isinstance(params, dict) else {}
    if tool == "Bash":
        cmd = str(params.get("command", "")).strip()
        if not cmd or NEVER_LEARN.search(cmd) or never_shell(cmd) or re.search(r"&&|\|\||;|\||>", cmd):
            return ""
        words = re.findall(r"[A-Za-z][\w.-]*", cmd)[:2]
        if not words:
            return ""
        head = Path(words[0]).stem.lower()
        sub = words[1].lower() if len(words) > 1 and head in (
            "git", "gh", "npm", "pip", "python", "py", "node", "winget", "docker", "ssh", "cargo", "uv") else ""
        return f"Bash:{head}{' ' + sub if sub else ''}"
    if tool in EDIT_TOOLS:
        path = str(params.get("file_path") or params.get("notebook_path") or "")
        if not path or SENSITIVE.search(path) or never(tool, params):
            return ""
        try:
            return f"{tool}:{str(Path(path).resolve().parent).lower()}"
        except (OSError, ValueError):
            return ""
    if tool.startswith("mcp__nova__"):
        name = tool.split("__")[-1]
        return "" if name in NEVER_LEARN_TOOLS else f"nova:{name}"
    return ""


def _trust() -> dict:
    try:
        t = json.loads(TRUST_FILE.read_text(encoding="utf-8"))
        return t if isinstance(t, dict) else {}
    except (OSError, ValueError):
        return {}


def trusted(tool: str, params: dict | None = None) -> bool:
    sig = signature(tool, params)
    return bool(sig) and int(_trust().get(sig, {}).get("yes", 0)) >= TRUST_AFTER


def learn(tool: str, params: dict | None, allowed: bool) -> str:
    """Record his answer. Returns the signature if this yes just earned trust."""
    sig = signature(tool, params)
    if not sig:
        return ""
    table = _trust()
    entry = table.get(sig) or {"yes": 0}
    before = int(entry.get("yes", 0))
    entry["yes"] = before + 1 if allowed else 0
    table[sig] = entry
    try:
        TRUST_FILE.write_text(json.dumps(table, indent=1), encoding="utf-8")
    except OSError:
        return ""
    return sig if allowed and before + 1 == TRUST_AFTER else ""


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
