#!/usr/bin/env python3
"""
nova_memory.py -- Nova's long-term memory. Replaces "the last 60 lines of
memory.jsonl, pasted in at start-up".

One SQLite file (~/.nova/memory.db) with full-text search. No extra packages,
nothing leaves this machine.

    python nova_memory.py import                 # bring in memory.jsonl + project notes
    python nova_memory.py add "Uno prefers tea"  # store a fact
    python nova_memory.py search "minecraft server"
    python nova_memory.py forget 12              # delete by id
    python nova_memory.py list
    python nova_memory.py stats

Kinds of memory:
  fact      something Sir told her (preferences, people, decisions)
  project   notes about one of his projects, indexed read-only from the Claude
            Code memory folder so "restart the Cobblemon server" means something
  day       one line per exchange, grouped by date, for "what did we do yesterday"

What she recalls for a question is the best full-text matches, not everything.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path

ROOT = Path.home() / ".nova"
DB_FILE = ROOT / "memory.db"
LEGACY = ROOT / "memory.jsonl"
PROJECT_NOTES = Path.home() / ".claude" / "projects" / "C--Users-You-Downloads" / "memory"

_lock = threading.Lock()
# What the last recall_block did, for the brain view (see recall_block).
LAST_RECALL: list[int] = []     # ids of the rows it actually handed over
LAST_NEAR: list[int] = []       # ids that nearly made it: cut by the budget, or turned away by search()
LAST_DAYS: list[str] = []       # the diary days (YYYY-MM-DD) of the log lines it handed over
LAST_TERMS: list[str] = []      # the words it searched for (at most 8)
NEAR_MAX, TERMS_MAX = 12, 8

STOP = set("""a an the and or but if then so to of in on at by for with from as is are was were be been
i you he she it we they me my your his her its our their this that these those what which who how why
when where one two three four five plus minus times many much some any do does did can could would should will shall have has had not no yes please nova hey just
about tell show give get make let there here now up out me""".split())


def connect() -> sqlite3.Connection:
    ROOT.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_FILE, timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS memory (
            id      INTEGER PRIMARY KEY,
            kind    TEXT NOT NULL DEFAULT 'fact',
            text    TEXT NOT NULL,
            source  TEXT NOT NULL DEFAULT 'voice',
            topic   TEXT NOT NULL DEFAULT '',
            at      TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
            text, topic, content='memory', content_rowid='id', tokenize='porter unicode61'
        );
        CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory BEGIN
            INSERT INTO memory_fts(rowid, text, topic) VALUES (new.id, new.text, new.topic);
        END;
        CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory BEGIN
            INSERT INTO memory_fts(memory_fts, rowid, text, topic) VALUES ('delete', old.id, old.text, old.topic);
        END;
    """)
    return db


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def repair(text: str) -> str:
    """Undo UTF-8 that was once mis-read as Latin-1 ("PiÃ±a" -> "Piña")."""
    if "Ã" in text or "â€" in text:
        try:
            return text.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return text


# ------------------------------------------------------------------- writes

def add(text: str, kind: str = "fact", source: str = "voice", topic: str = "",
        at: str | None = None) -> int | None:
    """Store one memory. Returns its id, or None if it was empty or a duplicate."""
    text = repair(" ".join(str(text).split()))
    if not text or (kind == "fact" and len(text) > 400):
        return None
    with _lock, connect() as db:
        if db.execute("SELECT 1 FROM memory WHERE kind=? AND text=?", (kind, text)).fetchone():
            return None
        cur = db.execute("INSERT INTO memory(kind, text, source, topic, at) VALUES (?,?,?,?,?)",
                         (kind, text, source, topic, at or now()))
        return cur.lastrowid


def forget(ident: int) -> bool:
    with _lock, connect() as db:
        return db.execute("DELETE FROM memory WHERE id=?", (ident,)).rowcount > 0


def forget_matching(query: str) -> list[str]:
    """Delete the single best-matching fact for "forget that ..." and report it."""
    hits = search(query, limit=1, kinds=("fact",))
    if not hits:
        return []
    forget(hits[0]["id"])
    return [hits[0]["text"]]


def log_exchange(said: str, reply: str) -> None:
    """One line in the day log. Kept short; this is a diary, not a transcript."""
    said, reply = " ".join(said.split())[:160], " ".join(reply.split())[:200]
    if said:
        add(f"He asked: {said} -- She answered: {reply}", kind="day", source="log",
            topic=time.strftime("%Y-%m-%d"))


