#!/usr/bin/env python3
"""
nova_spotify.py -- "Nova, play SOS by SZA on Spotify."

Free and key-less: no Spotify developer app, no Web API, no OAuth.

  1. find   the Spotify link for the song / album / playlist / artist with a
            DuckDuckGo "site:open.spotify.com/<kind>" search, and its exact title
            from Spotify's public oEmbed endpoint.
  2. open   spotify:<kind>:<id> in Sir's own desktop app (it starts the app if
            it is closed).
  3. press  the page's big green Play button through UI Automation (Invoke, so
            no mouse moves and it works on the second monitor), then check that
            the player bar's button turned into "Pause".

Main path is Spotify's OWN search (spotify:search:<words>): wait until the
results are for these words, take the first row of the right type (Song /
Album / Playlist / Artist; the filter chip if "All" has none) and press that
row's Play button. Backup when that fails: a DuckDuckGo
"site:open.spotify.com/<kind>" lookup, open spotify:<kind>:<id>, press the
page's big Play button. Either way it then checks the player bar shows "Pause".

    python nova_spotify.py "blinding lights the weeknd"
    python nova_spotify.py "SOS SZA" --kind album
    python nova_spotify.py --find "abbey road" --kind album      # lookup only, plays nothing
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from ctypes import wintypes

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"}
KINDS = ("track", "album", "playlist", "artist")
WORD_KIND = {"song": "track", "track": "track", "single": "track", "album": "album", "ep": "album",
             "record": "album", "playlist": "playlist", "artist": "artist", "band": "artist"}

# --------------------------------------------------------------------- understanding him

_LEAD = (r"^(?:(?:hey|okay|ok|yo)\s+)?(?:nova[\s,]+)?"
         r"(?:please\s+|can you\s+|could you\s+|would you\s+|just\s+|like\s+|maybe\s+|now\s+)*")
PLAY = re.compile(_LEAD + r"(?:play|put on|queue up|start playing|throw on|blast)\s+(?P<rest>.+?)[\s.!?]*$", re.I)
ON_SPOTIFY = re.compile(r"\s*(?:,\s*)?(?:on|in|from|with|using)\s+spotify\b", re.I)
KIND_WORDS = re.compile(r"\b(?:the\s+|a\s+|an\s+|some\s+|my\s+)?(song|track|single|album|ep|record|playlist|artist|band)\b"
                        r"(?:\s+(?:called|named|titled))?\s*", re.I)


def parse_request(text: str) -> tuple[str, str] | None:
    """(query, kind) when Sir plainly asked for music on Spotify, else None.

    Plain "play X" is not enough (that could be a video, a game, a file): it
    has to say Spotify, or name a song / album / playlist / artist."""
    m = None
    # "Can you just try again? Could you like play a lo-fi album from Spotify?"
    for sentence in re.split(r"(?<=[.?!])\s+", " ".join((text or "").replace("’", "'").split())):
        m = PLAY.match(sentence) or m
    if not m:
        return None
    rest = m.group("rest")
    said_spotify = bool(ON_SPOTIFY.search(rest))
    rest = ON_SPOTIFY.sub(" ", rest)
    kind = "auto"
    found = list(KIND_WORDS.finditer(rest))
    if found:
        kind = WORD_KIND[found[0].group(1).lower()]
        rest = KIND_WORDS.sub(" ", rest)            # "an album for me, like a lo-fi album" -> both go
    rest = " ".join(rest.split()).strip(" ,.!?")
    # "for me, like a lo-fi" -> "lo-fi"; but "Like a Prayer" / "For Me" as titles stay.
    hedge = re.match(r"^(?:(?:me\s+)?for me\b[\s,]*|.*,\s*)(?:something like|like|maybe|kind of|sort of)\s+(.+)$",
                     rest, flags=re.I)
    if hedge:
        rest = hedge.group(1)
    rest = re.sub(r"^like\s+(?=some\b)", "", rest, flags=re.I)
    for _ in range(3):                             # peel filler off both ends
        rest = re.sub(r"^(?:me|please|just|the|some|my|a|an)\b[\s,]*", "", rest, flags=re.I)
        cut = re.sub(r"[\s,]*\b(?:for me|please|sir|nova|now|again|or something|maybe)$", "", rest,
                     flags=re.I).strip(" ,.!?")
        rest = cut or rest                         # "the song For Me": the title is all there is
    if not rest or len(rest) > 120 or (not said_spotify and kind == "auto"):
        return None
    if rest.lower() in ("music", "something", "anything", "a song", "it", "that", "this"):
        return None
    return rest, kind


def guess_kind(query: str, kind: str = "auto") -> tuple[str, str]:
    """Pull "album" / "playlist" out of the words if the caller did not say."""
    kind = (kind or "auto").lower()
    if kind in KINDS:
        return query, kind
    km = KIND_WORDS.search(query)
    if km:
        return (query[:km.start()] + " " + query[km.end():]).strip(), WORD_KIND[km.group(1).lower()]
    return query, "track"


# ------------------------------------------------------------------------- finding it

def _get(url: str, timeout: float = 10) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def oembed_title(kind: str, sid: str) -> str:
    try:
        u = "https://open.spotify.com/oembed?url=" + urllib.parse.quote(f"https://open.spotify.com/{kind}/{sid}")
        return json.loads(_get(u, 8)).get("title", "")
    except Exception:                                 # noqa: BLE001
        return ""


def find(query: str, kind: str = "track") -> tuple[str, str] | None:
    """(spotify id, title) of the best match, or None."""
    q = urllib.parse.quote(f"site:open.spotify.com/{kind} {query}")
    pat = re.compile(rf"open\.spotify\.com/(?:intl-[a-z-]+/)?{kind}/([A-Za-z0-9]{{22}})")
    for url in (f"https://html.duckduckgo.com/html/?q={q}", f"https://lite.duckduckgo.com/lite/?q={q}"):
        try:
            ids = pat.findall(urllib.parse.unquote(_get(url)))
        except Exception:                             # noqa: BLE001 -- try the next one
            continue
        if ids:
            return ids[0], oembed_title(kind, ids[0])
    return None


# ------------------------------------------------------------------- the desktop app

def _exe_of(pid: int) -> str:
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)              # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf, n = ctypes.create_unicode_buffer(520), wintypes.DWORD(520)
        k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
        return buf.value.rsplit("\\", 1)[-1].lower()
    finally:
        k.CloseHandle(h)


def _auto():
    import uiautomation as auto
    return auto


def _window():
    """Spotify's main window (the app is Chromium: class Chrome_WidgetWin_1)."""
    auto = _auto()
    for w in auto.GetRootControl().GetChildren():
        if w.ClassName == "Chrome_WidgetWin_1" and _exe_of(w.ProcessId) == "spotify.exe" and w.Name:
            return w
    return None


