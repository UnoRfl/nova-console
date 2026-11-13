"""Nova's own browser: a real Chrome, with its own profile, that only she drives.

Runs as a small local service (127.0.0.1:8783, same token as the bridge) so the
browser outlives each Claude process. The bridge starts it on demand. One
worker thread owns Playwright; every request is queued to it.

    python nova_hands.py            run the service
    python nova_hands.py --show     run it with the window visible (to sign in)

Rules that are enforced here, in code, not left to the model:
  * Messenger and Facebook are READ-ONLY. click / type / press refuse on those
    hosts. Reading goes through messenger_chats / messenger_read only.
  * Everything read from a page is untrusted. Each read tells the bridge
    (/api/taint), and the approval gate then refuses risky PC actions (shell,
    file edits, desktop clicks) until Sir speaks again in his own words.
  * She never solves captchas; she reports them.
  * Her profile is ~/.nova/hands-profile. Sir's own Chrome profile, cookies
    and saved passwords are never opened.
"""
from __future__ import annotations

import json
import queue
import re
import secrets
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import Future
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path.home() / ".nova"
PROFILE = ROOT / "hands-profile"
SHOTS = ROOT / "captures"
PORT = 8783
CDP_PORT = 9333
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
BRIDGE = "http://127.0.0.1:8765"
# Read-only sites: she may look, never click, type or press keys there.
READ_ONLY_HOSTS = re.compile(r"(?:^|\.)(?:messenger\.com|facebook\.com|fb\.com|fbsbx\.com|"
                             r"mail\.google\.com|accounts\.google\.com|myaccount\.google\.com)$", re.I)


def _port_up(port: int) -> bool:
    import socket
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0
CAPTCHA = re.compile(r"(?:unusual traffic|are you a robot|not a robot|captcha|verify you are human|"
                     r"confirm this search was made by a human)", re.I)
MAX_TEXT = 6000

# JS that numbers the things one can click or type into, so the model can say
# "click 12" instead of guessing selectors.
MARK_JS = r"""
() => {
  const sel = 'a[href], button, input, textarea, select, [role=button], [role=link], [role=tab], ' +
              '[role=menuitem], [role=checkbox], [role=option], [contenteditable=true], summary';
  const out = [];
  let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || r.bottom < 0 || r.top > innerHeight * 3) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') continue;
    let label = (el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder ||
                 el.title || el.name || el.getAttribute('href') || '').trim().replace(/\s+/g, ' ');
    if (!label) continue;
    n += 1;
    el.setAttribute('data-nova', String(n));
    const tag = el.tagName.toLowerCase();
    const kind = (tag === 'input' || tag === 'textarea' || el.isContentEditable) ? 'field' :
                 (tag === 'select' ? 'select' : (tag === 'a' ? 'link' : 'button'));
    out.push([n, kind, label.slice(0, 80)]);
    if (n >= 120) break;
  }
  return out;
}
"""


def token() -> str:
    try:
        return (ROOT / "token").read_text(encoding="utf-8-sig").strip()
    except OSError:
        return ""


def taint(source: str) -> None:
    """Tell the bridge she has just read outside content."""
    try:
        req = urllib.request.Request(BRIDGE + "/api/taint", data=json.dumps({"source": source}).encode(),
                                     headers={"Content-Type": "application/json", "X-Nova-Token": token()})
        urllib.request.urlopen(req, timeout=2).close()
    except Exception:                                 # noqa: BLE001 -- the bridge may be restarting
        pass


def host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()


