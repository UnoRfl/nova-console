#!/usr/bin/env python3
"""
nova_web.py -- looking things up without opening a browser.

Her Chrome (nova_hands) is for sites that need Sir's login, a click, or his
eyes. Most questions need none of that: they need three or four pages read
quickly and only the parts that bear on the question. That is this file.

  search     results from a plain search endpoint (DuckDuckGo, then Bing)
  fetch      one page as clean text: menus, scripts, link farms and footers
             removed, the article kept. PDFs too, when pypdf is installed.
  cache      a page read in the last fifteen minutes is not fetched again
  research   search, read the top pages at the same time, and keep only the
             passages that match the question, each with its source
  extract    named fields out of pages as rows, using the free local model
  watches    "tell me when this page changes": a text comparison on a timer,
             no model until something is different

Standard library only. Everything it returns is outside content and must be
treated as such by whoever calls it (nova_tools marks it and taints the turn).

    python nova_web.py search "query" | read URL | research "question"
"""
from __future__ import annotations

import base64
import difflib
import gzip
import hashlib
import html
import io
import json
import math
import re
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path.home() / ".nova"
CACHE_DB = ROOT / "web-cache.db"
WATCH_FILE = ROOT / "web-watches.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")
PAGE_TTL = 900          # seconds a fetched page stays good
SEARCH_TTL = 600
MAX_BYTES = 3_000_000

sys.path.insert(0, str(ROOT))

# --------------------------------------------------------------------- cache

_db_lock = threading.Lock()


def _db() -> sqlite3.Connection:
    db = sqlite3.connect(str(CACHE_DB), timeout=5)
    db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, at REAL, body TEXT)")
    return db


def cache_get(key: str, ttl: float):
    try:
        with _db_lock, _db() as db:
            row = db.execute("SELECT at, body FROM cache WHERE key=?", (key,)).fetchone()
        if row and time.time() - row[0] < ttl:
            return json.loads(row[1])
    except (sqlite3.Error, ValueError):
        pass
    return None


def cache_put(key: str, value) -> None:
    try:
        with _db_lock, _db() as db:
            db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (key, time.time(), json.dumps(value)))
            db.execute("DELETE FROM cache WHERE at < ?", (time.time() - 86400,))
    except sqlite3.Error:
        pass


# ---------------------------------------------------------------------- http

def _open(url: str, data: bytes | None = None, timeout: float = 12) -> tuple[bytes, str, str]:
    """(body, content-type, final url). Raises OSError / ValueError on failure."""
    if not re.match(r"^https?://", url, re.I):
        raise ValueError("only http and https addresses are read")
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip, deflate"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(MAX_BYTES + 1)
        enc = (r.headers.get("Content-Encoding") or "").lower()
        ctype = r.headers.get("Content-Type") or ""
        final = r.geturl()
    if enc == "gzip":
        raw = gzip.decompress(raw)
    elif enc == "deflate":
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw[:MAX_BYTES], ctype, final


def _decode(raw: bytes, ctype: str) -> str:
    m = re.search(r"charset=([\w-]+)", ctype, re.I) or re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', raw[:3000], re.I)
    name = (m.group(1).decode() if m and isinstance(m.group(1), bytes) else m.group(1) if m else "utf-8")
    try:
        return raw.decode(name, "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


def host(url: str) -> str:
    try:
        return re.sub(r"^www\.", "", urllib.parse.urlparse(url).hostname or "")
    except ValueError:
        return ""


# ------------------------------------------------------------------- extract

SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "aside", "form", "iframe", "template",
        "select", "button", "canvas", "video", "audio", "figure", "dialog", "menu"}
BLOCK = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "pre", "blockquote", "dd", "dt",
         "div", "section", "article", "main", "tr", "br", "figcaption", "summary", "caption"}
