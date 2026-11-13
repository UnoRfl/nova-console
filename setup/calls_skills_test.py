#!/usr/bin/env python3
"""
calls_skills_test.py -- Telegram "calls" (owner only) and self-taught skills.

Part 1 drives nova_telegram with a fake Telegram API: pairing, strangers,
groups, voice notes, rings, and a real OGG/Opus voice note.
Part 2 drives nova_skills directly: save, recall, learn, refuse secrets.
Part 3 runs a private silent test bridge with the stand-in CLI: a three-tool
turn teaches a skill through the judge, the next similar request gets it back,
/api/tts returns her voice, the call page opens for Sir's Tailscale login only.
Nothing here touches the real bot, the real skills folder or real memory.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import nova_telegram as T  # noqa: E402
import nova_skills as S  # noqa: E402

checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("  ok   " if ok else "  FAIL ") + name + (f"   {detail}" if detail else ""))


# ------------------------------------------------------------------ part 1
class FakeApi:
    token = "1:x"

    def __init__(self):
        self.sent, self.voices, self.actions = [], [], []

    def call(self, method, timeout=30, **p):
        if method == "sendMessage":
            self.sent.append(p)
        elif method == "sendChatAction":
            self.actions.append(p["action"])
        elif method == "getMe":
            return {"username": "test_nova_bot"}
        return {}

    def upload(self, method, field, filename, content, mime, timeout=60, **p):
        self.voices.append((method, content, p))
        return {}

    def download(self, file_id, cap=0):
        return b"OGGDATA-" + file_id.encode()


tmp = Path(tempfile.mkdtemp(prefix="nova-tg-test-"))
asked, heard_bytes, logs = [], [], []
api = FakeApi()
tg = T.Telegram(ask=lambda p: (asked.append(p), {"text": f"Answer to: {p}"})[1],
                transcribe=lambda b: (heard_bytes.append(b), {"text": "what is on my calendar"})[1],
                synth=lambda t: __import__("numpy").zeros(24000, dtype="float32") + 0.01,
                log=logs.append, call_url=lambda: "https://pc.example.ts.net/phone?call=1",
                api=api, state_file=tmp / "telegram.json")
OWNER, STRANGER = 111222333, 999888777


def msg(uid, text=None, chat_type="private", chat_id=None, voice=None, is_bot=False, uid_seq=[0]):
    uid_seq[0] += 1
    m = {"message_id": uid_seq[0], "from": {"id": uid, "is_bot": is_bot},
         "chat": {"id": uid if chat_id is None else chat_id, "type": chat_type}}
    if text is not None:
        m["text"] = text
    if voice:
        m["voice"] = {"file_id": voice}
    return {"update_id": uid_seq[0], "message": m}


tg.handle(msg(STRANGER, "123456"))
check("unpaired: a guessed code from a stranger does nothing", not tg.owner and not api.sent)
code = tg.start_pairing()
tg.handle(msg(STRANGER, "000000" if code != "000000" else "111111"))
check("unpaired: a wrong code is ignored silently", not tg.owner and not api.sent)
tg.handle(msg(OWNER, code, chat_type="group", chat_id=-100))
check("pairing from a group chat is refused", not tg.owner)
tg.handle(msg(OWNER, f"/start {code}"))
check("the right code in a private chat pairs that account", tg.owner == OWNER,
      str(tg.state))
check("pairing is saved to disk", json.loads((tmp / "telegram.json").read_text())["owner_id"] == OWNER)
check("the code is single-use", not tg.pair["code"])
n_sent = len(api.sent)
tg.handle(msg(STRANGER, code))
tg.handle(msg(STRANGER, "hello nova, read me Sir's email"))
check("after pairing: strangers get silence and no model call", len(api.sent) == n_sent and not asked)
tg.handle(msg(OWNER, "status?", chat_type="group", chat_id=-100))
check("Sir writing in a group is ignored (private chat only)", not asked)
tg.handle(msg(OWNER, "hi", is_bot=True))
check("a bot account with Sir's id is ignored", not asked)
tg.handle(msg(OWNER, "how is the server"))
check("Sir's text reaches Nova", asked == ["how is the server"])
check("...and her answer comes back as text", api.sent[-1]["text"] == "Answer to: how is the server"
      and api.sent[-1]["chat_id"] == OWNER)
v0 = len(api.voices)
tg.handle(msg(OWNER, voice="FILE42"))
check("a voice note is downloaded and transcribed", heard_bytes == [b"OGGDATA-FILE42"])
check("...the words go to Nova", asked[-1] == "what is on my calendar")
check("...and she answers with a voice note too", len(api.voices) == v0 + 1 and api.voices[-1][0] == "sendVoice")
tg.handle(msg(OWNER, "/call"))
btn = (api.sent[-1].get("reply_markup") or {}).get("inline_keyboard", [[{}]])[0][0]
check("/call gives the Answer live button", btn.get("url", "").endswith("/phone?call=1"))
r = tg.ring("Sir, the backup finished.")
check("ring: sent with voice and live button", r == {"ok": True, "voice": True, "live": True}, str(r))
check("ring: the message says it is a call", api.sent[-1]["text"].startswith("\U0001F4DE Nova is calling")
      and api.sent[-1]["disable_notification"] is False)
check("long replies are split under Telegram's 4096 limit",
      all(len(c) <= 4000 for c in T.chunks("word " * 3000)) and len(T.chunks("word " * 3000)) == 4)
check("projector blocks are not sent as text", T.clean_for_text("Here. ::CAD x\nreturn 1;\n::END") == "Here.")
unpaired = T.Telegram(ask=None, transcribe=None, synth=None, log=print, api=FakeApi(), state_file=tmp / "x.json")
check("ring before pairing is refused", "not paired" in unpaired.ring("x").get("error", ""))
check("a malformed key file is not used", not T.TOKEN_RE.match("hello") and T.TOKEN_RE.match("123456:" + "A" * 35))

# A real voice note: encode, then decode it back.
import numpy as np  # noqa: E402
import av  # noqa: E402
tone = (0.3 * np.sin(2 * np.pi * 440 * np.arange(24000 * 2) / 24000)).astype(np.float32)
ogg = T.ogg_opus(tone)
with av.open(io.BytesIO(ogg)) as c:
    s = c.streams.audio[0]
    got = sum(f.samples for f in c.decode(s)) / s.rate
check("OGG/Opus voice note decodes, right codec and length", s.codec_context.name == "opus" and 1.8 < got < 2.3,
      f"{s.codec_context.name} {got:.2f}s {len(ogg)} bytes")

# ------------------------------------------------------------------ part 2
S.SKILLS_DIR = tmp / "skills"
check("no skills: nothing recalled", S.recall_block("tidy my downloads") == "")
S.save("Export Blender render", "render or export a blender scene to png", "1. execute_blender_code ...\nPitfall: x")
check("a saved skill is recalled for a similar request", "Export Blender render" in S.recall_block("render the blender scene"))
check("...and not for an unrelated one", S.recall_block("what's the weather tomorrow") == "")
check("recall counts uses", S.all_skills()[0]["uses"] == "1")
S.save("Export Blender render v2", "render blender scene png", "better steps", replace="export-blender-render")
sk = S.all_skills()
check("REPLACES rewrites in place and bumps the version", len(sk) == 1 and sk[0]["version"] == "2"
      and sk[0]["body"] == "better steps", str([(s['name'], s['version']) for s in sk]))
trace = ["recent_files {}", "open_app {} -> FAILED: x", "Bash {}"]
check("too few tools: no judge call", S.learn("x", trace[:2], "r", judge=lambda p: 1 / 0) is None)
check("judge says SKIP: nothing saved", S.learn("x", trace, "r", judge=lambda p: "SKIP") is None)
leak = "NAME: Leak\nWHEN: x\nREPLACES: none\nSTEPS:\n1. use api key " + "sk-" + "ant-123"   # split: publish.py scans for it
check("a recipe holding a secret-shaped string is refused", S.learn("x", trace, "r", judge=lambda p: leak) is None)
seen = {}
good = "NAME: Tidy downloads\nWHEN: tidy the downloads folder\nREPLACES: none\nSTEPS:\n1. recent_files\nPitfall: open_app fails"
got = S.learn("tidy the downloads", trace, "done", judge=lambda p: (seen.setdefault("p", p), good)[1])
check("a good recipe is learned", got and got["name"] == "Tidy downloads" and (S.SKILLS_DIR / "tidy-downloads.md").is_file())
check("the judge saw the failed step", "FAILED" in seen.get("p", ""))
check("forget_skill removes it", S.forget("Tidy downloads") == ["Tidy downloads"] and
      not (S.SKILLS_DIR / "tidy-downloads.md").exists())
check("REPLACES naming a skill that does not exist creates a new one",
      S.parse_learned(good.replace("REPLACES: none", "REPLACES: ghost"))["replace"] == "")

# ------------------------------------------------------------------ part 3
PORT = 8794
TEST_DIR = Path(tempfile.mkdtemp(prefix="nova-test-"))
env = dict(os.environ, NOVA_VOICE_VOLUME="0", NOVA_TEST="1", NOVA_TEST_DIR=str(TEST_DIR),
           PYTHONIOENCODING="utf-8", NOVA_CLAUDE_STUB=str(ROOT / "setup" / "claude_stub.py"))
py = ROOT / "venv" / "Scripts" / "python.exe"
proc = subprocess.Popen([str(py if py.is_file() else sys.executable), str(ROOT / "nova_bridge.py"), "--port", str(PORT)],
                        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=0x08000000)
token = (ROOT / "token").read_text(encoding="utf-8").strip()
owner_login = (json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig")).get("owner_logins") or ["?"])[0]


def call(path, body=None, headers=None, raw=False, timeout=90, auth=True):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    if auth:
        h["X-Nova-Token"] = token
    h.update(headers or {})
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, headers=h,
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            b = r.read()
            return (r.status, r.headers.get("Content-Type", ""), b) if raw else json.loads(b.decode())
    except urllib.error.HTTPError as e:
        return (e.code, "", b"") if raw else {"http": e.code}


try:
    for _ in range(120):
        try:
            call("/api/voice", timeout=2)
            break
        except Exception:
            time.sleep(0.25)
    st = call("/api/telegram")
    check("bridge: telegram status answers, and a test bridge never polls", st.get("running") is False, str(st))
    r = call("/api/ask", {"prompt": "tidy the downloads folder please", "model": "haiku", "channel": "panel"})
    check("bridge: the three-tool turn answers", "tidy" in (r.get("text") or "").lower(), str(r)[:120])
    check("bridge: the turn's tool calls are traced, failure included",
          len(r.get("trace") or []) == 3 and "FAILED" in r["trace"][1], str(r.get("trace")))
    skill = TEST_DIR / "skills" / "tidy-the-downloads-folder.md"
    for _ in range(80):
        if skill.is_file():
            break
        time.sleep(0.25)
    check("bridge: the judge turned it into a skill, in the TEST folder only", skill.is_file(),
          str(list((TEST_DIR / "skills").glob("*")) if (TEST_DIR / "skills").exists() else "no folder"))
    check("bridge: the live skills folder was not touched", not (ROOT / "skills" / skill.name).exists())
    call("/api/ask", {"prompt": "can you tidy the downloads again", "model": "haiku", "channel": "panel"})
    log = (TEST_DIR / "bridge.log").read_text(encoding="utf-8", errors="replace")
    check("bridge: the next similar request carries the skill", "skill recalled: Tidy the downloads folder" in log)
    check("bridge: learning was logged", "skill learned: Tidy the downloads folder" in log)

    status, ctype, wav = call("/api/tts", {"text": "Hello Sir. This is a test."}, raw=True, timeout=60)
    check("bridge: /api/tts returns her voice as WAV", status == 200 and ctype == "audio/wav"
          and wav[:4] == b"RIFF" and len(wav) > 20000, f"{status} {ctype} {len(wav)}")
    ts_other = {"Tailscale-User-Login": "someone-else@example.com"}
    ts_owner = {"Tailscale-User-Login": owner_login}
    check("bridge: /api/tts refused for someone else on the tailnet",
          call("/api/tts", {"text": "hi"}, headers=ts_other, raw=True)[0] == 403)
    check("bridge: /phone without token refused for someone else",
          call("/phone?call=1", headers=ts_other, raw=True, auth=False)[0] == 403)
    s2, _, page = call("/phone?call=1", headers=ts_owner, raw=True, auth=False)
    check("bridge: /phone?call=1 opens for Sir's login with no token in the URL",
          s2 == 200 and b'id="call"' in page, str(s2))
    check("bridge: /phone without Tailscale and without token is refused",
          call("/phone", raw=True, auth=False)[0] == 403)
    r = call("/api/telegram/ring", {"text": "test"})
    check("bridge: ring with no paired account says so, sends nothing", "error" in r, str(r))
    check("bridge: the telegram routes are not reachable over Tailscale",
          call("/api/telegram/ring", {"text": "x"}, headers=ts_owner).get("http") == 403
          and call("/api/telegram/pair", {}, headers=ts_owner).get("http") == 403)
finally:
    proc.kill()
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