def _unminimise(win) -> None:
    user32 = ctypes.windll.user32
    hwnd = win.NativeWindowHandle
    if hwnd and user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 4)                     # SW_SHOWNOACTIVATE: a minimised page has no layout


def _in_player_bar(ctrl) -> bool:
    p = ctrl.GetParentControl()
    for _ in range(4):
        if not p:
            return False
        if (p.Name or "") == "Player controls":
            return True
        p = p.GetParentControl()
    return False


def _scan(win, title: str = ""):
    """(page play button, player-bar button, title seen on the page)."""
    auto = _auto()
    page = bar = None
    seen = not title
    want = {f"more options for {title}".lower(), f"explore {title}".lower(), f"play {title}".lower()}
    for ctrl, _d in auto.WalkControl(win, maxDepth=40):
        name = ctrl.Name or ""
        if not seen and name.lower() in want:
            seen = True
        if ctrl.ControlTypeName != "ButtonControl" or name not in ("Play", "Pause"):
            continue
        if _in_player_bar(ctrl):
            bar = bar or ctrl
        elif page is None:
            page = ctrl
        if page and bar and seen:
            break
    return page, bar, seen


def _not_responding_dialog():
    """The "Spotify is not responding, force it to close?" box a second Spotify
    process shows when it could not hand a link to the running one."""
    auto = _auto()
    for w in auto.GetRootControl().GetChildren():
        if w.ClassName == "#32770" and _exe_of(w.ProcessId) == "spotify.exe":
            return w
    return None


