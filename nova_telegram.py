"""Nova on Telegram: her way of "calling" Sir when he is away from the desk.

Real phone calls cost money per minute, so this is the free version:

  * A RING is an urgent message in his private chat with the bot. On his phone
    he gives that one chat its own loud ringtone, so it sounds like a call.
    It carries a spoken voice note (her own voice, made on this PC) and an
    "Answer live" button that opens the phone page in call mode (a two-way
    conversation over Tailscale).
  * HE CALLS HER by sending the bot a text or a voice note. Voice notes are
    transcribed on this PC with Whisper; she answers in text and a voice note.

Only Sir. The bot answers exactly one Telegram account: the one that paired
with a code that was spoken and shown on this PC. Everything else -- other
people, groups, channels, forwarded bots -- gets silence: no reply, no model
call, nothing stored. A Telegram user id is assigned by Telegram's servers and
cannot be faked by the sender.

Files (personal, never published):
  telegram.key   the bot token from @BotFather (one line)
  telegram.json  {"owner_id": ..., "offset": ...}

Long polling (getUpdates), so nothing is exposed to the internet and no
server or webhook is needed. Standard library + numpy/av only.
"""
from __future__ import annotations

import io
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KEY_FILE = ROOT / "telegram.key"
STATE_FILE = ROOT / "telegram.json"
API = "https://api.telegram.org"
PAIR_SECONDS = 10 * 60
MAX_TEXT = 4000                      # Telegram's limit is 4096
SPOKEN_CHARS = 900                   # longer replies: speak the start, the rest is in the text
TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")


def load_key() -> str:
    try:
        key = KEY_FILE.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return ""
    return key if TOKEN_RE.match(key) else ""


def clean_for_text(text: str) -> str:
    """Drop projector blocks; Telegram shows plain text."""
    return re.sub(r"::(CAD|MODEL|HOLO|BUILD)\b[\s\S]*?(::END|$)", "", text or "").strip()


def chunks(text: str, size: int = MAX_TEXT) -> list[str]:
    out, text = [], text.strip()
    while len(text) > size:
        cut = text.rfind("\n", 0, size)
        if cut < size // 2:
            cut = text.rfind(" ", 0, size)
        if cut < size // 2:
            cut = size
        out.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        out.append(text)
    return out


