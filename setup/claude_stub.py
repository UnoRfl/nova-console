#!/usr/bin/env python3
"""
claude_stub.py -- a stand-in for the Claude CLI, for tests.

Speaks the same stream-json protocol the bridge expects (system, stream_event
text deltas, assistant, result), with realistic pauses, so the pipeline --
streaming, sentence-by-sentence speech, acknowledgements, memory markers,
projector blocks -- can be exercised without a signed-in Claude.

It answers from a tiny script keyed on words in the prompt. It is not a model.
"""
import json
import sys
import time


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def stream_text(text, delay=0.03):
    words = text.split(" ")
    for i, w in enumerate(words):
        out({"type": "stream_event", "event": {"type": "content_block_delta",
             "delta": {"type": "text_delta", "text": w + (" " if i < len(words) - 1 else "")}}})
        time.sleep(delay)


out({"type": "system", "subtype": "init", "session_id": "stub-session"})

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        prompt = json.loads(line)["message"]["content"][0]["text"]
    except Exception:
        continue
    low = prompt.lower()

    if "single word: ready" in low:
        reply = "ready"
    elif "library of how-to recipes" in low:
        # the skill judge (nova_skills.LEARN_PROMPT)
        reply = ("NAME: Tidy the downloads folder\n"
                 "WHEN: tidy, clean up or sort the downloads folder\n"
                 "REPLACES: none\n"
                 "STEPS:\n1. recent_files where=downloads days=30\n"
                 "2. Bash: move installers into Downloads/Installers\n"
                 "Pitfall: open_app cannot open folders; use Bash explorer instead.")
    elif "tidy the downloads" in low:
        # a three-tool turn, the second tool failing first time
        for name, bad in (("mcp__nova__recent_files", False), ("mcp__nova__open_app", True), ("Bash", False)):
            out({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": name, "input": {"where": "downloads"}}]}})
            time.sleep(0.1)
            out({"type": "user", "message": {"content": [{"type": "tool_result", "is_error": bad,
                 "content": [{"type": "text", "text": "no app called downloads"}] if bad else "ok"}]}})
        reply = "Your downloads are tidy, Sir."
    elif "tool" in low:
        # a turn that reaches for a tool before saying anything
        time.sleep(0.4)
        out({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "mcp__nova__pc_status", "input": {}}]}})
        time.sleep(1.5)
        out({"type": "user", "message": {"content": [{"type": "tool_result", "is_error": False}]}})
        reply = "The machine is in good order, Sir. Memory is at sixty percent and the disk has plenty of room."
    elif "shape" in low:
        reply = ('Here is a simple block, Sir. ::CAD title="Block" unit="cm"\n'
                 "return [{label:'Block', note:'A cube.', geom: cuboid({size:[10,10,10]})}];\n::END")
    elif "favourite" in low or "favorite" in low:
        reply = "Noted, Sir. I shall keep that in mind. [[remember: Uno's favourite test colour is teal]]"
    else:
        reply = ("Good evening, Sir. This is a longer answer so the first sentence can be heard early. "
                 "The second sentence arrives a moment later. And here is a third to finish.")

    time.sleep(0.5)                       # "thinking"
    stream_text(reply)
    out({"type": "assistant", "message": {"content": [{"type": "text", "text": reply}]}})
    out({"type": "result", "subtype": "success", "is_error": False, "result": reply,
         "session_id": "stub-session", "num_turns": 1, "total_cost_usd": 0.0123,
         "permission_denials": []})
