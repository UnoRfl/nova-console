#!/usr/bin/env python3
"""memory_test.py -- remember / recall / forget through the bridge's own code paths."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.argv = ["x"]
import nova_bridge as b  # noqa: E402
import nova_memory as m  # noqa: E402

checks = []
checks.append(("core facts load", len(b.load_memories()) >= 1))
checks.append(("'forget it' is not a deletion", b.forget_from_prompt("forget it") == []))
checks.append(("a question is not stored", b.remember_from_prompt("remember what I said?") == []))

stored = b.remember_from_prompt("Nova, remember that the test fact is purple elephants")
checks.append(("explicit remember is stored", stored == ["the test fact is purple elephants"]))
checks.append(("it is recalled for a related question", "purple elephants" in m.recall_block("what was the test fact about elephants")))
gone = b.forget_from_prompt("Nova, forget that purple elephants")
checks.append(("forget removes it", gone == ["the test fact is purple elephants"] and not m.search("purple elephants", kinds=("fact",))))
checks.append(("project notes answer a project question", "cobblemon" in m.recall_block("restart the cobblemon server").lower()))
checks.append(("unrelated question recalls nothing", m.recall_block("what is two plus two") == ""))
checks.append(("persona builds", 3000 < len(b.build_persona()) < 40000))

for name, ok in checks:
    print(("  ok   " if ok else "  FAIL ") + name)
print("RESULT:", "PASS" if all(ok for _, ok in checks) else "NEEDS WORK")