def _send(uri: str) -> None:
    """Hand a spotify: link to the app (starting it if closed). Never answers
    "Yes" to force-closing Sir's Spotify: says No and hands the link over again."""
    running = _window() is not None
    os.startfile(uri)
    time.sleep(2.0 if running else 5.0)             # leave the app alone while it takes the link
    for _ in range(2):
        box = _safe(_not_responding_dialog)
        if not box:
            return
        no = box.ButtonControl(Name="No", searchDepth=2)
        if no.Exists(1):
            _press(no)
        time.sleep(2.0)
        os.startfile(uri)
        time.sleep(2.0)


def _bar(win):
    """The player bar's Play/Pause button, found without walking the whole page."""
    group = win.GroupControl(Name="Player controls", searchDepth=20)
    if not group.Exists(0.5):
        return None
    for name in ("Pause", "Play"):
        btn = group.ButtonControl(Name=name, searchDepth=2)
        if btn.Exists(0):
            return btn
    return None


def _press(ctrl) -> None:
    try:
        ctrl.GetInvokePattern().Invoke()
    except Exception:                                 # noqa: BLE001
        ctrl.Click(simulateMove=False)


def _now_playing(win) -> str:
    """Spotify titles its window "Artist - Song" while something plays."""
    name = (win.Name or "").strip()
    return "" if name.lower().startswith("spotify") else name


def open_and_play(uri: str, title: str = "", wait: float = 25) -> dict:
    auto = _auto()
    with auto.UIAutomationInitializerInThread():
        _send(uri)
        end = time.time() + wait
        page = bar = None
        while time.time() < end:
            win = _window()
            if win:
                _unminimise(win)
                page, bar, seen = _safe(_scan, win, title) or (None, None, False)
                if page and seen:
                    break
            time.sleep(1.0)
        if not page:
            return {"ok": False, "error": "Spotify opened but I could not find its Play button."}
        if page.Name == "Pause":                       # this very page is already playing
            return {"ok": True, "already": True, "now": _now_playing(_window())}
        time.sleep(0.4)                                # let the page settle before pressing
        _press(page)
        now = _wait_playing()
        if now is None:
            return {"ok": False, "error": "I pressed Play but Spotify does not show it playing."}
        return {"ok": True, "now": now}


# ------------------------------------------------------- Spotify's own search (main path)

ROW_TYPES = {"track": ("Song",), "album": ("Album", "EP"),
             "playlist": ("Playlist",), "artist": ("Artist",)}
FALLBACK_TYPES = {"track": ("Single",), "album": ("Single", "Compilation"), "playlist": (), "artist": ()}
CHIP = {"track": "Songs", "album": "Albums", "playlist": "Playlists", "artist": "Artists"}
TYPE_WORDS = {"Song", "Single", "Album", "EP", "Compilation", "Playlist", "Artist", "Podcast",
              "Episode", "Audiobook", "Profile", "Genre"}


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", urllib.parse.unquote_plus(s or "").lower()).split())


def _core(title: str) -> str:
    """Title without edition noise: "Abbey Road (Remastered 2009)" -> "abbey road"."""
    t = re.sub(r"\s*[(\[][^)\]]*[)\]]", " ", title or "")
    t = re.split(r"\s+[-–—]\s+", t)[0]
    return _norm(t)