def ogg_opus(audio, rate: int = 24000) -> bytes:
    """float32 mono samples -> OGG/Opus bytes (what Telegram shows as a voice note)."""
    import av
    import numpy as np
    pcm = (np.clip(np.asarray(audio, dtype=np.float32), -1, 1) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with av.open(buf, "w", format="ogg") as out:
        st = out.add_stream("libopus", rate=rate)
        st.layout = "mono"
        step = rate // 50 * 10                           # 200 ms frames
        for i in range(0, len(pcm), step):
            part = pcm[i:i + step]
            frame = av.AudioFrame.from_ndarray(part.reshape(1, -1), format="s16", layout="mono")
            frame.sample_rate = rate
            for pkt in st.encode(frame):
                out.mux(pkt)
        for pkt in st.encode(None):
            out.mux(pkt)
    return buf.getvalue()


class Api:
    def __init__(self, token: str, opener=None) -> None:
        self.token = token
        self.open = opener or urllib.request.urlopen

    def call(self, method: str, timeout: float = 30, **params) -> dict:
        data = json.dumps({k: v for k, v in params.items() if v is not None}).encode("utf-8")
        req = urllib.request.Request(f"{API}/bot{self.token}/{method}", data=data,
                                     headers={"Content-Type": "application/json"})
        with self.open(req, timeout=timeout) as r:
            out = json.loads(r.read().decode("utf-8") or "{}")
        if not out.get("ok"):
            raise RuntimeError(f"{method}: {out.get('description', 'failed')}")
        return out.get("result")

    def upload(self, method: str, field: str, filename: str, content: bytes, mime: str,
               timeout: float = 60, **params) -> dict:
        boundary = uuid.uuid4().hex
        body = io.BytesIO()
        for k, v in params.items():
            if v is None:
                continue
            v = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
            body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
        body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
                   f"filename=\"{filename}\"\r\nContent-Type: {mime}\r\n\r\n".encode())
        body.write(content)
        body.write(f"\r\n--{boundary}--\r\n".encode())
        req = urllib.request.Request(f"{API}/bot{self.token}/{method}", data=body.getvalue(),
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with self.open(req, timeout=timeout) as r:
            out = json.loads(r.read().decode("utf-8") or "{}")
        if not out.get("ok"):
            raise RuntimeError(f"{method}: {out.get('description', 'failed')}")
        return out.get("result")

    def download(self, file_id: str, cap: int = 20_000_000) -> bytes:
        info = self.call("getFile", file_id=file_id)
        if int(info.get("file_size") or 0) > cap:
            raise RuntimeError("file too large")
        req = urllib.request.Request(f"{API}/file/bot{self.token}/{info['file_path']}")
        with self.open(req, timeout=60) as r:
            return r.read(cap + 1)[:cap]


class Telegram:
    """ask(prompt) -> dict like /api/ask; transcribe(bytes) -> {"text"} ;
    synth(text) -> float32 24 kHz samples (or raises); call_url() -> str."""

    def __init__(self, ask, transcribe, synth, log, call_url=lambda: "", announce=None,
                 api: Api | None = None, state_file: Path = STATE_FILE) -> None:
        self.ask, self.transcribe, self.synth, self.log = ask, transcribe, synth, log
        self.call_url, self.announce = call_url, announce
        self.state_file = state_file
        key = "" if api else load_key()
        self.api = api or (Api(key) if key else None)
        self.state = self._load()
        self.pair = {"code": "", "until": 0.0}
        self.ignored = 0
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.stop_flag = threading.Event()
        self.username = ""
        self.real_call = None                            # set by the bridge: rings from her own number

    # ------------------------------------------------------------ state
    def _load(self) -> dict:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        self.state_file.write_text(json.dumps(self.state, indent=1), encoding="utf-8")

    @property
    def owner(self) -> int:
        return int(self.state.get("owner_id") or 0)

    def status(self) -> dict:
        return {"configured": bool(self.api), "paired": bool(self.owner), "bot": self.username,
                "pairing": bool(self.pair["code"]) and time.time() < self.pair["until"],
                "running": bool(self.thread and self.thread.is_alive()), "ignored": self.ignored}

    def start_pairing(self) -> str:
        self.pair = {"code": f"{secrets.randbelow(1_000_000):06d}", "until": time.time() + PAIR_SECONDS}
        return self.pair["code"]

    def unpair(self) -> None:
        self.state.pop("owner_id", None)
        self._save()

    # ------------------------------------------------------------ the gate
    def is_owner(self, msg: dict) -> bool:
        frm, chat = msg.get("from") or {}, msg.get("chat") or {}
        return (bool(self.owner) and chat.get("type") == "private" and not frm.get("is_bot")
                and frm.get("id") == self.owner and chat.get("id") == self.owner)

    def handle(self, upd: dict) -> None:
        msg = upd.get("message")
        if not msg:
            return                                       # edits, channel posts, callbacks: ignored
        chat, frm = msg.get("chat") or {}, msg.get("from") or {}
        text = (msg.get("text") or "").strip()
        if not self.owner:
            # Not paired yet: the only thing anyone can do is present the code
            # that is on Sir's PC right now, in a private chat.
            m = re.fullmatch(r"/?(?:start\s+|pair\s+)?(\d{6})", text, re.I)
            ok = (m and self.pair["code"] and time.time() < self.pair["until"]
                  and secrets.compare_digest(m.group(1), self.pair["code"])
                  and chat.get("type") == "private" and not frm.get("is_bot")
                  and chat.get("id") == frm.get("id"))
            if ok:
                self.state["owner_id"] = frm["id"]
                self._save()
                self.pair = {"code": "", "until": 0.0}
                self.log(f"telegram: paired with account {frm['id']}")
                self.send("Paired, Sir. Only you can reach me here now. Give this chat its own "
                          "ringtone in Telegram, and that is how my calls will sound.")
                if self.announce:
                    self.announce("Sir, Telegram is paired. I will call you there when you are away.")
            else:
                self.ignored += 1
            return
        if not self.is_owner(msg):
            self.ignored += 1                            # silence; nothing kept about them
            return
        self.from_owner(msg, text)

    def from_owner(self, msg: dict, text: str) -> None:
        voice = msg.get("voice") or msg.get("audio")
        if text.lower() in ("/call", "call", "call me", "/start"):
            if text.lower() != "/start" and self.real_call and self.real_call():
                self.send("Ringing you now, Sir.", loud=False)
                return
            url = self.call_url()
            if url:
                self.send("Tap to talk to me live.", button=("Answer live", url))
            else:
                self.send("The live call page is not set up yet (Tailscale on the PC).")
            return
        if voice:
            self.chat_action("typing")
            try:
                heard = (self.transcribe(self.api.download(voice["file_id"])) or {}).get("text", "").strip()
            except Exception as exc:                     # noqa: BLE001
                self.log(f"telegram: voice note failed: {exc}")
                heard = ""
            if not heard:
                self.send("I could not make out that voice note, Sir.")
                return
            text = heard
        if not text:
            return
        self.chat_action("typing")
        out = self.ask(text) or {}
        if out.get("error"):
            self.send(f"Something went wrong, Sir: {out.get('detail') or out['error']}")
            return
        reply = clean_for_text(out.get("text", "")) or "Done, Sir."
        self.reply(reply, voice=bool(voice))

    # ------------------------------------------------------------ sending
    def chat_action(self, action: str) -> None:
        try:
            self.api.call("sendChatAction", chat_id=self.owner, action=action, timeout=10)
        except Exception:                                # noqa: BLE001
            pass

    def send(self, text: str, button: tuple[str, str] | None = None, loud: bool = True) -> bool:
        if not (self.api and self.owner):
            return False
        try:
            parts = chunks(text)
            for i, part in enumerate(parts):
                markup = None
                if button and i == len(parts) - 1:
                    markup = {"inline_keyboard": [[{"text": button[0], "url": button[1]}]]}
                self.api.call("sendMessage", chat_id=self.owner, text=part, reply_markup=markup,
                              disable_notification=not loud, link_preview_options={"is_disabled": True})
            return True
        except Exception as exc:                         # noqa: BLE001
            self.log(f"telegram: send failed: {exc}")
            return False

    def send_voice(self, text: str, caption: str = "") -> bool:
        if not (self.api and self.owner):
            return False
        said = text if len(text) <= SPOKEN_CHARS else text[:SPOKEN_CHARS].rsplit(". ", 1)[0] + ". The rest is in the message."
        try:
            audio = self.synth(said)
            data = ogg_opus(audio)
            self.api.upload("sendVoice", "voice", "nova.ogg", data, "audio/ogg",
                            chat_id=self.owner, caption=caption[:1000] or None)
            return True
        except Exception as exc:                         # noqa: BLE001
            self.log(f"telegram: voice note failed: {exc}")
            return False

    def reply(self, text: str, voice: bool = False) -> None:
        if voice:
            self.chat_action("record_voice")
            self.send_voice(text)
        self.send(text)

    def ring(self, text: str, live: bool = True) -> dict:
        """Nova calls Sir: a loud message, a voice note, and the live-call button."""
        if not self.api:
            return {"error": "Telegram is not set up (no telegram.key)."}
        if not self.owner:
            return {"error": "Telegram is not paired with Sir yet."}
        text = " ".join(str(text).split())[:1500]
        url = self.call_url() if live else ""
        sent = self.send("\U0001F4DE Nova is calling\n\n" + text,
                         button=("Answer live", url) if url else None)
        voiced = self.send_voice(text) if sent else False
        self.log(f"telegram: ring ({'sent' if sent else 'FAILED'}, voice {'yes' if voiced else 'no'}): {text[:80]}")
        return {"ok": sent, "voice": voiced, "live": bool(url)}

    # ------------------------------------------------------------ polling
    def start(self) -> bool:
        if not self.api or (self.thread and self.thread.is_alive()):
            return False
        try:
            self.username = (self.api.call("getMe", timeout=15) or {}).get("username", "")
        except Exception as exc:                         # noqa: BLE001
            self.log(f"telegram: getMe failed: {exc}")
        self.thread = threading.Thread(target=self.run, daemon=True, name="telegram")
        self.thread.start()
        return True

    def run(self) -> None:
        wait = 2.0
        while not self.stop_flag.is_set():
            try:
                self._long_poll()
                wait = 2.0
            except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
                if "Conflict" in str(exc):
                    self.log("telegram: another copy of the bot is polling; backing off")
                    wait = 30.0
                else:
                    self.log(f"telegram: poll error: {str(exc)[:120]}")
                self.stop_flag.wait(wait)
                wait = min(wait * 2, 120.0)

    def _long_poll(self) -> None:
        data = json.dumps({"offset": self.state.get("offset"), "timeout": 50,
                           "allowed_updates": ["message"]}).encode("utf-8")
        req = urllib.request.Request(f"{API}/bot{self.api.token}/getUpdates", data=data,
                                     headers={"Content-Type": "application/json"})
        with self.api.open(req, timeout=70) as r:
            out = json.loads(r.read().decode("utf-8") or "{}")
        if not out.get("ok"):
            raise RuntimeError(out.get("description", "getUpdates failed"))
        for upd in out.get("result") or []:
            self.state["offset"] = int(upd["update_id"]) + 1
            self._save()                                 # never handle one twice, even after a crash
            try:
                with self.lock:
                    self.handle(upd)
            except Exception as exc:                     # noqa: BLE001
                self.log(f"telegram: handling failed: {exc}")