VOID = {"br", "img", "hr", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr"}
JUNK = re.compile(r"cookie|consent|newsletter|subscribe|sidebar|related|share|social|comment|promo|advert|"
                  r"breadcrumb|popup|modal|banner|footer|masthead|navbar|\bnav\b|\bmenu\b", re.I)


class _Reader(HTMLParser):
    """Text blocks out of HTML, each with how much of it is link text and
    whether it sat inside <article>/<main>."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.blocks: list[dict] = []
        self.stack: list[tuple[str, bool]] = []      # (tag, skipped-here)
        self.skip = 0
        self.main = 0
        self.in_a = 0
        self.in_title = False
        self.cur: list[str] = []
        self.cur_link = 0
        self.cur_head = ""

    def _flush(self) -> None:
        text = " ".join("".join(self.cur).split())
        if text:
            self.blocks.append({"text": text, "link": self.cur_link / max(1, len(text)),
                                "head": self.cur_head, "main": self.main > 0})
        self.cur, self.cur_link, self.cur_head = [], 0, ""

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        if tag in VOID:
            if tag == "br":
                self._flush()
            return
        a = dict(attrs)
        junk = tag in SKIP or bool(JUNK.search((a.get("class") or "") + " " + (a.get("id") or "")
                                                + " " + (a.get("role") or ""))
                                   and tag in ("div", "section", "ul", "header", "span", "p", "ol", "table"))
        if a.get("aria-hidden") == "true" or "hidden" in a:
            junk = True
        self.stack.append((tag, junk))
        if junk:
            self.skip += 1
        if self.skip:
            return
        if tag in ("article", "main"):
            self.main += 1
        if tag in BLOCK:
            self._flush()
            if tag in ("h1", "h2", "h3", "h4"):
                self.cur_head = tag
        if tag == "a":
            self.in_a += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag in VOID:
            return
        # Close back to the matching open tag; sloppy HTML leaves some unclosed.
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                for t, junk in reversed(self.stack[i:]):
                    if junk:
                        self.skip = max(0, self.skip - 1)
                    elif not self.skip:
                        if t in ("article", "main"):
                            self.main = max(0, self.main - 1)
                        if t == "a":
                            self.in_a = max(0, self.in_a - 1)
                del self.stack[i:]
                break
        if not self.skip and tag in BLOCK:
            self._flush()

    def handle_data(self, data):
        if self.in_title and not self.title:
            self.title = " ".join(data.split())[:200]
            return
        if self.skip or not data.strip():
            if data and not self.skip and self.cur:
                self.cur.append(" ")
            return
        self.cur.append(data)
        if self.in_a:
            self.cur_link += len(data.strip())


def clean_html(markup: str) -> tuple[str, list[str]]:
    """(title, paragraphs). Headings come back as '# Heading'."""
    p = _Reader()
    try:
        p.feed(markup)
        p.close()
    except Exception:                                 # noqa: BLE001 -- broken HTML: keep what we have
        pass
    p._flush()
    blocks = [b for b in p.blocks
              if not (b["link"] > 0.6 and len(b["text"]) < 200)       # a link list, not prose
              and (len(b["text"]) >= 25 or b["head"])]
    inside = [b for b in blocks if b["main"]]
    if sum(len(b["text"]) for b in inside) >= max(600, 0.35 * sum(len(b["text"]) for b in blocks)):
        blocks = inside
    out, seen = [], set()
    for b in blocks:
        key = b["text"][:160]
        if key in seen:
            continue
        seen.add(key)
        out.append(("# " if b["head"] else "") + b["text"])
    return html.unescape(p.title), out


def pdf_text(raw: bytes, max_pages: int = 30) -> list[str] | None:
    """Paragraphs of a PDF, or None when pypdf is not installed."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        reader = PdfReader(io.BytesIO(raw))
        paras = []
        for page in reader.pages[:max_pages]:
            text = page.extract_text() or ""
            paras += [" ".join(x.split()) for x in re.split(r"\n\s*\n|(?<=[.!?])\n", text) if len(x.strip()) > 25]
        return paras
    except Exception:                                 # noqa: BLE001
        return []


WALL = re.compile(r"not for bots|complete the challenge|verify (?:that )?you(?: are|'re) (?:a )?human|are you a robot|"
                  r"just a moment|enable javascript|access denied|unusual traffic|checking your browser|"
                  r"captcha|request blocked|please turn javascript on", re.I)


def fetch(url: str, fresh: bool = False, via_browser=None, timeout: float = 8) -> dict:
    """{url, title, paras, kind, cached} or {url, error}. via_browser(url) -> text
    is tried when the page turns out to need JavaScript to show anything."""
    key = "page:" + url
    if not fresh:
        hit = cache_get(key, PAGE_TTL)
        if hit:
            hit["cached"] = True
            return hit
    try:
        raw, ctype, final = _open(url, timeout=timeout)
    except Exception as exc:                          # noqa: BLE001
        raw, ctype, final, err = b"", "", url, f"{type(exc).__name__}: {str(exc)[:120]}"
    else:
        err = ""
    out: dict = {"url": final, "title": "", "paras": [], "kind": "html", "cached": False}
    if raw[:5] == b"%PDF-" or "pdf" in ctype.lower():
        paras = pdf_text(raw)
        if paras is None:
            return {"url": final, "error": "this is a PDF and the PDF reader (pypdf) is not installed"}
        out.update(kind="pdf", paras=paras, title=final.rsplit("/", 1)[-1])
    elif raw:
        title, paras = clean_html(_decode(raw, ctype))
        out.update(title=title, paras=paras)
    if out["paras"] and WALL.search(" ".join(out["paras"])[:600]) and sum(len(p) for p in out["paras"]) < 1500:
        out["paras"] = []                             # a "prove you are human" page is not the page
        err = "the site refused an automated read"
    if sum(len(p) for p in out["paras"]) < 400 and via_browser:
        # Nothing readable without running the page's scripts (or the site
        # refused a plain request): let her browser render it, in the background.
        try:
            text = via_browser(final or url) or ""
            paras = [" ".join(x.split()) for x in text.split("\n") if len(x.strip()) > 25]
            if sum(len(p) for p in paras) > sum(len(p) for p in out["paras"]):
                out.update(paras=paras, kind="rendered")
        except Exception:                             # noqa: BLE001
            pass
    if not out["paras"]:
        return {"url": final, "error": err or "nothing readable on that page"}
    cache_put(key, out)
    return out


# -------------------------------------------------------------------- search

def _strip(s: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", s)).split())


def parse_ddg(markup: str) -> list[dict]:
    out = []
    for m in re.finditer(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="[^"]*result__a|\Z)',
                         markup, re.S):
        href, title, rest = html.unescape(m.group(1)), _strip(m.group(2)), m.group(3)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        url = q["uddg"][0] if q.get("uddg") else ("https:" + href if href.startswith("//") else href)
        if "duckduckgo.com/y.js" in url or not url.startswith("http"):
            continue                                  # an advert
        s = re.search(r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', rest, re.S)
        out.append({"title": title, "url": url, "snippet": _strip(s.group(1)) if s else ""})
    return out


def _bing_url(href: str) -> str:
    if "bing.com/ck/a" not in href:
        return href
    u = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("u", [""])[0]
    if u.startswith("a1"):
        try:
            pad = u[2:] + "=" * (-len(u[2:]) % 4)
            return base64.urlsafe_b64decode(pad).decode("utf-8", "replace")
        except Exception:                             # noqa: BLE001
            return href
    return href


def parse_bing(markup: str) -> list[dict]:
    out = []
    for m in re.finditer(r'<li class="b_algo"[^>]*>(.*?)</li>', markup, re.S):
        block = m.group(1)
        a = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not a:
            continue
        s = re.search(r'<p[^>]*>(.*?)</p>', block, re.S)
        url = _bing_url(html.unescape(a.group(1)))
        if url.startswith("http"):
            out.append({"title": _strip(a.group(2)), "url": url, "snippet": _strip(s.group(1)) if s else ""})
    return out


def search(query: str, limit: int = 8, fresh: bool = False, opener=_open, via_browser=None) -> list[dict]:
    """[{title, url, snippet}], best first. [] when nothing answered.

    Measured 2026-11-14: every plain search endpoint now refuses a script
    (DuckDuckGo serves a challenge page, Brave 429s, and Bing answers with
    plausible-looking results that have nothing to do with the query, which
    is worse than refusing -- so Bing is not used). DuckDuckGo is still tried
    because it costs a fifth of a second; the search that actually works is
    via_browser(query) -> rows, her own Chrome reading Google in a background
    tab (nova_hands `serp`)."""
    query = " ".join(query.split())
    if not query:
        return []
    key = "search:" + query.lower()
    if not fresh:
        hit = cache_get(key, SEARCH_TTL)
        if hit:
            return hit[:limit]
    rows: list[dict] = []
    try:
        raw, ctype, _ = opener("https://html.duckduckgo.com/html/",
                               urllib.parse.urlencode({"q": query}).encode(), 6)
        rows = parse_ddg(_decode(raw, ctype))
    except Exception:                                 # noqa: BLE001
        rows = []
    if not rows and via_browser:
        try:
            rows = [r for r in (via_browser(query) or []) if r.get("url")]
        except Exception:                             # noqa: BLE001
            rows = []
    seen, out = set(), []
    for r in rows:
        k = re.sub(r"[#?].*$", "", r["url"]).rstrip("/").lower()
        if k not in seen:
            seen.add(k)
            out.append(r)
    if out:
        cache_put(key, out)
    return out[:limit]


# ------------------------------------------------------------------- ranking

STOP = set("a an and are as at be but by for from has have how i if in into is it its of on or that the "
           "their there these this to was were what when where which who why will with you your my me do "
           "does did can could should would about tell find look up please nova".split())


def terms(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9][a-z0-9'+#.-]*", text.lower()) if w not in STOP and len(w) > 1]


def best_passages(paras: list[str], query: str, budget: int = 1800) -> list[str]:
    """The paragraphs that bear on the query, in page order, within budget chars.
    Plain term matching weighted by rarity on this page: no model."""
    want = list(dict.fromkeys(terms(query)))
    if not paras:
        return []
    if not want:
        out, used = [], 0
        for p in paras:
            if used + len(p) > budget:
                break
            out.append(p)
            used += len(p)
        return out or [paras[0][:budget]]
    low = [p.lower() for p in paras]
    df = {t: sum(1 for p in low if t in p) for t in want}
    scores = []
    for i, p in enumerate(low):
        s = 0.0
        for t in want:
            c = p.count(t)
            if c:
                s += (1 + math.log(c)) * math.log(1 + len(paras) / df[t])
        if s and paras[i].startswith("# "):
            s *= 1.3
        scores.append(s / (1 + len(p) / 1200))        # do not let one enormous block win on size
    order = sorted(range(len(paras)), key=lambda i: scores[i], reverse=True)
    keep: set[int] = set()
    used = 0
    for i in order:
        if scores[i] <= 0:
            break
        for j in (i, i + 1):                          # the sentence after a hit usually finishes the thought
            if 0 <= j < len(paras) and j not in keep and used + min(len(paras[j]), 700) <= budget:
                keep.add(j)
                used += min(len(paras[j]), 700)
    if not keep:
        keep = {0}
    # A heading is only worth its line when the text under it was kept too.
    keep = {i for i in keep if not paras[i].startswith("# ")
            or (i + 1 in keep and not paras[i + 1].startswith("# "))}
    if not keep:
        keep = {next((i for i in order if not paras[i].startswith("# ")), 0)}
    return [paras[i] if len(paras[i]) <= 700 else paras[i][:700] + " ..." for i in sorted(keep)]


# ------------------------------------------------------------------ research

SKIP_HOSTS = re.compile(r"(?:^|\.)(?:youtube\.com|youtu\.be|tiktok\.com|instagram\.com|facebook\.com|x\.com|"
                        r"twitter\.com|pinterest\.[a-z.]+|linkedin\.com)$")


def research(question: str, pages: int = 4, budget: int = 1500, via_browser=None, deadline: float = 9,
             fresh: bool = False, searcher=None, fetcher=None, search_browser=None) -> dict:
    """Search, read the top pages at once, keep what answers the question.

    {question, sources: [{n, title, url, host, passages, kind}], skipped: [...], seconds}
    """
    began = time.time()
    fetcher = fetcher or fetch
    if searcher:
        results = searcher(question, limit=max(8, pages * 2), fresh=fresh)
    else:
        results = search(question, limit=max(8, pages * 2), fresh=fresh, via_browser=search_browser)
    picks, hosts = [], set()
    for r in results:
        h = host(r["url"])
        if not h or h in hosts or SKIP_HOSTS.search(h):
            continue
        hosts.add(h)
        picks.append(r)
        if len(picks) >= pages + 2:                   # two spares for pages that fail
            break
    got: dict[str, dict] = {}
    if picks:
        pool = ThreadPoolExecutor(max_workers=min(6, len(picks)))
        # No browser fallback here: one page that needs rendering must not
        # cost the whole answer ten seconds. There are spares for that.
        futs = {pool.submit(fetcher, r["url"], fresh, None): r for r in picks}
        try:
            for f in as_completed(futs, timeout=deadline):
                try:
                    got[futs[f]["url"]] = f.result()
                except Exception as exc:              # noqa: BLE001
                    got[futs[f]["url"]] = {"error": type(exc).__name__}
        except Exception:                             # noqa: BLE001 -- the deadline: use what arrived
            pass
        pool.shutdown(wait=False, cancel_futures=True)
    sources, skipped = [], []
    for r in picks:
        page = got.get(r["url"])
        if not page or page.get("error"):
            skipped.append({"url": r["url"], "why": (page or {}).get("error", "too slow")})
            continue
        if len(sources) >= pages:
            continue
        passages = best_passages(page["paras"], question, budget)
        sources.append({"n": len(sources) + 1, "title": page.get("title") or r["title"], "url": page["url"],
                        "host": host(page["url"]), "passages": passages, "kind": page.get("kind", "html"),
                        "cached": bool(page.get("cached"))})
    if not sources:
        # Nothing could be read: the search snippets are still worth having.
        for r in results[:pages]:
            if r.get("snippet"):
                sources.append({"n": len(sources) + 1, "title": r["title"], "url": r["url"], "host": host(r["url"]),
                                "passages": [r["snippet"]], "kind": "snippet", "cached": False})
    return {"question": question, "sources": sources, "skipped": skipped,
            "seconds": round(time.time() - began, 1), "results": len(results)}


def digest(found: dict) -> str:
    if not found["sources"]:
        return "Nothing came back for that search."
    out = []
    for s in found["sources"]:
        out.append(f"[{s['n']}] {s['title']} -- {s['host']}\n{s['url']}\n" + "\n".join(s["passages"]))
    return "\n\n".join(out)


# ------------------------------------------------------------------- extract

def extract_fields(urls: list[str], fields: list[str], model: str = "", via_browser=None,
                   ollama: str = "http://127.0.0.1:11434") -> list[dict]:
    """One row per page: {url, <field>: value...}, read by the free local model.
    A page the model could not handle comes back with its passages instead."""
    fields = [f.strip() for f in fields if f and f.strip()][:12]
    if not model:
        try:
            import nova_harness
            model = nova_harness.local_model() or ""
        except Exception:                             # noqa: BLE001
            model = ""

    def one(url: str) -> dict:
        page = fetch(url, via_browser=via_browser)
        if page.get("error"):
            return {"url": url, "error": page["error"]}
        text = "\n".join(best_passages(page["paras"], " ".join(fields) + " " + (page.get("title") or ""), 3200))
        row = {"url": page["url"], "title": page.get("title", "")}
        if not model:
            row["passages"] = text[:1500]
            return row
        prompt = ("From the page text below, give these fields as one JSON object with exactly these keys: "
                  + json.dumps(fields) + ". Use the page's own words and numbers. If a field is not on the "
                  "page, use null. Reply with the JSON object only.\n\nPage: " + (page.get("title") or "")
                  + "\n\n" + text)
        body = json.dumps({"model": model, "stream": False, "format": "json", "keep_alive": "10m",
                           "messages": [{"role": "user", "content": prompt}],
                           "options": {"temperature": 0, "num_predict": 400}}).encode("utf-8")
        try:
            req = urllib.request.Request(ollama + "/api/chat", data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.loads((json.loads(r.read().decode("utf-8")).get("message") or {}).get("content") or "{}")
            for f in fields:
                row[f] = data.get(f)
        except Exception:                             # noqa: BLE001
            row["passages"] = text[:1500]
        return row

    urls = [u for u in urls if isinstance(u, str) and u.strip()][:8]
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(urls)))) as pool:
        return list(pool.map(one, urls))


