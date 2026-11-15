#!/usr/bin/env python3
"""nova_eyes.py -- her eyes: one still frame from Sir's webcam, when he asks.

    python nova_eyes.py            # list cameras
    python nova_eyes.py --look     # take one picture, print where it went

How it works. PyAV (already here for Whisper) carries FFmpeg, and FFmpeg reads
Windows cameras through DirectShow, so nothing new is installed -- OpenCV's
unsigned DLL would be blocked by Smart App Control on this PC anyway.

Rules she keeps, on purpose:
  * The camera is opened only inside look(), for about a second, and closed
    again. There is no background watching, no timer and no video: the light
    on the camera comes on exactly when she looks.
  * Every look is written to the bridge log by the caller, and the picture
    stays on this PC (~/.nova/captures/cam-*.jpg, newest 12 kept). It is in
    no published file list.
  * config.json  "eyes": {"enabled": false}  switches them off entirely;
    "camera": "name" picks one when there are several.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from pathlib import Path

ROOT = Path.home() / ".nova"
CAPTURES = ROOT / "captures"
CONFIG_FILE = ROOT / "config.json"
KEEP = 12
_lock = threading.Lock()


def cfg() -> dict:
    try:
        c = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig")).get("eyes")
        return c if isinstance(c, dict) else {}
    except (OSError, ValueError):
        return {}


def cameras() -> list[str]:
    """Names of the video capture devices Windows has, in its own order."""
    import subprocess
    script = ("Get-PnpDevice -PresentOnly -Class Camera,Image -ErrorAction SilentlyContinue | "
              "Where-Object { $_.Status -eq 'OK' -and $_.Class -eq 'Camera' } | "
              "ForEach-Object { $_.FriendlyName }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, text=True, timeout=15, creationflags=0x08000000,
                           stdin=subprocess.DEVNULL)
        return [x.strip() for x in r.stdout.splitlines() if x.strip()]
    except (OSError, subprocess.SubprocessError):
        return []


def _pick(names: list[str], want: str) -> list[str]:
    if want:
        hit = [n for n in names if want.lower() in n.lower()]
        return hit + [n for n in names if n not in hit]
    return names


def _grab(name: str, warm_frames: int, timeout: float):
    """One frame from one camera, as a PIL image. The first frames from a webcam
    are dark while it sets its exposure, so a few are thrown away."""
    import av
    box = None
    last = None
    began = time.time()
    for opts in ({"video_size": "1280x720", "rtbufsize": "64M"}, {"rtbufsize": "64M"}):
        try:
            box = av.open(f"video={name}", format="dshow", options=opts, timeout=timeout)
            break
        except Exception as exc:                      # noqa: BLE001 - try the camera's own default size
            last = exc
    if box is None:
        raise RuntimeError(f"{type(last).__name__}: {str(last)[:160]}")
    try:
        n = 0
        frame = None
        bad = ""
        for packet in box.demux(video=0):
            try:
                got = packet.decode()
            except Exception as exc:                  # noqa: BLE001 - a torn first frame is normal; skip it
                bad = f"{type(exc).__name__}: {str(exc)[:120]}"
                got = []
            for f in got:
                frame = f
                n += 1
            if n > warm_frames or time.time() - began > timeout:
                break
        if frame is None:
            raise RuntimeError("the camera opened but sent no picture" + (f" ({bad})" if bad else ""))
        return frame.to_image()
    finally:
        box.close()


def look(note: str = "") -> dict:
    """Take one picture. {"ok", "path", "camera", "brightness"} or {"error"}."""
    c = cfg()
    if c.get("enabled") is False:
        return {"error": "Her eyes are switched off in config.json (eyes.enabled)."}
    if not _lock.acquire(timeout=20):
        return {"error": "The camera is already being used by another look."}
    try:
        names = _pick(cameras(), str(c.get("camera") or ""))
        if not names:
            return {"error": "No camera is connected to this PC."}
        fault = ""
        for name in names:
            try:
                img = _grab(name, int(c.get("warm_frames") or 12), float(c.get("timeout") or 12))
            except Exception as exc:                  # noqa: BLE001
                fault = f"{name}: {str(exc)[:200]}"
                continue
            if max(img.size) > 1280:
                img.thumbnail((1280, 1280))
            CAPTURES.mkdir(exist_ok=True)
            path = CAPTURES / time.strftime("cam-%Y%m%d-%H%M%S.jpg")
            img.convert("RGB").save(path, "JPEG", quality=88)
            grey = img.convert("L").resize((64, 36))
            px = list(grey.tobytes())
            for old in sorted(CAPTURES.glob("cam-*.jpg"))[:-KEEP]:
                old.unlink(missing_ok=True)
            return {"ok": True, "path": str(path), "camera": name, "size": list(img.size),
                    "brightness": round(sum(px) / len(px) / 255, 2)}
        busy = bool(re.search(r"I/O error|busy|in use|Could not run graph", fault, re.I))
        return {"error": ("The camera is in use by another app (a call or the Camera app), so I could not look. "
                          if busy else "The camera would not give a picture. ") + fault}
    finally:
        _lock.release()


def main() -> int:
    if "--look" in sys.argv:
        print(json.dumps(look(), indent=1))
        return 0
    print(json.dumps({"cameras": cameras(), "config": cfg()}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
