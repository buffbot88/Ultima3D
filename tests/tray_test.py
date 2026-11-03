"""Tray controller test: status model, icons, process manager (headless-safe)."""
import struct
import subprocess
import sys
import time

sys.path.insert(0, ".")

from ultima3d.tray import (
    BUSY, IDLE, ONLINE, STOPPED,
    ServerManager, StatusTracker, icon_files, log,
)

failures = []


def check(label, cond, detail=""):
    print(f"  [{'ok' if cond else 'FAIL'}] {label}{f' -- {detail}' if detail else ''}")
    if not cond:
        failures.append(label)


# 1. status model: stopped -> online -> busy -> idle(+chime once) -> busy re-arms
t = StatusTracker(idle_secs=120.0)
check("starts stopped", t.status == STOPPED)
t.sample(False, False, now=1000.0); check("down stays stopped", t.status == STOPPED)
t.sample(True, False, now=1000.0); check("quiet server is online", t.status == ONLINE)
check("no chime without ever being busy", t.take_chime() is False)
t.sample(True, True, now=1001.0); check("worker cpu -> busy", t.status == BUSY)
t.sample(True, False, now=1010.0); check("recent activity -> online", t.status == ONLINE)
check("still no chime before 120s", t.take_chime() is False)
t.sample(True, False, now=1001.0 + 121.0); check("quiet 121s -> idle", t.status == IDLE)
check("idle chime fires", t.take_chime() is True)
check("chime fires only once", t.take_chime() is False)
t.sample(True, True, now=1200.0); check("new work re-arms busy", t.status == BUSY)
t.sample(True, False, now=1200.0 + 121.0)
check("second idle chimes again", t.take_chime() is True)
t.server_down(); check("server down resets", t.status == STOPPED and t.take_chime() is False)

# 2. icons: real .ico files with valid headers, distinct per status
paths = icon_files()
check("four icons", set(paths) == {STOPPED, ONLINE, BUSY, IDLE}, str(sorted(paths)))
seen = set()
for name, p in paths.items():
    raw = open(p, "rb").read()
    ok = raw[:4] == b"\x00\x00\x01\x00" and struct.unpack("<H", raw[4:6])[0] == 1
    check(f"icon {name} valid ICO", ok, f"{len(raw)}B")
    seen.add(raw)
check("icons differ by status color", len(seen) == 4)

# 3. process manager against a dummy stdin-holding child (never the real server)
mgr = ServerManager(command=[sys.executable, "-c", "import sys; sys.stdin.read()"])
check("starts stopped", mgr.is_running() is False and mgr.pid() is None)
mgr.start()
time.sleep(0.5)
check("start launches child", mgr.is_running() and mgr.pid() is not None, f"pid={mgr.pid()}")
mgr.start(); check("double start is idempotent", mgr.is_running())
mgr.stop()
check("stop terminates child", mgr.is_running() is False and mgr.pid() is None)
mgr.restart()
time.sleep(0.5)
check("restart relaunches", mgr.is_running())
mgr.stop(); check("final stop clean", not mgr.is_running())

# 4. log() never raises
try:
    log("tray_test ping")
    check("log writes", True)
except Exception as e:  # noqa: BLE001
    check("log writes", False, str(e))

if failures:
    print(f"\nTRAY TEST FAILED ({len(failures)}):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("\nTRAY TEST OK (status model, icons, process manager)")
