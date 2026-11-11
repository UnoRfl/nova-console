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


def search(query: str, limit: int = 6, kinds: tuple = ("fact", "project")) -> list[dict]:
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
    return out[:limit]


def core(limit: int = 25) -> list[str]:
    """The newest plain facts -- always in her standing brief, whatever is asked."""
    with _lock, connect() as db:
        rows = db.execute("SELECT text FROM memory WHERE kind='fact' ORDER BY id DESC LIMIT ?",
                          (limit,)).fetchall()
    return [r["text"] for r in reversed(rows)]


def day(date: str) -> list[str]:
    with _lock, connect() as db:
        rows = db.execute("SELECT text, at FROM memory WHERE kind='day' AND topic=? ORDER BY id",
                          (date,)).fetchall()
    return [f"{r['at'][11:16]}  {r['text']}" for r in rows]


def count(kind: str | None = None) -> int:
    with _lock, connect() as db:
        if kind:
            return db.execute("SELECT COUNT(*) FROM memory WHERE kind=?", (kind,)).fetchone()[0]
        return db.execute("SELECT COUNT(*) FROM memory").fetchone()[0]


def recall_block(prompt: str, limit: int = 5, budget: int = 1400) -> str:
    """What to whisper to her alongside a question: the memories that bear on it."""
    hits = search(prompt, limit=limit)
    low = prompt.lower()
    extra: list[str] = []
    if re.search(r"\byesterday\b", low):
        extra = day(time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400)))[-12:]
    elif re.search(r"\b(today|earlier|this morning|just now)\b", low):
        extra = day(time.strftime("%Y-%m-%d"))[-12:]
    if not hits and not extra:
        return ""
    lines, used = [], 0
    for h in hits:
        t = h["text"] if len(h["text"]) <= 420 else h["text"][:420] + "..."
        tag = f"[{h['topic']}] " if h["kind"] == "project" and h["topic"] else ""
        if used + len(t) > budget:
            break
        lines.append(f"- {tag}{t}")
        used += len(t)
    for e in extra:
        lines.append(f"- (log) {e}")
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