def _results(win, query: str):
    """Rows on the search page for exactly this query: [(title, type, play button)].
    None while the page still shows an older search or recent searches."""
    auto = _auto()
    want = _norm(query)
    grid = None
    loaded = False
    for ctrl, _d in auto.WalkControl(win, maxDepth=24):
        name = ctrl.Name or ""
        if ctrl.ControlTypeName == "GroupControl" and name.startswith("Search for "):
            loaded = _norm(name[len("Search for "):].rsplit("|", 1)[0]) == want
        if ctrl.ControlTypeName == "DataGridControl" and name == "Search results":
            grid = ctrl
            break
    if not loaded or grid is None:
        return None
    rows = []
    for item in grid.GetChildren():
        title, typ, btn = item.Name or "", "", None
        for ctrl, _d in auto.WalkControl(item, maxDepth=8):
            n = ctrl.Name or ""
            if not typ and ctrl.ControlTypeName == "TextControl" and n in TYPE_WORDS:
                typ = n
            if btn is None and ctrl.ControlTypeName == "ButtonControl" and re.match(r"^(?:Play|Pause)\b", n):
                btn = ctrl
        if btn is not None:
            rows.append((title, typ, btn))
    return rows


def _safe(fn, *a):
    """UI Automation throws COMError while Spotify re-renders: treat as "not yet"."""
    try:
        return fn(*a)
    except Exception:                                 # noqa: BLE001
        return None


def _pick(rows, kind, query: str = ""):
    """First row of the wanted type, preferring one whose whole title is in his
    words ("SOS SZA" -> the album SOS, not "SOS Deluxe: LANA" ranked above it)."""
    said = set(_norm(query).split())
    for types in (ROW_TYPES[kind], FALLBACK_TYPES[kind]):
        typed = [row for row in rows if row[1] in types]
        exact = [row for row in typed[:6] if said and set(_core(row[0]).split()) <= said]
        if exact or typed:
            return (exact or typed)[0]
    return None


def _wait_playing(timeout: float = 6.5) -> str | None:
    """Window title ("Artist - Song") once the player bar shows Pause, else None."""
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(0.5)
        win = _window()
        if not win:
            continue
        bar = _safe(_bar, win)
        if bar is not None and bar.Name == "Pause":
            time.sleep(0.6)
            return _now_playing(_window()) or "?"
    return None


def search_and_play(query: str, kind: str, wait: float = 25) -> dict:
    auto = _auto()
    with auto.UIAutomationInitializerInThread():
        _send("spotify:search:" + urllib.parse.quote(query))
        end = time.time() + wait
        rows, last = None, None
        while time.time() < end:
            win = _window()
            if win:
                _unminimise(win)
                rows = _safe(_results, win, query)
                # The old list lingers for a moment after the header changes:
                # only trust the rows once two reads in a row agree.
                titles = [r[0] for r in rows] if rows else None
                if titles and titles == last:
                    break
                last = titles
            time.sleep(0.8)
        if not rows:
            return {"ok": False, "error": "Spotify's search showed no results."}
        row = _pick(rows, kind, query)
        if row is None:                                # not in "All": narrow with the filter chip
            win = _window()
            chip = win.HyperlinkControl(Name=CHIP[kind], searchDepth=30)
            if chip.Exists(1):
                _press(chip)
                for _ in range(10):
                    time.sleep(0.8)
                    rows = _safe(_results, _window(), query) or []
                    if rows and (row := _pick(rows, kind, query) or (rows[0] if rows[0][1] == "" else None)):
                        break
        if row is None:
            return {"ok": False, "error": f"no {CHIP[kind].lower()} in Spotify's results."}
        title, typ, btn = row
        if btn.Name.startswith("Pause"):
            return {"ok": True, "already": True, "title": title, "type": typ, "now": _now_playing(_window())}
        _press(btn)
        now = _wait_playing()
        if now is None:
            return {"ok": False, "error": "I pressed Play but Spotify does not show it playing.", "title": title}
        return {"ok": True, "title": title, "type": typ, "now": now}


