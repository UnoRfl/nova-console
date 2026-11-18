"""nova_duck -- soften every other sound on the PC while Nova listens or talks.

Sir, 2026-11-17: "when music is playing and I call on Nova it softens sounds
from other parts of the PC so I can understand Nova".

Windows keeps a volume per app ("session") in the mixer. While she is
listening or speaking, every session that is not hers is faded down to
`level` of where it was, and faded back afterwards. Plain Core Audio through
comtypes (no pycaw, nothing to install).

Safety:
  * what each app was at is written to duck.json before anything is lowered,
    so a crash mid-duck is undone the next time the bridge starts, and an app that closed while softened is put
    back when it opens again (Windows remembers per-app volume);
  * on the way back up an app is only restored if it is still where we put it,
    so a volume he changed by hand in the meantime is left alone.

    python nova_duck.py list        every session, its process and volume
    python nova_duck.py demo        duck for 3 s, then restore (audible)
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
import time
from ctypes import POINTER, c_float, c_int, c_uint, c_void_p, HRESULT
from ctypes.wintypes import BOOL, DWORD, LPCWSTR, LPWSTR
from pathlib import Path

import comtypes
from comtypes import COMMETHOD, GUID, IUnknown

ROOT = Path.home() / ".nova"
STATE_FILE = ROOT / "duck.json"


class IMMDevice(IUnknown):
    _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Activate", (["in"], POINTER(GUID), "iid"), (["in"], DWORD, "ctx"),
                  (["in"], c_void_p, "params"), (["out"], POINTER(POINTER(IUnknown)), "out")),
        COMMETHOD([], HRESULT, "OpenPropertyStore", (["in"], DWORD), (["out"], POINTER(c_void_p))),
        COMMETHOD([], HRESULT, "GetId", (["out"], POINTER(LPWSTR))),
        COMMETHOD([], HRESULT, "GetState", (["out"], POINTER(DWORD))),
    ]


class IMMDeviceCollection(IUnknown):
    _iid_ = GUID("{0BD7A1BE-7A1A-44DB-8397-CC5392387B5E}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_uint))),
        COMMETHOD([], HRESULT, "Item", (["in"], c_uint), (["out"], POINTER(POINTER(IMMDevice)))),
    ]


class IMMDeviceEnumerator(IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _methods_ = [
        COMMETHOD([], HRESULT, "EnumAudioEndpoints", (["in"], c_int), (["in"], DWORD),
                  (["out"], POINTER(POINTER(IMMDeviceCollection)))),
        COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint", (["in"], c_int), (["in"], c_int),
                  (["out"], POINTER(POINTER(IMMDevice)))),
    ]


class ISimpleAudioVolume(IUnknown):
    _iid_ = GUID("{87CE5498-68D6-44E5-9215-6DA47EF883D8}")
    _methods_ = [
        COMMETHOD([], HRESULT, "SetMasterVolume", (["in"], c_float), (["in"], POINTER(GUID))),
        COMMETHOD([], HRESULT, "GetMasterVolume", (["out"], POINTER(c_float))),
        COMMETHOD([], HRESULT, "SetMute", (["in"], BOOL), (["in"], POINTER(GUID))),
        COMMETHOD([], HRESULT, "GetMute", (["out"], POINTER(BOOL))),
    ]


class IAudioSessionControl(IUnknown):
    _iid_ = GUID("{F4B1A599-7266-4319-A8CA-E70ACB11E8CD}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetState", (["out"], POINTER(c_int))),
        COMMETHOD([], HRESULT, "GetDisplayName", (["out"], POINTER(LPWSTR))),
        COMMETHOD([], HRESULT, "SetDisplayName", (["in"], LPCWSTR), (["in"], POINTER(GUID))),
        COMMETHOD([], HRESULT, "GetIconPath", (["out"], POINTER(LPWSTR))),
        COMMETHOD([], HRESULT, "SetIconPath", (["in"], LPCWSTR), (["in"], POINTER(GUID))),
        COMMETHOD([], HRESULT, "GetGroupingParam", (["out"], POINTER(GUID))),
        COMMETHOD([], HRESULT, "SetGroupingParam", (["in"], POINTER(GUID)), (["in"], POINTER(GUID))),
        COMMETHOD([], HRESULT, "RegisterAudioSessionNotification", (["in"], c_void_p)),
        COMMETHOD([], HRESULT, "UnregisterAudioSessionNotification", (["in"], c_void_p)),
    ]


class IAudioSessionControl2(IAudioSessionControl):
    _iid_ = GUID("{BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetSessionIdentifier", (["out"], POINTER(LPWSTR))),
        COMMETHOD([], HRESULT, "GetSessionInstanceIdentifier", (["out"], POINTER(LPWSTR))),
        COMMETHOD([], HRESULT, "GetProcessId", (["out"], POINTER(DWORD))),
        COMMETHOD([], HRESULT, "IsSystemSoundsSession"),
        COMMETHOD([], HRESULT, "SetDuckingPreference", (["in"], BOOL)),
    ]


class IAudioSessionEnumerator(IUnknown):
    _iid_ = GUID("{E2F5BB11-0570-40CA-ACDD-3AA01277DEE8}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_int))),
        COMMETHOD([], HRESULT, "GetSession", (["in"], c_int), (["out"], POINTER(POINTER(IAudioSessionControl)))),
    ]


class IAudioSessionManager2(IUnknown):
    _iid_ = GUID("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetAudioSessionControl", (["in"], POINTER(GUID)), (["in"], DWORD),
                  (["out"], POINTER(c_void_p))),
        COMMETHOD([], HRESULT, "GetSimpleAudioVolume", (["in"], POINTER(GUID)), (["in"], DWORD),
                  (["out"], POINTER(c_void_p))),
        COMMETHOD([], HRESULT, "GetSessionEnumerator", (["out"], POINTER(POINTER(IAudioSessionEnumerator)))),
    ]


CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
E_RENDER, DEVICE_STATE_ACTIVE, CLSCTX_ALL = 0, 1, 23


def _proc_path(pid: int) -> str:
    """Full image path of a process, lower-case ("" if it cannot be read)."""
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = c_void_p
    h = k.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        size = DWORD(520)
        if k.QueryFullProcessImageNameW(c_void_p(h), 0, buf, ctypes.byref(size)):
            return buf.value.lower()
        return ""
    finally:
        k.CloseHandle(c_void_p(h))


def sessions() -> list[dict]:
    """Every app session on every active output device: {key, pid, name, vol (ISimpleAudioVolume)}.
    Call on a thread that has done CoInitialize."""
    out: list[dict] = []
    en = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, CLSCTX_ALL)
    coll = en.EnumAudioEndpoints(E_RENDER, DEVICE_STATE_ACTIVE)
    for d in range(coll.GetCount()):
        dev = coll.Item(d)
        dev_id = dev.GetId()
        try:
            unk = dev.Activate(ctypes.byref(IAudioSessionManager2._iid_), CLSCTX_ALL, None)
            mgr = unk.QueryInterface(IAudioSessionManager2)
            senum = mgr.GetSessionEnumerator()
        except Exception:                             # noqa: BLE001
            continue
        for i in range(senum.GetCount()):
            try:
                ctl = senum.GetSession(i)
                c2 = ctl.QueryInterface(IAudioSessionControl2)
                if c2.IsSystemSoundsSession() == 0:   # S_OK = it IS the system-sounds session
                    continue
                pid = int(c2.GetProcessId())
                inst = c2.GetSessionInstanceIdentifier()
                vol = ctl.QueryInterface(ISimpleAudioVolume)
                path = _proc_path(pid)
                out.append({"key": f"{dev_id}|{inst}", "pid": pid, "name": os.path.basename(path),
                            "path": path, "vol": vol, "state": int(ctl.GetState())})
            except Exception:                         # noqa: BLE001
                continue
    return out


NOVA_DIR = str(ROOT).lower() + os.sep


def is_nova(s: dict, own: set[int]) -> bool:
    """Her own voice: this process or its venv launcher (by pid -- the real
    interpreter is the shared system pythonw.exe, which his Discord bot and the
    PocketNova panel also run on), or anything whose exe lives in ~/.nova."""
    return s["pid"] in own or s.get("path", "").startswith(NOVA_DIR)


def _load() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(saved: dict) -> None:
    try:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(saved), encoding="utf-8")
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass


class Ducker:
    """Owns one COM thread. want() is polled ~10x a second; True = keep the
    other apps down."""

    def __init__(self, want, conf=None, log=None) -> None:
        self.want = want
        self.conf = conf or (lambda: {})
        self.log = log or (lambda m: None)
        self.own = {os.getpid(), os.getppid()}      # the venv launcher is the parent
        self.saved: dict[str, dict] = {}            # key -> {"orig": v, "set": v, "name": n}
        self.ducked = False
        self.since_off = 0.0
        threading.Thread(target=self._run, daemon=True, name="nova-duck").start()

    def _c(self) -> dict:
        c = {"enabled": True, "level": 0.25, "fade_ms": 250, "release_s": 1.2, "skip": []}
        try:
            c.update(self.conf() or {})
        except Exception:                             # noqa: BLE001
            pass
        return c

    def _fade(self, steps: list[tuple[object, float, float]], ms: float) -> None:
        n = max(1, int(ms / 25))
        for k in range(1, n + 1):
            t = k / n
            for vol, a, b in steps:
                try:
                    vol.SetMasterVolume(c_float(a + (b - a) * t), None)
                except Exception:                     # noqa: BLE001
                    pass
            time.sleep(0.025)

    def _match(self, s: dict, cur: float) -> str | None:
        """The saved entry this session belongs to: its own key, or -- when the
        app closed its old session while we had it down and opened a new one,
        which Windows starts at the remembered (lowered) volume -- the same app
        still sitting where we put it."""
        if s["key"] in self.saved:
            return s["key"]
        for k, v in self.saved.items():
            if v.get("name") and v["name"] == s["name"] and abs(cur - v["set"]) < 0.02:
                return k
        return None

    def _keep_nova_loud(self, s: dict) -> None:
        """Sir, 2026-11-18: her voice must never come out softer -- that is the
        whole point of ducking. Windows remembers per-app volume by exe, so one
        stray lowering would stick to every Nova session after it."""
        try:
            if float(s["vol"].GetMasterVolume()) < 0.999:
                s["vol"].SetMasterVolume(c_float(1.0), None)
                self.log(f"duck: Nova's own volume was low ({s['name']}), put back to full")
            if s["vol"].GetMute():
                s["vol"].SetMute(False, None)
        except Exception:                             # noqa: BLE001
            pass

    def _down(self, c: dict) -> None:
        skip = {x.lower() for x in c["skip"]}
        level = max(0.0, min(1.0, float(c["level"])))
        steps = []
        for s in sessions():
            if is_nova(s, self.own):
                self._keep_nova_loud(s)
                continue
            if s["pid"] == 0 or s["name"] in skip:
                continue
            try:
                cur = float(s["vol"].GetMasterVolume())
            except Exception:                         # noqa: BLE001
                continue
            k = self._match(s, cur)
            if k:
                if k != s["key"]:                     # same app, new session: already down
                    self.saved[s["key"]] = dict(self.saved.pop(k), at=time.time())
                continue
            if cur <= 0.02:
                continue
            new = cur * level
            self.saved[s["key"]] = {"orig": cur, "set": new, "name": s["name"], "at": time.time()}
            steps.append((s["vol"], cur, new))
        if steps:
            _save(self.saved)                         # before lowering: a crash can always be undone
            self._fade(steps, float(c["fade_ms"]))
            names = sorted({v["name"] for v in self.saved.values()})
            self.log(f"duck: softened {len(steps)} more app(s); down now: " + ", ".join(names)[:160])

    def _up(self, c: dict | None = None, fade: bool = True) -> int:
        """Put back everything we lowered that is still where we left it. An
        app he moved by hand meanwhile is left alone. Entries for sessions that
        are gone stay (a day at most) so the app's next session is put back."""
        steps = []
        for s in sessions():
            if is_nova(s, self.own):
                continue
            try:
                cur = float(s["vol"].GetMasterVolume())
            except Exception:                         # noqa: BLE001
                continue
            k = self._match(s, cur)
            if not k:
                continue
            got = self.saved.pop(k)
            if abs(cur - got["set"]) < 0.02:          # still where we left it: he did not touch it
                steps.append((s["vol"], cur, got["orig"]))
        if steps:
            if fade:
                self._fade(steps, float((c or self._c())["fade_ms"]) * 2)
            else:
                for vol, _, b in steps:
                    try:
                        vol.SetMasterVolume(c_float(b), None)
                    except Exception:                 # noqa: BLE001
                        pass
        self.saved = {k: v for k, v in self.saved.items() if time.time() - float(v.get("at") or 0) < 86400}
        _save(self.saved)
        return len(steps)

    def _run(self) -> None:
        comtypes.CoInitialize()
        try:
            self.saved = {k: v for k, v in _load().items() if isinstance(v, dict) and "set" in v}
            for s in sessions():
                if is_nova(s, self.own):
                    self._keep_nova_loud(s)
            n = self._up(fade=False) if self.saved else 0
            if n:
                self.log(f"duck: restored {n} app(s) left softened by a crash")
        except Exception:                             # noqa: BLE001
            self.saved = {}
        last_sweep = 0.0
        while True:
            c = self._c()
            try:
                want = bool(c["enabled"]) and bool(self.want())
            except Exception:                         # noqa: BLE001
                want = False
            try:
                now = time.time()
                if want:
                    self.since_off = 0.0
                    if not self.ducked:
                        self.ducked = True
                        self._down(c)
                    elif int(now * 10) % 10 == 0:
                        self._down(c)                 # once a second: an app that started playing meanwhile
                elif self.ducked:
                    if not self.since_off:
                        self.since_off = now
                    elif now - self.since_off >= float(c["release_s"]):
                        self.ducked = False
                        self._up(c)
                elif self.saved and now - last_sweep > 1.0:
                    last_sweep = now
                    n = self._up(fade=False)          # an app that closed while down has come back
                    if n:
                        self.log(f"duck: put back {n} app(s) that reopened while softened")
            except Exception as exc:                  # noqa: BLE001
                self.log(f"duck: {type(exc).__name__}: {exc}")
            time.sleep(0.1)


if __name__ == "__main__":
    comtypes.CoInitialize()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        for s in sessions():
            tag = "  <- Nova" if is_nova(s, set()) else ""
            print(f"{s['pid']:>6}  {s['name']:<32} vol={s['vol'].GetMasterVolume():.2f} state={s['state']}{tag}")
    elif cmd == "demo":
        flag = {"on": True}
        d = Ducker(lambda: flag["on"], log=print)
        time.sleep(3)
        flag["on"] = False
        time.sleep(3)
