"""Nova's hands on the Windows desktop, through UI Automation (what screen
readers use), so she clicks real buttons by name instead of guessing pixels.

  look(window)            numbered list of what can be clicked / typed in
  click(target, window)   by number from look(), or by name
  set_text(target, text)  fill a text box
  keys("ctrl+l")          key presses to the foreground window
  click_at(x, y)          last resort, with capture_screen for eyes
  whatsapp_chats / whatsapp_read   READ-ONLY WhatsApp, his own signed-in app

Read-only apps (WhatsApp) refuse click / set_text / keys from the model: only
the reader functions here may touch them, and those never type a message.
Everything read from another app is untrusted: each read tells the bridge
(/api/taint) so the gate refuses risky actions until Sir speaks again.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path.home() / ".nova"
LAST = ROOT / "captures" / "desk-last.json"
READ_ONLY_APPS = re.compile(r"^(?:WhatsApp)$", re.I)
WHATSAPP_APP = r"shell:AppsFolder\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"
CLICKABLE = {"ButtonControl", "MenuItemControl", "HyperlinkControl", "TabItemControl", "ListItemControl",
             "CheckBoxControl", "RadioButtonControl", "ComboBoxControl", "EditControl", "TreeItemControl",
             "DataItemControl", "SplitButtonControl", "ToggleButtonControl" }


def _auto():
    import uiautomation as auto
    return auto


def taint(source: str) -> None:
    try:
        tok = (ROOT / "token").read_text(encoding="utf-8-sig").strip()
        req = urllib.request.Request("http://127.0.0.1:8765/api/taint", data=json.dumps({"source": source}).encode(),
                                     headers={"Content-Type": "application/json", "X-Nova-Token": tok})
        urllib.request.urlopen(req, timeout=2).close()
    except Exception:                                 # noqa: BLE001
        pass


def _window(name: str = ""):
    auto = _auto()
    if not name:
        return auto.GetForegroundControl().GetTopLevelControl()
    want = name.lower()
    for w in auto.GetRootControl().GetChildren():
        if want in (w.Name or "").lower():
            return w
    raise LookupError(f"no open window matches {name!r}; list_windows shows what is open")


def _guard(win, what: str) -> None:
    if READ_ONLY_APPS.match((win.Name or "").strip()):
        raise PermissionError(f"{what} refused: {win.Name} is read-only for Nova. Use whatsapp_chats / "
                              "whatsapp_read to read it; she never types or clicks there.")


def look(window: str = "", limit: int = 80) -> str:
    """Numbered controls in a window (default: the one in front)."""
    auto = _auto()
    with auto.UIAutomationInitializerInThread():
        win = _window(window)
        found = []
        for ctrl, _depth in auto.WalkControl(win, maxDepth=25):
            if ctrl.ControlTypeName not in CLICKABLE:
                continue
            name = " ".join((ctrl.Name or "").split())
            if not name and ctrl.ControlTypeName != "EditControl":
                continue
            r = ctrl.BoundingRectangle
            if r.width() <= 0 or r.height() <= 0:
                continue
            found.append({"n": len(found) + 1, "type": ctrl.ControlTypeName.replace("Control", ""),
                          "name": name[:80], "x": r.xcenter(), "y": r.ycenter()})
            if len(found) >= limit:
                break
        LAST.parent.mkdir(parents=True, exist_ok=True)
        LAST.write_text(json.dumps({"window": win.Name, "items": found}), encoding="utf-8")
        taint("desktop")
        return f"Window: {win.Name}\n" + "\n".join(f"[{i['n']}] {i['type']}: {i['name'] or '(text box)'}"
                                                   for i in found)


def _find(win, target: str):
    auto = _auto()
    t = str(target).strip()
    if t.isdigit():
        try:
            last = json.loads(LAST.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise LookupError("call desktop_look first")
        item = next((i for i in last["items"] if i["n"] == int(t)), None)
        if not item:
            raise LookupError(f"no [{t}] in the last look; look again")
        return None, (item["x"], item["y"])
    want = t.lower()
    best = None
    for ctrl, _d in auto.WalkControl(win, maxDepth=25):
        if ctrl.ControlTypeName in CLICKABLE and want in (ctrl.Name or "").lower():
            if (ctrl.Name or "").lower().strip() == want:
                return ctrl, None
            best = best or ctrl
    if best is None:
        raise LookupError(f"nothing named {t!r} in {win.Name}; desktop_look lists what is there")
    return best, None


def click(target: str, window: str = "", double: bool = False, right: bool = False) -> str:
    auto = _auto()
    with auto.UIAutomationInitializerInThread():
        win = _window(window)
        _guard(win, "Clicking")
        win.SetActive()
        ctrl, xy = _find(win, target)
        if xy:
            fn = auto.RightClick if right else (auto.DoubleClick if double else auto.Click)
            fn(*xy)
            label = f"[{target}]"
        else:
            if right:
                ctrl.RightClick(simulateMove=False)
            elif double:
                ctrl.DoubleClick(simulateMove=False)
            else:
                ctrl.Click(simulateMove=False)
            label = ctrl.Name
        time.sleep(0.4)
        return f"Clicked {label} in {win.Name}."


def click_at(x: int, y: int, double: bool = False, right: bool = False) -> str:
    auto = _auto()
    with auto.UIAutomationInitializerInThread():
        ctrl = auto.ControlFromPoint(int(x), int(y))
        if ctrl:
            _guard(ctrl.GetTopLevelControl(), "Clicking")
        (auto.RightClick if right else auto.DoubleClick if double else auto.Click)(int(x), int(y))
        return f"Clicked at {x},{y}" + (f" ({ctrl.Name[:40]})" if ctrl and ctrl.Name else "") + "."


def set_text(target: str, text: str, window: str = "", enter: bool = False) -> str:
    auto = _auto()
    with auto.UIAutomationInitializerInThread():
        win = _window(window)
        _guard(win, "Typing")
        win.SetActive()
        ctrl, xy = _find(win, target)
        if xy:
            auto.Click(*xy)
            auto.SendKeys("{Ctrl}a", waitTime=0.05)
            auto.SendKeys(text.replace("{", "{{}").replace("}", "{}}"), interval=0.005, waitTime=0.1)
        else:
            try:
                ctrl.GetValuePattern().SetValue(text)
            except Exception:                         # noqa: BLE001 -- not every box has a value pattern
                ctrl.Click(simulateMove=False)
                auto.SendKeys("{Ctrl}a", waitTime=0.05)
                auto.SendKeys(text.replace("{", "{{}").replace("}", "{}}"), interval=0.005, waitTime=0.1)
        if enter:
            auto.SendKeys("{Enter}")
        return f"Typed into {target} in {win.Name}."


KEYMAP = {"ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win", "enter": "Enter",
          "esc": "Esc", "escape": "Esc", "tab": "Tab", "up": "Up", "down": "Down", "left": "Left",
          "right": "Right", "delete": "Del", "del": "Del", "backspace": "Back", "home": "Home", "end": "End",
          "pageup": "PageUp", "pagedown": "PageDown", "space": "Space"}


def keys(combo: str, window: str = "") -> str:
    """'ctrl+l', 'alt+tab', 'f5', 'enter'."""
    auto = _auto()
    with auto.UIAutomationInitializerInThread():
        win = _window(window)
        _guard(win, "Key presses")
        win.SetActive()
        parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
        seq = "".join("{" + KEYMAP.get(p, p.upper() if re.fullmatch(r"f\d{1,2}", p) else p) + "}" for p in parts)
        auto.SendKeys(seq, waitTime=0.1)
        return f"Pressed {combo} in {win.Name}."


# ------------------------------------------------------------- WhatsApp (read-only)

def _whatsapp():
    auto = _auto()
    win = auto.WindowControl(searchDepth=1, Name="WhatsApp", ClassName="WinUIDesktopWin32WindowClass")
    hide_after = False
    if not win.Exists(0.5):
        subprocess.Popen(creationflags=0x08000000, args=["explorer.exe", WHATSAPP_APP])
        if not win.Exists(15):
            raise RuntimeError("WhatsApp did not open. Is the WhatsApp app installed and signed in?")
        hide_after = True                             # it was only in the tray: put it back after
    elif win.IsMinimize():
        # A minimised WebView2 window shows no chat list to UI Automation.
        # Restore it without taking focus; minimise it again when done.
        auto.ShowWindow(win.NativeWindowHandle, 4)   # SW_SHOWNOACTIVATE
        hide_after = True
    grid = win.DataGridControl(searchDepth=20, Name="Chat list")
    if not grid.Exists(10):
        _put_back(win, hide_after)
        raise RuntimeError("WhatsApp is open but shows no chat list (signed out, or still loading).")
    return win, grid, hide_after


def _put_back(win, hide_after: bool) -> None:
    if hide_after:
        try:
            _auto().ShowWindow(win.NativeWindowHandle, 7)   # SW_SHOWMINNOACTIVE
        except Exception:                             # noqa: BLE001
            pass


def _rows(grid) -> list[str]:
    return [" ".join((c.Name or "").replace("​", "").split()) for c in grid.GetChildren() if c.Name]


def whatsapp_chats(limit: int = 15) -> str:
    """Most recent WhatsApp chats with time and last message. Read-only."""
    auto = _auto()
    with auto.UIAutomationInitializerInThread():
        win, grid, hide_after = _whatsapp()
        rows = [r if len(r) <= 220 else r[:217] + "..." for r in _rows(grid)[: int(limit)]]
        _put_back(win, hide_after)
        taint("whatsapp")
        return "\n".join(rows) or "No chats visible."


def _texts_outside(ctrl, skip) -> list[str]:
    out = []
    for child in ctrl.GetChildren():
        if skip(child):
            continue
        if child.ControlTypeName in ("TextControl", "GroupControl") and child.Name and not child.GetFirstChildControl():
            out.append(child.Name)
        elif child.ControlTypeName == "GroupControl" and child.Name and child.Name.strip().endswith(":"):
            out.append("\n" + child.Name.strip() + " ")       # a "You:" / sender block starts
        out += _texts_outside(child, skip)
    return out


def whatsapp_read(chat: str, max_chars: int = 3000) -> str:
    """Open a WhatsApp chat by name (e.g. "Mom") and read its latest messages.
    Read-only: she never types. Opening it shows the sender blue ticks, and the
    WhatsApp window comes to the front for a moment."""
    auto = _auto()
    with auto.UIAutomationInitializerInThread():
        before = auto.GetForegroundControl().GetTopLevelControl()
        win, grid, hide_after = _whatsapp()
        want = chat.lower().strip()
        win.SetActive()
        time.sleep(0.3)
        row = next((c for c in grid.GetChildren() if want in (c.Name or "").lower()), None)
        if row is None:
            # Not among the visible recent chats: use WhatsApp's own search box.
            box = win.EditControl(searchDepth=25, Name="Search or start a new chat")
            box.Click(simulateMove=False)
            try:
                box.GetValuePattern().SetValue(chat)
            except Exception:                         # noqa: BLE001
                auto.SendKeys(chat, interval=0.01)
            time.sleep(1.5)
            row = next((c for c in grid.GetChildren() if want in (c.Name or "").lower()), None)
            if row is None:
                auto.SendKeys("{Esc}")
                raise LookupError(f"no WhatsApp chat matches {chat!r}")
        title = " ".join((row.Name or "").split())[:60]
        row.Click(simulateMove=False)
        time.sleep(2.0)
        doc = win.DocumentControl(searchDepth=12, AutomationId="RootWebArea")
        nav = {"Chat list", "chat-list-filters"}
        parts = _texts_outside(doc, lambda c: c.Name in nav or c.ControlTypeName in ("DataGridControl", "TabControl"))
        text = re.sub(r"[ \t]+", " ", "".join(p if p.startswith("\n") else p + " " for p in parts))
        text = re.sub(r"\n\s*", "\n", text).strip()
        _put_back(win, hide_after)
        try:
            if before and before.Name != win.Name:
                before.SetActive()                    # give him his window back
        except Exception:                             # noqa: BLE001
            pass
        taint("whatsapp")
        return f"Chat: {title}\n" + text[-int(max_chars):]