# ------------------------------------------------------------------- watches

_watch_lock = threading.Lock()


def load_watches(path: Path | None = None) -> list[dict]:
    try:
        data = json.loads((path or WATCH_FILE).read_text(encoding="utf-8"))
        return [w for w in data if isinstance(w, dict) and w.get("id")]
    except (OSError, ValueError):
        return []


def save_watches(items: list[dict], path: Path | None = None) -> None:
    (path or WATCH_FILE).write_text(json.dumps(items, indent=1), encoding="utf-8")


def _focus(paras: list[str], what: str) -> list[str]:
    """The part of the page a watch is about. Clock times and counters are
    blanked so a page does not 'change' every time it is loaded."""
    lines = best_passages(paras, what, 1500) if what else paras[:60]
    out = []
    for p in lines:
        p = re.sub(r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?", "", p, flags=re.I)
        p = re.sub(r"\b\d+\s+(?:second|minute|hour|day|week|month|year)s?\s+ago\b|\b(?:just now|yesterday|today)\b", "", p, flags=re.I)
        p = re.sub(r"\b[\d,.]+[km]?\s+(?:views?|likes?|comments?|shares?|reactions?)\b", "", p, flags=re.I)
        p = " ".join(p.split())
        if len(p) > 15:
            out.append(p)
    return out


def add_watch(url: str, what: str = "", every_minutes: float = 30, path: Path | None = None) -> dict:
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    if not host(url):
        return {"error": "that is not a web address"}
    with _watch_lock:
        items = load_watches(path)
        if len(items) >= 12:
            return {"error": "twelve pages are already being watched; stop one first"}
        ident = "w" + hashlib.sha1((url + what).encode()).hexdigest()[:6]
        items = [w for w in items if w["id"] != ident]
        item = {"id": ident, "url": url, "what": " ".join(what.split())[:120],
                "every": max(10.0, float(every_minutes)) * 60, "checked": 0.0, "lines": None,
                "made": time.time(), "changed": 0.0}
        items.append(item)
        save_watches(items, path)
    return item


def stop_watch(which: str, path: Path | None = None) -> list[dict]:
    key = (which or "").lower().strip()
    with _watch_lock:
        items = load_watches(path)
        gone = [w for w in items if key and (key == "all" or w["id"] == key or key in w["url"].lower()
                                             or (w.get("what") and key in w["what"].lower()))]
        if gone:
            save_watches([w for w in items if w not in gone], path)
    return gone


def check_watches(now: float | None = None, path: Path | None = None, fetcher=None, via_browser=None) -> list[str]:
    """Look at every watch that is due. Returns lines worth saying. The first
    look at a page only learns it."""
    now = time.time() if now is None else now
    fetcher = fetcher or fetch
    with _watch_lock:
        items = load_watches(path)
    due = [w for w in items if now - float(w.get("checked") or 0) >= float(w.get("every") or 1800)]
    lines_out, updates = [], {}
    for w in due[:4]:
        page = fetcher(w["url"], True, via_browser)
        new = {"checked": now}
        if not page.get("error"):
            lines = _focus(page["paras"], w.get("what") or "")
            old = w.get("lines")
            if old is not None and lines and lines != old:
                added = [l for l in difflib.ndiff(old, lines) if l.startswith("+ ")]
                fresh_text = added[0][2:] if added else lines[0]
                # A line that only moved is not news: it has to be new text.
                if any(l[2:] not in old for l in added) or len(lines) != len(old):
                    name = (w.get("what") or page.get("title") or host(w["url"]))[:70]
                    lines_out.append(f"Sir, the page you asked me to watch, {name}, has changed. "
                                     f"New on it: {fresh_text[:220]}")
                    new["changed"] = now
            if lines:
                new["lines"] = lines
        updates[w["id"]] = new
    if updates:
        with _watch_lock:
            items = load_watches(path)
            for w in items:
                if w["id"] in updates:
                    w.update(updates[w["id"]])
            save_watches(items, path)
    return lines_out


# ---------------------------------------------------------------------- main

def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    cmd, arg = (sys.argv[1:3] + ["", ""])[:2]
    if cmd == "search":
        for r in search(arg):
            print(f"- {r['title']}\n  {r['url']}\n  {r['snippet'][:160]}")
    elif cmd == "read":
        page = fetch(arg)
        print(page.get("error") or (page["title"] + "\n\n" + "\n".join(page["paras"])[:4000]))
    elif cmd == "research":
        found = research(arg)
        print(digest(found))
        print(f"\n({found['seconds']} s, {len(found['sources'])} sources, {len(found['skipped'])} skipped)")
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
