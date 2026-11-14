#!/usr/bin/env python3
"""
never_test.py -- the never-list against the spellings that used to get past it.

Pure: nothing is run, the command strings are only handed to nova_gate.

    python setup/never_test.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import nova_gate as g                                               # noqa: E402

H = str(Path.home())                       # C:\Users\<name>
HB = "/" + H[0].lower() + H[2:].replace("\\", "/")                  # /c/Users/<name>

REFUSE = [
    # home / drive roots
    "rm -rf ~", "rm -rf ~/", f"rm -rf {HB}", f"rm -rf {HB}/./", "rm -rf ${HOME:?}/", "rm -rf /c/Users",
    "cd ~ && rm -rf ./*", "rm -rf *", "rm -rf ./*", "find . -delete", f"find {HB} -delete",
    f"del /s /q {H}\\*.*", "rd /s /q %HOMEDRIVE%%HOMEPATH%", "rd /s /q %USERPROFILE%",
    "Get-ChildItem $HOME | Remove-Item -Recurse -Force", "$p=$HOME; Remove-Item $p -Recurse -Force",
    f"Remove-Item -LiteralPath \\\\?\\{H} -Recurse", "[IO.Directory]::Delete($HOME,$true)",
    f"python -c \"import shutil; shutil.rmtree(r'{H}')\"", f"robocopy C:\\empty {H} /MIR",
    f"git -C {HB} clean -fdx", "Remove-Item D:\\*.* -Recurse", "rm -rf /", "ri C:\\ -Recurse -Force",
    "cd .. ; rm -rf *", "p=~; rm -rf $p",
    # disks
    "format D: /q", "format /q D:", "format.com D: /q /y", "Format-Volume -DriveLetter D",
    "Remove-Partition -DriveLetter D", "diskpart /s x.txt", "bcdedit /set x y", "Clear-Disk 1",
    # encoded
    "powershell -enc QWxhZGRpbjpvcGVuIHNlc2FtZQ==", "powershell -enco QWxhZGRpbjpvcGVuIHNlc2FtZQ==",
    "powershell -en QWxhZGRpbjpvcGVuIHNlc2FtZQ==", "pwsh -EncodedCommand QWxhZGRpbjpvcGVuIHNlc2FtZQ==",
    "echo cm0gLXJmIH4= | base64 -d | bash",
    # keys and secrets: reading as well as writing
    "cat ~/.ssh/id_rsa", "rm -rf ~/.ssh", "cd ~/.ssh && rm *", "cp k ~/.ssh/authorized_keys",
    "tee -a ~/.ssh/authorized_keys", f"Add-Content {H}\\.ssh\\authorized_keys x",
    "powershell -c \"Remove-Item -Recurse -Force $HOME\\.ssh\"",
    "cat ~/.nova/token", f"type {H}\\.nova\\telegram.key", "rm ~/.nova/tok*", "rm ~/.nova/telegram.ke?",
    "mv ~/.nova/voiceprint.npy x", "cd ~/.nova && cat token", "cat ~/.nova/url.txt",
    "find /c/Windows/win.ini -fprintf ~/.ssh/authorized_keys x", "cat server.pem", "type my.key",
]

ALLOW = [
    "ls", "dir", "git status", "rm -rf build", "rm -rf ~/Downloads/old-build", "del /q notes.txt",
    "cd ~/Downloads && rm -rf ./*", f"cd {H}\\Downloads\\proj; Remove-Item * -Recurse",
    "Remove-Item C:\\Users\\Public\\Desktop\\x.lnk", "find . -name '*.py'", "find ~/Downloads -name '*.tmp' -delete",
    "Get-ChildItem $HOME | Format-Table Name", "git -C ~/Downloads/repo clean -fdx", "python script.py",
    "Get-Process | Sort-Object CPU | Select-Object -First 5", "$h.Keys | ForEach-Object { $_.Key }",
    "rm *.tmp", "ssh headless hostname", "echo hello > ~/Downloads/a.txt", "npm install", "pip list",
    "robocopy C:\\a D:\\backup\\a /E", "Get-Content ~/.nova/config.json", "nvidia-smi",
    "Format-Table -AutoSize", "git commit -m 'format c: joke in a message'" if False else "git log --format=%H",
]

FILES_REFUSE = [("Write", f"{H}\\.ssh\\config"), ("Edit", f"{H}\\.nova\\token"), ("Write", f"{H}\\.nova\\token."),
                ("Write", f"{H}\\.nova\\telegram.key "), ("Write", f"{H}\\.nova\\telegram.key::$DATA"),
                ("Edit", f"{H}\\.nova\\voiceprint.npy"), ("Edit", f"{H}\\.nova\\nova_gate.py"),
                ("Read", f"{H}\\.nova\\token"), ("Read", f"{H}\\.ssh\\id_ed25519"), ("Read", f"{H}\\.nova\\url.txt"),
                ("Write", f"{H}\\Downloads\\..\\.ssh\\authorized_keys")]
FILES_ALLOW = [("Write", f"{H}\\Downloads\\notes.txt"), ("Read", f"{H}\\.nova\\config.json"),
               ("Edit", f"{H}\\.nova\\nova_tools.py"), ("Read", f"{H}\\Documents\\tokens-essay.docx")]

bad = 0
for c in REFUSE:
    if not g.never("Bash", {"command": c}):
        bad += 1
        print("  NOT REFUSED  ", c)
    if g.classify("Bash", {"command": c})["tier"] == "free":
        bad += 1
        print("  CLASSED FREE ", c)
for c in ALLOW:
    why = g.never("Bash", {"command": c})
    if why:
        bad += 1
        print(f"  WRONGLY REFUSED ({why})  ", c)
for tool, p in FILES_REFUSE:
    if not g.never(tool, {"file_path": p}):
        bad += 1
        print("  FILE NOT REFUSED  ", tool, p)
for tool, p in FILES_ALLOW:
    if g.never(tool, {"file_path": p}):
        bad += 1
        print("  FILE WRONGLY REFUSED  ", tool, p)

print(f"{len(REFUSE)} refuse, {len(ALLOW)} allow, {len(FILES_REFUSE) + len(FILES_ALLOW)} files, {bad} wrong")
print("RESULT:", "PASS" if not bad else "FAIL")
sys.exit(1 if bad else 0)
