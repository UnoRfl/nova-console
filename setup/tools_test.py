#!/usr/bin/env python3
"""
tools_test.py -- call the read-only tools directly, then talk to the server
over stdio exactly as an engine would (initialize, tools/list, tools/call).
Nothing here changes the PC or the Minecraft server.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import nova_tools as T  # noqa: E402

checks = []


def check(name, ok, detail=""):
    checks.append(ok)
    print(("  ok   " if ok else "  FAIL ") + name + (f"   {detail}" if detail else ""))


# every tool has a tier, and nothing risky is free
check("every tool has a tier", all(f.__name__ in T.TIERS for f in T.ALL),
      str([f.__name__ for f in T.ALL if f.__name__ not in T.TIERS]))
risky = {"sleep_pc", "lock_pc", "close_window", "forget", "minecraft_stop", "minecraft_restart"}
check("risky tools need confirmation", all(T.tier(n) == "confirm" for n in risky))
check("unknown tool defaults to confirm", T.tier("mcp__nova__does_not_exist") == "confirm")

# read-only calls
out = T.pc_status();        check("pc_status", "build" in out.lower() and len(out) > 100,out.splitlines()[0][:70])
out = T.list_windows();     check("list_windows", "window" in out.lower(), out.splitlines()[0][:70])
out = T.foreground_app();   check("foreground_app", '"app"' in out, out[:90])
out = T.volume_status();    check("volume_status", "volume" in out.lower(), out[:60])
out = T.recall("cobblemon server"); check("recall", "cobblemon" in out.lower(), out[:70].replace("\n", " "))
out = T.server_status();    check("server_status (headless over ssh)", "minecraft" in out.lower(), " | ".join(out.splitlines()[-3:])[:110])
out = T.minecraft_players(); check("minecraft_players", "player" in out.lower(), out[:80].replace("\n", " "))

# the MCP server itself, over stdio
proc = subprocess.Popen([sys.executable, str(ROOT / "nova_tools.py")], stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")


def rpc(obj, expect=True):
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()
    if not expect:
        return None
    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("server closed: " + proc.stderr.read()[-600:])
        msg = json.loads(line)
        if msg.get("id") == obj.get("id"):
            return msg


try:
    init = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "tools_test", "version": "1"}}})
    check("mcp initialize", "result" in init, str(init.get("result", {}).get("serverInfo", init))[:80])
    rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect=False)
    listed = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [t["name"] for t in listed["result"]["tools"]]
    check("mcp tools/list", len(names) == len(T.ALL) + 1 and "approve" in names, f"{len(names)} tools")
    called = rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                  "params": {"name": "volume_status", "arguments": {}}})
    text = json.dumps(called.get("result", called))
    check("mcp tools/call", "volume" in text.lower(), text[:90])
except Exception as exc:  # noqa: BLE001
    check("mcp stdio round trip", False, f"{type(exc).__name__}: {exc}"[:300])
finally:
    proc.kill()

print("RESULT:", "PASS" if all(checks) else "NEEDS WORK")