# ------------------------------------------------------------------------ the one call

LOG = os.path.join(os.path.expanduser("~"), ".nova", "spotify.log")


def _log(line: str) -> None:
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S  ") + line + "\n")
    except OSError:
        pass


class _OneAtATime:
    """Two requests driving Spotify at once fight (each opens its own search):
    a second one waits for the first, across processes."""
    def __enter__(self):
        import msvcrt
        self.f = open(os.path.join(os.path.expanduser("~"), ".nova", "spotify.lock"), "a+")
        end = time.time() + 60
        while True:
            try:
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
                return self
            except OSError:
                if time.time() > end:
                    raise TimeoutError("another Spotify request is still running")
                time.sleep(0.5)

    def __exit__(self, *exc):
        import msvcrt
        try:
            self.f.seek(0)
            msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self.f.close()


def play(query: str, kind: str = "auto") -> str:
    """Find and play a song / album / playlist / artist in Sir's Spotify app."""
    try:
        with _OneAtATime():
            out = _play(query, kind)
    except TimeoutError as exc:
        out = f"I couldn't play that, Sir: {exc}."
    _log(f"{kind:8} {query!r} -> {out}")
    return out


def _play(query: str, kind: str = "auto") -> str:
    query = (query or "").strip()
    if not query:
        return "Play what, Sir?"
    query, kind = guess_kind(query, kind)
    query = re.sub(r"\s+by\s+", " ", query).strip()  # "SOS by SZA" searches better as "SOS SZA"
    try:
        res = search_and_play(query, kind)
    except Exception as exc:                          # noqa: BLE001 -- UI changed? try the link route
        res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if res.get("ok"):
        typ = (res.get("type") or kind).lower().replace("track", "song")
        label = f"the {typ} {res.get('title')}" if res.get("title") else query
    else:
        hit = find(query, kind)                        # backup: web lookup + open the exact page
        if not hit:
            return f"I couldn't play {query!r} on Spotify, Sir: {res.get('error')}"
        sid, title = hit
        res = open_and_play(f"spotify:{kind}:{sid}", title)
        label = f"the {kind.replace('track', 'song')} {title}" if title else query
        if not res.get("ok"):
            return f"I couldn't play {label} on Spotify, Sir: {res.get('error')}"
    now = res.get("now") or ""
    head = "Already playing" if res.get("already") else "Playing"
    return f"{head} {label} on Spotify" + (f" -- now on: {now}." if now and now != "?" else ".")


def play_in_child(query: str, kind: str = "auto", timeout: int = 75) -> str:
    """play() in its own process (UI Automation wants a clean COM thread, and a
    stuck Spotify window must not hang the bridge or the tool server)."""
    import subprocess
    from pathlib import Path
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists():
        exe = exe.with_name("python.exe")
    kind = kind if kind in ("auto",) + KINDS else "auto"
    try:
        p = subprocess.run([str(exe), str(Path(__file__).resolve()), query, "--kind", kind],
                           capture_output=True, text=True, timeout=timeout, encoding="utf-8",
                           errors="replace", stdin=subprocess.DEVNULL, creationflags=0x08000000)
    except subprocess.TimeoutExpired:
        return "Spotify took too long to respond, Sir."
    out = (p.stdout or "").strip().splitlines()
    return out[-1] if out else ("Spotify failed: " + (p.stderr or "").strip()[-300:])


if __name__ == "__main__":
    import argparse
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--kind", default="auto", choices=("auto",) + KINDS)
    ap.add_argument("--find", action="store_true", help="look it up only, play nothing")
    a = ap.parse_args()
    if a.find:
        q, k = guess_kind(a.query, a.kind)
        print(k, find(q, k))
    else:
        print(play(a.query, a.kind))
