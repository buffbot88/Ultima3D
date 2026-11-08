"""CLI bridge between a shell (or a Freebuff skill) and the local Ultima3D daemon.

    python -m ultima3d.skill call create_3d '{"recipe": {...}, "name": "crate"}'
    python -m ultima3d.skill tools
    python -m ultima3d.skill status
    python -m ultima3d.skill stop
    python -m ultima3d.skill install

The first call starts the daemon (loopback only) and waits for it to answer, so the
tool keeps scene and recipe state between calls; --http on the server runs it by hand.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_ADDR = "127.0.0.1:8791"
ADDR_ENV = "ULTIMA3D_DAEMON"
TOKEN_ENV = "ULTIMA3D_DAEMON_TOKEN"
CALL_TIMEOUT = float(os.environ.get("ULTIMA3D_HTTP_TIMEOUT", "900"))
START_TIMEOUT = float(os.environ.get("ULTIMA3D_START_TIMEOUT", "60"))

ROOT = Path(__file__).resolve().parent.parent
SKILL_SOURCE = ROOT / "skills" / "ultima3d"


def out_dir() -> Path:
    return Path(os.environ.get("ULTIMA3D_OUT", Path.cwd() / "output"))


def daemon_addr() -> str:
    return os.environ.get(ADDR_ENV) or DEFAULT_ADDR


def request(path: str, body: dict | None = None, addr: str | None = None,
            timeout: float | None = None) -> tuple[int, dict]:
    """Issue one JSON request; 0 means the daemon could not be reached."""
    url = f"http://{addr or daemon_addr()}{path}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET")
    req.add_header("Content-Type", "application/json")
    token = os.environ.get(TOKEN_ENV)
    if token:
        req.add_header("X-Ultima3D-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=timeout or CALL_TIMEOUT) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return exc.code, {"ok": False, "error": raw.decode("utf-8", "replace")}
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        reason = getattr(exc, "reason", exc)
        return 0, {"ok": False, "error": f"daemon unreachable at {url}: {reason}"}


NO_DASHBOARD_ENV = "ULTIMA3D_NO_DASHBOARD"


def dashboard_suppressed(args: argparse.Namespace | None = None) -> bool:
    """True when the dashboard must not pop up (headless/tests opt out)."""
    if os.environ.get(NO_DASHBOARD_ENV) == "1":
        return True
    return bool(args is not None and getattr(args, "no_dashboard", False))


def ensure_daemon(addr: str | None = None, no_dashboard: bool = False) -> dict:
    """Return health of the running daemon, starting one when none answers.

    A fresh boot also pops up the Electron dashboard (it attaches by itself),
    unless no_dashboard or ULTIMA3D_NO_DASHBOARD=1. Already-running daemons
    are returned untouched — the window only opens on bootup.
    """
    target = addr or daemon_addr()
    code, body = request("/health", addr=target, timeout=5)
    if code == 200:
        return body
    project_out = out_dir()
    log = project_out / "daemon.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess,
                                                                     "CREATE_NEW_PROCESS_GROUP", 0)
    # The daemon runs with cwd=ROOT (so it finds blender/worker.py), but its
    # outputs must land in the *invoker's* project, not the Ultima3D checkout.
    # Forward the resolved output dir explicitly; an explicit ULTIMA3D_OUT wins.
    child_env = dict(os.environ)
    child_env.setdefault("ULTIMA3D_OUT", str(project_out))
    with open(log, "ab", buffering=0) as handle:
        proc = subprocess.Popen(
            [sys.executable, "-m", "ultima3d.server", "--http", target],
            cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=handle, stderr=handle,
            creationflags=flags, env=child_env,
        )
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"daemon exited during startup with code {proc.returncode}; see {log}")
        code, body = request("/health", addr=target, timeout=5)
        if code == 200:
            body["started_pid"] = proc.pid
            if not no_dashboard and os.environ.get(NO_DASHBOARD_ENV) != "1":
                try:
                    body["dashboard"] = launch_dashboard()
                except Exception as exc:  # GUI must never break daemon boot
                    body["dashboard"] = {"ok": False, "error": str(exc)}
            return body
        time.sleep(0.5)
    proc.terminate()
    raise RuntimeError(f"daemon did not answer /health within {START_TIMEOUT:g}s; see {log}")


def args_payload(raw: str | None, args_file: str | None) -> dict:
    """Read tool arguments from a JSON string, a file, or stdin (in that order)."""
    if args_file:
        return json.loads(Path(args_file).read_text(encoding="utf-8"))
    if raw and raw.strip():
        return json.loads(raw)
    if not sys.stdin.isatty():
        text = sys.stdin.read().strip()
        if text:
            return json.loads(text)
    return {}


def cmd_call(args: argparse.Namespace) -> int:
    ensure_daemon(args.addr, no_dashboard=dashboard_suppressed(args))
    payload = args_payload(args.args, args.args_file)
    code, body = request("/call", {"tool": args.tool, "args": payload}, addr=args.addr)
    print(json.dumps(body, indent=2, default=str))
    return 0 if code == 200 and body.get("ok") else 1


def cmd_tools(args: argparse.Namespace) -> int:
    ensure_daemon(args.addr, no_dashboard=dashboard_suppressed(args))
    code, body = request("/tools", addr=args.addr, timeout=30)
    if code != 200:
        print(json.dumps(body, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(body, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    code, body = request("/status", addr=args.addr, timeout=10)
    if code == 0:
        body = {"ok": False,
                "daemon": f"not running at {args.addr or daemon_addr()}",
                "hint": "any call starts it; or run python -m ultima3d.server --http"}
    print(json.dumps(body, indent=2))
    return 0 if code == 200 else 1


def cmd_stop(args: argparse.Namespace) -> int:
    code, body = request("/shutdown", {}, addr=args.addr, timeout=10)
    if code == 0:
        body = {"ok": True, "stopped": False,
                "note": "no daemon was running; the Blender worker dies with it"}
    print(json.dumps(body, indent=2))
    return 0


def launch_dashboard() -> dict:
    """Open the Electron dashboard (detached); it attaches to the running daemon."""
    dash = ROOT / "dashboard"
    if not dash.is_dir():
        return {"ok": False, "error": f"dashboard missing: {dash}"}
    npm = shutil.which("npm")
    if npm is None:
        return {"ok": False,
                "error": "npm not found on PATH; install Node 18+ to run the dashboard"}
    flags = 0
    extra: dict = {}
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        extra["start_new_session"] = True
    try:
        proc = subprocess.Popen(
            [npm, "start"], cwd=str(dash), stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            creationflags=flags, **extra)
    except OSError as exc:
        return {"ok": False, "error": f"could not start dashboard: {exc}"}
    return {"ok": True, "pid": proc.pid,
            "note": "dashboard window opening; it attaches to the daemon by itself"}


def cmd_dashboard(args: argparse.Namespace) -> int:
    print(json.dumps(launch_dashboard(), indent=2))
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    body = ensure_daemon(args.addr, no_dashboard=dashboard_suppressed(args))
    print(json.dumps(body, indent=2))
    return 0


def cmd_skills(args: argparse.Namespace) -> int:
    from . import skills as _skills

    body = _skills.list_skills()
    print(json.dumps(body, indent=2))
    return 0 if body.get("ok") else 1


def cmd_skill_update(args: argparse.Namespace) -> int:
    from . import skills as _skills

    dry_run = not args.live
    if args.all:
        body = _skills.update_all(dry_run=dry_run)
    else:
        body = _skills.update_skill(args.id, dry_run=dry_run)
    print(json.dumps(body, indent=2, default=str))
    return 0 if body.get("ok") else 1


def cmd_modules(args: argparse.Namespace) -> int:
    from . import config as _config

    if args.enable:
        for mod in args.enable:
            if mod not in _config.MODULES:
                print(json.dumps({"ok": False, "error": f"unknown module {mod!r}"}, indent=2))
                return 1
            _config.set_flag("modules", mod, True)
    if args.disable:
        for mod in args.disable:
            if mod not in _config.MODULES:
                print(json.dumps({"ok": False, "error": f"unknown module {mod!r}"}, indent=2))
                return 1
            _config.set_flag("modules", mod, False)
    cfg = _config.load()
    print(json.dumps({"ok": True, "modules": cfg.get("modules", {}),
                       "note": "restart the daemon to apply flag changes"}, indent=2))
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    if not SKILL_SOURCE.is_dir():
        print(f"skill source missing: {SKILL_SOURCE}", file=sys.stderr)
        return 2
    base = Path(args.dir).expanduser() if args.dir else Path.home() / ".agents" / "skills"
    target = base / "ultima3d"
    base.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SKILL_SOURCE, target, dirs_exist_ok=True)
    print(json.dumps({
        "ok": True,
        "installed": str(target),
        "next": "restart or reload Freebuff Desktop so it discovers the skill",
    }, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ultima3d.skill",
        description="Drive the local Ultima3D tool surface from a shell or a Freebuff skill.",
    )
    parser.add_argument("--addr", default=None, help=f"daemon address (default {DEFAULT_ADDR})")
    parser.add_argument("--no-dashboard", action="store_true",
                        help="don't pop up the Electron dashboard on daemon boot "
                             "(also: ULTIMA3D_NO_DASHBOARD=1)")
    subs = parser.add_subparsers(dest="command", required=True)

    call = subs.add_parser("call", help="call one tool with JSON arguments")
    call.add_argument("tool")
    call.add_argument("args", nargs="?", default=None, help="JSON object (or use --args-file / stdin)")
    call.add_argument("--args-file", default=None)
    call.set_defaults(func=cmd_call)

    subs.add_parser("tools", help="list registered tools").set_defaults(func=cmd_tools)
    subs.add_parser("status", help="daemon + session status").set_defaults(func=cmd_status)
    subs.add_parser("stop", help="stop the daemon and its Blender worker").set_defaults(func=cmd_stop)
    start = subs.add_parser("start", help="start the daemon in the background (+ dashboard)")
    start.set_defaults(func=cmd_start)
    subs.add_parser("dashboard", help="pop up the Electron dashboard (attaches to the daemon)").set_defaults(func=cmd_dashboard)

    install = subs.add_parser("install", help="copy the skill into ~/.agents/skills")
    install.add_argument("--dir", default=None, help="override the skills directory")
    install.set_defaults(func=cmd_install)

    subs.add_parser("skills", help="list managed skills and their state").set_defaults(func=cmd_skills)

    update = subs.add_parser("skill-update", help="update a managed skill from its source")
    update.add_argument("id", nargs="?", default=None, help="skill id (omit with --all)")
    update.add_argument("--all", action="store_true", help="update every skill")
    update.add_argument("--live", action="store_true",
                        help="actually fetch (default is a dry run that changes nothing)")
    update.set_defaults(func=cmd_skill_update)

    modules = subs.add_parser("modules", help="show or flip backend-module flags")
    modules.add_argument("--enable", nargs="*", default=None, help="module(s) to enable")
    modules.add_argument("--disable", nargs="*", default=None, help="module(s) to disable")
    modules.set_defaults(func=cmd_modules)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