# -------------------------------------------------------------------- reads

def _terms(query: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'_-]{1,}", query.lower())
    return [w for w in words if w not in STOP][:12]


def search(query: str, limit: int = 6, kinds: tuple = ("fact", "project"),
           near: list | None = None) -> list[dict]:
    """The best full-text matches that share most of the question's words.

    `near`, when given, collects the ids of rows the full-text search found
    but the coverage test turned away, best first -- the near misses the brain
    view rings. It never changes what is returned."""
    terms = _terms(query)
    if not terms:
        return []
    match = " OR ".join(f'"{t}"' for t in terms)
    marks = ",".join("?" * len(kinds))
    with _lock, connect() as db:
        try:
            rows = db.execute(
                f"""SELECT m.id, m.kind, m.text, m.topic, m.at, bm25(memory_fts) AS rank
                    FROM memory_fts JOIN memory m ON m.id = memory_fts.rowid
                    WHERE memory_fts MATCH ? AND m.kind IN ({marks})
                    ORDER BY rank LIMIT ?""", (match, *kinds, limit * 4)).fetchall()
        except sqlite3.OperationalError:
            return []
    # Full-text OR-matching is generous: one common word is enough to "match".
    # Keep a hit only if it shares most of the question's meaningful words, so
    # an unrelated question recalls nothing rather than something random.
    distinct = set(terms)
    need = 1 if len(distinct) == 1 else max(2, round(len(distinct) * 0.6))
    out = []
    for r in rows:
        hay = (r["text"] + " " + r["topic"]).lower()
        if sum(1 for t in distinct if t[:5] in hay) >= need:
            out.append(dict(r))
        elif near is not None:
            near.append(r["id"])
    return out[:limit]


def top_terms(query: str, n: int = TERMS_MAX) -> list[str]:
    """The words a search for `query` looks for, each once, at most n."""
    return list(dict.fromkeys(_terms(query)))[:n]


def _near_ids(candidates: list, exclude: list, n: int = NEAR_MAX) -> list[int]:
    """`candidates` in order, each once, minus `exclude`, at most n."""
    out, skip = [], set(exclude)
    for i in candidates:
        if i is not None and i not in skip:
            out.append(i)
            skip.add(i)
            if len(out) >= n:
                break
    return out


def brain_rows(since: int = 0) -> dict:
    """Every row after `since`, trimmed, for the brain view (plus totals so it
    can tell when something was forgotten and reload)."""
    with _lock, connect() as db:
        rows = db.execute("SELECT id, kind, topic, source, at, substr(text, 1, 700) AS text "
                          "FROM memory WHERE id > ? ORDER BY id", (int(since),)).fetchall()
        total, top = db.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM memory").fetchone()
    return {"rows": [dict(r) for r in rows], "count": total, "max": top}


def match(query: str, limit: int = 8) -> dict:
    """For the brain view's recall(query): the rows her recall tool would get
    (ids), the rows the coverage test turned away (near, best first, at most
    12) and the words searched for (terms, at most 8)."""
    near: list[int] = []
    ids = [h["id"] for h in search(query, limit=limit, kinds=("fact", "project", "loop"), near=near)]
    return {"ids": ids, "near": _near_ids(near, ids), "terms": top_terms(query)}


def match_ids(query: str, limit: int = 8) -> list[int]:
    """The rows a recall for `query` would bring back -- what her recall tool sees."""
    return match(query, limit)["ids"]


def core(limit: int = 25) -> list[str]:
    """The newest plain facts -- always in her standing brief, whatever is asked."""
    with _lock, connect() as db:
        rows = db.execute("SELECT text FROM memory WHERE kind='fact' ORDER BY id DESC LIMIT ?",
                          (limit,)).fetchall()
    return [r["text"] for r in reversed(rows)]


def _day_rows(date: str) -> list[tuple[str, str]]:
    """day(), each line paired with the diary day it belongs to."""
    with _lock, connect() as db:
        rows = db.execute("SELECT text, at FROM memory WHERE kind='day' AND topic=? ORDER BY id",
                          (date,)).fetchall()
    return [(f"{r['at'][11:16]}  {r['text']}", date) for r in rows]


def day(date: str) -> list[str]:
    return [line for line, _ in _day_rows(date)]


def loops() -> list[dict]:
    """Things left unfinished, oldest first."""
    with _lock, connect() as db:
        rows = db.execute("SELECT id, text, topic, at FROM memory WHERE kind='loop' ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def close_loop(query: str) -> list[str]:
    """Close the open loop that best matches. The closure goes in the day log."""
    hits = search(query, limit=1, kinds=("loop",))
    if not hits:
        low = " ".join(query.lower().split())
        hits = [l for l in loops() if low and (low in l["text"].lower() or l["text"].lower() in low)][:1]
    if not hits:
        return []
    forget(hits[0]["id"])
    add(f"Finished: {hits[0]['text']}", kind="day", source="log", topic=time.strftime("%Y-%m-%d"))
    return [hits[0]["text"]]


def _trail_rows(query: str, days: int = 21, limit: int = 8) -> list[tuple[str, str]]:
    """trail(), each line paired with the diary day it belongs to."""
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    hits = [h for h in search(query, limit=40, kinds=("day",)) if h["topic"] >= since]
    hits.sort(key=lambda h: h["id"])
    return [(f"{h['at'][:16]}  {h['text']}", h["topic"] or h["at"][:10]) for h in hits[-limit:]]


def trail(query: str, days: int = 21, limit: int = 8) -> list[str]:
    """The most recent diary lines about a subject, for "where did we leave off"."""
    return [line for line, _ in _trail_rows(query, days, limit)]


def count(kind: str | None = None) -> int:
    with _lock, connect() as db:
        if kind:
            return db.execute("SELECT COUNT(*) FROM memory WHERE kind=?", (kind,)).fetchone()[0]
        return db.execute("SELECT COUNT(*) FROM memory").fetchone()[0]


def recall_block(prompt: str, limit: int = 5, budget: int = 1400, info: dict | None = None) -> str:
    """What to whisper to her alongside a question: the memories that bear on it.

    It also notes, for the brain view, what she was really handed: LAST_RECALL
    (the rows in the block -- a hit cut by the budget never reached her),
    LAST_NEAR (rows that nearly made it: cut by the budget, then rows search()
    found but turned away; at most 12), LAST_DAYS (the diary days of the log
    lines) and LAST_TERMS (the words searched for, at most 8). `info`, when
    given, receives the same as ids / near / days / terms: the caller's own
    copy, which a recall running at the same time cannot overwrite."""
    turned_away: list[int] = []
    hits = search(prompt, limit=limit, kinds=("fact", "project", "loop"), near=turned_away)
    low = prompt.lower()
    extra: list[str] = []
    dates: list[str] = []                   # the diary day of each line in extra
    if re.search(r"\byesterday\b", low):
        rows = _day_rows(time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400)))[-12:]
        extra, dates = [t for t, _ in rows], [d for _, d in rows]
    elif re.search(r"\b(today|earlier|this morning|just now)\b", low):
        rows = _day_rows(time.strftime("%Y-%m-%d"))[-12:]
        extra, dates = [t for t, _ in rows], [d for _, d in rows]
    if re.search(r"\b(leave off|left off|leaving off|pick(?:ing)? (?:it |this |that )?(?:back )?up|where were we|"
                 r"last time|what was i (?:doing|working on)|still (?:open|pending|unfinished|to do)|"
                 r"open loops?|unfinished|catch me up)\b", low):
        # "Where did we leave off on the deck?" -- the diary lines about the
        # subject, and whatever is still marked open.
        subject = re.sub(r"\b(leave|left|leaving|off|pick(?:ing)?|back|up|where|were|we|last|time|what|was|"
                         r"doing|working|still|open|pending|unfinished|loops?|catch|did|with|on)\b", " ", low)
        more = [(t, d) for t, d in _trail_rows(subject) if t not in extra]
        extra += [t for t, _ in more]
        dates += [d for _, d in more]
        open_now = [l for l in loops() if l["id"] not in {h["id"] for h in hits}]
        hits += [dict(l, kind="loop") for l in open_now[-6:]]
    lines, used = [], 0
    given: list[int] = []                   # the rows that really made it into the block
    cut: list[int] = []                     # hits the budget left out
    for i, h in enumerate(hits):
        t = h["text"] if len(h["text"]) <= 420 else h["text"][:420] + "..."
        tag = f"[{h['topic']}] " if h["kind"] == "project" and h["topic"] else ""
        if h["kind"] == "loop":
            tag = f"(still open, from {h.get('at', '')[:10]}) "
        if used + len(t) > budget:
            cut = [x.get("id") for x in hits[i:]]
            break
        lines.append(f"- {tag}{t}")
        used += len(t)
        if h.get("id") is not None:
            given.append(h["id"])
    for e in extra:
        lines.append(f"- (log) {e}")
    # The brain view lights up exactly these rows while she answers.
    global LAST_RECALL, LAST_NEAR, LAST_DAYS, LAST_TERMS
    seen = {"ids": given, "near": _near_ids(cut + turned_away, given),
            "days": sorted(set(dates)), "terms": top_terms(prompt)}
    LAST_RECALL, LAST_NEAR, LAST_DAYS, LAST_TERMS = seen["ids"], seen["near"], seen["days"], seen["terms"]
    if info is not None:
        info.update({k: list(v) for k, v in seen.items()})
    if not hits and not extra:
        return ""
    return ("[Recalled from your memory -- use only if relevant, never read it out as a list]\n"
            + "\n".join(lines))


