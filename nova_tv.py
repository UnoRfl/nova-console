"""Nova's hands on Sir's LG webOS TV (local Wi-Fi, no cloud).

Pairing happened once (2026-11-15); the key lives in ~/.nova/tv.json (never published).
Every command opens a fresh connection with that key, so there is nothing to "stay
connected": if the TV was off, rebooted or changed IP, the next command just works.

- IP changes: if the saved address does not answer, the TV is found again by its MAC
  (ARP table, then a quick ping sweep) and tv.json is updated.
- Power on: Wake-on-LAN magic packet (needs "Turn on via Wi-Fi" on the TV), then wait
  for the control port.

    python nova_tv.py status | on | off | volume 15 | mute | toast "hi" | app youtube
"""
from __future__ import annotations

import asyncio
import json
import re
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONF = ROOT / "tv.json"
NOWIN = 0x08000000
PORTS = (3001, 3000)

APPS = {  # spoken name -> webOS app id
    "youtube": "youtube.leanback.v4", "netflix": "netflix", "browser": "com.webos.app.browser",
    "web": "com.webos.app.browser", "prime": "amazon", "prime video": "amazon", "disney": "com.disney.disneyplus-prod",
    "disney+": "com.disney.disneyplus-prod", "spotify": "spotify-beehive", "live tv": "com.webos.app.livetv",
    "tv": "com.webos.app.livetv", "settings": "com.palm.app.settings", "home": "com.webos.app.home",
}


def _conf() -> dict:
    try:
        return json.loads(CONF.read_text(encoding="utf-8-sig"))   # tolerate a BOM (PowerShell edits)
    except (OSError, ValueError):
        return {}


def _save(c: dict) -> None:
    CONF.write_text(json.dumps(c, indent=1), encoding="utf-8")


def _open(host: str, timeout: float = 0.6) -> bool:
    for p in PORTS:
        try:
            with socket.create_connection((host, p), timeout=timeout):
                return True
        except OSError:
            pass
    return False


def _arp_lookup(mac: str) -> str | None:
    want = mac.lower().replace(":", "-")
    out = subprocess.run(["arp", "-a"], capture_output=True, text=True, creationflags=NOWIN).stdout
    for line in out.splitlines():
        m = re.match(r"\s*(\d+\.\d+\.\d+\.\d+)\s+([0-9a-f-]{17})", line, re.I)
        if m and m.group(2).lower() == want:
            return m.group(1)
    return None


def _sweep(prefix: str) -> None:
    def ping(i):
        subprocess.run(["ping", "-n", "1", "-w", "300", f"{prefix}.{i}"], capture_output=True, creationflags=NOWIN)
    with ThreadPoolExecutor(64) as ex:
        list(ex.map(ping, range(1, 255)))


def find_host() -> str | None:
    """Saved IP if it answers, else rediscover by MAC and remember the new IP."""
    c = _conf()
    host, mac = c.get("host"), c.get("mac")
    if host and _open(host):
        return host
    if not mac:
        return None
    new = _arp_lookup(mac)
    if not new and host:
        _sweep(".".join(host.split(".")[:3]))
        new = _arp_lookup(mac)
    if new and new != host:
        c["host"] = new
        _save(c)
    return new if new and _open(new) else None


def wake() -> None:
    mac = bytes.fromhex(_conf().get("mac", "").replace(":", "").replace("-", ""))
    if len(mac) != 6:
        return
    pkt = b"\xff" * 6 + mac * 16
    host = _conf().get("host", "192.168.0.255")
    bcast = ".".join(host.split(".")[:3]) + ".255"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    for _ in range(3):
        for t in (bcast, "255.255.255.255"):
            for p in (9, 7):
                s.sendto(pkt, (t, p))
        time.sleep(0.2)
    s.close()


def _in_thread(fn):
    """Run an async fn on a private loop in a thread (safe inside MCP's own loop)."""
    box = {}
    def run():
        try:
            box["v"] = asyncio.run(fn())
        except Exception as exc:  # noqa: BLE001
            box["e"] = exc
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(40)
    if "e" in box:
        raise box["e"]
    return box.get("v", "TV command timed out.")


def _session(op):
    """Connect with the saved key, run op(client), always disconnect."""
    from aiowebostv import WebOsClient
    c = _conf()
    host = find_host()
    if not host:
        raise ConnectionError("TV is off or unreachable")

    async def go():
        cl = WebOsClient(host, client_key=c.get("client_key"), connect_timeout=6)
        await cl.connect()
        try:
            if cl.client_key and cl.client_key != c.get("client_key"):   # TV rotated it
                c["client_key"] = cl.client_key
                _save(c)
            return await op(cl)
        finally:
            await cl.disconnect()
    return _in_thread(go)


def power_on() -> str:
    if find_host():
        return "The TV is already on."
    wake()
    for _ in range(25):
        time.sleep(0.6)
        if find_host():
            return "TV is on."
    return ("Sent the wake signal but the TV did not answer. Check that 'Turn on via Wi-Fi' "
            "(Settings > General > Devices > TV Management) is on.")


