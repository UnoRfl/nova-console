"""The main PC's end of Nova's real Telegram calls.

nova_caller.py on the headless PC holds Nova's own Telegram account and moves
raw call audio. This connects OUT to it (LAN, token, one controller) and does
everything that needs this PC: finding where Sir starts and stops talking,
Whisper on the GPU, Claude, and her voice.

  call(text)  ring Sir; when he picks up she says `text`, then it is a
              conversation until either side hangs up. Not answered ->
              on_missed(text) (the Telegram bot's message + voice note).
  Sir rings her number -> she answers with "Yes, Sir?".

Config (config.json "phoneline"): {"host": "192.168.0.108", "port": 8790,
"token": "..."} -- the token is personal and never published.
"""
from __future__ import annotations

import io
import json
import re
import socket
import struct
import threading
import time
import wave

import numpy as np

RATE = 48000
WIN = RATE // 50                         # 20 ms analysis windows (samples)
BYE = re.compile(r"^\W*(ok(ay)?[, ]+)?(bye|goodbye|good night|hang up|that'?s all|talk later|thanks?,? bye)\b", re.I)


def to_wav(pcm: bytes, rate: int = RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def to_48k_pcm(audio, rate: int = 24000) -> bytes:
    a = np.asarray(audio, dtype=np.float32)
    if rate != RATE and len(a):
        n = int(len(a) * RATE / rate)
        a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a)
    return (np.clip(a, -1, 1) * 32767).astype("<i2").tobytes()


class Detector:
    """Where does he start and stop talking? Energy with a learned noise floor
    (the same rule the phone page uses), on 20 ms windows."""

    def __init__(self, start_ms: int = 120, end_ms: int = 900, min_ms: int = 350, max_s: int = 30) -> None:
        self.start_n, self.end_n, self.min_n, self.max_n = start_ms // 20, end_ms // 20, min_ms // 20, max_s * 50
        self.reset()
        self.floor = 0.006

    def reset(self) -> None:
        self.state, self.loud, self.quiet, self.voiced, self.buf, self.pre = "idle", 0, 0, 0, bytearray(), []
        self.rest = b""

    def feed(self, pcm: bytes) -> bytes | None:
        """Feed audio; returns one finished utterance (PCM) when he stops talking."""
        # Call audio arrives 10 ms at a time, half of one analysis window. Fed
        # piece by piece, no window was ever complete and she heard NOTHING on
        # a real call ("Yes, Sir?" and then silence). Carry the remainder over.
        pcm = self.rest + pcm
        whole = len(pcm) // (WIN * 2) * (WIN * 2)
        pcm, self.rest = pcm[:whole], pcm[whole:]
        a = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        out = None
        for i in range(0, len(a) - WIN + 1, WIN):
            w = a[i:i + WIN]
            raw = pcm[i * 2:(i + WIN) * 2]
            rms = float(np.sqrt(np.mean(w * w)))
            loud = rms > max(0.012, self.floor * 3.2)
            if self.state == "idle":
                if not loud:
                    self.floor = self.floor * 0.97 + rms * 0.03
                self.pre = (self.pre + [raw])[-self.start_n - 3:]    # keep the first syllable
                self.loud = self.loud + 1 if loud else 0
                if self.loud >= self.start_n:
                    self.state, self.buf, self.quiet, self.voiced = "talking", bytearray(b"".join(self.pre)), 0, self.loud
            else:
                self.buf += raw
                if loud:
                    self.quiet, self.voiced = 0, self.voiced + 1
                else:
                    self.quiet += 1
                if self.quiet >= self.end_n or len(self.buf) >= self.max_n * WIN * 2:
                    if self.voiced >= self.min_n:
                        out = bytes(self.buf)
                    self.reset()
        return out


