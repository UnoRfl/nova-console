#!/usr/bin/env python3
"""
grant_mic.py -- pre-grant microphone access to the NOVA panel.

The panel opens in Chrome's --app mode, which has no address bar and therefore
no padlock to click, so the usual "allow the microphone" gesture is not
available. Instead we write the permission straight into the panel's own
browser profile, scoped to the bridge origin and nothing else.

    python grant_mic.py                       # grant for http://127.0.0.1:8765
    python grant_mic.py --origin http://127.0.0.1:9000
    python grant_mic.py --revoke
    python grant_mic.py --show

Chrome must not be running against this profile while the file is rewritten --
it holds Preferences in memory and flushes on exit, which would silently undo
the change. Nova.ps1 closes the panel first.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROFILE = Path.home() / ".nova" / "browser-profile" / "Default"
PREFS = PROFILE / "Preferences"

ALLOW, BLOCK = 1, 2

# Chrome timestamps content settings in microseconds since 1601-01-01 UTC.
EPOCH_1601 = datetime(1601, 1, 1, tzinfo=timezone.utc)


def chrome_now() -> str:
    delta = datetime.now(timezone.utc) - EPOCH_1601
    return str(int(delta.total_seconds() * 1_000_000))


def load_prefs() -> dict:
    if not PREFS.exists():
        return {}
    try:
        return json.loads(PREFS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("Preferences exists but could not be parsed; starting a fresh one.")
        return {}


def dig(node: dict, *keys: str) -> dict:
    """Walk down a nested dict, creating plain dicts as needed."""
    for key in keys:
        nxt = node.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            node[key] = nxt
        node = nxt
    return node


# Chrome's content-setting keys are "<primary pattern>,<secondary pattern>".
# For a page's own capture permission the secondary pattern is a bare wildcard.
def pattern_for(origin: str) -> str:
    return f"{origin.rstrip('/')},*"


def show(origin: str) -> int:
    prefs = load_prefs()
    exceptions = (
        prefs.get("profile", {})
        .get("content_settings", {})
        .get("exceptions", {})
    )
    print(f"profile: {PROFILE}")
    print(f"exists:  {PREFS.exists()}")
    for kind in ("media_stream_mic", "media_stream_camera"):
        entries = exceptions.get(kind) or {}
        if not entries:
            print(f"  {kind:22} (none)")
            continue
        for pattern, body in entries.items():
            setting = body.get("setting") if isinstance(body, dict) else body
            word = {ALLOW: "allow", BLOCK: "block"}.get(setting, str(setting))
            print(f"  {kind:22} {pattern}  ->  {word}")
    print()
    want = pattern_for(origin)
    mic = exceptions.get("media_stream_mic") or {}
    current = mic.get(want, {}).get("setting") if isinstance(mic.get(want), dict) else None
    print(f"{origin} is currently: "
          + {ALLOW: "ALLOWED", BLOCK: "BLOCKED"}.get(current, "not set"))
    return 0


def apply(origin: str, setting: int) -> int:
    PROFILE.mkdir(parents=True, exist_ok=True)
    prefs = load_prefs()

    exceptions = dig(prefs, "profile", "content_settings", "exceptions")
    pattern = pattern_for(origin)

    for kind in ("media_stream_mic",):
        table = exceptions.get(kind)
        if not isinstance(table, dict):
            table = {}
            exceptions[kind] = table
        table[pattern] = {"last_modified": chrome_now(), "setting": setting}

    # Chrome refuses to load a Preferences file it considers corrupt, so write
    # beside it and swap only once the full document is on disk.
    tmp = PREFS.with_suffix(".nova-tmp")
    try:
        tmp.write_text(json.dumps(prefs, separators=(",", ":")), encoding="utf-8")
        tmp.replace(PREFS)
    except OSError as exc:
        print(f"Could not write Preferences: {exc}")
        return 1

    verb = "granted to" if setting == ALLOW else "revoked from"
    print(f"Microphone {verb} {origin}")
    print(f"  profile: {PROFILE}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Grant the NOVA panel microphone access")
    ap.add_argument("--origin", default="http://127.0.0.1:8765")
    ap.add_argument("--revoke", action="store_true")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    if args.show:
        return show(args.origin)
    return apply(args.origin, BLOCK if args.revoke else ALLOW)


if __name__ == "__main__":
    sys.exit(main())