def status() -> str:
    if not find_host():
        return "The TV is off (or not on the network)."
    async def op(cl):
        app = await cl.get_current_app()
        vol = await cl.get_volume()
        muted = await cl.get_muted()
        if isinstance(vol, dict):
            vol = vol.get("volume", vol.get("volumeStatus", {}).get("volume", "?"))
        return f"TV is on. App: {app or 'unknown'}. Volume {vol}{', muted' if muted else ''}."
    return _session(op)


def _app_id(name: str, apps: dict) -> str | None:
    n = name.strip().lower()
    if n in APPS:
        return APPS[n]
    for aid, a in apps.items():
        if n == (a.get("title") or "").lower():
            return aid
    for aid, a in apps.items():
        if n in (a.get("title") or "").lower():
            return aid
    return None


def run(action: str, value: str = "") -> str:
    """One TV action. Returns a plain sentence for Nova to relay."""
    a = (action or "").strip().lower().replace("-", "_").replace(" ", "_")
    v = (value or "").strip()
    try:
        if a in ("on", "power_on", "turn_on"):
            return power_on()
        if a in ("status", "state", "info"):
            return status()
        if not find_host():
            if a in ("off", "power_off"):
                return "The TV is already off."
            on = power_on()
            if "is on" not in on:
                return on
            time.sleep(2)

        async def op(cl):
            if a in ("off", "power_off", "turn_off"):
                await cl.power_off(); return "TV turned off."
            if a in ("volume", "set_volume"):
                n = max(0, min(100, int(re.sub(r"\D", "", v) or 10)))
                await cl.set_volume(n); return f"TV volume set to {n}."
            if a in ("volume_up", "louder"):
                for _ in range(int(v or 2)): await cl.volume_up()
                return "Turned the TV up."
            if a in ("volume_down", "quieter"):
                for _ in range(int(v or 2)): await cl.volume_down()
                return "Turned the TV down."
            if a in ("mute", "unmute"):
                await cl.set_mute(a == "mute"); return "TV muted." if a == "mute" else "TV unmuted."
            if a in ("toast", "message", "notify", "say"):
                await cl.send_message(v[:200] or "Hello from Nova"); return "Message shown on the TV."
            if a in ("app", "open", "launch"):
                apps = await cl.get_apps_all() if hasattr(cl, "get_apps_all") else {}
                apps = {x["id"]: x for x in apps} if isinstance(apps, list) else (apps or {})
                aid = _app_id(v, apps)
                if not aid:
                    return f"No app called '{v}' on the TV."
                await cl.launch_app(aid); return f"Opened {v} on the TV."
            if a in ("youtube", "play_youtube"):
                vid = re.search(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})", v)
                if vid:
                    await cl.launch_app_with_content_id(APPS["youtube"], vid.group(1))
                    # The YouTube app often opens the video paused on its page; OK starts it
                    # (Sir had to ask for it on 2026-11-15).
                    await asyncio.sleep(5)
                    await cl.button("ENTER")
                    return "Playing that video on the TV."
                await cl.launch_app_with_params(APPS["youtube"], {"contentTarget": f"https://www.youtube.com/results?search_query={v}"})
                return f"Searched YouTube on the TV for {v}."
            if a in ("url", "web", "browse", "show"):
                url = v if v.startswith("http") else "https://" + v
                await cl.launch_app_with_params(APPS["browser"], {"target": url}); return f"Opened {url} on the TV."
            if a in ("input", "hdmi", "source"):
                ins = await cl.get_inputs()
                ins = ins.get("devices", ins) if isinstance(ins, dict) else ins
                want = v.lower().replace(" ", "")
                for d in ins or []:
                    label = (d.get("label") or "").lower().replace(" ", "")
                    if want and (want in label or want in (d.get("id") or "").lower().replace("_", "")):
                        await cl.set_input(d["id"]); return f"Switched the TV to {d.get('label') or d['id']}."
                names = ", ".join(d.get("label") or d.get("id") for d in ins or [])
                return f"No input matching '{v}'. Inputs: {names}."
            if a in ("pause", "play", "stop"):
                await getattr(cl, a)(); return f"TV {a}."
            if a in ("button", "key", "home", "back"):
                btn = (v or a).upper()
                await cl.button(btn); return f"Pressed {btn} on the TV."
            if a in ("apps", "list_apps"):
                apps = await cl.get_apps()   # visible apps only, not system overlays
                titles = sorted({x.get("title") for x in (apps if isinstance(apps, list) else apps.values()) if x.get("title")})
                return "TV apps: " + ", ".join(titles[:60])
            return f"Unknown TV action '{action}'."
        return _session(op)
    except Exception as exc:  # noqa: BLE001
        return f"TV error: {type(exc).__name__}: {exc}"


if __name__ == "__main__":
    print(run(sys.argv[1] if len(sys.argv) > 1 else "status", " ".join(sys.argv[2:])))
