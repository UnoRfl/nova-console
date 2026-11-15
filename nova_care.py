#!/usr/bin/env python3
"""nova_care.py -- she notices how Sir is doing, and learns what he is like.

Three things, all on this PC, none of it published or sent anywhere:

  Habits     every ten seconds she notes which app is in front (its name
             only, never the window's title or contents), whether he is
             there, and what Spotify is playing. Kept as totals per hour in
             ~/.nova/habits.db: hours per app, his usual hours, his late
             nights, his artists and songs.
  Care       from that she speaks up on her own, sparingly: a long stretch
             with no real break, a night that has run past one o'clock, a
             run of late nights. Never during a game (she waits until he
             tabs out), at most one such remark per care.repeat_minutes.
  Knowing    digest() is added to her standing brief ("what you have noticed
             about him"), state_line() rides along with a request when it is
             relevant ("he has been at it 3 hours"), and curiosity() lets
             her ask him one real question a day and remember the answer.

    python nova_care.py            # what she has noticed so far
    python nova_care.py --today    # today in detail

config.json, all optional:
  "care": {"enabled": true, "session_minutes": 120, "repeat_minutes": 90,
           "game_minutes": 240, "late_from": "01:00", "late_until": "05:00",
           "break_minutes": 8, "curiosity": true, "track": true}
"""
from __future__ import annotations

import ctypes
import json
import random
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path

ROOT = Path.home() / ".nova"
DB_FILE = ROOT / "habits.db"
CONFIG_FILE = ROOT / "config.json"

DEFAULTS = {
    "enabled": True,          # speak up about breaks and late nights
    "track": True,            # keep the habit totals at all
    "session_minutes": 120,   # at the desk this long with no real break -> a word
    "repeat_minutes": 90,     # and again after this much more
    "game_minutes": 240,      # inside a game she stays quiet until this, then says it once, briefly
    "break_minutes": 8,       # no input for this long counts as a real break
    "late_from": "01:00",     # a night that runs past this gets one remark
    "late_until": "05:00",
    "curiosity": True,        # one personal question a day, when he is just chatting
}

# What to call a process when she talks about it. Anything else is title-cased.
APP_NAMES = {
    "valorant-win64-shipping": "Valorant", "valorant": "Valorant", "robloxplayerbeta": "Roblox",
    "robloxstudiobeta": "Roblox Studio", "javaw": "Minecraft", "minecraft": "Minecraft",
    "gamemd": "Red Alert 2", "ra2": "Red Alert 2", "cs2": "Counter-Strike", "fivem": "FiveM",
    "code": "VS Code", "claude": "Claude", "chrome": "Chrome", "opera": "Opera", "msedge": "Edge",
    "discord": "Discord", "spotify": "Spotify", "blender": "Blender", "winword": "Word",
    "powerpnt": "PowerPoint", "excel": "Excel", "explorer": "File Explorer",
    "windowsterminal": "the terminal", "rhino": "Rhino", "telegram": "Telegram", "obs64": "OBS",
}
GAMES = {"valorant-win64-shipping", "valorant", "robloxplayerbeta", "javaw", "minecraft", "gamemd",
         "ra2", "cs2", "fivem", "mlbb"}
MAKING = {"code", "claude", "blender", "robloxstudiobeta", "rhino", "windowsterminal", "winword",
          "powerpnt", "excel", "devenv", "pycharm64", "notepad++"}
IGNORE = {"", "lockapp", "searchhost", "shellexperiencehost", "startmenuexperiencehost", "pythonw",
          "python", "applicationframehost", "textinputhost", "dwm", "idle"}