class Hands:
    def __init__(self, show: bool = False) -> None:
        self.jobs: "queue.Queue[tuple[str, dict, Future]]" = queue.Queue()
        self.show = show
        self.ctx = None
        self.pw = None
        self.last_used = time.time()
        threading.Thread(target=self._run, daemon=True).start()

    # ------------------------------------------------------------- plumbing
    def call(self, action: str, args: dict, timeout: float = 90) -> dict:
        fut: Future = Future()
        self.jobs.put((action, args, fut))
        return fut.result(timeout=timeout)

    def _run(self) -> None:
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        while True:
            try:
                action, args, fut = self.jobs.get(timeout=60)
            except queue.Empty:
                # Idle for 20 minutes: close the browser, keep the service.
                if self.ctx and time.time() - self.last_used > 1200:
                    self._close()
                continue
            self.last_used = time.time()
            fn = getattr(self, "do_" + action, None)
            if not fn:
                fut.set_result({"error": f"unknown action {action!r}"})
                continue
            # If Sir closed her Chrome, the first try fails on a dead connection:
            # detach, relaunch and try again, so she never reports "the browser is closed".
            for attempt in (1, 2):
                try:
                    fut.set_result(fn(**args))
                    break
                except Exception as exc:              # noqa: BLE001
                    gone = self._gone(exc)
                    if gone:
                        self._close()
                    if attempt == 1 and gone:
                        continue
                    fut.set_result({"error": f"{type(exc).__name__}: {str(exc)[:300]}"})
                    break

    @staticmethod
    def _gone(exc: Exception) -> bool:
        s = str(exc).lower()
        return any(k in s for k in ("closed", "target page", "disconnected", "connection refused",
                                    "econnrefused", "browser has been", "no window", "websocket"))

    def _close(self) -> None:
        """Detach (Chrome itself keeps running, signed in, minimised)."""
        try:
            if getattr(self, "cdp", None):
                self.cdp.close()
        except Exception:                             # noqa: BLE001
            pass
        self.ctx = self.cdp = None

    def browser(self):
        """Her Chrome: started as a plain Chrome (NOT launched by Playwright,
        because Google refuses sign-in in an automation-launched browser),
        then attached to over a local debugging port."""
        if self.ctx is not None:
            # Chrome closed by hand: the port is gone even though the old
            # connection object still answers .pages with [].
            alive = _port_up(CDP_PORT) and getattr(self, "cdp", None) is not None and self.cdp.is_connected()
            if alive:
                try:
                    _ = self.ctx.pages
                    return self.ctx
                except Exception:                     # noqa: BLE001
                    pass
            self._close()
        endpoint = f"http://127.0.0.1:{CDP_PORT}"
        if not _port_up(CDP_PORT):
            PROFILE.mkdir(parents=True, exist_ok=True)
            args = [CHROME, f"--user-data-dir={PROFILE}", f"--remote-debugging-port={CDP_PORT}",
                    "--remote-debugging-address=127.0.0.1", "--no-first-run", "--no-default-browser-check",
                    "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
                    "--disable-background-timer-throttling", "--window-size=1280,900"]
            if not self.show:
                args.append("--start-minimized")
            import subprocess
            subprocess.Popen(args, creationflags=0x08000000, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True)
            for _ in range(60):
                if _port_up(CDP_PORT):
                    break
                time.sleep(0.25)
        self.cdp = self.pw.chromium.connect_over_cdp(endpoint)
        self.ctx = self.cdp.contexts[0] if self.cdp.contexts else self.cdp.new_context()
        self.ctx.set_default_timeout(20000)
        return self.ctx

    def page(self):
        ctx = self.browser()
        pages = [p for p in ctx.pages if not p.is_closed()]
        if not pages:
            return ctx.new_page()
        return getattr(self, "_current", None) if getattr(self, "_current", None) in pages else pages[-1]

    def _settle(self, page) -> None:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
            page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:                             # noqa: BLE001 -- busy pages never go idle
            pass

    def _guard(self, page, what: str) -> None:
        if READ_ONLY_HOSTS.search(host(page.url)):
            raise PermissionError(f"{what} refused: {host(page.url)} is read-only for Nova "
                                  "(Messenger/Facebook). Use messenger_chats / messenger_read to read it.")

    def _snapshot(self, page, max_chars: int = MAX_TEXT, mark: bool = True) -> dict:
        self._settle(page)
        text = page.inner_text("body") if page.url != "about:blank" else ""
        text = re.sub(r"\n\s*\n+", "\n", text).strip()
        out = {"url": page.url, "title": page.title(), "text": text[:max_chars],
               "truncated": len(text) > max_chars}
        if mark and not READ_ONLY_HOSTS.search(host(page.url)):
            try:
                out["elements"] = [f"[{n}] {k}: {label}" for n, k, label in page.evaluate(MARK_JS)]
            except Exception:                         # noqa: BLE001
                out["elements"] = []
        if CAPTCHA.search(text[:3000]):
            out["captcha"] = ("This page is a captcha / bot check. Nova does not solve these: "
                              "tell Sir, and he can finish it in her window (web_show).")
        taint(host(page.url) or "web")
        return out

    def _target(self, page, target: str):
        t = str(target).strip()
        if t.isdigit():
            loc = page.locator(f'[data-nova="{t}"]')
            if loc.count() == 0:
                raise LookupError(f"no element [{t}] -- call web_read again, the page changed")
            return loc.first
        for loc in (page.get_by_role("button", name=t), page.get_by_role("link", name=t),
                    page.get_by_label(t), page.get_by_placeholder(t), page.get_by_text(t)):
            try:
                if loc.count():
                    return loc.first
            except Exception:                         # noqa: BLE001
                continue
        raise LookupError(f"nothing on the page matches {t!r}; use a number from web_read")

    # -------------------------------------------------------------- actions
    def do_open(self, url: str, show: bool = False) -> dict:
        if not re.match(r"^[a-z]+://", url) and not url.startswith(("about:", "chrome:")):
            url = "https://" + url
        page = self.page()
        page.goto(url, wait_until="domcontentloaded")
        self._current = page
        out = self._snapshot(page)
        if show:
            out["note"] = "On Sir's screen, in front." if self.do_show(True).get("front") else "Shown on screen."
        return out

    def do_scan(self, url: str, scrolls: int = 2) -> dict:
        """Background read for the school-notice watcher: its own tab (never the
        one he is looking at), no clicks, no taint (the text goes to a toolless
        judge, not to her), closed again afterwards."""
        page = self.browser().new_page()
        try:
            page.goto(url, wait_until="domcontentloaded")
            self._settle(page)
            # Facebook cuts long posts at "See more"; unfold them where it lets us.
            try:
                page.evaluate("""() => { for (const el of document.querySelectorAll('div[role=button]'))
                                   if (['See more', 'Tumingin pa'].includes((el.innerText || '').trim())) el.click(); }""")
            except Exception:                         # noqa: BLE001
                pass
            for _ in range(max(0, int(scrolls))):
                page.mouse.wheel(0, 2500)
                time.sleep(1.2)
            text = page.inner_text("body")
            return {"url": page.url, "text": re.sub(r"\n\s*\n+", "\n", text).strip()[:40000]}
        finally:
            try:
                page.close()
            except Exception:                         # noqa: BLE001
                pass

    def do_search(self, query: str, show: bool = False) -> dict:
        return self.do_open("https://www.google.com/search?hl=en&q=" + urllib.parse.quote_plus(query), show)

    def do_read(self, max_chars: int = MAX_TEXT) -> dict:
        return self._snapshot(self.page(), int(max_chars))

    def do_click(self, target: str, show: bool = False) -> dict:
        page = self.page()
        self._guard(page, "Clicking")
        before = len(self.browser().pages)
        self._target(page, target).click()
        time.sleep(0.6)
        pages = self.browser().pages
        if len(pages) > before:                       # it opened a new tab: follow it
            self._current = pages[-1]
            page = pages[-1]
        out = self._snapshot(page)
        if show:                                      # the new tab goes in front, not behind his windows
            page.bring_to_front()
            self._to_foreground(page)
        return out

    def do_type(self, target: str, text: str, enter: bool = False) -> dict:
        page = self.page()
        self._guard(page, "Typing")
        el = self._target(page, target)
        el.fill(text)
        if enter:
            el.press("Enter")
        return self._snapshot(page)

    def do_press(self, keys: str) -> dict:
        page = self.page()
        self._guard(page, "Key presses")
        page.keyboard.press(keys)
        return self._snapshot(page)

    def do_back(self) -> dict:
        page = self.page()
        page.go_back()
        return self._snapshot(page)

    def do_scroll(self, down: bool = True) -> dict:
        page = self.page()
        page.mouse.wheel(0, 900 if down else -900)
        time.sleep(0.4)
        return self._snapshot(page)

    def do_tabs(self, switch: int = -1, close: int = -1) -> dict:
        pages = [p for p in self.browser().pages if not p.is_closed()]
        if 0 <= close < len(pages):
            pages[close].close()
            pages = [p for p in self.browser().pages if not p.is_closed()]
        if 0 <= switch < len(pages):
            self._current = pages[switch]
            pages[switch].bring_to_front()
        return {"tabs": [f"[{i}] {p.title()[:60]} -- {p.url[:100]}" for i, p in enumerate(pages)]}

    def do_screenshot(self) -> dict:
        page = self.page()
        SHOTS.mkdir(parents=True, exist_ok=True)
        path = SHOTS / f"web-{time.strftime('%Y%m%d-%H%M%S')}.png"
        page.screenshot(path=str(path))
        taint(host(page.url) or "web")
        return {"path": str(path), "url": page.url}

    def do_show(self, visible: bool = True) -> dict:
        """Bring her browser window up (to sign in, solve a captcha) or minimise it."""
        page = self.page()
        cdp = self.browser().new_cdp_session(page)
        win = cdp.send("Browser.getWindowForTarget")["windowId"]
        cdp.send("Browser.setWindowBounds", {"windowId": win,
                                             "bounds": {"windowState": "normal" if visible else "minimized"}})
        front = False
        if visible:
            page.bring_to_front()
            front = self._to_foreground(page)
        return {"visible": visible, "front": front, "url": page.url}

    def _to_foreground(self, page) -> bool:
        """Put her Chrome window in front of everything else. Chrome cannot do
        this itself (Windows' foreground lock), so find the window by a marker
        title and raise it with the Alt-key trick."""
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        marker = f"NOVA-FRONT-{int(time.time() * 1000)}"
        try:
            old = page.evaluate("t => { const o = document.title; document.title = t; return o; }", marker)
        except Exception:                             # noqa: BLE001
            return False
        found = []
        proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd, _l):
            buf = ctypes.create_unicode_buffer(512)
            u.GetWindowTextW(hwnd, buf, 512)
            if marker in buf.value:
                found.append(hwnd)
                return False
            return True
        for _ in range(20):                           # the title takes a moment to reach the window
            u.EnumWindows(proc(cb), 0)
            if found:
                break
            time.sleep(0.05)
        try:
            page.evaluate("t => { document.title = t; }", old)
        except Exception:                             # noqa: BLE001
            pass
        if not found:
            return False
        hwnd = found[0]
        SW_RESTORE, VK_MENU, KEYUP = 9, 0x12, 0x0002
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, SW_RESTORE)
        u.keybd_event(VK_MENU, 0, 0, 0)               # a key press grants foreground rights
        u.keybd_event(VK_MENU, 0, KEYUP, 0)
        u.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0003)  # HWND_TOPMOST, no move/size
        u.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0003)  # back to normal, but now on top
        u.BringWindowToTop(hwnd)
        u.SetForegroundWindow(hwnd)
        return u.GetForegroundWindow() == hwnd

    # ------------------------------------------------------ Messenger (read-only)
    def _messenger(self):
        page = self.page()
        if "messenger.com" not in host(page.url):
            page.goto("https://www.messenger.com/", wait_until="domcontentloaded")
        self._current = page
        self._settle(page)
        if re.search(r"/login|checkpoint", page.url) or page.locator("input[name=pass]").count():
            raise PermissionError("Messenger is not signed in in Nova's browser. Sir must sign in once "
                                  "himself: say 'show your browser' and log in to messenger.com there.")
        return page

    def _chat_links(self, page) -> list[dict]:
        return page.evaluate(r"""() => {
          const seen = new Set(), out = [];
          for (const a of document.querySelectorAll('a[href*="/t/"]')) {
            const href = a.getAttribute('href');
            if (seen.has(href)) continue;
            seen.add(href);
            const text = (a.innerText || '').trim().replace(/\n+/g, ' | ');
            if (!text) continue;
            const bold = [...a.querySelectorAll('span')].some(s => parseInt(getComputedStyle(s).fontWeight) >= 600);
            out.push({href, text: text.slice(0, 160), unread: bold});
            if (out.length >= 25) break;
          }
          return out;
        }""")

    def do_messenger_chats(self, limit: int = 15) -> dict:
        page = self._messenger()
        page.wait_for_selector('a[href*="/t/"]', timeout=15000)
        chats = self._chat_links(page)[: int(limit)]
        taint("messenger")
        return {"chats": [("(unread) " if c["unread"] else "") + c["text"] for c in chats],
                "note": "Read-only. Listing chats does not mark anything as seen."}

    def do_messenger_read(self, chat: str, max_chars: int = 4000) -> dict:
        page = self._messenger()
        page.wait_for_selector('a[href*="/t/"]', timeout=15000)
        want = chat.lower().strip()
        match = next((c for c in self._chat_links(page) if want in c["text"].lower()), None)
        if not match:
            raise LookupError(f"no recent chat matches {chat!r}; call messenger_chats for the names")
        page.goto(urllib.parse.urljoin("https://www.messenger.com", match["href"]), wait_until="domcontentloaded")
        self._settle(page)
        time.sleep(1.5)
        main = page.locator("[role=main]")
        text = main.first.inner_text() if main.count() else page.inner_text("body")
        text = re.sub(r"\n\s*\n+", "\n", text).strip()
        taint("messenger")
        return {"chat": match["text"].split(" | ")[0], "messages": text[-int(max_chars):],
                "note": "Read-only. Opening a chat shows the sender it was seen."}


    # ---------------------------------------------------------- Gmail (read-only)
    def _gmail(self, where: str = "inbox"):
        page = self.page()
        page.goto("https://mail.google.com/mail/u/0/#" + where, wait_until="domcontentloaded")
        self._current = page
        if "accounts.google.com" in host(page.url) or "workspace.google.com" in host(page.url):
            raise PermissionError("Gmail is not signed in in Nova's browser. Sir must sign in once "
                                  "himself: say 'show your browser' and log in to Gmail there.")
        page.wait_for_selector("tr.zA, td.TC", timeout=25000)
        time.sleep(0.8)
        return page

    ROWS_JS = r"""(limit) => [...document.querySelectorAll('tr.zA')].filter(r => r.offsetParent)
        .slice(0, limit).map((r, i) => {
          const q = s => (r.querySelector(s) || {});
          const who = q('.yP, .zF');
          const when = q('td.xW span, .xW span');
          return {i, unread: r.classList.contains('zE'),
                  from: (who.getAttribute && (who.getAttribute('name') || who.innerText)) || '',
                  email: (who.getAttribute && who.getAttribute('email')) || '',
                  subject: (q('.bog').innerText || '').trim(),
                  snippet: (q('.y2').innerText || '').replace(/^\s*-\s*/, '').trim().slice(0, 140),
                  date: (when.getAttribute && (when.getAttribute('title') || when.innerText)) || ''};
        })"""

    def do_gmail_inbox(self, limit: int = 15, unread_only: bool = False, query: str = "") -> dict:
        """Newest mail, or a Gmail search ("from:mom", "is:unread newer_than:2d")."""
        where = "search/" + urllib.parse.quote(query) if query else "inbox"
        page = self._gmail(where)
        rows = page.evaluate(self.ROWS_JS, 50)
        if unread_only:
            rows = [r for r in rows if r["unread"]]
        taint("gmail")
        return {"mail": [f"{'(unread) ' if r['unread'] else ''}{r['date']} -- {r['from']}"
                         f"{' <' + r['email'] + '>' if r['email'] else ''}: {r['subject']} -- {r['snippet']}"
                         for r in rows[: int(limit)]] or ["Nothing here."],
                "note": "Read-only. Listing does not mark anything read."}

    def do_gmail_read(self, match: str, query: str = "", max_chars: int = 5000) -> dict:
        """Open the newest message whose sender/subject/snippet contains `match`."""
        page = self._gmail("search/" + urllib.parse.quote(query) if query else "inbox")
        rows = page.evaluate(self.ROWS_JS, 50)
        want = match.lower().strip()
        hit = next((r for r in rows if want in (r["from"] + " " + r["email"] + " " + r["subject"] + " "
                                                + r["snippet"]).lower()), None)
        if not hit:
            raise LookupError(f"no message in this view matches {match!r}; try gmail_inbox with a query")
        page.locator("tr.zA").filter(visible=True).nth(hit["i"]).click()   # internal: the read itself
        page.wait_for_selector("div.a3s, h2.hP", timeout=20000)
        time.sleep(0.8)
        subject = page.locator("h2.hP").first.inner_text() if page.locator("h2.hP").count() else hit["subject"]
        bodies = page.locator("div.a3s")
        text = "\n---\n".join(bodies.nth(k).inner_text() for k in range(bodies.count())) or page.inner_text("[role=main]")
        text = re.sub(r"\n\s*\n+", "\n", text).strip()
        taint("gmail")
        page.go_back()
        return {"from": f"{hit['from']} <{hit['email']}>", "subject": subject, "date": hit["date"],
                "body": text[-int(max_chars):],
                "note": "Read-only. Opening it marks it read in Gmail."}


# ------------------------------------------------------------------ service

class Handler(BaseHTTPRequestHandler):
    hands: Hands = None  # type: ignore[assignment]

    def log_message(self, *_a) -> None:
        pass

    def _send(self, obj: dict, code: int = 200) -> None:
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send({"ok": True, "browser": self.hands.ctx is not None})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if not secrets.compare_digest(self.headers.get("X-Nova-Token", ""), token()):
            self._send({"error": "forbidden"}, 403)
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            self._send({"error": "bad json"}, 400)
            return
        if self.path != "/do":
            self._send({"error": "not found"}, 404)
            return
        try:
            self._send(self.hands.call(str(body.get("action")), dict(body.get("args") or {})))
        except Exception as exc:                      # noqa: BLE001
            self._send({"error": f"{type(exc).__name__}: {exc}"})


def main() -> int:
    show = "--show" in sys.argv
    Handler.hands = Hands(show=show)
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        print("nova_hands already running")
        return 0
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