class PhoneLine:
    def __init__(self, cfg: dict, ask, transcribe, synth, log, on_missed=None, on_turn=None) -> None:
        self.host, self.port, self.token = cfg.get("host"), int(cfg.get("port", 8790)), cfg.get("token", "")
        self.ask, self.transcribe, self.synth, self.log = ask, transcribe, synth, log
        self.on_missed, self.on_turn = on_missed, on_turn
        self.sock: socket.socket | None = None
        self.send_lock = threading.Lock()
        self.connected = threading.Event()
        self.state = "idle"                  # idle | ringing | listening | thinking | speaking
        self.greeting = ""
        self.speaking_until = 0.0
        self.last_heard = 0.0
        self.det = Detector()
        self.rx_bytes, self.rx_peak = 0, 0     # what the call delivered from his side (for the log)
        self.turn_busy = threading.Lock()
        self.stop = threading.Event()

    # ------------------------------------------------------------ wire
    def _send(self, kind: bytes, payload: bytes) -> bool:
        s = self.sock
        if not s:
            return False
        try:
            with self.send_lock:
                s.sendall(kind + struct.pack(">I", len(payload)) + payload)
            return True
        except OSError:
            return False

    def cmd(self, name: str) -> bool:
        return self._send(b"J", json.dumps({"cmd": name}).encode())

    def _recv(self, s: socket.socket, n: int) -> bytes:
        out = bytearray()
        while len(out) < n:
            chunk = s.recv(n - len(out))
            if not chunk:
                raise ConnectionError("closed")
            out += chunk
        return bytes(out)

    # ------------------------------------------------------------ public
    def ready(self) -> bool:
        return self.connected.is_set()

    def status(self) -> dict:
        return {"connected": self.ready(), "state": self.state, "host": self.host}

    def call(self, text: str) -> bool:
        if not self.ready() or self.state != "idle":
            return False
        self.greeting = " ".join(str(text).split())[:1200]
        self.state = "ringing"
        return self.cmd("call")

    def hangup(self) -> None:
        self.cmd("hangup")

    # ------------------------------------------------------------ talking
    def say(self, text: str) -> None:
        """Speak into the call; returns when she should be done."""
        try:
            pcm = to_48k_pcm(self.synth(text))
        except Exception as exc:                          # noqa: BLE001
            self.log(f"phoneline: voice failed: {exc}")
            return
        self.state = "speaking"
        for i in range(0, len(pcm), RATE * 2):            # 1 s pieces
            self._send(b"A", pcm[i:i + RATE * 2])
        dur = len(pcm) / (RATE * 2)
        self.speaking_until = time.time() + dur + 0.35
        while time.time() < self.speaking_until and self.state == "speaking":
            time.sleep(0.05)
        if self.state == "speaking":
            self.state = "listening"
            self.det.reset()
            self.last_heard = time.time()

    def _turn(self, pcm: bytes) -> None:
        if not self.turn_busy.acquire(blocking=False):
            return
        try:
            self.state = "thinking"
            heard = ((self.transcribe(to_wav(pcm)) or {}).get("text") or "").strip()
            if not heard or len(re.sub(r"\W", "", heard)) < 2:
                self.state = "listening"
                return
            self.log(f"phoneline: heard {heard[:100]!r}")
            if BYE.search(heard):
                self.say("Goodbye, Sir.")
                self.hangup()
                return
            out = self.ask("[Live phone call: answer in one to three short spoken sentences, no lists, "
                           "no markdown, no links.] " + heard) or {}
            reply = (out.get("text") or "").strip() if not out.get("error") else "Sorry, Sir, something went wrong on my end."
            reply = re.sub(r"::(CAD|MODEL|HOLO|BUILD)\b[\s\S]*?(::END|$)", "", reply).strip() or "Done, Sir."
            if self.on_turn:
                self.on_turn(heard, reply)
            if self.state != "idle":
                self.say(reply)
        finally:
            self.turn_busy.release()

    def _event(self, ev: dict) -> None:
        name = ev.get("event")
        if name == "ready":
            self.log(f"phoneline: connected to Nova's line at {self.host}")
        elif name == "incoming":
            self.greeting = "Yes, Sir?"
        elif name == "answered":
            self.state, self.last_heard = "listening", time.time()
            self.rx_bytes, self.rx_peak = 0, 0
            greet = self.greeting or "Yes, Sir?"
            self.greeting = ""
            self.log(f"phoneline: call answered ({'he rang' if ev.get('incoming') else 'she rang'})")
            threading.Thread(target=self.say, args=(greet,), daemon=True).start()
        elif name == "ended":
            was = self.greeting
            self.state, self.greeting = "idle", ""
            self.det.reset()
            self.log(f"phoneline: call ended ({ev.get('reason')}); heard {self.rx_bytes / (RATE * 2):.1f} s of "
                     f"audio from him, peak level {self.rx_peak / 32768:.2f}")
            if not ev.get("answered") and was and self.on_missed:
                self.on_missed(was)
        elif name == "error":
            self.log(f"phoneline: line error: {ev.get('detail')}")
            if self.state == "ringing":
                was, self.state, self.greeting = self.greeting, "idle", ""
                if was and self.on_missed:
                    self.on_missed(was)

    def _audio(self, pcm: bytes) -> None:
        self.rx_bytes += len(pcm)
        if pcm:
            peak = int(np.abs(np.frombuffer(pcm[:len(pcm) // 2 * 2], dtype="<i2")).max())
            self.rx_peak = max(self.rx_peak, peak)
        if self.state != "listening" or time.time() < self.speaking_until:
            return
        done = self.det.feed(pcm)
        if done:
            self.last_heard = time.time()
            threading.Thread(target=self._turn, args=(done,), daemon=True).start()
        elif time.time() - self.last_heard > 75:
            self.last_heard = time.time()
            threading.Thread(target=lambda: (self.say("I will let you go, Sir."), self.hangup()), daemon=True).start()

    # ------------------------------------------------------------ connection
    def start(self) -> bool:
        if not (self.host and self.token):
            return False
        threading.Thread(target=self.run, daemon=True, name="phoneline").start()
        return True

    def run(self) -> None:
        wait = 3.0
        while not self.stop.is_set():
            try:
                s = socket.create_connection((self.host, self.port), timeout=8)
                s.settimeout(None)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.sock = s
                self._send(b"J", json.dumps({"auth": self.token}).encode())
                self.connected.set()
                wait = 3.0
                while True:
                    head = self._recv(s, 5)
                    body = self._recv(s, struct.unpack(">I", head[1:])[0])
                    if head[:1] == b"J":
                        self._event(json.loads(body))
                    elif head[:1] == b"A":
                        self._audio(body)
            except (OSError, ConnectionError, ValueError):
                pass
            finally:
                self.connected.clear()
                self.sock = None
                if self.state != "idle":
                    self.state = "idle"
            self.stop.wait(wait)
            wait = min(wait * 2, 60.0)