def cfg() -> dict:
    out = dict(DEFAULTS)
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig")).get("care")
        if isinstance(raw, dict):
            out.update({k: v for k, v in raw.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return out


def app_name(proc: str) -> str:
    p = (proc or "").lower()
    return APP_NAMES.get(p) or p.replace("_", " ").replace("-", " ").title()


def spoken(seconds: float) -> str:
    """'two and a half hours', 'three hours', 'fifty minutes' -- for speech."""
    m = int(round(seconds / 60))
    if m < 60:
        return f"{max(1, m)} minutes"
    half = round(m / 30) / 2                          # to the nearest half hour
    whole, frac = int(half), half - int(half)
    words = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
             "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen"]
    w = words[whole] if whole < len(words) else str(whole)
    if frac:
        return "an hour and a half" if whole == 1 else f"{w} and a half hours"
    return "an hour" if whole == 1 else f"{w} hours"


HOURS = ["twelve", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven"]


def spoken_clock(hour: int, minute: int) -> str:
    """'one in the morning', 'half past one in the morning', 'nearly two in the morning'."""
    if minute >= 40:
        return f"nearly {HOURS[(hour + 1) % 12]} in the morning"
    h = HOURS[hour % 12]
    lead = "" if minute < 10 else "quarter past " if minute < 25 else "half past "
    return f"{lead}{h} in the morning"


def _hm(text: str, default: int) -> int:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(text or ""))
    return int(m[1]) * 60 + int(m[2]) if m else default


def night_of(ts: float) -> str:
    """The 'day' a moment belongs to, with the day turning over at 06:00, so
    02:00 on Tuesday still counts as Monday night."""
    return time.strftime("%Y-%m-%d", time.localtime(ts - 6 * 3600))


# ------------------------------------------------------------- Spotify title

def spotify_title() -> str:
    """'Artist - Song' while Spotify plays; '' when paused, closed or showing an advert.
    Read from the window's title, which costs nothing and touches nothing."""
    try:
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        found: list[str] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def each(hwnd, _):
            n = user32.GetWindowTextLengthW(hwnd)
            if n < 4:
                return True
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            h = kernel32.OpenProcess(0x1000, False, pid.value)
            if not h:
                return True
            buf, size = ctypes.create_unicode_buffer(520), ctypes.c_ulong(520)
            ok = kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
            kernel32.CloseHandle(h)
            if ok and Path(buf.value).stem.lower() == "spotify":
                t = ctypes.create_unicode_buffer(n + 2)
                user32.GetWindowTextW(hwnd, t, n + 1)
                if " - " in t.value:
                    found.append(t.value)
            return True

        user32.EnumWindows(each, 0)
        for t in found:
            if not re.match(r"^(spotify|advertisement)\b", t, re.I):
                return t.strip()
    except Exception:                                 # noqa: BLE001
        pass
    return ""


# ------------------------------------------------------------------- storage

class Store:
    def __init__(self, path: Path | str = DB_FILE) -> None:
        self.path = str(path)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.executescript("""
            create table if not exists usage(day text, hour int, app text, seconds real,
                                             primary key(day, hour, app));
            create table if not exists sessions(start real primary key, end real, top text);
            create table if not exists plays(at real primary key, artist text, track text);
            create table if not exists state(k text primary key, v text);
        """)
        self.db.commit()

    def get(self, k: str, default=None):
        with self.lock:
            row = self.db.execute("select v from state where k=?", (k,)).fetchone()
        try:
            return json.loads(row[0]) if row else default
        except ValueError:
            return default

    def put(self, k: str, v) -> None:
        with self.lock:
            self.db.execute("insert into state(k,v) values(?,?) on conflict(k) do update set v=excluded.v",
                            (k, json.dumps(v)))
            self.db.commit()

    def add_usage(self, now: float, app: str, seconds: float) -> None:
        lt = time.localtime(now)
        with self.lock:
            self.db.execute(
                "insert into usage(day,hour,app,seconds) values(?,?,?,?) "
                "on conflict(day,hour,app) do update set seconds=seconds+excluded.seconds",
                (time.strftime("%Y-%m-%d", lt), lt.tm_hour, app, seconds))
            self.db.commit()

    def add_play(self, now: float, artist: str, track: str) -> None:
        with self.lock:
            self.db.execute("insert or replace into plays(at,artist,track) values(?,?,?)", (now, artist, track))
            self.db.commit()

    def save_session(self, start: float, end: float, top: str) -> None:
        with self.lock:
            self.db.execute("insert or replace into sessions(start,end,top) values(?,?,?)", (start, end, top))
            self.db.commit()

    def rows(self, sql: str, args: tuple = ()) -> list:
        with self.lock:
            return self.db.execute(sql, args).fetchall()


# ---------------------------------------------------------------------- care

LONG_MAKING = [
    "Sir, that is {dur} at the desk without a real break. Stand up and drink some water; {app} will keep.",
    "{dur} straight in {app}, Sir. Ten minutes away from the screen will cost you less than the mistake you are about to make.",
    "Sir, you have been at this for {dur}. I would take a short walk now; the problem is not going anywhere, I checked.",
    "A gentle observation, Sir: {dur} without stopping. Stretch, eat something, then come back and be brilliant.",
    "Sir, {dur} in one sitting. Your back has asked me to say something on its behalf.",
]
LONG_GAME = [
    "Sir, that was {dur} of {app}. Perhaps some water and a stretch before the next one.",
    "{dur} of {app}, Sir. I say this with affection: your eyes would like a few minutes off.",
    "Sir, {dur} in {app} without a break. Stand up for five minutes; your rank will survive it.",
]
LONG_GAME_NOW = [
    "Sir, {dur} of {app} without a break. After this one, take ten minutes.",
    "{dur} in, Sir. Have some water when this round ends.",
]
LONG_OTHER = [
    "Sir, you have been at the screen for {dur} without a real break. A few minutes away would do you good.",
    "That is {dur} straight, Sir. Stand up, look at something further away than a monitor, drink some water.",
    "Sir, {dur} and counting. I would take a short break now, before it takes you.",
]
AGAIN = [
    "Still here, Sir, and so are you: {dur} now. I did mention a break.",
    "Sir, {dur} now. I shall keep asking, politely, until you stand up.",
    "{dur}, Sir. At this point the chair is doing more of the work than you are. Take ten minutes.",
]
LATE = [
    "Sir, it is {clock}. Whatever this is will still be here in the morning, and you will be better at it.",
    "It is {clock}, Sir. I would call it a night soon.",
    "Sir, {clock}. You have done enough for one day; go and sleep.",
]
LATE_CLASS = [
    "Sir, it is {clock}, and you have {klass} at {when}. I would sleep now and thank myself later.",
    "It is {clock}, Sir, with {klass} at {when}. Bed would be the clever move.",
]
LATE_STREAK = [
    "Sir, it is {clock}, and that makes {n} late nights running. I am not counting to be unkind; I would simply like you rested.",
    "It is {clock}, Sir, for the {nth} night in a row. Let tonight be the one where you sleep.",
]
NTH = {2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh"}

# One question a day at most, only while he is just chatting. Topics she has not asked about yet.
QUESTIONS = [
    ("music", "what he has been listening to lately and what he likes about it"),
    ("building", "which of his projects he is most excited about right now, and why that one"),
    ("school", "how school is honestly going, and which subject he likes or dreads"),
    ("start", "how he first got into coding and making things"),
    ("goal", "what he wants to be doing a few years from now"),
    ("rest", "what he does when he actually wants to switch off"),
    ("food", "what he would eat right now if he could have anything"),
    ("friends", "who he usually plays or talks with, and what they are like"),
    ("proud", "something he made or did that he is quietly proud of"),
    ("stress", "what has been weighing on him most this week"),
    ("games", "what keeps him coming back to the games he plays"),
    ("sleep", "whether he is a night person by choice or by deadline"),
    ("family", "who is at home with him, and whether they know what he builds"),
    ("dream", "something he would build if time and money were no object"),
]


class Care:
    """Call tick() every ten seconds or so. It records, and returns a line to
    say aloud when one is due (else "")."""

    def __init__(self, store: Store | None = None, rng: random.Random | None = None) -> None:
        self.store = store or Store()
        self.rng = rng or random.Random()
        s = self.store.get("session") or {}
        self.session_start = float(s.get("start") or 0.0)   # 0 = not in a session
        self.last_active = float(s.get("last") or 0.0)
        self.apps: dict[str, float] = dict(s.get("apps") or {})
        self.nudged_at = float(s.get("nudged_at") or 0.0)   # session length (s) at the last remark
        self.nudges = int(s.get("nudges") or 0)
        self.last_tick = 0.0
        self.last_track = str(self.store.get("last_track") or "")
        self.track_since = 0.0
        self.track_saved = False
        self._saved = 0.0

    # -- recording
    def _persist(self, now: float, force: bool = False) -> None:
        if force or now - self._saved > 60:
            self._saved = now
            self.store.put("session", {"start": self.session_start, "last": self.last_active,
                                       "apps": self.apps, "nudged_at": self.nudged_at, "nudges": self.nudges})

    def top_app(self) -> str:
        return max(self.apps, key=self.apps.get) if self.apps else ""

    def tick(self, now: float | None = None, idle: float = 0.0, app: str = "", music: str | None = None,
             hold: bool = False, next_class=None) -> str:
        """idle: seconds since his last input. app: the process in front.
        music: Spotify's 'Artist - Song' ('' if none; None = look it up).
        hold: she may not speak just now (a game, quiet hours, he is away).
        next_class: optional (description, clock text) of his first class in the morning."""
        now = time.time() if now is None else now
        c = cfg()
        step = min(max(now - self.last_tick, 0.0), 30.0) if self.last_tick else 10.0
        self.last_tick = now
        app = (app or "").lower()
        brk = float(c["break_minutes"]) * 60
        present = idle < 120

        # a session ends when he has really stopped (or the PC was off / asleep)
        if self.session_start and (idle >= brk or now - self.last_active > brk + 60):
            end = self.last_active
            if end - self.session_start >= 600:
                self.store.save_session(self.session_start, end, self.top_app())
            self.session_start, self.apps, self.nudged_at, self.nudges = 0.0, {}, 0.0, 0
            self._persist(now, force=True)

        if present:
            if not self.session_start:
                self.session_start = now
            self.last_active = now
            if c["track"] and app not in IGNORE:
                self.apps[app] = self.apps.get(app, 0.0) + step
                self.store.add_usage(now, app, step)
            self._persist(now)

        if c["track"]:
            self._music(now, spotify_title() if music is None else music)

        if not c["enabled"] or not present or not self.session_start:
            return ""
        return self._late(now, c, hold, next_class) or self._long(now, c, hold, app)

    def _music(self, now: float, title: str) -> None:
        if title != self.last_track:
            self.last_track, self.track_since, self.track_saved = title, now, False
            self.store.put("last_track", title)
        elif title and not self.track_saved and now - self.track_since >= 30:
            # half a minute in: he is listening to it, not skipping past it
            artist, _, track = title.partition(" - ")
            self.store.add_play(self.track_since, artist.strip(), track.strip())
            self.track_saved = True

    # -- speaking up
    def _long(self, now: float, c: dict, hold: bool, app: str) -> str:
        length = now - self.session_start
        first = float(c["session_minutes"]) * 60
        again = float(c["repeat_minutes"]) * 60
        due = first if not self.nudges else self.nudged_at + again
        if length < due:
            return ""
        top = self.top_app() or app
        in_game = app in GAMES
        if hold:
            # She does not talk over a game. One exception, once: a very long
            # sitting inside one, said in a few words.
            if not (in_game and length >= float(c["game_minutes"]) * 60 and not self.store.get("game_said") == self.session_start):
                return ""
            self.store.put("game_said", self.session_start)
            pool = LONG_GAME_NOW
        elif self.nudges:
            pool = AGAIN
        elif top in GAMES:
            pool = LONG_GAME
        elif top in MAKING:
            pool = LONG_MAKING
        else:
            pool = LONG_OTHER
        self.nudged_at, self.nudges = length, self.nudges + 1
        self._persist(now, force=True)
        line = self._pick(pool, "long").format(dur=spoken(length), app=app_name(top))
        return line[:1].upper() + line[1:]

    def _late(self, now: float, c: dict, hold: bool, next_class) -> str:
        lt = time.localtime(now)
        cur = lt.tm_hour * 60 + lt.tm_min
        start, end = _hm(c["late_from"], 60), _hm(c["late_until"], 300)
        if not (start <= cur < end) or hold:
            return ""
        night = night_of(now)
        if self.store.get("late_said") == night:
            return ""
        self.store.put("late_said", night)
        nights = self.store.get("late_nights") or []
        if night not in nights:
            nights = (nights + [night])[-14:]
            self.store.put("late_nights", nights)
        streak = 1
        t = now
        while night_of(t - 86400) in nights:
            streak, t = streak + 1, t - 86400
        clock = spoken_clock(lt.tm_hour, lt.tm_min)
        # the long-sitting remark would otherwise follow within minutes
        if self.session_start and now - self.session_start > float(c["session_minutes"]) * 60 * 0.75:
            self.nudged_at, self.nudges = now - self.session_start, max(self.nudges, 1)
            self._persist(now, force=True)
        if streak >= 3:
            return self._pick(LATE_STREAK, "late").format(clock=clock, n=streak, nth=NTH.get(streak, f"{streak}th"))
        if next_class:
            return self._pick(LATE_CLASS, "late").format(clock=clock, klass=next_class[0], when=next_class[1])
        return self._pick(LATE, "late").format(clock=clock)

    def _pick(self, pool: list[str], key: str) -> str:
        """A line from the pool, never the one she used last time."""
        last = self.store.get("line_" + key)
        choices = [x for x in pool if x != last] or pool
        line = self.rng.choice(choices)
        self.store.put("line_" + key, line)
        return line

    # -- what she knows, for the model
    def state_line(self, now: float | None = None) -> str:
        """A note to ride along with his request when it matters, else ''."""
        now = time.time() if now is None else now
        bits = []
        if self.session_start and now - self.last_active < 300:
            length = now - self.session_start
            if length >= 90 * 60:
                top = self.top_app()
                bits.append(f"he has been at the desk for {spoken(length)} without a real break"
                            + (f", mostly in {app_name(top)}" if top else ""))
        lt = time.localtime(now)
        if lt.tm_hour < 5:
            bits.append(f"it is {lt.tm_hour}:{lt.tm_min:02d} at night for him")
        if not bits:
            return ""
        return ("(For your judgement only -- do not recite it, and do not nag: " + "; ".join(bits)
                + ". Let it colour your tone if it fits; say nothing about it if he is busy.)")

    def curiosity(self, now: float | None = None) -> str:
        """Once a day, a nudge to ask him one real question. '' if not today."""
        now = time.time() if now is None else now
        if not cfg()["curiosity"]:
            return ""
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        if self.store.get("asked_day") == day:
            return ""
        asked = self.store.get("asked_topics") or []
        left = [q for q in QUESTIONS if q[0] not in asked] or QUESTIONS
        if len(left) == len(QUESTIONS) and asked:
            asked = []
        key, about = left[0]
        self.store.put("asked_day", day)
        self.store.put("asked_topics", asked + [key])
        return ("(You are getting to know him. After answering, if the moment is relaxed, ask him ONE short, "
                f"genuine question about {about} -- in your own words, as a friend would, not as a survey. "
                "Skip it if he seems busy, tired or annoyed. When he answers, now or later, keep what you learn "
                "with a [[remember: ...]] line.)")


# -------------------------------------------------------------------- digest

def _fmt_hours(sec: float) -> str:
    h = sec / 3600
    return f"{h:.0f} h" if h >= 10 else f"{h:.1f} h"


def _clock(minutes: float) -> str:
    m = int(minutes) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def _median(xs: list[float]) -> float:
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else 0.0


def summary(store: Store | None = None, now: float | None = None, days: int = 14) -> dict:
    store = store or Store()
    now = time.time() if now is None else now
    since_day = time.strftime("%Y-%m-%d", time.localtime(now - days * 86400))
    week_day = time.strftime("%Y-%m-%d", time.localtime(now - 7 * 86400))
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    out: dict = {"days_observed": len(store.rows("select distinct day from usage where day>=?", (since_day,)))}
    out["apps_week"] = [(app_name(a), s) for a, s in store.rows(
        "select app, sum(seconds) s from usage where day>=? group by app order by s desc limit 8", (week_day,))]
    out["today"] = [(app_name(a), s) for a, s in store.rows(
        "select app, sum(seconds) s from usage where day=? group by app order by s desc limit 6", (today,))]
    out["today_total"] = sum(s for _a, s in store.rows("select app, sum(seconds) from usage where day=? group by app", (today,)))
    hours = store.rows("select hour, sum(seconds) s from usage where day>=? group by hour order by s desc", (since_day,))
    out["busiest_hours"] = [h for h, _s in hours[:4]]
    sess = store.rows("select start, end, top from sessions where start>=? order by start", (now - days * 86400,))
    out["longest_session"] = max((e - s for s, e, _t in sess), default=0.0)
    by_night: dict[str, list[tuple[float, float]]] = {}
    for s, e, _t in sess:
        by_night.setdefault(night_of(s), []).append((s, e))
    starts, ends = [], []
    for _night, items in by_night.items():
        first, last = min(i[0] for i in items), max(i[1] for i in items)
        lt = time.localtime(first)
        starts.append(lt.tm_hour * 60 + lt.tm_min)
        le = time.localtime(last)
        m = le.tm_hour * 60 + le.tm_min
        ends.append(m + 1440 if m < 360 else m)       # past midnight sorts after the evening
    out["usual_start"] = _clock(_median(starts)) if len(starts) >= 3 else ""
    out["usual_end"] = _clock(_median(ends)) if len(ends) >= 3 else ""
    out["late_nights_week"] = len([n for n in (store.get("late_nights") or []) if n >= week_day])
    plays_since = now - days * 86400
    out["artists"] = store.rows("select artist, count(*) c from plays where at>=? and artist<>'' "
                                "group by artist order by c desc limit 8", (plays_since,))
    out["tracks"] = store.rows("select artist, track, count(*) c from plays where at>=? and track<>'' "
                               "group by artist, track order by c desc limit 8", (plays_since,))
    out["plays"] = store.rows("select count(*) from plays where at>=?", (plays_since,))[0][0]
    ph = store.rows("select cast(strftime('%H', at, 'unixepoch', 'localtime') as int) h, count(*) c "
                    "from plays where at>=? group by h order by c desc limit 3", (plays_since,))
    out["music_hours"] = [h for h, _c in ph]
    last = store.rows("select artist, track, at from plays order by at desc limit 1")
    out["last_play"] = last[0] if last else None
    return out


def digest(store: Store | None = None, now: float | None = None) -> str:
    """A few lines for her standing brief. '' until there is something to say."""
    s = summary(store, now)
    lines = []
    if s["apps_week"] and s["days_observed"] >= 2:
        lines.append("Where his time went this week: " + ", ".join(
            f"{a} {_fmt_hours(sec)}" for a, sec in s["apps_week"][:6] if sec >= 900) + ".")
    if s["usual_start"] and s["usual_end"]:
        lines.append(f"He usually sits down around {s['usual_start']} and stops around {s['usual_end']}.")
    if s["busiest_hours"] and s["days_observed"] >= 3:
        lines.append("His busiest hours: " + ", ".join(f"{h:02d}:00" for h in sorted(s["busiest_hours"])) + ".")
    if s["longest_session"] >= 3 * 3600:
        lines.append(f"Longest sitting without a break lately: {spoken(s['longest_session'])}.")
    if s["late_nights_week"]:
        lines.append(f"Nights past one o'clock this week: {s['late_nights_week']}.")
    if s["plays"] >= 5:
        lines.append("What he listens to on Spotify lately: " + ", ".join(
            f"{a} ({c})" for a, c in s["artists"][:6]) + ".")
        top = [f"{t} by {a}" for a, t, c in s["tracks"][:4] if c >= 2]
        if top:
            lines.append("Songs on repeat: " + "; ".join(top) + ".")
        if s["music_hours"]:
            lines.append("He mostly listens around " + ", ".join(f"{h:02d}:00" for h in sorted(s["music_hours"])) + ".")
    if not lines:
        return ""
    return ("You have watched how he uses this PC (apps in front, hours, Spotify) and this is what you have "
            "noticed. Use it the way a friend who pays attention would: to suggest, to tease gently, to pick music "
            "he would like, to notice when something is unusual. Never read it out as a report unless he asks, "
            "and call the `habits` tool for today's exact numbers.\n" + "\n".join("- " + x for x in lines))


def report(store: Store | None = None, now: float | None = None, care: "Care | None" = None) -> str:
    """Plain text for the `habits` tool: today, this week, music."""
    now = time.time() if now is None else now
    s = summary(store, now)
    out = []
    if care and care.session_start and now - care.last_active < 300:
        out.append(f"Current sitting: {spoken(now - care.session_start)} without a real break"
                   + (f", mostly {app_name(care.top_app())}" if care.top_app() else "") + ".")
    out.append(f"Today at the PC: {_fmt_hours(s['today_total'])}"
               + (" -- " + ", ".join(f"{a} {_fmt_hours(sec)}" for a, sec in s["today"]) if s["today"] else "") + ".")
    if s["apps_week"]:
        out.append("Last 7 days: " + ", ".join(f"{a} {_fmt_hours(sec)}" for a, sec in s["apps_week"]) + ".")
    if s["usual_start"]:
        out.append(f"Usual hours: from about {s['usual_start']} to about {s['usual_end']}.")
    if s["longest_session"]:
        out.append(f"Longest sitting in two weeks: {spoken(s['longest_session'])}.")
    out.append(f"Nights past one this week: {s['late_nights_week']}.")
    if s["plays"]:
        out.append(f"Spotify, last 14 days ({s['plays']} plays): artists " + ", ".join(
            f"{a} ({c})" for a, c in s["artists"]) + ".")
        out.append("Most played songs: " + "; ".join(f"{t} by {a} ({c})" for a, t, c in s["tracks"]) + ".")
        if s["last_play"]:
            a, t, at = s["last_play"]
            out.append(f"Last played: {t} by {a}, {time.strftime('%a %H:%M', time.localtime(at))}.")
    else:
        out.append("No Spotify plays recorded yet.")
    out.append(f"(Observed on {s['days_observed']} of the last 14 days. App names only; no window contents are kept.)")
    return "\n".join(out)


_shared: Care | None = None
_shared_lock = threading.Lock()


def shared() -> Care:
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = Care()
        return _shared


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    print(report())
    print()
    print(digest() or "(nothing noticed yet)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
