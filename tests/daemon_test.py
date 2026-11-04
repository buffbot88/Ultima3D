"""Loopback daemon: a tool call must round-trip as JSON without Blender or an MCP client.

Uses `godot_status`, which is offline-tolerant, so the whole dispatch path is
exercised (validation, tool manager, result conversion) with no editor running.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "output", "daemon_test")

failures = []
finished = False


def check(label, cond, detail=""):
    print(f"  [{'ok' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        failures.append(label)


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(url, body=None, timeout=30):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read() or b"{}")


def main():
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, ULTIMA3D_OUT=OUT, ULTIMA3D_SESSION_MINUTES="5")
    proc = subprocess.Popen(
        [sys.executable, "-m", "ultima3d.server", "--http", f"127.0.0.1:{port}"],
        cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        deadline = time.monotonic() + 90
        health = None
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                check("daemon starts", False, f"exited {proc.returncode}")
                print(proc.stdout.read()[-2000:])
                return finish()
            try:
                _, health = request(f"{base}/health", timeout=5)
                break
            except (urllib.error.URLError, ConnectionError, OSError):
                time.sleep(0.3)
        check("daemon starts and answers /health", bool(health and health.get("ok")), str(health))

        _, status = request(f"{base}/status")
        check("session budget is reported", "session" in status and
              status["session"]["limit_minutes"] == 5.0, str(status.get("session")))
        check("status counts the full surface", status.get("tools") == 61, str(status.get("tools")))

        _, listed = request(f"{base}/tools")
        names = {tool["name"] for tool in listed.get("tools", [])}
        check("GET /tools lists create_3d and godot_status",
              {"create_3d", "godot_status", "gimp_status"} <= names, f"{len(names)} tools")

        code, body = request(f"{base}/call", {"tool": "godot_status", "args": {}})
        check("POST /call dispatches a tool", code == 200 and body.get("ok") is True,
              f"{code} {str(body)[:120]}")
        check("offline editor is state, not a crash",
              isinstance(body.get("result"), dict) and body["result"].get("ok") is False,
              str(body.get("result"))[:120])
        check("call response carries the budget", "session" in body)

        _, missing = request(f"{base}/call", {"tool": "no_such_tool", "args": {}})
        check("unknown tool fails honestly", missing.get("ok") is False and
              bool(missing.get("error")), str(missing.get("error"))[:120])

        _, bad = request(f"{base}/call", {"args": {}})
        check("missing tool name is a 400", bad.get("ok") is False and
              "tool" in str(bad.get("error")), str(bad.get("error"))[:120])

        code, _ = request(f"{base}/shutdown", {})
        deadline = time.monotonic() + 20
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.2)
        check("/shutdown stops the daemon", proc.poll() is not None, str(proc.poll()))
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        return finish()


def finish():
    global finished
    if finished:
        return 1 if failures else 0
    finished = True
    print()
    if failures:
        print(f"DAEMON TEST FAILED ({len(failures)}):")
        for name in failures:
            print(f"  - {name}")
        sys.exit(1)
    print("DAEMON TEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
