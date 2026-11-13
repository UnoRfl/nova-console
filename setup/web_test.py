#!/usr/bin/env python3
"""
web_test.py -- nova_web offline: page cleaning, passage ranking, result
parsing, research with a failing page and a slow one, the cache, and page
watches. No network, no browser, no model: search and fetch are faked.
    python setup/web_test.py
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")
import nova_web as W  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="nova-web-"))
W.CACHE_DB = TMP / "cache.db"
W.WATCH_FILE = TMP / "watches.json"
fails = []


def check(name, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + name + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        fails.append(name)


PAGE = """<html><head><title>Kettle review &amp; price</title><style>p{color:red}</style>
<script>var tracking = "do not read me";</script></head><body>
<nav><a href="/">Home</a> <a href="/shop">Shop</a> <a href="/about">About us and more links</a></nav>
<div class="cookie-banner">We use cookies to improve your experience on this website today.</div>
<article><h1>The Volt kettle, reviewed</h1>
<p>The Volt kettle boils a litre of water in two minutes and ten seconds, which is quick for its size.</p>
<p>It costs 1,499 pesos at most shops, and the warranty runs for two years from purchase.</p>
<ul><li><a href="/a">Related: toasters</a></li><li><a href="/b">Related: blenders</a></li></ul>
<h2>Noise</h2><p>At full boil it reaches 61 decibels, about the level of a normal conversation at home.</p>
</article>
<aside>Sign up for the newsletter and never miss a deal from our partners.</aside>
<footer>Copyright 2026 Example Media. All rights reserved worldwide, forever and ever.</footer>
</body></html>"""

print("cleaning")
title, paras = W.clean_html(PAGE)
text = "\n".join(paras)
check("title, with entities decoded", title == "Kettle review & price", title)
check("article text kept", "two minutes and ten seconds" in text and "61 decibels" in text, text)
check("script, style, nav, cookie bar, aside and footer dropped",
      not any(x in text for x in ("tracking", "color:red", "Shop", "cookies", "newsletter", "Copyright")), text)
check("link lists dropped", "toasters" not in text, text)
check("headings marked", "# The Volt kettle, reviewed" in paras and "# Noise" in paras, str(paras))
check("broken HTML does not raise", isinstance(W.clean_html("<div><p>Unclosed paragraph that goes on long enough to keep")[1], list))

print("ranking")
best = W.best_passages(paras, "how much does the kettle cost", 400)
check("the price paragraph is chosen", any("1,499 pesos" in p for p in best), str(best))
check("an unrelated heading is not carried along", "# Noise" not in best or any("decibels" in p for p in best), str(best))
check("budget respected", sum(len(p) for p in W.best_passages(paras, "kettle", 200)) <= 220)
check("no query: the start of the page", W.best_passages(paras, "", 150)[0] == paras[0])
check("stop words ignored", W.terms("what is the price of the kettle") == ["price", "kettle"])

print("search parsing")
ddg = """<div class="result"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fkettle&amp;rut=x">Volt <b>kettle</b> review</a>
<a class="result__snippet" href="x">A quick <b>kettle</b> at a fair price.</a></div>
<div class="result"><a class="result__a" href="https://duckduckgo.com/y.js?ad=1">Advert</a></div>"""
rows = W.parse_ddg(ddg)
check("result unwrapped from the redirect, advert dropped",
      rows == [{"title": "Volt kettle review", "url": "https://example.com/kettle", "snippet": "A quick kettle at a fair price."}], str(rows))
calls = []
rows = W.search("volt kettle", opener=lambda u, d, t: (b"<html>challenge</html>", "text/html", u),
                via_browser=lambda q: calls.append(q) or [{"title": "T", "url": "https://a.example/x", "snippet": "s"},
                                                            {"title": "T", "url": "https://a.example/x/", "snippet": "dup"}])
check("a blocked endpoint falls through to the browser", calls == ["volt kettle"] and len(rows) == 1, str(rows))
W.search("volt kettle", opener=lambda *a: 1 / 0, via_browser=lambda q: calls.append(q) or [])
check("a repeated search comes from the cache", len(calls) == 1)
check("nothing anywhere is an empty list, not an error", W.search("zzz nothing", opener=lambda *a: 1 / 0) == [])

print("research")
RESULTS = [{"title": "Good", "url": "https://good.example/a", "snippet": "good snippet"},
           {"title": "Video", "url": "https://www.youtube.com/watch?v=1", "snippet": "video"},
           {"title": "Same host", "url": "https://good.example/b", "snippet": "again"},
           {"title": "Broken", "url": "https://broken.example/", "snippet": "broken snippet"},
           {"title": "Slow", "url": "https://slow.example/", "snippet": "slow snippet"},
           {"title": "Second", "url": "https://second.example/", "snippet": "second snippet"}]


def fake_fetch(url, fresh=False, via_browser=None):
    if "broken" in url:
        return {"url": url, "error": "HTTPError: 403"}
    if "slow" in url:
        time.sleep(3)
    return {"url": url, "title": "Page " + W.host(url), "kind": "html", "cached": False,
            "paras": ["Nothing to do with it at all, just filler text for the page.",
                      "The kettle costs 1,499 pesos according to " + W.host(url) + ".",
                      "More filler that should not be selected by the ranking step."]}


t = time.time()
found = W.research("how much does the kettle cost", pages=2, searcher=lambda q, limit, fresh: RESULTS,
                   fetcher=fake_fetch, deadline=1.5)
took = time.time() - t
hosts = [s["host"] for s in found["sources"]]
check("two sources, one per site, video sites skipped", hosts == ["good.example", "second.example"], str(hosts))
check("each source carries the matching passage", all("1,499 pesos" in " ".join(s["passages"]) for s in found["sources"]))
check("the failed and the slow page are reported, not fatal",
      {s["url"] for s in found["skipped"]} == {"https://broken.example/", "https://slow.example/"}, str(found["skipped"]))
check("a slow page does not hold up the answer", took < 2.5, f"{took:.1f}s")
d = W.digest(found)
check("digest is numbered with sources", d.startswith("[1] Page good.example -- good.example\nhttps://good.example/a") and "[2]" in d, d)
found = W.research("kettle", pages=2, searcher=lambda q, limit, fresh: RESULTS[3:4], fetcher=fake_fetch)
check("nothing readable: the snippets are used", found["sources"][0]["kind"] == "snippet"
      and found["sources"][0]["passages"] == ["broken snippet"])
check("no results at all", W.digest(W.research("x", searcher=lambda q, limit, fresh: [], fetcher=fake_fetch))
      == "Nothing came back for that search.")

print("cache")
W.cache_put("page:https://c.example/", {"url": "https://c.example/", "title": "C", "paras": ["cached text here"], "kind": "html"})
check("a cached page is served without fetching", W.fetch("https://c.example/").get("cached") is True)
check("only http(s) is read", "error" in W.fetch("file:///C:/Windows/win.ini", fresh=True))

print("watches")
check("a watch is saved, address completed", W.add_watch("shop.example/kettle", "price", 5)["url"] == "https://shop.example/kettle")
check("never more often than ten minutes", W.load_watches()[0]["every"] == 600)
page = {"paras": ["The Volt kettle price is 1,499 pesos today, updated at 10:42 AM, 12 views.", "Other text about toasters."]}
fx = lambda url, fresh, via: {"url": url, "title": "Shop", **page}   # noqa: E731
T0 = 2_000_000
check("the first look only learns the page", W.check_watches(T0, fetcher=fx) == [])
check("not due yet: no fetch", W.check_watches(T0 + 60, fetcher=lambda *a: 1 / 0) == [])
page["paras"][0] = "The Volt kettle price is 1,499 pesos today, updated at 11:15 AM, 57 views."
check("a clock and a view counter changing is not a change", W.check_watches(T0 + 700, fetcher=fx) == [])
page["paras"][0] = "The Volt kettle price is 1,299 pesos today, updated at 11:20 AM, 60 views."
out = W.check_watches(T0 + 1400, fetcher=fx)
check("a new price is announced with the new text",
      len(out) == 1 and out[0].startswith("Sir, the page you asked me to watch, price, has changed.") and "1,299 pesos" in out[0], str(out))
check("and only once", W.check_watches(T0 + 2100, fetcher=fx) == [])
check("a page that fails to load says nothing", W.check_watches(T0 + 2800, fetcher=lambda u, f, v: {"url": u, "error": "x"}) == [])
check("stop by a word", [w["url"] for w in W.stop_watch("kettle")] == ["https://shop.example/kettle"] and W.load_watches() == [])

print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED: " + "; ".join(fails))
sys.exit(1 if fails else 0)
