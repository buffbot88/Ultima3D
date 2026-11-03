"""Session budget: one server run is capped at 40 minutes and then stops itself.

Pure clock math is tested with a fake clock; the last case drives the real
`ultima3d.server.Worker` to prove an expired budget refuses work before Blender
is ever launched.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ultima3d.session import DEFAULT_MINUTES, ENV_MINUTES, SessionBudget, SessionExpired

failures = []


def check(label, cond, detail=""):
    print(f"  [{'ok' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        failures.append(label)


# --- default limit ---------------------------------------------------------
old_env = os.environ.pop(ENV_MINUTES, None)
budget = SessionBudget()
check("default limit is 40 minutes", budget.limit_minutes == DEFAULT_MINUTES == 40.0,
      f"{budget.limit_minutes}")

# --- clock math ------------------------------------------------------------
now = [0.0]
budget = SessionBudget(minutes=40, clock=lambda: now[0])
budget.start()
check("nothing elapsed at start", budget.elapsed_seconds() == 0.0)
check("not expired at start", not budget.expired())
now[0] = 39 * 60
budget.check("create_3d")  # must not raise just short of the deadline
check("work still allowed before the deadline", not budget.expired())
now[0] = 40 * 60
check("expires at exactly 40 minutes", budget.expired())
check("remaining is zero when expired", budget.remaining_seconds() == 0.0)

try:
    budget.check("create_3d")
    check("expired call raises", False, "no error raised")
except SessionExpired as exc:
    check("expired call raises", True)
    check("message names the budget", "session budget exhausted" in str(exc))
    check("message says it stops on its own", "stops automatically" in str(exc))

snapshot = budget.snapshot()
check("snapshot reports expiry", snapshot["expired"] is True)
check("snapshot reports 40-minute limit", snapshot["limit_minutes"] == 40.0, str(snapshot))

# --- start is idempotent (a restart cannot extend the clock) ---------------
now[0] += 60
started_before = budget.started_at
budget.start()
check("start is idempotent", budget.started_at == started_before)

# --- limit override + env parsing -----------------------------------------
budget = SessionBudget(minutes=10)
budget.set_limit_minutes(25)
check("explicit limit override applies", budget.limit_minutes == 25.0)

os.environ[ENV_MINUTES] = "0.5"
check("env override applies", abs(SessionBudget().limit_minutes - 0.5) < 1e-9)
os.environ[ENV_MINUTES] = "soon"
check("bad env value falls back to 40", SessionBudget().limit_minutes == DEFAULT_MINUTES)
os.environ.pop(ENV_MINUTES, None)
if old_env is not None:
    os.environ[ENV_MINUTES] = old_env

# --- watchdog fires at the deadline ---------------------------------------
fired = []
tiny = SessionBudget(minutes=0.0001)
tiny.enforce(lambda: fired.append(time.monotonic()))
deadline = time.monotonic() + 5.0
while not fired and time.monotonic() < deadline:
    time.sleep(0.02)
check("watchdog fires at the deadline", bool(fired))

# --- the real Worker refuses work before launching Blender ----------------
import ultima3d.server as server

server.SESSION.set_limit_minutes(0)
server.SESSION.start()
try:
    server._w.call("ping")
    check("worker call refused after expiry", False, "ping ran anyway")
except Exception as exc:
    check("worker call refused after expiry", "session budget exhausted" in str(exc), str(exc)[:80])

print()
if failures:
    print(f"SESSION TEST FAILED ({len(failures)}):")
    for name in failures:
        print(f"  - {name}")
    sys.exit(1)
print("SESSION TEST OK")
