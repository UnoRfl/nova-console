"""State files that survive being killed mid-write.

The bridge is stopped by killing it. A plain write_text that is cut off leaves
half a file, the loader then reads "nothing", and the next save writes that
nothing back: every reminder gone, silently. Here a save goes to a temp file
that replaces the real one in a single step, and a file that will not parse is
kept as <name>.corrupt instead of being overwritten.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def write_text(path: Path, text: str) -> None:
    """Whole file or nothing. Raises OSError like Path.write_text."""
    path = Path(path)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def write_json(path: Path, data, **kw) -> None:
    write_text(path, json.dumps(data, **kw))


def read_json(path: Path, default):
    """The parsed file, or default when it is missing. A file that exists but
    will not parse is copied to <name>.corrupt first, so it can be recovered."""
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        if raw.strip():
            try:
                path.with_name(path.name + ".corrupt").write_text(raw, encoding="utf-8")
            except OSError:
                pass
        return default
