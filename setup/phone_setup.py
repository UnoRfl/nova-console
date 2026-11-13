"""One-time setup so Sir can use Nova from his phone, with full control.

Run AFTER this PC is signed in to Tailscale (and the phone too, same account):

    venv\\Scripts\\python.exe setup\\phone_setup.py

It:
  1. reads this PC's Tailscale identity and records that login as the owner
     (config.json "owner_logins") -- only that login gets full control;
  2. runs `tailscale serve` so the bridge is reachable inside the tailnet only
     (HTTPS on this PC's *.ts.net name; never Funnel, never the open internet);
  3. prints the phone URL (with the token) to open on the phone.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.home() / ".nova"
TS = shutil.which("tailscale") or r"C:\Program Files\Tailscale\tailscale.exe"


def ts(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([TS, *args], creationflags=0x08000000, capture_output=True, text=True, timeout=60)


def main() -> int:
    st = ts("status", "--json")
    try:
        status = json.loads(st.stdout)
    except ValueError:
        print("Tailscale is not answering:", (st.stderr or st.stdout).strip()[:300])
        return 1
    if status.get("BackendState") != "Running":
        print(f"Tailscale is '{status.get('BackendState')}'. Open the Tailscale app on this PC and sign in, "
              "then run this again.")
        return 1
    me = status.get("Self") or {}
    user = (status.get("User") or {}).get(str(me.get("UserID"))) or {}
    login = user.get("LoginName") or ""
    if not login:
        print("Could not read this PC's Tailscale login.")
        return 1

    cfg_path = ROOT / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    logins = [x for x in cfg.get("owner_logins") or [] if x]
    if login not in logins:
        logins.append(login)
    cfg["owner_logins"] = logins
    cfg["_owner_logins"] = ("Tailscale logins that get FULL control from a phone (same as sitting at the desk, "
                            "minus speaking aloud). Anyone else on the tailnet stays read-only.")
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("owner login recorded")

    funnel = ts("funnel", "status")
    if "Funnel on" in (funnel.stdout or ""):
        print("WARNING: Tailscale Funnel is ON (public internet). Turn it off: tailscale funnel reset")

    serve = ts("serve", "--bg", "http://127.0.0.1:8765")
    if serve.returncode != 0:
        print("tailscale serve failed:", (serve.stderr or serve.stdout).strip()[:400])
        print("If it asks you to enable HTTPS, follow the link it prints, then run this again.")
        return 1
    dns = (me.get("DNSName") or "").rstrip(".")
    token = (ROOT / "token").read_text(encoding="utf-8-sig").strip()
    url = f"https://{dns}/phone?token={token}"
    (ROOT / "phone-url.txt").write_text(url + "\n", encoding="utf-8")
    print("\nOpen this on your phone (Tailscale on, same account), then Add to Home Screen:\n")
    print("  " + url + "\n")
    print("Saved to ~/.nova/phone-url.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