# ------------------------------------------------------------------ imports

def import_legacy() -> int:
    """memory.jsonl -> facts. Safe to repeat: duplicates are skipped."""
    n = 0
    if not LEGACY.is_file():
        return 0
    for line in LEGACY.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if add(str(e.get("text") or ""), kind="fact", source="memory.jsonl", at=e.get("at")):
            n += 1
    return n


def import_project_notes() -> int:
    """Index the Claude Code memory notes, read-only, as project knowledge.

    Each note is split into paragraphs so a search returns the relevant part
    rather than a whole page. Re-running replaces the previous index.
    """
    if not PROJECT_NOTES.is_dir():
        return 0
    with _lock, connect() as db:
        db.execute("DELETE FROM memory WHERE kind='project'")
    n = 0
    for f in sorted(PROJECT_NOTES.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        raw = f.read_text(encoding="utf-8", errors="replace")
        body = re.sub(r"^---\n.*?\n---\n", "", raw, count=1, flags=re.S)
        m = re.search(r"^description:\s*\"?(.+?)\"?\s*$", raw, flags=re.M)
        topic = f.stem
        if m:
            if add(f"{topic}: {m.group(1)}", kind="project", source=f.name, topic=topic):
                n += 1
        for para in re.split(r"\n\s*\n", body):
            para = " ".join(para.split())
            if len(para) < 40:
                continue
            for i in range(0, len(para), 600):
                if add(para[i:i + 600], kind="project", source=f.name, topic=topic):
                    n += 1
    return n


# ------------------------------------------------- Claude's notes, kept in sync
# The brain view shows the Claude Code memory notes as one galaxy with hers
# (2026-11-20: the separate SecondBrain page was merged in). sync_project_notes
# re-indexes ONLY the notes whose paragraphs changed, so unchanged notes keep
# their row ids (their stars never move). notes_meta / note() feed the reader.
_LINK = re.compile(r"\[\[([^\]|#]+)")
_notes_cache: dict = {"sig": None, "meta": [], "bodies": {}}


def _note_parts(raw: str) -> tuple[dict, str]:
    """Frontmatter fields (name, description, type, modified) and the body."""
    fm: dict = {}
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?", raw, flags=re.S)
    body = raw[m.end():] if m else raw
    for line in (m.group(1).splitlines() if m else []):
        kv = re.match(r"^\s*(name|description|type|modified):\s*(.*)$", line)
        if kv and kv.group(1) not in fm:
            fm[kv.group(1)] = kv.group(2).strip().strip('"')
    return fm, body.strip()


def _note_rows(f: Path) -> tuple[str, list[str]]:
    """The rows one note becomes (same split as import_project_notes)."""
    raw = f.read_text(encoding="utf-8", errors="replace")
    fm, body = _note_parts(raw)
    topic, out = f.stem, []
    if fm.get("description"):
        out.append(f"{topic}: {fm['description']}")
    for para in re.split(r"\n\s*\n", body):
        para = " ".join(para.split())
        if len(para) < 40:
            continue
        out.extend(para[i:i + 600] for i in range(0, len(para), 600))
    return topic, [repair(" ".join(t.split())) for t in out]


def _note_files() -> list[Path]:
    if not PROJECT_NOTES.is_dir():
        return []
    return sorted(f for f in PROJECT_NOTES.glob("*.md") if f.name != "MEMORY.md")


def sync_project_notes() -> dict:
    """Bring the project rows in line with the notes folder, note by note.
    Returns {"changed": [...topics], "removed": [...topics]}; cheap when nothing changed."""
    files = _note_files()
    if not files:
        return {"changed": [], "removed": []}
    with _lock, connect() as db:
        have: dict[str, list[str]] = {}
        for r in db.execute("SELECT source, text FROM memory WHERE kind='project' ORDER BY id"):
            have.setdefault(r["source"], []).append(r["text"])
    owner = {t: src for src, ts in have.items() for t in ts}
    changed, removed = [], []
    for f in files:
        topic, want = _note_rows(f)
        # add() skips a text that is already stored, so a repeated paragraph is expected once
        want = [t for t in dict.fromkeys(want) if t and owner.get(t, f.name) == f.name]
        if have.pop(f.name, None) == want:
            continue
        with _lock, connect() as db:
            db.execute("DELETE FROM memory WHERE kind='project' AND source=?", (f.name,))
        for t in want:
            add(t, kind="project", source=f.name, topic=topic)
        changed.append(topic)
    for src in have:                     # notes deleted from the folder
        with _lock, connect() as db:
            db.execute("DELETE FROM memory WHERE kind='project' AND source=?", (src,))
        removed.append(src[:-3] if src.endswith(".md") else src)
    return {"changed": changed, "removed": removed}


def notes_signature() -> str:
    """Changes whenever a note is added, removed or edited (names + mtimes)."""
    parts = []
    for f in _note_files() + [PROJECT_NOTES / "MEMORY.md"]:
        try:
            parts.append(f"{f.name}:{f.stat().st_mtime_ns}")
        except OSError:
            pass
    return str(abs(hash("|".join(parts))))


def _load_notes() -> None:
    sig = notes_signature()
    if sig == _notes_cache["sig"]:
        return
    files = _note_files()
    slugs = {f.stem for f in files}
    titles: dict[str, str] = {}
    try:
        idx = (PROJECT_NOTES / "MEMORY.md").read_text(encoding="utf-8", errors="replace")
        for t, s in re.findall(r"- \[([^\]]+)\]\(([^)]+)\.md\)", idx):
            titles[s] = t
    except OSError:
        pass
    meta, bodies = [], {}
    for f in files:
        try:
            fm, body = _note_parts(f.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        s = f.stem
        links = {l.strip() for l in _LINK.findall(body)} & slugs
        for o in slugs:                  # a note named in the text links too (as SecondBrain did)
            if o != s and re.search(r"(?<![\w-])" + re.escape(o) + r"(?![\w-])", body):
                links.add(o)
        links.discard(s)
        bodies[s] = body
        meta.append({"topic": s, "title": titles.get(s) or s.replace("-", " ").title(),
                     "desc": fm.get("description", ""), "type": fm.get("type", ""),
                     "modified": (fm.get("modified") or "")[:10], "links": sorted(links)})
    back: dict[str, list[str]] = {}
    for m in meta:
        for l in m["links"]:
            back.setdefault(l, []).append(m["topic"])
    for m in meta:
        m["backlinks"] = sorted(back.get(m["topic"], []))
    _notes_cache.update(sig=sig, meta=meta, bodies=bodies)


def notes_meta() -> list[dict]:
    """Every note: topic, title, desc, type, modified, links, backlinks."""
    _load_notes()
    return [dict(m) for m in _notes_cache["meta"]]


def note(topic: str) -> dict | None:
    """One note in full (body without frontmatter) plus its meta, or None."""
    _load_notes()
    for m in _notes_cache["meta"]:
        if m["topic"] == topic:
            return {**m, "body": _notes_cache["bodies"].get(topic, "")}
    return None


# --------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="Nova's memory")
    ap.add_argument("cmd", choices=["import", "add", "search", "forget", "list", "stats", "day", "recall"])
    ap.add_argument("arg", nargs="*")
    a = ap.parse_args()
    arg = " ".join(a.arg)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if a.cmd == "import":
        print(f"facts imported: {import_legacy()}   project paragraphs indexed: {import_project_notes()}")
    elif a.cmd == "add":
        print("stored as", add(arg, source="manual"))
    elif a.cmd == "search":
        for h in search(arg, limit=8):
            print(f"  #{h['id']:<5} {h['kind']:<8} {h['topic'][:22]:<22} {h['text'][:110]}")
    elif a.cmd == "recall":
        print(recall_block(arg) or "(nothing relevant)")
    elif a.cmd == "forget":
        print("forgotten" if forget(int(arg)) else "no such id")
    elif a.cmd == "list":
        for t in core(200):
            print("  -", t)
    elif a.cmd == "day":
        print("\n".join(day(arg or time.strftime("%Y-%m-%d"))) or "(nothing logged)")
    elif a.cmd == "stats":
        print({k: count(k) for k in ("fact", "project", "day")})
    return 0


if __name__ == "__main__":
    sys.exit(main())
