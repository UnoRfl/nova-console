#!/usr/bin/env python3
"""voice_test.py -- timing and interruption check for nova_voice, played silently."""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import nova_voice as V  # noqa: E402

v = V.Voice()
v.cfg["volume"] = 0.0          # silent: this measures timing, it is not a listening test

r = v.say("Good evening, Sir. The server is up with three players online. "
          "Your render finished ten minutes ago. Shall I open it?")
print("full reply :", r)

threading.Timer(1.2, v.stop).start()
t = time.time()
r = v.say("This is a long reply that should be cut off. It keeps going for a while. "
          "And then some more. And even more after that. Still talking here.")
print("interrupted:", r, "returned after %.2fs" % (time.time() - t))

text = "Sir. **Done** -- see `C:\\x\\y.txt` and https://a.b/c. [[remember: x]] ::CAD foo ::END"
print("cleaned    :", V.sentences(V.speakable(text)))

ok = (r.get("interrupted") is True and time.time() - t < 2.0)
print("RESULT:", "PASS" if ok else "NEEDS WORK")
