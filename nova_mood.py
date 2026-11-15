#!/usr/bin/env python3
"""
nova_mood.py -- what colour the orb should lean towards, and why.

The orb's mode (idle / listening / thinking / speaking / approval) says WHAT
she is doing. This adds the flavour of it, for free -- no model call, just the
tool she is holding and the words coming out of the speakers:

    while she works     searching  web, pages, research
                        music      Spotify, TV, volume, media keys
                        hands      clicking, typing, apps, shell, the desktop
                        memory     remember / recall / notes / rules
                        looking    screen, camera, "look at this"
                        talking    Gmail, Discord, calls, Minecraft chat
                        fault      a tool just came back with an error
    while she speaks    playful    teasing, jokes, "!"
                        warm       care: breaks, sleep, water, "proud of you"
                        sorry      something failed, could not, unfortunately
                        alert      reminders, heads-up, warnings, due soon
                        done       success: done, all set, sorted
                        (none)     plain speech keeps her own colour

The orb owns the colours and the motion; this module only names the flavour.
"""
from __future__ import annotations

import re
import time

TOOL_GROUPS = [
    ("searching", ("web_search", "web_open", "web_read", "web_research", "web_find", "web_text",
                   "web_extract", "web_scroll", "web_back", "web_tabs", "webfetch", "websearch",
                   "stock_quote", "watch_page", "class_schedule", "school_days")),
    ("music", ("spotify", "tv", "volume", "mute", "media_key")),
    ("memory", ("remember", "recall", "forget", "save_note", "save_rule", "list_rules", "delete_rule",
                "save_skill", "list_skills", "day_log", "habits", "open_loops", "close_loop",
                "remind", "reminder", "routine", "meeting", "notes_")),
    ("looking", ("capture_screen", "read_screen", "desktop_look", "look_at_this", "look_through_camera",
                 "web_screenshot", "web_show", "list_windows", "foreground_app", "presence")),
    ("talking", ("gmail", "messenger", "whatsapp", "call_me", "minecraft_say", "say")),
    ("hands", ("run_ps", "run_headless", "bash", "open_app", "close_window", "focus_window", "type_text",
               "desktop_click", "desktop_type", "desktop_keys", "web_click", "web_type", "web_keys",
               "open_settings", "clipboard", "minecraft_", "start_job", "lock_pc", "sleep_pc",
               "display_off", "nova_panel", "edit", "write", "read", "grep", "glob")),
]

CAPTIONS = {
    "searching": "SEARCHING", "music": "ON IT", "hands": "WORKING", "memory": "REMEMBERING",
    "looking": "LOOKING", "talking": "MESSAGING", "fault": "HMM",
}

_WORDS = {
    "sorry":   r"\b(sorry|apolog|unfortunately|couldn'?t|could not|can'?t|cannot|failed|isn'?t working|"
               r"not working|no luck|went wrong|error|broke|down right now)\b",
    "alert":   r"\b(reminder|remind|heads[- ]up|warning|careful|urgent|due|deadline|in \d+ minutes|"
               r"don'?t forget|battery|low on|suspended|alert)\b",
    "warm":    r"\b(break|rest|sleep|water|stretch|take care|proud|well done|good job|you'?ve got this|"
               r"hope you|feel better|glad|late,? sir|go to bed|breathe)\b",
    "playful": r"\b(haha|heh|cheeky|joke|kidding|naturally|obviously|shocking|bold of you|of course you|"
               r"tragic|dramatic|scandal)\b",
    "done":    r"\b(done|all set|sorted|finished|ready|playing|opened|sent|saved|there you go|"
               r"got it|on screen now|complete)\b",
}
_RX = {k: re.compile(v, re.I) for k, v in _WORDS.items()}
ORDER = ["sorry", "alert", "warm", "playful", "done"]   # first match wins


def tool_group(name: str) -> str:
    """mcp__nova__web_search -> searching. Unknown tools are just 'hands'."""
    n = str(name or "").lower().rsplit("__", 1)[-1]
    for group, keys in TOOL_GROUPS:
        if any(n == k or n.startswith(k) or k in n for k in keys):
            return group
    return "hands"


def speech_mood(text: str) -> str:
    """The feeling of one spoken sentence, or '' for plain speech."""
    t = str(text or "")
    if not t.strip():
        return ""
    for mood in ORDER:
        if _RX[mood].search(t):
            return mood
    if t.rstrip().endswith("!") and len(t) < 160:
        return "playful"
    return ""


class Tracker:
    """Fed by the bridge's activity stream; asked by /api/orb."""

    FAULT_S = 2.5          # how long a failed tool tints her
    MOOD_HOLD_S = 2.0      # a mood stays this long after the sentence that set it

    def __init__(self) -> None:
        self.tool = ""             # group of the tool in hand
        self.tool_open = 0         # tools started minus tools finished this turn
        self.fault_at = 0.0
        self.mood = ""
        self.mood_at = 0.0
        self.last_said = ""

    def on_event(self, kind: str, text: str = "", detail: str = "") -> None:
        if kind == "tool":
            self.tool = tool_group(text)
            self.tool_open += 1
        elif kind == "tool_done":
            self.tool_open = max(0, self.tool_open - 1)
            if text == "error":
                self.fault_at = time.time()
        elif kind in ("exit", "turn_end"):
            self.tool, self.tool_open = "", 0

    def on_saying(self, sentence: str) -> None:
        if sentence and sentence != self.last_said:
            self.last_said = sentence
            mood = speech_mood(sentence)
            if mood or time.time() - self.mood_at > self.MOOD_HOLD_S:
                self.mood, self.mood_at = mood, time.time()

    def flavour(self, mode: str) -> str:
        now = time.time()
        if mode == "thinking":
            if now - self.fault_at < self.FAULT_S:
                return "fault"
            return self.tool if self.tool_open and self.tool else ""
        if mode == "speaking":
            return self.mood
        if mode == "idle":
            self.tool, self.tool_open = "", 0
            if now - self.mood_at > self.MOOD_HOLD_S:
                self.mood = ""
        return ""


if __name__ == "__main__":
    for s in ["Sorry Sir, the page wouldn't load.", "Heads-up: your class starts in 10 minutes.",
              "You've been at it two hours, take a break.", "Bold of you to ask that at 3am.",
              "Done, it's playing now.", "The capital of France is Paris."]:
        print(f"{speech_mood(s) or '-':8} {s}")
    for n in ["mcp__nova__web_search", "mcp__nova__spotify_play", "Bash", "mcp__nova__remember",
              "mcp__nova__look_through_camera", "mcp__nova__gmail_send", "mcp__nova__whatever"]:
        print(f"{tool_group(n):10} {n}")
