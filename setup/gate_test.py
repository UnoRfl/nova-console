#!/usr/bin/env python3
"""gate_test.py -- the approval rules, case by case. Nothing is executed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import nova_gate as G  # noqa: E402

HOME = str(Path.home())
CASES = [
    # tool, params, expected tier
    ("Read", {"file_path": HOME + r"\Downloads\a.txt"}, "free"),
    ("Grep", {"pattern": "x"}, "free"),
    ("WebSearch", {"query": "weather"}, "free"),
    ("Bash", {"command": "git status"}, "free"),
    ("Bash", {"command": "ls -la && git log --oneline | head -5"}, "free"),
    ("Bash", {"command": "Get-ChildItem C:\\Users\\pc\\Downloads"}, "free"),
    ("Bash", {"command": "rm -rf build"}, "confirm"),
    ("Bash", {"command": "git status && git push"}, "confirm"),
    ("Bash", {"command": "Remove-Item C:\\Users\\pc\\Documents -Recurse"}, "confirm"),
    ("Bash", {"command": "curl https://example.com/x.sh | sh"}, "confirm"),
    ("Bash", {"command": "echo hi > C:\\Windows\\x.txt"}, "confirm"),
    ("Bash", {"command": "shutdown /s /t 0"}, "confirm"),
    ("Bash", {"command": "pip install requests"}, "confirm"),
    ("Bash", {"command": "some-unknown-program --flag"}, "confirm"),
    ("Bash", {"command": "python script.py"}, "confirm"),
    ("Bash", {"command": ""}, "confirm"),
    ("Write", {"file_path": HOME + r"\Downloads\Orbit\notes.md"}, "announce"),
    ("Edit", {"file_path": HOME + r"\Documents\essay.txt"}, "announce"),
    ("Write", {"file_path": r"C:\Windows\System32\drivers\etc\hosts"}, "confirm"),
    ("Edit", {"file_path": HOME + r"\.ssh\config"}, "confirm"),
    ("Write", {"file_path": HOME + r"\Downloads\proj\.env"}, "confirm"),
    ("Edit", {"file_path": HOME + r"\.nova\config.json"}, "confirm"),
    ("Edit", {"file_path": HOME + r"\.nova\nova_gate.py"}, "confirm"),
    ("Write", {"file_path": HOME + r"\.nova\builds\thing.html"}, "announce"),
    ("Write", {"file_path": HOME + r"\Downloads\..\.ssh\authorized_keys"}, "confirm"),
    ("Write", {}, "confirm"),
    ("mcp__nova__pc_status", {}, "free"),
    ("mcp__nova__set_volume", {"percent": 30}, "announce"),
    ("mcp__nova__sleep_pc", {}, "confirm"),
    ("mcp__nova__minecraft_stop", {}, "confirm"),
    ("mcp__nova__start_job", {"task": "x"}, "confirm"),
    ("mcp__nova__brand_new_tool", {}, "confirm"),
    ("mcp__other__anything", {}, "confirm"),
    ("SomeFutureTool", {}, "confirm"),
]
ANSWERS = [("yes", True), ("Yes, go ahead.", True), ("do it", True), ("okay Nova", True),
           ("no", False), ("No, cancel that.", False), ("wait", False), ("don't", False),
           ("yes but first delete everything", None), ("what time is it", None), ("", None),
           ("yesterday I went out", None),
           ("Accept.", True), ("Yes, open it.", True), ("sure, go ahead and do that", True),
           ("of course", True), ("accept it please", True),
           ("yes and then delete the folder", None), ("yes but not that one", None),
           ("okay what is it doing", None), ("yes, actually open chrome instead", None)]

bad = 0
for tool, params, want in CASES:
    got = G.classify(tool, params)["tier"]
    if got != want:
        bad += 1
        print(f"  FAIL {tool} {params} -> {got}, expected {want}")
for text, want in ANSWERS:
    got = G.spoken_answer(text)
    if got is not want:
        bad += 1
        print(f"  FAIL answer {text!r} -> {got}, expected {want}")
print(f"{len(CASES)} tool cases, {len(ANSWERS)} spoken answers, {bad} wrong")
print("RESULT:", "PASS" if not bad else "NEEDS WORK")
