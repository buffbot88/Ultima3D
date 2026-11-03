"""Server-side call timeout: a hung Blender worker must fail fast, not stall forever.

Drives ultima3d.server.Worker directly (not over MCP): a `_probe_sleep` op hangs the
worker, the call must raise within the deadline, and the next call must restart cleanly.
"""
import os
import sys
import time

os.environ.setdefault(
    "ULTIMA3D_BLENDER",
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ultima3d.server import Worker
from mcp.server.mcpserver.exceptions import ToolError

failures = []


def check(label, cond, detail=""):
    print(f"  [{'ok' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        failures.append(label)


w = Worker()
r = w.call("ping")
check("worker starts and pings", r.get("pong") is True, str(r.get("blender")))

# A 30s hang with a 5s deadline: the old blocking readline would return only
# after 30s (successfully); the deadline must raise ToolError at ~5s instead.
t0 = time.monotonic()
try:
    w.call("_probe_sleep", {"seconds": 30}, timeout=5)
    check("hung call raises instead of stalling", False, "no error raised")
except ToolError as e:
    dt = time.monotonic() - t0
    check("hung call raises instead of stalling", True, f"ToolError after {dt:.1f}s")
    check("error names the timeout", "timed out" in str(e), str(e)[:100])
    check("error arrives near the deadline, not the hang", dt < 25, f"{dt:.1f}s")
    check("error is honest about lost state", "state is lost" in str(e))

# The kill must leave a clean slate: the next call restarts the worker.
r2 = w.call("ping")
check("worker restarts cleanly after the kill", r2.get("pong") is True,
      str(r2.get("blender")))
check("request ids keep working after restart", w.call("ping").get("pong") is True)

w._kill()

print()
if failures:
    print(f"TIMEOUT TEST FAILED ({len(failures)}):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("TIMEOUT TEST OK")
