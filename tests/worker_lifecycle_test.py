"""Worker lifecycle: a worker that cannot start must say why, and a dead worker must recover.

Regression cover for the failure that took Ultima3D down silently: the worker's
stderr went to `DEVNULL`, so every symptom read "blender worker died; check
blender stderr" with no stderr to check, and a missing `blender/worker.py`
(cwd-dependent path) looked identical to a Blender crash.

Drives `ultima3d.server.Worker` directly, like tests/timeout_test.py.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault(
    "ULTIMA3D_BLENDER",
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mcp.server.mcpserver.exceptions import ToolError

from ultima3d import server
from ultima3d.server import Worker

failures = []


def check(label, cond, detail=""):
    print(f"  [{'ok' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        failures.append(label)


real_worker = server.WORKER
scratch = Path(tempfile.mkdtemp(prefix="ultima3d-lifecycle-"))

# --- 1. A missing worker script is a configuration error, named as such -------
server.WORKER = scratch / "does-not-exist" / "worker.py"
w = Worker()
t0 = time.monotonic()
try:
    w.call("ping")
    check("missing worker script raises", False, "no error raised")
except ToolError as e:
    dt = time.monotonic() - t0
    check("missing worker script raises", True, f"ToolError after {dt:.2f}s")
    check("error names the missing path", "worker script not found" in str(e)
          and "does-not-exist" in str(e), str(e)[:120])
    check("error says how to fix it", "ULTIMA3D_WORKER" in str(e))
    check("fails before spawning blender", dt < 5, f"{dt:.2f}s")

# --- 2. A worker that dies at startup reports what it printed ----------------
broken = scratch / "broken_worker.py"
broken.write_text(
    "import sys\n"
    "print('probe: worker exploded at startup', file=sys.stderr, flush=True)\n"
    "raise SystemExit(3)\n",
    encoding="utf-8",
)
server.WORKER = broken
w2 = Worker()
try:
    w2.call("ping")
    check("startup crash raises", False, "no error raised")
except ToolError as e:
    text = str(e)
    check("startup crash raises", True, text[:80])
    check("error says it failed to start", "failed to start" in text, text[:120])
    check("worker's own stderr reaches the caller", "worker exploded at startup" in text,
          text[-160:])

# --- 3. stderr is kept on disk for post-mortem, not discarded ----------------
log = Path(server.OUT_DIR) / "worker.log"
check("worker stderr is logged", log.is_file() and "worker exploded" in
      log.read_text(encoding="utf-8", errors="replace")[-4000:], str(log))

# --- 4. A healthy worker still starts, and a killed one recovers -------------
server.WORKER = real_worker
w3 = Worker()
r = w3.call("ping")
check("real worker starts and pings", r.get("pong") is True, str(r.get("blender")))

w3.proc.kill()
w3.proc.wait(timeout=20)
r2 = w3.call("ping")
check("worker recovers after being killed", r2.get("pong") is True, str(r2.get("blender")))

w3._kill()
server.WORKER = real_worker

print()
if failures:
    print(f"WORKER LIFECYCLE TEST FAILED ({len(failures)}):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("WORKER LIFECYCLE TEST OK")
